"""Typed inspect metadata and write guards for drop-downs, pivots and charts.

Imported workbooks keep these features byte-for-byte; this module reads them
for ``inspect()`` and decides which cell writes they forbid. The rules are the
ones in docs/xlsx-features.md, and rust/src/xlsx_features.rs implements the
same rules; tools/corpus/feature-cases.json holds the cases both engines must
agree on.

- Data validation ("drop-downs"): every ``dataValidation`` is reported with
  its type, operator, source (inline list, range, or defined name) and, where
  it can be resolved, its allowed values. A rule is *enforced* when Excel
  would stop the entry: ``showErrorMessage`` is on and ``errorStyle`` is
  ``stop`` (the default). An enforced rule refuses an out-of-rule value with
  ``validation_failed``. A formula entry is checked by its calculated
  result, as Excel checks it once at entry. Rules this module cannot evaluate (custom formulas,
  lists built by a formula, non-constant bounds) are reported with
  ``enforced: false`` and a reason; writes into them are not checked.
- Pivot tables: location, source range and fields are reported. Refresh is
  not supported. A write into a pivot's output range refuses with
  ``pivot_output_protected``.
- Charts: type, anchor and series references are reported. Value writes keep
  references valid. Structural changes to a sheet a chart reads refuse with
  ``chart_reference_protected``.
"""
from __future__ import annotations

import math
import posixpath
import re
from typing import Any
from xml.etree import ElementTree as ET

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
CHART = "http://schemas.openxmlformats.org/drawingml/2006/chart"
NS = {"m": MAIN, "r": REL_DOC, "p": PKG_REL, "xdr": DRAWING, "c": CHART}

ENFORCEABLE_TYPES = {"list", "whole", "decimal", "date", "time", "textLength"}
OPERATORS = {
    "between", "notBetween", "equal", "notEqual", "greaterThan", "lessThan",
    "greaterThanOrEqual", "lessThanOrEqual",
}
CHART_KINDS = (
    "area3DChart", "areaChart", "bar3DChart", "barChart", "bubbleChart", "doughnutChart",
    "line3DChart", "lineChart", "ofPieChart", "pie3DChart", "pieChart", "radarChart",
    "scatterChart", "stockChart", "surface3DChart", "surfaceChart",
)
# Pivot outputs, chart sources and validation lists are only ever read; the
# limit bounds how many cells one resolved list may hold.
MAX_LIST_CELLS = 10_000

_CELL = re.compile(r"^\$?([A-Za-z]{1,3})\$?([0-9]+)$")
_COLUMN = re.compile(r"^\$?([A-Za-z]{1,3})$")
_ROW = re.compile(r"^\$?([0-9]+)$")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value in {"1", "true"}


def column_number(letters: str) -> int:
    number = 0
    for char in letters.upper():
        number = number * 26 + ord(char) - 64
    return number


def column_name(number: int) -> str:
    name = ""
    while number:
        number, rem = divmod(number - 1, 26)
        name = chr(65 + rem) + name
    return name


def range_bounds(reference: str) -> tuple[int, int, int, int] | None:
    """``A1``, ``A1:B4``, ``A:A`` or ``2:2`` -> (c1, r1, c2, r2); None if malformed."""
    first, _, last = reference.partition(":")
    last = last or first
    a, b = _CELL.match(first), _CELL.match(last)
    if a and b:
        c1, r1, c2, r2 = column_number(a[1]), int(a[2]), column_number(b[1]), int(b[2])
    elif (a := _COLUMN.match(first)) and (b := _COLUMN.match(last)) and ":" in reference:
        c1, r1, c2, r2 = column_number(a[1]), 1, column_number(b[1]), 1_048_576
    elif (a := _ROW.match(first)) and (b := _ROW.match(last)) and ":" in reference:
        c1, r1, c2, r2 = 1, int(a[1]), 16_384, int(b[1])
    else:
        return None
    c1, c2 = sorted((c1, c2))
    r1, r2 = sorted((r1, r2))
    if not (1 <= c1 and c2 <= 16_384 and 1 <= r1 and r2 <= 1_048_576):
        return None
    return c1, r1, c2, r2


