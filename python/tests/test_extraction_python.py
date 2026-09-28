from __future__ import annotations

import hashlib
import json

import pytest

from test_workbook import make_table_xlsx, make_xlsx, write_parts
from workbook_forge import extraction, xml_patterns
from workbook_forge.extraction import extract_xlsx, pattern_catalog
from workbook_forge.workbook import MAIN, REL_DOC, UnsupportedWorkbook, Workbook, WorkbookError, _safe_xml


def worksheet(cells: str, extra: str = "") -> bytes:
    return (f'<worksheet xmlns="{MAIN}" xmlns:r="{REL_DOC}">'
            f'<sheetData><row r="1">{cells}</row></sheetData>{extra}</worksheet>').encode()


def test_namespace_paths_exclude_extension_decoys_and_keep_original_package(tmp_path):
    source = tmp_path / "namespace.xlsx"
    xml = (f'<m:worksheet xmlns:m="{MAIN}" xmlns:e="urn:extension">'
           '<m:sheetData><m:row r="1"><e:c r="A1"><e:v>99</e:v></e:c>'
           '<m:c r="A1" e:note="safe"><m:v>2</m:v></m:c>'
           '<m:c r="B1"><m:f>SUM(A1,3)</m:f><m:v>5</m:v></m:c>'
           '<m:extLst><m:c r="A1"><m:v>77</m:v></m:c></m:extLst>'
           '</m:row></m:sheetData><m:extLst><m:c r="B1"><m:f>BAD()</m:f>'
           '</m:c></m:extLst></m:worksheet>').encode()
    make_xlsx(source, {"xl/worksheets/sheet1.xml": xml})
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = extract_xlsx(source, patterns=["formula", "cell"], sheet="sHEET1")
    assert report["total_records"] == 3
    assert [record["pattern"] for record in report["records"]] == ["cell", "cell", "formula"]
    first = report["records"][0]
    assert first["path"] == "/worksheet[1]/sheetData[1]/row[1]/c[1]"
    assert first["attributes"]["{urn:extension}note"] == "safe"
    assert first["sheet"] == "Sheet1"
    assert first["data"]["value"] == 2
    formula = report["records"][-1]["data"]
    assert formula["effective_formula"] == "SUM(A1,3)"
    assert formula["analysis"]["functions"] == [pattern_catalog()["functions"]["SUM"]]
    assert report["diagnostics"] == []
    with Workbook.open(source) as workbook:
        assert workbook.get("Sheet1", "A1").value == 2
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def test_shared_groups_keep_raw_formula_and_copy_anchors(tmp_path):
    source = tmp_path / "shared.xlsx"
    make_xlsx(source, {"xl/worksheets/sheet1.xml": worksheet(
        '<c r="B2"><f t="shared" si="01" ref="B2:B3">'
        'SUM($A1,A$1,$A$1,&quot;SUM(B9)&quot;)</f><v>9</v></c>'
        '<c r="B3"><f t="shared" si="1">ignored follower text</f><v>10</v></c>'
    )})
    report = extract_xlsx(source, patterns=["formula", "cell"])
    formulas = [record for record in report["records"] if record["pattern"] == "formula"]
    assert formulas[0]["data"]["effective_formula"] == 'SUM($A1,A$1,$A$1,"SUM(B9)")'
    follower = formulas[1]
    assert follower["text"] == "ignored follower text"
    assert follower["data"]["shared_index"] == "1"
    assert follower["data"]["master_cell"] == "B2"
    assert follower["data"]["effective_formula"] == '=SUM($A2,A$1,$A$1,"SUM(B9)")'
    assert len(follower["data"]["analysis"]["references"]) == 3
    references = follower["data"]["analysis"]["references"]
    assert references[0]["start"]["absolute_column"] is True
    assert references[0]["start"]["absolute_row"] is False
    assert references[1]["start"]["absolute_row"] is True
    assert report["records"][0]["data"] == {
        "value": None, "cached_value": 9, "cell_type": "n", "style_id": None,
    }
    assert report["diagnostics"] == []


