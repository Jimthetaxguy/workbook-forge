"""Drop-downs, pivots and charts on imported workbooks (docs/xlsx-features.md).

tools/corpus/feature-cases.json holds the cases; rust/tests/xlsx_features.rs
runs the same table against the Rust engine. Expected outcomes follow Excel's
documented data-validation behavior (a stop alert blocks the entry; warning
and information alerts let it through; a rule with no alert accepts anything),
not either engine.
"""
from __future__ import annotations

import json
from pathlib import Path
import zipfile

import pytest

from workbook_forge.workbook import UnsupportedWorkbook, WorkbookError
from workbook_forge.xlsx import export_snapshot, export_xlsx, import_xlsx

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "fixtures/corpus"
CASES = json.loads((ROOT / "tools/corpus/feature-cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "case", CASES["writes"], ids=lambda c: f"{c['file']}:{c['sheet']}!{c['address']}={c['value']!r}"
)
def test_write_cases(case):
    model = import_xlsx(CORPUS / case["file"])
    if case["expect"] == "ok":
        model.set_value(case["sheet"], case["address"], case["value"])
        return
    with pytest.raises(WorkbookError) as caught:
        model.set_value(case["sheet"], case["address"], case["value"])
    assert caught.value.code == case["expect"]


@pytest.mark.parametrize("name", sorted(CASES["inspect"]))
def test_inspect_cases(name):
    want = CASES["inspect"][name]
    got = import_xlsx(CORPUS / name).inspect()["xlsx"]
    if "data_validations" in want:
        rules = got["data_validations"]
        assert len(rules) == want["data_validations"]
        assert sum(rule["enforced"] for rule in rules) == want["enforced"]
        assert all(rule["enforced"] or rule["unenforced_reason"] for rule in rules)
        for sqref, values in want.get("allowed_values", {}).items():
            rule = next(rule for rule in rules if " ".join(rule["sqref"]) == sqref)
            assert rule["allowed_values"] == values
    if "pivots" in want:
        assert [
            {
                "sheet": p["sheet"], "location": p["location"],
                "source_sheet": p["source"].get("sheet"), "source_ref": p["source"].get("ref"),
                "row_fields": p["row_fields"], "column_fields": p["column_fields"],
                "page_fields": p["page_fields"], "data_fields": [d["field"] for d in p["data_fields"]],
            }
            for p in got["pivots"]
        ] == want["pivots"]
        assert all(p["refresh"].startswith("unsupported") for p in got["pivots"])
    if "charts" in want:
        assert len(got["charts"]) == len(want["charts"])
        for chart, expected in zip(got["charts"], want["charts"], strict=True):
            assert chart["types"] == expected["types"]
            assert len(chart["series"]) == expected["series"]
            assert all(series["refs"].get("val") or series["refs"].get("yVal") for series in chart["series"])
            if expected.get("anchor"):
                assert chart["anchor"] == expected["anchor"]


@pytest.mark.parametrize(
    "name", ["hb_pivot_cache.xlsx", "hb_pivot_fields.xlsx", "op_charts.xlsx", "xw_chart_image.xlsx",
             "xw_chartsheet.xlsx", "xw_dropdowns.xlsx", "hb_dropdown_rules.xlsx"],
)
def test_features_survive_a_value_edit_byte_for_byte(name, tmp_path):
    model = import_xlsx(CORPUS / name)
    metadata = model._import_metadata
    first = metadata["sheets"][0]
    target = tmp_path / "out.xlsx"
    model.set_value(first["name"], "Z99", 1)
    export_xlsx(model, target)
    with zipfile.ZipFile(CORPUS / name) as before, zipfile.ZipFile(target) as after:
        for part in before.namelist():
            if part == first["part"] or part.startswith("docProps/") or part == "xl/calcChain.xml":
                continue
            if "pivot" in part or "chart" in part or "drawing" in part or part.startswith("xl/worksheets/"):
                assert after.read(part) == before.read(part), part
    again = import_xlsx(target).inspect()["xlsx"]
    assert again["pivots"] == metadata["pivots"]
    assert again["charts"] == metadata["charts"]
    assert again["data_validations"] == metadata["data_validations"]


def test_export_checks_the_same_rules_as_edits(tmp_path):
    model = import_xlsx(CORPUS / "xw_dropdowns.xlsx")
    document = model.to_dict()
    orders = next(sheet for sheet in document["sheets"] if sheet["name"] == "Orders")
    orders["cells"]["C2"] = {"value": "Purple"}
    with pytest.raises(UnsupportedWorkbook) as caught:
        export_snapshot(model, document, tmp_path / "out.xlsx")
    assert caught.value.code == "validation_failed"


def test_renaming_a_charted_sheet_refuses(tmp_path):
    model = import_xlsx(CORPUS / "op_charts.xlsx")
    document = model.to_dict()
    document["sheets"][0]["name"] = "Renamed"
    with pytest.raises(UnsupportedWorkbook) as caught:
        export_snapshot(model, document, tmp_path / "out.xlsx")
    assert caught.value.code == "chart_reference_protected"


def test_formula_entries_are_not_validated_and_blank_clears_pass():
    model = import_xlsx(CORPUS / "xw_dropdowns.xlsx")
    model.set_formula("Orders", "C3", '"Pur"&"ple"')
    model.set_value("Orders", "C4", None)
