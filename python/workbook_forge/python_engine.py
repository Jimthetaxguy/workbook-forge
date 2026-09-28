"""Independent, standard-library Python programmable workbook implementation.

The JSON boundary matches the optional Rust bridge, but model validation,
calculation, dependency tracking, edit transactions and revision publication are
implemented here. Formulas execute through the original Python evaluator.
"""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import json
import math
import re
from threading import Lock, RLock
from typing import Any
import unicodedata

from . import ArrayValue, ErrorValue, evaluate_result
from .catalog import _catalog_file
from .expressions import analyze_formula as _analyze_formula, copy_formula

MAX_REQUEST_BYTES = 128 * 1024 * 1024
MAX_WORKBOOK_CELLS = 100_000
MAX_DEPENDENCIES = 100_000
MAX_EDIT_BATCH = 10_000
MAX_WORKERS = 8
MAX_RESULT_CELLS = 100_000
_UINT64_MAX = (1 << 64) - 1
_CELL = re.compile(r"\$?([A-Za-z]+)\$?([1-9][0-9]*)\Z")
_ERRORS = frozenset({"#VALUE!", "#DIV/0!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#N/A!", "#CALC!", "#NULL!", "#SPILL!"})
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


class ToolkitError(ValueError):
    """A structural or operational failure, distinct from an Excel cell error."""

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")


def _fail(code: str, message: str):
    raise ToolkitError(code, message)


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _decode(source: str) -> Any:
    if not isinstance(source, str):
        _fail("invalid_json", "request must be a JSON string")
    try:
        if len(source.encode("utf-8")) > MAX_REQUEST_BYTES:
            _fail("resource_limit", "request exceeds 128 MiB")
        return json.loads(source, parse_constant=lambda value: _fail("invalid_value", f"nonfinite JSON constant {value}"))
    except (json.JSONDecodeError, UnicodeError, RecursionError) as error:
        _fail("invalid_json", str(error))


def _object(value: Any, allowed: set[str] | None = None) -> dict:
    if not isinstance(value, dict):
        _fail("invalid_model", "expected an object")
    if allowed is not None and set(value) - allowed:
        _fail("invalid_model", "unknown fields: " + ", ".join(sorted(set(value) - allowed)))
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        _fail("invalid_model", f"{label} must be text")
    try:
        value.encode("utf-8")
    except UnicodeError:
        _fail("invalid_value", f"{label} contains invalid Unicode")
    return value


def _units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _scalar(value: Any) -> Any:
    if value is None or type(value) is bool:
        return value
    if type(value) in (int, float):
        try:
            value = float(value)
        except (OverflowError, ValueError):
            _fail("invalid_value", "numbers must be finite")
        if not math.isfinite(value):
            _fail("invalid_value", "numbers must be finite")
        return value
    if isinstance(value, str):
        _string(value, "cell text")
        if _units(value) > 32_767:
            _fail("resource_limit", "cell text exceeds 32767 UTF-16 code units")
        return value
    if isinstance(value, dict):
        _object(value, {"error", "message"})
        error = _string(value.get("error"), "error")
        if error not in _ERRORS:
            _fail("invalid_value", "unknown Excel error code")
        result = {"error": error}
        if value.get("message") is not None:
            result["message"] = _string(value["message"], "error message")
        return result
    _fail("invalid_value", "cell values must be finite numbers, text, booleans, blanks or error objects")


def _style(value: Any) -> dict:
    value = _object(value, {"number_format", "bold", "font_color", "fill_color", "horizontal"})
    result = {name: item for name, item in value.items() if item is not None}
    for name, item in result.items():
        if name == "bold":
            if type(item) is not bool:
                _fail("invalid_style", "bold must be boolean")
        else:
            _string(item, name)
    if "horizontal" in result and result["horizontal"] not in {"left", "center", "right"}:
        _fail("invalid_style", "horizontal alignment must be left, center or right")
    for name in ("font_color", "fill_color"):
        if name in result and not re.fullmatch(r"(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})", result[name]):
            _fail("invalid_style", "colors must be six- or eight-digit hex")
    if "number_format" in result and len(result["number_format"].encode()) > 1024:
        _fail("resource_limit", "number format exceeds 1024 bytes")
    return result


