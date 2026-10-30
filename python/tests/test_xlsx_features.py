"""Drop-downs, pivots and charts on imported workbooks.

tools/corpus/feature-cases.json holds the cases; rust/tests/xlsx_features.rs
runs the same table against the Rust engine. Expected outcomes are the rules
in docs/xlsx-features.md.
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


_CONTENT_TYPES = """<?xml version="1.0"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
_ROOT_RELS = """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
_WORKBOOK_RELS = """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""


def _workbook_xml(defined_name: str | None, expression: str | None) -> str:
    names = ""
    if defined_name is not None and expression is not None:
        names = f'<definedNames><definedName name="{defined_name}">{expression}</definedName></definedNames>'
    return (
        '<?xml version="1.0"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
        f"{names}</workbook>"
    )


def _sheet_xml(sqref: str, formula1: str) -> str:
    return (
        '<?xml version="1.0"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData><row r="1"><c r="A1"><v>1</v></c></row></sheetData>'
        '<dataValidations count="1">'
        f'<dataValidation type="list" allowBlank="1" showErrorMessage="1" sqref="{sqref}">'
        f"<formula1>{formula1}</formula1></dataValidation></dataValidations></worksheet>"
    )


def _write_zip(path: Path, parts: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)


def _grammar_parts(case: dict) -> dict[str, str]:
    return {
        "[Content_Types].xml": _CONTENT_TYPES,
        "_rels/.rels": _ROOT_RELS,
        "xl/workbook.xml": _workbook_xml(case.get("defined_name"), case.get("expression")),
        "xl/_rels/workbook.xml.rels": _WORKBOOK_RELS,
        "xl/worksheets/sheet1.xml": _sheet_xml(case["sqref"], case["formula1"]),
    }


@pytest.mark.parametrize("case", CASES["grammar"], ids=lambda case: case["id"])
def test_grammar_cases(case, tmp_path):
    path = tmp_path / "book.xlsx"
    _write_zip(path, _grammar_parts(case))
    model = import_xlsx(path)
    rule = model.inspect()["xlsx"]["data_validations"][0]
    assert rule["enforced"] is case["enforced"]
    if "unenforced_reason" in case:
        assert rule["unenforced_reason"] == case["unenforced_reason"]
    if "allowed_values" in case:
        assert rule["allowed_values"] == case["allowed_values"]
    if case["expect"] == "ok":
        model.set_value("Sheet1", case["address"], case["value"])
        return
    with pytest.raises(WorkbookError) as caught:
        model.set_value("Sheet1", case["address"], case["value"])
    assert caught.value.code == case["expect"]


def _pivot_package(path: Path, pivot_xml: str) -> None:
    parts = _grammar_parts(
        {"sqref": "B1", "formula1": '"Yes,No"', "defined_name": None, "expression": None}
    )
    parts["xl/worksheets/_rels/sheet1.xml.rels"] = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/pivotTable" '
        'Target="../pivotTables/pivotTable1.xml"/>'
        "</Relationships>"
    )
    parts["xl/pivotTables/pivotTable1.xml"] = pivot_xml
    _write_zip(path, parts)


def test_a_pivot_part_with_a_doctype_is_refused(tmp_path):
    path = tmp_path / "book.xlsx"
    _pivot_package(
        path,
        """<?xml version="1.0"?>
<!DOCTYPE pivotTableDefinition [<!ENTITY boom "hi">]>
<pivotTableDefinition xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">&boom;</pivotTableDefinition>
""",
    )
    with pytest.raises(WorkbookError) as caught:
        import_xlsx(path)
    assert caught.value.code == "unsupported_package"
    assert "DTD" in str(caught.value) or "entity" in str(caught.value).casefold()


def test_a_broken_pivot_part_is_package_damaged(tmp_path):
    path = tmp_path / "book.xlsx"
    _pivot_package(path, "<?xml version='1.0'?><pivotTableDefinition>")
    with pytest.raises(WorkbookError) as caught:
        import_xlsx(path)
    assert caught.value.code == "package_damaged"
