"""Canonical workbook hydration and calc-binding parity."""

import json
import math
import os
import subprocess
from pathlib import Path

import jsonschema
import pytest

from workbook_forge.model import ModelError, calculate, hydrate

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "fixtures" / "operating-scenario.workbook.json"
SCHEMA = ROOT / "schemas" / "workbook-model.v1.schema.json"


def _schema():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator
    validator.check_schema(schema)
    return schema


def _normalized(value):
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer() and abs(value) <= 2**53:
            return int(value)
        return value
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalized(item) for key, item in value.items()}
    raise AssertionError(type(value))


def _canonical(value) -> str:
    return json.dumps(_normalized(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _rust(mode: str) -> dict:
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(ROOT / "rust" / "target")
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--manifest-path",
            str(ROOT / "rust" / "Cargo.toml"),
            "--locked",
            "--example",
            "canonical_calc",
            "--",
            str(FIXTURE),
            mode,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_schema_accepts_the_shared_fixture_and_required_fields_are_enforced():
    schema = _schema()
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    jsonschema.validate(document, schema)
    for field in schema["required"]:
        broken = dict(document)
        del broken[field]
        with pytest.raises(ModelError) as caught:
            hydrate(json.dumps(broken).encode())
        assert caught.value.code == "missing_required_field"
        assert caught.value.message == field


def test_hydrate_round_trip_rejects_unknown_versions_and_fields():
    raw = FIXTURE.read_bytes()
    workbook = hydrate(raw)
    assert workbook.schema_version == 1
    assert workbook.model_version == 1
    assert workbook.diagnostics == ()
    revenue = workbook.sheet("Forecast").cells["F2"]
    assert revenue.formula is not None
    assert revenue.formula.result is None
    assert revenue.formula.dependencies == ()
    again = hydrate(workbook.to_json())
    assert again.to_json() == workbook.to_json()
    assert workbook.bindings.inputs["unit_price"].key() == "Assumptions!B1"
    assert workbook.bindings.outputs["revenue"].key() == "Forecast!F2"

    document = json.loads(raw)
    document["schema_version"] = 2
    with pytest.raises(ModelError) as caught:
        hydrate(json.dumps(document).encode())
    assert caught.value.code == "unsupported_schema_version"
    assert caught.value.message == "2"

    document["schema_version"] = 1
    document["model_version"] = 2
    with pytest.raises(ModelError) as caught:
        hydrate(json.dumps(document).encode())
    assert caught.value.code == "unsupported_model_version"

    document["model_version"] = 1
    document["revision"] = 1
    with pytest.raises(ModelError) as caught:
        hydrate(json.dumps(document).encode())
    assert caught.value.code == "unknown_field"
    assert caught.value.message == "revision"

    del document["revision"]
    del document["sheets"][0]["cells"]["A1"]["value"]
    with pytest.raises(ModelError) as caught:
        hydrate(json.dumps(document).encode())
    assert caught.value.code == "missing_required_field"
    assert caught.value.message == "sheets[0].cells.A1.value"


def test_a_blank_reference_calculates_to_zero_not_null():
    def formula(address, expression):
        return {"address": address, "value": None, "data_type": "blank",
                "formula": {"expression": expression, "dependencies": [], "result": None}}

    document = {
        "schema_version": 1, "model_version": 1, "metadata": {},
        "sheets": [{"name": "S", "cells": {
            "A1": formula("A1", "=B1"),
            "A2": formula("A2", "=B1+0"),
            "A3": formula("A3", '=B1&"x"'),
            "A4": formula("A4", "=IF(TRUE,B1)"),
        }}],
    }
    calculated = calculate(hydrate(json.dumps(document).encode()))
    assert calculated.diagnostics == ()
    cells = calculated.sheet("S").cells
    assert {a: cells[a].formula.result for a in ("A1", "A2", "A3", "A4")} == {"A1": 0, "A2": 0, "A3": "x", "A4": 0}
    assert type(cells["A1"].formula.result) is int
    assert "B1" not in cells


def test_writing_an_unsupported_version_is_refused():
    import dataclasses

    workbook = hydrate(FIXTURE.read_bytes())
    with pytest.raises(ModelError) as caught:
        dataclasses.replace(workbook, schema_version=7).to_json()
    assert (caught.value.code, caught.value.message) == ("unsupported_schema_version", "7")
    with pytest.raises(ModelError) as caught:
        dataclasses.replace(workbook, model_version=9).to_dict()
    assert (caught.value.code, caught.value.message) == ("unsupported_model_version", "9")
    with pytest.raises(ModelError) as caught:
        dataclasses.replace(workbook, model_version=None).to_json()
    assert caught.value.code == "invalid_model"
    assert json.loads(workbook.to_json())["model_version"] == 1


def test_operating_scenario_calculation_keeps_versions_and_classifications():
    calculated = calculate(hydrate(FIXTURE.read_bytes()))
    assert calculated.schema_version == 1
    assert calculated.model_version == 1
    assert calculated.sheet("Forecast").cells["F2"].formula.result == 7400
    assert calculated.sheet("Forecast").cells["F3"].formula.result == 1440
    assert calculated.sheet("Forecast").cells["F4"].formula.result == pytest.approx(1000 / 12)
    assert calculated.sheet("Forecast").cells["F5"].formula.result == {"error": "#DIV/0!", "message": None}
    assert calculated.sheet("Forecast").cells["F2"].formula.dependencies == (
        "Forecast!C2",
        "Forecast!C3",
        "Forecast!C4",
    )
    assert calculated.sheet("Forecast").cells["C2"].formula.dependencies == (
        "Assumptions!B1",
        "Forecast!B2",
    )
    assert [(item.address, item.code, item.classification, item.function) for item in calculated.diagnostics] == [
        ("J1", "unsupported_formula", "unsupported", "NOW"),
        ("J2", "parse_error", "parse_error", None),
    ]
    jsonschema.validate(calculated.to_dict(), _schema())
    assert calculate(calculated).to_json() == calculated.to_json()
    # The authored cache is not the calculated result.
    stale = {
        "schema_version": 1,
        "model_version": 1,
        "metadata": {},
        "sheets": [
            {
                "name": "Data",
                "cells": {
                    "A1": {"address": "A1", "value": 1, "data_type": "number"},
                    "B1": {
                        "address": "B1",
                        "value": 999,
                        "data_type": "number",
                        "formula": {"expression": "=A1+1", "dependencies": [], "result": None},
                    },
                },
            }
        ],
    }
    fresh = calculate(hydrate(json.dumps(stale).encode()))
    assert fresh.sheet("Data").cells["B1"].value == 999
    assert fresh.sheet("Data").cells["B1"].formula.result == 2


def test_cycle_and_bad_binding_are_explicit():
    document = {
        "schema_version": 1,
        "model_version": 1,
        "metadata": {},
        "sheets": [
            {
                "name": "Data",
                "cells": {
                    "A1": {
                        "address": "A1",
                        "value": None,
                        "data_type": "blank",
                        "formula": {"expression": "=B1", "dependencies": [], "result": None},
                    },
                    "B1": {
                        "address": "B1",
                        "value": None,
                        "data_type": "blank",
                        "formula": {"expression": "=A1", "dependencies": [], "result": None},
                    },
                },
            }
        ],
    }
    cycled = calculate(hydrate(json.dumps(document).encode()))
    assert [item.classification for item in cycled.diagnostics] == ["cycle", "cycle"]
    document["bindings"] = {"inputs": {"price": {"sheet": "Data", "address": "A1"}}, "outputs": {}}
    with pytest.raises(ModelError) as caught:
        calculate(hydrate(json.dumps(document).encode()))
    assert caught.value.code == "invalid_model"
    assert caught.value.message == "input binding 'price' points at a formula cell"


def test_python_and_rust_semantic_diff_is_empty():
    raw = FIXTURE.read_bytes()
    hydrated = hydrate(raw).to_dict()
    calculated = calculate(hydrate(raw)).to_dict()
    rust_hydrated = _rust("hydrate")["workbook"]
    rust_calculated = _rust("calculate")["workbook"]
    assert _canonical(hydrated) == _canonical(rust_hydrated)
    assert _canonical(calculated) == _canonical(rust_calculated)
    assert calculated["schema_version"] == 1
    assert calculated["model_version"] == 1
    assert rust_calculated["bindings"]["outputs"]["revenue"] == {
        "sheet": "Forecast",
        "address": "F2",
    }


CASES = ROOT / "fixtures" / "operating-scenario-cases.json"


def _apply_inputs(document: dict, inputs: dict) -> bytes:
    """Rewrite named binding input cells in the canonical JSON (no adapter DTO)."""
    workbook = hydrate(json.dumps(document))
    assert workbook.bindings is not None
    sheets = {sheet["name"]: sheet for sheet in document["sheets"]}
    for name, value in inputs.items():
        target = workbook.bindings.inputs[name]
        cell = sheets[target.sheet]["cells"][target.address]
        assert cell.get("formula") is None, name
        cell["value"] = value
        cell["data_type"] = "number" if isinstance(value, (int, float)) and not isinstance(value, bool) else cell["data_type"]
    return json.dumps(document).encode("utf-8")


def _close(actual, expected, tolerance: float) -> bool:
    if isinstance(expected, dict) and "error" in expected:
        return actual == expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return abs(float(actual) - float(expected)) <= tolerance
    return actual == expected


def test_shared_golden_input_cases_match_python_and_rust():
    """Mac-lineage multi-case coverage on the Grok schema/bindings path."""
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    tolerance = float(cases["numeric_tolerance"])
    assert cases["model_fixture"] == FIXTURE.name

    for case in cases["cases"]:
        document = json.loads(json.dumps(fixture))
        raw = _apply_inputs(document, case["inputs"])
        calculated = calculate(hydrate(raw))
        assert calculated.bindings is not None
        for name, expected in case["expected"].items():
            target = calculated.bindings.outputs[name]
            cell = calculated.sheet(target.sheet).cells[target.address]
            actual = cell.formula.result if cell.formula is not None else cell.value
            assert _close(actual, expected, tolerance), (case["name"], name, actual, expected)

        # Rust must agree on the same rewritten bytes (values, errors, deps).
        # Write a temp sibling so the rust example can read a path.
        temp = ROOT / "fixtures" / f".tmp-{case['name']}.workbook.json"
        try:
            temp.write_bytes(raw)
            rust_calculated = _rust_path(temp, "calculate")["workbook"]
            py_dict = calculated.to_dict()
            assert _canonical(py_dict) == _canonical(rust_calculated), case["name"]
        finally:
            if temp.exists():
                temp.unlink()


def _rust_path(path: Path, mode: str) -> dict:
    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(ROOT / "rust" / "target")
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--manifest-path",
            str(ROOT / "rust" / "Cargo.toml"),
            "--locked",
            "--example",
            "canonical_calc",
            "--",
            str(path),
            mode,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)
