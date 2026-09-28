"""Bounded, revision-aware agent operations over an owned workbook session.

The host chooses the workbook and export directory. Agents receive no operation
for selecting arbitrary source paths, executing code, or interpreting workbook
text as instructions. Callers should mutate the supplied model through this
adapter; its lock does not serialize independent host calls on that model.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
from threading import RLock
from typing import Any

from .catalog import _catalog_file
from .toolkit import WorkbookModel
from .workbook import UnsupportedWorkbook, WorkbookError, _cell_position, _normal_address
from .xlsx import export_xlsx

MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_PAGE_CELLS = 100
MAX_DIAGNOSTICS = 100
MAX_DIRECT_DEPENDENCIES = 100
MAX_INPUTS = 100
MAX_EDITS = 100
MAX_OUTPUTS = 100
_UINT64_MAX = (1 << 64) - 1
_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.xlsx\Z")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_OPERATIONS = frozenset({"discover", "describe", "read", "calculate", "explain", "preview_inputs", "set_inputs", "edit", "export"})


class _OperationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def _fail(code: str, message: str):
    raise _OperationError(code, message)


def _bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def operation_catalog() -> dict[str, Any]:
    """Return a detached copy of the installed canonical operation catalog."""
    return json.loads(_catalog_file("agent-operations.json").read_text(encoding="utf-8"))


def _arguments(arguments: dict, allowed: set[str], required: set[str] = frozenset()):
    if set(arguments) - allowed:
        _fail("invalid_request", "unknown argument properties: " + ", ".join(sorted(set(arguments) - allowed)))
    if required - set(arguments):
        _fail("invalid_request", "missing argument properties: " + ", ".join(sorted(required - set(arguments))))


def _integer(value: Any, name: str, minimum: int, maximum: int = _UINT64_MAX) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail("invalid_request", f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        _fail("invalid_request", f"{name} must be nonempty text")
    return value


def _clip_diagnostics(diagnostics: list[dict]) -> dict:
    return {"diagnostics": deepcopy(diagnostics[:MAX_DIAGNOSTICS]), "diagnostic_count": len(diagnostics), "diagnostics_truncated": len(diagnostics) > MAX_DIAGNOSTICS}


def _compact(report: dict, *, stale: bool = False) -> dict:
    return {"revision": report["revision"], "outputs": deepcopy(report["outputs"]), **_clip_diagnostics(report["diagnostics"]), "evaluated_cell_count": len(report["evaluated_cells"]), "stale": bool(report["stale"] or stale)}


def _sheet(snapshot: dict, name: Any) -> dict:
    name = _text(name, "sheet")
    folded = name.translate(_ASCII_LOWER)
    for sheet in snapshot["sheets"]:
        if sheet["name"].translate(_ASCII_LOWER) == folded:
            return sheet
    _fail("invalid_reference", f"unknown worksheet {name}")


def _canonical(address: Any) -> str:
    if not isinstance(address, str):
        _fail("invalid_reference", "cell address must be A1 text")
    try:
        return _normal_address(address)
    except (ValueError, TypeError, WorkbookError) as error:
        raise _OperationError("invalid_reference", str(error)) from error


def _binding(snapshot: dict, binding: dict) -> tuple[dict, str]:
    return _sheet(snapshot, binding["sheet"]), _canonical(binding["address"])


def _key(snapshot: dict, binding: dict) -> str:
    sheet, address = _binding(snapshot, binding)
    return f"{sheet['name']}!{address}"


def _output(snapshot: dict, name: Any) -> tuple[str, dict]:
    name = _text(name, "output")
    if name not in snapshot["outputs"]:
        _fail("invalid_input", f"unknown output {name}")
    return name, snapshot["outputs"][name]


def _selected_outputs(snapshot: dict, arguments: dict) -> dict:
    if "outputs" not in arguments:
        names = sorted(snapshot["outputs"])
    else:
        names = arguments["outputs"]
        if not isinstance(names, list) or any(not isinstance(name, str) or not name for name in names):
            _fail("invalid_request", "outputs must be a nonempty list of output names")
    if not 1 <= len(names) <= MAX_OUTPUTS or len(set(names)) != len(names):
        _fail("invalid_request" if "outputs" in arguments else "invalid_input", "select 1 to 100 unique named outputs")
    for name in names:
        _output(snapshot, name)
    return {name: deepcopy(snapshot["outputs"][name]) for name in sorted(names)}


def _expected(snapshot: dict, arguments: dict, *, required: bool = False):
    if "expected_revision" not in arguments:
        if required:
            _fail("invalid_request", "expected_revision is required")
        return
    expected = _integer(arguments["expected_revision"], "expected_revision", 0)
    if expected != snapshot["revision"]:
        _fail("revision_conflict", f"expected revision {expected} does not match {snapshot['revision']}")


def _paging(snapshot: dict, arguments: dict) -> tuple[int, int]:
    offset = _integer(arguments.get("offset", 0), "offset", 0)
    limit = _integer(arguments.get("limit", 40), "limit", 1, MAX_PAGE_CELLS)
    _expected(snapshot, arguments, required=offset > 0)
    return offset, limit


def _scalar_argument(value: Any):
    if value is None or type(value) in (str, bool, int, float):
        return
    if not isinstance(value, dict) or set(value) - {"error", "message"} or not isinstance(value.get("error"), str) or "message" in value and not isinstance(value["message"], str):
        _fail("invalid_request", "value must be a scalar or a valid error object")


def _style_argument(style: Any):
    if style is None:
        return
    if not isinstance(style, dict):
        _fail("invalid_request", "style must be an object or null")
    _arguments(style, {"number_format", "bold", "font_color", "fill_color", "horizontal"})
    for name, value in style.items():
        if name == "bold" and type(value) is not bool or name != "bold" and not isinstance(value, str):
            _fail("invalid_request", f"invalid style property type: {name}")
    if "horizontal" in style and style["horizontal"] not in {"left", "center", "right"}:
        _fail("invalid_request", "horizontal must be left, center or right")


def _inputs(arguments: dict, snapshot: dict) -> dict:
    values = arguments["values"]
    if not isinstance(values, dict) or not 1 <= len(values) <= MAX_INPUTS:
        _fail("invalid_request", "values must contain 1 to 100 named inputs")
    for name, value in values.items():
        _scalar_argument(value)
        if name not in snapshot["inputs"]:
            _fail("invalid_input", f"unknown input {name}")
    return values


class AgentWorkbook:
    """Own an agent operation boundary around one Python or explicit Rust model.

    Operations are serialized through this adapter. Read and calculation work
    uses detached snapshots, so paging and report revisions remain coherent
    even if the host independently accesses the supplied model.
    """

    def __init__(self, model: WorkbookModel, *, output_dir: str | Path):
        if not isinstance(model, WorkbookModel):
            raise TypeError("model must be a WorkbookModel")
        self._model = model
        self._output_dir = Path(output_dir).expanduser().resolve()
        self._lock = RLock()

    @property
    def revision(self) -> int:
        with self._lock:
            return self._model.revision

    def _detached(self, snapshot: dict, *, outputs: dict | None = None) -> WorkbookModel:
        document = deepcopy(snapshot)
        if outputs is not None:
            document["outputs"] = deepcopy(outputs)
        detached = WorkbookModel(document=document, backend=self._model.backend)
        # Source bytes and their original model are immutable. Keep this same
        # preservation authority on previews and exports instead of reimporting
        # a source path that the host might since have changed.
        detached._source_baseline = self._model._source_baseline
        detached._import_metadata = deepcopy(self._model._import_metadata)
        return detached

    @staticmethod
    def _envelope(request_id: str | None, operation: str | None, revision: int, result: Any) -> dict:
        return {"schema_version": 1, "id": request_id, "operation": operation, "ok": True, "revision": revision, "result": result}

    @staticmethod
    def _check_response(response: dict):
        if len(_bytes(response)) > MAX_RESPONSE_BYTES:
            _fail("resource_limit", "response exceeds 256 KiB; narrow the selection or reduce the page size")

    def _error(self, request_id: str | None, operation: str | None, revision: int, error: Exception) -> dict:
        if isinstance(error, _OperationError):
            code, message = error.code, error.message
        elif isinstance(error, UnsupportedWorkbook):
            code, message = "unsupported_operation", str(error)
        elif isinstance(error, (FileExistsError, OSError, WorkbookError)):
            code, message = "export_error", str(error)
        elif hasattr(error, "code"):
            code, message = str(error.code), getattr(error, "message", str(error))
        elif isinstance(error, (ValueError, TypeError, OverflowError)):
            message = str(error)
            prefix, separator, _ = message.partition(":")
            code = prefix if separator and re.fullmatch(r"[a-z][a-z_]*", prefix) else "invalid_input"
        else:
            code, message = "invalid_request", "operation could not be completed"
        response = {"schema_version": 1, "id": request_id, "operation": operation, "ok": False, "revision": revision, "error": {"code": code, "message": message}}
        try:
            self._check_response(response)
        except (ValueError, TypeError, UnicodeError):
            # Oversized untrusted operation names and error messages must not
            # recursively create another oversized failure envelope.
            response.update(operation=None, error={"code": "resource_limit", "message": "request or response exceeds the agent byte budget"})
        return response

    def call(self, operation: str, arguments: dict | None = None, request_id: str | None = None) -> dict:
        request = {"operation": operation, "arguments": {} if arguments is None else arguments}
        if request_id is not None:
            request["id"] = request_id
        return self.handle(request)

    def handle(self, request: dict) -> dict:
        with self._lock:
            request_id = None
            operation = None
            revision = self._model.revision
            try:
                if not isinstance(request, dict):
                    _fail("invalid_request", "request must be an object")
                # Correlation fields are independently valid even when the
                # rest of an envelope is malformed. Never echo invalid UTF-8.
                try:
                    if isinstance(request.get("id"), str) and len(request["id"].encode("utf-8")) <= 128:
                        request_id = request["id"]
                except UnicodeError:
                    pass
                try:
                    candidate = request.get("operation")
                    if isinstance(candidate, str) and candidate:
                        candidate.encode("utf-8")
                        operation = candidate
                except UnicodeError:
                    pass
                try:
                    size = len(_bytes(request))
                except (TypeError, ValueError, UnicodeError) as error:
                    raise _OperationError("invalid_request", "request must contain finite JSON data") from error
                if size > MAX_REQUEST_BYTES:
                    _fail("resource_limit", "request exceeds 1 MiB")
                if set(request) - {"id", "operation", "arguments"}:
                    _fail("invalid_request", "unknown request properties")
                if "id" in request and (not isinstance(request["id"], str) or len(request["id"].encode("utf-8")) > 128):
                    _fail("invalid_request", "id must be text of at most 128 UTF-8 bytes")
                if not isinstance(operation, str) or not operation:
                    _fail("invalid_request", "operation must be nonempty text")
                arguments = request.get("arguments", {})
                if not isinstance(arguments, dict):
                    _fail("invalid_request", "arguments must be an object")
                if operation not in _OPERATIONS:
                    _fail("unknown_operation", "unknown operation")
                snapshot = self._model.to_dict()
                revision = snapshot["revision"]
                result, revision = self._dispatch(operation, arguments, snapshot, request_id)
                response = self._envelope(request_id, operation, revision, result)
                self._check_response(response)
                return response
            except Exception as error:
                return self._error(request_id, operation, revision, error)

    def _dispatch(self, operation: str, arguments: dict, snapshot: dict, request_id: str | None) -> tuple[Any, int]:
        revision = snapshot["revision"]
        if operation == "discover":
            _arguments(arguments, set())
            return operation_catalog(), revision
        if operation == "describe":
            _arguments(arguments, set())
            return {"sheets": [{"id": sheet["id"], "name": sheet["name"], "populated_cells": len(sheet["cells"])} for sheet in snapshot["sheets"]], "inputs": deepcopy(snapshot["inputs"]), "outputs": deepcopy(snapshot["outputs"]), "limits": operation_catalog()["limits"], "source": {"kind": "xlsx" if self._model._source_baseline is not None else "authored"}}, revision
        if operation in {"read", "explain"}:
            allowed = {"output", "offset", "limit", "expected_revision"}
            if operation == "read":
                allowed |= {"sheet", "range"}
            _arguments(arguments, allowed, {"output"} if operation == "explain" else set())
            return self._read(snapshot, arguments, explain=operation == "explain"), revision
        if operation == "calculate":
            _arguments(arguments, {"outputs", "workers"})
            workers = _integer(arguments.get("workers", 1), "workers", 1, 8)
            detached = self._detached(snapshot, outputs=_selected_outputs(snapshot, arguments))
            report = detached.calculate(workers=workers)
            return _compact(report, stale=self._model.revision != revision), revision
        if operation == "preview_inputs":
            _arguments(arguments, {"values", "expected_revision", "outputs"}, {"values", "expected_revision"})
            _expected(snapshot, arguments, required=True)
            values = _inputs(arguments, snapshot)
            self._preflight_imported(snapshot, self._input_edit_records(snapshot, values))
            detached = self._detached(snapshot, outputs=_selected_outputs(snapshot, arguments))
            before = detached.calculate()
            proposed = detached.set_inputs(values, expected_revision=revision)
            after_snapshot, after = detached.to_dict(), detached.calculate()
            changes = []
            for name in sorted(values):
                sheet, address = _binding(snapshot, snapshot["inputs"][name])
                updated_sheet = _sheet(after_snapshot, sheet["name"])
                changes.append({"sheet": sheet["name"], "address": address, "before": deepcopy(sheet["cells"].get(address, {}).get("value")), "after": deepcopy(updated_sheet["cells"].get(address, {}).get("value"))})
            stale = self._model.revision != revision
            return {"applied": False, "base_revision": revision, "proposed_revision": proposed, "changes": changes, "before": _compact(before, stale=stale), "after": _compact(after, stale=stale)}, revision
        if operation in {"set_inputs", "edit"}:
            return self._mutate(operation, arguments, snapshot, request_id)
        if operation == "export":
            _arguments(arguments, {"filename", "expected_revision"}, {"filename", "expected_revision"})
            _expected(snapshot, arguments, required=True)
            filename = arguments["filename"]
            if not isinstance(filename, str) or _FILENAME.fullmatch(filename) is None:
                _fail("invalid_request", "filename must be one safe .xlsx filename")
            target = self._output_dir / filename
            if self._output_dir.resolve() != self._output_dir or target.parent.resolve() != self._output_dir:
                _fail("export_error", "configured output directory changed")
            if os.path.lexists(target):
                _fail("export_error", "output path already exists")
            receipt = {"filename": filename, "revision": revision, "bytes": 999_999_999}
            self._check_response(self._envelope(request_id, operation, revision, receipt))
            # Calculate and publish exactly the revision checked above, keeping
            # imported package bytes even if an independent host edit races us.
            try:
                result = export_xlsx(self._detached(snapshot), target)
            except (WorkbookError, OSError, ValueError) as error:
                raise _OperationError("export_error", str(error)) from error
            return {"filename": filename, "revision": revision, "bytes": result.stat().st_size}, revision
        _fail("unknown_operation", "unknown operation")

    @staticmethod
    def _input_edit_records(snapshot: dict, values: dict) -> list[dict]:
        return [{"sheet": snapshot["inputs"][name]["sheet"], "address": snapshot["inputs"][name]["address"], "value": value} for name, value in values.items()]

    def _preflight_imported(self, snapshot: dict, edits: list[dict]):
        baseline = self._model._source_baseline
        if baseline is None:
            return
        original = json.loads(baseline.document_json)
        original_sheets = {sheet["name"]: sheet for sheet in original["sheets"]}
        for edit in edits:
            sheet, address = _binding(snapshot, edit)
            previous = original_sheets.get(sheet["name"], {}).get("cells", {}).get(address)
            if "style" in edit:
                _fail("unsupported_operation", "agent edits to imported styles are unsupported")
            if previous is None:
                _fail("unsupported_operation", "agent edits require an original imported cell")
            if previous.get("blocked_reason") is not None:
                _fail("unsupported_operation", "cannot edit preserved-only imported content")
            if baseline.uses_1904_date_system and edit.get("formula") is not None:
                _fail("unsupported_operation", "formula edits in imported 1904 workbooks are unsupported")

    def _mutate(self, operation: str, arguments: dict, snapshot: dict, request_id: str | None) -> tuple[dict, int]:
        field = "values" if operation == "set_inputs" else "edits"
        _arguments(arguments, {field, "expected_revision"}, {field, "expected_revision"})
        _expected(snapshot, arguments, required=True)
        previous = snapshot["revision"]
        changed = []
        if operation == "set_inputs":
            values = _inputs(arguments, snapshot)
            self._preflight_imported(snapshot, self._input_edit_records(snapshot, values))
            for name in sorted(values):
                sheet, address = _binding(snapshot, snapshot["inputs"][name])
                changed.append({"sheet": sheet["name"], "address": address, "fields": ["value"]})
        else:
            edits = arguments["edits"]
            if not isinstance(edits, list) or not 1 <= len(edits) <= MAX_EDITS:
                _fail("invalid_request", "edits must contain 1 to 100 edit records")
            for edit in edits:
                if not isinstance(edit, dict):
                    _fail("invalid_request", "edit records must be objects")
                _arguments(edit, {"sheet", "address", "value", "formula", "style"}, {"sheet", "address"})
                _text(edit["sheet"], "sheet")
                _text(edit["address"], "address")
                if not ({"value", "formula", "style"} & set(edit)) or {"value", "formula"} <= set(edit):
                    _fail("invalid_request", "edits need value, formula or style and cannot include both value and formula")
                if "value" in edit:
                    _scalar_argument(edit["value"])
                if "formula" in edit and edit["formula"] is not None and not isinstance(edit["formula"], str):
                    _fail("invalid_request", "formula must be text or null")
                if "style" in edit:
                    _style_argument(edit["style"])
                sheet, address = _binding(snapshot, edit)
                fields = [name for name in ("value", "formula", "style") if name in edit]
                changed.append({"sheet": sheet["name"], "address": address, "fields": fields})
            self._preflight_imported(snapshot, edits)
        if previous == _UINT64_MAX:
            _fail("resource_limit", "revision counter exhausted")
        receipt = {"previous_revision": previous, "revision": previous + 1, "changed_cells": changed}
        # No commit is allowed before its compact success response is known to
        # fit. Large input/formula/style payloads never appear in this receipt.
        self._check_response(self._envelope(request_id, operation, previous + 1, receipt))
        if operation == "set_inputs":
            revision = self._model.set_inputs(values, expected_revision=previous)
        else:
            revision = self._model.apply(edits, expected_revision=previous)
        receipt["revision"] = revision
        return receipt, revision

    def _read(self, snapshot: dict, arguments: dict, *, explain: bool) -> dict:
        offset, limit = _paging(snapshot, arguments)
        has_sheet, has_output = "sheet" in arguments, "output" in arguments
        if has_sheet == has_output or "range" in arguments and not has_sheet:
            _fail("invalid_request", "select exactly one sheet or output; range requires sheet")
        detached = self._detached(snapshot)
        inspection = detached.inspect()
        dependencies = inspection.get("dependencies", {})
        cells = {f"{sheet['name']}!{address}": cell for sheet in snapshot["sheets"] for address, cell in sheet["cells"].items()}
        sheets = {sheet["name"]: sheet for sheet in snapshot["sheets"]}
        order = {sheet["name"]: index for index, sheet in enumerate(snapshot["sheets"])}
        binding = None
        if has_output:
            name, binding = _output(snapshot, arguments["output"])
            selected, pending = set(), [_key(snapshot, binding)]
            while pending:
                current = pending.pop()
                if current not in selected:
                    selected.add(current)
                    pending.extend(dependencies.get(current, ()))
        else:
            selected_sheet = _sheet(snapshot, arguments["sheet"])
            selected = {f"{selected_sheet['name']}!{address}" for address in selected_sheet["cells"]}
            if "range" in arguments:
                reference = _text(arguments["range"], "range")
                endpoints = reference.split(":")
                if len(endpoints) not in (1, 2):
                    _fail("invalid_reference", "range must be an A1 cell or rectangle")
                first, last = _canonical(endpoints[0]), _canonical(endpoints[-1])
                c1, r1 = _cell_position(first)
                c2, r2 = _cell_position(last)
                selected = {current for current in selected if min(c1, c2) <= _cell_position(current.rsplit("!", 1)[1])[0] <= max(c1, c2) and min(r1, r2) <= _cell_position(current.rsplit("!", 1)[1])[1] <= max(r1, r2)}

        def position(current: str):
            sheet, address = current.rsplit("!", 1)
            column, row = _cell_position(address)
            return order[sheet], row, column

        ordered = sorted(selected, key=position)
        page = []
        calculation = None
        if explain:
            calculation = self._detached(snapshot, outputs={name: binding}).calculate()
        for current in ordered[offset:offset + limit]:
            sheet, address = current.rsplit("!", 1)
            content = deepcopy(cells.get(current, {"value": None}))
            direct = dependencies.get(current, [])
            record = {"sheet_id": sheets[sheet]["id"], "sheet": sheet, "address": address, "content": content, "dependencies": direct[:MAX_DIRECT_DEPENDENCIES], "dependency_count": len(direct), "dependencies_truncated": len(direct) > MAX_DIRECT_DEPENDENCIES}
            if calculation is not None:
                available = current in calculation["values"]
                origin = "unavailable" if not available else "calculated" if content.get("formula") is not None else "blank" if content.get("value") is None else "authored"
                record.update(calculated_value=deepcopy(calculation["values"].get(current)), value_origin=origin)
            page.append(record)
        diagnostics = calculation["diagnostics"] if calculation is not None else [item for item in inspection["diagnostics"] if "sheet" not in item or f"{item['sheet']}!{item.get('address', '')}" in selected]
        more = offset + len(page) < len(ordered)
        result = {"cells": page, "offset": offset, "limit": limit, "total_cells": len(ordered), "next_offset": offset + len(page) if more else None, "truncated": more, **_clip_diagnostics(diagnostics)}
        if calculation is not None:
            result.update(output=name, binding=deepcopy(binding), value=deepcopy(calculation["outputs"].get(name)))
        return result
