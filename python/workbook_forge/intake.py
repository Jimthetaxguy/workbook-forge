"""Read an Excel workbook into the canonical workbook model.

This is the entry point for turning an `.xlsx` file into the versioned JSON
document that both engines read. It adds no reader of its own: the package is
read by `canonical_xlsx.import_canonical`.

A value Excel stored for a formula cell is kept as `Cell.value` with
provenance `imported`. `Formula.result` and `Formula.dependencies` stay empty
until `model.calculate` fills them, so an imported cache is never mistaken
for a value Workbook Forge calculated.

Two promises are kept here. Intake never returns a document that the
canonical reader would refuse. And what the workbook holds that version 1
does not carry is listed in `metadata["intake"]["not_carried"]`, so nothing
is left out without a word.
"""
from __future__ import annotations

import dataclasses
import math
from pathlib import Path
from typing import Any
import zipfile
import zlib

from .canonical_xlsx import import_canonical
from .model import ModelError, Workbook, hydrate
from .workbook import MAIN, Workbook as Package, WorkbookError, _safe_xml
from .xlsx import _BUILTIN_FORMATS, STYLES_CONTENT_TYPE

_MAIN = f"{{{MAIN}}}"
# Worksheet content that version 1 has no place for, by element name.
_SHEET_CONTENT = {
    "mergeCells": ("merged_cells", "mergeCell"),
    "dataValidations": ("data_validations", "dataValidation"),
    "conditionalFormatting": ("conditional_formats", None),
    "hyperlinks": ("hyperlinks", "hyperlink"),
    "tableParts": ("tables", "tablePart"),
    "autoFilter": ("filters", None),
    "drawing": ("drawings", None),
    "legacyDrawing": ("comments_or_controls", None),
    "sheetProtection": ("sheet_protection", None),
}


def intake_workbook_model(path: str | Path) -> Workbook:
    """Read an `.xlsx` file and return the canonical workbook."""
    source = Path(path)
    if source.suffix.lower() != ".xlsx":
        raise WorkbookError(f"intake reads .xlsx files, not {source.suffix or 'a file with no suffix'}")
    if not source.is_file():
        raise WorkbookError(f"workbook not found: {source}")
    try:
        workbook = import_canonical(source)
        not_carried = _not_carried(source)
    except (zipfile.BadZipFile, zlib.error, EOFError) as error:
        raise WorkbookError(f"the workbook package is damaged: {type(error).__name__}") from None
    metadata = dict(workbook.metadata)
    metadata["intake"] = {"not_carried": not_carried}
    workbook = dataclasses.replace(workbook, metadata=metadata)
    _refuse_what_the_reader_would_refuse(workbook)
    return workbook


def intake_workbook(path: str | Path) -> bytes:
    """Read an `.xlsx` file and return the canonical JSON document."""
    return intake_workbook_model(path).to_json().encode("utf-8")


def _refuse_what_the_reader_would_refuse(workbook: Workbook) -> None:
    for sheet in workbook.sheets:
        for address, cell in sheet.cells.items():
            if isinstance(cell.value, float) and not math.isfinite(cell.value):
                raise WorkbookError(f"{sheet.name}!{address} holds a number that is not finite")
    try:
        hydrate(workbook.to_json())
    except ModelError as error:
        raise WorkbookError(f"this workbook cannot be carried into model version 1: {error}") from None


def _count(parent: Any, child: str | None) -> int:
    return 1 if child is None else max(1, len(parent.findall(f"{_MAIN}{child}")))


def _not_carried(source: Path) -> list[dict[str, Any]]:
    """What the workbook holds that the version 1 model has no place for."""
    found: list[dict[str, Any]] = []
    with Package.open(source) as package:
        book = _safe_xml(package._parts[package._workbook_part], package._workbook_part)
        names = book.findall(f"{_MAIN}definedNames/{_MAIN}definedName")
        if names:
            found.append({"kind": "defined_names", "count": len(names)})
        for sheet in book.findall(f"{_MAIN}sheets/{_MAIN}sheet"):
            if sheet.attrib.get("state") in {"hidden", "veryHidden"}:
                found.append({"kind": "hidden_sheet", "sheet": sheet.attrib.get("name")})
        for tag, kind in (("externalReferences", "external_links"), ("pivotCaches", "pivot_tables"),
                          ("workbookProtection", "workbook_protection")):
            if book.find(f"{_MAIN}{tag}") is not None:
                found.append({"kind": kind})
        if any(part.casefold().endswith("vbaproject.bin") for part in package._parts):
            found.append({"kind": "macros"})

        format_ids = _format_ids(package)
        for name in package.sheet_names:
            part = package._sheet_parts[name]
            root = _safe_xml(package._parts[part], part)
            counts: dict[str, int] = {}
            for element in root:
                tag = element.tag.removeprefix(_MAIN)
                if tag in _SHEET_CONTENT:
                    kind, child = _SHEET_CONTENT[tag]
                    counts[kind] = counts.get(kind, 0) + _count(element, child)
            found.extend({"kind": kind, "sheet": name, "count": count} for kind, count in counts.items())

            unknown: dict[int, int] = {}
            for element in package._cells[name].values():
                style = element.attrib.get("s")
                if style is None or not style.isdigit() or int(style) >= len(format_ids):
                    continue
                format_id = format_ids[int(style)]
                if format_id is not None:
                    unknown[format_id] = unknown.get(format_id, 0) + 1
            for format_id, count in sorted(unknown.items()):
                found.append({"kind": "number_format", "sheet": name, "format_id": format_id, "count": count})
    return found


def _format_ids(package: Package) -> list[int | None]:
    """For each cell style, the number format id intake cannot name, or None."""
    styles = [name for name in package._parts if package._content_type_for(name) == STYLES_CONTENT_TYPE]
    if len(styles) != 1:
        return []
    root = _safe_xml(package._parts[styles[0]], styles[0])
    named = set(_BUILTIN_FORMATS)
    for item in root.findall(f"{_MAIN}numFmts/{_MAIN}numFmt"):
        if item.attrib.get("numFmtId", "").isdigit():
            named.add(int(item.attrib["numFmtId"]))
    result: list[int | None] = []
    for xf in root.findall(f"{_MAIN}cellXfs/{_MAIN}xf"):
        text = xf.attrib.get("numFmtId", "0")
        format_id = int(text) if text.isdigit() else 0
        result.append(None if format_id in named else format_id)
    return result


def summarize(workbook: Workbook) -> dict[str, Any]:
    """Counts and versions for a person to read. Not the model itself."""
    sheets = [
        {
            "name": sheet.name,
            "cells": len(sheet.cells),
            "formulas": sum(1 for cell in sheet.cells.values() if cell.formula is not None),
            "dimensions": list(sheet.dimensions) if sheet.dimensions else None,
        }
        for sheet in workbook.sheets
    ]
    return {
        "schema_version": workbook.schema_version,
        "model_version": workbook.model_version,
        "source_path": workbook.source_path,
        "sheets": sheets,
        "cell_count": sum(sheet["cells"] for sheet in sheets),
        "formula_count": sum(sheet["formulas"] for sheet in sheets),
        "not_carried": workbook.metadata.get("intake", {}).get("not_carried", []),
    }
