"""Real native model plus bounded OOXML integration tests (no fallback engine)."""

from __future__ import annotations

import importlib.util
import zipfile
from xml.etree import ElementTree as ET

import pytest

from workbook_forge import evaluate
from workbook_forge.toolkit import NativeUnavailableError, WorkbookModel, operating_scenario
from workbook_forge.workbook import MAIN, UnsupportedWorkbook, Workbook, WorkbookError
from workbook_forge.xlsx import export_xlsx, import_xlsx
from test_workbook import make_xlsx, make_table_xlsx, write_parts

native = pytest.mark.skipif(importlib.util.find_spec("workbook_forge._native") is None, reason="native extension must be built for toolkit integration")


def test_missing_native_is_explicit_and_legacy_api_stays_available(monkeypatch):
    from workbook_forge import toolkit

    def missing(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(toolkit.importlib, "import_module", missing)
    with pytest.raises(NativeUnavailableError, match="native Workbook Forge wheel"):
        WorkbookModel()
    assert evaluate("=SUM(1,2,3)") == 6


@native
def test_scenario_native_generation_edit_and_reimport(tmp_path):
    model = operating_scenario()
    initial = model.calculate()
    assert initial["diagnostics"] == []
    assert initial["outputs"] == pytest.approx({"revenue": 7400, "profit": 1440, "break_even_units": 1000 / 12})
    path = export_xlsx(model, tmp_path / "scenario.xlsx", report=initial)
    document = model.to_dict()
    with zipfile.ZipFile(path) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        validations = sheet.find(f"{{{MAIN}}}dataValidations")
        assert validations.attrib["count"] == "3"
        assert all(node.attrib["errorStyle"] == "stop" for node in validations)
        assert any("ISNUMBER(B1)" in node[0].text for node in validations)
        assert b"Arial" in archive.read("xl/styles.xml")
        assert b"fullCalcOnLoad" in archive.read("xl/workbook.xml")
    extracted = import_xlsx(path, inputs=document["inputs"], outputs=document["outputs"])
    assert extracted.calculate()["outputs"] == initial["outputs"]
    snapshot = extracted.to_dict()
    forecast = snapshot["sheets"][1]["cells"]
    assert forecast["C2"]["value"] is None
    assert forecast["C2"]["cached_value"] == 2000
    assert forecast["C3"]["formula"].startswith("(") or "B3" in forecast["C3"]["formula"]
    assert snapshot["sheets"][0]["cells"]["B1"]["style"]["fill_color"] == "FFE8F2FF"
    extracted.set_inputs({"unit_price": 25})
    edited = export_xlsx(extracted, tmp_path / "edited.xlsx")
    round_trip = import_xlsx(edited, inputs=document["inputs"], outputs=document["outputs"])
    assert round_trip.calculate()["outputs"]["revenue"] == 9250
    assert round_trip.inspect()["xlsx"]["date_system"] == "1900"
    with pytest.raises(FileExistsError):
        export_xlsx(extracted, edited)


@native
def test_snapshot_ownership_formula_copy_and_atomic_validation():
    model = WorkbookModel(["Input Data", "Result"])
    model.apply([
        {"sheet": "Input Data", "address": "A1", "value": 5},
        {"sheet": "Input Data", "address": "A2", "value": 7},
        {"sheet": "Result", "address": "B1", "formula": "='Input Data'!A1+'Input Data'!$A$1"},
    ])
    model.copy_formula("Result", "B1", "B2")
    assert model.calculate()["values"]["Result!B2"] == 12
    snapshot = model.to_dict()
    snapshot["sheets"][0]["cells"]["A1"]["value"] = 999
    assert model.calculate()["values"]["Result!B1"] == 10
    revision = model.revision
    with pytest.raises(ValueError):
        model.apply([
            {"sheet": "Input Data", "address": "A1", "value": 8},
            {"sheet": "missing", "address": "A1", "value": 1},
        ])
    assert model.revision == revision
    assert model.to_dict()["sheets"][0]["cells"]["A1"]["value"] == 5
    model.set_value("Input Data", "A1", None)
    assert model.to_dict()["sheets"][0]["cells"]["A1"]["value"] is None


@native
def test_import_preserves_opaque_content_and_immutable_source(tmp_path):
    source = tmp_path / "import.xlsx"
    parts = make_xlsx(source)
    parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(b"</row>", b'<c r="C1"><f>UNKNOWN_FUNCTION(1)</f><v>123</v></c></row>')
    write_parts(source, parts)
    outputs = {"supported": {"sheet": "Sheet1", "address": "B1"}}
    model = import_xlsx(source, outputs=outputs)
    assert model.to_dict()["sheets"][0]["cells"]["C1"]["value"] is None
    assert model.calculate()["outputs"] == {"supported": 3}
    # Replacing the on-disk input after import must not alter this model's baseline.
    source.write_bytes(b"this input path has changed")
    model.set_value("Sheet1", "A1", "edited")
    output = export_xlsx(model, tmp_path / "preserved.xlsx")
    with zipfile.ZipFile(output) as archive:
        assert archive.read("custom/custom.bin") == parts["custom/custom.bin"]
        for name in ("[Content_Types].xml", "_rels/.rels", "xl/_rels/workbook.xml.rels"):
            assert archive.read(name) == parts[name]
    with Workbook.open(output) as workbook:
        assert workbook.get("Sheet1", "C1").formula == "UNKNOWN_FUNCTION(1)"
        assert workbook.get("Sheet1", "C1").value == 123
        assert workbook.get("Sheet1", "A1").value == "edited"
    blocked = import_xlsx(output, outputs={"unsupported": {"sheet": "Sheet1", "address": "C1"}})
    assert blocked.calculate()["diagnostics"]
    rejected = tmp_path / "rejected.xlsx"
    with pytest.raises(UnsupportedWorkbook, match="diagnostics"):
        export_xlsx(blocked, rejected)
    assert not rejected.exists()


@native
@pytest.mark.parametrize("kind", ["array", "table"])
def test_nonformula_result_cells_never_become_authored_inputs(tmp_path, kind):
    source = tmp_path / "special.xlsx"
    if kind == "array":
        xml = f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1"><f t="array" ref="A1:A2">1</f><v>99</v></c><c r="B1"><f>A2+1</f><v>100</v></c></row><row r="2"><c r="A2"><v>99</v></c></row></sheetData></worksheet>'.encode()
        make_xlsx(source, {"xl/worksheets/sheet1.xml": xml})
        output_address, blocked_address = "B1", "A2"
    else:
        xml = f'<worksheet xmlns="{MAIN}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Input</t></is></c><c r="B1" t="inlineStr"><is><t>Calculated</t></is></c><c r="C1"><f>B2+1</f><v>100</v></c></row><row r="2"><c r="A2"><v>1</v></c><c r="B2"><v>99</v></c></row></sheetData></worksheet>'.encode()
        make_table_xlsx(source, xml, 'ref="A1:B2" totalsRowShown="0"', '<tableColumn id="2" name="Calculated"><calculatedColumnFormula>[@Input]+1</calculatedColumnFormula></tableColumn>')
        output_address, blocked_address = "C1", "B2"
    model = import_xlsx(source, outputs={"output": {"sheet": "Sheet1", "address": output_address}})
    blocked = model.to_dict()["sheets"][0]["cells"][blocked_address]
    assert blocked["value"] is None and blocked["cached_value"] == 99
    assert blocked["blocked_reason"]
    assert model.calculate()["diagnostics"]
    with pytest.raises(ValueError, match="preserved-only"):
        model.set_value("Sheet1", blocked_address, 2)
    with pytest.raises(UnsupportedWorkbook):
        export_xlsx(model, tmp_path / "blocked.xlsx")
    assert not (tmp_path / "blocked.xlsx").exists()


@native
def test_stale_and_foreign_reports_and_imported_style_edits_fail(tmp_path):
    model = operating_scenario()
    report = model.calculate()
    model.set_inputs({"unit_price": 21})
    with pytest.raises(WorkbookError, match="stale"):
        export_xlsx(model, tmp_path / "stale.xlsx", report=report)
    other = operating_scenario()
    other.set_inputs({"unit_price": 22})
    assert model.revision == other.revision
    with pytest.raises(WorkbookError, match="different model"):
        export_xlsx(other, tmp_path / "foreign.xlsx", report=model.calculate())
    path = export_xlsx(model, tmp_path / "source.xlsx")
    extracted = import_xlsx(path, inputs=model.to_dict()["inputs"], outputs=model.to_dict()["outputs"])
    extracted.set_style("Assumptions", "B1", bold=True)
    with pytest.raises(UnsupportedWorkbook, match="style"):
        export_xlsx(extracted, tmp_path / "style.xlsx")
    assert not (tmp_path / "style.xlsx").exists()


@native
def test_1904_formula_caches_are_preserved_but_not_calculated(tmp_path):
    source = tmp_path / "date1904.xlsx"
    parts = make_xlsx(source)
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(b"<sheets>", b'<workbookPr date1904="1"/><sheets>')
    write_parts(source, parts)
    model = import_xlsx(source, outputs={"number": {"sheet": "Sheet1", "address": "B1"}})
    assert model.inspect()["xlsx"]["date_system"] == "1904"
    assert model.calculate()["diagnostics"]


@native
def test_new_input_constraints_cannot_silently_replace_imported_validation(tmp_path):
    source = tmp_path / "source.xlsx"
    make_xlsx(source)
    model = import_xlsx(
        source,
        inputs={"label": {"sheet": "Sheet1", "address": "A1", "kind": "text"}},
        outputs={"result": {"sheet": "Sheet1", "address": "B1"}},
    )
    assert model.calculate()["outputs"] == {"result": 3}
    with pytest.raises(UnsupportedWorkbook, match="do not match preserved Excel validation"):
        export_xlsx(model, tmp_path / "unvalidated.xlsx")
    assert not (tmp_path / "unvalidated.xlsx").exists()


@native
def test_input_constraints_are_native_and_generate_explicit_excel_rules(tmp_path):
    document = {
        "sheets": [{"id": "inputs", "name": "Inputs", "cells": {
            "A1": {"value": 4}, "B1": {"value": "Base"}, "C1": {"value": True},
        }}],
        "inputs": {
            "quantity": {"sheet": "Inputs", "address": "A1", "kind": "number", "min": 1, "max": 10},
            "scenario": {"sheet": "Inputs", "address": "B1", "kind": "text", "choices": ["Base", "Upside"]},
            "enabled": {"sheet": "Inputs", "address": "C1", "kind": "boolean", "required": False},
        },
        "outputs": {"result": {"sheet": "Inputs", "address": "A1"}},
    }
    model = WorkbookModel(document=document)
    for invalid in ({"quantity": 11}, {"quantity": "4"}, {"scenario": "base"}, {"enabled": 1}):
        with pytest.raises(ValueError):
            model.set_inputs(invalid)
    assert model.revision == 0
    model.set_inputs({"enabled": None})
    path = export_xlsx(model, tmp_path / "validation.xlsx")
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        rules = {node.attrib["sqref"]: node.find(f"{{{MAIN}}}formula1").text for node in root.findall(f"{{{MAIN}}}dataValidations/{{{MAIN}}}dataValidation")}
    assert rules["A1"] == "AND(ISNUMBER(A1),A1>=1.0,A1<=10.0)"
    assert 'EXACT(B1,"Base")' in rules["B1"]
    assert rules["C1"] == "OR(ISBLANK(C1),AND(ISLOGICAL(C1)))"
    imported = import_xlsx(path, inputs=document["inputs"], outputs=document["outputs"])
    export_xlsx(imported, tmp_path / "validation-roundtrip.xlsx")


@native
def test_scalar_error_caches_roundtrip_but_array_spills_refuse_output(tmp_path):
    model = WorkbookModel(document={
        "sheets": [{"id": "one", "name": "Sheet1", "cells": {
            "A1": {"formula": "=1/0"},
        }}],
        "outputs": {"error": {"sheet": "Sheet1", "address": "A1"}},
    })
    path = export_xlsx(model, tmp_path / "error.xlsx")
    with Workbook.open(path) as workbook:
        assert workbook.get("Sheet1", "A1").value.code == "#DIV/0!"
    model.apply([
        {"sheet": "Sheet1", "address": "A2", "value": 2},
        {"sheet": "Sheet1", "address": "A3", "value": 1},
        {"sheet": "Sheet1", "address": "A1", "formula": "=SORT(A2:A3)"},
    ])
    destination = tmp_path / "spill.xlsx"
    with pytest.raises(UnsupportedWorkbook, match="array spill"):
        export_xlsx(model, destination)
    assert not destination.exists()


@native
def test_unrelated_unsupported_error_values_and_caches_are_preserved(tmp_path):
    source = tmp_path / "unsupported-errors.xlsx"
    parts = make_xlsx(source)
    parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
        b"</row>",
        b'<c r="C1" t="e"><v>#SPILL!</v></c>'
        b'<c r="D1" t="e"><f>UNKNOWN_FUNCTION(1)</f><v>#NULL!</v></c>'
        b'<c r="E1"><f>C1+1</f><v>0</v></c></row>',
    )
    write_parts(source, parts)
    model = import_xlsx(source, outputs={"supported": {"sheet": "Sheet1", "address": "B1"}})
    assert model.calculate()["outputs"] == {"supported": 3}
    cells = model.to_dict()["sheets"][0]["cells"]
    assert cells["C1"]["value"] == {"error": "#SPILL!"}
    assert cells["D1"]["cached_value"] == {"error": "#NULL!"}
    path = export_xlsx(model, tmp_path / "preserved-errors.xlsx")
    with Workbook.open(path) as workbook:
        assert workbook.get("Sheet1", "C1").value.code == "#SPILL!"
        assert workbook.get("Sheet1", "D1").value.code == "#NULL!"
    connected = import_xlsx(path, outputs={"dependent": {"sheet": "Sheet1", "address": "E1"}})
    assert connected.calculate()["diagnostics"]
    rejected = tmp_path / "unsupported-error-dependency.xlsx"
    with pytest.raises(UnsupportedWorkbook):
        export_xlsx(connected, rejected)
    assert not rejected.exists()


