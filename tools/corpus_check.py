"""Run every corpus workbook through every engine and write a matrix report.

For each file in fixtures/corpus/ and each engine the check runs open, inspect,
calculate, export and reimport. The py_toolkit and rust lanes also apply one
value edit and one absent-cell insert. The py_canonical lane edits an existing
number and does not insert a cell.

The check then confirms that the exported package is a valid ZIP that reopens
(in openpyxl when it is installed, and in LibreOffice only when --libreoffice
is passed), that untouched package parts are byte-identical where the
preservation contract says so, that every source cell value round-trips, and
that any refusal carries a typed code.

Engines:
  py_toolkit    workbook_forge.xlsx.import_xlsx / export_xlsx (preserving patch writer)
  py_canonical  workbook_forge.intake + canonical_xlsx.export_canonical (fresh package;
                carried content only, by design)
  rust          rust/examples/corpus_roundtrip (import_xlsx_bytes, the path the WASM
                engine uses), when the example binary is built

The full calculation's diagnostic count is recorded. The edit and export then
run in a session whose output binding is the edited cell, the documented flow
for exporting a workbook that holds formulas the engine does not calculate.

Outcomes per file and engine:
  pass      every stage ran and every check held
  refuse    a stage stopped with a typed refusal (an error carrying a known code)
  untyped   a stage stopped with a workbook error that carries no specific code
  crash     a stage raised something that is not a workbook refusal
  corrupt   export produced a package that does not reopen, or dropped/changed a
            part the contract preserves
  lossy     a source value did not round-trip

Usage:
  python tools/corpus_check.py [--report docs/corpus-report.md] [--json out.json]
         [--engines py_toolkit,py_canonical,rust] [--rust-bin PATH]
         [--libreoffice] [--strict] [FILE ...]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
CORPUS = ROOT / "fixtures" / "corpus"
EXPECTATIONS = ROOT / "tools" / "corpus" / "expectations.json"
DEFAULT_RUST_BIN = ROOT / "rust" / "target" / "debug" / "examples" / "corpus_roundtrip"
OUTCOMES = ("pass", "refuse", "untyped", "crash", "corrupt", "lossy")
# Parts an edit may rewrite. Everything else in the source package must come
# back byte-identical from the preserving writer.
MAY_CHANGE = {
    "[Content_Types].xml",  # calcChain removal
    "xl/workbook.xml",  # calcPr full-calc-on-load
    "xl/_rels/workbook.xml.rels",  # calcChain removal
    "xl/calcChain.xml",  # invalidated after an edit
    "xl/sharedStrings.xml",
}
GENERIC_CODES = {"", None, "workbook_error", "xlsx_error", "unsupported_workbook", "invalid_workbook"}


def _code_of(error: BaseException) -> str | None:
    code = getattr(error, "code", None)
    return code if isinstance(code, str) else None


def _refusal_classes():
    from workbook_forge.workbook import WorkbookError
    from workbook_forge.model import ModelError
    from workbook_forge.canonical_xlsx import CanonicalPackageError
    from workbook_forge.python_engine import ToolkitError

    return (WorkbookError, ModelError, CanonicalPackageError, ToolkitError)


def classify(error: BaseException) -> tuple[str, str | None]:
    if isinstance(error, _refusal_classes()):
        code = _code_of(error)
        return ("refuse" if code not in GENERIC_CODES else "untyped"), code
    return "crash", None


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return math.isclose(float(a), float(b), rel_tol=1e-12, abs_tol=1e-12)
        except (TypeError, ValueError):
            return False
    return a == b


def _zip_parts(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise ValueError(f"corrupt zip entry {bad}")
        return {info.filename: archive.read(info) for info in archive.infolist()}


def _openpyxl_reopens(path: Path) -> str | None:
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        return None
    import warnings

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import openpyxl

            openpyxl.load_workbook(path, keep_vba=path.suffix == ".xlsm").close()
    except Exception as error:  # noqa: BLE001 - any failure is a finding
        return f"openpyxl cannot reopen: {type(error).__name__}: {error}"
    return None


def _libreoffice_reopens(path: Path) -> str | None:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    with tempfile.TemporaryDirectory() as folder:
        result = subprocess.run(
            [soffice, "--headless", "--convert-to", "csv", "--outdir", folder, str(path)],
            capture_output=True, timeout=120,
        )
        if result.returncode != 0 or not list(Path(folder).glob("*.csv")):
            return "LibreOffice could not convert the export"
    return None


def preservation_findings(source: dict[str, bytes], exported: dict[str, bytes], edited_parts: set[str]) -> list[str]:
    findings = []
    for name, data in source.items():
        if name in MAY_CHANGE or name in edited_parts:
            continue
        if name not in exported:
            findings.append(f"dropped part {name}")
        elif exported[name] != data:
            findings.append(f"changed part {name}")
    return findings


def sheet_parts(path: Path) -> dict[str, str]:
    """Worksheet name -> part name, read from the package itself."""
    import posixpath
    from xml.etree import ElementTree as ET

    out: dict[str, str] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            workbook = "xl/workbook.xml"
            rels_name = "xl/_rels/workbook.xml.rels"
            if workbook not in names or rels_name not in names:
                return out
            root = ET.fromstring(archive.read(workbook))
            rels = ET.fromstring(archive.read(rels_name))
    except (zipfile.BadZipFile, ET.ParseError, KeyError):
        return out
    targets = {}
    for rel in rels:
        target = rel.attrib.get("Target", "")
        part = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        targets[rel.attrib.get("Id")] = part
    for node in root.iter():
        if node.tag.endswith("}sheet"):
            rid = next((v for k, v in node.attrib.items() if k.endswith("}id")), None)
            if rid in targets:
                out[node.attrib.get("name", "")] = targets[rid]
    return out


# --------------------------------------------------------------- toolkit


def _pick_edit(document: dict) -> tuple[str, str, Any] | None:
    for sheet in document["sheets"]:
        for address, cell in sheet["cells"].items():
            if cell.get("formula") is not None or cell.get("blocked_reason"):
                continue
            value = cell.get("value")
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, (int, float)):
                return sheet["name"], address, value + 1
            if isinstance(value, str) and len(value) < 1000:
                return sheet["name"], address, value + " (edited)"
    return None


def _pick_insert(document: dict, merged: dict[str, list]) -> tuple[str, str] | None:
    from workbook_forge.workbook import _cell_address

    for sheet in document["sheets"]:
        cells = sheet["cells"]
        ranges = merged.get(sheet["name"], [])
        for row in range(1, 60):
            for column in range(1, 12):
                address = _cell_address(column, row)
                if address in cells:
                    continue
                if any(c1 <= column <= c2 and r1 <= row <= r2 for c1, r1, c2, r2 in ranges):
                    continue
                return sheet["name"], address
    return None


def _values(document: dict) -> dict[str, Any]:
    out = {}
    for sheet in document["sheets"]:
        for address, cell in sheet["cells"].items():
            out[f"{sheet['name']}!{address}"] = (cell.get("formula"), cell.get("value"), cell.get("cached_value"))
    return out


def run_toolkit(path: Path, folder: Path, *, libreoffice: bool = False) -> dict[str, Any]:
    from workbook_forge.xlsx import export_xlsx, import_xlsx

    record: dict[str, Any] = {"stages": []}
    stage = "open"
    try:
        model = import_xlsx(path)
        record["stages"].append("open")
        stage = "inspect"
        report = model.inspect()
        record["inspect_keys"] = sorted(report.get("xlsx", {}))
        record["stages"].append("inspect")
        stage = "calculate"
        full = model.calculate()
        record["full_calc_diagnostics"] = len(full.get("diagnostics", []))
        record["stages"].append("calculate")
        before = model.to_dict()
        merged = {name: list(ranges) for name, ranges in model._source_baseline.merged}
        edit = _pick_edit(before)
        if edit:
            # The documented export flow for a workbook with formulas the engine
            # does not calculate: bind the cells the caller works with. The
            # edited cell is the binding here, so unrelated unsupported formulas
            # are preserved instead of blocking the export.
            stage = "rebind"
            model = import_xlsx(path, outputs={"edited": {"sheet": edit[0], "address": edit[1]}})
        stage = "edit"
        edits = []
        if edit:
            model.set_value(edit[0], edit[1], edit[2])
            edits.append(edit)
        record["stages"].append("edit" if edit else "edit(skipped: no editable value)")
        stage = "insert"
        insert = _pick_insert(before, merged)
        if insert:
            model.set_value(insert[0], insert[1], 4242)
            edits.append((insert[0], insert[1], 4242))
        record["stages"].append("insert" if insert else "insert(skipped)")
        stage = "export"
        target = folder / (path.stem + ".out.xlsx")
        export_xlsx(model, target)
        record["stages"].append("export")
        stage = "reimport"
        again = import_xlsx(target)
        record["stages"].append("reimport")
    except BaseException as error:  # noqa: BLE001 - classified below
        if isinstance(error, KeyboardInterrupt):
            raise
        outcome, code = classify(error)
        record.update(outcome=outcome, stage=stage, code=code, error=f"{type(error).__name__}: {error}"[:400])
        if outcome == "crash":
            record["traceback"] = traceback.format_exc()[-1500:]
        return record
    findings = []
    source_parts = _zip_parts(path)
    try:
        exported = _zip_parts(target)
    except Exception as error:  # noqa: BLE001
        record.update(outcome="corrupt", stage="verify", error=str(error))
        return record
    edited_sheet_parts = set()
    for sheet_name, _address, _value in edits:
        part = next(item["part"] for item in model._import_metadata["sheets"] if item["name"] == sheet_name)
        edited_sheet_parts.add(part)
    findings += preservation_findings(source_parts, exported, edited_sheet_parts)
    reopen = _openpyxl_reopens(target)
    if libreoffice:
        reopen = reopen or _libreoffice_reopens(target)
    corrupt = [item for item in findings if item.startswith("dropped")] + ([reopen] if reopen else [])
    changed = [item for item in findings if item.startswith("changed")]
    after = _values(again.to_dict())
    expected = _values(before)
    for sheet_name, address, value in edits:
        expected[f"{sheet_name}!{address}"] = (None, value, None)
    lossy = []
    for key, (formula, value, _cache) in expected.items():
        got = after.get(key)
        if got is None:
            lossy.append(f"{key} missing after reimport")
            continue
        if got[0] != formula or (formula is None and not _equal(got[1], value)):
            lossy.append(f"{key}: {formula!r}/{value!r} became {got[0]!r}/{got[1]!r}")
    record["changed_parts"] = changed
    if corrupt or changed:
        record.update(outcome="corrupt", stage="verify", error="; ".join(corrupt + changed)[:400])
    elif lossy:
        record.update(outcome="lossy", stage="verify", error="; ".join(lossy[:5])[:400])
    else:
        record["outcome"] = "pass"
    return record


# ------------------------------------------------------------- canonical


def run_canonical(path: Path, folder: Path) -> dict[str, Any]:
    import dataclasses

    from workbook_forge.canonical_xlsx import export_canonical
    from workbook_forge.intake import intake_workbook_model
    from workbook_forge.model import calculate

    record: dict[str, Any] = {"stages": []}
    stage = "open"
    try:
        workbook = intake_workbook_model(path)
        record["stages"].append("open")
        stage = "inspect"
        record["not_carried"] = len(workbook.metadata.get("intake", {}).get("not_carried", []))
        record["stages"].append("inspect")
        stage = "calculate"
        calculate(workbook)
        record["stages"].append("calculate")
        stage = "edit"
        edited = None
        sheets = list(workbook.sheets)
        for index, sheet in enumerate(sheets):
            for address, cell in sheet.cells.items():
                if cell.formula is None and isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool):
                    new = dataclasses.replace(cell, value=cell.value + 1)
                    cells = dict(sheet.cells)
                    cells[address] = new
                    sheets[index] = dataclasses.replace(sheet, cells=cells)
                    edited = (sheet.name, address, cell.value + 1)
                    break
            if edited:
                break
        workbook = dataclasses.replace(workbook, sheets=tuple(sheets))
        record["stages"].append("edit" if edited else "edit(skipped)")
        stage = "export"
        target = folder / (path.stem + ".canonical.xlsx")
        export_canonical(workbook, target)
        record["stages"].append("export")
        stage = "reimport"
        again = intake_workbook_model(target)
        record["stages"].append("reimport")
    except BaseException as error:  # noqa: BLE001
        if isinstance(error, KeyboardInterrupt):
            raise
        outcome, code = classify(error)
        record.update(outcome=outcome, stage=stage, code=code, error=f"{type(error).__name__}: {error}"[:400])
        if outcome == "crash":
            record["traceback"] = traceback.format_exc()[-1500:]
        return record
    try:
        _zip_parts(target)
    except Exception as error:  # noqa: BLE001
        record.update(outcome="corrupt", stage="verify", error=str(error))
        return record
    reopen = _openpyxl_reopens(target)
    if reopen:
        record.update(outcome="corrupt", stage="verify", error=reopen)
        return record
    lossy = []
    for sheet in workbook.sheets:
        other = again.sheet(sheet.name)
        if other is None:
            lossy.append(f"sheet {sheet.name} missing")
            continue
        for address, cell in sheet.cells.items():
            got = other.cells.get(address)
            if got is None:
                lossy.append(f"{sheet.name}!{address} missing")
            elif (cell.formula is None) != (got.formula is None) or (
                cell.formula is None and not _equal(cell.value, got.value)
            ) or (cell.formula is not None and cell.formula.expression != got.formula.expression):
                lossy.append(f"{sheet.name}!{address}: {cell.value!r} became {got.value!r}")
    if lossy:
        record.update(outcome="lossy", stage="verify", error="; ".join(lossy[:5])[:400])
    else:
        record["outcome"] = "pass"
    return record


# ------------------------------------------------------------------ rust


def run_rust(path: Path, folder: Path, binary: Path) -> dict[str, Any]:
    target = folder / (path.stem + ".rust.xlsx")
    try:
        result = subprocess.run([str(binary), str(path), str(target)], capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return {"outcome": "crash", "stage": "timeout", "error": "rust harness exceeded 300 s"}
    if result.returncode not in (0, 3):
        return {"outcome": "crash", "stage": "process", "error": (result.stderr or result.stdout)[-400:]}
    try:
        record = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"outcome": "crash", "stage": "output", "error": (result.stdout + result.stderr)[-400:]}
    if record.get("outcome") == "refused":
        code = record.get("code")
        record["outcome"] = "refuse" if code not in GENERIC_CODES else "untyped"
        return record
    if record.get("outcome") != "exported":
        return record
    findings = []
    try:
        source_parts, exported = _zip_parts(path), _zip_parts(target)
    except Exception as error:  # noqa: BLE001
        record.update(outcome="corrupt", error=str(error))
        return record
    parts_by_sheet = sheet_parts(path)
    edited_parts = {parts_by_sheet.get(name, "") for name in record.get("edited_sheets", [])}
    findings += preservation_findings(source_parts, exported, edited_parts)
    reopen = _openpyxl_reopens(target)
    corrupt = [item for item in findings if item.startswith("dropped")] + ([reopen] if reopen else [])
    changed = [item for item in findings if item.startswith("changed")]
    record["changed_parts"] = changed
    if corrupt or changed:
        record.update(outcome="corrupt", stage="verify", error="; ".join(corrupt + changed)[:400])
    elif record.get("lossy"):
        record.update(outcome="lossy", stage="verify", error="; ".join(record["lossy"][:5])[:400])
    else:
        record["outcome"] = "pass"
    return record


# ---------------------------------------------------------------- report


def corpus_files(selected: list[str]) -> list[Path]:
    if selected:
        return [Path(item).resolve() for item in selected]
    return sorted(p for p in CORPUS.iterdir() if p.suffix.lower() in {".xlsx", ".xlsm"})


def run_isolated(
    files: list[Path], engines: list[str], rust_bin: Path, *, libreoffice: bool = False
) -> dict[str, Any]:
    """Run each file in its own interpreter so one file's memory or a hard
    crash cannot take the rest of the corpus with it."""
    results: dict[str, dict[str, Any]] = {}
    for path in files:
        command = [sys.executable, str(Path(__file__).resolve()), "--child", "--engines",
                   ",".join(engines), "--rust-bin", str(rust_bin)]
        if libreoffice:
            command.append("--libreoffice")
        command.append(str(path))
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=900)
            row = json.loads(done.stdout.strip().splitlines()[-1]) if done.returncode == 0 else None
        except (subprocess.TimeoutExpired, json.JSONDecodeError, IndexError):
            row = None
            done = None
        if row is None:
            detail = "timeout" if done is None else (done.stderr or done.stdout)[-400:]
            row = {engine: {"outcome": "crash", "stage": "process", "error": detail} for engine in engines}
        results[path.name] = row
    return results


def run(
    files: list[Path], engines: list[str], rust_bin: Path, *, libreoffice: bool = False
) -> dict[str, Any]:
    results: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="corpus-check-") as tmp:
        folder = Path(tmp)
        for path in files:
            row = {}
            for engine in engines:
                start = time.perf_counter()
                if engine == "py_toolkit":
                    record = run_toolkit(path, folder, libreoffice=libreoffice)
                elif engine == "py_canonical":
                    record = run_canonical(path, folder)
                elif engine == "rust":
                    if not rust_bin.exists():
                        record = {"outcome": "not_run", "error": f"{rust_bin.name} not built"}
                    else:
                        record = run_rust(path, folder, rust_bin)
                else:
                    raise SystemExit(f"unknown engine {engine}")
                record["seconds"] = round(time.perf_counter() - start, 3)
                row[engine] = record
            results[path.name] = row
    return results


def _cell(record: dict[str, Any]) -> str:
    outcome = record.get("outcome", "?")
    if outcome == "pass":
        return "pass"
    if outcome == "refuse":
        return f"refuse `{record.get('code')}`"
    return f"**{outcome}** ({record.get('stage', '?')})"


def markdown(
    results: dict[str, Any], engines: list[str], expectations: dict[str, Any], *, strict: bool = False
) -> str:
    manifest = {}
    manifest_path = CORPUS / "MANIFEST.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")).get("files", {})
    lines = [
        "# Corpus robustness report",
        "",
        "Generated by `python tools/corpus_check.py`. Do not edit by hand.",
        "",
        "Each corpus workbook goes through open, inspect, calculate, export and reimport in each",
        "engine. The py_toolkit and rust lanes also apply one value edit and one absent-cell",
        "insert. The py_canonical lane edits an existing number and does not insert a cell.",
        "`pass` means every stage ran,",
        "the export reopened, preserved parts came back byte-identical and every source value",
        "round-tripped. `refuse` means a stage stopped with a typed refusal code (see",
        "[refusal codes](refusal-codes.md)); `untyped` means a refusal without a specific code.",
        "Bold outcomes are defects. The `py_canonical`",
        "engine writes a fresh package by design, so only carried content is compared there.",
        "This is a robustness check of Workbook Forge's own reader and writers. It is not an",
        "Excel observation and does not show Excel equivalence.",
        "",
        "## Totals",
        "",
        "| Engine | " + " | ".join(OUTCOMES) + " |",
        "| --- | " + " | ".join("---:" for _ in OUTCOMES) + " |",
    ]
    for engine in engines:
        counts = {key: 0 for key in OUTCOMES}
        for row in results.values():
            outcome = row[engine].get("outcome")
            if outcome in counts:
                counts[outcome] += 1
        lines.append(f"| {engine} | " + " | ".join(str(counts[key]) for key in OUTCOMES) + " |")
    lines += ["", "## Matrix", "", "| File | Writer | Categories | " + " | ".join(engines) + " |",
              "| --- | --- | --- | " + " | ".join("---" for _ in engines) + " |"]
    for name, row in results.items():
        info = manifest.get(name, {})
        cats = ", ".join(info.get("categories", []))
        lines.append(f"| `{name}` | {info.get('writer', '?')} | {cats} | " + " | ".join(_cell(row[e]) for e in engines) + " |")
    lines += ["", "## Details", ""]
    for name, row in results.items():
        notes = []
        for engine in engines:
            record = row[engine]
            if record.get("outcome") != "pass" and record.get("error"):
                notes.append(f"- {engine}: {record['error']}".replace("\n", " "))
        if notes:
            lines.append(f"`{name}`")
            lines.append("")
            lines.extend(notes)
            lines.append("")
    lines += ["## Formulas a full calculation does not cover", "",
              "The edit and export run with the edited cell as the session's output binding,",
              "the documented flow for workbooks that hold formulas the engine does not calculate.",
              "These files hold such formulas; an unbound full-workbook export of them is refused",
              "until the formulas are supported. Counts are calculation diagnostics.", "",
              "| File | " + " | ".join(e for e in engines if e != "py_canonical") + " |",
              "| --- | " + " | ".join("---:" for e in engines if e != "py_canonical") + " |"]
    for name, row in results.items():
        counts = [row[e].get("full_calc_diagnostics") for e in engines if e != "py_canonical"]
        if any(counts):
            lines.append(f"| `{name}` | " + " | ".join(str(c if c is not None else "-") for c in counts) + " |")
    lines.append("")
    mismatches = expectation_mismatches(results, expectations, strict=strict)
    lines += ["## Expectation mismatches", ""]
    lines += [f"- {item}" for item in mismatches] or ["None."]
    lines.append("")
    return "\n".join(lines)


def expectation_mismatches(
    results: dict[str, Any], expectations: dict[str, Any], *, strict: bool = False
) -> list[str]:
    out = []
    for name, row in results.items():
        for engine, record in row.items():
            # A missing corpus_roundtrip binary is not a pass. --strict counts it.
            if record.get("outcome") == "not_run" and not strict:
                continue
            want = expectations.get(name, {}).get(engine, "pass") if not name.startswith("_") else "pass"

            got = record.get("outcome")
            if want.startswith("refuse:"):
                if got != "refuse" or record.get("code") != want.split(":", 1)[1]:
                    out.append(f"{name} {engine}: expected {want}, got {got} {record.get('code') or ''}".strip())
            elif got != want:
                out.append(f"{name} {engine}: expected {want}, got {got}")
    return out


def expectations_from(results: dict[str, Any], note: str) -> dict[str, Any]:
    """The non-pass outcomes of a run, in the expectations.json shape.

    Defects (crash, corrupt, lossy) are never written: the test rejects them
    whatever the file says, so they must be fixed rather than expected.
    """
    out: dict[str, Any] = {"_note": note} if note else {}
    for name in sorted(results):
        row = {}
        for engine, record in results[name].items():
            got = record.get("outcome")
            if got == "refuse":
                row[engine] = f"refuse:{record.get('code')}"
            elif got == "untyped":
                row[engine] = "untyped"
        if row:
            out[name] = row
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*")
    parser.add_argument("--engines", default="py_toolkit,py_canonical,rust")
    parser.add_argument("--rust-bin", default=os.environ.get("CORPUS_RUST_BIN", str(DEFAULT_RUST_BIN)))
    parser.add_argument("--report")
    parser.add_argument("--json")
    parser.add_argument("--libreoffice", action="store_true",
                        help="reopen toolkit exports in LibreOffice when soffice is on PATH")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 on an expectation mismatch, including an engine that did not run")
    parser.add_argument("--write-expectations", action="store_true",
                        help="rewrite tools/corpus/expectations.json from this run (whole corpus only)")
    parser.add_argument("--in-process", action="store_true", help="run every file in this interpreter")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    engines = [item for item in args.engines.split(",") if item]
    files = corpus_files(args.files)
    if args.child:
        row = run(files, engines, Path(args.rust_bin), libreoffice=args.libreoffice)
        print(json.dumps(row[files[0].name], ensure_ascii=False))
        return 0
    runner = run if args.in_process else run_isolated
    results = runner(files, engines, Path(args.rust_bin), libreoffice=args.libreoffice)
    expectations = json.loads(EXPECTATIONS.read_text(encoding="utf-8")) if EXPECTATIONS.exists() else {}
    if args.write_expectations:
        if args.files:
            parser.error("--write-expectations needs the whole corpus")
        expectations = expectations_from(results, expectations.get("_note", ""))
        EXPECTATIONS.write_text(json.dumps(expectations, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.report:
        Path(args.report).write_text(
            markdown(results, engines, expectations, strict=args.strict), encoding="utf-8"
        )
    for name, row in results.items():
        print(name.ljust(40), "  ".join(f"{e}={_cell(r)}" for e, r in row.items()))
    mismatches = expectation_mismatches(results, expectations, strict=args.strict)
    for item in mismatches:
        print("MISMATCH", item)
    return 1 if (args.strict and mismatches) else 0


if __name__ == "__main__":
    raise SystemExit(main())
