"""Exercise public Python toolkit and command-line entry points."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from workbook_forge.catalog import workbook_capabilities


def test_capability_map_distinguishes_backends_and_preserved_content():
    catalog = workbook_capabilities()
    assert set(catalog["backends"]) == {"python-reference", "python", "rust", "python-native", "python-ooxml", "rust-ooxml"}
    primitives = {entry["id"]: entry for entry in catalog["primitives"]}
    assert primitives["arrays"]["calculate"] and not primitives["arrays"]["export"]
    assert primitives["names-and-tables"]["read"] and not primitives["names-and-tables"]["calculate"]


def test_python_expression_builder_preserves_copy_anchors():
    from workbook_forge.expressions import CellReference, Expression

    expression = Expression.reference(CellReference(1, 1, column_absolute=True))
    expression += Expression.reference(CellReference(2, 2, row_absolute=True))
    expression += Expression.reference(CellReference(3, 3, "Rates Sheet", True, True))
    copied = expression.copy(rows=1, columns=1)
    from workbook_forge import evaluate

    assert evaluate(copied.formula, {"A2": 10, "C2": 20, "Rates Sheet!C3": 30}) == 60
    assert "$A2" in copied.formula and "C$2" in copied.formula and "$C$3" in copied.formula
    assert Expression.literal('say "hello"').source == '"say ""hello"""'
    with pytest.raises(ValueError, match="bounds"):
        CellReference(0, 1)
    with pytest.raises(ValueError):
        Expression.literal(float("inf"))


def test_python_model_scenario_and_detached_snapshots():
    from workbook_forge.toolkit import operating_scenario

    model = operating_scenario()
    initial = model.calculate()
    assert initial["diagnostics"] == []
    assert initial["outputs"]["revenue"] == 7400
    assert initial["outputs"]["profit"] == 1440
    assert initial["outputs"]["break_even_units"] == pytest.approx(1000 / 12)
    snapshot = model.to_dict()
    snapshot["sheets"][0]["cells"]["B1"]["value"] = -500
    assert model.to_dict()["sheets"][0]["cells"]["B1"]["value"] == 20
    model.set_inputs({"unit_price": 25})
    updated = model.calculate(workers=2)
    assert updated["outputs"]["revenue"] == 9250
    assert updated["outputs"]["profit"] == 3290
    assert updated["revision"] > initial["revision"]


def test_python_inputs_are_atomic_and_errors_cross_boundary():
    from workbook_forge.toolkit import operating_scenario

    model = operating_scenario()
    before = model.to_dict()
    with pytest.raises((ValueError, RuntimeError)):
        model.set_inputs({"unit_price": 25, "unit_cost": -1})
    assert model.to_dict() == before
    with pytest.raises((ValueError, RuntimeError)):
        model.set_inputs({"unit_price": True})
    assert model.to_dict() == before


def test_cli_runs_and_reopens_real_excel_output(tmp_path):
    from workbook_forge.xlsx import import_xlsx
    from workbook_forge.toolkit import operating_scenario

    output = tmp_path / "scenario.xlsx"
    result = subprocess.run(
        [sys.executable, "-m", "workbook_forge.cli", "scenario", "--inputs", '{"unit_price":25}', "--xlsx", str(output)],
        text=True, capture_output=True, check=True,
    )
    report = json.loads(result.stdout)
    assert report["outputs"]["profit"] == 3290
    bindings = operating_scenario().to_dict()
    imported = import_xlsx(output, inputs=bindings["inputs"], outputs=bindings["outputs"])
    assert imported.calculate()["outputs"] == report["outputs"]
    again = subprocess.run(
        [sys.executable, "-m", "workbook_forge.cli", "scenario", "--xlsx", str(output)],
        text=True, capture_output=True,
    )
    assert again.returncode != 0
    assert output.is_file()


def test_cli_rejects_invalid_input_without_an_output(tmp_path):
    output = tmp_path / "invalid.xlsx"
    result = subprocess.run(
        [sys.executable, "-m", "workbook_forge.cli", "scenario", "--inputs", '{"unit_price":true}', "--xlsx", str(output)],
        text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert not output.exists()
    assert json.loads(result.stderr)["error"]
