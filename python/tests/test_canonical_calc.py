"""Parity tests for calculation sessions over the canonical Workbook model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from workbook_forge.canonical_calc import CalculationError, CanonicalCalculationSession
from workbook_forge.model import Workbook

ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / "tests/fixtures/canonical/operating-scenario-v1.json"
CASES_PATH = ROOT / "tests/fixtures/canonical/operating-scenario-cases.json"
SCHEMA_PATH = ROOT / "schemas/workbook-model-v1.schema.json"
MODEL_BYTES = MODEL_PATH.read_bytes()
CASES = json.loads(CASES_PATH.read_text(encoding="utf-8"))


def assert_result_close(actual, expected, tolerance: float, path: str) -> None:
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        assert isinstance(actual, (int, float)) and not isinstance(actual, bool), path
        assert abs(actual - expected) <= tolerance, path
    else:
        assert actual == expected, path


def load_workbook(data: bytes = MODEL_BYTES) -> Workbook:
    return Workbook.from_bytes(data)


def run_case(case: dict) -> dict:
    session = CanonicalCalculationSession(load_workbook())
    session.set_inputs(case["inputs"])
    return session.calculate().to_dict()


def test_shared_fixture_matches_schema_and_native_round_trip_meaning():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(json.loads(MODEL_BYTES))

    workbook = load_workbook()
    assert json.loads(workbook.to_bytes()) == json.loads(MODEL_BYTES)
    rehydrated = Workbook.from_bytes(workbook.to_bytes())
    assert json.loads(rehydrated.to_bytes()) == json.loads(MODEL_BYTES)
    assert {
        binding.name: (binding.sheet, binding.address)
        for binding in rehydrated.bindings
        if binding.direction == "input"
    } == {
        "unit_price": ("Assumptions", "B1"),
        "unit_cost": ("Assumptions", "B2"),
        "fixed_cost": ("Assumptions", "B3"),
    }
    assert {
        binding.name: (binding.sheet, binding.address)
        for binding in rehydrated.bindings
        if binding.direction == "output"
    } == {
        "revenue": ("Forecast", "F2"),
        "profit": ("Forecast", "F3"),
        "break_even_units": ("Forecast", "F4"),
    }
    formula_cell = rehydrated.sheet("Forecast").cells["F2"]
    assert formula_cell.value is None
    assert formula_cell.formula.cached_value == 7400


def test_shared_golden_cases_use_fresh_results_and_typed_excel_errors():
    assert CASES["model_fixture"] == MODEL_PATH.name
    for case in CASES["cases"]:
        report = run_case(case)
        assert report["backend"] == "python"
        assert report["schema_version"] == 1
        assert report["model_version"] == 1
        assert report["revision"] == 1
        assert report["diagnostics"] == []
        assert set(report["outputs"]) == set(case["expected"])
        for name, expected in case["expected"].items():
            assert_result_close(
                report["outputs"][name], expected, CASES["numeric_tolerance"], name
            )
    assert run_case(CASES["cases"][2])["outputs"]["break_even_units"] == {"error": "#N/A"}


def test_imported_formula_cache_is_not_overwritten_or_reported_as_fresh_calculation():
    source = load_workbook()
    session = CanonicalCalculationSession(source)
    session.set_inputs({"unit_price": 25})
    report = session.calculate().to_dict()

    assert report["outputs"]["revenue"] == 9250
    assert report["values"]["Forecast!F2"] == 9250
    assert source.sheet("Assumptions").cells["B1"].value == 20
    updated = session.workbook
    assert updated.sheet("Assumptions").cells["B1"].value == 25
    assert updated.sheet("Forecast").cells["F2"].value is None
    assert updated.sheet("Forecast").cells["F2"].formula.cached_value == 7400


@pytest.mark.parametrize(
    ("inputs", "code"),
    [
        ({"unknown": 1}, "unknown_input"),
        ({"unit_price": "20"}, "invalid_input"),
        ({"unit_price": None}, "invalid_input"),
        ({"unit_price": -1}, "invalid_input"),
        ({"unit_price": 10**10000}, "invalid_input"),
    ],
)
def test_input_refusals_do_not_change_session(inputs, code):
    session = CanonicalCalculationSession(load_workbook())
    before = session.workbook.to_bytes()
    with pytest.raises(CalculationError) as error:
        session.set_inputs(inputs)
    assert error.value.code == code
    assert session.revision == 0
    assert session.workbook.to_bytes() == before


def test_rejected_batch_is_atomic_and_revision_conflicts_are_explicit():
    session = CanonicalCalculationSession(load_workbook())
    before = session.workbook.to_bytes()
    with pytest.raises(CalculationError, match="invalid_input"):
        session.set_inputs({"unit_price": 25, "unit_cost": "bad"})
    assert session.revision == 0
    assert session.workbook.to_bytes() == before
    with pytest.raises(CalculationError) as error:
        session.set_inputs({"unit_price": 25}, expected_revision=2)
    assert error.value.code == "revision_conflict"


def test_unsupported_formula_and_dependency_cycle_are_refused_not_guessed():
    document = json.loads(MODEL_BYTES)
    document["sheets"][1]["cells"]["F2"]["formula"]["expression"] = "=MYSTERY(1)"
    document["sheets"][1]["cells"]["F2"]["formula"]["dependencies"] = []
    unsupported = CanonicalCalculationSession(load_workbook(json.dumps(document).encode()))
    unsupported_report = unsupported.calculate().to_dict()
    assert "revenue" not in unsupported_report["outputs"]
    assert any(item["code"] == "unsupported_formula" for item in unsupported_report["diagnostics"])

    document = json.loads(MODEL_BYTES)
    for address, expression, dependency in (
        ("C2", "=D2+1", "Forecast!D2"),
        ("D2", "=C2+1", "Forecast!C2"),
    ):
        formula = document["sheets"][1]["cells"][address]["formula"]
        formula["expression"] = expression
        formula["dependencies"] = [dependency]
        formula["cached_value"] = 999
    cyclic = CanonicalCalculationSession(load_workbook(json.dumps(document).encode()))
    cycle_report = cyclic.calculate().to_dict()
    assert "revenue" not in cycle_report["outputs"]
    assert any(item["code"] == "dependency_cycle" for item in cycle_report["diagnostics"])
