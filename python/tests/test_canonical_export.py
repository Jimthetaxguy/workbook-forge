"""Canonical fixture export, reimport, and the v1 spill refusal."""

import json
from pathlib import Path
import zipfile

import pytest

from workbook_forge.canonical_roundtrip import build_canonical_receipt
from workbook_forge.canonical_xlsx import (
    CanonicalPackageError,
    compare_structure,
    export_canonical,
    import_canonical,
)
from workbook_forge.model import calculate, hydrate, with_input

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "fixtures" / "operating-scenario.workbook.json"


def _sort_spill() -> dict:
    return {
        "schema_version": 1,
        "model_version": 1,
        "metadata": {},
        "sheets": [
            {
                "name": "Data",
                "cells": {
                    "A1": {"address": "A1", "value": 2, "data_type": "number"},
                    "A2": {"address": "A2", "value": 1, "data_type": "number"},
                    "B1": {
                        "address": "B1",
                        "value": None,
                        "data_type": "blank",
                        "formula": {
                            "expression": "=SORT(A1:A2)",
                            "dependencies": [],
                            "result": None,
                        },
                    },
                },
            }
        ],
    }


def test_canonical_fixture_exports_and_reimports_formula_text(tmp_path):
    original = hydrate(FIXTURE.read_bytes())
    path = export_canonical(original, tmp_path / "operating-scenario.exported.xlsx")
    imported = import_canonical(path)
    structure = compare_structure(original, imported)
    assert structure["formula_rewrites"] == []
    assert structure["mismatches"] == []
    assert structure["match"] is True
    assert imported.schema_version == 1
    assert imported.model_version == 1
    revenue = imported.sheet("Forecast").cells["F2"]
    assert revenue.formula.expression == "=SUM(C2:C4)"
    assert revenue.formula.result is None
    assert revenue.formula.dependencies == ()
    assert revenue.value == 7400
    assert imported.sheet("Assumptions").cells["B1"].value == 20
    assert imported.sheet("Assumptions").cells["B1"].number_format == "$#,##0.00;[Red]($#,##0.00)"
    assert imported.sheet("Assumptions").cells["C1"].value is True
    assert imported.sheet("Assumptions").cells["C3"].value == {"error": "#REF!", "message": None}
    restored = calculate(imported)
    assert restored.bindings is None
    assert restored.sheet("Forecast").cells["F2"].formula.result == 7400
    assert restored.sheet("Forecast").cells["F3"].formula.result == 1440
    assert restored.sheet("Forecast").cells["F4"].formula.result == pytest.approx(1000 / 12)
    assert restored.sheet("Forecast").cells["F5"].formula.result == {"error": "#DIV/0!", "message": None}
    assert restored.sheet("Forecast").cells["F2"].value == 7400
    assert [(item.address, item.classification) for item in restored.diagnostics] == [
        ("J1", "unsupported"),
        ("J2", "parse_error"),
    ]
    assert imported.sheet("Forecast").cells["J1"].formula.expression == "=NOW()"
    assert imported.sheet("Forecast").cells["J2"].formula.expression == "=1+"
    assert imported.sheet("Forecast").cells["F5"].value == {"error": "#DIV/0!", "message": None}


def test_reimported_fixture_matches_python_calculation_when_bindings_return(tmp_path):
    original = hydrate(FIXTURE.read_bytes())
    path = export_canonical(original, tmp_path / "scenario.xlsx")
    imported = import_canonical(path)
    rebound = hydrate(original.to_json())
    # The package does not store bindings. Calculation uses the fixture bytes
    # plus the reimported formula text, checked above. Here the edited input
    # is applied to the fixture model.
    edited = with_input(rebound, "unit_price", 25)
    calculated = calculate(edited)
    assert calculated.schema_version == 1
    assert calculated.model_version == 1
    assert calculated.sheet("Forecast").cells["F2"].formula.result == 9250
    assert calculated.sheet("Forecast").cells["F3"].formula.result == 3290
    assert calculated.sheet("Forecast").cells["F4"].formula.result == pytest.approx(1000 / 17)
    assert calculated.sheet("Forecast").cells["F5"].formula.result == {"error": "#DIV/0!", "message": None}
    assert [(item.address, item.code, item.classification) for item in calculated.diagnostics] == [
        ("J1", "unsupported_formula", "unsupported"),
        ("J2", "parse_error", "parse_error"),
    ]
    assert imported.sheet("Forecast").cells["F2"].formula.expression == rebound.sheet("Forecast").cells["F2"].formula.expression


