"""Synthetic, versioned OOXML -> typed toolkit IR -> OOXML benchmark.

Host runtimes consume each other's actual IR bytes. No source-code generation
claim is made. Package preservation uses a separate immutable source baseline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import resource
import statistics
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from workbook_forge.toolkit import WorkbookModel
from workbook_forge.xlsx import export_snapshot, export_xlsx, import_xlsx

ROOT = Path(__file__).resolve().parents[1]
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def rust(binary: Path, *args: str) -> dict:
    result = subprocess.run([str(binary), *args], check=True, capture_output=True, text=True, timeout=60)
    return json.loads(result.stdout)


def fixture(path: Path) -> None:
    model = WorkbookModel(sheets=["Data", "Calc"])
    for address, value in {"A1": 2, "A2": 3, "A3": "4", "A4": True, "A5": ""}.items():
        model.set_value("Data", address, value)
    for address, formula in {
        "B1": "=Data!$A$1*2", "B2": "=SUM(Data!A1:A2)+B1",
        "B3": '=IF(Data!A4,"yes","no")', "B4": "=1/0",
        "B5": "=IFERROR(B4,9)", "B6": '=Data!A3&"x"',
        "B7": "=Data!A9", "B8": "=LEN(Data!A5)",
    }.items():
        model.set_formula("Calc", address, formula)
    model.set_style("Data", "A1", number_format="0.00", bold=True)
    seed = path.with_name("seed.xlsx")
    export_xlsx(model, seed)
    with zipfile.ZipFile(seed) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    workbook = ET.fromstring(parts["xl/workbook.xml"])
    names = ET.Element(f"{{{MAIN}}}definedNames")
    ET.SubElement(names, f"{{{MAIN}}}definedName", {"name": "InputPrice"}).text = "Data!$A$1"
    calc = workbook.find(f"{{{MAIN}}}calcPr")
    workbook.insert(list(workbook).index(calc) if calc is not None else len(workbook), names)
    parts["xl/workbook.xml"] = ET.tostring(workbook)
    sheet = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
    merges = ET.SubElement(sheet, f"{{{MAIN}}}mergeCells", {"count": "1"})
    ET.SubElement(merges, f"{{{MAIN}}}mergeCell", {"ref": "C1:D1"})
    parts["xl/worksheets/sheet1.xml"] = ET.tostring(sheet)
    parts["custom/benchmark.xml"] = b'<benchmark version="1"><opaque>preserve me</opaque></benchmark>'
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)


def xml_semantics(data: bytes, *, workbook: bool = False, worksheet: bool = False):
    root = ET.fromstring(data)
    if workbook:
        for node in list(root):
            if node.tag == f"{{{MAIN}}}calcPr":
                root.remove(node)  # Export explicitly requests full Excel recalculation.
    if worksheet:
        for node in list(root):
            if node.tag in {f"{{{MAIN}}}sheetData", f"{{{MAIN}}}dimension"}:
                root.remove(node)  # Cell meaning is compared through independent reimport.

    def normalized(node):
        return [node.tag, sorted(node.attrib.items()), (node.text or "").strip(),
                [normalized(child) for child in node]]
    return normalized(root)


def check_package(source: Path, output: Path) -> None:
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(output) as after:
        assert set(before.namelist()) == set(after.namelist()), "package parts changed"
        for name in before.namelist():
            if name == "xl/workbook.xml":
                assert xml_semantics(before.read(name), workbook=True) == xml_semantics(after.read(name), workbook=True)
            elif name.startswith("xl/worksheets/"):
                assert xml_semantics(before.read(name), worksheet=True) == xml_semantics(after.read(name), worksheet=True)
            else:
                assert before.read(name) == after.read(name), f"preserved part changed: {name}"


def decode(binary: Path, engine: str, source: Path) -> dict:
    return rust(binary, "decode", str(source)) if engine == "rust" else import_xlsx(source).to_dict()


def stdlib_patch(source: Path, output: Path) -> None:
    """Conventional XML-library overlap: edit/preserve, explicitly no engine."""
    with zipfile.ZipFile(source) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    for index in (1, 2):
        part = f"xl/worksheets/sheet{index}.xml"
        root = ET.fromstring(parts[part])
        for cell in root.findall(f".//{{{MAIN}}}sheetData/{{{MAIN}}}row/{{{MAIN}}}c"):
            formula = cell.find(f"{{{MAIN}}}f")
            value = cell.find(f"{{{MAIN}}}v")
            if index == 1 and cell.get("r") == "A1":
                value.text = "7"
            if index == 2 and cell.get("r") == "B1":
                formula.text = "Data!$A$1*3"
            if formula is not None and value is not None:
                cell.remove(value)  # Cached results would be stale after the edit.
        parts[part] = ET.tostring(root)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)


def emit(binary: Path, engine: str, source: Path, ir: Path, output: Path) -> dict:
    if engine == "rust":
        return rust(binary, "emit", str(source), str(ir), str(output))
    document = json.loads(ir.read_text())
    imported = import_xlsx(source)
    originals = imported.to_dict()["sheets"]
    assert [s["name"] for s in document["sheets"]] == [s["name"] for s in originals]
    for sheet, original in zip(document["sheets"], originals, strict=True):
        sheet["id"] = original["id"]  # Explicitly rebind runtime-local IDs.
    report = WorkbookModel(document=document).calculate()
    export_snapshot(imported, document, output)
    return report


def run(binary: Path, folder: Path, repeats: int = 3) -> dict:
    folder.mkdir(parents=True, exist_ok=False)
    source = folder / "source.xlsx"
    fixture(source)
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    rows = []
    overlap_timings = []
    for sample in range(repeats + 1):
        output = folder / f"stdlib-{sample}.xlsx"
        start = time.perf_counter()
        stdlib_patch(source, output)
        elapsed = (time.perf_counter() - start) * 1000
        check_package(source, output)
        if sample:
            overlap_timings.append(elapsed)
    for front, back in [("rust", "rust"), ("python", "python"), ("rust", "python"), ("python", "rust")]:
        timings, artifact_hashes = [], []
        for sample in range(repeats + 1):
            start = time.perf_counter()
            document = decode(binary, front, source)
            # Deliberately change the *actual front-end artifact*, not the XML.
            document["sheets"][0]["cells"]["A1"]["value"] = 7
            document["sheets"][1]["cells"]["B1"]["formula"] = "=Data!$A$1*3"
            # Imported formula caches are deliberately untrusted by calculation.
            for sheet in document["sheets"]:
                for cell in sheet["cells"].values():
                    if cell.get("formula"):
                        cell["cached_value"] = -999
            ir = folder / f"{front}-{back}-{sample}.json"
            ir.write_text(json.dumps(document, allow_nan=False))
            output = folder / f"{front}-{back}-{sample}.xlsx"
            report = emit(binary, back, source, ir, output)
            elapsed = (time.perf_counter() - start) * 1000
            assert not report["diagnostics"], report["diagnostics"]
            expected = {"Calc!B1": 21, "Calc!B2": 31, "Calc!B3": "yes", "Calc!B5": 9,
                        "Calc!B6": "4x", "Calc!B7": 0, "Calc!B8": 0}
            for key, value in expected.items():
                assert report["values"][key] == value, (key, report["values"][key])
            error = report["values"]["Calc!B4"]
            assert error["error"] == "#DIV/0!", error
            # Fresh independent reimport of emitted content in both runtimes.
            py = decode(binary, "python", output)
            rs = decode(binary, "rust", output)
            for snapshot in (py, rs):
                assert snapshot["sheets"][0]["cells"]["A1"]["value"] == 7
                assert snapshot["sheets"][1]["cells"]["B1"]["formula"].removeprefix("=") == "Data!$A$1*3"
                assert snapshot["sheets"][1]["cells"]["B1"]["cached_value"] == 21
                for address, cell in snapshot["sheets"][1]["cells"].items():
                    key = f"Calc!{address}"
                    if key in expected:
                        assert cell["cached_value"] == expected[key]
                assert snapshot["sheets"][1]["cells"]["B4"]["cached_value"]["error"] == "#DIV/0!"
                assert snapshot["sheets"][0]["cells"]["A1"]["style"] == document["sheets"][0]["cells"]["A1"]["style"]
            check_package(source, output)
            if sample:  # One warmup, same workload and boundary count on every route.
                timings.append(elapsed)
                artifact_hashes.append({"input_ooxml_sha256":fingerprint,
                    "transferred_ir_sha256":hashlib.sha256(ir.read_bytes()).hexdigest(),
                    "output_ooxml_sha256":hashlib.sha256(output.read_bytes()).hexdigest()})
        rows.append({"path": f"ooxml->{front}-typed-ir->{back}->ooxml",
                     "median_ms": statistics.median(timings), "samples_ms": timings,
                     "semantic_checks": "passed-independent-arithmetic-and-package-invariants",
                     "artifact_hashes":artifact_hashes,
                     "lane":{"reader":front+":xlsx.import_xlsx","baseline_reader":back+":xlsx.import_xlsx",
                         "evaluator":back+":toolkit.calculate","writer":back+":xlsx.export_snapshot",
                         "output_contract":"preservation-aware patching of immutable imported package",
                         "orchestrator":"python; fixture creation and validation are outside the native CLI",
                         "python_calculation_in_lane":back=="python",
                         "native_reader_evaluator_writer_rust_only":front==back=="rust"},
                     "excel_recalculation": "not_run", "source_translation": "not_implemented"})
    assert hashlib.sha256(source.read_bytes()).hexdigest() == fingerprint
    return {"benchmark_version": 1, "fixture_version": 1, "source_sha256": fingerprint,
            "workload": "same 13 stored cells, 8 formulas, changed input and expression; baseline required for preservation",
            "timing_scope": "front import, IR mutation/serialization, target baseline import/calculation/export; excludes validation reimports",
            "memory": {"parent_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                       "largest_child_peak_rss_bytes": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
                       "scope": "process-lifetime high-water marks, macOS bytes; not per-route allocation or comparable Rust/Python memory"},
            "conventional_library_overlap": {"library": "Python zipfile + ElementTree", "median_ms": statistics.median(overlap_timings),
                "samples_ms": overlap_timings, "work": "same package/input/formula edits and preservation, all formula caches removed",
                "formula_execution": False, "dependency_calculation": False, "source_translation": False,
                "comparison_limit": "overlapping package edits only; no speed ratio against routes that additionally calculate and validate"},
            "routes": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rust-binary", type=Path, default=ROOT / "rust/target/release/examples/compiler_roundtrip")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:
        parser.error("repeats must be between 1 and 20")
    result = run(args.rust_binary.resolve(), args.output, args.repeats)
    (args.output / "receipt.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    sys.exit(main())
