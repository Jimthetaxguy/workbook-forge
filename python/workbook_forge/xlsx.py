"""Preservation-aware XLSX boundary for the independent workbook implementations.

The legacy adapter remains the bounded OOXML package reader and patch writer.
Imported bytes form an immutable baseline; export never reloads a mutable input
file and never lets stored formula caches become calculation inputs.
"""

from __future__ import annotations

import copy
import math
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from . import ErrorValue
from .toolkit import WorkbookModel, _digest
from .workbook import (
    CONTENT_TYPES, EXCEL_ERROR_CODES, NS, PKG_REL, REL_DOC,
    TABLE_CONTENT_TYPE, WORKBOOK_CONTENT_TYPE, WORKSHEET_CONTENT_TYPE,
    MAX_CALCULATION_REFERENCE_CELLS, MAX_PACKAGE_BYTES, MAX_PACKAGE_ENTRIES,
    MAX_XML_PART_BYTES, RANGE_RE, UnsupportedWorkbook, Workbook, WorkbookError,
    XML_NS, _XML_SERIALIZE_LOCK, _cell_address, _cell_position, _in_range, _normal_address, _q, _safe_xml,
    _validate_xml_text,
)

STYLES_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"
_BUILTIN_FORMATS = {0: "General", 1: "0", 2: "0.00", 9: "0%", 10: "0.00%", 14: "mm-dd-yy", 49: "@"}


@dataclass(frozen=True)
class _Baseline:
    source: Path
    parts: tuple[tuple[str, bytes], ...]
    infos: tuple[zipfile.ZipInfo, ...]
    comment: bytes
    document_json: str
    uses_1904_date_system: bool


def _scalar(value: Any) -> Any:
    if isinstance(value, ErrorValue):
        return {"error": value.code}
    if isinstance(value, float) and not math.isfinite(value):
        raise UnsupportedWorkbook("non-finite stored cell values are unsupported")
    return value


def _styles(workbook: Workbook) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    parts = [name for name in workbook._parts if workbook._content_type_for(name) == STYLES_CONTENT_TYPE]
    if not parts:
        return [], {"count": 0, "status": "absent"}
    if len(parts) != 1:
        raise UnsupportedWorkbook("multiple style tables are unsupported")
    root = _safe_xml(workbook._parts[parts[0]], parts[0])
    formats = dict(_BUILTIN_FORMATS)
    for item in root.findall("m:numFmts/m:numFmt", NS):
        formats[int(item.attrib["numFmtId"])] = item.attrib.get("formatCode", "General")
    fonts = root.findall("m:fonts/m:font", NS)
    fills = root.findall("m:fills/m:fill", NS)
    result = []
    for xf in root.findall("m:cellXfs/m:xf", NS):
        style: dict[str, Any] = {}
        format_id = int(xf.attrib.get("numFmtId", "0"))
        if format_id in formats:
            style["number_format"] = formats[format_id]
        font_id, fill_id = int(xf.attrib.get("fontId", "0")), int(xf.attrib.get("fillId", "0"))
        if not 0 <= font_id < len(fonts) or not 0 <= fill_id < len(fills):
            raise WorkbookError("style references an absent font or fill")
        bold = fonts[font_id].find(_q("b"))
        if bold is not None:
            style["bold"] = bold.attrib.get("val", "1") not in {"0", "false"}
        color = fonts[font_id].find(_q("color"))
        if color is not None and "rgb" in color.attrib:
            style["font_color"] = color.attrib["rgb"]
        fill = fills[fill_id].find("m:patternFill", NS)
        if fill is not None and fill.attrib.get("patternType") == "solid":
            color = fill.find(_q("fgColor"))
            if color is not None and "rgb" in color.attrib:
                style["fill_color"] = color.attrib["rgb"]
        alignment = xf.find(_q("alignment"))
        if alignment is not None and alignment.attrib.get("horizontal") in {"left", "center", "right"}:
            style["horizontal"] = alignment.attrib["horizontal"]
        result.append(style)
    return result, {"part": parts[0], "count": len(result), "status": "preserved; supported fields exposed"}