def position(address: str) -> tuple[int, int]:
    match = _CELL.match(address)
    if match is None:
        raise ValueError(address)
    return column_number(match[1]), int(match[2])


def _contains(bounds: tuple[int, int, int, int], column: int, row: int) -> bool:
    c1, r1, c2, r2 = bounds
    return c1 <= column <= c2 and r1 <= row <= r2


def split_reference(text: str) -> tuple[str | None, str] | None:
    """``'My Sheet'!$A$1:$A$4`` -> ("My Sheet", "$A$1:$A$4"); None if it is not a reference."""
    text = text.strip()
    sheet = None
    if text.startswith("'"):
        end = 1
        name = ""
        while end < len(text):
            if text[end] == "'":
                if text[end + 1 : end + 2] == "'":
                    name += "'"
                    end += 2
                    continue
                break
            name += text[end]
            end += 1
        if text[end + 1 : end + 2] != "!":
            return None
        sheet, text = name, text[end + 2 :]
    elif "!" in text:
        sheet, text = text.split("!", 1)
        if not re.fullmatch(r"[A-Za-z0-9_.]+", sheet):
            return None
    if range_bounds(text) is None:
        return None
    return sheet, text


def parse_inline_list(text: str) -> list[str] | None:
    """Excel's inline list (one quoted string, items split on commas, ``""`` for a quote)."""
    if len(text) < 2 or not text.startswith('"') or not text.endswith('"'):
        return None
    body = text[1:-1]
    if re.search(r'(?<!")"(?!")', body.replace('""', "")):
        return None
    return [item.strip() for item in body.replace('""', '"').split(",")]


def parse_array_constant(text: str) -> list[str] | None:
    """``{"Red","Green"}`` or ``{1,2}`` -> items; None otherwise (one row or column only)."""
    text = text.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None
    items = []
    for raw in re.split(r"[,;](?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", text[1:-1]):
        raw = raw.strip()
        if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
            items.append(raw[1:-1].replace('""', '"'))
        elif re.fullmatch(r"[-+]?[0-9.]+(?:[eE][-+]?[0-9]+)?|TRUE|FALSE", raw, re.IGNORECASE):
            items.append(raw)
        else:
            return None
    return items


def _number(text: str | None) -> float | None:
    if text is None:
        return None
    text = text.strip().removeprefix("=")
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


# ------------------------------------------------------------- names


def defined_names(workbook_root: ET.Element, sheet_entries: list[str]) -> dict[tuple[str | None, str], str]:
    """(scope sheet or None, casefolded name) -> expression."""
    names: dict[tuple[str | None, str], str] = {}
    for node in workbook_root.findall("m:definedNames/m:definedName", NS):
        name = node.attrib.get("name", "")
        scope = None
        local = node.attrib.get("localSheetId")
        if local is not None and local.isdigit() and int(local) < len(sheet_entries):
            scope = sheet_entries[int(local)]
        names[(scope, name.casefold())] = (node.text or "").strip()
    return names


# ------------------------------------------------------- data validation


