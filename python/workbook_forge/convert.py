"""Explicit conversion edges between the canonical and toolkit workbook shapes.

Spec 09 step 0. ``to_toolkit`` turns a canonical ``model.Workbook`` into the
normalised toolkit document and ``to_canonical`` turns a toolkit document into
a hydrated canonical workbook. Each returns a ``Conversion`` naming every field
path it dropped and every one it added, in the canonical hydrator's own path
grammar, so nothing is lost or invented silently.

Neither direction judges a document itself. The toolkit reader
(``python_engine._normalize_model``) is the only judge of a toolkit document and
``model.hydrate`` the only judge of a canonical one; a refusal from either
becomes a ``ConversionError`` carrying that reader's code and message.

Binding typing is not mechanical (spec 09 section 5 row 0): ``to_toolkit``
carries an input binding only when the caller supplies its kind, and drops and
lists it otherwise.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import json
import math
from typing import TYPE_CHECKING, Any

from .model import (
    MODEL_VERSION,
    Bindings,
    Cell,
    CellId,
    Diagnostic,
    Formula,
    ModelError,
    Provenance,
    Sheet,
    Workbook,
    _analyze,
    _diagnostic_sort,
    _value_type,
    hydrate,
)
from .python_engine import ToolkitError, _coordinates, _normalize_model

if TYPE_CHECKING:
    from .envelope import PreservationEnvelope

# 2^53: the largest magnitude at which every integer is one float.
_EXACT_INTEGER = 1 << 53


@dataclass(frozen=True)
class Conversion:
    """Every field path one conversion dropped and every one it added."""

    dropped: tuple[str, ...]
    added: tuple[str, ...]

    def to_dict(self) -> dict[str, list[str]]:
        return {"dropped": list(self.dropped), "added": list(self.added)}


class ConversionError(ValueError):
    """The target shape's reader refused the converted document."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class _Ledger:
    """Collects dropped and added paths; sorted by code point, no duplicates."""

    def __init__(self) -> None:
        self._dropped: set[str] = set()
        self._added: set[str] = set()

    def dropped(self, path: str) -> None:
        self._dropped.add(path)

    def added(self, path: str) -> None:
        self._added.add(path)

    def conversion(self) -> Conversion:
        return Conversion(tuple(sorted(self._dropped)), tuple(sorted(self._added)))


def to_toolkit(
    workbook: Workbook, *, input_kinds: Mapping[str, str] | None = None
) -> tuple[dict[str, Any], Conversion]:
    """The normalised toolkit document for a canonical workbook.

    ``input_kinds`` maps an input binding name to its toolkit kind; an input it
    does not name is dropped and listed. Naming an input the workbook does not
    bind is refused with code ``unknown_input``.
    """
    try:
        # A hand-built workbook with another version is refused with the
        # model's own code, the same as writing it would be.
        workbook.require_supported_versions()
    except ModelError as error:
        raise ConversionError(error.code, error.message) from error
    kinds = dict(input_kinds or {})
    bound = set(workbook.bindings.inputs) if workbook.bindings is not None else set()
    unknown = sorted(set(kinds) - bound)
    if unknown:
        raise ConversionError("unknown_input", f"unknown input {unknown[0]}")
    ledger = _Ledger()
    ledger.dropped("model_version")
    for key in workbook.metadata:
        ledger.dropped(f"metadata.{key}")
    if workbook.provenance is not None:
        ledger.dropped("provenance")
    for index in range(len(workbook.diagnostics)):
        ledger.dropped(f"diagnostics[{index}]")
    ledger.added("revision")
    sheets = []
    for index, sheet in enumerate(workbook.sheets):
        prefix = f"sheets[{index}]"
        ledger.added(f"{prefix}.id")
        if sheet.dimensions is not None:
            ledger.dropped(f"{prefix}.dimensions")
        cells: dict[str, Any] = {}
        for address, cell in sheet.cells.items():
            cell_path = f"{prefix}.cells.{address}"
            ledger.dropped(f"{cell_path}.data_type")
            if cell.provenance is not None:
                ledger.dropped(f"{cell_path}.provenance")
            value = _toolkit_value(cell.value)
            item: dict[str, Any] = {"value": value}
            if cell.formula is not None:
                ledger.dropped(f"{cell_path}.formula.dependencies")
                ledger.dropped(f"{cell_path}.formula.result")
                item["formula"] = cell.formula.expression
                if value is not None:
                    # The toolkit keeps a formula cell's stored value beside
                    # the formula, never in `value`.
                    item["value"] = None
                    item["cached_value"] = value
                    ledger.added(f"{cell_path}.cached_value")
            if cell.number_format is not None:
                item["style"] = {"number_format": cell.number_format}
            cells[address] = item
        sheets.append(
            {"id": f"sheet-{index + 1}", "name": sheet.name, "cells": cells, "column_widths": {}}
        )
    inputs: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    if workbook.bindings is not None:
        for name, target in workbook.bindings.inputs.items():
            if name not in kinds:
                ledger.dropped(f"bindings.inputs.{name}")
                continue
            inputs[name] = {"sheet": target.sheet, "address": target.address, "kind": kinds[name]}
            ledger.added(f"bindings.inputs.{name}.kind")
            ledger.added(f"bindings.inputs.{name}.required")
        for name, target in workbook.bindings.outputs.items():
            outputs[name] = {"sheet": target.sheet, "address": target.address}
    built = {
        "schema_version": workbook.schema_version,
        "revision": 0,
        "sheets": sheets,
        "inputs": inputs,
        "outputs": outputs,
    }
    try:
        document = _normalize_model(built)
    except ToolkitError as error:
        raise ConversionError(error.code, error.message) from error
    return document, ledger.conversion()