def _coordinates(address: Any) -> tuple[int, int]:
    match = _CELL.fullmatch(address) if isinstance(address, str) else None
    if match is None:
        _fail("invalid_reference", f"invalid A1 reference: {address}")
    column = 0
    for letter in match[1].upper():
        column = column * 26 + ord(letter) - 64
        if column > 16_384:
            _fail("invalid_reference", f"reference outside Excel grid: {address}")
    if len(match[2]) > 7:
        _fail("invalid_reference", f"reference outside Excel grid: {address}")
    row = int(match[2])
    if row > 1_048_576:
        _fail("invalid_reference", f"reference outside Excel grid: {address}")
    return row, column


def _address(row: int, column: int) -> str:
    letters = ""
    while column:
        column, digit = divmod(column - 1, 26)
        letters = chr(65 + digit) + letters
    return f"{letters}{row}"


def _canonical(address: Any) -> str:
    return _address(*_coordinates(address))


def _resolve(model: dict, sheet: str, address: str) -> str:
    folded = _string(sheet, "sheet").translate(_ASCII_LOWER)
    actual = next((item["name"] for item in model["sheets"] if item["name"].translate(_ASCII_LOWER) == folded), None)
    if actual is None:
        _fail("invalid_reference", f"unknown worksheet {sheet}")
    return f"{actual}!{_canonical(address)}"


def _cells(model: dict) -> dict[str, dict]:
    return {f"{sheet['name']}!{address}": cell for sheet in model["sheets"] for address, cell in sheet["cells"].items()}


def _validate_input_value(binding: dict, value: Any):
    value = _scalar(value)
    if value is None:
        if binding["required"]:
            _fail("invalid_input", "required input is blank")
        return
    matches_kind = {
        "number": type(value) is float,
        "text": isinstance(value, str),
        "boolean": type(value) is bool,
    }
    if not matches_kind[binding["kind"]]:
        _fail("invalid_input", f"input expects {binding['kind']}")
    if type(value) is float and (binding.get("min") is not None and value < binding["min"] or binding.get("max") is not None and value > binding["max"]):
        _fail("invalid_input", "input is outside its numeric bounds")
    if "choices" in binding and not any(type(value) is type(choice) and value == choice for choice in binding["choices"]):
        _fail("invalid_input", "input is not an allowed choice")