def read_validations(sheet: str, root: ET.Element, names: dict) -> list[dict[str, Any]]:
    rules = []
    for index, node in enumerate(root.findall("m:dataValidations/m:dataValidation", NS)):
        attrs = node.attrib
        kind = attrs.get("type", "none")
        formula1 = node.findtext("m:formula1", default=None, namespaces=NS)
        formula2 = node.findtext("m:formula2", default=None, namespaces=NS)
        error_style = attrs.get("errorStyle", "stop")
        show_error = _bool(attrs.get("showErrorMessage"), False)
        rule: dict[str, Any] = {
            "sheet": sheet,
            "index": index,
            "sqref": attrs.get("sqref", "").split(),
            "type": kind,
            "operator": attrs.get("operator", "between") if kind not in {"list", "custom", "none"} else None,
            "allow_blank": _bool(attrs.get("allowBlank"), False),
            "show_error": show_error,
            "error_style": error_style,
            # showDropDown="1" *hides* the in-cell arrow in OOXML.
            "in_cell_dropdown": kind == "list" and not _bool(attrs.get("showDropDown"), False),
            "formula1": formula1,
            "formula2": formula2,
            "error_title": attrs.get("errorTitle"),
            "error": attrs.get("error"),
            "source": None,
            "allowed_values": None,
            "enforced": False,
            "unenforced_reason": None,
        }
        reason = None
        if kind == "list":
            rule["source"] = _list_source(sheet, (formula1 or "").strip(), names)
            if rule["source"]["kind"] == "inline":
                rule["allowed_values"] = rule["source"]["values"]
            elif rule["source"]["kind"] in {"range", "name"} and "values" in rule["source"]:
                rule["allowed_values"] = rule["source"]["values"]
            elif rule["source"]["kind"] not in {"range", "name"} or "range" not in rule["source"]:
                reason = "list source is a formula this engine does not evaluate"
        elif kind in ENFORCEABLE_TYPES:
            operator = rule["operator"]
            bounds = [_number(formula1)] + ([_number(formula2)] if operator in {"between", "notBetween"} else [])
            if operator not in OPERATORS:
                reason = f"unknown operator {operator}"
            elif any(value is None for value in bounds):
                reason = "bounds are formulas this engine does not evaluate"
        elif kind == "custom":
            reason = "custom formula rules are not evaluated"
        else:
            reason = "no rule"
        if reason is None and not show_error:
            reason = "Excel shows no error alert (showErrorMessage is off), so any value is accepted"
        elif reason is None and error_style != "stop":
            reason = f"errorStyle {error_style} lets the entry through in Excel"
        rule["enforced"] = reason is None
        rule["unenforced_reason"] = reason
        rule["_bounds"] = [b for b in (range_bounds(r) for r in rule["sqref"]) if b]
        rules.append(rule)
    return rules


def _list_source(sheet: str, formula: str, names: dict, depth: int = 0) -> dict[str, Any]:
    text = formula.removeprefix("=").strip()
    inline = parse_inline_list(text)
    if inline is not None:
        return {"kind": "inline", "values": inline}
    reference = split_reference(text)
    if reference is not None:
        target_sheet, cells = reference
        return {"kind": "range", "sheet": target_sheet or sheet, "range": cells.replace("$", "")}
    if re.fullmatch(r"[A-Za-z_\\][A-Za-z0-9_.\\]*", text) and depth == 0:
        expression = names.get((sheet, text.casefold()), names.get((None, text.casefold())))
        if expression is None:
            return {"kind": "name", "name": text, "resolves_to": None}
        array = parse_array_constant(expression)
        if array is not None:
            return {"kind": "name", "name": text, "resolves_to": expression, "values": array}
        inner = _list_source(sheet, expression, names, depth + 1)
        if inner["kind"] == "range":
            return {"kind": "name", "name": text, "resolves_to": expression,
                    "sheet": inner["sheet"], "range": inner["range"]}
        return {"kind": "name", "name": text, "resolves_to": expression}
    return {"kind": "formula", "formula": text}


def resolve_list_values(rule: dict[str, Any], read) -> list[Any] | None:
    """Current allowed values; ``read(sheet, address)`` returns a cell's value or None."""
    source = rule["source"]
    if "values" in source:
        return source["values"]
    if "range" not in source:
        return None
    bounds = range_bounds(source["range"])
    if bounds is None:
        return None
    c1, r1, c2, r2 = bounds
    if (c2 - c1 + 1) * (r2 - r1 + 1) > MAX_LIST_CELLS:
        return None
    values = []
    for row in range(r1, r2 + 1):
        for column in range(c1, c2 + 1):
            value = read(source["sheet"], f"{column_name(column)}{row}")
            if value is not None and value != "":
                values.append(value)
    return values