def _toolkit_value(value: Any) -> Any:
    """A canonical cell value in the toolkit's spelling; numbers are left to the reader."""
    if isinstance(value, dict):
        spelled = {"error": value["error"]}
        if value.get("message") is not None:
            spelled["message"] = value["message"]
        return spelled
    return value


def to_canonical(
    document: Mapping[str, Any], envelope: PreservationEnvelope | None = None
) -> tuple[Workbook, Conversion]:
    """The hydrated canonical workbook for a toolkit document.

    The document is normalised by the toolkit reader first, so the drop list
    names what the normalised form holds (``required`` included) rather than
    what the caller happened to write. With an ``envelope`` the workbook is
    marked imported from its workbook part, a cell the baseline document
    still holds unchanged is marked imported from its sheet part, a cell added
    or changed since import is marked authored, and ``metadata.source``
    records the package identity. Without an envelope the workbook gets no
    provenance and only a value that came from ``cached_value`` is marked
    imported.
    """
    try:
        normalized = _normalize_model(document)
    except ToolkitError as error:
        raise ConversionError(error.code, error.message) from error
    ledger = _Ledger()
    ledger.dropped("revision")
    ledger.added("model_version")
    metadata: dict[str, Any] = {}
    # A toolkit document does not say whether it was authored or imported, so
    # without an envelope no workbook provenance is invented.
    provenance = None
    # What the import held, by sheet name and address, in normalised form.
    baseline: dict[str, dict[str, Any]] = {}
    if envelope is not None:
        provenance = Provenance("imported", envelope.source.workbook_part)
        ledger.added("provenance")
        metadata["source"] = {
            "fingerprint": envelope.source.fingerprint,
            "parts": list(envelope.source.parts),
        }
        ledger.added("metadata.source")
        baseline = {item["name"]: item["cells"] for item in envelope.baseline.document["sheets"]}
    sheets: list[Sheet] = []
    for index, sheet in enumerate(normalized["sheets"]):
        prefix = f"sheets[{index}]"
        ledger.dropped(f"{prefix}.id")
        for column in sheet["column_widths"]:
            ledger.dropped(f"{prefix}.column_widths.{column}")
        held_cells = baseline.get(sheet["name"], {})
        sheet_part = None if envelope is None else envelope.source.sheet_parts.get(sheet["name"])
        cells: dict[str, Cell] = {}
        for address, item in sheet["cells"].items():
            cell_path = f"{prefix}.cells.{address}"
            ledger.added(f"{cell_path}.data_type")
            if "blocked_reason" in item:
                ledger.dropped(f"{cell_path}.blocked_reason")
            number_format = None
            for key, style_value in item.get("style", {}).items():
                if key == "number_format" and style_value != "General":
                    number_format = style_value
                else:
                    ledger.dropped(f"{cell_path}.style.{key}")
            value = _canonical_value(item["value"])
            formula = None
            cell_provenance = None
            if "formula" in item:
                ledger.added(f"{cell_path}.formula.dependencies")
                ledger.added(f"{cell_path}.formula.result")
                formula = Formula(_canonical_formula_text(item["formula"]))
                if "cached_value" in item:
                    # The stored value comes back to `value`; only calculation
                    # writes `result`, so an imported cache is never taken for
                    # one (the intake module keeps the same rule).
                    value = _canonical_value(item["cached_value"])
                    cell_provenance = Provenance("imported")
            elif "cached_value" in item:
                ledger.dropped(f"{cell_path}.cached_value")
            if envelope is not None:
                cell_provenance = _import_provenance(item, held_cells.get(address), sheet_part)
            if cell_provenance is not None:
                ledger.added(f"{cell_path}.provenance")
            cells[address] = Cell(
                address, value, _value_type(value), number_format, formula, cell_provenance
            )
        dimensions = _dimensions(cells)
        if dimensions is not None:
            ledger.added(f"{prefix}.dimensions")
        sheets.append(Sheet(sheet["name"], cells, dimensions))
    bindings = None
    if normalized["inputs"] or normalized["outputs"]:
        inputs: dict[str, CellId] = {}
        for name, binding in normalized["inputs"].items():
            for key in binding:
                if key not in ("sheet", "address"):
                    ledger.dropped(f"bindings.inputs.{name}.{key}")
            inputs[name] = CellId(binding["sheet"], binding["address"])
        outputs = {
            name: CellId(binding["sheet"], binding["address"])
            for name, binding in normalized["outputs"].items()
        }
        bindings = Bindings(inputs, outputs)
    workbook = Workbook(
        normalized["schema_version"],
        MODEL_VERSION,
        metadata,
        tuple(sheets),
        None,
        provenance,
        bindings,
        (),
    )
    workbook = _with_dependencies(workbook)
    for index in range(len(workbook.diagnostics)):
        ledger.added(f"diagnostics[{index}]")
    try:
        hydrated = hydrate(workbook.to_json())
    except ModelError as error:
        raise ConversionError(error.code, error.message) from error
    return hydrated, ledger.conversion()