@pytest.mark.parametrize("cells", [
    '<c r="B2"><f t="shared" si="0"/></c>',
    '<c r="B2"><f t="shared" si="0" ref="B2:B3">A1</f></c>'
    '<c r="B3"><f t="shared" si="0" ref="B2:B3">A2</f></c>',
    '<c r="B2"><f t="shared" si="4294967296" ref="B2">1</f></c>',
    '<c r="B2"><f t="shared" si="0" ref="B2:B3">A1</f></c>'
    '<c r="B4"><f t="shared" si="0"/></c>',
    '<c r="B2"><f t="shared" si="0" ref="B2:B3">LET(x,1,x)</f></c>'
    '<c r="B3"><f t="shared" si="0"/></c>',
    '<c r="B2"><f t="shared" si="0" ref="B3:B2">A1</f></c>',
    '<c r="B2"><f t="shared" si="-1" ref="B2">1</f></c>',
])
def test_unresolved_shared_groups_are_inspectable_and_diagnosed(tmp_path, cells):
    source = tmp_path / "malformed-group.xlsx"
    make_xlsx(source, {"xl/worksheets/sheet1.xml": worksheet(cells)})
    report = extract_xlsx(source, patterns=["formula"], limit=1)
    assert report["diagnostic_count"] == report["total_records"]
    assert all(item["code"] == "shared_formula" for item in report["diagnostics"])
    assert report["records"][0]["data"]["effective_formula"] is None
    assert report["records"][0]["data"]["analysis"]["status"] == "unresolved"
    assert extract_xlsx(source, patterns=["cell"])["diagnostic_count"] == 0


def test_strings_entities_cdata_and_direct_text(tmp_path):
    source = tmp_path / "strings.xlsx"
    parts = make_xlsx(source, {"xl/worksheets/sheet1.xml": worksheet(
        '<c r="A1" t="inlineStr"><is><t>A&amp;</t><r><t><![CDATA[B<C]]></t></r>'
        '<rPh><t>phonetic</t></rPh><extLst><t>decoy</t></extLst></is></c>'
        '<c r="B1" t="s"><v>0</v></c>'
        '<c r="C1" t="e"><v>#N/A</v></c>',
        '<dataValidations><dataValidation type="custom">first<extLst>hidden</extLst>'
        'tail<formula1>SUM(A1,1)</formula1>end</dataValidation></dataValidations>'
    )})
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
        b"</Types>", b'<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>')
    parts["xl/sharedStrings.xml"] = (
        f'<sst xmlns="{MAIN}"><si><t>A&amp;</t><r><t><![CDATA[B<C]]></t></r>'
        '<rPh><t>ignored</t></rPh><extLst><t>ignored</t></extLst></si></sst>'
    ).encode()
    write_parts(source, parts)
    report = extract_xlsx(source, patterns=["cell", "shared_string", "validation"])
    cells = [record for record in report["records"] if record["pattern"] == "cell"]
    assert [record["data"]["value"] for record in cells] == ["A&B<C", "A&B<C", {"error": "#N/A"}]
    shared = next(record for record in report["records"] if record["pattern"] == "shared_string")
    assert shared["data"]["value"] == "A&B<C"
    assert shared["sheet"] is None
    validation = report["records"][-1]
    assert validation["text"] == "firsttailend"
    assert validation["data"] == {"formula1": "SUM(A1,1)", "formula2": None}


def test_names_tables_columns_validation_and_global_filtering(tmp_path):
    source = tmp_path / "table.xlsx"
    parts = make_table_xlsx(
        source, worksheet('<c r="A1"><v>1</v></c>',
                          '<cols><col min="1" max="2" width="12"/></cols>'
                          '<mergeCells><mergeCell ref="D1:E1"/></mergeCells>'),
        'ref="A1:B4"',
        '<tableColumn id="2" name="Result"><calculatedColumnFormula>SUM(A2,1)</calculatedColumnFormula></tableColumn>',
    )
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(
        b"</workbook>", b'<definedNames><definedName name="Global">SUM(Sheet1!A1,2)</definedName>'
        b'<definedName name="Local" localSheetId="0">Sheet1!$A$1</definedName></definedNames></workbook>')
    write_parts(source, parts)
    report = extract_xlsx(source)
    table_records = [record for record in report["records"] if record["pattern"].startswith("table")]
    assert [record["pattern"] for record in table_records] == ["table", "table_column", "table_column", "table_formula"]
    assert all(record["sheet"] == "Sheet1" and record["cell"] is None for record in table_records)
    names = [record for record in report["records"] if record["pattern"] == "defined_name"]
    assert [record["sheet"] for record in names] == [None, "Sheet1"]
    filtered = extract_xlsx(source, patterns=["defined_name"], sheet="sheet1")
    assert filtered["total_records"] == 1
    assert filtered["records"][0]["attributes"]["name"] == "Local"
    assert report["counts"]["merged_range"] == report["counts"]["column"] == 1