@native
def test_imported_1904_policy_blocks_new_and_replacement_formulas_atomically(tmp_path):
    source = tmp_path / "date1904-input.xlsx"
    parts = make_xlsx(source)
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(
        b"<sheets>", b'<workbookPr date1904="1"/><sheets>'
    )
    write_parts(source, parts)
    model = import_xlsx(source, outputs={"label": {"sheet": "Sheet1", "address": "A1"}})
    before = model.to_dict()
    for address in ("A1", "B1", "C1"):
        with pytest.raises(UnsupportedWorkbook, match="1904"):
            model.set_formula("Sheet1", address, "=DATE(2024,1,1)")
        assert model.to_dict() == before
    with pytest.raises(UnsupportedWorkbook, match="1904"):
        model.apply([
            {"sheet": "Sheet1", "address": "A1", "value": "changed"},
            {"sheet": "Sheet1", "address": "C1", "formula": "=DATE(2024,1,1)"},
        ])
    assert model.to_dict() == before
    assert model.calculate()["outputs"] == {"label": "hello"}
    preserved = export_xlsx(model, tmp_path / "date1904-preserved.xlsx")
    with Workbook.open(preserved) as workbook:
        assert workbook._uses_1904_date_system
        assert workbook.get("Sheet1", "B1").formula == "1+2"
        assert workbook.get("Sheet1", "B1").value == 3
    # Independently defend the export boundary if a supplied report attempts
    # to introduce caches outside the supported output closure.
    report = model.calculate()
    report["values"]["Sheet1!B1"] = 45292
    with pytest.raises((UnsupportedWorkbook, WorkbookError)):
        export_xlsx(model, tmp_path / "date1904-wrong-cache.xlsx", report=report)
    assert not (tmp_path / "date1904-wrong-cache.xlsx").exists()


@native
@pytest.mark.parametrize("mutation", ["change", "delete", "add"])
def test_caller_owned_reports_cannot_authorize_wrong_formula_caches(tmp_path, mutation):
    model = operating_scenario()
    report = model.calculate()
    if mutation == "change":
        report["values"]["Forecast!F2"] = 123
    elif mutation == "delete":
        del report["values"]["Forecast!F2"]
    else:
        report["values"]["Forecast!Z100"] = 123
    destination = tmp_path / f"tampered-{mutation}.xlsx"
    with pytest.raises(WorkbookError, match="authoritative native results"):
        export_xlsx(model, destination, report=report)
    assert not destination.exists()
    assert model.calculate()["outputs"]["revenue"] == 7400
