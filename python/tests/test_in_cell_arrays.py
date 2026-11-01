"""Excel's lists, arrays in cells and nested arrays (Excel Beta, September 2026).

The corpus files are MODELED, not captured from Excel (see
tools/corpus/in_cell_arrays.py and their MANIFEST.json entries). Each construct
is either refused with one typed code, naming the cell, or read and preserved
byte-for-byte with a typed calculation diagnostic. Nothing is replaced by its
fallback value. Rust holds the same files to the same outcomes in
rust/tests/corpus.rs, and the browser engine in web/forge-engine/test.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import zipfile

import pytest

from workbook_forge import _in_cell_array_function_message, _in_cell_array_syntax, analyze_formula
from workbook_forge.workbook import (
    COMPAT_V3_CALCULATION,
    COMPAT_V3_EDIT,
    UnsupportedWorkbook,
    in_cell_array_message,
)
from workbook_forge.xlsx import export_xlsx, import_xlsx

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "fixtures" / "corpus"
CASES = json.loads((ROOT / "tools/corpus/in-cell-array-cases.json").read_text(encoding="utf-8"))
REFUSED = [
    ("hb_in_cell_list.xlsx", "Projects", "B2"),
    ("hb_in_cell_array_formula.xlsx", "Splits", "A1"),
    ("hb_nested_array.xlsx", "Nested", "A1"),
]
MODELED = [name for name, *_ in REFUSED] + ["hb_flatten_has_functions.xlsx", "hb_compat_version_3.xlsx"]


def _parts(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _cell_xml(sheet: bytes, address: str) -> bytes:
    match = re.search(rb'<c r="' + address.encode() + rb'"[^>]*?(?:/>|>.*?</c>)', sheet, re.S)
    assert match, address
    return match.group(0)


def test_modeled_files_say_so_in_the_manifest():
    manifest = json.loads((CORPUS / "MANIFEST.json").read_text(encoding="utf-8"))["files"]
    for name in MODELED:
        entry = manifest[name]
        assert entry["provenance"] == "modeled, not captured from Excel"
        assert "Replace with a real capture from Excel Beta" in entry["note"]
        assert "modeled" in entry["categories"]


@pytest.mark.parametrize(("name", "sheet", "address"), REFUSED)
def test_a_cell_holding_an_array_is_refused_with_one_typed_code(name, sheet, address):
    with pytest.raises(UnsupportedWorkbook) as caught:
        import_xlsx(CORPUS / name)
    assert caught.value.code == "in_cell_array"
    assert str(caught.value) == in_cell_array_message(sheet, address)


@pytest.mark.parametrize(("name", "sheet", "address"), REFUSED)
def test_the_canonical_path_refuses_the_same_cell(name, sheet, address):
    from workbook_forge.intake import intake_workbook_model

    with pytest.raises(UnsupportedWorkbook) as caught:
        intake_workbook_model(CORPUS / name)
    assert caught.value.code == "in_cell_array"
    assert str(caught.value) == in_cell_array_message(sheet, address)


def test_value_metadata_without_rich_arrays_still_reads(tmp_path):
    """The refusal is scoped to packages that store rich arrays: a rich value
    of another type (an image in a cell, a linked data type) reads as before."""
    sys.path.insert(0, str(ROOT / "tools" / "corpus"))
    import handbuilt
    import in_cell_arrays

    parts = in_cell_arrays.in_cell_list()
    del parts["xl/richData/rdarray.xml"]
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
        '<Override PartName="/xl/richData/rdarray.xml" ContentType="application/vnd.ms-excel.rdArray+xml"/>', ""
    )
    parts["xl/_rels/workbook.xml.rels"] = re.sub(
        r'<Relationship [^>]*Target="richData/rdarray.xml"/>', "", parts["xl/_rels/workbook.xml.rels"]
    )
    parts["xl/richData/rdrichvaluestructure.xml"] = parts["xl/richData/rdrichvaluestructure.xml"].replace(
        't="_array"><k n="array" t="a"/>', 't="_localImage"><k n="_rvRel:LocalImageIdentifier" t="i"/>'
    )
    path = tmp_path / "rich_value.xlsx"
    handbuilt.write_package(path, parts)
    cells = import_xlsx(path).to_dict()["sheets"][0]["cells"]
    assert cells["B2"]["value"] == {"error": "#VALUE!"}


def test_an_array_structure_without_an_array_part_is_still_refused(tmp_path):
    sys.path.insert(0, str(ROOT / "tools" / "corpus"))
    import handbuilt
    import in_cell_arrays

    parts = in_cell_arrays.in_cell_list()
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace("rdArray+xml", "rdArrayRenamed+xml")
    path = tmp_path / "structure_only.xlsx"
    handbuilt.write_package(path, parts)
    with pytest.raises(UnsupportedWorkbook) as caught:
        import_xlsx(path)
    assert caught.value.code == "in_cell_array"


@pytest.mark.parametrize("case", CASES["formulas"], ids=lambda case: case["formula"][:40])
def test_in_cell_array_syntax_matches_the_shared_cases(case):
    assert _in_cell_array_syntax(case["formula"]) == case["message"]
    if case["message"] is not None:
        with pytest.raises(ValueError) as caught:
            analyze_formula("=" + case["formula"])
        assert str(caught.value) == case["message"]


def test_rust_carries_the_same_messages():
    lib = (ROOT / "rust/src/lib.rs").read_text(encoding="utf-8")
    xlsx = (ROOT / "rust/src/xlsx.rs").read_text(encoding="utf-8")
    assert (
        "braces around a formula or an array keep an array in one cell (Excel arrays in cells), "
        "which is not calculated"
    ) in lib
    assert "FLATTEN, HAS, HASANY and HASALL work on Excel arrays in cells and are not calculated" in lib
    assert in_cell_array_message("{sheet}", "{address}") in xlsx
    assert COMPAT_V3_CALCULATION in xlsx
    assert f'"{COMPAT_V3_EDIT}"' in xlsx


def test_new_functions_are_typed_not_calculated_and_preserved(tmp_path):
    source = CORPUS / "hb_flatten_has_functions.xlsx"
    report = import_xlsx(source).calculate()
    by_cell = {d["address"]: d for d in report["diagnostics"]}
    for address, name in (("A3", "HAS"), ("B3", "HASANY"), ("C3", "HASALL"), ("D3", "FLATTEN")):
        assert by_cell[address]["code"] == "unsupported_formula"
        assert by_cell[address]["message"] == _in_cell_array_function_message(name)
    assert "E3" not in by_cell
    assert report["values"]["Tags!E3"] == 6

    model = import_xlsx(source, outputs={"edited": {"sheet": "Tags", "address": "E3"}})
    model.set_value("Tags", "A2", 10)
    target = export_xlsx(model, tmp_path / "out.xlsx")
    before, after = _parts(source), _parts(target)
    assert set(before) == set(after)
    for name in before:
        if name not in {"xl/worksheets/sheet1.xml", "xl/workbook.xml", "[Content_Types].xml"}:
            assert after[name] == before[name], name
    for address in ("A3", "B3", "C3", "D3"):
        assert _cell_xml(after["xl/worksheets/sheet1.xml"], address) == _cell_xml(
            before["xl/worksheets/sheet1.xml"], address
        )
    again = import_xlsx(target).to_dict()["sheets"][0]["cells"]
    assert again["A3"]["formula"] == '_xlfn.HAS(A1:C1,"space")'
    assert again["A3"]["cached_value"] is True
    assert again["A2"]["value"] == 10


def test_a_new_function_typed_as_a_formula_edit_is_refused_with_the_same_message():
    from workbook_forge.python_engine import ToolkitError

    model = import_xlsx(CORPUS / "hb_flatten_has_functions.xlsx")
    before = model.revision
    with pytest.raises(ToolkitError) as caught:
        model.set_formula("Tags", "F3", "=HAS(A1:C1,\"drama\")")
    assert caught.value.code == "unsupported_formula"
    assert str(caught.value).endswith(_in_cell_array_function_message("HAS"))
    assert model.revision == before


def test_compatibility_version_3_preserves_formulas_and_refuses_formula_edits(tmp_path):
    source = CORPUS / "hb_compat_version_3.xlsx"
    model = import_xlsx(source)
    cells = model.to_dict()["sheets"][0]["cells"]
    assert cells["B1"]["blocked_reason"] == COMPAT_V3_CALCULATION
    assert cells["B2"]["blocked_reason"] == COMPAT_V3_CALCULATION
    assert cells["A1"]["value"] == "caf\u00e9 \U0001F600"
    report = model.calculate()
    assert {d["address"] for d in report["diagnostics"]} == {"B1", "B2"}
    assert all(d["message"] == COMPAT_V3_CALCULATION for d in report["diagnostics"])
    with pytest.raises(UnsupportedWorkbook) as caught:
        model.set_formula("Calc", "C1", "=A2+1")
    assert caught.value.code == "compat_version_3"
    assert str(caught.value) == COMPAT_V3_EDIT

    model = import_xlsx(source, outputs={"edited": {"sheet": "Calc", "address": "A2"}})
    model.set_value("Calc", "A2", 5)
    target = export_xlsx(model, tmp_path / "out.xlsx")
    before, after = _parts(source), _parts(target)
    # workbook.xml is rewritten (calcPr asks Excel to recalculate), so the
    # setting is compared as XML rather than as bytes.
    import xml.etree.ElementTree as ET

    tag = "{http://schemas.microsoft.com/office/spreadsheetml/2024/workbookCompatibilityVersion}version"
    for package in (before, after):
        found = ET.fromstring(package["xl/workbook.xml"]).iter(tag)
        assert [dict(node.attrib) for node in found] == [{"setVersion": "3", "warnBelowVersion": "3"}]
    for address in ("B1", "B2"):
        assert _cell_xml(after["xl/worksheets/sheet1.xml"], address) == _cell_xml(
            before["xl/worksheets/sheet1.xml"], address
        )


def test_compatibility_version_3_is_refused_by_the_canonical_path():
    from workbook_forge.canonical_xlsx import CanonicalPackageError
    from workbook_forge.intake import intake_workbook_model

    with pytest.raises(CanonicalPackageError) as caught:
        intake_workbook_model(CORPUS / "hb_compat_version_3.xlsx")
    assert caught.value.code == "compat_version_3"


def test_compatibility_version_2_is_not_treated_as_3(tmp_path):
    parts = _parts(CORPUS / "hb_compat_version_3.xlsx")
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(
        b'setVersion="3" warnBelowVersion="3"', b'setVersion="2" warnBelowVersion="1"'
    )
    path = tmp_path / "v2.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    cells = import_xlsx(path).to_dict()["sheets"][0]["cells"]
    assert "blocked_reason" not in cells["B2"]
