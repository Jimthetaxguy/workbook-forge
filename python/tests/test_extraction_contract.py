"""Independent formula-aware extraction contracts over hand-authored XLSX data.

Expected values/formulas below come from the fixture's XML and fixed reference
arithmetic, never from either implementation. Rust is a standalone process.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

import jsonschema
import pytest

from workbook_forge.workbook import WorkbookError
from workbook_forge.xlsx import import_xlsx
from test_workbook import make_xlsx, write_parts

ROOT = Path(__file__).resolve().parents[2]
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
PATTERNS = {"cell", "formula", "defined_name", "table", "table_column", "table_formula", "validation", "merged_range", "shared_string", "column"}


@pytest.fixture(scope="module")
def rust_extractor():
    cargo = shutil.which("cargo")
    if cargo is None:
        pytest.skip("Rust toolchain unavailable; standalone extraction contracts were not run")
    target = ROOT / ".verification/xml-contract-target"
    result = subprocess.run(
        [cargo, "build", "--manifest-path", str(ROOT / "rust/Cargo.toml"), "--locked", "--offline", "--example", "extract_xlsx", "--example", "agent_workbook"],
        env=dict(os.environ, CARGO_TARGET_DIR=str(target)), text=True,
        capture_output=True, timeout=180,
    )
    assert result.returncode == 0, result.stderr
    return target / "debug/examples" / ("extract_xlsx.exe" if os.name == "nt" else "extract_xlsx")


class _RustExtractionError(ValueError):
    pass


@pytest.fixture(params=["python", "rust"])
def extractor(request):
    if request.param == "python":
        module = importlib.import_module("workbook_forge.extraction")
        extract, catalog = module.extract_xlsx, module.pattern_catalog
    else:
        binary = request.getfixturevalue("rust_extractor")

        def run(arguments):
            result = subprocess.run([str(binary), *arguments], capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise _RustExtractionError(result.stderr)
            return json.loads(result.stdout)

        def extract(path, **options):
            arguments = [str(path)]
            for key, value in options.items():
                if key == "patterns":
                    for pattern in value:
                        arguments.extend(["--pattern", pattern])
                else:
                    arguments.extend(["--" + key, str(value)])
            return run(arguments)

        catalog = lambda: run(["--catalog"])

    def checked(path, **options):
        before = path.read_bytes()
        try:
            report = extract(path, **options)
        finally:
            assert path.read_bytes() == before, "extraction changed source bytes"
        jsonschema.validate(report, json.loads((ROOT / "catalog/extraction-report.schema.json").read_text()))
        assert report["schema_version"] == 1 and report["profile"] == "xlsx-extraction-v1"
        assert len(report["records"]) <= options.get("limit", 100)
        assert len(json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode()) <= 1024 * 1024
        assert report["diagnostic_count"] >= len(report["diagnostics"])
        assert len(report["diagnostics"]) <= 100
        assert report["diagnostics_truncated"] == (report["diagnostic_count"] > 100)
        for record in report["records"]:
            assert set(record) == {"pattern", "part", "path", "sheet", "cell", "attributes", "text", "data"}
            assert not any(name == "xmlns" or name.startswith("xmlns:") for name in record["attributes"])
        return report

    checked.catalog = catalog
    checked.backend = request.param
    checked.rust_binary = binary if request.param == "rust" else None
    return checked


def _prefix_main_xml(source):
    # Fixture construction only: give every known SpreadsheetML element a
    # prefix without parsing/reserializing away the literal CDATA probes.
    names = ["worksheet", "sheetData", "row", "c", "v", "f", "is", "t", "r", "rPh", "cols", "col", "dataValidations", "dataValidation", "formula1", "formula2", "mergeCells", "mergeCell", "tableParts", "tablePart", "extLst", "ext", "workbook", "sheets", "sheet", "definedNames", "definedName", "sst", "si", "table", "tableColumns", "tableColumn", "calculatedColumnFormula", "totalsRowFormula"]
    text = source.decode().replace(f'xmlns="{MAIN}"', f'xmlns:s="{MAIN}"')
    for name in sorted(names, key=len, reverse=True):
        for ending in (" ", ">", "/"):
            text = text.replace(f"<{name}{ending}", f"<s:{name}{ending}")
        text = text.replace(f"</{name}>", f"</s:{name}>")
    return text.encode()


def _fixture(path, *, prefixed=False, sheet_xml=None):
    parts = make_xlsx(path)
    parts["xl/workbook.xml"] = f'''<workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets><sheet name="Data" sheetId="1" r:id="rId1"/><sheet name="Odd Sheet" sheetId="2" r:id="rId2"/></sheets><definedNames><definedName name="GlobalAmount">Data!$D$1</definedName><definedName name="LocalAmount" localSheetId="0">SUM(Data!D1)</definedName></definedNames></workbook>'''.encode()
    parts["xl/_rels/workbook.xml.rels"] = f'''<Relationships xmlns="{PKG}"><Relationship Id="rId1" Type="{REL}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="{REL}/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="strings" Type="{REL}/sharedStrings" Target="sharedStrings.xml"/></Relationships>'''.encode()
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(b"</Types>", b'''<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/><Override PartName="/xl/tables/table1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/></Types>''')
    parts["xl/sharedStrings.xml"] = f'''<sst xmlns="{MAIN}" count="1" uniqueCount="1"><si><r><t>東</t></r><r><t>京 &amp; A</t></r><rPh sb="0" eb="2"><t>とうきょう</t></rPh><extLst><ext uri="urn:decoy"><t>hidden text</t></ext></extLst></si></sst>'''.encode()
    parts["xl/worksheets/sheet2.xml"] = f'''<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1"><v>9</v></c></row></sheetData></worksheet>'''.encode()
    parts["xl/worksheets/_rels/sheet1.xml.rels"] = f'''<Relationships xmlns="{PKG}"><Relationship Id="table1" Type="{REL}/table" Target="../tables/table1.xml"/></Relationships>'''.encode()
    parts["xl/tables/table1.xml"] = f'''<table xmlns="{MAIN}" id="1" name="Items" displayName="Items" ref="F1:G3" totalsRowCount="1"><tableColumns count="2"><tableColumn id="1" name="Item"/><tableColumn id="2" name="Amount"><calculatedColumnFormula>SUM(Data!D1)</calculatedColumnFormula><totalsRowFormula>SUM(Data!D1:D2)</totalsRowFormula></tableColumn></tableColumns></table>'''.encode()
    parts["xl/worksheets/sheet1.xml"] = (sheet_xml or f'''<worksheet xmlns="{MAIN}" xmlns:r="{REL}" xmlns:meta="urn:fixture:metadata"><cols><col min="1" max="3" width="22" meta:hint="wide &amp; clear"/></cols><sheetData>
<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><r><t><![CDATA[Alpha & Beta]]></t></r><rPh sb="0" eb="5"><t>ignore pronunciation</t></rPh></is></c><c r="C1" t="str"><v></v></c><c r="D1"><v>5</v></c></row>
<row r="2"><c r="A2" t="e"><v>#DIV/0!</v></c><c r="B2"><f><![CDATA[IF(A1="SUM(99)",SUM($D$1,D$1,$D1),'Odd Sheet'!A1)]]></f><v>19</v></c><c r="C2"><f>UNKNOWN_FN(D1)</f><v>123</v></c><c r="D2"><f>A1#</f><v>321</v></c></row>
<row r="3"><c r="A3"><f t="shared" si="01" ref="A3:B4">SUM($D3,D$1,$D$1,A3)</f><v>30</v></c><c r="B3"><f t="shared" si="1"/><v>31</v></c></row>
<row r="4"><c r="A4"><f t="shared" si="1">SUM(999)</f><v>40</v></c><c r="B4"><f t="shared" si="01"/><v>41</v></c></row>
<row r="5"><c r="A5"><f t="array" ref="A5:B5">SEQUENCE(1,2)</f><v>1</v></c><c r="B5"><v>2</v></c></row>
</sheetData><mergeCells count="1"><mergeCell ref="C6:D6"/></mergeCells><dataValidations count="1"><dataValidation type="whole" operator="between" sqref="D1"><formula1>0</formula1><formula2>100</formula2></dataValidation></dataValidations><tableParts count="1"><tablePart r:id="table1"/></tableParts><extLst><ext uri="urn:fixture:decoys"><row r="99"><c r="Z99"><f>SUM(999)</f><v>999</v></c></row><c r="D1"><v>999</v></c><dataValidation sqref="Z99"><formula1>999</formula1></dataValidation></ext></extLst></worksheet>''').encode()
    if prefixed:
        for part in ["xl/workbook.xml", "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml", "xl/sharedStrings.xml", "xl/tables/table1.xml"]:
            parts[part] = _prefix_main_xml(parts[part])
    write_parts(path, parts)
    return parts


def _records(report, pattern):
    return [record for record in report["records"] if record["pattern"] == pattern]


def _at(report, pattern, cell):
    matches = [record for record in _records(report, pattern) if record["sheet"] == "Data" and record["cell"] == cell]
    assert len(matches) == 1
    return matches[0]


def test_catalog_is_identical_and_describes_all_structural_patterns(extractor):
    expected = json.loads((ROOT / "catalog/extraction-patterns.json").read_text())
    assert expected == json.loads((ROOT / "rust/src/extraction_patterns.json").read_text())
    assert extractor.catalog() == expected
    assert expected["schema_version"] == 1
    assert len(json.dumps(expected)) < 1024 * 1024


@pytest.mark.parametrize("prefixed", [False, True])
def test_structural_paths_ignore_extension_decoys_and_decode_text(extractor, tmp_path, prefixed):
    path = tmp_path / "records.xlsx"
    _fixture(path, prefixed=prefixed)
    report = extractor(path)
    assert set(report["counts"]) == PATTERNS
    assert report["counts"] == {"cell": 15, "formula": 8, "defined_name": 2, "table": 1, "table_column": 2, "table_formula": 2, "validation": 1, "merged_range": 1, "shared_string": 1, "column": 1}
    assert report["total_records"] == sum(report["counts"].values())
    assert not any(record["cell"] == "Z99" for record in report["records"])
    assert _at(report, "cell", "D1")["data"]["value"] == 5
    assert _at(report, "cell", "A1")["data"]["value"] == "東京 & A"
    assert _at(report, "cell", "B1")["data"]["value"] == "Alpha & Beta"
    assert _at(report, "cell", "C1")["data"]["value"] == ""
    assert _at(report, "cell", "A2")["data"]["value"] == {"error": "#DIV/0!"}
    literal = _at(report, "cell", "D1")["data"]
    assert literal["cached_value"] is None and literal["cell_type"] == "n" and literal["style_id"] is None
    formula_cell = _at(report, "cell", "B2")["data"]
    assert formula_cell["value"] is None and formula_cell["cached_value"] == 19
    formula = _at(report, "formula", "B2")
    assert formula["text"] == 'IF(A1="SUM(99)",SUM($D$1,D$1,$D1),\'Odd Sheet\'!A1)'
    assert formula["path"] == "/worksheet[1]/sheetData[1]/row[2]/c[2]/f[1]"
    assert formula["data"]["effective_formula"] == formula["text"]
    assert [item["name"] for item in formula["data"]["analysis"]["functions"]] == ["IF", "SUM"]
    refs = [item["start"] for item in formula["data"]["analysis"]["references"]]
    assert {(ref["absolute_column"], ref["absolute_row"]) for ref in refs if ref["column"] == 4} == {(True, True), (False, True), (True, False)}
    column = _records(report, "column")[0]
    assert column["attributes"]["{urn:fixture:metadata}hint"] == "wide & clear"
    assert _records(report, "shared_string")[0]["data"]["value"] == "東京 & A"
    assert _records(report, "shared_string")[0]["text"] == ""
    assert [(item["code"], item["sheet"], item["cell"]) for item in report["diagnostics"]] == [("formula_syntax", "Data", "D2")]


def test_shared_formula_translation_uses_master_even_when_follower_has_text(extractor, tmp_path):
    path = tmp_path / "shared.xlsx"
    _fixture(path)
    report = extractor(path, patterns=["formula"], sheet="dAtA")
    expected = {"A3": "SUM($D3,D$1,$D$1,A3)", "B3": "=SUM($D3,E$1,$D$1,B3)", "A4": "=SUM($D4,D$1,$D$1,A4)", "B4": "=SUM($D4,E$1,$D$1,B4)"}
    for cell, formula in expected.items():
        record = _at(report, "formula", cell)
        assert record["data"]["effective_formula"] == formula
        assert record["data"]["master_cell"] == "A3"
        assert record["data"]["group_range"] == "A3:B4"
        assert record["data"]["kind"] == "shared"
        assert record["data"]["analysis"]["status"] == "parsed"
    assert _at(report, "formula", "A3")["data"]["shared_index"] == "01"
    assert _at(report, "formula", "B3")["data"]["shared_index"] == "1"
    assert _at(report, "formula", "A4")["text"] == "SUM(999)"
    assert _at(report, "formula", "B4")["text"] == ""


def test_function_mapping_does_not_claim_formula_execution(extractor, tmp_path):
    path = tmp_path / "functions.xlsx"
    _fixture(path)
    report = extractor(path, patterns=["formula"])
    unknown = _at(report, "formula", "C2")["data"]["analysis"]
    assert unknown["status"] == "parsed"
    assert unknown["functions"] == [{"name": "UNKNOWN_FN", "known": False, "category": None, "python": "unknown", "rust": "unknown"}]
    assert unknown["categories"] == []
    unsupported = _at(report, "formula", "D2")["data"]
    assert unsupported["effective_formula"] == "A1#"
    assert unsupported["analysis"] == {"status": "unsupported", "functions": [], "references": [], "categories": []}
    array = _at(report, "formula", "A5")["data"]
    assert array["kind"] == "array" and array["effective_formula"] == "SEQUENCE(1,2)"


def test_metadata_ownership_sheet_filters_and_source_order(extractor, tmp_path):
    path = tmp_path / "metadata.xlsx"
    _fixture(path)
    report = extractor(path, patterns=["defined_name", "table", "table_column", "table_formula", "validation", "merged_range"])
    assert [record["part"] for record in report["records"]] == sorted(record["part"] for record in report["records"])
    table_formulas = _records(report, "table_formula")
    assert [record["text"] for record in table_formulas] == ["SUM(Data!D1)", "SUM(Data!D1:D2)"]
    assert all(record["sheet"] == "Data" and record["cell"] is None for record in table_formulas)
    assert all(record["data"]["kind"] == "table" for record in table_formulas)
    names = {record["attributes"]["name"]: record for record in _records(report, "defined_name")}
    assert names["GlobalAmount"]["sheet"] is None and names["GlobalAmount"]["cell"] is None
    assert names["LocalAmount"]["sheet"] == "Data"
    assert names["LocalAmount"]["data"]["analysis"]["status"] == "parsed"
    validation = _records(report, "validation")[0]
    assert validation["data"] == {"formula1": "0", "formula2": "100"}
    assert _records(report, "merged_range")[0]["attributes"]["ref"] == "C6:D6"
    filtered = extractor(path, sheet="data")
    assert all(record["sheet"] == "Data" for record in filtered["records"])
    assert filtered["counts"]["defined_name"] == 1 and filtered["counts"]["shared_string"] == 0
    assert filtered["counts"]["cell"] == 14
    other = extractor(path, sheet="ODD SHEET", patterns=["cell", "formula", "table"])
    assert other["counts"] == {"cell": 1, "formula": 0, "table": 0}
    assert other["records"][0]["data"]["value"] == 9


def test_pagination_counts_before_slicing_and_keeps_global_diagnostics(extractor, tmp_path):
    path = tmp_path / "pages.xlsx"
    _fixture(path)
    all_records = extractor(path)
    collected = []
    offset = 0
    while True:
        page = extractor(path, offset=offset, limit=3)
        assert page["counts"] == all_records["counts"]
        assert page["total_records"] == all_records["total_records"]
        assert page["diagnostic_count"] == all_records["diagnostic_count"]
        collected.extend(page["records"])
        if page["next_offset"] is None:
            assert page["truncated"] is False
            break
        assert page["truncated"] is True and page["next_offset"] == offset + 3
        offset = page["next_offset"]
    assert collected == all_records["records"]
    beyond = extractor(path, offset=2**64 - 1, limit=1)
    assert beyond["records"] == [] and beyond["next_offset"] is None and not beyond["truncated"]
    only_cells = extractor(path, patterns=["cell"])
    assert only_cells["diagnostics"] == []


@pytest.mark.parametrize("options", [
    {"patterns": ["unknown"]}, {"patterns": ["cell", "cell"]},
    {"sheet": "Missing"}, {"limit": 0}, {"limit": 101},
    {"limit": True}, {"offset": -1}, {"offset": 2**64}, {"offset": True},
])
def test_selection_and_pagination_reject_invalid_requests(extractor, tmp_path, options):
    path = tmp_path / "bounds.xlsx"
    _fixture(path)
    with pytest.raises((ValueError, TypeError, WorkbookError)):
        extractor(path, **options)


def _shared_source(path, formulas):
    rows = {}
    for cell, attributes, text in formulas:
        row = int(''.join(character for character in cell if character.isdigit()))
        rows.setdefault(row, []).append(f'<c r="{cell}"><f t="shared" {attributes}>{text}</f><v>999</v></c>')
    data = ''.join(f'<row r="{row}">{"".join(cells)}</row>' for row, cells in sorted(rows.items()))
    xml = f'<worksheet xmlns="{MAIN}"><sheetData>{data}</sheetData></worksheet>'
    _fixture(path, sheet_xml=xml)


@pytest.mark.parametrize("group_range", ["$C$1:$C$2", "$C1:C$2"])
def test_shared_group_footprint_anchors_preserve_raw_range(extractor, tmp_path, group_range):
    path = tmp_path / "anchored-group.xlsx"
    _shared_source(path, [
        ("C1", f'si="1" ref="{group_range}"', "SUM(A1,$A1,A$1,$A$1)"),
        ("C2", 'si="01"', ""),
    ])
    report = extractor(path, patterns=["formula"])
    assert report["diagnostics"] == []
    assert [record["data"]["group_range"] for record in report["records"]] == [group_range] * 2
    assert _at(report, "formula", "C1")["data"]["effective_formula"] == "SUM(A1,$A1,A$1,$A$1)"
    follower = _at(report, "formula", "C2")["data"]
    assert follower["effective_formula"] == "=SUM(A2,$A2,A$1,$A$1)"
    assert follower["master_cell"] == "C1"
    assert follower["analysis"]["status"] == "parsed"


@pytest.mark.parametrize("members", [
    [("A1", 'si="1"', "")],
    [("A1", 'si="1" ref="A1:B1"', "1"), ("B1", 'si="01" ref="A1:B1"', "2")],
    [("A1", 'si="1" ref="A1:B1"', "1"), ("C1", 'si="1"', "")],
    [("A1", 'si="1" ref="B1:C1"', "1"), ("B1", 'si="1"', "")],
    [("A1", 'si="1" ref="A0:B1"', "1"), ("B1", 'si="1"', "")],
    [("A1", 'si="-1" ref="A1:B1"', "1"), ("B1", 'si="-1"', "")],
    [("A1", 'si="4294967296" ref="A1:B1"', "1"), ("B1", 'si="4294967296"', "")],
    [("A1", 'si="1" ref="A1:B1"', "A1#"), ("B1", 'si="1"', "")],
])
def test_invalid_shared_groups_are_visible_and_wholly_unresolved(extractor, tmp_path, members):
    path = tmp_path / "invalid-group.xlsx"
    _shared_source(path, members)
    report = extractor(path, patterns=["formula"])
    assert len(report["records"]) == len(members)
    assert report["diagnostic_count"] == len(members)
    assert {(item["code"], item["part"], item["sheet"], item["cell"]) for item in report["diagnostics"]} == {("shared_formula", "xl/worksheets/sheet1.xml", "Data", cell) for cell, _, _ in members}
    for record in report["records"]:
        assert record["data"]["effective_formula"] is None
        assert record["data"]["analysis"] == {"status": "unresolved", "functions": [], "references": [], "categories": []}
    first = extractor(path, patterns=["formula"], limit=1)
    assert first["diagnostic_count"] == len(members)
    assert first["records"][0]["data"]["effective_formula"] is None
    cell_only = extractor(path, patterns=["cell"])
    assert cell_only["diagnostics"] == []


def test_shared_inspection_does_not_enable_grouped_calculation_or_edits(extractor, tmp_path):
    path = tmp_path / "protected.xlsx"
    _shared_source(path, [("A1", 'si="1" ref="A1:B1"', "1+2"), ("B1", 'si="1"', "")])
    extracted = extractor(path, patterns=["formula"])
    assert all(record["data"]["analysis"]["status"] == "parsed" for record in extracted["records"])
    imported = import_xlsx(path, outputs={"grouped": {"sheet": "Data", "address": "B1"}}, backend="python")
    assert imported.calculate()["diagnostics"]
    with pytest.raises(ValueError):
        imported.set_value("Data", "B1", 10)


def test_diagnostic_clipping_and_no_implicit_formula_expansion(extractor, tmp_path):
    path = tmp_path / "diagnostics.xlsx"
    members = [(f"A{row}", f'si="{row}"', "") for row in range(1, 106)]
    _shared_source(path, members)
    report = extractor(path, patterns=["formula"], limit=1)
    assert report["total_records"] == 105 and len(report["records"]) == 1
    assert report["diagnostic_count"] == 105 and len(report["diagnostics"]) == 100
    assert report["diagnostics_truncated"] is True
    sparse = tmp_path / "sparse-shared.xlsx"
    _shared_source(sparse, [("A1", 'si="0" ref="A1:XFD1048576"', "1+2")])
    report = extractor(sparse, patterns=["formula"])
    assert report["total_records"] == 1
    assert report["records"][0]["data"]["effective_formula"] == "1+2"
    assert report["diagnostics"] == []


@pytest.mark.parametrize("body", [
    "<f>1+2</f><f>40+2</f><v>3</v>",
    "<v>3</v><v>42</v>",
    "<is><t>first</t></is><is><t>second</t></is>",
    "<f>1<extLst>40</extLst>+2</f><v>3</v>",
    "<v>1<extLst>40</extLst>2</v>",
])
def test_ambiguous_primary_cell_content_is_rejected_before_pattern_selection(extractor, tmp_path, body):
    path = tmp_path / "ambiguous.xlsx"
    _fixture(path, sheet_xml=f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1">{body}</c></row></sheetData></worksheet>')
    before = path.read_bytes()
    for options in ({}, {"patterns": ["shared_string"], "sheet": "Odd Sheet"}):
        with pytest.raises((ValueError, TypeError, WorkbookError)):
            extractor(path, **options)
    if extractor.backend == "python":
        with pytest.raises((ValueError, WorkbookError)):
            import_xlsx(path, outputs={"out": {"sheet": "Data", "address": "A1"}})
    else:
        bindings = tmp_path / "bindings.json"
        bindings.write_text(json.dumps({"outputs": {"out": {"sheet": "Data", "address": "A1"}}}))
        binary = extractor.rust_binary.with_name("agent_workbook.exe" if os.name == "nt" else "agent_workbook")
        imported = subprocess.run(
            [str(binary), str(path), "--bindings", str(bindings), "--output-dir", str(tmp_path / "exports")],
            input=b"", capture_output=True, timeout=30,
        )
        assert imported.returncode != 0, "strict Rust import accepted ambiguous cell content"
    assert path.read_bytes() == before


def test_direct_character_data_includes_tails_without_descendant_text(extractor, tmp_path):
    path = tmp_path / "direct-text.xlsx"
    parts = _fixture(path)
    parts["xl/sharedStrings.xml"] = f'<sst xmlns="{MAIN}"><si>\n  <r><t>visible</t></r>\n  <rPh sb="0" eb="1"><t>phonetic</t></rPh>\n</si></sst>'.encode()
    parts["xl/worksheets/sheet1.xml"] = f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><f>IF(A1=&quot;visible&quot;,&quot;a&amp;b&quot;,&quot;&quot;)</f><v>0</v></c></row></sheetData></worksheet>'.encode()
    write_parts(path, parts)
    report = extractor(path, patterns=["shared_string", "formula"])
    string = _records(report, "shared_string")[0]
    assert string["text"] == "\n  \n  \n"
    assert string["data"]["value"] == "visible"
    formula = _at(report, "formula", "B1")
    assert formula["text"] == 'IF(A1="visible","a&b","")'
    assert formula["data"]["effective_formula"] == formula["text"]
    assert formula["data"]["analysis"]["status"] == "parsed"


def test_xml_depth_and_response_size_limits_refuse_without_source_changes(extractor, tmp_path):
    deep = tmp_path / "deep.xlsx"
    _fixture(deep, sheet_xml=f'<worksheet xmlns="{MAIN}"><sheetData/><extLst><ext uri="urn:deep">' + '<x>' * 129 + '</x>' * 129 + '</ext></extLst></worksheet>')
    with pytest.raises((ValueError, WorkbookError)):
        extractor(deep)
    large = tmp_path / "large-response.xlsx"
    rows = ''.join(f'<row r="{row}"><c r="A{row}" t="inlineStr"><is><t>{"x" * 32767}</t></is></c></row>' for row in range(1, 34))
    _fixture(large, sheet_xml=f'<worksheet xmlns="{MAIN}"><sheetData>{rows}</sheetData></worksheet>')
    with pytest.raises((ValueError, WorkbookError)):
        extractor(large, patterns=["cell"], sheet="Data")
    assert len(extractor(large, patterns=["cell"], sheet="Data", limit=1)["records"]) == 1


def test_selected_record_budget_is_applied_before_pagination(extractor, tmp_path):
    path = tmp_path / "record-budget.xlsx"
    rows = ''.join(f'<row r="{row}"><c r="A{row}"><f>1</f><v>1</v></c></row>' for row in range(1, 50002))
    _fixture(path, sheet_xml=f'<worksheet xmlns="{MAIN}"><sheetData>{rows}</sheetData></worksheet>')
    with pytest.raises((ValueError, WorkbookError)):
        extractor(path, patterns=["cell", "formula"], sheet="Data", limit=1)
    page = extractor(path, patterns=["formula"], sheet="Data", limit=1)
    assert page["counts"] == {"formula": 50001} and page["total_records"] == 50001
    assert page["next_offset"] == 1 and page["truncated"] is True


@pytest.mark.parametrize("bad_cell", [
    '<c r="A1" t="s"><v>-1</v></c>',
    '<c r="A1" s="-1"><v>1</v></c>',
])
def test_negative_stored_indices_never_wrap_to_last_entry(extractor, tmp_path, bad_cell):
    path = tmp_path / "negative-index.xlsx"
    parts = _fixture(path, sheet_xml=f'<worksheet xmlns="{MAIN}"><sheetData><row r="1">{bad_cell}</row></sheetData></worksheet>')
    parts["xl/styles.xml"] = f'<styleSheet xmlns="{MAIN}"><fonts count="1"><font/></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0"/></cellXfs></styleSheet>'.encode()
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(b"</Types>", b'<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
    parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace(b"</Relationships>", f'<Relationship Id="styles" Type="{REL}/styles" Target="styles.xml"/></Relationships>'.encode())
    write_parts(path, parts)
    with pytest.raises((ValueError, WorkbookError)):
        extractor(path, patterns=["cell"])


@pytest.mark.parametrize("scope", ["-1", "2", "not-an-index"])
def test_invalid_defined_name_scope_is_not_reclassified_as_global(extractor, tmp_path, scope):
    path = tmp_path / "invalid-scope.xlsx"
    parts = _fixture(path)
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(b'localSheetId="0"', f'localSheetId="{scope}"'.encode())
    write_parts(path, parts)
    with pytest.raises((ValueError, WorkbookError)):
        extractor(path, patterns=["defined_name"], sheet="Odd Sheet")