def _block_range(cells: dict[str, Any], bounds: tuple[int, int, int, int], reason: str, budget: list[int]) -> None:
    first_row, last_row, first_column, last_column = bounds
    area = (last_row - first_row + 1) * (last_column - first_column + 1)
    if area < 1 or budget[0] + area > MAX_CALCULATION_REFERENCE_CELLS:
        raise UnsupportedWorkbook("unsupported result regions exceed the 100000-cell import limit")
    budget[0] += area
    for row in range(first_row, last_row + 1):
        for column in range(first_column, last_column + 1):
            cell = cells.setdefault(_cell_address(column, row), {"value": None})
            if cell.get("value") is not None:
                cell["cached_value"] = cell["value"]
                cell["value"] = None
            cell["blocked_reason"] = reason


def import_xlsx(path: str | Path, *, inputs=None, outputs=None, backend: str = "python") -> WorkbookModel:
    """Extract an XLSX with explicit optional application input/output bindings.

    Styles, names, tables, validation, and opaque package content are preserved.
    Formula groups and table formula regions remain inspectable but cannot be
    calculated or edited. Excessively large unsupported regions are rejected.
    """
    import json

    with Workbook.open(path) as workbook:
        styles, style_metadata = _styles(workbook)
        document: dict[str, Any] = {"schema_version": 1, "revision": 0, "sheets": [], "inputs": inputs or {}, "outputs": outputs or {}}
        metadata: dict[str, Any] = {
            "date_system": "1904" if workbook._uses_1904_date_system else "1900",
            "styles": style_metadata, "defined_names": [], "tables": [], "sheets": [],
            "supported": ["scalar cells", "normal formulas", "basic style inspection", "column widths"],
            "preserved_only": ["unmodeled package parts", "imported styles and validation", "defined names", "table metadata"],
            "rejected": ["macros", "structural edits", "imported style or validation edits", "calculation through blocked cells"],
            "package_parts": list(workbook._parts),
        }
        wb_root = _safe_xml(workbook._parts[workbook._workbook_part], workbook._workbook_part)
        for node in wb_root.findall("m:definedNames/m:definedName", NS):
            metadata["defined_names"].append({"attributes": dict(node.attrib), "expression": node.text or ""})
        for part, data in workbook._parts.items():
            if workbook._content_type_for(part) == TABLE_CONTENT_TYPE:
                table = _safe_xml(data, part)
                metadata["tables"].append({"part": part, "attributes": dict(table.attrib), "columns": [dict(column.attrib) for column in table.findall("m:tableColumns/m:tableColumn", NS)]})
        budget = [0]
        width_work = 0
        for index, sheet in enumerate(workbook.sheet_names, 1):
            cells: dict[str, Any] = {}
            part = workbook._sheet_parts[sheet]
            root = _safe_xml(workbook._parts[part], part)
            groups = []
            for address, element in workbook._cells[sheet].items():
                source = workbook.get(sheet, address)
                node = element.find(_q("f"))
                cell: dict[str, Any] = {"value": None if node is not None else _scalar(source.value)}
                if node is not None:
                    cell["formula"] = node.text or ""
                    cell["cached_value"] = _scalar(source.value)
                    if not node.text or source.formula_kind not in {None, "normal"}:
                        cell["blocked_reason"] = f"unsupported formula kind {source.formula_kind!r}"
                    if workbook._uses_1904_date_system:
                        cell["blocked_reason"] = "1904 date-system formula calculation is unsupported"
                    if source.formula_kind in {"shared", "array", "dataTable"}:
                        reference = node.attrib.get("ref")
                        if reference:
                            match = RANGE_RE.fullmatch(reference)
                            if not match:
                                raise UnsupportedWorkbook(f"invalid grouped result range: {sheet}!{address}")
                            first_column, first_row = _cell_position(match[1] + match[2])
                            last_column, last_row = _cell_position((match[3] or match[1]) + (match[4] or match[2]))
                            if first_column > last_column or first_row > last_row:
                                raise UnsupportedWorkbook("reversed grouped result range")
                            groups.append(((first_row, last_row, first_column, last_column), f"grouped formula region {sheet}!{reference}"))
                        elif source.formula_kind != "shared" or not node.attrib.get("si"):
                            raise UnsupportedWorkbook("unbounded grouped formula cannot be imported safely")
                if source.cell_type not in {None, "n", "b", "e", "s", "str", "inlineStr"}:
                    cell["blocked_reason"] = f"unsupported stored cell type {source.cell_type!r}"
                if source.style_id is not None:
                    if not 0 <= source.style_id < len(styles):
                        raise WorkbookError(f"invalid style index: {sheet}!{address}")
                    cell["style"] = styles[source.style_id]
                cells[address] = cell
            # Shared followers must have a real master range. A cache alone
            # cannot establish either their formula or their result footprint.
            shared_masters = {node.attrib.get("si") for element in workbook._cells[sheet].values() if (node := element.find(_q("f"))) is not None and node.attrib.get("t") == "shared" and node.attrib.get("ref")}
            for address, element in workbook._cells[sheet].items():
                node = element.find(_q("f"))
                if node is not None and node.attrib.get("t") == "shared" and not node.attrib.get("ref"):
                    index_value = node.attrib.get("si")
                    if index_value not in shared_masters:
                        raise UnsupportedWorkbook("shared formula follower has no bounded master")
            for bounds, reason in groups:
                _block_range(cells, bounds, reason, budget)
            for first_row, last_row, first_column, last_column, owner in workbook._table_formula_ranges.get(sheet.casefold(), []):
                _block_range(cells, (first_row, last_row, first_column, last_column), f"table formula result region {owner}", budget)
            widths = {}
            for col in root.findall("m:cols/m:col", NS):
                first, last = int(col.attrib["min"]), int(col.attrib["max"])
                if not 1 <= first <= last <= 16384:
                    raise WorkbookError("invalid column-width range")
                width_work += last - first + 1
                if width_work > MAX_CALCULATION_REFERENCE_CELLS:
                    raise UnsupportedWorkbook("column-width expansion exceeds the 100000-cell import work limit")
                if "width" in col.attrib:
                    width = float(col.attrib["width"])
                    if not math.isfinite(width) or not 0 <= width <= 255:
                        raise UnsupportedWorkbook("column width outside supported range")
                    if width == 0:
                        continue  # Hidden zero-width columns stay in the preserved baseline.
                    for column in range(first, last + 1):
                        widths[_cell_address(column, 1)[:-1]] = width
            document["sheets"].append({"id": f"sheet-{index}", "name": sheet, "cells": cells, "column_widths": widths})
            metadata["sheets"].append({"name": sheet, "part": part, "stored_cells": len(workbook._cells[sheet]), "blocked_cells": sum(bool(cell.get("blocked_reason")) for cell in cells.values()), "validation": [ET.tostring(item, encoding="unicode") for item in root.findall("m:dataValidations/m:dataValidation", NS)]})
        model = WorkbookModel(document=document, backend=backend)
        model._source_baseline = _Baseline(workbook.source, tuple(workbook._parts.items()), tuple(copy.copy(info) for info in workbook._archive.infolist()), workbook._archive.comment, json.dumps(model.to_dict(), allow_nan=False), workbook._uses_1904_date_system)
        model._import_metadata = metadata
        return model


