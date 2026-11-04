"""Defined names: the shared fixture in every Python engine, plus xlsx import,
export and the edit path. Rust runs the same fixture in rust/tests/defined_names.rs."""

from __future__ import annotations

import dataclasses
import importlib
import json
import math
import zipfile
from pathlib import Path

import pytest

from workbook_forge import DefinedNames, evaluate
from workbook_forge.model import Cell, Formula, Sheet, Workbook, calculate

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "fixtures/defined-names.v1.json").read_text(encoding="utf-8"))
CORPUS = ROOT / "fixtures/corpus"
CANONICAL = {
    "name_cycle": ("cycle", "cycle"),
    "resource_limit": ("resource_limit", "resource_limit"),
    "parse_error": ("parse_error", "parse_error"),
    "unsupported_formula": ("unsupported_formula", "unsupported"),
    "invalid_reference": ("invalid_reference", "invalid_reference"),
}


def _column(index: int) -> str:
    letters = ""
    while index:
        index, digit = divmod(index - 1, 26)
        letters = chr(65 + digit) + letters
    return letters


CASE_CELLS = {
    case["id"]: (case["sheet"], f"{_column(index)}10")
    for index, case in enumerate(FIXTURE["cases"], 1)
}


def _toolkit_document() -> dict:
    sheets = {name: {"id": name, "name": name, "cells": {}, "column_widths": {}} for name in FIXTURE["sheets"]}
    for key, value in FIXTURE["cells"].items():
        sheet, address = key.split("!")
        sheets[sheet]["cells"][address] = {"value": value}
    for case in FIXTURE["cases"]:
        sheet, address = CASE_CELLS[case["id"]]
        sheets[sheet]["cells"][address] = {"formula": case["formula"]}
    return {"schema_version": 1, "revision": 0, "sheets": list(sheets.values()), "inputs": {}, "outputs": {}}


def _same(actual, expected) -> bool:
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isclose(actual, expected, rel_tol=1e-12)
    return actual == expected


def _backends() -> list[str]:
    backends = ["python"]
    try:
        importlib.import_module("workbook_forge._native")
        backends.append("rust")
    except ImportError:
        pass
    return backends


def _check_toolkit(report: dict, cases: list[dict]) -> list[str]:
    diagnostics = {(item.get("sheet"), item.get("address")): item for item in report["diagnostics"]}
    problems = []
    for case in cases:
        sheet, address = CASE_CELLS[case["id"]]
        key, expected = f"{sheet}!{address}", case["expected"]
        if "code" in expected:
            got = diagnostics.get((sheet, address))
            if got is None or got["code"] != expected["code"] or (
                "message" in expected and got["message"] != expected["message"]
            ):
                problems.append(f"{case['id']}: {got} != {expected}")
        elif "error" in expected:
            if report["values"].get(key) != {"error": expected["error"]}:
                problems.append(f"{case['id']}: {report['values'].get(key)} != {expected}")
        elif not _same(report["values"].get(key), expected["value"]):
            problems.append(f"{case['id']}: {report['values'].get(key)!r} != {expected['value']!r}")
    return problems


@pytest.mark.parametrize("backend", _backends())
def test_shared_fixture_in_the_toolkit_engines(backend):
    engine = importlib.import_module("workbook_forge.python_engine") if backend == "python" else importlib.import_module("workbook_forge._native")
    session = engine.Session(json.dumps(_toolkit_document()), json.dumps(FIXTURE["names"]))
    assert _check_toolkit(json.loads(session.calculate(1)), FIXTURE["cases"]) == []
    by_id = {case["id"]: case for case in FIXTURE["cases"]}
    for step in FIXTURE["recalculations"]:
        session.apply(json.dumps([step["edit"]]))
        report = json.loads(session.calculate(1))
        changed = [dict(by_id[name], expected={"value": value}) for name, value in step["expected"].items()]
        # Editing a target recalculates exactly through the name.
        assert _check_toolkit(report, changed) == []