def _list_item_matches(item: Any, value: Any) -> bool:
    if isinstance(value, bool):
        return str(item).strip().upper() == ("TRUE" if value else "FALSE")
    if isinstance(value, (int, float)):
        if isinstance(item, bool):
            return False
        if isinstance(item, (int, float)):
            return float(item) == float(value)
        number = _number(str(item))
        return number is not None and number == float(value)
    if isinstance(item, bool):
        return str(value).strip().upper() == ("TRUE" if item else "FALSE")
    if isinstance(item, (int, float)):
        return False  # Text never matches a number in the source range.
    # Excel's list check is not case-sensitive.
    return str(item).casefold() == str(value).casefold()


def general_text(value: float) -> str:
    """A number as text with 15 significant digits (``%.15g``, ``E`` exponent).

    Used when a number is written into a Text-formatted (``@``) cell and to
    measure a number for a text-length rule. Python and Rust share this rule.
    """
    text = format(float(value), ".15g")
    if "e" in text:
        mantissa, exponent = text.split("e")
        sign = "-" if exponent.startswith("-") else "+"
        text = f"{mantissa}E{sign}{exponent.lstrip('+-').rjust(2, '0')}"
    return "0" if text == "-0" else text


def _general(item: Any) -> str:
    if isinstance(item, bool):
        return "TRUE" if item else "FALSE"
    if isinstance(item, float) and item.is_integer() and abs(item) < 1e15:
        return str(int(item))
    return str(item)


def _compare(operator: str, value: float, low: float, high: float | None) -> bool:
    return {
        "between": lambda: low <= value <= (high if high is not None else low),
        "notBetween": lambda: not (low <= value <= (high if high is not None else low)),
        "equal": lambda: value == low,
        "notEqual": lambda: value != low,
        "greaterThan": lambda: value > low,
        "lessThan": lambda: value < low,
        "greaterThanOrEqual": lambda: value >= low,
        "lessThanOrEqual": lambda: value <= low,
    }[operator]()


def rule_violation(rule: dict[str, Any], value: Any, read) -> str | None:
    """Why ``value`` breaks an enforced rule, or None when Excel would accept it."""
    if not rule["enforced"] or value is None or value == "":
        return None  # Clearing a cell is never validated in Excel.
    if isinstance(value, dict):
        return "an error value is not an allowed entry"
    kind = rule["type"]
    if kind == "list":
        allowed = resolve_list_values(rule, read)
        if allowed is None:
            return None
        if any(_list_item_matches(item, value) for item in allowed):
            return None
        shown = ", ".join(_general(item) for item in allowed[:20])
        return f"value is not in the drop-down list ({shown}{', ...' if len(allowed) > 20 else ''})"
    low = _number(rule["formula1"])
    high = _number(rule["formula2"])
    if kind == "textLength":
        if isinstance(value, bool):
            measured = float(len("TRUE" if value else "FALSE"))
        elif isinstance(value, (int, float)):
            measured = float(len(general_text(value)))
        else:
            measured = float(len(str(value)))
    else:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"a {kind} rule needs a number"
        measured = float(value)
        if kind == "whole" and not measured.is_integer():
            return "a whole-number rule needs an integer"
    if not _compare(rule["operator"], measured, low, high):
        bound = f"{rule['formula1']}" + (f" and {rule['formula2']}" if rule["formula2"] is not None and rule["operator"] in {"between", "notBetween"} else "")
        return f"{kind} rule {rule['operator']} {bound} is not met"
    return None


def covering_rule(rules: list[dict[str, Any]], sheet: str, column: int, row: int) -> dict[str, Any] | None:
    """The rule Excel applies to a cell: the first one whose sqref covers it."""
    for rule in rules:
        if rule["sheet"].casefold() == sheet.casefold() and any(
            _contains(bounds, column, row) for bounds in rule["_bounds"]
        ):
            return rule
    return None