def _xml(root: ET.Element) -> bytes:
    # The patch writer preserves source prefixes and intentionally refuses
    # ElementTree-reserved ns0 names. Serialize fresh parts with stable names
    # under the same lock used by that writer.
    with _XML_SERIALIZE_LOCK:
        ET.register_namespace("", root.tag[1:].split("}", 1)[0])
        ET.register_namespace("r", REL_DOC)
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _style_table(sheets) -> tuple[bytes, dict[str, int]]:
    import json

    root = ET.Element(_q("styleSheet"))
    formats = ET.SubElement(root, _q("numFmts"))
    fonts = ET.SubElement(root, _q("fonts"))
    fills = ET.SubElement(root, _q("fills"))
    for pattern in ("none", "gray125"):
        ET.SubElement(ET.SubElement(fills, _q("fill")), _q("patternFill"), {"patternType": pattern})
    borders = ET.SubElement(root, _q("borders"), {"count": "1"})
    border = ET.SubElement(borders, _q("border"))
    for side in ("left", "right", "top", "bottom", "diagonal"):
        ET.SubElement(border, _q(side))
    base = ET.SubElement(root, _q("cellStyleXfs"), {"count": "1"})
    ET.SubElement(base, _q("xf"), {"numFmtId": "0", "fontId": "0", "fillId": "0", "borderId": "0"})
    xfs = ET.SubElement(root, _q("cellXfs"))
    styles = [{}]
    ids = {"{}": 0}
    style_work = 512
    for sheet in sheets:
        for cell in sheet["cells"].values():
            style = {key: value for key, value in (cell.get("style") or {}).items() if value is not None}
            key = json.dumps(style, sort_keys=True)
            if key not in ids:
                # Bound XML expansion before allocating the complete style tree.
                style_work += len(key.encode("utf-8")) * 6 + 512
                if style_work > MAX_XML_PART_BYTES:
                    raise UnsupportedWorkbook("generated style table exceeds the XML work limit")
                ids[key] = len(styles)
                styles.append(style)
    for index, style in enumerate(styles):
        font = ET.SubElement(fonts, _q("font"))
        ET.SubElement(font, _q("sz"), {"val": "11"})
        ET.SubElement(font, _q("name"), {"val": "Arial"})
        if style.get("bold"):
            ET.SubElement(font, _q("b"))
        if style.get("font_color"):
            ET.SubElement(font, _q("color"), {"rgb": _rgb(style["font_color"])})
        fill_id = "0"
        if style.get("fill_color"):
            fill_id = str(len(fills))
            pattern = ET.SubElement(ET.SubElement(fills, _q("fill")), _q("patternFill"), {"patternType": "solid"})
            ET.SubElement(pattern, _q("fgColor"), {"rgb": _rgb(style["fill_color"])})
            ET.SubElement(pattern, _q("bgColor"), {"indexed": "64"})
        format_id = "0"
        if style.get("number_format"):
            _validate_xml_text(style["number_format"], "number format", 255)
            format_id = str(164 + index)
            ET.SubElement(formats, _q("numFmt"), {"numFmtId": format_id, "formatCode": style["number_format"]})
        xf = ET.SubElement(xfs, _q("xf"), {"numFmtId": format_id, "fontId": str(index), "fillId": fill_id, "borderId": "0", "xfId": "0", "applyNumberFormat": "1", "applyFont": "1", "applyFill": "1"})
        if style.get("horizontal"):
            xf.set("applyAlignment", "1")
            ET.SubElement(xf, _q("alignment"), {"horizontal": style["horizontal"]})
    for collection in (formats, fonts, fills, xfs):
        collection.set("count", str(len(collection)))
    names = ET.SubElement(root, _q("cellStyles"), {"count": "1"})
    ET.SubElement(names, _q("cellStyle"), {"name": "Normal", "xfId": "0", "builtinId": "0"})
    return _xml(root), ids