def _import_provenance(
    item: Mapping[str, Any], held: Mapping[str, Any] | None, sheet_part: str | None
) -> Provenance:
    """Imported from its sheet part when the baseline held this cell unchanged, else authored.

    The baseline document is what the import produced, so a cell it never
    held, or held with another value, formula or cache, was added or changed
    after import. The sheet part alone does not make a cell imported.
    """
    if held is not None and _cell_content(held) == _cell_content(item):
        return Provenance("imported", sheet_part)
    return Provenance("authored")


def _cell_content(cell: Mapping[str, Any]) -> str:
    # JSON text, not ==: Python reads True == 1.0 and -0.0 == 0.0 as equal.
    return json.dumps(
        [cell.get("value"), cell.get("formula"), cell.get("cached_value")],
        sort_keys=True,
        allow_nan=False,
    )


def _with_dependencies(workbook: Workbook) -> Workbook:
    """Fill every formula's dependencies with the canonical analyzer's answer.

    The analyzer resolves cross-sheet references against the whole workbook,
    so it runs once the sheets exist. A formula it refuses keeps no
    dependencies, and the refusal travels as a canonical diagnostic on that
    cell, so an unparseable formula is never read as one with no references.
    """
    sheets = []
    diagnostics: list[Diagnostic] = []
    for sheet in workbook.sheets:
        cells = {}
        for address, cell in sheet.cells.items():
            if cell.formula is not None:
                dependencies, diagnostic = _analyze(
                    workbook, sheet.name, address, cell.formula.expression
                )
                if diagnostic is not None:
                    diagnostics.append(diagnostic)
                cell = replace(cell, formula=Formula(cell.formula.expression, dependencies))
            cells[address] = cell
        sheets.append(replace(sheet, cells=cells))
    return replace(
        workbook,
        sheets=tuple(sheets),
        diagnostics=tuple(sorted(diagnostics, key=_diagnostic_sort)),
    )


def _canonical_formula_text(text: str) -> str:
    """A toolkit formula in the canonical spelling: led by ``=``.

    Imported toolkit formulas are the raw OOXML ``<f>`` text, which has no
    leading ``=``; the canonical importer writes one. Both engines read either
    spelling as the same formula, so nothing is listed. An empty formula (a
    shared-formula follower with no text) is left for the canonical reader,
    which refuses it as the canonical importer does.
    """
    if text and not text.startswith("="):
        return "=" + text
    return text


def _canonical_value(value: Any) -> Any:
    """A toolkit cell value in the canonical spelling."""
    if isinstance(value, float) and value.is_integer() and abs(value) <= _EXACT_INTEGER:
        # Negative zero stays a float: an int has no sign to carry it.
        if value == 0 and math.copysign(1.0, value) < 0:
            return value
        return int(value)
    if isinstance(value, dict):
        code = "#N/A" if value["error"] == "#N/A!" else value["error"]
        return {"error": code, "message": value.get("message")}
    return value


def _dimensions(cells: Mapping[str, Cell]) -> tuple[int, int, int, int] | None:
    """``[min_row, max_row, min_col, max_col]`` of the cells, as the canonical exporter derives it."""
    if not cells:
        return None
    positions = [_coordinates(address) for address in cells]
    rows = [row for row, _column in positions]
    columns = [column for _row, column in positions]
    return (min(rows), max(rows), min(columns), max(columns))