def test_function_mapping_does_not_guess_from_unsupported_syntax_or_literals(tmp_path):
    source = tmp_path / "formulas.xlsx"
    make_xlsx(source, {"xl/worksheets/sheet1.xml": worksheet(
        '<c r="A1"><f>UNLISTED(&quot;SUM(B3)&quot;)</f></c>'
        '<c r="B1"><f>LET(x,1,x)</f></c>'
        '<c r="C1"><f t="array" ref="C1:C2">SUM(A1,2)</f></c>'
    )})
    report = extract_xlsx(source, patterns=["formula"])
    first, second, third = [record["data"] for record in report["records"]]
    assert first["analysis"]["functions"] == [{"name": "UNLISTED", "known": False,
                                                "category": None, "python": "unknown", "rust": "unknown"}]
    assert first["analysis"]["references"] == []
    assert second["analysis"] == {"status": "unsupported", "functions": [], "references": [], "categories": []}
    assert third["kind"] == "array"
    assert third["analysis"]["status"] == "parsed"
    assert report["diagnostic_count"] == 1
    assert report["diagnostics"][0]["cell"] == "B1"


@pytest.mark.parametrize("options", [
    {"limit": True}, {"limit": 0}, {"limit": 101}, {"offset": True},
    {"offset": -1}, {"offset": 2**64}, {"patterns": []},
    {"patterns": ["cell", "cell"]}, {"patterns": ["unknown"]},
    {"patterns": "cell"}, {"sheet": "Missing"}, {"sheet": ""},
])
def test_explicit_option_validation(tmp_path, options):
    source = tmp_path / "source.xlsx"
    make_xlsx(source)
    with pytest.raises(ValueError):
        extract_xlsx(source, **options)


def test_pages_count_full_selection_and_limits_refuse_output(tmp_path, monkeypatch):
    source = tmp_path / "limits.xlsx"
    make_xlsx(source, {"xl/worksheets/sheet1.xml": worksheet(
        ''.join(f'<c r="A{row}"><f>LET(x,1,x)</f></c>' for row in range(1, 111))
    )})
    report = extract_xlsx(source, patterns=["formula"], offset=109, limit=1)
    assert report["total_records"] == 110
    assert report["next_offset"] is None
    assert report["records"][0]["cell"] == "A110"
    assert report["diagnostic_count"] == 110
    assert len(report["diagnostics"]) == 100
    assert report["diagnostics_truncated"] is True
    assert extract_xlsx(source, patterns=["formula"], offset=2**64 - 1)["records"] == []
    monkeypatch.setattr(extraction, "MAX_RECORDS", 5)
    with pytest.raises(UnsupportedWorkbook, match="scan limit"):
        extract_xlsx(source, patterns=["formula"])
    monkeypatch.setattr(extraction, "MAX_RECORDS", 100_000)
    monkeypatch.setattr(extraction, "MAX_RESPONSE_BYTES", 100)
    with pytest.raises(UnsupportedWorkbook, match="response limit"):
        extract_xlsx(source, patterns=["formula"])


def test_bounded_parser_preserves_encodings_and_error_types(monkeypatch):
    source = '<?xml version="1.0" encoding="UTF-16"?><a>caf\u00e9</a>'.encode("utf-16")
    assert _safe_xml(source, "encoded.xml").text == "caf\u00e9"
    with pytest.raises(UnsupportedWorkbook, match="DTD"):
        _safe_xml('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE a><a/>'.encode("utf-16"), "dtd.xml")
    with pytest.raises(WorkbookError, match="invalid XML"):
        _safe_xml(b"<broken>", "malformed.xml")
    assert _safe_xml(b"<a>" * 128 + b"</a>" * 128, "depth.xml") is not None
    with pytest.raises(UnsupportedWorkbook, match="depth"):
        _safe_xml(b"<a>" * 129 + b"</a>" * 129, "depth.xml")
    monkeypatch.setattr(xml_patterns, "MAX_XML_NODES", 3)
    with pytest.raises(UnsupportedWorkbook, match="element count"):
        _safe_xml(b"<a><b/><b/><b/></a>", "nodes.xml")
    assert json.dumps(pattern_catalog())