def _rgb(value: str) -> str:
    import re

    value = value.lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?", value):
        raise ValueError("style colors must be six-digit RGB or eight-digit ARGB hex")
    return ("FF" + value if len(value) == 6 else value).upper()


def _validation(binding: dict[str, Any]) -> str:
    address = _normal_address(binding["address"])
    kind = binding.get("kind", "number")
    clauses = [{"number": "ISNUMBER", "text": "ISTEXT", "boolean": "ISLOGICAL"}[kind] + f"({address})"]
    for field, operator in (("min", ">="), ("max", "<=")):
        if binding.get(field) is not None:
            clauses.append(f"{address}{operator}{binding[field]}")
    if binding.get("choices") is not None:
        choices = []
        for value in binding["choices"]:
            if isinstance(value, str):
                choices.append(f'EXACT({address},"{value.replace(chr(34), chr(34) * 2)}")')
            elif isinstance(value, bool):
                choices.append(f"{address}={'TRUE()' if value else 'FALSE()'}")
            elif isinstance(value, (float, int)):
                choices.append(f"{address}={value}")
            else:
                raise UnsupportedWorkbook("XLSX input choice must be text, numeric, or Boolean")
        if not choices:
            raise UnsupportedWorkbook("XLSX input choice list cannot be empty")
        clauses.append("OR(" + ",".join(choices) + ")")
    formula = "AND(" + ",".join(clauses) + ")"
    if not binding.get("required", True):
        formula = f"OR(ISBLANK({address}),{formula})"
    _validate_xml_text(formula, "input validation", 255)
    return formula