def _normalize_model(source: Any) -> dict:
    source = _object(source, {"schema_version", "revision", "sheets", "inputs", "outputs"})
    version, revision = source.get("schema_version", 1), source.get("revision", 0)
    if type(version) is not int or version != 1:
        _fail("schema_version", "expected workbook schema version 1")
    if type(revision) is not int or not 0 <= revision <= _UINT64_MAX:
        _fail("invalid_model", "revision must be an unsigned 64-bit integer")
    sheets = source.get("sheets")
    if not isinstance(sheets, list):
        _fail("invalid_model", "sheets must be an array")
    if len(sheets) > 1024:
        _fail("resource_limit", "at most 1024 worksheets")
    model = {"schema_version": 1, "revision": revision, "sheets": [], "inputs": {}, "outputs": {}}
    names, identifiers, populated = set(), set(), 0
    for sheet in sheets:
        _object(sheet, {"id", "name", "cells", "column_widths"})
        identifier, name = _string(sheet.get("id"), "sheet ID"), _string(sheet.get("name"), "sheet name")
        folded = name.translate(_ASCII_LOWER)
        if not identifier or identifier in identifiers or not name or _units(name) > 31 or name.startswith("'") or name.endswith("'") or any(unicodedata.category(c) == "Cc" or c in "[]:*?/\\" for c in name) or folded in names:
            _fail("invalid_sheet", "sheet names and IDs must be valid and unique")
        names.add(folded)
        identifiers.add(identifier)
        cells, widths = _object(sheet.get("cells", {})), _object(sheet.get("column_widths", {}))
        populated += len(cells)
        if populated > MAX_WORKBOOK_CELLS:
            _fail("resource_limit", "workbook exceeds 100000 populated cells")
        normalized = {"id": identifier, "name": name, "cells": {}, "column_widths": {}}
        for address, cell in sorted(cells.items()):
            if _canonical(address) != address:
                _fail("invalid_reference", "cell map keys must be canonical uppercase unanchored A1 addresses")
            _object(cell, {"value", "formula", "cached_value", "style", "blocked_reason"})
            item = {"value": _scalar(cell.get("value"))}
            if cell.get("formula") is not None:
                formula = _string(cell["formula"], "formula")
                if item["value"] is not None:
                    _fail("invalid_cell", "a formula cell cannot also contain an authored value")
                if _units(formula) > 8193:
                    _fail("resource_limit", "formula exceeds 8192 UTF-16 code units")
                item["formula"] = formula
            if cell.get("cached_value") is not None:
                item["cached_value"] = _scalar(cell["cached_value"])
            if cell.get("style") is not None:
                item["style"] = _style(cell["style"])
            if cell.get("blocked_reason") is not None:
                item["blocked_reason"] = _string(cell["blocked_reason"], "blocked reason")
            normalized["cells"][address] = item
        for column, width in sorted(widths.items()):
            if not re.fullmatch(r"[A-Z]+", column) or _coordinates(column + "1")[1] > 16384 or type(width) not in (int, float):
                _fail("invalid_layout", "column widths require A-XFD keys and finite widths in (0,255]")
            width = _scalar(width)
            if not 0 < width <= 255:
                _fail("invalid_layout", "column widths require finite widths in (0,255]")
            normalized["column_widths"][column] = width
        model["sheets"].append(normalized)
    raw_inputs, raw_outputs = _object(source.get("inputs", {})), _object(source.get("outputs", {}))
    if len(raw_inputs) > MAX_WORKBOOK_CELLS or len(raw_outputs) > MAX_WORKBOOK_CELLS:
        _fail("resource_limit", "input and output counts are capped at 100000 each")
    populated_cells, bound = _cells(model), set()
    for name, binding in sorted(raw_inputs.items()):
        _string(name, "input name")
        _object(binding, {"sheet", "address", "kind", "required", "min", "max", "choices"})
        kind = binding.get("kind")
        if not name or kind not in ("number", "text", "boolean"):
            _fail("invalid_input", "inputs require nonempty names and number, text or boolean kinds")
        target = _resolve(model, binding.get("sheet"), binding.get("address"))
        if target in bound:
            _fail("invalid_input", "multiple inputs cannot bind the same cell")
        bound.add(target)
        cell = populated_cells.get(target, {})
        if cell.get("formula") is not None or cell.get("blocked_reason") is not None:
            _fail("invalid_input", "input cannot bind a formula or unsupported cell")
        required = binding.get("required", True)
        if type(required) is not bool:
            _fail("invalid_input", "required must be boolean")
        item = {"sheet": binding["sheet"], "address": binding["address"], "kind": kind, "required": required}
        for limit in ("min", "max"):
            if binding.get(limit) is not None:
                value = binding[limit]
                if type(value) not in (int, float) or kind != "number":
                    _fail("invalid_input", "numeric bounds must be finite and used only with numeric inputs")
                item[limit] = _scalar(value)
        if "min" in item and "max" in item and item["min"] > item["max"]:
            _fail("invalid_input", "numeric bounds must be ordered")
        if binding.get("choices") is not None:
            choices = binding["choices"]
            if not isinstance(choices, list) or not 1 <= len(choices) <= 32:
                _fail("invalid_input", "choice lists must contain 1 to 32 values")
            item["choices"] = [_scalar(choice) for choice in choices]
            for choice in item["choices"]:
                _validate_input_value(item, choice)
        model["inputs"][name] = item
    for name, binding in sorted(raw_outputs.items()):
        if not _string(name, "output name"):
            _fail("invalid_output", "outputs require nonempty names")
        _object(binding, {"sheet", "address"})
        _resolve(model, binding.get("sheet"), binding.get("address"))
        model["outputs"][name] = {"sheet": binding["sheet"], "address": binding["address"]}
    return model


