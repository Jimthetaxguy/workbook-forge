"""Readable and structural views of one canonical workbook revision.

The Markdown scan is a navigation aid. The cell map is the coordinate record.
Neither view is a calculation, and neither includes a filesystem path.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .model import Workbook, require_fingerprint

_COORDINATE_HEADING = "## Coordinates"
_ADDRESS = re.compile(r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")


def cell_map(workbook: Workbook, *, source_fingerprint: str | None = None) -> dict[str, Any]:
    """Coordinate-preserving map of the cells the model actually holds."""
    fingerprint = None if source_fingerprint is None else require_fingerprint(source_fingerprint)
    sheets = []
    for sheet in workbook.sheets:
        cells = []
        for address in sorted(sheet.cells):
            cell = sheet.cells[address]
            item: dict[str, Any] = {
                "address": address,
                "data_type": cell.data_type,
                "value": _json_value(cell.value),
            }
            if cell.formula is not None:
                item["formula"] = cell.formula.expression
            if cell.provenance is not None:
                item["origin"] = cell.provenance.origin
                source = _package_source(cell.provenance.source)
                if source is not None:
                    item["source"] = source
            cells.append(item)
        entry: dict[str, Any] = {"name": sheet.name, "cells": cells}
        if sheet.dimensions is not None:
            entry["dimensions"] = list(sheet.dimensions)
        sheets.append(entry)
    mapped: dict[str, Any] = {
        "schema_version": workbook.schema_version,
        "model_version": workbook.model_version,
        "sheets": sheets,
    }
    if fingerprint is not None:
        mapped["source_fingerprint"] = fingerprint
    return mapped


def markdown_scan(workbook: Workbook, *, source_fingerprint: str | None = None) -> str:
    """Readable scan. Every stored cell is listed again under Coordinates."""
    fingerprint = None if source_fingerprint is None else require_fingerprint(source_fingerprint)
    lines = ["# Workbook scan", ""]
    lines.append(f"- schema_version: {workbook.schema_version}")
    lines.append(f"- model_version: {workbook.model_version}")
    if fingerprint is not None:
        lines.append(f"- source_fingerprint: {fingerprint}")
    cell_count = sum(len(sheet.cells) for sheet in workbook.sheets)
    formula_count = sum(
        1 for sheet in workbook.sheets for cell in sheet.cells.values() if cell.formula is not None
    )
    lines.append(f"- sheets: {len(workbook.sheets)}")
    lines.append(f"- cells: {cell_count}")
    lines.append(f"- formulas: {formula_count}")
    lines.append("")
    for sheet in workbook.sheets:
        lines.append(f"## {_single_line(sheet.name)}")
        lines.append("")
        if not sheet.cells:
            lines.append("(no stored cells)")
            lines.append("")
            continue
        for address in sorted(sheet.cells):
            cell = sheet.cells[address]
            details = [cell.data_type, json.dumps(_json_value(cell.value), ensure_ascii=False, allow_nan=False)]
            if cell.provenance is not None:
                details.append(f"origin {cell.provenance.origin}")
            if cell.formula is not None:
                details.append(
                    "formula "
                    + json.dumps(cell.formula.expression, ensure_ascii=False, allow_nan=False)
                )
            lines.append(f"- `{_single_line(sheet.name)}!{address}` " + " ".join(details))
        lines.append("")
    not_carried = _not_carried(workbook)
    if not_carried:
        lines.append("## Not carried")
        lines.append("")
        for item in not_carried:
            lines.append(f"- {item}")
        lines.append("")
    lines.append(_COORDINATE_HEADING)
    lines.append("")
    for sheet in workbook.sheets:
        for address in sorted(sheet.cells):
            lines.append(f"{sheet.name}!{address}")
    lines.append("")
    return "\n".join(lines)


def paired_views(workbook: Workbook, *, source_fingerprint: str | None = None) -> dict[str, Any]:
    """Return both views and the checks that keep them on the same cells.

    Formula references are compared at their corners. A range is not expanded
    into every address it covers.
    """
    mapped = cell_map(workbook, source_fingerprint=source_fingerprint)
    scan = markdown_scan(workbook, source_fingerprint=source_fingerprint)
    stored = {
        f"{sheet.name}!{address}"
        for sheet in workbook.sheets
        for address in sheet.cells
    }
    listed = _coordinate_lines(scan)
    missing = sorted(item for item in stored if item not in listed)
    unknown = sorted(item for item in listed if item not in stored)
    referenced = sorted(_formula_corners(workbook) - stored)
    paired: dict[str, Any] = {
        "schema_version": workbook.schema_version,
        "model_version": workbook.model_version,
        "markdown": scan,
        "cell_map": mapped,
        "checks": {
            "missing_from_scan": missing,
            "coordinates_not_in_map": unknown,
            "referenced_but_not_stored": referenced,
        },
    }
    if "source_fingerprint" in mapped:
        paired["source_fingerprint"] = mapped["source_fingerprint"]
    return paired


def _json_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


def _package_source(source: str | None) -> str | None:
    from .model import is_filesystem_path

    if source is None or is_filesystem_path(source):
        return None
    return source


def _single_line(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ")


def _not_carried(workbook: Workbook) -> list[str]:
    intake = workbook.metadata.get("intake") if isinstance(workbook.metadata, dict) else None
    items = intake.get("not_carried") if isinstance(intake, dict) else None
    if not isinstance(items, list):
        return []
    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        where = item.get("where", "")
        sheet = item.get("sheet")
        what = item.get("what", "")
        count = item.get("count", "")
        place = f"{where} {sheet}" if isinstance(sheet, str) else str(where)
        lines.append(f"{place}: {what} ({count})")
    return lines


def _coordinate_lines(scan: str) -> set[str]:
    if _COORDINATE_HEADING not in scan:
        return set()
    section = scan.split(_COORDINATE_HEADING, 1)[1]
    return {line.strip() for line in section.splitlines() if line.strip()}


def _formula_corners(workbook: Workbook) -> set[str]:
    from workbook_forge import analyze_formula

    found: set[str] = set()
    for sheet in workbook.sheets:
        for cell in sheet.cells.values():
            if cell.formula is None:
                continue
            try:
                analysis = analyze_formula(cell.formula.expression)
            except (TypeError, ValueError, RecursionError):
                continue
            for reference in analysis.references:
                target = _resolve_sheet(workbook, reference.sheet, sheet.name)
                for address in (reference.start, reference.end):
                    if address and _ADDRESS.fullmatch(address):
                        found.add(f"{target}!{address}")
    return found


def _resolve_sheet(workbook: Workbook, raw: str | None, current: str) -> str:
    wanted = current if raw is None else raw
    for sheet in workbook.sheets:
        if sheet.name.casefold() == wanted.casefold():
            return sheet.name
    return wanted