@pytest.mark.parametrize("content", [
    '<f>1+2</f><f>40+2</f>', '<v>3</v><v>42</v>',
    '<is><t>one</t></is><is><t>two</t></is>',
    '<f>1<extLst>40</extLst>+2</f>', '<v>1<extLst>40</extLst>2</v>',
])
def test_ambiguous_scalar_xml_is_refused_even_when_not_selected(tmp_path, content):
    source = tmp_path / "ambiguous.xlsx"
    make_xlsx(source, {"xl/worksheets/sheet1.xml": worksheet(f'<c r="A1">{content}</c>')})
    with pytest.raises(UnsupportedWorkbook):
        extract_xlsx(source, patterns=["column"])
    with pytest.raises(UnsupportedWorkbook):
        Workbook.open(source)


@pytest.mark.parametrize("cell", [
    '<c r="A1" s="-1"><v>1</v></c>', '<c r="A1" t="s"><v>-1</v></c>',
])
def test_invalid_numeric_indices_do_not_wrap_or_escape_schema(tmp_path, cell):
    source = tmp_path / "negative-index.xlsx"
    make_xlsx(source, {"xl/worksheets/sheet1.xml": worksheet(cell)})
    with pytest.raises(WorkbookError):
        extract_xlsx(source, patterns=["cell"])


@pytest.mark.parametrize("index", ["1", "-1", "invalid", "4294967296"])
def test_invalid_name_scope_is_not_silently_global(tmp_path, index):
    source = tmp_path / "scope.xlsx"
    parts = make_xlsx(source)
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(
        b"</workbook>", f'<definedNames><definedName name="bad" localSheetId="{index}">1</definedName></definedNames></workbook>'.encode())
    write_parts(source, parts)
    with pytest.raises(WorkbookError, match="localSheetId"):
        extract_xlsx(source, patterns=["defined_name"], sheet="Sheet1")


def test_table_with_multiple_worksheet_owners_is_rejected(tmp_path):
    source = tmp_path / "multiple-owners.xlsx"
    parts = make_table_xlsx(source, worksheet('<c r="A1"><v>1</v></c>'),
                           'ref="A1:B4"', '<tableColumn id="2" name="Result"/>')
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
        b"</Types>", b'<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(
        b"</sheets>", b'<sheet name="Sheet2" sheetId="2" r:id="rId2"/></sheets>')
    parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace(
        b"</Relationships>", f'<Relationship Id="rId2" Type="{REL_DOC}/worksheet" Target="worksheets/sheet2.xml"/></Relationships>'.encode())
    parts["xl/worksheets/sheet2.xml"] = parts["xl/worksheets/sheet1.xml"]
    parts["xl/worksheets/_rels/sheet2.xml.rels"] = parts["xl/worksheets/_rels/sheet1.xml.rels"]
    write_parts(source, parts)
    with pytest.raises(UnsupportedWorkbook, match="multiple worksheet owners"):
        extract_xlsx(source, patterns=["table"], sheet="Sheet1")


def test_declared_unbound_table_remains_global(tmp_path):
    source = tmp_path / "unbound-table.xlsx"
    parts = make_table_xlsx(source, worksheet('<c r="A1"><v>1</v></c>'),
                           'ref="A1:B4"', '<tableColumn id="2" name="Result"/>')
    parts["xl/worksheets/sheet1.xml"] = worksheet('<c r="A1"><v>1</v></c>')
    write_parts(source, parts)
    report = extract_xlsx(source, patterns=["table"])
    assert report["total_records"] == 1
    assert report["records"][0]["sheet"] is None
    assert report["records"][0]["cell"] is None
    assert extract_xlsx(source, patterns=["table"], sheet="Sheet1")["total_records"] == 0