def _canonical_workbook() -> Workbook:
    cells: dict[str, dict[str, Cell]] = {name: {} for name in FIXTURE["sheets"]}
    for key, value in FIXTURE["cells"].items():
        sheet, address = key.split("!")
        cells[sheet][address] = Cell(address, value, "number")
    for case in FIXTURE["cases"]:
        sheet, address = CASE_CELLS[case["id"]]
        cells[sheet][address] = Cell(address, None, "blank", None, Formula(case["formula"]))
    sheets = tuple(Sheet(name, cells[name], None) for name in FIXTURE["sheets"])
    return Workbook(1, 1, {"defined_names": FIXTURE["names"]}, sheets, None, None, None, ())


def _check_canonical(workbook: Workbook, cases: list[dict]) -> list[str]:
    diagnostics = {(item.sheet, item.address): item for item in workbook.diagnostics}
    problems = []
    for case in cases:
        sheet, address = CASE_CELLS[case["id"]]
        expected = case["expected"]
        cell = workbook.sheet(sheet).cells[address]
        if "code" in expected:
            got = diagnostics.get((sheet, address))
            code, classification = CANONICAL[expected["code"]]
            if got is None or (got.code, got.classification) != (code, classification) or (
                "message" in expected and got.message != expected["message"]
            ):
                problems.append(f"{case['id']}: {got} != {expected}")
        elif "error" in expected:
            if not isinstance(cell.formula.result, dict) or cell.formula.result.get("error") != expected["error"]:
                problems.append(f"{case['id']}: {cell.formula.result!r} != {expected}")
        elif not _same(cell.formula.result, expected["value"]):
            problems.append(f"{case['id']}: {cell.formula.result!r} != {expected['value']!r}")
    return problems


def test_shared_fixture_in_the_canonical_model():
    workbook = _canonical_workbook()
    assert _check_canonical(calculate(workbook), FIXTURE["cases"]) == []
    by_id = {case["id"]: case for case in FIXTURE["cases"]}
    for step in FIXTURE["recalculations"]:
        edit = step["edit"]
        sheets = []
        for sheet in workbook.sheets:
            if sheet.name == edit["sheet"]:
                cells = dict(sheet.cells)
                cells[edit["address"]] = Cell(edit["address"], edit["value"], "number")
                sheet = dataclasses.replace(sheet, cells=cells)
            sheets.append(sheet)
        workbook = dataclasses.replace(workbook, sheets=tuple(sheets))
        changed = [dict(by_id[name], expected={"value": value}) for name, value in step["expected"].items()]
        assert _check_canonical(calculate(workbook), changed) == []


def test_a_table_without_a_name_keeps_the_unsupported_name_refusal():
    # No table (a document that never came from a workbook): unchanged.
    assert evaluate("=Rate").message == "unsupported name 'Rate'"
    # A table: unknown is Excel's #NAME?.
    assert evaluate("=Rate", names=DefinedNames()).code == "#NAME?"


def test_from_ooxml_scopes_by_sheet_entry_and_drops_unscoped_ids():
    table = DefinedNames.from_ooxml(
        [
            ({"name": "Rate"}, "Inputs!$A$1"),
            ({"name": "Local", "localSheetId": "1"}, "Calc!$B$1"),
            ({"name": "Lost", "localSheetId": "7"}, "Calc!$B$1"),
            ({"name": "Odd", "localSheetId": "+1"}, "Calc!$B$1"),
            ({"name": "Quiet", "hidden": "1"}, "Calc!$B$1"),
        ],
        ["Inputs", "Calc"],
    )
    assert table.to_json() == [
        {"name": "Rate", "sheet": None, "formula": "Inputs!$A$1", "hidden": False},
        {"name": "Local", "sheet": "Calc", "formula": "Calc!$B$1", "hidden": False},
        {"name": "Quiet", "sheet": None, "formula": "Calc!$B$1", "hidden": True},
    ]


CORPUS_VALUES = {
    "op_defined_names.xlsx": {"Calc!A1": 120.0, "Calc!A2": 6.0},
    "xw_hyperlinks_names.xlsx": {"Index!B1": 100.0},
    "hb_r1c1_looking_names.xlsx": {"Calc!A2": 5.0, "Calc!B2": 3.05, "Calc!D2": 200.0},
}


