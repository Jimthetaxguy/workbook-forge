"""Independent Python extraction of bounded, formula-aware XLSX records."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from . import ErrorValue
from .catalog import _catalog_file
from .expressions import analyze_formula, copy_formula
from .workbook import (
    MAIN, PKG_REL, RANGE_RE, REL_DOC, TABLE_CONTENT_TYPE, TABLE_REL,
    UnsupportedWorkbook, Workbook, WorkbookError, _cell_position,
    _normal_address, _q, _relationship_part, _resolve_part, _safe_xml,
)
from .xml_patterns import direct_text, qualified, rich_text, select_path, walk_paths

MAX_RECORDS = 100_000
MAX_PAGE_RECORDS = 100
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_DIAGNOSTICS = 100
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_SHARED_STRING_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"
)


def pattern_catalog() -> dict[str, Any]:
    """Return the installed, language-neutral pattern and function catalog."""
    return json.loads(_catalog_file("extraction-patterns.json").read_text(encoding="utf-8"))


def _unsigned(value: str | None, maximum: int) -> int | None:
    if value is None or re.fullmatch(r"[0-9]+", value) is None:
        return None
    normalized = value.lstrip("0") or "0"
    if len(normalized) > len(str(maximum)):
        return None
    number = int(normalized)
    return number if number <= maximum else None


@dataclass
class _SharedFormula:
    effective: str | None
    master: str | None
    reference: str | None
    error: str | None
    row_delta: int = 0
    column_delta: int = 0
    is_master: bool = False


def _shared_formulas(root: ET.Element) -> dict[ET.Element, _SharedFormula]:
    """Validate complete explicit groups without expanding their range."""
    groups: dict[int, list[tuple[ET.Element, str | None]]] = {}
    result: dict[ET.Element, _SharedFormula] = {}
    for cell in select_path(root, MAIN, ("worksheet", "sheetData", "row", "c")):
        address = _normal_address(cell.attrib["r"]) if cell.attrib.get("r") else None
        for formula in cell.findall(_q("f")):
            if formula.attrib.get("t") != "shared":
                continue
            index = _unsigned(formula.attrib.get("si"), 2**32 - 1)
            if index is None:
                result[formula] = _SharedFormula(
                    None, None, formula.attrib.get("ref"), "invalid shared-formula index"
                )
            else:
                groups.setdefault(index, []).append((formula, address))
    for members in groups.values():
        masters = [(node, cell) for node, cell in members if "ref" in node.attrib]
        master_node, master_cell = masters[0] if len(masters) == 1 else (None, None)
        reference = master_node.attrib.get("ref") if master_node is not None else None
        offsets: dict[ET.Element, tuple[int, int]] = {}
        source = direct_text(master_node) if master_node is not None else None
        error = None
        try:
            if master_node is None or master_cell is None:
                raise ValueError("shared group requires exactly one addressed master")
            if not (source or "").strip():
                raise ValueError("shared-formula master has no expression")
            match = RANGE_RE.fullmatch(reference or "")
            if match is None:
                raise ValueError("invalid shared-formula range")
            first = _cell_position(match[1] + match[2])
            last = _cell_position((match[3] or match[1]) + (match[4] or match[2]))
            if first[0] > last[0] or first[1] > last[1]:
                raise ValueError("reversed shared-formula range")
            master_column, master_row = _cell_position(master_cell)
            # Parse/copy even a master-only group so unsupported copying cannot
            # appear resolved merely because this page has no follower.
            copy_formula(source or "")
            for node, cell in members:
                if cell is None:
                    raise ValueError("shared-formula member has no cell address")
                column, row = _cell_position(cell)
                if not (first[0] <= column <= last[0] and first[1] <= row <= last[1]):
                    raise ValueError("shared-formula member is outside the master range")
                offsets[node] = (row - master_row, column - master_column)
                copy_formula(source or "", *offsets[node])
        except (ValueError, WorkbookError) as exc:
            error = str(exc)
        for node, _ in members:
            result[node] = _SharedFormula(
                source if error is None else None,
                master_cell, reference if master_node is not None else node.attrib.get("ref"),
                error, *offsets.get(node, (0, 0)), node is master_node,
            )
    return result


class _Diagnostics:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self.count = 0

    def add(self, code: str, message: str, record: dict[str, Any]) -> None:
        self.count += 1
        if len(self.entries) < MAX_DIAGNOSTICS:
            self.entries.append({"code": code, "message": message,
                                 **{key: record[key] for key in ("part", "sheet", "cell")}})


def _analysis(expression: str | None, functions: dict[str, Any],
              diagnostics: _Diagnostics, record: dict[str, Any]) -> dict[str, Any]:
    empty = {"functions": [], "references": [], "categories": []}
    if expression is None:
        return {"status": "unresolved", **empty}
    try:
        parsed = analyze_formula(expression)
    except ValueError as error:
        diagnostics.add("formula_syntax", str(error), record)
        return {"status": "unsupported", **empty}
    mapped = [functions.get(name, {"name": name, "known": False, "category": None,
                                   "python": "unknown", "rust": "unknown"})
              for name in parsed["functions"]]
    return {"status": "parsed", "functions": mapped, "references": parsed["references"],
            "categories": sorted({entry["category"] for entry in mapped if entry["category"]})}


def _part_selections(workbook: Workbook) -> dict[str, tuple[str, str | None]]:
    parts: dict[str, tuple[str, str | None]] = {workbook._workbook_part: ("workbook", None)}
    parts.update({part: ("worksheet", sheet) for sheet, part in workbook._sheet_parts.items()})
    for part in workbook._parts:
        content_type = workbook._content_type_for(part)
        if content_type == TABLE_CONTENT_TYPE:
            parts[part] = ("table", None)
        elif content_type == _SHARED_STRING_TYPE:
            parts[part] = ("shared_strings", None)
    for sheet, part in workbook._sheet_parts.items():
        relationship_part = _relationship_part(part)
        if relationship_part not in workbook._parts:
            continue
        relationships = _safe_xml(workbook._parts[relationship_part], relationship_part)
        by_id = {node.attrib.get("Id"): node for node in
                 relationships.findall(f"{{{PKG_REL}}}Relationship")}
        root = _safe_xml(workbook._parts[part], part)
        for node in select_path(root, MAIN, ("worksheet", "tableParts", "tablePart")):
            relationship = by_id.get(node.attrib.get(f"{{{REL_DOC}}}id"))
            if relationship is not None and relationship.attrib.get("Type") == TABLE_REL:
                target = _resolve_part(part, relationship.attrib.get("Target", ""))
                previous_owner = parts.get(target, ("table", None))[1]
                if previous_owner is not None and previous_owner != sheet:
                    raise UnsupportedWorkbook(
                        f"table part has multiple worksheet owners: {target}"
                    )
                parts[target] = ("table", sheet)
    return parts


def _record_data(pattern: str, element: ET.Element, record: dict[str, Any],
                 workbook: Workbook, functions: dict[str, Any],
                 shared: dict[ET.Element, _SharedFormula],
                 diagnostics: _Diagnostics) -> dict[str, Any]:
    if pattern == "cell":
        if record["cell"] is None:
            raise WorkbookError("cannot extract a cell without an address")
        cell = workbook.get(record["sheet"], record["cell"])
        value = {"error": cell.value.code} if isinstance(cell.value, ErrorValue) else cell.value
        if isinstance(value, float) and not math.isfinite(value):
            raise WorkbookError("cannot extract a nonfinite numeric cell")
        formula = element.find(_q("f")) is not None
        return {"value": None if formula else value, "cached_value": value if formula else None,
                "cell_type": cell.cell_type or "n", "style_id": cell.style_id}
    if pattern in {"formula", "table_formula"}:
        kind = "table" if pattern == "table_formula" else element.attrib.get("t", "normal")
        resolved = shared.get(element) if kind == "shared" else None
        effective = resolved.effective if resolved is not None else direct_text(element)
        if resolved is not None and effective is not None and not resolved.is_master:
            effective = copy_formula(effective, resolved.row_delta, resolved.column_delta)
        if resolved is not None and resolved.error is not None:
            diagnostics.add("shared_formula", resolved.error, record)
        return {
            "kind": kind, "shared_index": element.attrib.get("si"),
            "group_range": resolved.reference if resolved is not None else element.attrib.get("ref"),
            "master_cell": resolved.master if resolved is not None else None,
            "effective_formula": effective,
            "analysis": _analysis(effective, functions, diagnostics, record),
        }
    if pattern == "defined_name":
        return {"analysis": _analysis(direct_text(element), functions, diagnostics, record)}
    if pattern == "shared_string":
        return {"value": rich_text(element, MAIN)}
    if pattern == "validation":
        return {name: direct_text(node) if (node := element.find(_q(name))) is not None else None
                for name in ("formula1", "formula2")}
    return {}


def extract_xlsx(path: str | Path, *, patterns: list[str] | None = None,
                 sheet: str | None = None, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    """Inspect one validated package snapshot; never calculate or modify it."""
    if type(offset) is not int or not 0 <= offset <= 2**64 - 1:
        raise ValueError("offset must be an unsigned 64-bit integer")
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_RECORDS:
        raise ValueError(f"limit must be an integer from 1 through {MAX_PAGE_RECORDS}")
    if sheet is not None and (not isinstance(sheet, str) or not sheet):
        raise ValueError("sheet must be a nonempty name")
    catalog = pattern_catalog()
    specs = catalog["patterns"]
    all_patterns = [spec["id"] for spec in specs]
    if patterns is None:
        patterns = all_patterns
    elif (not isinstance(patterns, list) or not patterns
          or any(not isinstance(pattern, str) for pattern in patterns)
          or len(set(patterns)) != len(patterns)
          or any(pattern not in all_patterns for pattern in patterns)):
        raise ValueError("patterns must be a nonempty unique list of known pattern IDs")
    selected = set(patterns)
    compiled: dict[tuple[str, tuple[str, ...]], list[str]] = {}
    for spec in specs:
        if spec["id"] in selected:
            for names in spec["paths"]:
                key = (spec["part_kind"], tuple(qualified(catalog["namespace"], name) for name in names))
                compiled.setdefault(key, []).append(spec["id"])
    counts = {name: 0 for name in all_patterns if name in selected}
    diagnostics = _Diagnostics()
    records: list[dict[str, Any]] = []
    total = 0
    with Workbook.open(path) as workbook:
        if sheet is not None:
            canonical = next((name for name in workbook.sheet_names
                              if name.translate(_ASCII_LOWER) == sheet.translate(_ASCII_LOWER)), None)
            if canonical is None:
                raise ValueError(f"unknown worksheet: {sheet}")
            sheet = canonical
        parts = _part_selections(workbook)
        for part, (kind, owner) in sorted(parts.items()):
            if not any(key[0] == kind for key in compiled):
                continue
            if sheet is not None and kind != "workbook" and owner != sheet:
                continue
            root = _safe_xml(workbook._parts[part], part)
            shared = _shared_formulas(root) if kind == "worksheet" and "formula" in selected else {}
            for node in walk_paths(root):
                for pattern in compiled.get((kind, node.tags), ()):
                    element = node.element
                    record_sheet = owner
                    if pattern == "defined_name":
                        index = _unsigned(element.attrib.get("localSheetId"), 2**32 - 1)
                        if "localSheetId" in element.attrib and (
                            index is None or index >= len(workbook.sheet_names)
                        ):
                            raise WorkbookError("defined name has an invalid localSheetId")
                        if index is not None and index < len(workbook.sheet_names):
                            record_sheet = workbook.sheet_names[index]
                    if sheet is not None and record_sheet != sheet:
                        continue
                    total += 1
                    if total > MAX_RECORDS:
                        raise UnsupportedWorkbook("selected extraction records exceed scan limit")
                    counts[pattern] += 1
                    source_cell = (element if pattern == "cell" else node.ancestors[-1]
                                   if pattern == "formula" else None)
                    cell = source_cell.attrib.get("r") if source_cell is not None else None
                    record = {"pattern": pattern, "part": part, "path": node.path,
                              "sheet": record_sheet, "cell": _normal_address(cell) if cell else None,
                              "attributes": dict(element.attrib), "text": direct_text(element)}
                    record["data"] = _record_data(
                        pattern, element, record, workbook, catalog["functions"], shared, diagnostics
                    )
                    if offset <= total - 1 < offset + limit:
                        records.append(record)
    next_offset = offset + len(records) if offset + len(records) < total else None
    report = {"schema_version": 1, "profile": "xlsx-extraction-v1", "records": records,
              "counts": counts, "total_records": total, "offset": offset, "limit": limit,
              "next_offset": next_offset, "truncated": next_offset is not None,
              "diagnostics": diagnostics.entries, "diagnostic_count": diagnostics.count,
              "diagnostics_truncated": diagnostics.count > len(diagnostics.entries)}
    if len(json.dumps(report, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise UnsupportedWorkbook("serialized extraction report exceeds response limit")
    return report