def test_formula_text_change_is_an_explicit_rewrite(tmp_path):
    original = hydrate(FIXTURE.read_bytes())
    path = export_canonical(original, tmp_path / "scenario.xlsx")
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("xl/worksheets/sheet2.xml")
        parts = {name: archive.read(name) for name in archive.namelist()}
    assert b"<f>SUM(C2:C4)</f>" in xml
    parts["xl/worksheets/sheet2.xml"] = xml.replace(
        b"<f>SUM(C2:C4)</f>",
        b"<f>SUM(C2:C4)+0</f>",
        1,
    )
    rewritten = tmp_path / "rewritten.xlsx"
    with zipfile.ZipFile(rewritten, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    imported = import_canonical(rewritten)
    structure = compare_structure(original, imported)
    assert structure["match"] is False
    assert structure["formula_rewrites"] == [
        {
            "sheet": "Forecast",
            "address": "F2",
            "exported": "=SUM(C2:C4)",
            "reimported": "=SUM(C2:C4)+0",
        }
    ]


def test_array_spill_export_refuses_and_writes_nothing(tmp_path):
    destination = tmp_path / "spill.xlsx"
    with pytest.raises(CanonicalPackageError) as caught:
        export_canonical(hydrate(json.dumps(_sort_spill()).encode()), destination)
    assert caught.value.code == "array_spill_refused"
    assert "Data!B1" in caught.value.message
    assert "SORT" in caught.value.message
    assert "spill cells are not written" in caught.value.message
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_array_spill_import_refuses_and_returns_no_model(tmp_path):
    source = export_canonical(hydrate(json.dumps({
        "schema_version": 1,
        "model_version": 1,
        "metadata": {},
        "sheets": [{
            "name": "Data",
            "cells": {
                "A1": {"address": "A1", "value": 1, "data_type": "number"},
                "B1": {
                    "address": "B1",
                    "value": None,
                    "data_type": "blank",
                    "formula": {"expression": "=A1+1", "dependencies": [], "result": None},
                },
            },
        }],
    }).encode()), tmp_path / "scalar.xlsx")
    with zipfile.ZipFile(source) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
        b"<f>A1+1</f>",
        b'<f t="array" ref="B1:B2">A1:A2</f>',
    )
    spilled = tmp_path / "spill.xlsx"
    with zipfile.ZipFile(spilled, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    with pytest.raises(CanonicalPackageError) as caught:
        import_canonical(spilled)
    assert caught.value.code == "array_spill_refused"
    assert "Data!B1" in caught.value.message
    assert 't="array"' in caught.value.message
    assert "not read into the model" in caught.value.message


def test_receipt_records_pending_excel_and_refused_spill(tmp_path):
    receipt = build_canonical_receipt(FIXTURE, tmp_path / "receipt", rust=True)
    assert receipt["harness"] == "excel_export_roundtrip_v1"
    assert receipt["spec2_passed"] is False
    assert receipt["array_spill"]["status"] == "blocked"
    assert receipt["array_spill"]["gate"] == "not_passed"
    assert receipt["array_spill"]["blocker_kind"] == "unsupported_capability"
    assert receipt["array_spill"]["capability"] == "worksheet_dynamic_array_spill_placement"
    assert "passed" not in receipt["array_spill"]["status"]
    assert receipt["scenario_roundtrip"]["status"] == "prepared"
    assert receipt["scenario_roundtrip"]["excel_desktop"] == "pending"
    assert receipt["scenario_roundtrip"]["formula_diffs"] == []
    assert "--excel" in receipt["desktop_command"]
    assert receipt["red_flag_roundtrip"]["status"] == "blocked"
    assert receipt["red_flag_roundtrip"]["blocker_kind"] == "unsupported_capability"
    assert receipt["red_flag_roundtrip"]["preflight_skipped"] is True
    assert receipt["structural_reimport"]["match"] is True
    assert receipt["structural_reimport"]["formula_rewrites"] == []
    assert receipt["engines"]["before_input_change"]["python_rust_match"] is True
    preview = receipt["engines"]["after_input_change_preview"]
    assert preview["python_rust_match"] is True
    assert preview["source"] == "python_and_rust_not_excel"
    assert preview["outputs"]["revenue"]["result"] == 9250
    assert preview["outputs"]["profit"]["result"] == 3290
    assert preview["outputs"]["break_even_units"]["result"] == pytest.approx(1000 / 17)
    assert receipt["input_change"] == {
        "name": "unit_price",
        "sheet": "Assumptions",
        "address": "B1",
        "before": 20,
        "after": 25,
    }
    assert receipt["agent_headless"]["status"] == "not_started"
    assert (tmp_path / "receipt" / "receipt.json").is_file()
    assert (tmp_path / "receipt" / "receipt.md").is_file()
    assert (tmp_path / "receipt" / "operating-scenario.exported.xlsx").is_file()
    assert (tmp_path / "receipt" / "scenario-roundtrip.json").is_file()
    assert (tmp_path / "receipt" / "red-flag-roundtrip.json").is_file()
    assert not (tmp_path / "receipt" / "scenario").exists()
    text = (tmp_path / "receipt" / "receipt.md").read_text(encoding="utf-8")
    assert "Spec 2 passed: `false`" in text
    assert "unit_price" in text
    assert "Excel formula rewrites" in text
    assert "not_passed" in text
