#!/usr/bin/env python3
"""Generate synthetic Excel cases; opt in to observing Excel Desktop on macOS.

This tool never accepts an existing workbook. Every run owns a fresh directory
and a newly generated macro-free package. Proposed expectations and actual Excel
observations remain separate; an unavailable Excel is a blocked run, not parity.
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-dir", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument("--run-excel", action="store_true", help="Explicitly authorize local Excel automation for a new synthetic workbook")
    parser.add_argument("--live-scenario", action="store_true", help="With --run-excel, observe the toolkit scenario in a new unsaved Excel workbook")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be positive and finite")
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
