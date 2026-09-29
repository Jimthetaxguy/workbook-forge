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