def _input_edits(model: dict, values: dict) -> list[dict]:
    _object(values)
    for name in values:
        if name not in model["inputs"]:
            _fail("unknown_input", f"unknown input {name}")
    populated, edits = _cells(model), []
    for name, binding in model["inputs"].items():
        target = _resolve(model, binding["sheet"], binding["address"])
        value = _scalar(values[name]) if name in values else populated.get(target, {}).get("value")
        try:
            _validate_input_value(binding, value)
        except ToolkitError as error:
            raise ToolkitError(error.code, f"{name}: {error.message}") from error
        if name in values:
            edits.append({"sheet": binding["sheet"], "address": _canonical(binding["address"]), "value": value})
    return edits


@lru_cache(maxsize=1)
def _supported_functions() -> frozenset[str]:
    catalog = json.loads(_catalog_file("formulas.json").read_text(encoding="utf-8"))
    return frozenset(item["name"] for item in catalog["functions"] if item["status"].get("python") in {"evaluated", "conformance-tested"})


def _formula_analysis(formula: str) -> dict:
    try:
        return _analyze_formula(formula)
    except (ValueError, TypeError, RecursionError) as error:
        message = str(error)
        if message.startswith("unsupported name "):
            code = "unsupported_formula"
        elif any(word in message.lower() for word in ("limit", "nesting", "depth", "8192")):
            code = "resource_limit"
        elif "bound" in message.lower():
            code = "invalid_reference"
        else:
            code = "parse_error"
        raise ToolkitError(code, message) from error


@dataclass
class _Graph:
    dependencies: dict[str, set[str]]
    errors: dict[str, ToolkitError]
    cells: dict[str, dict]
    formulas: dict[str, str]

    @classmethod
    def build(cls, model: dict) -> _Graph:
        graph = cls({}, {}, _cells(model), {})
        expanded = 0
        for current, cell in graph.cells.items():
            dependencies: set[str] = set()
            graph.dependencies[current] = dependencies
            if cell.get("blocked_reason") is not None:
                graph.errors[current] = ToolkitError("unsupported_cell", cell["blocked_reason"])
                continue
            if isinstance(cell.get("value"), dict) and cell["value"]["error"] in {"#NULL!", "#SPILL!"}:
                graph.errors[current] = ToolkitError("unsupported_error_value", "this preserved Excel error has no evaluator semantics")
                continue
            if "formula" not in cell:
                continue
            try:
                analysis = _formula_analysis(cell["formula"])
                graph.formulas[current] = analysis["rendered"]
                unknown = set(analysis["functions"]) - _supported_functions()
                if unknown:
                    _fail("unsupported_formula", f"unsupported function {min(unknown)}")
                for reference in analysis["references"]:
                    start, end = reference["start"], reference.get("end", reference["start"])
                    first_row, last_row = sorted((start["row"], end["row"]))
                    first_col, last_col = sorted((start["column"], end["column"]))
                    count = (last_row - first_row + 1) * (last_col - first_col + 1)
                    if count > MAX_DEPENDENCIES:
                        _fail("resource_limit", "reference range exceeds 100000 cells")
                    expanded += count
                    if expanded > MAX_DEPENDENCIES:
                        # Aggregate work is a workbook profile, independent of
                        # which output happens to be requested on this run.
                        raise _GlobalGraphLimit("resource_limit", "expanded workbook references exceed 100000 cells")
                    sheet = start.get("sheet") or current.rsplit("!", 1)[0]
                    canonical_sheet = _resolve(model, sheet, "A1").rsplit("!", 1)[0]
                    dependencies.update(f"{canonical_sheet}!{_address(row, col)}" for row in range(first_row, last_row + 1) for col in range(first_col, last_col + 1))
            except _GlobalGraphLimit:
                raise
            except ToolkitError as error:
                graph.errors[current] = error
        for referenced in set().union(*graph.dependencies.values()) if graph.dependencies else ():
            graph.dependencies.setdefault(referenced, set())
        if len(graph.dependencies) > MAX_WORKBOOK_CELLS:
            _fail("resource_limit", "populated and referenced workbook cells exceed 100000")
        return graph

    def closure(self, model: dict) -> set[str]:
        pending = [_resolve(model, binding["sheet"], binding["address"]) for binding in model["outputs"].values()] if model["outputs"] else list(self.dependencies)
        selected: set[str] = set()
        while pending:
            current = pending.pop()
            if current not in selected:
                selected.add(current)
                pending.extend(self.dependencies.get(current, ()))
        return selected

    def reverse(self) -> dict[str, set[str]]:
        reverse: dict[str, set[str]] = defaultdict(set)
        for current, dependencies in self.dependencies.items():
            for dependency in dependencies:
                reverse[dependency].add(current)
        return reverse


