"""Export and reimport the canonical workbook model as OOXML.

The serialized workbook model remains the source of truth. The dict passed to
the package writer is an OOXML buffer for this call, not a second model.

v1 does not place worksheet spill cells. Array results and stored array/spill
formulas fail closed with ``array_spill_refused``. That refusal is not a
passing spill test.
"""

from __future__ import annotations

import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from workbook_forge import ErrorValue, analyze_formula
from workbook_forge.model import (
    SCHEMA_VERSION,
    MODEL_VERSION,
    Cell,
    Formula,
    Provenance,
    Sheet,
    Workbook,
    calculate,
)
from workbook_forge.workbook import (
    Workbook as Package,
    _cell_position,
    _q,
)
from workbook_forge.xlsx import _fresh_parts, _styles

_GENERAL_FORMATS = frozenset({None, "General"})


class CanonicalPackageError(Exception):
    """The canonical workbook cannot be exported or imported under the v1 boundary."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def export_canonical(workbook: Workbook, output: str | Path) -> Path:
    """Write ``workbook`` to a new ``.xlsx`` file.

    Calculated scalar results are stored as Excel caches. Diagnostics stay
    formula text without a cache. An array result refuses the whole package
    before any file is published.
    """
    calculated = calculate(workbook)
    spills = _spill_sites(calculated)
    if spills:
        listed = "; ".join(spills)
        raise CanonicalPackageError(
            "array_spill_refused",
            "v1 export refuses worksheet array-spill placement at "
            f"{listed}; spill cells are not written",
        )
    target = Path(output).resolve()
    if target.suffix.lower() != ".xlsx":
        raise CanonicalPackageError("invalid_output", "output must use the .xlsx extension")
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    parts = _fresh_parts(_ooxml_buffer(workbook))
    caches = _scalar_caches(calculated)
    with tempfile.TemporaryDirectory(prefix=".workbook-forge-", dir=target.parent) as folder:
        seed, staged = Path(folder) / "seed.xlsx", Path(folder) / "result.xlsx"
        with zipfile.ZipFile(seed, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for part, data in parts.items():
                archive.writestr(part, data)
        with Package.open(seed) as package:
            for (sheet, address), value in caches.items():
                package._write_formula_cache(sheet, address, value)
            package._request_recalculation()
            calc = package._changed_workbook.find(_q("calcPr"))
            if calc is not None:
                calc.set("fullPrecision", "1")
            package.save_as(staged)
        os.link(staged, target)
    return target


def import_canonical(path: str | Path) -> Workbook:
    """Read an ``.xlsx`` package into a canonical workbook.

    Formula caches land on ``Cell.value``. ``Formula.result`` stays empty until
    ``calculate``. Bindings are not stored in the package. Array and spill
    formulas raise ``array_spill_refused`` and return no workbook.
    """
    stored = read_package(path)
    spills = [item for item in stored["cells"] if item["spill"]]
    if spills:
        listed = "; ".join(
            f"{item['sheet']}!{item['address']} ({item['spill']})" for item in spills
        )
        raise CanonicalPackageError(
            "array_spill_refused",
            "v1 import refuses worksheet array-spill placement at "
            f"{listed}; the spill region is not read into the model",
        )
    grouped = [item for item in stored["cells"] if item["group"]]
    if grouped:
        listed = "; ".join(
            f"{item['sheet']}!{item['address']} ({item['group']})" for item in grouped
        )
        raise CanonicalPackageError(
            "unsupported_formula_group",
            f"v1 canonical import refuses grouped formulas at {listed}",
        )
    untexted = [item for item in stored["cells"] if item["formula_without_text"]]
    if untexted:
        listed = "; ".join(f"{item['sheet']}!{item['address']}" for item in untexted)
        raise CanonicalPackageError(
            "unsupported_formula",
            f"formula cells with no formula text at {listed}; their stored values are not read as literals",
        )
    if stored["date_system"] != "1900":
        raise CanonicalPackageError(
            "unsupported_package",
            f"date system {stored['date_system']} is outside the v1 canonical export profile",
        )
    sheets: list[Sheet] = []
    for name in stored["sheet_names"]:
        cells: dict[str, Cell] = {}
        for item in stored["cells"]:
            if item["sheet"] != name:
                continue
            formula = None
            if item["formula"] is not None:
                formula = Formula(item["formula"], (), None)
            cells[item["address"]] = Cell(
                item["address"],
                item["value"],
                item["data_type"],
                item["number_format"],
                formula,
                Provenance("imported", stored["source"]),
            )
        sheets.append(Sheet(name, cells, _dimensions(cells)))
    return Workbook(
        SCHEMA_VERSION,
        MODEL_VERSION,
        {},
        tuple(sheets),
        stored["source"],
        Provenance("imported", stored["source"]),
        None,
        (),
    )


def read_package(path: str | Path) -> dict[str, Any]:
    """Read stored cells, formula text, and spill markers. Does not calculate."""
    source = Path(path).resolve()
    cells: list[dict[str, Any]] = []
    with Package.open(source) as package:
        date_system = "1904" if package._uses_1904_date_system else "1900"
        styles, _metadata = _styles(package)
        for sheet in package.sheet_names:
            for address, element in package._cells[sheet].items():
                _refuse_a_cell_that_would_be_misread(element, sheet, address)
                stored = package.get(sheet, address)
                attrs = dict(stored.formula_attributes)
                kind = stored.formula_kind
                spill = _spill_marker(kind, attrs, element.attrib)
                group = _group_marker(kind)
                formula = None
                value = None
                data_type = "blank"
                if stored.formula is not None:
                    formula = stored.formula if stored.formula.startswith("=") else f"={stored.formula}"
                if stored.formula is None or stored.value is not None:
                    value, data_type = _stored_value(stored.value)
                number_format = None
                if stored.style_id is not None and 0 <= stored.style_id < len(styles):
                    number_format = styles[stored.style_id].get("number_format")
                if number_format in _GENERAL_FORMATS:
                    number_format = None
                cells.append(
                    {
                        "sheet": sheet,
                        "address": address,
                        "formula": formula,
                        "value": value,
                        "data_type": data_type,
                        "number_format": number_format,
                        "spill": spill,
                        "group": group,
                        "formula_without_text": stored.formula is None and element.find(_q("f")) is not None,
                    }
                )
        sheet_names = list(package.sheet_names)
    return {
        "source": str(source),
        "date_system": date_system,
        "sheet_names": sheet_names,
        "cells": cells,
    }


def compare_structure(source: Workbook, imported: Workbook) -> dict[str, Any]:
    """Compare sheets, addresses, formula text, number formats, and literals.

    Formula caches, dependencies, diagnostics, and provenance are not structure.
    Formula text that differs is returned as ``formula_rewrites``, not folded
    into a generic mismatch.
    """
    mismatches: list[dict[str, Any]] = []
    rewrites: list[dict[str, Any]] = []
    source_names = [sheet.name for sheet in source.sheets]
    imported_names = [sheet.name for sheet in imported.sheets]
    if source_names != imported_names:
        mismatches.append(
            {"kind": "sheets", "source": source_names, "imported": imported_names}
        )
    for name in source_names:
        if name not in imported_names:
            continue
        left = source.sheet(name)
        right = imported.sheet(name)
        assert left is not None and right is not None
        if left.dimensions != right.dimensions:
            mismatches.append(
                {
                    "kind": "dimensions",
                    "sheet": name,
                    "source": None if left.dimensions is None else list(left.dimensions),
                    "imported": None if right.dimensions is None else list(right.dimensions),
                }
            )
        left_addresses = set(left.cells)
        right_addresses = set(right.cells)
        if left_addresses != right_addresses:
            mismatches.append(
                {
                    "kind": "addresses",
                    "sheet": name,
                    "missing": sorted(left_addresses - right_addresses),
                    "added": sorted(right_addresses - left_addresses),
                }
            )
        for address in sorted(left_addresses & right_addresses):
            before = left.cells[address]
            after = right.cells[address]
            if before.number_format != after.number_format:
                mismatches.append(
                    {
                        "kind": "number_format",
                        "sheet": name,
                        "address": address,
                        "source": before.number_format,
                        "imported": after.number_format,
                    }
                )
            before_formula = None if before.formula is None else before.formula.expression
            after_formula = None if after.formula is None else after.formula.expression
            if _formula_key(before_formula) != _formula_key(after_formula):
                rewrites.append(
                    {
                        "sheet": name,
                        "address": address,
                        "exported": before_formula,
                        "reimported": after_formula,
                    }
                )
            elif before.formula is None and (
                before.data_type != after.data_type or before.value != after.value
            ):
                mismatches.append(
                    {
                        "kind": "literal",
                        "sheet": name,
                        "address": address,
                        "source": {"data_type": before.data_type, "value": before.value},
                        "imported": {"data_type": after.data_type, "value": after.value},
                    }
                )
    return {
        "match": not mismatches and not rewrites,
        "mismatches": mismatches,
        "formula_rewrites": rewrites,
    }


def _spill_sites(workbook: Workbook) -> list[str]:
    sites: list[str] = []
    for item in workbook.diagnostics:
        if item.message != "array results are unsupported":
            continue
        where = f"{item.sheet}!{item.address}"
        expression = ""
        function = item.function
        sheet = workbook.sheet(item.sheet or "")
        cell = None if sheet is None or item.address is None else sheet.cells.get(item.address)
        if cell is not None and cell.formula is not None:
            expression = cell.formula.expression
            if function is None:
                try:
                    names = analyze_formula(expression).functions
                except (TypeError, ValueError, RecursionError):
                    names = ()
                function = names[0] if names else None
        detail = expression or "array result"
        if function:
            detail = f"{function} {detail}"
        sites.append(f"{where} ({detail})")
    return sites


def _ooxml_buffer(workbook: Workbook) -> dict[str, Any]:
    sheets = []
    for sheet in workbook.sheets:
        cells: dict[str, Any] = {}
        for address, cell in sheet.cells.items():
            payload: dict[str, Any] = {}
            if cell.formula is not None:
                payload["formula"] = cell.formula.expression
            else:
                payload["value"] = cell.value
            if cell.number_format:
                payload["style"] = {"number_format": cell.number_format}
            cells[address] = payload
        sheets.append({"name": sheet.name, "cells": cells})
    return {"sheets": sheets}


def _scalar_caches(workbook: Workbook) -> dict[tuple[str, str], Any]:
    caches: dict[tuple[str, str], Any] = {}
    for sheet in workbook.sheets:
        for address, cell in sheet.cells.items():
            if cell.formula is None or cell.formula.result is None:
                continue
            result = cell.formula.result
            if isinstance(result, dict) and "error" in result:
                value: Any = ErrorValue(result["error"])
            else:
                value = result
            caches[(sheet.name, address)] = Package._normalize_formula_cache(
                value, sheet.name, address
            )
    return caches


_CELL_TYPES = frozenset({None, "n", "s", "str", "inlineStr", "b", "e"})
# A number as OOXML writes one. Python's float() also reads 1_000, digits
# from other scripts and padded text, none of which is a number in a workbook.
_STORED_NUMBER = re.compile(r"[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?")


def _refuse_a_cell_that_would_be_misread(element: Any, sheet: str, address: str) -> None:
    kind = element.attrib.get("t")
    if kind not in _CELL_TYPES:
        what = "a date written as text" if kind == "d" else f"type {kind!r}"
        raise CanonicalPackageError(
            "unsupported_cell", f"{sheet}!{address} holds {what}, which would be read as plain text"
        )
    stored = element.find(_q("v"))
    if kind in (None, "n") and stored is not None and stored.text is not None:
        if _STORED_NUMBER.fullmatch(stored.text) is None:
            raise CanonicalPackageError(
                "unsupported_cell", f"{sheet}!{address} holds a number that is not written as a number"
            )


def _spill_marker(kind: str | None, attributes: dict[str, str], cell_attributes: dict[str, str]) -> str | None:
    folded = (kind or attributes.get("t") or "").casefold()
    ref = attributes.get("ref")
    if folded in {"shared", "datatable"}:
        # Their ref is the filled range. `_group_marker` reports them.
        return None
    if folded == "array":
        return f't="array"{f" ref={ref!r}" if ref else ""}'
    if ref and _multi_cell_ref(ref):
        return f"ref={ref!r}"
    if "cm" in cell_attributes:
        return "cell metadata cm"
    return None


def _group_marker(kind: str | None) -> str | None:
    folded = (kind or "").casefold()
    if folded in {"shared", "datatable"}:
        return folded
    return None


def _multi_cell_ref(reference: str) -> bool:
    parts = reference.split(":")
    if len(parts) != 2:
        return False
    try:
        start = _cell_position(parts[0].replace("$", ""))
        end = _cell_position(parts[1].replace("$", ""))
    except ValueError:
        return True
    return start != end


def _stored_value(value: Any) -> tuple[Any, str]:
    if value is None:
        return None, "blank"
    if isinstance(value, ErrorValue):
        return {"error": value.code, "message": None}, "error"
    if isinstance(value, bool):
        return value, "boolean"
    if isinstance(value, int):
        return value, "number"
    if isinstance(value, float):
        if value.is_integer() and abs(value) <= 2**53:
            return int(value), "number"
        return value, "number"
    if isinstance(value, str):
        return value, "text"
    raise CanonicalPackageError(
        "unsupported_package",
        f"stored cell value type {type(value).__name__} is outside the v1 canonical profile",
    )


def _dimensions(cells: dict[str, Cell]) -> tuple[int, int, int, int] | None:
    if not cells:
        return None
    columns: list[int] = []
    rows: list[int] = []
    for address in cells:
        column, row = _cell_position(address)
        columns.append(column)
        rows.append(row)
    return (min(rows), max(rows), min(columns), max(columns))


def _formula_key(expression: str | None) -> str | None:
    if expression is None:
        return None
    text = expression.strip()
    if not text.startswith("="):
        text = "=" + text
    return text