# ------------------------------------------------------------- packages


def _rels(parts: dict[str, bytes], part: str) -> dict[str, tuple[str, str, bool]]:
    folder, name = posixpath.split(part)
    data = parts.get(posixpath.join(folder, "_rels", name + ".rels"))
    if data is None:
        return {}
    out = {}
    for rel in ET.fromstring(data).findall("p:Relationship", NS):
        target = rel.attrib.get("Target", "")
        external = rel.attrib.get("TargetMode") == "External"
        if external:
            resolved = target
        elif target.startswith("/"):
            resolved = target.lstrip("/")
        else:
            resolved = posixpath.normpath(posixpath.join(folder, target))
        out[rel.attrib.get("Id", "")] = (rel.attrib.get("Type", "").rsplit("/", 1)[-1], resolved, external)
    return out


def read_pivots(parts: dict[str, bytes], sheets: dict[str, str]) -> list[dict[str, Any]]:
    """Every pivot table on a worksheet: location, cache source and fields."""
    pivots = []
    for sheet, part in sheets.items():
        for kind, target, external in _rels(parts, part).values():
            if kind != "pivotTable" or external or target not in parts:
                continue
            root = ET.fromstring(parts[target])
            location = root.find("m:location", NS)
            ref = location.attrib.get("ref", "") if location is not None else ""
            source: dict[str, Any] = {"type": None}
            cache_fields: list[str] = []
            for cache_kind, cache_part, cache_external in _rels(parts, target).values():
                if cache_kind != "pivotCacheDefinition" or cache_external or cache_part not in parts:
                    continue
                cache = ET.fromstring(parts[cache_part])
                cache_source = cache.find("m:cacheSource", NS)
                if cache_source is not None:
                    source["type"] = cache_source.attrib.get("type")
                    worksheet = cache_source.find("m:worksheetSource", NS)
                    if worksheet is not None:
                        for key in ("sheet", "ref", "name"):
                            if key in worksheet.attrib:
                                source[key] = worksheet.attrib[key]
                cache_fields = [
                    field.attrib.get("name", "") for field in cache.findall("m:cacheFields/m:cacheField", NS)
                ]
                source["cache_part"] = cache_part
                source["refresh_on_load"] = _bool(cache.attrib.get("refreshOnLoad"), False)

            def names(path: str, attribute: str = "x") -> list[str]:
                out = []
                for field in root.findall(path, NS):
                    index = field.attrib.get(attribute)
                    if index is not None and index.lstrip("-").isdigit():
                        number = int(index)
                        out.append("Values" if number == -2 else (cache_fields[number] if 0 <= number < len(cache_fields) else f"#{number}"))
                return out

            pivots.append({
                "sheet": sheet,
                "part": target,
                "name": root.attrib.get("name"),
                "location": ref,
                "source": source,
                "fields": cache_fields,
                "row_fields": names("m:rowFields/m:field"),
                "column_fields": names("m:colFields/m:field"),
                "page_fields": names("m:pageFields/m:pageField", "fld"),
                "data_fields": [
                    {"name": field.attrib.get("name"),
                     "field": names_one(cache_fields, field.attrib.get("fld")),
                     "function": field.attrib.get("subtotal", "sum")}
                    for field in root.findall("m:dataFields/m:dataField", NS)
                ],
                "refresh": "unsupported: the cached output is preserved as stored; edits to the source do not update it",
                "_bounds": range_bounds(ref),
            })
    return pivots


def names_one(fields: list[str], index: str | None) -> str | None:
    if index is None or not index.isdigit():
        return None
    number = int(index)
    return fields[number] if number < len(fields) else f"#{number}"


