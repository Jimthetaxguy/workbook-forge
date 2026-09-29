#!/usr/bin/env python3
"""Run shared canonical calc cases through Python and Rust and print a receipt."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from workbook_forge.canonical_calc import CanonicalCalculationSession  # noqa: E402
from workbook_forge.model import Workbook  # noqa: E402


def compare_values(
    actual: Any,
    expected: Any,
    tolerance: float,
    path: str,
    differences: list[dict[str, Any]],
) -> None:
    if (
        isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and isinstance(expected, (int, float))
        and not isinstance(expected, bool)
    ):
        delta = abs(actual - expected)
        if delta > tolerance:
            differences.append(
                {"path": path, "expected": expected, "actual": actual, "difference": delta}
            )
        return
    if isinstance(actual, dict) and isinstance(expected, dict):
        if set(actual) != set(expected):
            differences.append(
                {
                    "path": path,
                    "expected_keys": sorted(expected),
                    "actual_keys": sorted(actual),
                }
            )
            return
        for key in sorted(expected):
            compare_values(actual[key], expected[key], tolerance, f"{path}.{key}", differences)
        return
    if isinstance(actual, list) and isinstance(expected, list):
        if len(actual) != len(expected):
            differences.append(
                {"path": path, "expected_length": len(expected), "actual_length": len(actual)}
            )
            return
        for index, (actual_item, expected_item) in enumerate(zip(actual, expected, strict=True)):
            compare_values(actual_item, expected_item, tolerance, f"{path}[{index}]", differences)
        return
    if actual != expected:
        differences.append({"path": path, "expected": expected, "actual": actual})


def main() -> int:
    model_path = ROOT / "tests/fixtures/canonical/operating-scenario-v1.json"
    cases_path = ROOT / "tests/fixtures/canonical/operating-scenario-cases.json"
    schema_path = ROOT / "schemas/workbook-model-v1.schema.json"
    model_bytes = model_path.read_bytes()
    source_document = json.loads(model_bytes)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(source_document)
    cases_document = json.loads(cases_path.read_text(encoding="utf-8"))
    workbook = Workbook.from_bytes(model_bytes)

    environment = os.environ.copy()
    environment["CARGO_TARGET_DIR"] = str(ROOT / "rust" / "target")
    rust_process = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--manifest-path",
            str(ROOT / "rust/Cargo.toml"),
            "--example",
            "canonical_calc",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    rust_receipt = json.loads(rust_process.stdout)
    differences: list[dict[str, Any]] = []
    compare_values(
        json.loads(workbook.to_bytes()),
        rust_receipt["workbook"],
        0,
        "canonical_workbook",
        differences,
    )

    python_cases: list[dict[str, Any]] = []
    for case in cases_document["cases"]:
        session = CanonicalCalculationSession(workbook)
        session.set_inputs(case["inputs"], expected_revision=0)
        report = session.calculate().to_dict()
        compare_values(
            report["outputs"],
            case["expected"],
            cases_document["numeric_tolerance"],
            f"python.{case['name']}.outputs",
            differences,
        )
        python_cases.append(
            {
                "name": case["name"],
                "inputs": case["inputs"],
                "outputs": report["outputs"],
                "values": report["values"],
                "revision": report["revision"],
                "diagnostics": report["diagnostics"],
            }
        )

    if rust_receipt["schema_version"] != workbook.schema_version:
        differences.append({"path": "schema_version", "python": workbook.schema_version,
                            "rust": rust_receipt["schema_version"]})
    if rust_receipt["model_version"] != workbook.model_version:
        differences.append({"path": "model_version", "python": workbook.model_version,
                            "rust": rust_receipt["model_version"]})
    if len(rust_receipt["cases"]) != len(cases_document["cases"]):
        differences.append({"path": "case_count", "expected": len(cases_document["cases"]),
                            "actual": len(rust_receipt["cases"])})
    else:
        for index, (case, rust_case) in enumerate(
            zip(cases_document["cases"], rust_receipt["cases"], strict=True)
        ):
            case_path = f"rust.{case['name']}.outputs"
            if rust_case["name"] != case["name"]:
                differences.append({"path": f"rust.cases[{index}].name", "expected": case["name"],
                                    "actual": rust_case["name"]})
            compare_values(rust_case["outputs"], case["expected"],
                           cases_document["numeric_tolerance"], case_path, differences)
            compare_values(python_cases[index]["outputs"], rust_case["outputs"],
                           cases_document["numeric_tolerance"],
                           f"parity.{case['name']}.outputs", differences)
            compare_values(rust_case["values"], python_cases[index]["values"],
                           cases_document["numeric_tolerance"],
                           f"parity.{case['name']}.cell_values", differences)
            compare_values(rust_case["diagnostics"], python_cases[index]["diagnostics"],
                           0, f"parity.{case['name']}.diagnostics", differences)

    bindings = [
        {
            "direction": binding.direction,
            "name": binding.name,
            "cell": f"{binding.sheet}!{binding.address}",
            "value_type": binding.value_type,
        }
        for binding in workbook.bindings
    ]
    receipt = {
        "status": "pass" if not differences else "fail",
        "fixture_sha256": hashlib.sha256(model_bytes).hexdigest(),
        "schema_version": workbook.schema_version,
        "model_version": workbook.model_version,
        "python_backend": "python",
        "rust_backend": rust_receipt["backend"],
        "numeric_tolerance": cases_document["numeric_tolerance"],
        "bindings": bindings,
        "python_cases": python_cases,
        "rust_cases": rust_receipt["cases"],
        "differences": differences,
    }
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0 if not differences else 1


if __name__ == "__main__":
    raise SystemExit(main())
