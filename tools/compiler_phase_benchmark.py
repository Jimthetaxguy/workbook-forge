"""Matched bounded phase costs; no Excel oracle, OS-cache flush or speedup assumption."""

import argparse
import hashlib
import json
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import time

from compiler_workloads import workload
from workbook_forge import source_graph as g
from workbook_forge.toolkit import WorkbookModel
from workbook_forge.xlsx import export_xlsx, import_xlsx


def timed(fn):
    start = time.perf_counter()
    value = fn()
    return value, (time.perf_counter() - start) * 1000


def equal(actual, expected):
    assert {a: g.typed(v) for a, v in actual.items()} == {
        a: g.typed(v) for a, v in expected.items()
    }


def sdk_values(report, program):
    return {
        address: report["values"][sheet + "!" + reference.render()]
        for address in program.defaults.keys() | program.formulas.keys()
        for sheet, reference in [g.split_reference(address, program.sheet, program.sheets)]
    }


def decode_stdout(stdout):
    return {
        bytes.fromhex(address).decode(): g.decode(tag, payload)
        for address, tag, payload in (line.split("\t", 2) for line in stdout.splitlines())
    }


def rust(binary, *args):
    return json.loads(
        subprocess.run(
            [str(binary), *map(str, args)], check=True, capture_output=True, text=True, timeout=60
        ).stdout
    )