def _write_cell_content(element: ET.Element, cell: dict[str, Any]) -> None:
    formula = cell.get("formula")
    if formula is not None:
        formula = formula.removeprefix("=")
        _validate_xml_text(formula, "formula", 8192)
        ET.SubElement(element, _q("f")).text = formula
        return
    value = cell.get("value")
    if value is None:
        return
    if isinstance(value, dict):
        if value.get("error") not in EXCEL_ERROR_CODES:
            raise UnsupportedWorkbook("unsupported stored error value")
        element.set("t", "e")
        ET.SubElement(element, _q("v")).text = value["error"]
    elif isinstance(value, bool):
        element.set("t", "b")
        ET.SubElement(element, _q("v")).text = "1" if value else "0"
    elif isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError("non-finite cell value")
        ET.SubElement(element, _q("v")).text = str(value)
    elif isinstance(value, str):
        _validate_xml_text(value, "cell value", 32767)
        element.set("t", "inlineStr")
        text = ET.SubElement(ET.SubElement(element, _q("is")), _q("t"))
        text.set(f"{{{XML_NS}}}space", "preserve")
        text.text = value
    else:
        raise UnsupportedWorkbook("unsupported stored cell value")


def _fresh_parts(document: dict[str, Any]) -> dict[str, bytes]:
    import json

    types = ET.Element(f"{{{CONTENT_TYPES}}}Types")
    ET.SubElement(types, f"{{{CONTENT_TYPES}}}Default", {"Extension": "rels", "ContentType": "application/vnd.openxmlformats-package.relationships+xml"})
    ET.SubElement(types, f"{{{CONTENT_TYPES}}}Default", {"Extension": "xml", "ContentType": "application/xml"})
    package_rels = ET.Element(f"{{{PKG_REL}}}Relationships")
    ET.SubElement(package_rels, f"{{{PKG_REL}}}Relationship", {"Id": "rId1", "Type": f"{REL_DOC}/officeDocument", "Target": "xl/workbook.xml"})
    root = ET.Element(_q("workbook"))
    ET.SubElement(root, _q("workbookPr"), {"date1904": "0"})
    sheets_node = ET.SubElement(root, _q("sheets"))
    rels = ET.Element(f"{{{PKG_REL}}}Relationships")
    parts = {}
    style_bytes, style_ids = _style_table(document["sheets"])
    parts["xl/styles.xml"] = style_bytes
    ET.SubElement(rels, f"{{{PKG_REL}}}Relationship", {"Id": "styles", "Type": f"{REL_DOC}/styles", "Target": "styles.xml"})
    for part, content_type in (("xl/workbook.xml", WORKBOOK_CONTENT_TYPE), ("xl/styles.xml", STYLES_CONTENT_TYPE)):
        ET.SubElement(types, f"{{{CONTENT_TYPES}}}Override", {"PartName": "/" + part, "ContentType": content_type})
    for index, sheet in enumerate(document["sheets"], 1):
        name = sheet["name"]
        _validate_xml_text(name, "worksheet name", 31)
        ET.SubElement(sheets_node, _q("sheet"), {"name": name, "sheetId": str(index), f"{{{REL_DOC}}}id": f"rId{index}"})
        part = f"xl/worksheets/sheet{index}.xml"
        ET.SubElement(rels, f"{{{PKG_REL}}}Relationship", {"Id": f"rId{index}", "Type": f"{REL_DOC}/worksheet", "Target": f"worksheets/sheet{index}.xml"})
        ET.SubElement(types, f"{{{CONTENT_TYPES}}}Override", {"PartName": "/" + part, "ContentType": WORKSHEET_CONTENT_TYPE})
        worksheet = ET.Element(_q("worksheet"))
        if sheet.get("column_widths"):
            columns = ET.SubElement(worksheet, _q("cols"))
            for column, width in sorted(sheet["column_widths"].items(), key=lambda item: _cell_position(item[0] + "1")[0]):
                column_number, _ = _cell_position(column + "1")
                ET.SubElement(columns, _q("col"), {"min": str(column_number), "max": str(column_number), "width": str(width), "customWidth": "1"})
        data = ET.SubElement(worksheet, _q("sheetData"))
        rows = {}
        sheet_work = 512
        for address, cell in sorted(sheet["cells"].items(), key=lambda item: _cell_position(item[0])[::-1]):
            address = _normal_address(address)
            _, row_number = _cell_position(address)
            if row_number not in rows:
                rows[row_number] = ET.SubElement(data, _q("row"), {"r": str(row_number)})
            style_key = json.dumps({key: value for key, value in (cell.get("style") or {}).items() if value is not None}, sort_keys=True)
            element = ET.SubElement(rows[row_number], _q("c"), {"r": address, "s": str(style_ids[style_key])})
            _write_cell_content(element, cell)
            sheet_work += len(ET.tostring(element, encoding="utf-8")) + 40
            if sheet_work > MAX_XML_PART_BYTES:
                raise UnsupportedWorkbook("generated worksheet exceeds the XML work limit")
        validations = [binding for binding in document.get("inputs", {}).values() if binding["sheet"].casefold() == name.casefold()]
        if validations:
            validation_nodes = ET.SubElement(worksheet, _q("dataValidations"), {"count": str(len(validations))})
            for binding in validations:
                validation = ET.SubElement(validation_nodes, _q("dataValidation"), {"type": "custom", "allowBlank": "0" if binding.get("required", True) else "1", "showErrorMessage": "1", "errorStyle": "stop", "errorTitle": "Invalid input", "error": "Enter a value allowed by this model.", "sqref": _normal_address(binding["address"])})
                ET.SubElement(validation, _q("formula1")).text = _validation(binding)
                sheet_work += len(ET.tostring(validation, encoding="utf-8"))
                if sheet_work > MAX_XML_PART_BYTES:
                    raise UnsupportedWorkbook("generated validation exceeds the XML work limit")
        parts[part] = _xml(worksheet)
        if sum(map(len, parts.values())) > MAX_PACKAGE_BYTES:
            raise UnsupportedWorkbook("generated XLSX exceeds package resource limits")
    parts["xl/workbook.xml"] = _xml(root)
    parts["xl/_rels/workbook.xml.rels"] = _xml(rels)
    parts["_rels/.rels"] = _xml(package_rels)
    parts["[Content_Types].xml"] = _xml(types)
    if len(parts) > MAX_PACKAGE_ENTRIES or sum(map(len, parts.values())) > MAX_PACKAGE_BYTES or any(len(part) > MAX_XML_PART_BYTES for part in parts.values()):
        raise UnsupportedWorkbook("generated XLSX exceeds package resource limits")
    return parts