@pytest.mark.parametrize("backend", _backends())
@pytest.mark.parametrize("name", ["op_defined_names.xlsx", "xw_hyperlinks_names.xlsx", "hb_r1c1_looking_names.xlsx"])
def test_imported_names_calculate(name, backend):
    from workbook_forge.xlsx import import_xlsx

    report = import_xlsx(CORPUS / name, backend=backend).calculate()
    for key, value in CORPUS_VALUES[name].items():
        assert _same(report["values"][key], value), (key, report["values"].get(key))


def test_editing_a_target_recalculates_the_cell_that_names_it(tmp_path):
    from workbook_forge.xlsx import export_xlsx, import_xlsx

    model = import_xlsx(CORPUS / "op_defined_names.xlsx")
    model.set_value("Inputs", "A1", 0.1)
    assert _same(model.calculate()["values"]["Calc!A1"], 150.0)
    # A formula edit may use a name, and the export keeps workbook.xml as it was.
    model.set_formula("Calc", "C1", "=Principal+Growth")
    assert _same(model.calculate()["values"]["Calc!C1"], 1500.03)
    target = tmp_path / "out.xlsx"
    export_xlsx(model, target)
    with zipfile.ZipFile(CORPUS / "op_defined_names.xlsx") as source, zipfile.ZipFile(target) as out:
        assert out.read("xl/workbook.xml").count(b"<definedName") == source.read("xl/workbook.xml").count(b"<definedName")
        assert _defined_names_xml(out.read("xl/workbook.xml")) == _defined_names_xml(source.read("xl/workbook.xml"))


def _defined_names_xml(data: bytes) -> bytes:
    start = data.index(b"<definedNames")
    return data[start : data.index(b"</definedNames>") + len(b"</definedNames>")]


def test_canonical_export_writes_the_names_back(tmp_path):
    from workbook_forge.canonical_xlsx import export_canonical
    from workbook_forge.intake import intake_workbook_model

    workbook = intake_workbook_model(CORPUS / "op_defined_names.xlsx")
    assert not [item for item in workbook.metadata["intake"]["not_carried"] if item["what"] == "definedNames"]
    target = export_canonical(workbook, tmp_path / "out.xlsx")
    again = intake_workbook_model(target)
    assert again.metadata["defined_names"] == workbook.metadata["defined_names"]
    results = {f"{sheet.name}!{address}": cell.formula.result for sheet in calculate(again).sheets for address, cell in sheet.cells.items() if cell.formula}
    assert results == {"Calc!A1": 120, "Calc!A2": 6}


HB_VALUES = {
    "Calc!B1": 500.0,
    "Calc!B2": 375.0,
    "Calc!B3": 60.0,
    "Calc!B4": 5.0,
    "Calc!B5": 25.0,
    "Calc!B6": {"error": "#REF!"},
    "Calc!B8": {"error": "#NAME?"},
    "Report!A1": 2.0,
}


@pytest.mark.parametrize("backend", _backends())
def test_handbuilt_corpus_file_in_the_toolkit(backend):
    from workbook_forge.xlsx import import_xlsx

    report = import_xlsx(CORPUS / "hb_defined_names.xlsx", backend=backend).calculate()
    for key, value in HB_VALUES.items():
        assert _same(report["values"].get(key), value), (key, report["values"].get(key))
    assert sorted((item["sheet"], item["address"], item["code"]) for item in report["diagnostics"]) == [
        ("Calc", "B7", "name_cycle"),
        ("Report", "A2", "unsupported_formula"),
    ]


def test_handbuilt_corpus_file_in_the_canonical_model():
    from workbook_forge.intake import intake_workbook_model

    calculated = calculate(intake_workbook_model(CORPUS / "hb_defined_names.xlsx"))
    results = {
        f"{sheet.name}!{address}": cell.formula.result
        for sheet in calculated.sheets
        for address, cell in sheet.cells.items()
        if cell.formula
    }
    for key, value in HB_VALUES.items():
        got = results[key]
        if isinstance(value, dict):
            assert isinstance(got, dict) and got.get("error") == value["error"], (key, got)
        else:
            assert _same(got, value), (key, got)
    assert sorted((item.sheet, item.address, item.code) for item in calculated.diagnostics) == [
        ("Calc", "B7", "cycle"),
        ("Report", "A2", "unsupported_formula"),
    ]