def run(directory, binary, repeats):
    directory.mkdir(parents=True, exist_ok=False)
    rows = []
    for kind in ("chain", "fanout", "ranges", "multisheet"):
        for size in ("small", "medium"):
            document, edit, original_expected, edited_expected = workload(kind, size)
            base = directory / (kind + "-" + size)
            base.mkdir()
            package = base / "original.xlsx"
            export_xlsx(WorkbookModel(document=document), package)
            for language in ("python", "rust"):
                folder = base / language
                folder.mkdir()
                phases = []
                for sample in range(repeats + 1):
                    sample_dir = folder / str(sample)
                    sample_dir.mkdir()
                    _, import_ms = timed(
                        lambda: (
                            import_xlsx(package)
                            if language == "python"
                            else rust(binary, "decode", package)
                        )
                    )
                    (code, manifest), compile_ms = timed(
                        lambda: g.compile_workbook(package, language, 5)
                    )
                    program = g.import_source(code, language)
                    _, parse_ms = timed(lambda: g.import_source(code, language))
                    _, validate_ms = timed(lambda: g.validate(program))
                    inputs = {g.sheet_prefix(edit[0]) + edit[1]: edit[2]}
                    lowered, lower_ms = timed(lambda: g.lower(code, language, manifest, inputs))
                    model = WorkbookModel(document=document, backend=language)
                    equal(sdk_values(model.calculate(), program), original_expected)

                    def edit_calculate():
                        model.apply(
                            [{"sheet": edit[0], "address": edit[1], "value": edit[2]}],
                            expected_revision=0,
                        )
                        return model.calculate(workers=1)

                    report, edit_ms = timed(edit_calculate)
                    equal(sdk_values(report, program), edited_expected)
                    output = sample_dir / "backend.xlsx"
                    ir = sample_dir / "model.json"
                    ir.write_text(json.dumps(model.to_dict()))
                    _, export_ms = timed(
                        lambda: (
                            export_xlsx(model, output)
                            if language == "python"
                            else rust(binary, "emit-fresh", ir, output)
                        )
                    )
                    observed = import_xlsx(output).to_dict()
                    equal(
                        {
                            g.sheet_prefix(s["name"]) + a: c.get("cached_value")
                            if c.get("formula") is not None
                            else c.get("value")
                            for s in observed["sheets"]
                            for a, c in s["cells"].items()
                        },
                        edited_expected,
                    )
                    phases.append(
                        {
                            "intake_ms": import_ms,
                            "compile_import_codegen_ms": compile_ms,
                            "source_parse_validate_ms": parse_ms,
                            "graph_validate_ms": validate_ms,
                            "lower_ms": lower_ms,
                            "session_edit_recalculate_ms": edit_ms,
                            "backend_export_ms": export_ms,
                        }
                    )
                source = folder / ("program.rs" if language == "rust" else "program.py")
                source.write_text(code)
                actual, cold = timed(
                    lambda: g.execute(source, language, inputs, folder / "retained-executable")
                )
                equal(actual, edited_expected)
                build_ms = 0
                if language == "rust":
                    executable = folder / "warm-executable"
                    _, build_ms = timed(
                        lambda: subprocess.run(
                            [
                                "rustc",
                                "--edition=2024",
                                "--crate-name",
                                "forge_phase",
                                "-O",
                                str(source.resolve()),
                                "-o",
                                str(executable.resolve()),
                            ],
                            check=True,
                            capture_output=True,
                            timeout=60,
                        )
                    )
                    command = [str(executable.resolve()), "--stdin"]
                else:
                    command = [sys.executable, "-I", str(source.resolve()), "--stdin"]
                stdin = "\n".join(a.encode().hex() + "=" + g.encode(v) for a, v in inputs.items())
                warm = []
                for sample in range(repeats + 1):
                    output, duration = timed(
                        lambda: subprocess.run(
                            command,
                            input=stdin,
                            check=True,
                            capture_output=True,
                            text=True,
                            timeout=60,
                        )
                    )
                    equal(decode_stdout(output.stdout), edited_expected)
                    warm.append(duration)
                _, checked = timed(
                    lambda: g.emit(
                        source, language, manifest, inputs, folder / "checked.xlsx", binary
                    )
                )
                rows.append(
                    {
                        "workload": kind,
                        "size": size,
                        "language": language,
                        "cells": len(program.defaults) + len(program.formulas),
                        "formulas": len(program.formulas),
                        "edges": sum(map(len, g.validate(program).values())),
                        "source_bytes": source.stat().st_size,
                        "package_bytes": package.stat().st_size,
                        "fixture_sha256": hashlib.sha256(
                            json.dumps(document, sort_keys=True).encode()
                        ).hexdigest(),
                        "first_sample": phases[0],
                        "warm_samples": phases[1:],
                        "warm_median_ms": {
                            k: statistics.median(p[k] for p in phases[1:]) for k in phases[0]
                        },
                        "cold_validated_build_execute_once_ms": cold,
                        "separate_rust_build_once_ms": build_ms,
                        "first_prebuilt_process_ms": warm[0],
                        "prebuilt_process_samples_ms": warm[1:],
                        "prebuilt_process_median_ms": statistics.median(warm[1:]),
                        "checked_source_to_ooxml_once_ms": checked,
                        "lane": {
                            "intake": language
                            + (":process startup included" if language == "rust" else ":library"),
                            "compiler": "Python intake/parser/codegen for both source languages",
                            "generated_evaluator": language,
                            "session": language + ":SDK; Rust uses existing PyO3 bridge",
                            "writer": language
                            + ":fresh export including recalculation; Rust process includes startup",
                            "output_validator": "Python reimport; typed independent workload arithmetic",
                            "prebuilt_execution": "new process per run; excludes parsing/build; no persistent engine/cache claim",
                        },
                        "excel_observed": False,
                    }
                )
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    child = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    receipt = {
        "version": 1,
        "repetitions": repeats,
        "warmup": 1,
        "timing_note": "first invocation, not OS-cold cache; matched fixtures; sequential workers=1; build/setup outside phase medians",
        "memory": {
            "parent_peak_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
            "largest_child_peak_rss_bytes": child if sys.platform == "darwin" else child * 1024,
            "scope": "lifetime peaks including rustc, not per-phase allocations or a hard memory bound",
        },
        "rust_adapter_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "source_graph_sha256": hashlib.sha256(Path(g.__file__).read_bytes()).hexdigest(),
        "rows": rows,
    }
    (directory / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--rust-binary", type=Path, required=True)
    parser.add_argument("--repeats", type=int, choices=range(1, 6), default=3)
    args = parser.parse_args()
    run(args.directory, args.rust_binary.resolve(), args.repeats)