def _anchor(node: ET.Element, tag: str) -> str | None:
    marker = node.find(f"xdr:{tag}", NS)
    if marker is None:
        return None
    try:
        column = int(marker.findtext("xdr:col", namespaces=NS)) + 1
        row = int(marker.findtext("xdr:row", namespaces=NS)) + 1
    except (TypeError, ValueError):
        return None
    return f"{column_name(column)}{row}"


def _chart(parts: dict[str, bytes], chart_part: str) -> dict[str, Any]:
    root = ET.fromstring(parts[chart_part])
    plot = root.find("c:chart/c:plotArea", NS)
    kinds, series = [], []
    for node in list(plot) if plot is not None else []:
        kind = _local(node.tag)
        if kind not in CHART_KINDS:
            continue
        kinds.append(kind)
        for ser in node.findall("c:ser", NS):
            refs = {}
            for role in ("tx", "cat", "val", "xVal", "yVal", "bubbleSize"):
                child = ser.find(f"c:{role}", NS)
                if child is None:
                    continue
                formula = next((item.text for item in child.iter() if _local(item.tag) == "f" and item.text), None)
                if formula:
                    refs[role] = formula
            series.append({"chart_type": kind, "refs": refs})
    return {"part": chart_part, "types": kinds, "series": series}


def read_charts(parts: dict[str, bytes], sheets: dict[str, str], chartsheets: dict[str, str]) -> list[dict[str, Any]]:
    """Every chart on a worksheet drawing or a chartsheet: type, anchor, series refs."""
    charts = []
    for host_kind, hosts in (("worksheet", sheets), ("chartsheet", chartsheets)):
        for sheet, part in hosts.items():
            for kind, drawing, external in _rels(parts, part).values():
                if kind != "drawing" or external or drawing not in parts:
                    continue
                drawing_rels = _rels(parts, drawing)
                root = ET.fromstring(parts[drawing])
                for anchor in list(root):
                    for frame in anchor.iter():
                        if _local(frame.tag) != "chart" or not frame.tag.startswith("{" + CHART):
                            continue
                        rid = frame.attrib.get(f"{{{REL_DOC}}}id")
                        rel = drawing_rels.get(rid or "")
                        if rel is None or rel[0] != "chart" or rel[2] or rel[1] not in parts:
                            continue
                        start, end = _anchor(anchor, "from"), _anchor(anchor, "to")
                        chart = _chart(parts, rel[1])
                        chart.update(
                            sheet=sheet,
                            host=host_kind,
                            anchor=(f"{start}:{end}" if start and end else start) if host_kind == "worksheet" else "whole sheet",
                        )
                        charts.append(chart)
    return charts


def referenced_sheets(formula: str) -> set[str]:
    """Sheet names a chart series formula reads (``Sheet1!$B$2:$B$5``, ``'A b'!$A$1``, ``(S!A1,S!A2)``)."""
    out = set()
    for match in re.finditer(r"'((?:[^']|'')+)'!|([A-Za-z0-9_.]+)!", formula):
        out.add((match[1] or match[2]).replace("''", "'"))
    return out


