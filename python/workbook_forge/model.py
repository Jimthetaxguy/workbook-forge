"""Canonical workbook model.

The serialized JSON Schema in ``schemas/workbook-model.v1.schema.json`` is the
source of truth. These types are a hydration of that document. ``schema_version``
is the wire shape. ``model_version`` is the semantic interpretation. See
``docs/specs/model-versioning.md``.
"""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from workbook_forge import ArrayValue, ErrorValue, analyze_formula, evaluate_result
from workbook_forge.catalog import function_status

SCHEMA_VERSION = 1
MODEL_VERSION = 1
MAX_ROW = 1_048_576
MAX_COLUMN = 16_384
MAX_RANGE_CELLS = 100_000
_A1 = re.compile(r"^\$?([A-Za-z]{1,3})\$?([1-9][0-9]*)$")
_CANONICAL_A1 = re.compile(r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")
_DEPENDENCY = re.compile(r"^[^!]+![A-Z]{1,3}[1-9][0-9]{0,6}$")
_ERROR_CODES = frozenset(
    {"#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#SPILL!", "#CALC!"}
)
_DATA_TYPES = frozenset({"blank", "number", "text", "boolean", "error"})
_ORIGINS = frozenset({"authored", "imported", "calculated"})
_CLASSIFICATIONS = frozenset(
    {"unsupported", "parse_error", "cycle", "resource_limit", "invalid_reference"}
)
_DIAGNOSTIC_CODES = frozenset(
    {
        "unsupported_formula",
        "parse_error",
        "cycle",
        "resource_limit",
        "invalid_reference",
        "blocked_dependency",
    }
)
_WORKBOOK_FIELDS = frozenset(
    {
        "schema_version",
        "model_version",
        "source_path",
        "metadata",
        "provenance",
        "bindings",
        "diagnostics",
        "sheets",
    }
)
_SHEET_FIELDS = frozenset({"name", "dimensions", "cells"})
_CELL_FIELDS = frozenset(
    {"address", "value", "data_type", "number_format", "formula", "provenance"}
)
_FORMULA_FIELDS = frozenset({"expression", "dependencies", "result"})
_ERROR_FIELDS = frozenset({"error", "message"})
_PROVENANCE_FIELDS = frozenset({"origin", "source"})
_BINDING_FIELDS = frozenset({"inputs", "outputs"})
_CELL_ID_FIELDS = frozenset({"sheet", "address"})
_DIAGNOSTIC_FIELDS = frozenset(
    {"code", "classification", "message", "sheet", "address", "function"}
)


class ModelError(ValueError):
    """The canonical document is not a supported workbook model."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class Provenance:
    origin: str
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"origin": self.origin}
        if self.source is not None:
            data["source"] = self.source
        return data


@dataclass(frozen=True, slots=True)
class CellId:
    sheet: str
    address: str

    def key(self) -> str:
        return f"{self.sheet}!{self.address}"

    def to_dict(self) -> dict[str, str]:
        return {"sheet": self.sheet, "address": self.address}


@dataclass(frozen=True, slots=True)
class Bindings:
    inputs: dict[str, CellId]
    outputs: dict[str, CellId]

    def to_dict(self) -> dict[str, Any]:
        return {
            "inputs": {name: target.to_dict() for name, target in self.inputs.items()},
            "outputs": {name: target.to_dict() for name, target in self.outputs.items()},
        }


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    classification: str
    message: str
    sheet: str | None = None
    address: str | None = None
    function: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "code": self.code,
            "classification": self.classification,
            "message": self.message,
        }
        if self.sheet is not None:
            data["sheet"] = self.sheet
        if self.address is not None:
            data["address"] = self.address
        if self.function is not None:
            data["function"] = self.function
        return data


@dataclass(frozen=True, slots=True)
class Formula:
    expression: str
    dependencies: tuple[str, ...] = ()
    result: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "expression": self.expression,
            "dependencies": list(self.dependencies),
            "result": _copy_json(self.result),
        }


@dataclass(frozen=True, slots=True)
class Cell:
    address: str
    value: Any
    data_type: str
    number_format: str | None = None
    formula: Formula | None = None
    provenance: Provenance | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "address": self.address,
            "value": _copy_json(self.value),
            "data_type": self.data_type,
        }
        if self.number_format is not None:
            data["number_format"] = self.number_format
        if self.formula is not None:
            data["formula"] = self.formula.to_dict()
        if self.provenance is not None:
            data["provenance"] = self.provenance.to_dict()
        return data


@dataclass(frozen=True, slots=True)
class Sheet:
    name: str
    cells: dict[str, Cell]
    dimensions: tuple[int, int, int, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "cells": {address: cell.to_dict() for address, cell in self.cells.items()},
        }
        if self.dimensions is not None:
            data["dimensions"] = list(self.dimensions)
        return data


@dataclass(frozen=True, slots=True)
class Workbook:
    schema_version: int
    model_version: int
    metadata: dict[str, Any]
    sheets: tuple[Sheet, ...]
    source_path: str | None = None
    provenance: Provenance | None = None
    bindings: Bindings | None = None
    diagnostics: tuple[Diagnostic, ...] = field(default_factory=tuple)

    def sheet(self, name: str) -> Sheet | None:
        for item in self.sheets:
            if item.name == name:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema_version": self.schema_version,
            "model_version": self.model_version,
            "metadata": _copy_json(self.metadata),
            "sheets": [sheet.to_dict() for sheet in self.sheets],
        }
        if self.source_path is not None:
            data["source_path"] = self.source_path
        if self.provenance is not None:
            data["provenance"] = self.provenance.to_dict()
        if self.bindings is not None:
            data["bindings"] = self.bindings.to_dict()
        if self.diagnostics:
            data["diagnostics"] = [item.to_dict() for item in self.diagnostics]
        return data

    def to_json(self) -> str:
        """Serialize with stable key order. Numbers follow JSON number rules."""
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )


def hydrate(document: bytes | str) -> Workbook:
    """Hydrate native types from canonical JSON bytes. Does not calculate."""
    if isinstance(document, str):
        raw = document.encode("utf-8")
    elif isinstance(document, bytes):
        raw = document
    else:
        raise ModelError("invalid_model", "document must be JSON bytes or text")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ModelError("invalid_model", "document is not valid UTF-8") from exc
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelError("invalid_model", "document is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ModelError("invalid_model", "document must be an object")
    return _workbook(parsed)


def calculate(workbook: Workbook) -> Workbook:
    """Calculate formula results on a hydrated workbook.

    Bindings already name canonical cells. This does not build a second
    calculation document. Unsupported formulas are classified, not guessed.
    """
    _require_bindings(workbook)
    cells = {
        f"{sheet.name}!{address}": cell
        for sheet in workbook.sheets
        for address, cell in sheet.cells.items()
    }
    sheet_of = {
        f"{sheet.name}!{address}": sheet.name
        for sheet in workbook.sheets
        for address in sheet.cells
    }
    analyzed: dict[str, tuple[tuple[str, ...], Diagnostic | None]] = {}
    for sheet in workbook.sheets:
        for address in sorted(sheet.cells):
            cell = sheet.cells[address]
            if cell.formula is None:
                continue
            key = f"{sheet.name}!{address}"
            analyzed[key] = _analyze(workbook, sheet.name, address, cell.formula.expression)

    formula_keys = set(analyzed)
    pending = {
        key: {item for item in deps if item in formula_keys}
        for key, (deps, _diagnostic) in analyzed.items()
    }
    ready = sorted(key for key, deps in pending.items() if not deps)
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for key in sorted(pending):
            if current in pending[key]:
                pending[key].remove(current)
                if not pending[key]:
                    ready.append(key)
        ready.sort()
    diagnostics = {
        key: diagnostic
        for key, (_deps, diagnostic) in analyzed.items()
        if diagnostic is not None
    }
    for key in sorted(set(formula_keys) - set(order)):
        diagnostics.setdefault(
            key,
            Diagnostic(
                "cycle",
                "cycle",
                "circular formula reference",
                sheet_of[key],
                key.rsplit("!", 1)[1],
            ),
        )

    results: dict[str, Any] = {}
    for key in order:
        diagnostic = diagnostics.get(key)
        if diagnostic is not None:
            continue
        deps = analyzed[key][0]
        blocked = sorted(
            dep
            for dep in deps
            if dep in formula_keys and (dep in diagnostics or dep not in results)
        )
        if blocked:
            upstream = diagnostics.get(blocked[0])
            classification = upstream.classification if upstream is not None else "unsupported"
            sheet_name, address = key.rsplit("!", 1)
            diagnostics[key] = Diagnostic(
                "blocked_dependency",
                classification,
                f"dependency {blocked[0]} was not calculated",
                sheet_name,
                address,
            )
            continue
        expression = cells[key].formula.expression if cells[key].formula is not None else ""
        _sheet_name, _address = key.rsplit("!", 1)
        outcome = _evaluate(workbook, _sheet_name, _address, expression, results)
        if isinstance(outcome, Diagnostic):
            diagnostics[key] = outcome
        else:
            results[key] = outcome

    return _with_calculation(workbook, cells, analyzed, results, diagnostics)


def _with_calculation(
    workbook: Workbook,
    cells: dict[str, Cell],
    analyzed: dict[str, tuple[tuple[str, ...], Diagnostic | None]],
    results: dict[str, Any],
    diagnostics: dict[str, Diagnostic],
) -> Workbook:
    sheets: list[Sheet] = []
    for sheet in workbook.sheets:
        updated: dict[str, Cell] = {}
        for address, cell in sheet.cells.items():
            key = f"{sheet.name}!{address}"
            formula = cell.formula
            if formula is not None and key in analyzed:
                deps, _diagnostic = analyzed[key]
                formula = Formula(
                    formula.expression,
                    deps,
                    results.get(key) if key in results else None,
                )
            updated[address] = Cell(
                cell.address,
                _copy_json(cell.value),
                cell.data_type,
                cell.number_format,
                formula,
                cell.provenance,
            )
        sheets.append(Sheet(sheet.name, updated, sheet.dimensions))
    ordered = tuple(
        diagnostics[key]
        for key in sorted(diagnostics, key=lambda item: _diagnostic_sort(diagnostics[item]))
    )
    return Workbook(
        workbook.schema_version,
        workbook.model_version,
        copy.deepcopy(workbook.metadata),
        tuple(sheets),
        workbook.source_path,
        workbook.provenance,
        workbook.bindings,
        ordered,
    )


def _diagnostic_sort(diagnostic: Diagnostic) -> tuple[str, str, str, str, str]:
    return (
        diagnostic.sheet or "",
        diagnostic.address or "",
        diagnostic.code,
        diagnostic.function or "",
        diagnostic.message,
    )


def _require_bindings(workbook: Workbook) -> None:
    if workbook.bindings is None:
        return
    for kind, mapping in (
        ("input", workbook.bindings.inputs),
        ("output", workbook.bindings.outputs),
    ):
        for name in sorted(mapping):
            target = mapping[name]
            sheet = workbook.sheet(target.sheet)
            cell = None if sheet is None else sheet.cells.get(target.address)
            if cell is None:
                raise ModelError(
                    "invalid_model",
                    f"{kind} binding '{name}' does not identify a cell",
                )
            if kind == "input" and cell.formula is not None:
                raise ModelError(
                    "invalid_model",
                    f"input binding '{name}' points at a formula cell",
                )


def _analyze(
    workbook: Workbook, sheet_name: str, address: str, expression: str
) -> tuple[tuple[str, ...], Diagnostic | None]:
    try:
        analysis = analyze_formula(expression)
    except (TypeError, ValueError, RecursionError):
        return (), _cell_diagnostic(
            "parse_error", "parse_error", "formula could not be parsed", sheet_name, address
        )
    dependencies: set[str] = set()
    unknown_sheets: list[str] = []
    oversized = False
    for reference in analysis.references:
        try:
            start_row, start_col = _parts(reference.start)
            end_row, end_col = _parts(reference.end or reference.start)
            # The formula parser accepts up to three column letters and any
            # row digits, so it lets through cells past XFD1048576, which
            # do not exist.
            if max(start_row, end_row) > MAX_ROW or max(start_col, end_col) > MAX_COLUMN:
                raise ValueError(reference.start)
        except ValueError:
            return (), _cell_diagnostic(
                "invalid_reference",
                "invalid_reference",
                f"invalid reference in {expression}",
                sheet_name,
                address,
            )
        rows = sorted((start_row, end_row))
        cols = sorted((start_col, end_col))
        count = (rows[1] - rows[0] + 1) * (cols[1] - cols[0] + 1)
        if count > MAX_RANGE_CELLS:
            oversized = True
            continue
        resolved = _resolve_sheet(workbook, reference.sheet, sheet_name)
        if resolved is None:
            unknown_sheets.append(reference.sheet or sheet_name)
            continue
        for row in range(rows[0], rows[1] + 1):
            for col in range(cols[0], cols[1] + 1):
                dependencies.add(f"{resolved}!{_encode(row, col)}")
    if oversized:
        return (), _cell_diagnostic(
            "resource_limit",
            "resource_limit",
            "reference range exceeds 100000 cells",
            sheet_name,
            address,
        )
    if unknown_sheets:
        return (), _cell_diagnostic(
            "invalid_reference",
            "invalid_reference",
            f"unknown worksheet {min(unknown_sheets)}",
            sheet_name,
            address,
        )
    unsupported = sorted(name for name in set(analysis.functions) if not _supported(name))
    diagnostic = None
    if unsupported:
        diagnostic = _cell_diagnostic(
            "unsupported_formula",
            "unsupported",
            f"unsupported function {unsupported[0]}",
            sheet_name,
            address,
            unsupported[0],
        )
    return tuple(sorted(dependencies)), diagnostic


def _cell_diagnostic(
    code: str,
    classification: str,
    message: str,
    sheet_name: str,
    address: str,
    function: str | None = None,
) -> Diagnostic:
    return Diagnostic(code, classification, message, sheet_name, address, function)


def _evaluate(
    workbook: Workbook,
    sheet_name: str,
    address: str,
    expression: str,
    results: dict[str, Any],
) -> Any:
    environment: dict[str, Any] = {}
    # The loop needs its own name: `address` is the cell being evaluated and
    # is used below to say where a diagnostic belongs.
    for sheet in workbook.sheets:
        for other, cell in sheet.cells.items():
            key = f"{sheet.name}!{other}"
            if cell.formula is not None:
                if key not in results:
                    continue
                scalar = _engine_scalar(results[key])
            else:
                scalar = _engine_scalar(cell.value)
            environment[f"{sheet.name.casefold()}!{other}"] = scalar
            if sheet.name == sheet_name:
                environment[other] = scalar
    outcome = evaluate_result(expression, environment, sheet_name)
    if isinstance(outcome, ArrayValue):
        return _cell_diagnostic(
            "unsupported_formula",
            "unsupported",
            "array results are unsupported",
            sheet_name,
            address,
        )
    if isinstance(outcome, ErrorValue):
        if outcome.message.startswith("unsupported function "):
            function = outcome.message.removeprefix("unsupported function ")
            return _cell_diagnostic(
                "unsupported_formula",
                "unsupported",
                f"unsupported function {function}",
                sheet_name,
                address,
                function,
            )
        if outcome.code not in _ERROR_CODES:
            return _cell_diagnostic(
                "parse_error", "parse_error", "formula could not be parsed", sheet_name, address
            )
        return {"error": outcome.code, "message": None}
    return _json_scalar(outcome)


def _supported(name: str) -> bool:
    try:
        status = function_status(name, "python")
    except KeyError:
        return False
    return status in {"evaluated", "conformance-tested"}


def _engine_scalar(value: Any) -> Any:
    if isinstance(value, dict):
        return ErrorValue(str(value["error"]))
    return value


def _json_scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool) or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return {"error": "#NUM!", "message": None}
        if value.is_integer() and abs(value) <= 2**53:
            return int(value)
        return value
    raise ModelError("invalid_model", "formula result is not a canonical value")


def _resolve_sheet(workbook: Workbook, raw: str | None, current: str) -> str | None:
    wanted = current if raw is None else raw
    for sheet in workbook.sheets:
        if sheet.name.casefold() == wanted.casefold():
            return sheet.name
    return None


def _parts(address: str) -> tuple[int, int]:
    match = _A1.fullmatch(address)
    if match is None:
        raise ValueError(address)
    column = 0
    for character in match.group(1).upper():
        column = column * 26 + ord(character) - ord("A") + 1
    return int(match.group(2)), column


def _encode(row: int, column: int) -> str:
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return f"{letters}{row}"


def _workbook(data: dict[str, Any]) -> Workbook:
    _fields(data, _WORKBOOK_FIELDS, ("schema_version", "model_version", "metadata", "sheets"), "")
    schema_version = _version(data["schema_version"], "schema_version", "unsupported_schema_version")
    model_version = _version(data["model_version"], "model_version", "unsupported_model_version")
    if not isinstance(data["metadata"], dict):
        raise ModelError("invalid_model", "metadata must be an object")
    source_path = None
    if "source_path" in data:
        source_path = _optional_string(data["source_path"], "source_path")
    provenance = _provenance(data["provenance"], "provenance") if "provenance" in data else None
    bindings = _bindings(data["bindings"]) if "bindings" in data else None
    diagnostics = _diagnostics(data["diagnostics"]) if "diagnostics" in data else ()
    sheets_raw = data["sheets"]
    if not isinstance(sheets_raw, list):
        raise ModelError("invalid_model", "sheets must be an array")
    sheets = tuple(_sheet(item, index) for index, item in enumerate(sheets_raw))
    seen: set[str] = set()
    for sheet in sheets:
        folded = sheet.name.casefold()
        if folded in seen:
            raise ModelError("invalid_model", f"duplicate sheet name '{sheet.name}'")
        seen.add(folded)
    return Workbook(
        schema_version,
        model_version,
        _copy_json(data["metadata"]),
        sheets,
        source_path,
        provenance,
        bindings,
        diagnostics,
    )


def _sheet(data: Any, index: int) -> Sheet:
    path = f"sheets[{index}]"
    if not isinstance(data, dict):
        raise ModelError("invalid_model", f"{path} must be an object")
    _fields(data, _SHEET_FIELDS, ("name", "cells"), f"{path}.")
    name = _required_string(data["name"], f"{path}.name")
    if "!" in name:
        raise ModelError("invalid_model", f"{path}.name must not contain '!'")
    dimensions = _dimensions(data["dimensions"], f"{path}.dimensions") if "dimensions" in data else None
    cells_raw = data["cells"]
    if not isinstance(cells_raw, dict):
        raise ModelError("invalid_model", f"{path}.cells must be an object")
    cells: dict[str, Cell] = {}
    for address in sorted(cells_raw):
        if not isinstance(address, str) or _canonical_address(address) is None:
            raise ModelError(
                "invalid_model",
                f"{path}.cells key '{address}' is not a canonical A1 reference",
            )
        cell = _cell(cells_raw[address], f"{path}.cells.{address}")
        if cell.address != address:
            raise ModelError(
                "invalid_model",
                f"{path}.cells.{address}.address does not match its map key",
            )
        cells[address] = cell
    return Sheet(name, cells, dimensions)


def _cell(data: Any, path: str) -> Cell:
    if not isinstance(data, dict):
        raise ModelError("invalid_model", f"{path} must be an object")
    _fields(data, _CELL_FIELDS, ("address", "value", "data_type"), f"{path}.")
    address = _required_string(data["address"], f"{path}.address")
    if _canonical_address(address) is None:
        raise ModelError("invalid_model", f"{path}.address is not a canonical A1 reference")
    value = _value(data["value"], f"{path}.value")
    data_type = data["data_type"]
    if data_type not in _DATA_TYPES:
        raise ModelError("invalid_model", f"{path}.data_type is not a supported data_type")
    if data_type != _value_type(value):
        raise ModelError("invalid_model", f"{path}.data_type does not match the cell value")
    number_format = None
    if "number_format" in data:
        number_format = _optional_string(data["number_format"], f"{path}.number_format")
    formula = _formula(data["formula"], f"{path}.formula") if "formula" in data else None
    provenance = _provenance(data["provenance"], f"{path}.provenance") if "provenance" in data else None
    return Cell(address, value, data_type, number_format, formula, provenance)


def _formula(data: Any, path: str) -> Formula:
    if not isinstance(data, dict):
        raise ModelError("invalid_model", f"{path} must be an object")
    _fields(data, _FORMULA_FIELDS, ("expression", "dependencies", "result"), f"{path}.")
    expression = _required_string(data["expression"], f"{path}.expression")
    dependencies = data["dependencies"]
    if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
        raise ModelError("invalid_model", f"{path}.dependencies must be an array of strings")
    for index, item in enumerate(dependencies):
        if _DEPENDENCY.fullmatch(item) is None:
            raise ModelError(
                "invalid_model",
                f"{path}.dependencies[{index}] is not a Sheet!A1 reference",
            )
    result = _value(data["result"], f"{path}.result")
    return Formula(expression, tuple(dependencies), result)


def _value(data: Any, path: str) -> Any:
    if data is None or isinstance(data, bool) or isinstance(data, str):
        return _copy_json(data)
    if isinstance(data, (int, float)) and not isinstance(data, bool):
        if isinstance(data, float) and not math.isfinite(data):
            raise ModelError("invalid_model", f"{path} must be a finite number")
        return data
    if isinstance(data, dict):
        _fields(data, _ERROR_FIELDS, ("error", "message"), f"{path}.")
        code = data["error"]
        if code not in _ERROR_CODES:
            raise ModelError("invalid_model", f"{path}.error is not a supported error")
        message = data["message"]
        if message is not None and not isinstance(message, str):
            raise ModelError("invalid_model", f"{path}.message must be a string or null")
        return {"error": code, "message": message}
    raise ModelError(
        "invalid_model",
        f"{path} must be null, a number, a string, a boolean, or an error",
    )


def _value_type(value: Any) -> str:
    if value is None:
        return "blank"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "text"
    return "error"


def _provenance(data: Any, path: str) -> Provenance:
    if not isinstance(data, dict):
        raise ModelError("invalid_model", f"{path} must be an object")
    _fields(data, _PROVENANCE_FIELDS, ("origin",), f"{path}.")
    origin = data["origin"]
    if origin not in _ORIGINS:
        raise ModelError("invalid_model", f"{path}.origin is not a supported origin")
    source = _optional_string(data["source"], f"{path}.source") if "source" in data else None
    return Provenance(origin, source)


def _bindings(data: Any) -> Bindings:
    if not isinstance(data, dict):
        raise ModelError("invalid_model", "bindings must be an object")
    _fields(data, _BINDING_FIELDS, ("inputs", "outputs"), "bindings.")
    return Bindings(_binding_map(data["inputs"], "bindings.inputs"), _binding_map(data["outputs"], "bindings.outputs"))


def _binding_map(data: Any, path: str) -> dict[str, CellId]:
    if not isinstance(data, dict):
        raise ModelError("invalid_model", f"{path} must be an object")
    mapped: dict[str, CellId] = {}
    for name in sorted(data):
        if not isinstance(name, str) or not name:
            raise ModelError("invalid_model", "binding name must not be empty")
        item = data[name]
        item_path = f"{path}.{name}"
        if not isinstance(item, dict):
            raise ModelError("invalid_model", f"{item_path} must be an object")
        _fields(item, _CELL_ID_FIELDS, ("sheet", "address"), f"{item_path}.")
        sheet = _required_string(item["sheet"], f"{item_path}.sheet")
        address = _required_string(item["address"], f"{item_path}.address")
        if _canonical_address(address) is None:
            raise ModelError("invalid_model", f"{item_path}.address is not a canonical A1 reference")
        mapped[name] = CellId(sheet, address)
    return mapped


def _diagnostics(data: Any) -> tuple[Diagnostic, ...]:
    if not isinstance(data, list):
        raise ModelError("invalid_model", "diagnostics must be an array")
    items = []
    for index, item in enumerate(data):
        path = f"diagnostics[{index}]"
        if not isinstance(item, dict):
            raise ModelError("invalid_model", f"{path} must be an object")
        _fields(item, _DIAGNOSTIC_FIELDS, ("code", "classification", "message"), f"{path}.")
        code = item["code"]
        classification = item["classification"]
        message = item["message"]
        if code not in _DIAGNOSTIC_CODES:
            raise ModelError("invalid_model", f"{path}.code is not a supported diagnostic")
        if classification not in _CLASSIFICATIONS:
            raise ModelError("invalid_model", f"{path}.classification is not a supported classification")
        if not isinstance(message, str):
            raise ModelError("invalid_model", f"{path}.message must be a string")
        sheet = _optional_present_string(item, "sheet", path)
        address = _optional_present_string(item, "address", path)
        if address is not None and _canonical_address(address) is None:
            raise ModelError("invalid_model", f"{path}.address is not a canonical A1 reference")
        function = _optional_present_string(item, "function", path)
        items.append(Diagnostic(code, classification, message, sheet, address, function))
    return tuple(items)


def _dimensions(data: Any, path: str) -> tuple[int, int, int, int]:
    if not isinstance(data, list) or len(data) != 4 or not all(_is_int(item) and item >= 1 for item in data):
        raise ModelError("invalid_model", f"{path} must be four positive integers")
    min_row, max_row, min_col, max_col = data
    if min_row > max_row or min_col > max_col or max_row > MAX_ROW or max_col > MAX_COLUMN:
        raise ModelError("invalid_model", f"{path} is out of order")
    return (min_row, max_row, min_col, max_col)


def _version(value: Any, path: str, unsupported: str) -> int:
    if not _is_int(value):
        raise ModelError("invalid_model", f"{path} must be an integer")
    if value != 1:
        raise ModelError(unsupported, str(value))
    return value


def _fields(data: dict[str, Any], allowed: frozenset[str], required: tuple[str, ...], prefix: str) -> None:
    for key in required:
        if key not in data:
            raise ModelError("missing_required_field", f"{prefix}{key}")
    for key in sorted(data):
        if key not in allowed:
            raise ModelError("unknown_field", f"{prefix}{key}")


def _required_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ModelError("invalid_model", f"{path} must be a non-empty string")
    return value


def _optional_string(value: Any, path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ModelError("invalid_model", f"{path} must be a string or null")
    return value


def _optional_present_string(data: dict[str, Any], key: str, path: str) -> str | None:
    if key not in data:
        return None
    value = data[key]
    if not isinstance(value, str) or not value:
        raise ModelError("invalid_model", f"{path}.{key} must be a non-empty string")
    return value


def _canonical_address(address: str) -> str | None:
    if _CANONICAL_A1.fullmatch(address) is None:
        return None
    try:
        row, column = _parts(address)
    except ValueError:
        return None
    if row > MAX_ROW or column > MAX_COLUMN:
        return None
    return address


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, allow_nan=False, ensure_ascii=False))