class _GlobalGraphLimit(ToolkitError):
    pass


def _diagnostic(error: ToolkitError, current: str | None = None) -> dict:
    result = {"code": error.code, "message": error.message}
    if current:
        result["sheet"], result["address"] = current.rsplit("!", 1)
    return result


def _engine_scalar(value: Any) -> Any:
    if isinstance(value, dict):
        code = "#N/A" if value["error"] == "#N/A!" else value["error"]
        # Stored messages describe provenance; the evaluator receives the Excel
        # error itself so user metadata cannot masquerade as an engine failure.
        return ErrorValue(code)
    if type(value) is float and value.is_integer():
        # The independent evaluator represents integral arguments as ints.
        # This preserves their type after the wire contract normalizes numbers.
        return int(value)
    return value


def _wire_scalar(value: Any) -> Any:
    if isinstance(value, ErrorValue):
        return {"error": value.code}
    if type(value) in (int, float):
        try:
            number = float(value)
            return number if math.isfinite(number) else {"error": "#NUM!"}
        except OverflowError:
            return {"error": "#NUM!"}
    return value


def _evaluate_node(graph: _Graph, current: str, values: dict) -> Any:
    if current in graph.errors:
        raise graph.errors[current]
    cell = graph.cells.get(current, {})
    if "formula" not in cell:
        return deepcopy(cell.get("value"))
    environment = {}
    for dependency in sorted(graph.dependencies.get(current, ())):
        if dependency not in values:
            _fail("unsupported_dependency", f"dependency {dependency} could not be calculated")
        value = values[dependency]
        if isinstance(value, dict) and "rows" in value:
            _fail("unsupported_array_reference", f"worksheet dependency {dependency} is an array; spill projection is unsupported")
        environment[dependency] = _engine_scalar(value)
    result = evaluate_result(graph.formulas[current], environment, current.rsplit("!", 1)[0])
    if isinstance(result, ErrorValue) and (result.message.startswith("unsupported function ") or "supports exact match_mode=0 only" in result.message or "search_mode must be 1 or -1" in result.message):
        _fail("unsupported_formula", result.message)
    if isinstance(result, ArrayValue):
        return {"rows": [[_wire_scalar(value) for value in row] for row in result.rows]}
    # Worksheet formula results store a reference to a blank as numeric zero;
    # standalone evaluate_result and authored cell blanks retain their identity.
    return 0.0 if result is None else _wire_scalar(result)


class _ResultBudget:
    def __init__(self):
        self.used = 0
        self.exhausted = False
        self.lock = Lock()

    def reserve(self, value: Any):
        count = sum(map(len, value["rows"])) if isinstance(value, dict) and "rows" in value else 1
        with self.lock:
            if self.used + count > MAX_RESULT_CELLS:
                self.exhausted = True
                _fail("resource_limit", "calculation exceeds 100000 materialized result cells")
            self.used += count


