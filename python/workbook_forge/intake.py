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
does not carry is listed in `metadata["intake"]["not_carried"]`.

The list is made from what version 1 does carry: cell addresses, values,
formula text, stored formula values and number formats. Every other element,
attribute, part and relationship is listed by its name in the file, so
content this reader has never heard of is listed too. The list says that
something is there. It does not say what that thing would have meant.
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
# What version 1 carries. Everything else a workbook holds is listed as not
# carried, so content this reader has never heard of is listed too.
_CARRIED_IN_WORKBOOK = {"sheets"}
_CARRIED_IN_SHEET = {"sheetData", "dimension"}
_CARRIED_ON_ROW = {"r", "spans"}
_CARRIED_ON_CELL = {"r", "s", "t"}
_CARRIED_IN_CELL = {"f", "v", "is"}
_CARRIED_PARTS = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml",
    "application/vnd.openxmlformats-package.relationships+xml",
}
_CARRIED_PARTS_STRINGS = "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"
_CARRIED_RELATIONSHIPS = {
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
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


def _name(tag: str) -> str:
    """An element or attribute name, marked when it is not in the main namespace."""
    namespace, _, local = tag.rpartition("}")
    return local if namespace in ("", f"{{{MAIN}") else f"{local} (extension)"


class _Tally:
    """Counts of what was seen, in the order it was first seen."""

    def __init__(self) -> None:
        self.counts: dict[tuple[str, str | None, str], int] = {}

    def add(self, where: str, sheet: str | None, what: str, count: int = 1) -> None:
        key = (where, sheet, what)
        self.counts[key] = self.counts.get(key, 0) + count

    def listed(self) -> list[dict[str, Any]]:
        entries = []
        for (where, sheet, what), count in self.counts.items():
            entry: dict[str, Any] = {"where": where, "what": what, "count": count}
            if sheet is not None:
                entry["sheet"] = sheet
            entries.append(entry)
        return entries


def _not_carried(source: Path) -> list[dict[str, Any]]:
    """What the workbook holds that the version 1 model has no place for."""
    tally = _Tally()
    with Package.open(source) as package:
        book = _safe_xml(package._parts[package._workbook_part], package._workbook_part)
        for element in book:
            name = _name(element.tag)
            if name not in _CARRIED_IN_WORKBOOK:
                tally.add("workbook", None, name, max(1, len(element)))
        for sheet in book.findall(f"{_MAIN}sheets/{_MAIN}sheet"):
            state = sheet.attrib.get("state", "visible")
            if state != "visible":
                tally.add("workbook", sheet.attrib.get("name"), f"sheet state {state}")

        for part in package._parts:
            kind = package._content_type_for(part)
            if part == "[Content_Types].xml" or part.endswith(".rels") or kind in _CARRIED_PARTS:
                continue
            if kind == STYLES_CONTENT_TYPE:
                tally.add("package", None, "cell styles other than number formats")
            else:
                tally.add("package", None, f"part of type {kind or 'unknown'}")
        for part in package._parts:
            if part.endswith(".rels"):
                relationships = _safe_xml(package._parts[part], part)
                for relationship in relationships:
                    kind = relationship.attrib.get("Type", "")
                    if part != package._workbook_rels_part and kind not in _CARRIED_RELATIONSHIPS:
                        tally.add("package", None, f"relationship {kind.rpartition('/')[2] or 'unknown'}")

        format_ids = _format_ids(package)
        for name in package.sheet_names:
            part = package._sheet_parts[name]
            root = _safe_xml(package._parts[part], part)
            for element in root:
                what = _name(element.tag)
                if what not in _CARRIED_IN_SHEET:
                    tally.add("sheet", name, what, max(1, len(element)))
            for row in root.iter(f"{_MAIN}row"):
                for attribute in row.attrib:
                    if _name(attribute) not in _CARRIED_ON_ROW:
                        tally.add("sheet", name, f"row attribute {_name(attribute)}")
            for cell in root.iter(f"{_MAIN}c"):
                for attribute in cell.attrib:
                    if _name(attribute) not in _CARRIED_ON_CELL:
                        tally.add("sheet", name, f"cell attribute {_name(attribute)}")
                for element in cell:
                    if _name(element.tag) not in _CARRIED_IN_CELL:
                        tally.add("sheet", name, f"cell element {_name(element.tag)}")
                for run in cell.iter(f"{_MAIN}rPr"):
                    tally.add("sheet", name, "text formatting within a cell")
                for run in cell.iter(f"{_MAIN}rPh"):
                    tally.add("sheet", name, "phonetic text")
                style = cell.attrib.get("s")
                if style is not None and style.isdigit() and int(style) < len(format_ids):
                    format_id = format_ids[int(style)]
                    if format_id is not None:
                        tally.add("sheet", name, f"number format {format_id}")

        for part in package._parts:
            if package._content_type_for(part) == _CARRIED_PARTS_STRINGS:
                strings = _safe_xml(package._parts[part], part)
                for what, tag in (("text formatting within a cell", "rPr"), ("phonetic text", "rPh")):
                    count = sum(1 for _ in strings.iter(f"{_MAIN}{tag}"))
                    if count:
                        tally.add("package", None, f"{what}, in shared text", count)
    return tally.listed()


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
