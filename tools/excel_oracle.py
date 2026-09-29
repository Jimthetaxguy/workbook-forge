#!/usr/bin/env python3
"""Generate synthetic Excel cases or observe an explicit SDK export in Excel.

Observation mode creates its own workbook. Round-trip mode accepts an SDK-created
workbook only when the caller explicitly requests Excel automation; it copies the
input before editing. Proposed expectations and actual Excel observations remain
separate; an unavailable Excel is a blocked run, not parity.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "fixtures" / "excel-observation-cases.json"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT = "http://schemas.openxmlformats.org/package/2006/content-types"
ADDRESS = re.compile(r"[A-Z]{1,3}[1-9][0-9]{0,6}\Z")
EXPECTED_KINDS = {"independently_derived", "documented", "forge_profile"}
SAFE_FUNCTIONS = {"SUM", "IF", "ISBLANK"}
ROUNDTRIP_CLASSES = {"scenario", "volatile", "iteration", "array", "array_spill", "excel_quirk"}
REQUIRED_RED_FLAG_CLASSES = {"volatile", "iteration", "array", "array_spill", "excel_quirk"}
EXTERNAL_DATA_FUNCTIONS = {"WEBSERVICE", "FILTERXML", "RTD", "STOCKHISTORY"}
ROUNDTRIP_RESULT_TYPES = {"number", "text", "boolean", "blank", "error", "array"}

ARRAY_FUNCTIONS = {"SEQUENCE", "FILTER", "SORT", "UNIQUE"}
UNSUPPORTED_ROUNDTRIP_CAPABILITIES = {
    "array_spill": {
        "capability": "worksheet_dynamic_array_spill_placement",
        "reason": (
            "Workbook Forge export currently refuses worksheet array spill caches. "
            "A scalar reduction such as SUM(SEQUENCE(...)) does not verify placement "
            "into the declared spill cells."
        ),
    },
}


def _xml(root: ET.Element) -> bytes:
    namespace = root.tag[1:].split("}", 1)[0]
    ET.register_namespace("", namespace)
    ET.register_namespace("r", REL)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _q(name: str) -> str:
    return f"{{{MAIN}}}{name}"


def _address_key(address: str) -> tuple[int, int]:
    if not isinstance(address, str) or not ADDRESS.fullmatch(address):
        raise ValueError(f"invalid synthetic cell address: {address!r}")
    letters = address.rstrip("0123456789")
    column = 0
    for char in letters:
        column = column * 26 + ord(char) - ord("A") + 1
    row = int(address[len(letters):])
    if column > 16_384 or row > 1_048_576:
        raise ValueError("cell address exceeds Excel limits")
    return row, column


def _column_name(column: int) -> str:
    if column < 1 or column > 16_384:
        raise ValueError("column is outside Excel limits")
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _spill_rectangle(anchor: str, shape: list[int]) -> list[str]:
    row, column = _address_key(anchor)
    rows, columns = shape
    if row + rows - 1 > 1_048_576 or column + columns - 1 > 16_384:
        raise ValueError("declared spill range exceeds Excel worksheet limits")
    return [
        f"{_column_name(col)}{row_index}"
        for row_index in range(row, row + rows)
        for col in range(column, column + columns)
    ]


def load_cases(path: Path = DEFAULT_CASES) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or data.get("date_system") != "1900":
        raise ValueError("only observation schema 1 with the 1900 date system is supported")
    sheets = data.get("sheets")
    if not isinstance(sheets, dict) or not 1 <= len(sheets) <= 8:
        raise ValueError("cases need one to eight synthetic worksheets")
    folded: set[str] = set()
    for name, cells in sheets.items():
        if (not name or len(name) > 31 or re.search(r"[\\/*?:\[\]\x00-\x1f]", name)
                or name.startswith("'") or name.endswith("'") or name.casefold() in folded):
            raise ValueError("invalid or duplicate synthetic worksheet name")
        folded.add(name.casefold())
        if not isinstance(cells, dict) or len(cells) > 1_000:
            raise ValueError("synthetic worksheet exceeds the case limit")
        for address, cell in cells.items():
            _address_key(address)
            if not isinstance(cell, dict) or set(cell) not in ({"value"}, {"formula"}):
                raise ValueError("cell must contain exactly value or formula")
            if "formula" in cell:
                formula = cell["formula"]
                if not isinstance(formula, str) or not formula.startswith("="):
                    raise ValueError("synthetic formula must start with =")
                # The case corpus is local authored input, but prohibit links,
                # DDE and known outbound functions before handing it to Excel.
                if len(formula) > 8192 or any(char in formula for char in "[]|\r\n"):
                    raise ValueError("external or oversized formulas are not observation cases")
                functions = {name.upper() for name in re.findall(r"\b([A-Za-z_][A-Za-z0-9_.]*)\s*\(", formula)}
                if not functions <= SAFE_FUNCTIONS:
                    raise ValueError("external-data or unreviewed functions are not observation cases")
            else:
                value = cell["value"]
                if value is not None and not isinstance(value, (bool, str, int, float)):
                    raise ValueError("unsupported synthetic input")
                if isinstance(value, (int, float)) and not math.isfinite(value):
                    raise ValueError("synthetic numbers must be finite")
    seen: set[str] = set()
    for case in data.get("cases", []):
        if case["id"] in seen:
            raise ValueError("observation case IDs must be unique")
        seen.add(case["id"])
        if case["sheet"] not in sheets:
            raise ValueError("unknown case worksheet")
        _address_key(case["cell"])
        if case["expectation_basis"]["kind"] not in EXPECTED_KINDS:
            raise ValueError("expectations must identify their independent or Forge basis")
        if case["expectation_basis"]["kind"] == "documented" and not case["expectation_basis"].get("source"):
            raise ValueError("documented expectations require a source")
        expected = case["expected"]
        if expected.get("type") not in {"number", "text", "boolean", "blank", "error"}:
            raise ValueError("unsupported expected type")
        if expected["type"] == "number":
            if isinstance(expected.get("value"), bool) or not math.isfinite(expected["value"]):
                raise ValueError("numeric expectation must be finite")
            for key in ("absolute_tolerance", "relative_tolerance"):
                if not math.isfinite(case.get(key, 0)) or case.get(key, 0) < 0:
                    raise ValueError("tolerance must be finite and nonnegative")
    if not seen:
        raise ValueError("observation corpus is empty")
    for copy in data.get("copies", []):
        if copy["sheet"] not in sheets or "formula" not in sheets[copy["sheet"]].get(copy["source"], {}):
            raise ValueError("copy source must be a synthetic formula")
        _address_key(copy["source"])
        _address_key(copy["destination"])
        if copy["destination"] in sheets[copy["sheet"]]:
            raise ValueError("copy destination must be empty")
    return data


def write_synthetic_workbook(path: Path, data: dict[str, Any]) -> None:
    """Write minimal OPC with no caches, links, macros, or user document data."""
    types = ET.Element(f"{{{CONTENT}}}Types")
    for extension, content in (("rels", "application/vnd.openxmlformats-package.relationships+xml"), ("xml", "application/xml")):
        ET.SubElement(types, f"{{{CONTENT}}}Default", Extension=extension, ContentType=content)
    ET.SubElement(types, f"{{{CONTENT}}}Override", PartName="/xl/workbook.xml", ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml")
    relationships = ET.Element(f"{{{PACKAGE_REL}}}Relationships")
    ET.SubElement(relationships, f"{{{PACKAGE_REL}}}Relationship", Id="rId1", Type=f"{REL}/officeDocument", Target="xl/workbook.xml")
    workbook = ET.Element(_q("workbook"))
    ET.SubElement(workbook, _q("workbookPr"), date1904="0")
    sheets = ET.SubElement(workbook, _q("sheets"))
    sheet_rels = ET.Element(f"{{{PACKAGE_REL}}}Relationships")
    parts: dict[str, bytes] = {}
    for index, (name, cells) in enumerate(data["sheets"].items(), 1):
        filename = f"worksheets/sheet{index}.xml"
        ET.SubElement(types, f"{{{CONTENT}}}Override", PartName=f"/xl/{filename}", ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")
        ET.SubElement(sheets, _q("sheet"), name=name, sheetId=str(index), attrib={f"{{{REL}}}id": f"rId{index}"})
        ET.SubElement(sheet_rels, f"{{{PACKAGE_REL}}}Relationship", Id=f"rId{index}", Type=f"{REL}/worksheet", Target=filename)
        sheet = ET.Element(_q("worksheet"))
        sheet_data = ET.SubElement(sheet, _q("sheetData"))
        rows: dict[int, ET.Element] = {}
        for address in sorted(cells, key=_address_key):
            spec = cells[address]
            row_number, _ = _address_key(address)
            if row_number not in rows:
                rows[row_number] = ET.SubElement(sheet_data, _q("row"), r=str(row_number))
            cell = ET.SubElement(rows[row_number], _q("c"), r=address)
            if "formula" in spec:
                ET.SubElement(cell, _q("f")).text = spec["formula"][1:]
                continue
            value = spec["value"]
            if isinstance(value, bool):
                cell.set("t", "b")
                ET.SubElement(cell, _q("v")).text = "1" if value else "0"
            elif isinstance(value, str):
                cell.set("t", "inlineStr")
                inline = ET.SubElement(cell, _q("is"))
                ET.SubElement(inline, _q("t")).text = value
            elif value is not None:
                ET.SubElement(cell, _q("v")).text = repr(value)
        parts[f"xl/{filename}"] = _xml(sheet)
    ET.SubElement(workbook, _q("calcPr"), fullCalcOnLoad="1", forceFullCalc="1", fullPrecision="1")
    parts.update({"[Content_Types].xml": _xml(types), "_rels/.rels": _xml(relationships), "xl/workbook.xml": _xml(workbook), "xl/_rels/workbook.xml.rels": _xml(sheet_rels)})
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, contents in parts.items():
            archive.writestr(name, contents)


def _as_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_applescript(data: dict[str, Any]) -> str:
    """Use explicit workbook references, with no error-handler close fallback."""
    operations = []
    for copy in data.get("copies", []):
        sheet = _as_string(copy["sheet"])
        source, target = _as_string(copy["source"]), _as_string(copy["destination"])
        operations.append(f"copy range (range {source} of worksheet {sheet} of targetBook) destination (range {target} of worksheet {sheet} of targetBook)")
    # Two scoped passes cover this small acyclic cross-sheet observation corpus.
    # This is not a general workbook dependency scheduler.
    for _ in range(2):
        for sheet in data["sheets"]:
            operations.append(f"calculate (used range of worksheet {_as_string(sheet)} of targetBook)")
    return '''on canonicalPath(pathText)
    if pathText starts with "/" then
        return POSIX path of ((POSIX file pathText) as alias)
    end if
    return POSIX path of (pathText as alias)
end canonicalPath

on run argv
    set workbookPath to item 1 of argv
    set workbookName to item 2 of argv
    set workbookFile to (POSIX file workbookPath) as alias
    with timeout of 45 seconds
        tell application "Microsoft Excel"
            set modeBefore to calculation as text
            log ("excel_version=" & (version as text))
            log ("calculation_version=" & (calculation version as text))
            log ("calculation_mode_before=" & modeBefore)
            if exists workbook workbookName then error "Synthetic workbook name is already open"
            log "stage:open"
            open workbookFile
            log "stage:identity"
            if not (exists workbook workbookName) then error "Synthetic workbook did not open"
            set targetBook to workbook workbookName
            if (name of targetBook) is not workbookName then error "Synthetic workbook identity mismatch"
            set openedName to full name of targetBook
            set openedPath to my canonicalPath(openedName)
            if openedPath is not workbookPath then error "Synthetic workbook path mismatch"
            ''' + "\n            ".join(operations) + '''
            log "stage:metadata"
            set metadata to "excel_version=" & (version as text) & linefeed
            set metadata to metadata & "calculation_version=" & (calculation version as text) & linefeed
            set metadata to metadata & "calculation_mode_before=" & modeBefore & linefeed
            set metadata to metadata & "calculation_mode_during=" & (calculation as text) & linefeed
            set metadata to metadata & "iteration=" & (iteration as text) & linefeed
            set metadata to metadata & "max_iterations=" & (max iterations as text) & linefeed
            set metadata to metadata & "max_change=" & (max change as text) & linefeed
            set metadata to metadata & "date_1904=" & (date 1904 of targetBook as text) & linefeed
            set metadata to metadata & "precision_as_displayed=" & (precision as displayed of targetBook as text) & linefeed
            log "stage:save"
            save targetBook
            log "stage:close"
            close targetBook saving no
            set metadata to metadata & "calculation_mode_after=" & (calculation as text) & linefeed
            return metadata & "owned_workbook_closed=true"
        end tell
    end timeout
end run
'''


def normalize_cell(cell: ET.Element | None, shared_strings: list[str]) -> dict[str, Any]:
    if cell is None:
        return {"type": "blank"}
    kind = cell.get("t", "n")
    value = cell.find(_q("v"))
    raw = value.text or "" if value is not None else ""
    if kind == "e":
        return {"type": "error", "value": raw}
    if kind == "b":
        if raw not in {"0", "1"}:
            raise ValueError("Excel saved an invalid Boolean cache")
        return {"type": "boolean", "value": raw == "1"}
    if kind in {"str", "inlineStr", "s"}:
        if kind == "s":
            raw = shared_strings[int(raw)]
        elif kind == "inlineStr":
            inline = cell.find(_q("is"))
            raw = "" if inline is None else "".join(node.text or "" for node in inline.iter(_q("t")))
        return {"type": "text", "value": raw}
    if not raw:
        if cell.find(_q("f")) is not None:
            raise ValueError("formula lacks an observed Excel cache")
        return {"type": "blank"}
    number = float(raw)
    if not math.isfinite(number):
        raise ValueError("Excel saved a nonfinite numeric cache")
    return {"type": "number", "value": number}


def matches_expectation(observed: dict[str, Any], case: dict[str, Any]) -> bool:
    expected = case["expected"]
    if observed.get("type") != expected["type"]:
        return False
    if expected["type"] == "number":
        return math.isclose(observed["value"], expected["value"], rel_tol=case.get("relative_tolerance", 0), abs_tol=case.get("absolute_tolerance", 0))
    return observed == expected


def observe_saved_workbook(path: Path, data: dict[str, Any]) -> list[dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.get("Id"): rel.get("Target") for rel in rels}
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.text or "" for node in item.iter(_q("t"))) for item in strings]
        by_sheet = {}
        for sheet in workbook.find(_q("sheets")):
            target = targets[sheet.get(f"{{{REL}}}id")]
            part = target.lstrip("/") if target.startswith("/") else "xl/" + target
            root = ET.fromstring(archive.read(part))
            by_sheet[sheet.get("name")] = {cell.get("r"): cell for cell in root.iter(_q("c"))}
        results = []
        copies = {(item["sheet"], item["destination"]): item for item in data.get("copies", [])}
        for case in data["cases"]:
            cell = by_sheet[case["sheet"]].get(case["cell"])
            observed = normalize_cell(cell, shared)
            formula = None if cell is None else cell.findtext(_q("f"))
            comparison = matches_expectation(observed, case)
            copy = copies.get((case["sheet"], case["cell"]))
            formula_matches = None
            if copy is not None:
                formula_matches = formula is not None and "=" + formula == copy["expected_formula"]
                comparison = comparison and formula_matches
            result = {"id": case["id"], "sheet": case["sheet"], "cell": case["cell"], "evidence_kind": "excel_observed", "observed": observed, "observed_formula": formula, "expected": case["expected"], "expectation_basis": case["expectation_basis"], "matches_expectation": comparison}
            if formula_matches is not None:
                result.update(expected_formula=copy["expected_formula"], copied_formula_matches=formula_matches)
            results.append(result)
        return results


def parse_metadata(output: str) -> dict[str, str]:
    metadata = dict(line.split("=", 1) for line in output.strip().splitlines() if "=" in line)
    for required in ("excel_version", "calculation_version", "calculation_mode_before", "calculation_mode_during", "calculation_mode_after", "iteration", "max_iterations", "max_change", "date_1904", "precision_as_displayed", "owned_workbook_closed"):
        if not metadata.get(required):
            raise ValueError(f"Excel metadata is missing {required}")
    if metadata["owned_workbook_closed"] != "true":
        raise ValueError("Excel did not confirm closing the owned workbook")
    return metadata


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_observations(data: dict[str, Any], output_root: Path, *, run_excel: bool = False, timeout: float = 60) -> tuple[Path, dict[str, Any]]:
    """Create a unique run; only explicit opt-in sends Apple events to Excel."""
    output_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="workbook-forge-excel-", dir=output_root)).resolve()
    path = directory / (directory.name + ".xlsx")
    write_synthetic_workbook(path, data)
    script = directory / "observe.applescript"
    script.write_text(build_applescript(data), encoding="utf-8")
    receipt: dict[str, Any] = {"schema_version": 1, "recorded_at_utc": datetime.now(timezone.utc).isoformat(), "status": "generated", "platform": platform.system(), "case_count": len(data["cases"]), "cases_sha256": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(), "generated_workbook_sha256": _hash(path), "workbook": path.name, "date_system_requested": data["date_system"], "observations": [], "safety": {"synthetic_only": True, "global_settings_written": False, "calculation_scope": "owned worksheet used ranges", "user_workbooks_opened_or_closed": False}}
    if run_excel:
        if platform.system() != "Darwin":
            receipt.update(status="blocked", reason="Excel automation requires macOS and Microsoft Excel Desktop.")
        else:
            try:
                completed = subprocess.run(["/usr/bin/osascript", str(script), str(path), path.name], capture_output=True, text=True, timeout=timeout, check=False)
                if completed.returncode:
                    # Preserve a public-safe reason, never AppleScript's potentially
                    # private process diagnostics or a list of open workbook names.
                    error = completed.stderr
                    known_errors = [message for message in ("Synthetic workbook name is already open", "Synthetic workbook did not open", "Synthetic workbook identity mismatch", "Synthetic workbook path mismatch") if message in error]
                    if known_errors:
                        receipt["diagnostic"] = known_errors[0]
                    stages = re.findall(r"stage:(\w+)", error)
                    receipt["last_stage"] = stages[-1] if stages else "metadata_before_open"
                    code = re.search(r"\((-?\d+)\)\s*$", error)
                    denied = "-1743" in error or "not authorized" in error.lower()
                    timed_out = "-1712" in error
                    partial = {key: value for key, value in (line.split("=", 1) for line in error.splitlines() if "=" in line) if key in {"excel_version", "calculation_version", "calculation_mode_before"}}
                    if partial:
                        receipt["excel_partial"] = partial
                    reason = "Excel automation access denied" if denied else "Excel Apple event timed out" if timed_out else "Excel automation command failed"
                    receipt.update(status="blocked" if denied or timed_out else "failed", reason=reason, automation_error_code=code.group(1) if code else None, owned_workbook_may_remain_open=True)
                else:
                    receipt["excel"] = parse_metadata(completed.stdout)
                    if receipt["excel"]["date_1904"] != "false" or receipt["excel"]["precision_as_displayed"] != "false":
                        raise ValueError("Excel settings differ from the observation profile")
                    receipt["observations"] = observe_saved_workbook(path, data)
                    receipt["saved_workbook_sha256"] = _hash(path)
                    receipt["status"] = "observed" if all(item["matches_expectation"] for item in receipt["observations"]) else "mismatch"
            except subprocess.TimeoutExpired:
                receipt.update(status="blocked", reason="Excel automation timed out; no cleanup commands were sent", owned_workbook_may_remain_open=True)
            except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
                receipt.update(status="failed", reason=type(exc).__name__ + " while collecting Excel observations", owned_workbook_may_remain_open=receipt.get("excel", {}).get("owned_workbook_closed") != "true")
    receipt_path = directory / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return receipt_path, receipt


def run_live_scenario(output_root: Path, *, timeout: float = 30) -> tuple[Path, dict[str, Any]]:
    """Observe the actual toolkit scenario in an Excel-created, unsaved workbook.

    This bypasses file I/O only. It cannot verify exported styles, validation,
    preservation, or XLSX compatibility. Values are logged before closing so an
    Apple-event failure cannot erase already observed formula evidence.
    """
    from workbook_forge.toolkit import operating_scenario

    document = operating_scenario().to_dict()
    output_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="workbook-forge-excel-live-", dir=output_root)).resolve()
    marker = directory.name
    statements = []
    for index, sheet in enumerate(document["sheets"]):
        name = _as_string(sheet["name"])
        if index == 0:
            statements.append(f"set name of worksheet 1 of ownedBook to {name}")
        else:
            statements.extend(["set ownedSheet to make new worksheet at end of ownedBook", f"set name of ownedSheet to {name}"])
    for sheet in document["sheets"]:
        for address, cell in sorted(sheet["cells"].items(), key=lambda item: _address_key(item[0])):
            target = f"range {_as_string(address)} of worksheet {_as_string(sheet['name'])} of ownedBook"
            if cell.get("formula") is not None:
                statements.append(f"set formula of {target} to {_as_string(cell['formula'])}")
            elif cell.get("value") is not None:
                value = cell["value"]
                literal = _as_string(value) if isinstance(value, str) else str(value).lower()
                statements.append(f"set value of {target} to {literal}")
    statements.append(f"set value of range \"Z100\" of worksheet 1 of ownedBook to {_as_string(marker)}")
    expected = {}
    for phase, price in (("baseline", 20), ("edited", 25)):
        statements.append(f"set value of range \"B1\" of worksheet \"Assumptions\" of ownedBook to {price}")
        for _ in range(2):
            for sheet in document["sheets"]:
                statements.append(f"calculate (used range of worksheet {_as_string(sheet['name'])} of ownedBook)")
        for address, label, value in (("F2", "total_revenue", 370 * price), ("F3", "total_profit", 370 * (price - 8) - 3000), ("F4", "break_even_units", 1000 / (price - 8))):
            identifier = f"{phase}.{label}"
            expected[identifier] = {"type": "number", "value": value}
            statements.extend([f"set cellValue to value of range \"{address}\" of worksheet \"Forecast\" of ownedBook", f"log (\"OBSERVED={identifier}|\" & (class of cellValue as text) & \"|\" & (cellValue as text))"])
    script = '''on run argv
    with timeout of 20 seconds
        tell application "Microsoft Excel"
            log ("excel_version=" & (version as text))
            log ("calculation_version=" & (calculation version as text))
            log ("calculation_mode=" & (calculation as text))
            log ("iteration=" & (iteration as text))
            log ("max_iterations=" & (max iterations as text))
            log ("max_change=" & (max change as text))
            set ownedBook to make new workbook
            log ("date_1904=" & (date 1904 of ownedBook as text))
            log ("precision_as_displayed=" & (precision as displayed of ownedBook as text))
            log "stage:populate"
            ''' + "\n            ".join(statements) + f'''
            if (value of range "Z100" of worksheet 1 of ownedBook) is not {_as_string(marker)} then error "Synthetic marker mismatch"
            close ownedBook saving no
            return "owned_workbook_closed=true"
        end tell
    end timeout
end run
'''
    script_path = directory / "observe-live.applescript"
    script_path.write_text(script, encoding="utf-8")
    receipt: dict[str, Any] = {"schema_version": 1, "recorded_at_utc": datetime.now(timezone.utc).isoformat(), "status": "blocked", "scope": "Numeric toolkit scenario observations in an Excel-created workbook, including a price edit; no file roundtrip, style or validation observation.", "model_sha256": hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest(), "script_sha256": _hash(script_path), "expected_count": len(expected), "save_attempted": False, "observations": [], "owned_workbook_closed": False}
    raw, stdout = "", ""
    if platform.system() != "Darwin":
        receipt["reason"] = "Excel automation requires macOS and Microsoft Excel Desktop"
    else:
        try:
            result = subprocess.run(["/usr/bin/osascript", str(script_path)], capture_output=True, text=True, timeout=timeout, check=False)
            raw, stdout = result.stderr, result.stdout
            receipt["automation_returncode"] = result.returncode
            if result.returncode:
                code = re.search(r"\((-?\d+)\)\s*$", raw)
                receipt["automation_error_code"] = code.group(1) if code else None
                receipt["reason"] = "Excel live-scenario Apple event failed"
                diagnostic = re.search(r"execution error: ([^\n]+)", raw)
                if diagnostic:
                    receipt["automation_diagnostic"] = diagnostic.group(1)
        except subprocess.TimeoutExpired as error:
            raw = error.stderr or ""
            raw = raw.decode() if isinstance(raw, bytes) else raw
            receipt["reason"] = "Excel automation timed out; observations flushed before failure are retained"
        except OSError:
            receipt["reason"] = "Unable to start Excel automation"
    receipt["excel"] = {key: value for key, value in (line.split("=", 1) for line in raw.splitlines() if "=" in line) if key in {"excel_version", "calculation_version", "calculation_mode", "iteration", "max_iterations", "max_change", "date_1904", "precision_as_displayed"}}
    for identifier, value_class, raw_value in re.findall(r"^OBSERVED=([a-z_.]+)\|([^|]+)\|([^\n]+)$", raw, re.M):
        if identifier not in expected:
            continue
        if value_class not in {"real", "integer"}:
            observed = {"type": "unsupported_automation_value", "class": value_class}
        else:
            try:
                number = float(raw_value)
            except ValueError:
                continue
            if not math.isfinite(number):
                continue
            observed = {"type": "number", "value": number}
        case = {"expected": expected[identifier], "absolute_tolerance": 1e-9}
        receipt["observations"].append({"id": identifier, "evidence_kind": "excel_live_observed", "observed": observed, "expected": expected[identifier], "expectation_basis": {"kind": "independently_derived", "detail": "370 units; revenue = units * price; profit = units * (price - 8) - 3000; break-even units = 1000 / (price - 8)."}, "absolute_tolerance": 1e-9, "matches_expectation": matches_expectation(observed, case)})
    receipt["owned_workbook_closed"] = "owned_workbook_closed=true" in stdout
    if len(receipt["observations"]) == len(expected):
        receipt["status"] = "observed_unsaved" if all(item["matches_expectation"] for item in receipt["observations"]) else "mismatch"
    elif receipt["observations"]:
        receipt["status"] = "observed_partial"
    receipt_path = directory / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt_path, receipt


def validate_roundtrip_contract(data: dict[str, Any]) -> dict[str, Any]:
    """Validate the small, versioned contract consumed by the export harness."""
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("round-trip contract must use schema_version 1")
    if not isinstance(data.get("id"), str) or not data["id"].strip():
        raise ValueError("round-trip contract needs a nonempty id")
    if "model_version" in data and (isinstance(data["model_version"], bool) or not isinstance(data["model_version"], int) or data["model_version"] < 1):
        raise ValueError("model_version must be a positive integer")
    checks = data.get("checks")
    if not isinstance(checks, list) or not checks or len(checks) > 1_000:
        raise ValueError("round-trip contract needs one to 1000 cell checks")
    check_ids: set[str] = set()
    checks_by_id: dict[str, dict[str, Any]] = {}
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("each round-trip check must be an object")
        identifier = check.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", identifier):
            raise ValueError("round-trip check IDs must be short and stable")
        if identifier in check_ids:
            raise ValueError("round-trip check IDs must be unique")
        check_ids.add(identifier)
        checks_by_id[identifier] = check
        if check.get("class") not in ROUNDTRIP_CLASSES:
            raise ValueError(f"unsupported round-trip fixture class: {check.get('class')!r}")
        if not isinstance(check.get("sheet"), str) or not check["sheet"]:
            raise ValueError(f"check {identifier} needs a worksheet name")
        if len(check["sheet"]) > 31 or re.search(r"[\\/*?:\[\]\x00-\x1f]", check["sheet"]):
            raise ValueError(f"check {identifier} has an invalid worksheet name")
        _address_key(check.get("address"))
        formula = check.get("expected_formula")
        if not isinstance(formula, str) or not formula.startswith("=") or formula.startswith("==") or len(formula) > 8192:
            raise ValueError(f"check {identifier} needs an expected Excel formula")
        expected = check.get("expected_result")
        if not isinstance(expected, dict) or expected.get("type") not in ROUNDTRIP_RESULT_TYPES:
            raise ValueError(f"check {identifier} needs a typed expected_result")
        if expected["type"] == "number":
            if "predicate" in expected:
                predicate = expected["predicate"]
                if not isinstance(predicate, dict) or not predicate:
                    raise ValueError(f"check {identifier} has an invalid numeric predicate")
                for bound in ("minimum", "maximum"):
                    if bound in predicate and (isinstance(predicate[bound], bool) or not isinstance(predicate[bound], (int, float)) or not math.isfinite(predicate[bound])):
                        raise ValueError(f"check {identifier} has a non-finite predicate bound")
                if "minimum" not in predicate and "maximum" not in predicate:
                    raise ValueError(f"check {identifier} numeric predicate needs a bound")
                for inclusion in ("minimum_inclusive", "maximum_inclusive"):
                    if inclusion in predicate and not isinstance(predicate[inclusion], bool):
                        raise ValueError(f"check {identifier} predicate flags must be Boolean")
                if "minimum" in predicate and "maximum" in predicate and predicate["minimum"] > predicate["maximum"]:
                    raise ValueError(f"check {identifier} predicate minimum exceeds maximum")
            else:
                value = expected.get("value")
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"check {identifier} numeric expectation must be finite")
                tolerance = expected.get("tolerance")
                if not isinstance(tolerance, dict) or set(tolerance) != {"absolute", "relative"}:
                    raise ValueError(f"check {identifier} numeric expectation needs explicit absolute and relative tolerances")
                if any(isinstance(tolerance[key], bool) or not isinstance(tolerance[key], (int, float)) or not math.isfinite(tolerance[key]) or tolerance[key] < 0 for key in tolerance):
                    raise ValueError(f"check {identifier} tolerances must be finite and nonnegative")
        elif expected["type"] == "array":
            if check["class"] != "array_spill":
                raise ValueError(f"check {identifier} may use an array result only for class array_spill")
            shape = expected.get("shape")
            if (not isinstance(shape, list) or len(shape) != 2
                    or any(isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 1 for dimension in shape)
                    or shape[0] * shape[1] > 10_000):
                raise ValueError(f"array spill check {identifier} needs a positive, bounded [rows, columns] shape")
            expected_cells = _spill_rectangle(check["address"], shape)
            spill_cells = check.get("spill_cells")
            if spill_cells != expected_cells:
                raise ValueError(f"array spill check {identifier} must enumerate its complete rectangular spill_cells")
        elif "predicate" in expected:
            raise ValueError(f"check {identifier} predicates currently apply only to numbers")
        elif expected["type"] != "blank":
            if "value" not in expected:
                raise ValueError(f"check {identifier} needs an expected value")
            wanted = expected["value"]
            valid = (
                isinstance(wanted, str) if expected["type"] in {"text", "error"}
                else isinstance(wanted, bool) if expected["type"] == "boolean"
                else False
            )
            if not valid:
                raise ValueError(f"check {identifier} expected value does not match its declared type")

        formula_functions = {
            name.upper().rsplit(".", 1)[-1]
            for name in re.findall(r"\b([A-Za-z_][A-Za-z0-9_.]*)\s*\(", formula)
        }
        if check["class"] == "volatile" and not formula_functions.intersection({"RAND", "RANDBETWEEN", "NOW", "TODAY"}):
            raise ValueError(f"volatile check {identifier} must exercise a volatile function")
        if check["class"] in {"array", "array_spill"} and not formula_functions.intersection(ARRAY_FUNCTIONS):
            raise ValueError(f"array check {identifier} must exercise a supported dynamic-array function")
        if check["class"] == "array_spill" and expected.get("type") != "array":
            raise ValueError(f"array spill check {identifier} needs an explicit shape result")
        if check["class"] != "array_spill" and "spill_cells" in check:
            raise ValueError(f"only array_spill checks may declare spill_cells ({identifier})")
        if check["class"] == "iteration":
            address = check["address"].replace("$", "")
            column = address.rstrip("0123456789")
            row = address[len(column):]
            token = re.compile(rf"(?<![A-Z0-9_])\$?{column}\$?{row}(?![A-Z0-9_])", re.I)
            # A direct self-reference makes the iteration fixture auditable; the
            # harness never invents a circular formula on the user's behalf.
            if not token.search(formula):
                raise ValueError(f"iteration check {identifier} must refer to its own cell")

    required_classes = data.get("required_fixture_classes", sorted(REQUIRED_RED_FLAG_CLASSES))
    if not isinstance(required_classes, list) or any(not isinstance(item, str) or item not in ROUNDTRIP_CLASSES for item in required_classes):
        raise ValueError("required_fixture_classes contains an unsupported class")
    if len(required_classes) != len(set(required_classes)):
        raise ValueError("required_fixture_classes must not repeat classes")
    present_classes = {check["class"] for check in checks}
    missing = set(required_classes) - present_classes
    if missing:
        raise ValueError("round-trip contract is missing required fixture classes: " + ", ".join(sorted(missing)))

    edits = data.get("edits", [])
    if not isinstance(edits, list) or len(edits) > 100:
        raise ValueError("round-trip contract supports at most 100 declared input edits")
    edit_keys: set[tuple[str, str]] = set()
    for edit in edits:
        if not isinstance(edit, dict) or not isinstance(edit.get("sheet"), str):
            raise ValueError("each round-trip edit needs a worksheet and address")
        if len(edit["sheet"]) > 31 or re.search(r"[\\/*?:\[\]\x00-\x1f]", edit["sheet"]):
            raise ValueError("round-trip edit has an invalid worksheet name")
        _address_key(edit.get("address"))
        key = (edit["sheet"], edit["address"])
        if key in edit_keys:
            raise ValueError("round-trip edits must target distinct cells")
        edit_keys.add(key)
        value = edit.get("value")
        if value is not None and not isinstance(value, (str, bool, int, float)):
            raise ValueError("round-trip edits must be scalar values")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("round-trip numeric edits must be finite")

    calculation_settings = data.get("calculation_settings", {})
    if not isinstance(calculation_settings, dict):
        raise ValueError("calculation_settings must be an object")
    if any(check["class"] == "iteration" for check in checks):
        if calculation_settings.get("iteration") is not True:
            raise ValueError("iteration fixtures require calculation_settings.iteration=true")
        count = calculation_settings.get("max_iterations")
        change = calculation_settings.get("max_change")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError("iteration fixtures require a positive max_iterations value")
        if isinstance(change, bool) or not isinstance(change, (int, float)) or not math.isfinite(change) or change <= 0:
            raise ValueError("iteration fixtures require a positive finite max_change value")

    allowlist = data.get("allowlist", [])
    if not isinstance(allowlist, list) or len(allowlist) > 1_000:
        raise ValueError("allowlist must be a list of at most 1000 entries")
    allowlist_keys: set[tuple[str, str]] = set()
    for item in allowlist:
        if not isinstance(item, dict) or item.get("check_id") not in checks_by_id:
            raise ValueError("allowlist entry must name a known check")
        field = item.get("field")
        key = (item["check_id"], field)
        if key in allowlist_keys:
            raise ValueError("allowlist check and field pairs must be unique")
        allowlist_keys.add(key)
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("allowlist entries require a human-readable reason")
        check = checks_by_id[item["check_id"]]
        if field == "result" and item.get("policy") == "volatile_predicate":
            if check["class"] != "volatile" or "predicate" not in check["expected_result"]:
                raise ValueError("volatile result allowlist requires a volatile predicate check")
        elif field == "formula":
            accepted = item.get("accepted_values")
            if not isinstance(accepted, list) or not accepted or any(not isinstance(value, str) or not value.startswith("=") for value in accepted):
                raise ValueError("formula allowlists require exact accepted_values")
        else:
            raise ValueError("unsupported round-trip allowlist policy")
    for check in checks:
        if check["class"] == "volatile" and (check["id"], "result") not in allowlist_keys:
            raise ValueError(f"volatile check {check['id']} needs an explicit result allowlist entry")

    return data


def load_roundtrip_contract(path: Path) -> dict[str, Any]:
    return validate_roundtrip_contract(json.loads(path.read_text(encoding="utf-8")))


def roundtrip_cell_value(cell: dict[str, Any] | None) -> dict[str, Any]:
    """Convert an imported SDK cell to a stable typed receipt value."""
    if cell is None:
        return {"type": "blank"}
    value = cell.get("cached_value") if cell.get("formula") is not None else cell.get("value")
    if value is None:
        return {"type": "blank"}
    if isinstance(value, dict) and set(value) == {"error"}:
        return {"type": "error", "value": value["error"]}
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, str):
        return {"type": "text", "value": value}
    if isinstance(value, (int, float)) and math.isfinite(value):
        return {"type": "number", "value": float(value)}
    return {"type": "unsupported", "python_type": type(value).__name__}


def _formula_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value if value.startswith("=") else "=" + value


def _model_view(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    from workbook_forge.xlsx import import_xlsx

    model = import_xlsx(path, backend="python")
    document = model.to_dict()
    cells: dict[str, dict[str, Any]] = {}
    for sheet in document["sheets"]:
        for address, data in sheet["cells"].items():
            cells[f"{sheet['name']}!{address}"] = {
                "sheet": sheet["name"],
                "address": address,
                "formula": _formula_text(data.get("formula")),
                "result": roundtrip_cell_value(data),
                "blocked_reason": data.get("blocked_reason"),
            }
    return cells, document


def _validate_excel_safe_package(path: Path, cells: dict[str, dict[str, Any]]) -> tuple[dict[str, str], bool]:
    """Reject external data sources before the workbook is handed to Excel."""
    from workbook_forge.workbook import Workbook, _safe_xml

    with Workbook.open(path) as workbook:
        part_names = {name.casefold() for name in workbook._parts}
        if any("externallink" in name or "connections.xml" in name or "querytables" in name for name in part_names):
            raise ValueError("Excel round-trip refuses external links, connections, and query tables")
        for name, content in workbook._parts.items():
            if not name.endswith(".rels"):
                continue
            rels = _safe_xml(content, name)
            if any(item.attrib.get("TargetMode", "").casefold() == "external" for item in rels):
                raise ValueError("Excel round-trip refuses external package relationships")
        calculation = _safe_xml(workbook._parts[workbook._workbook_part], workbook._workbook_part).find(_q("calcPr"))
        properties = {} if calculation is None else dict(calculation.attrib)
        uses_1904 = workbook._uses_1904_date_system
    for item in cells.values():
        formula = item["formula"] or ""
        functions = {name.upper().rsplit(".", 1)[-1] for name in re.findall(r"\b([A-Za-z_][A-Za-z0-9_.]*)\s*\(", formula)}
        denied = functions.intersection(EXTERNAL_DATA_FUNCTIONS)
        if denied:
            raise ValueError("Excel round-trip refuses external-data formula functions: " + ", ".join(sorted(denied)))
    return properties, uses_1904


def _contract_check_map(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f"{check['sheet']}!{check['address']}": check for check in contract["checks"]}


def _formula_allowlist(contract: dict[str, Any], check_id: str) -> list[str]:
    accepted = []
    for entry in contract.get("allowlist", []):
        if entry["check_id"] == check_id and entry["field"] == "formula":
            accepted.extend(entry["accepted_values"])
    return accepted


def roundtrip_result_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    if actual.get("type") != expected["type"]:
        return False
    if expected["type"] == "array":
        return actual == {"type": "array", "shape": expected["shape"]}
    if expected["type"] == "number":
        value = actual.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return False
        if "predicate" in expected:
            bounds = expected["predicate"]
            if "minimum" in bounds:
                if value < bounds["minimum"] or (value == bounds["minimum"] and not bounds.get("minimum_inclusive", True)):
                    return False
            if "maximum" in bounds:
                if value > bounds["maximum"] or (value == bounds["maximum"] and not bounds.get("maximum_inclusive", True)):
                    return False
            return True
        tolerance = expected["tolerance"]
        return math.isclose(value, expected["value"], rel_tol=tolerance["relative"], abs_tol=tolerance["absolute"])
    if expected["type"] == "blank":
        return actual == {"type": "blank"}
    return actual == {"type": expected["type"], "value": expected["value"]}


def _typed_applescript_value(value: Any) -> str:
    if isinstance(value, str):
        return _as_string(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "missing value"
    if isinstance(value, (int, float)) and math.isfinite(value):
        return repr(value)
    raise ValueError("unsupported AppleScript cell edit value")


def build_roundtrip_applescript(contract: dict[str, Any], *, timeout: float = 180) -> str:
    """Build a script scoped to one copied workbook and one explicit contract."""
    validate_roundtrip_contract(contract)
    edits = []
    for edit in contract.get("edits", []):
        edits.append(
            f"set value of range {_as_string(edit['address'])} of worksheet {_as_string(edit['sheet'])} of targetBook to {_typed_applescript_value(edit['value'])}"
        )
    if contract.get("calculation_settings", {}).get("iteration"):
        settings = contract["calculation_settings"]
        preflight = f'''if iterationBefore is "missing value" then error "Excel iteration setting is not observable; harness will not change global settings"
            if maxIterationsBefore is "missing value" then error "Excel max_iterations setting is not observable; harness will not change global settings"
            if maxChangeBefore is "missing value" then error "Excel max_change setting is not observable; harness will not change global settings"
            if iteration is not true then error "Excel iteration must already be enabled; harness will not change global settings"
            if (max iterations) is not {settings["max_iterations"]} then error "Excel iteration settings do not match; harness will not change global settings"
            if (max change) is not {settings["max_change"]} then error "Excel iteration settings do not match; harness will not change global settings"
            '''
    else:
        preflight = ""
    return f'''on canonicalPath(pathText)
    if pathText starts with "/" then
        return POSIX path of ((POSIX file pathText) as alias)
    end if
    return POSIX path of (pathText as alias)
end canonicalPath

on run argv
    set workbookPath to item 1 of argv
    set workbookName to item 2 of argv
    set workbookFile to (POSIX file workbookPath) as alias
    with timeout of {max(1, math.ceil(timeout))} seconds
        tell application "Microsoft Excel"
            if (count of workbooks) is not 0 then error "Full rebuild requires Excel to have no other open workbooks"
            set modeBefore to calculation as text
            set iterationBefore to iteration as text
            set maxIterationsBefore to max iterations as text
            set maxChangeBefore to max change as text
            ''' + preflight + '''
            log "stage:open"
            open workbookFile
            log "stage:identity"
            if not (exists workbook workbookName) then error "SDK workbook did not open"
            set targetBook to workbook workbookName
            if (name of targetBook) is not workbookName then error "SDK workbook identity mismatch"
            set openedPath to my canonicalPath(full name of targetBook)
            if openedPath is not workbookPath then error "SDK workbook path mismatch"
            if (count of workbooks) is not 1 then error "Unexpected workbook opened during isolated round-trip"
            ''' + "\n            ".join(edits) + '''
            log "stage:full_rebuild"
            calculate full rebuild
            log "stage:save"
            save targetBook
            log "stage:close"
            close targetBook saving no
            set modeAfter to calculation as text
            set iterationAfter to iteration as text
            set maxIterationsAfter to max iterations as text
            set maxChangeAfter to max change as text
            set metadata to "engine=Microsoft Excel Desktop" & linefeed
            set metadata to metadata & "excel_version=" & (version as text) & linefeed
            set metadata to metadata & "calculation_version=" & (calculation version as text) & linefeed
            set metadata to metadata & "calculation_mode_before=" & modeBefore & linefeed
            set metadata to metadata & "calculation_mode_after=" & modeAfter & linefeed
            set metadata to metadata & "iteration_before=" & iterationBefore & linefeed
            set metadata to metadata & "iteration_after=" & iterationAfter & linefeed
            set metadata to metadata & "max_iterations_before=" & maxIterationsBefore & linefeed
            set metadata to metadata & "max_iterations_after=" & maxIterationsAfter & linefeed
            set metadata to metadata & "max_change_before=" & maxChangeBefore & linefeed
            set metadata to metadata & "max_change_after=" & maxChangeAfter & linefeed
            set metadata to metadata & "full_rebuild_invoked=true" & linefeed
            set metadata to metadata & "saved=true" & linefeed
            set metadata to metadata & "owned_workbook_closed=true"
            return metadata
        end tell
    end timeout
end run
'''


def _metadata_from_roundtrip(output: str) -> dict[str, str]:
    metadata = dict(line.split("=", 1) for line in output.strip().splitlines() if "=" in line)
    required = (
        "excel_version", "calculation_version", "calculation_mode_before", "calculation_mode_after",
        "iteration_before", "iteration_after", "max_iterations_before", "max_iterations_after",
        "max_change_before", "max_change_after", "full_rebuild_invoked", "saved", "owned_workbook_closed",
    )
    for key in required:
        if not metadata.get(key):
            raise ValueError(f"Excel round-trip metadata is missing {key}")
    if metadata["full_rebuild_invoked"] != "true" or metadata["saved"] != "true":
        raise ValueError("Excel did not confirm full recalculation and save")
    if metadata["owned_workbook_closed"] != "true":
        raise ValueError("Excel did not confirm closing the owned workbook")
    setting_names = ("iteration", "max_iterations", "max_change", "calculation_mode")
    unavailable = []
    for key in setting_names:
        before = metadata.get(f"{key}_before")
        after = metadata.get(f"{key}_after")
        if before == "missing value" or after == "missing value":
            unavailable.append(key)
            continue
        if before != after:
            raise ValueError(f"Excel global {key} setting changed during the run")
    metadata["global_settings_observable"] = "false" if unavailable else "true"
    metadata["global_settings_unchanged"] = "unverified" if unavailable else "true"
    metadata["global_settings_unavailable"] = ",".join(unavailable)
    return metadata


def _run_preflight(contract: dict[str, Any], cells: dict[str, dict[str, Any]], calc_properties: dict[str, str]) -> list[str]:
    failures = []
    checks = _contract_check_map(contract)
    for key, check in checks.items():
        cell = cells.get(key)
        expected = check["expected_formula"]
        accepted = [expected, *_formula_allowlist(contract, check["id"])]
        actual = None if cell is None else cell["formula"]
        if actual not in accepted:
            failures.append(f"{key}: exported formula does not match contract {check['id']}")
    for edit in contract.get("edits", []):
        key = f"{edit['sheet']}!{edit['address']}"
        cell = cells.get(key)
        if cell is None:
            failures.append(f"{key}: declared edit target is missing")
        elif cell["formula"] is not None:
            failures.append(f"{key}: declared edit target is a formula cell")
    if any(check["class"] == "iteration" for check in contract["checks"]):
        expected = contract["calculation_settings"]
        if calc_properties.get("iterate", "0").casefold() not in {"1", "true"}:
            failures.append("iteration fixture requires calcPr iterate=true in the exported workbook")
        if int(calc_properties.get("iterateCount", "100")) != expected["max_iterations"]:
            failures.append("iteration fixture max_iterations differs from exported calcPr")
        try:
            actual_change = float(calc_properties.get("iterateDelta", "0.001"))
        except ValueError:
            actual_change = math.nan
        if not math.isclose(actual_change, expected["max_change"], rel_tol=0, abs_tol=1e-12):
            failures.append("iteration fixture max_change differs from exported calcPr")
    return failures


def _roundtrip_capability_blockers(contract: dict[str, Any]) -> list[dict[str, Any]]:
    blockers = []
    for check in contract["checks"]:
        limitation = UNSUPPORTED_ROUNDTRIP_CAPABILITIES.get(check["class"])
        if limitation is None:
            continue
        blockers.append({
            "check_id": check["id"],
            "class": check["class"],
            "cell": f"{check['sheet']}!{check['address']}",
            "expected_formula": check["expected_formula"],
            "expected_result": check["expected_result"],
            "spill_cells": check.get("spill_cells", []),
            "status": "blocked",
            "blocker_kind": "unsupported_capability",
            "capability": limitation["capability"],
            "reason": limitation["reason"],
        })
    return blockers


def _fixture_coverage(contract: dict[str, Any]) -> list[dict[str, Any]]:
    required = contract.get("required_fixture_classes", sorted(REQUIRED_RED_FLAG_CLASSES))
    checks_by_class: dict[str, list[str]] = {}
    for check in contract["checks"]:
        checks_by_class.setdefault(check["class"], []).append(check["id"])
    return [
        {"class": class_name, "check_ids": checks_by_class.get(class_name, []), "status": "not_observed"}
        for class_name in required
    ]


def _compare_roundtrip(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]],
    contract: dict[str, Any], metadata: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    checks_by_key = _contract_check_map(contract)
    formula_diffs = []
    all_formula_keys = sorted(
        key for key in set(before) | set(after)
        if (before.get(key) or {}).get("formula") is not None or (after.get(key) or {}).get("formula") is not None
    )
    for key in all_formula_keys:
        prior = (before.get(key) or {}).get("formula")
        current = (after.get(key) or {}).get("formula")
        if prior == current:
            continue
        check = checks_by_key.get(key)
        accepted = [] if check is None else [check["expected_formula"], *_formula_allowlist(contract, check["id"])]
        allowed = current in accepted
        formula_diffs.append({"cell": key, "before": prior, "after": current, "allowlisted": allowed})

    outcomes = []
    mismatches = []
    for check in contract["checks"]:
        key = f"{check['sheet']}!{check['address']}"
        old = before.get(key)
        new = after.get(key)
        expected_formula = check["expected_formula"]
        accepted_formulas = [expected_formula, *_formula_allowlist(contract, check["id"])]
        before_formula = None if old is None else old["formula"]
        after_formula = None if new is None else new["formula"]
        before_matches = before_formula in accepted_formulas
        formula_matches = after_formula in accepted_formulas
        observed = {"type": "blank"} if new is None else new["result"]
        expected_result = check["expected_result"]
        result_matches = roundtrip_result_matches(observed, expected_result)
        settings_match = True
        if check["class"] == "iteration":
            required = contract["calculation_settings"]
            settings_match = (
                metadata.get("iteration_after", "").casefold() == "true"
                and int(metadata["max_iterations_after"]) == required["max_iterations"]
                and math.isclose(float(metadata["max_change_after"]), required["max_change"], rel_tol=0, abs_tol=1e-12)
            )
        local_mismatches = []
        if not before_matches:
            local_mismatches.append("exported formula differs from contract")
        if not formula_matches:
            local_mismatches.append("reimported formula differs from contract")
        if not result_matches:
            local_mismatches.append("reimported result differs from expected behavior")
        if not settings_match:
            local_mismatches.append("Excel iterative calculation settings differ from contract")
        allowlist = [entry for entry in contract.get("allowlist", []) if entry["check_id"] == check["id"]]
        outcome = {
            "id": check["id"], "class": check["class"], "cell": key,
            "before_recalc": {"formula": before_formula, "result": {"type": "blank"} if old is None else old["result"]},
            "after_recalc_and_reimport": {"formula": after_formula, "result": observed},
            "expected_formula": expected_formula, "accepted_formula_values": accepted_formulas,
            "expected_result": expected_result, "formula_matches": formula_matches,
            "result_matches": result_matches, "calculation_settings_match": settings_match,
            "allowlist_applied": allowlist, "matches": not local_mismatches,
            "mismatches": local_mismatches,
        }
        outcomes.append(outcome)
        mismatches.extend(f"{check['id']}: {message}" for message in local_mismatches)
    for item in formula_diffs:
        if not item["allowlisted"]:
            mismatches.append(f"{item['cell']}: formula changed outside the exact allowlist")
    return outcomes, formula_diffs, mismatches


def run_roundtrip(
    input_path: Path, contract: dict[str, Any], output_root: Path, *, run_excel: bool = False,
    timeout: float = 180,
) -> tuple[Path, dict[str, Any]]:
    """Recalculate an SDK workbook in Excel, save a scratch copy, and SDK-reimport it."""
    validate_roundtrip_contract(contract)
    output_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="workbook-forge-roundtrip-", dir=output_root)).resolve()
    source = input_path.expanduser().resolve()
    working_copy = directory / "roundtrip.xlsx"
    receipt: dict[str, Any] = {
        "schema_version": 1, "harness": "excel_export_roundtrip_v1",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(), "status": "failed",
        "contract_id": contract["id"], "contract_sha256": hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest(),
        "model_version": contract.get("model_version"), "engine_requested": "Microsoft Excel Desktop",
        "source_workbook": source.name, "observations": [], "formula_diffs": [],
        "fixture_coverage": _fixture_coverage(contract),
        "safety": {"source_overwritten": False, "global_settings_written": False, "external_sources_allowed": False},
    }
    try:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("round-trip automation timeout must be positive and finite")
        if source.suffix.casefold() != ".xlsx" or not source.is_file():
            raise ValueError("round-trip input must be an existing macro-free .xlsx file")
        source_hash = _hash(source)
        receipt["source_workbook_sha256"] = source_hash
        before, before_document = _model_view(source)
        calc_properties, uses_1904 = _validate_excel_safe_package(source, before)
        receipt["exported_model_sha256"] = hashlib.sha256(json.dumps(before_document, sort_keys=True, allow_nan=False).encode()).hexdigest()
        receipt["date_system"] = "1904" if uses_1904 else "1900"
        receipt["exported_calc_properties"] = calc_properties
        receipt["formula_count_before"] = sum(item["formula"] is not None for item in before.values())
        capability_blockers = _roundtrip_capability_blockers(contract)
        preflight = [] if capability_blockers else _run_preflight(contract, before, calc_properties)
        if capability_blockers:
            for item in receipt["fixture_coverage"]:
                if item["class"] in {blocker["class"] for blocker in capability_blockers}:
                    item["status"] = "blocked"
            receipt.update(
                status="blocked", stage="capability_preflight", blocker_kind="unsupported_capability",
                reason="A required workbook behavior cannot be tested by the current SDK export path",
                capability_blockers=capability_blockers,
                preflight_skipped=True,
            )
        elif preflight:
            receipt.update(status="mismatch", stage="preflight", mismatches=preflight)
        elif not run_excel:
            receipt.update(status="prepared", stage="preflight", reason="Excel was not invoked; no recalculation or compatibility claim is made")
        elif platform.system() != "Darwin":
            receipt.update(status="blocked", stage="automation", reason="Microsoft Excel Desktop automation requires macOS")
        else:
            shutil.copyfile(source, working_copy)
            script_path = directory / "roundtrip.applescript"
            script_path.write_text(build_roundtrip_applescript(contract, timeout=timeout), encoding="utf-8")
            receipt["working_copy"] = working_copy.name
            receipt["working_copy_sha256_before_excel"] = _hash(working_copy)
            receipt["script_sha256"] = _hash(script_path)
            receipt["stage"] = "automation"
            completed = subprocess.run(
                ["/usr/bin/osascript", str(script_path), str(working_copy), working_copy.name],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
            if completed.returncode:
                error = completed.stderr or ""
                code = re.search(r"\((-?\d+)\)\s*$", error)
                stage = re.findall(r"stage:(\w+)", error)
                blocked = any(token in error.casefold() for token in (
                    "not authorized", "-1743", "-1712", "-600", "no other open workbooks",
                    "iteration setting is not observable", "max_iterations setting is not observable",
                    "max_change setting is not observable", "iteration must already be enabled",
                    "iteration settings do not match",
                ))
                receipt.update(
                    status="blocked" if blocked else "failed", stage=stage[-1] if stage else "before_open",
                    reason="Excel automation was blocked by the environment" if blocked else "Excel automation command failed",
                    automation_error_code=code.group(1) if code else None,
                    owned_workbook_may_remain_open="stage:open" in error or "stage:identity" in error,
                )
                if "no other open workbooks" in error.casefold():
                    receipt["reason"] = "Full rebuild would affect all open workbooks; close them and retry"
                if "iteration must already be enabled" in error.casefold():
                    receipt["reason"] = "Excel iteration is disabled; harness will not change global settings"
                if "setting is not observable" in error.casefold():
                    receipt["reason"] = "Excel iteration settings are not observable; harness will not change global settings"
                if "iteration settings do not match" in error.casefold():
                    receipt["reason"] = "Excel iteration settings differ; harness will not change global settings"
                if "not authorized" in error.casefold() or "-1743" in error:
                    receipt["reason"] = "Excel Apple-event automation is not authorized"
                if "-1712" in error:
                    receipt["reason"] = "Excel Apple-event automation timed out"
            else:
                metadata = _metadata_from_roundtrip(completed.stdout)
                receipt["excel"] = metadata
                receipt["full_rebuild_invoked"] = True
                receipt["saved_workbook_sha256"] = _hash(working_copy)
                receipt["stage"] = "reimport"
                after, after_document = _model_view(working_copy)
                saved_calc_properties, saved_uses_1904 = _validate_excel_safe_package(working_copy, after)
                receipt["reimported_model_sha256"] = hashlib.sha256(json.dumps(after_document, sort_keys=True, allow_nan=False).encode()).hexdigest()
                receipt["saved_calc_properties"] = saved_calc_properties
                receipt["formula_count_after"] = sum(item["formula"] is not None for item in after.values())
                receipt["date_system_after"] = "1904" if saved_uses_1904 else "1900"
                outcomes, formula_diffs, mismatches = _compare_roundtrip(before, after, contract, metadata)
                receipt["observations"] = outcomes
                receipt["formula_diffs"] = formula_diffs
                if saved_uses_1904 != uses_1904:
                    mismatches.append("workbook date system changed during Excel round-trip")
                for edit in contract.get("edits", []):
                    key = f"{edit['sheet']}!{edit['address']}"
                    actual = (after.get(key) or {}).get("result", {"type": "blank"})
                    expected = {"type": "blank"} if edit["value"] is None else {
                        "type": "boolean" if isinstance(edit["value"], bool) else "number" if isinstance(edit["value"], (int, float)) else "text",
                        **({} if edit["value"] is None else {"value": edit["value"]}),
                    }
                    if actual != expected:
                        mismatches.append(f"{key}: edited input differs after save and reimport")
                outcome_by_id = {outcome["id"]: outcome for outcome in outcomes}
                for item in receipt["fixture_coverage"]:
                    item["status"] = "observed" if all(outcome_by_id[check_id]["matches"] for check_id in item["check_ids"]) else "mismatch"
                receipt.update(status="mismatch" if mismatches else "observed", stage="reimport", mismatches=mismatches)
        receipt["source_unchanged"] = source.exists() and _hash(source) == receipt.get("source_workbook_sha256")
    except subprocess.TimeoutExpired:
        receipt.update(status="blocked", stage="automation", reason="Excel automation timed out; the owned scratch workbook may remain open", owned_workbook_may_remain_open=True)
    except OSError as exc:
        if receipt.get("stage") == "automation":
            receipt.update(status="blocked", reason="Unable to start Excel automation: " + str(exc))
        else:
            receipt.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        receipt.update(status="failed", stage=receipt.get("stage", "preflight"), reason=f"{type(exc).__name__}: {exc}")
    receipt["receipt"] = "receipt.json"
    receipt_path = directory / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return receipt_path, receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-dir", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument("--run-excel", action="store_true", help="Explicitly authorize local Excel automation for a new synthetic workbook")
    parser.add_argument("--live-scenario", action="store_true", help="With --run-excel, observe the toolkit scenario in a new unsaved Excel workbook")
    parser.add_argument("--roundtrip", type=Path, help="Open an SDK-generated .xlsx copy in Excel, full-rebuild, save, and reimport it")
    parser.add_argument("--contract", type=Path, help="Version 1 JSON contract for --roundtrip cell edits and behavior checks")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be positive and finite")
    if args.roundtrip is not None:
        if not args.run_excel:
            parser.error("--roundtrip requires explicit --run-excel")
        if args.live_scenario:
            parser.error("--roundtrip cannot be combined with --live-scenario")
        if args.contract is None:
            parser.error("--roundtrip requires --contract")
        try:
            contract = load_roundtrip_contract(args.contract)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(f"invalid round-trip contract: {exc}")
        path, receipt = run_roundtrip(args.roundtrip, contract, args.output_dir, run_excel=True, timeout=args.timeout)
        print(json.dumps({"status": receipt["status"], "receipt": str(path), "checks": len(receipt.get("observations", [])), "model_version": receipt.get("model_version")}))
        return 0 if receipt["status"] == "observed" else 2
    if args.contract is not None:
        parser.error("--contract requires --roundtrip")
    if args.live_scenario:
        if not args.run_excel:
            parser.error("--live-scenario requires explicit --run-excel")
        path, receipt = run_live_scenario(args.output_dir, timeout=args.timeout)
        print(json.dumps({"status": receipt["status"], "receipt": str(path), "observations": len(receipt["observations"])}))
        return 0 if receipt["status"] == "observed_unsaved" and receipt["owned_workbook_closed"] else 2
    data = load_cases(args.cases)
    path, receipt = run_observations(data, args.output_dir, run_excel=args.run_excel, timeout=args.timeout)
    print(json.dumps({"status": receipt["status"], "receipt": str(path), "cases": receipt["case_count"]}))
    return 0 if receipt["status"] in {"generated", "observed"} else 2


if __name__ == "__main__":
    sys.exit(main())