def public(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip private keys before a record goes into inspect output."""
    return [{key: value for key, value in item.items() if not key.startswith("_")} for item in items]


class FeatureGuard:
    """The cell-write rules an imported workbook's features impose."""

    def __init__(self, validations, pivots, charts):
        self.validations = validations
        self.pivots = pivots
        self.charts = charts

    def check(self, sheet: str, address: str, value: Any, formula: str | None, read) -> tuple[str, str] | None:
        """(code, message) when the write must be refused, else None."""
        try:
            column, row = position(address.replace("$", ""))
        except ValueError:
            return None
        for pivot in self.pivots:
            bounds = pivot["_bounds"]
            if bounds and pivot["sheet"].casefold() == sheet.casefold() and _contains(bounds, column, row):
                return (
                    "pivot_output_protected",
                    f"pivot_output_protected: {sheet}!{address} is inside pivot table "
                    f"{pivot['name']!s} output {pivot['location']}; pivot output is preserved "
                    "as stored and pivot refresh is unsupported",
                )
        if formula is not None:
            return None  # A formula entry is checked by its result: formula_rule/check_result.
        rule = covering_rule(self.validations, sheet, column, row)
        if rule is None:
            return None
        why = rule_violation(rule, value, read)
        if why is None:
            return None
        return (
            "validation_failed",
            f"validation_failed: {sheet}!{address} {value!r} is refused by the data validation "
            f"on {' '.join(rule['sqref'])}: {why}",
        )

    def formula_rule(self, sheet: str, address: str) -> dict[str, Any] | None:
        """The enforced rule a formula entry at this cell must satisfy, if any."""
        try:
            column, row = position(address.replace("$", ""))
        except ValueError:
            return None
        rule = covering_rule(self.validations, sheet, column, row)
        return rule if rule is not None and rule["enforced"] else None

    def check_result(
        self, sheet: str, address: str, formula: str, result: Any, diagnostic: dict | None, read
    ) -> tuple[str, str] | None:
        """(code, message) when a formula entry's calculated result breaks its rule.

        Excel checks a formula's result once, when it is entered, and never on
        recalculation. A result this engine cannot calculate cannot be shown
        to satisfy the rule, so it is refused too.
        """
        rule = self.formula_rule(sheet, address)
        if rule is None:
            return None
        where = " ".join(rule["sqref"])
        if diagnostic is not None:
            return (
                "validation_failed",
                f"validation_failed: {sheet}!{address} formula ={formula.removeprefix('=')} is refused "
                f"by the data validation on {where}: its result could not be calculated to check it "
                f"({diagnostic.get('code', 'unknown')}: {diagnostic.get('message', '')})",
            )
        why = rule_violation(rule, result, read)
        if why is None:
            return None
        return (
            "validation_failed",
            f"validation_failed: {sheet}!{address} formula ={formula.removeprefix('=')} "
            f"(result {result!r}) is refused by the data validation on {where}: {why}",
        )

    def check_formula_results(self, engine, document: dict[str, Any], entries, read) -> tuple[str, str] | None:
        """Check formula entries by their results in ``document`` (edits already applied).

        ``entries`` is ``[(sheet, address, formula)]``. Only entries under an
        enforced rule are calculated, each bound as an output of a detached
        copy of the document so nothing else is evaluated.
        """
        import json

        targets = [(sheet, address, formula) for sheet, address, formula in entries
                   if self.formula_rule(sheet, address) is not None]
        if not targets:
            return None
        detached = json.loads(json.dumps(document))
        detached["outputs"] = {
            f"forge_validation_{index}": {"sheet": sheet, "address": address}
            for index, (sheet, address, _) in enumerate(targets)
        }
        detached["inputs"] = {}
        report = json.loads(engine.calculate(json.dumps(detached, allow_nan=False, ensure_ascii=False), 1))
        failures = {
            (str(item.get("sheet", "")).casefold(), str(item.get("address", "")).upper()): item
            for item in report.get("diagnostics", [])
        }
        whole = next((item for item in report.get("diagnostics", []) if not item.get("address")), None)
        for index, (sheet, address, formula) in enumerate(targets):
            name = f"forge_validation_{index}"
            diagnostic = failures.get((sheet.casefold(), address.replace("$", "").upper()))
            if name not in report.get("outputs", {}) and diagnostic is None:
                diagnostic = whole or {"code": "unknown", "message": "no result was produced"}
            refusal = self.check_result(
                sheet, address, formula, report.get("outputs", {}).get(name), diagnostic, read
            )
            if refusal is not None:
                return refusal
        return None

    def charted_sheets(self) -> set[str]:
        out: set[str] = set()
        for chart in self.charts:
            for series in chart["series"]:
                for formula in series["refs"].values():
                    out |= referenced_sheets(formula)
        return out