def _workers(workers: int) -> int:
    if type(workers) is not int or workers < 0:
        _fail("invalid_workers", "workers must be a nonnegative integer")
    return max(1, min(workers, MAX_WORKERS))


def _calculate_document(model: dict, workers: int = 1, previous: dict | None = None, dirty: set[str] | None = None) -> dict:
    workers = _workers(workers)
    report = {"revision": model["revision"], "outputs": {}, "values": {}, "diagnostics": [], "evaluated_cells": [], "stale": False}
    try:
        _input_edits(model, {})
        graph = _Graph.build(model)
    except ToolkitError as error:
        report["diagnostics"].append(_diagnostic(error))
        return report
    selected, reverse = graph.closure(model), graph.reverse()
    invalidated, pending = set(dirty or ()), list(dirty or ())
    while pending:
        for dependent in reverse.get(pending.pop(), ()):
            if dependent not in invalidated:
                invalidated.add(dependent)
                pending.append(dependent)
    remaining = {current: len(graph.dependencies.get(current, ())) for current in selected}
    ready = sorted(current for current, count in remaining.items() if count == 0)
    finished, budget = set(), _ResultBudget()
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="workbook-forge-python") if workers > 1 else None
    try:
        while ready:
            evaluate = []
            for current in ready:
                if previous is not None and current not in invalidated and current in previous["values"]:
                    value = previous["values"][current]
                    try:
                        budget.reserve(value)
                    except ToolkitError as error:
                        report["diagnostics"].append(_diagnostic(error, current))
                        return report
                    report["values"][current] = deepcopy(value)
                elif graph.cells.get(current, {}).get("formula") is not None and current not in graph.errors:
                    evaluate.append(current)
                else:
                    try:
                        value = _evaluate_node(graph, current, report["values"])
                        budget.reserve(value)
                        report["values"][current] = value
                    except ToolkitError as error:
                        report["diagnostics"].append(_diagnostic(error, current))
                        if budget.exhausted:
                            return report

            def run_chunk(chunk: list[str]) -> list[tuple[str, Any, ToolkitError | None]]:
                results = []
                for current in chunk:
                    if budget.exhausted:
                        break
                    try:
                        value = _evaluate_node(graph, current, report["values"])
                        budget.reserve(value)
                        results.append((current, value, None))
                    except ToolkitError as error:
                        results.append((current, None, error))
                return results

            if executor is None or len(evaluate) < 2:
                chunks = [run_chunk(evaluate)]
            else:
                size = (len(evaluate) + workers - 1) // workers
                chunks = executor.map(run_chunk, [evaluate[index:index + size] for index in range(0, len(evaluate), size)])
            # Each submitted job is one bounded chunk; at most eight futures and
            # at most eight per-formula result arrays can be in flight.
            for chunk in chunks:
                for current, value, error in chunk:
                    report["evaluated_cells"].append(current)
                    if error is None:
                        report["values"][current] = value
                    else:
                        report["diagnostics"].append(_diagnostic(error, current))
            if budget.exhausted:
                return report
            next_ready = []
            for current in ready:
                finished.add(current)
                for dependent in reverse.get(current, ()):
                    if dependent in remaining:
                        remaining[dependent] -= 1
                        if remaining[dependent] == 0:
                            next_ready.append(dependent)
            ready = sorted(next_ready)
        for current in sorted(selected - finished):
            report["diagnostics"].append(_diagnostic(ToolkitError("dependency_cycle", "cell is part of, or depends on, a circular reference"), current))
        output_budget = _ResultBudget()
        for name, binding in model["outputs"].items():
            current = _resolve(model, binding["sheet"], binding["address"])
            if current in report["values"]:
                value = report["values"][current]
                try:
                    output_budget.reserve(value)
                except ToolkitError as error:
                    report["diagnostics"].append(_diagnostic(error, current))
                    break
                report["outputs"][name] = deepcopy(value)
        return report
    finally:
        if executor is not None:
            executor.shutdown(wait=True)
        report["evaluated_cells"].sort()
        report["diagnostics"].sort(key=lambda item: (item.get("sheet", ""), item.get("address", ""), item["code"], item["message"]))