def _check_preserved_validation(workbook: Workbook, document: dict[str, Any]) -> None:
    """Never imply that new application constraints already exist in Excel.

    This first transformer preserves imported validation rather than replacing
    rules it cannot understand. Only safe outer whitespace/equals differences
    are normalized; strings and the formula's logic retain exact meaning.
    """
    for binding in document.get("inputs", {}).values():
        sheet = next(name for name in workbook.sheet_names if name.casefold() == binding["sheet"].casefold())
        part = workbook._sheet_parts[sheet]
        root = _safe_xml(workbook._parts[part], part)
        address = _normal_address(binding["address"])
        covering = [
            node for node in root.findall("m:dataValidations/m:dataValidation", NS)
            if any(_in_range(address, reference) for reference in node.attrib.get("sqref", "").split())
        ]
        expected_blank = not binding.get("required", True)
        matching = False
        if len(covering) == 1:
            node = covering[0]
            formula = node.find(_q("formula1"))
            matching = (
                node.attrib.get("type") == "custom"
                and node.attrib.get("showErrorMessage", "0") in {"1", "true"}
                and node.attrib.get("errorStyle", "stop") == "stop"
                and (node.attrib.get("allowBlank", "0") in {"1", "true"}) == expected_blank
                and formula is not None
                and (formula.text or "").strip().removeprefix("=") == _validation(binding)
            )
        if not matching:
            raise UnsupportedWorkbook(
                f"input constraints at {sheet}!{address} do not match preserved Excel validation; "
                "imported validation changes are unsupported"
            )


def _patch_import(workbook: Workbook, original: dict[str, Any], current: dict[str, Any]) -> None:
    if original["inputs"] != current["inputs"]:
        raise UnsupportedWorkbook("imported input/validation bindings cannot be changed during export")
    if [(sheet["id"], sheet["name"], sheet["column_widths"]) for sheet in original["sheets"]] != [(sheet["id"], sheet["name"], sheet["column_widths"]) for sheet in current["sheets"]]:
        raise UnsupportedWorkbook("imported workbook structural and column-width edits are unsupported")
    _check_preserved_validation(workbook, current)
    for before, after in zip(original["sheets"], current["sheets"], strict=True):
        for address in before["cells"].keys() | after["cells"].keys():
            old, new = before["cells"].get(address, {}), after["cells"].get(address, {})
            if old == new:
                continue
            if old.get("blocked_reason") or new.get("blocked_reason"):
                raise UnsupportedWorkbook(f"cannot edit preserved-only cell {after['name']}!{address}")
            if old.get("style") != new.get("style"):
                raise UnsupportedWorkbook("imported style changes require a separately audited style transformer")
            if old.get("formula") == new.get("formula") and old.get("value") == new.get("value"):
                continue
            if new.get("formula") is not None:
                workbook.set_formula(after["name"], address, "=" + new["formula"].removeprefix("="))
            elif isinstance(new.get("value"), dict):
                workbook.set_value(after["name"], address, None)
                _write_cell_content(workbook._writable_cell(after["name"], address), new)
            else:
                workbook.set_value(after["name"], address, new.get("value"))


