"""Matched four-route executable-source benchmark for numeric source subset v1."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import time

from compiler_benchmark import rust
from source_compiler import compile_workbook, execute, lower, translate
from workbook_forge.toolkit import WorkbookModel
from workbook_forge.xlsx import export_xlsx, import_xlsx

ROOT = Path(__file__).resolve().parents[1]


def run(folder: Path, binary: Path, repeats: int) -> dict:
    folder.mkdir(parents=True, exist_ok=False)
    results = []
    for origin, target in [("rust", "rust"), ("python", "python"), ("rust", "python"), ("python", "rust")]:
        samples, artifacts = [], []
        for sample in range(repeats + 1):
            lane = folder / f"{origin}-{target}-{sample}"
            lane.mkdir()
            model = WorkbookModel(sheets=["Calc"])
            model.set_value("Calc", "A1", 2)
            model.set_formula("Calc", "B1", "=A1*2+3")
            model.set_style("Calc", "A1", number_format="0.00", bold=True)
            original = lane / "original.xlsx"
            export_xlsx(model, original)
            input_hash=hashlib.sha256(original.read_bytes()).hexdigest()
            started = time.perf_counter()
            program, manifest = compile_workbook(original, origin)
            generated = time.perf_counter()
            (lane / "manifest.json").write_text(json.dumps(manifest, indent=2))
            edited = program.replace("2.0", "5.0")
            first = lane / ("first.rs" if origin == "rust" else "first.py")
            first.write_text(edited)
            original.rename(lane / "original.unavailable")
            assert execute(first, origin, 7, lane / "first_executable") == 38
            executed_first = time.perf_counter()
            second_program = translate(first.read_text(), origin, target)
            second = lane / ("second.rs" if target == "rust" else "second.py")
            second.write_text(second_program)
            translated = time.perf_counter()
            assert execute(second, target, 7, lane / "second_executable") == 38
            executed_second = time.perf_counter()
            document = lower(second.read_text(), target, json.loads((lane / "manifest.json").read_text()), 7)
            output = lane / "output.xlsx"
            if target == "rust":
                ir = lane / "lowered.json"
                ir.write_text(json.dumps(document))
                subprocess.run([str(binary), "emit-fresh", str(ir), str(output)], check=True, capture_output=True, timeout=60)
            else:
                export_xlsx(WorkbookModel(document=document), output)
            exported = time.perf_counter()
            py = import_xlsx(output).to_dict()
            rs = rust(binary, "decode", str(output))
            for snapshot in (py, rs):
                cells = snapshot["sheets"][0]["cells"]
                assert cells["A1"]["value"] == 7
                assert cells["B1"]["cached_value"] == 38
                assert "5.0" in cells["B1"]["formula"]
                assert cells["A1"]["style"] == manifest["model"]["sheets"][0]["cells"]["A1"]["style"]
            if sample:
                artifacts.append({"input_ooxml_sha256":input_hash,
                    "edited_source_sha256":hashlib.sha256(first.read_bytes()).hexdigest(),
                    "translated_source_sha256":hashlib.sha256(second.read_bytes()).hexdigest(),
                    "manifest_sha256":hashlib.sha256((lane/"manifest.json").read_bytes()).hexdigest(),
                    "output_ooxml_sha256":hashlib.sha256(output.read_bytes()).hexdigest()})
                samples.append({"total_ms": (exported - started) * 1000,
                                "ooxml_to_source_ms": (generated - started) * 1000,
                                "first_compile_execute_ms": (executed_first - generated) * 1000,
                                "source_translation_ms": (translated - executed_first) * 1000,
                                "second_compile_execute_ms": (executed_second - translated) * 1000,
                                "source_to_ooxml_ms": (exported - executed_second) * 1000})
        results.append({"path": f"ooxml->{origin}_source->{target}_source->ooxml",
                        "median_ms": {key: statistics.median(s[key] for s in samples) for key in samples[0]},
                        "samples": samples, "original_package_during_translation_execution_export": "unavailable",
                        "artifacts":artifacts,
                        "lane":{"reader":"python:xlsx.import_xlsx","source_import_and_lowering":"python",
                            "first_generated_evaluator":origin,"translated_generated_evaluator":target,
                            "writer":target+":xlsx.export_xlsx","writer_recalculation":target,
                            "output_contract":"fresh workbook generation from formula-free metadata and edited source",
                            "orchestrator":"python; needed for reading/translation/lowering, not standalone Rust execution",
                            "end_to_end_rust_only":False},
                        "formula_caches_in_manifest": False, "edited_source_and_input_result": 38,
                        "independent_expected_result": "7*5+3=38", "excel_observation": "not_run"})
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    child = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return {"benchmark_version": 1, "source_subset_version": 1,
            "workload": "same two-cell workbook; source multiplier 2->5; input 2->7; both programs executed; both backends reimport exported values/formulas/style",
            "dependencies": {"generated_python": "Python standard library", "generated_rust": "Rust standard library; rustc to build",
                             "compiler": "Workbook Forge Python library and independent Rust export adapter; no UI, server or agent"},
            "timing_scope": "warmup excluded; rustc compilation and process startup included; validation reimports excluded; phases measured on identical workbook/edit workload",
            "memory": {"parent_peak_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
                       "largest_child_peak_rss_bytes": child if sys.platform == "darwin" else child * 1024,
                       "scope": "process-lifetime high-water marks in bytes including rustc; not per-route runtime allocations"},
            "routes": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--rust-binary", type=Path, default=ROOT / "rust/target/release/examples/compiler_roundtrip")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error("repeats must be 1..10")
    result = run(args.directory.resolve(), args.rust_binary.resolve(), args.repeats)
    (args.directory / "receipt.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