class Session:
    """Thread-safe transient Python session; snapshots are detached JSON data."""

    def __init__(self, model_json: str):
        self._model = _normalize_model(_decode(model_json))
        self._published: dict | None = None
        self._dirty: set[str] = set()
        self._lock = RLock()

    def snapshot(self) -> str:
        with self._lock:
            return _encode(self._model)

    def apply(self, edits_json: str, expected_revision: int | None = None) -> int:
        edits = _decode(edits_json)
        if not isinstance(edits, list):
            _fail("invalid_edit", "edit batch must be an array")
        if len(edits) > MAX_EDIT_BATCH:
            _fail("resource_limit", "edit batch exceeds 10000 edits")
        if expected_revision is not None and (type(expected_revision) is not int or not 0 <= expected_revision <= _UINT64_MAX):
            _fail("revision_conflict", "expected revision must be an unsigned 64-bit integer")
        with self._lock:
            if expected_revision is not None and expected_revision != self._model["revision"]:
                _fail("revision_conflict", f"expected revision does not match {self._model['revision']}")
            if not edits:
                return self._model["revision"]
            candidate, changed = deepcopy(self._model), set()
            sheets = {sheet["name"]: sheet for sheet in candidate["sheets"]}
            for edit in edits:
                _object(edit, {"sheet", "address", "value", "formula", "style"})
                has_value, has_formula, has_style = "value" in edit, edit.get("formula") is not None, edit.get("style") is not None
                if has_value and has_formula or not (has_value or has_formula or has_style):
                    _fail("invalid_edit", "edit requires one value or formula, optionally a style, or a style alone")
                target = _resolve(candidate, edit.get("sheet"), edit.get("address"))
                if target in changed:
                    _fail("invalid_edit", "a batch may edit each cell only once")
                changed.add(target)
                sheet, address = target.rsplit("!", 1)
                cell = sheets[sheet]["cells"].setdefault(address, {"value": None})
                if cell.get("blocked_reason") is not None:
                    _fail("unsupported_edit", f"{target} contains preserved-only content")
                if has_value:
                    cell["value"] = _scalar(edit["value"])
                    cell.pop("formula", None)
                    cell.pop("cached_value", None)
                if has_formula:
                    formula = _string(edit["formula"], "formula")
                    _formula_analysis(formula)
                    cell["formula"], cell["value"] = formula, None
                    cell.pop("cached_value", None)
                if has_style:
                    cell["style"] = _style(edit["style"])
            candidate = _normalize_model(candidate)
            _input_edits(candidate, {})
            _Graph.build(candidate)
            if candidate["revision"] == _UINT64_MAX:
                _fail("resource_limit", "revision counter exhausted")
            candidate["revision"] += 1
            self._model = candidate
            self._dirty.update(changed)
            return candidate["revision"]

    def set_inputs(self, inputs_json: str, expected_revision: int | None = None) -> int:
        values = _decode(inputs_json)
        if expected_revision is not None and (type(expected_revision) is not int or not 0 <= expected_revision <= _UINT64_MAX):
            _fail("revision_conflict", "expected revision must be an unsigned 64-bit integer")
        with self._lock:
            if expected_revision is not None and expected_revision != self._model["revision"]:
                _fail("revision_conflict", "model revision conflict")
            edits = _input_edits(self._model, values)
            return self.apply(_encode(edits), expected_revision=self._model["revision"])

    def calculate(self, workers: int = 1) -> str:
        with self._lock:
            model, previous, dirty = deepcopy(self._model), deepcopy(self._published), self._dirty.copy()
        report = _calculate_document(model, workers, previous, dirty)
        with self._lock:
            if self._model["revision"] != report["revision"]:
                report["stale"] = True
            else:
                self._published = deepcopy(report)
                self._dirty.clear()
        return _encode(report)