def export_xlsx(model: WorkbookModel, output: str | Path, *, report: dict[str, Any] | None = None) -> Path:
    """Write to a new path, after staging and reopening the complete package.

    Supplied reports must come from this model's ``calculate()`` method and
    match its current snapshot. Unsupported unrelated formulas survive import
    and export when explicit output bindings exclude them from calculation.
    """
    import json

    target = Path(output).resolve()
    if target.suffix.lower() != ".xlsx":
        raise UnsupportedWorkbook("output must use the .xlsx extension")
    baseline = model._source_baseline
    if baseline is not None and target == baseline.source:
        raise WorkbookError("refusing to overwrite the source workbook")
    if target.exists():
        raise FileExistsError(target)
    document = model.to_dict()
    calculation = model.calculate() if report is None else copy.deepcopy(report)
    if calculation.get("stale") or calculation.get("revision") != document["revision"] or calculation.get("model_digest") != _digest(document):
        raise WorkbookError("calculation report is stale or belongs to a different model snapshot")
    if report is not None:
        # Caller-owned dictionaries are evidence, not cache authority. A second
        # engine calculation reuses the session cache but verifies every supplied
        # value against the current snapshot before any package is published.
        authoritative = model.calculate()
        if (
            authoritative.get("stale")
            or authoritative.get("revision") != document["revision"]
            or authoritative.get("model_digest") != _digest(document)
        ):
            raise WorkbookError("model changed while validating the calculation report")
        fields = ("values", "outputs", "diagnostics")
        if _digest({key: calculation.get(key) for key in fields}) != _digest(
            {key: authoritative.get(key) for key in fields}
        ):
            raise WorkbookError("supplied calculation report differs from authoritative engine results")
        calculation = authoritative
    if calculation.get("diagnostics"):
        raise UnsupportedWorkbook("cannot export calculation diagnostics: " + "; ".join(item["message"] for item in calculation["diagnostics"]))
    cache_values = {}
    for sheet in document["sheets"]:
        for address, cell in sheet["cells"].items():
            label = f"{sheet['name']}!{address}"
            if cell.get("formula") is not None and label in calculation.get("values", {}):
                value = calculation["values"][label]
                if isinstance(value, dict):
                    if "rows" in value:
                        raise UnsupportedWorkbook("worksheet array spill caches are unsupported")
                    value = ErrorValue(value["error"])
                cache_values[(sheet["name"], address)] = Workbook._normalize_formula_cache(value, sheet["name"], address)
    if baseline is not None and baseline.uses_1904_date_system and cache_values:
        raise UnsupportedWorkbook("1904 date-system formula caches cannot be calculated or exported")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".workbook-forge-", dir=target.parent) as folder:
        seed, staged = Path(folder) / "seed.xlsx", Path(folder) / "result.xlsx"
        with zipfile.ZipFile(seed, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            if baseline is None:
                for part, data in _fresh_parts(document).items():
                    archive.writestr(part, data)
            else:
                parts = dict(baseline.parts)
                archive.comment = baseline.comment
                for info in baseline.infos:
                    archive.writestr(info, parts[info.filename])
        with Workbook.open(seed) as workbook:
            if baseline is not None:
                _patch_import(workbook, json.loads(baseline.document_json), document)
            workbook._request_recalculation()
            for (sheet, address), value in cache_values.items():
                workbook._write_formula_cache(sheet, address, value)
            workbook.save_as(staged)
        with Workbook.open(staged):
            pass
        if model.revision != document["revision"]:
            raise WorkbookError("model changed during export; retry from its current snapshot")
        os.link(staged, target)
    return target