def calculate(model_json: str, workers: int = 1) -> str:
    """Calculate a detached model with the independent Python formula engine."""
    return _encode(_calculate_document(_normalize_model(_decode(model_json)), workers))


def inspect(model_json: str) -> str:
    """Inspect structure and unsupported features without evaluating formulas."""
    model = _normalize_model(_decode(model_json))
    try:
        graph = _Graph.build(model)
    except ToolkitError as error:
        return _encode({"revision": model["revision"], "diagnostics": [_diagnostic(error)]})
    return _encode({"revision": model["revision"], "sheets": [{"id": sheet["id"], "name": sheet["name"], "populated_cells": len(sheet["cells"])} for sheet in model["sheets"]], "dependencies": {current: sorted(dependencies) for current, dependencies in sorted(graph.dependencies.items())}, "diagnostics": [_diagnostic(error, current) for current, error in sorted(graph.errors.items())], "inputs": model["inputs"], "outputs": model["outputs"]})


def analyze_formula(formula: str) -> str:
    return _encode(_analyze_formula(formula))


def scenario() -> str:
    """Construct the two-sheet operating planner entirely in Python."""
    header = {"bold": True, "font_color": "FFFFFF", "fill_color": "19324D"}
    money = {"number_format": "$#,##0.00;[Red]($#,##0.00)"}
    assumptions = {"id": "Assumptions", "name": "Assumptions", "cells": {}, "column_widths": {"A": 26, "B": 18}}
    forecast = {"id": "Forecast", "name": "Forecast", "cells": {}, "column_widths": {**{column: 20 for column in "ABCDEF"}, "G": 32}}
    for row, label, value in [(1, "Unit price", 20), (2, "Unit variable cost", 8), (3, "Fixed cost per period", 1000)]:
        assumptions["cells"][f"A{row}"] = {"value": label}
        assumptions["cells"][f"B{row}"] = {"value": value, "style": {**money, "fill_color": "E8F2FF"}}
    for column, label in zip("ABCDEF", ["Period", "Quantity", "Revenue", "Variable costs", "Profit", "Summary outputs"]):
        forecast["cells"][f"{column}1"] = {"value": label, "style": header.copy()}
    for row, quantity in [(2, 100), (3, 120), (4, 150)]:
        forecast["cells"][f"A{row}"] = {"value": f"Period {row - 1}"}
        forecast["cells"][f"B{row}"] = {"value": quantity}
        for column, source in [("C", "=B2*Assumptions!$B$1"), ("D", "=B2*Assumptions!$B$2"), ("E", "=C2-D2-Assumptions!$B$3")]:
            forecast["cells"][f"{column}{row}"] = {"value": None, "formula": copy_formula(source, row - 2, 0), "style": money.copy()}
    forecast["cells"].update({"F2": {"value": None, "formula": "=SUM(C2:C4)", "style": money.copy()}, "F3": {"value": None, "formula": "=SUM(E2:E4)", "style": money.copy()}, "F4": {"value": None, "formula": "=IF(Assumptions!B1>Assumptions!B2,Assumptions!B3/(Assumptions!B1-Assumptions!B2),NA())", "style": {"number_format": "0.00"}}})
    for address, label in [("G2", "Total revenue"), ("G3", "Total profit"), ("G4", "Break-even units per period")]:
        forecast["cells"][address] = {"value": label}
    model = {"schema_version": 1, "revision": 0, "sheets": [assumptions, forecast], "inputs": {name: {"sheet": "Assumptions", "address": f"B{row}", "kind": "number", "required": True, "min": 0} for name, row in [("unit_price", 1), ("unit_cost", 2), ("fixed_cost", 3)]}, "outputs": {name: {"sheet": "Forecast", "address": address} for name, address in [("revenue", "F2"), ("profit", "F3"), ("break_even_units", "F4")]}}
    return _encode(_normalize_model(model))
