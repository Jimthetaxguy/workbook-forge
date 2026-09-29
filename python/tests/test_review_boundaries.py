"""Focused regressions for three concrete PR boundary findings."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
import zipfile

import jsonschema
import pytest

from workbook_forge.workbook import WorkbookError
from workbook_forge.xlsx import import_xlsx
from test_agent_contract import agent_factory, rust_agent_binary, _ok, _call, _revision
from test_extraction_contract import extractor, rust_extractor, _fixture, MAIN

ROOT = Path(__file__).resolve().parents[2]
PREFIXED = {
    "C1": "_xlfn.XLOOKUP(2,A1:A2,B1:B2)",
    "C2": "SUM(_xlfn._xlws.FILTER(B1:B2,A1:A2>1))",
}


def _source(path):
    rows = (
        '<row r="1"><c r="A1"><v>1</v></c><c r="B1"><v>10</v></c>'
        f'<c r="C1"><f>{PREFIXED["C1"]}</f><v>999</v></c></row>'
        '<row r="2"><c r="A2"><v>2</v></c><c r="B2"><v>20</v></c>'
        f'<c r="C2"><f>{PREFIXED["C2"]}</f><v>998</v></c></row>'
    )
    _fixture(path, sheet_xml=f'<worksheet xmlns="{MAIN}"><sheetData>{rows}</sheetData></worksheet>')


def test_supported_excel_prefixes_calculate_and_preserve_original_xml(agent_factory, tmp_path):
    source = tmp_path / "prefixes.xlsx"
    _source(source)
    original = source.read_bytes()
    client = agent_factory(source=source, bindings={"outputs": {
        "lookup": {"sheet": "Data", "address": "C1"},
        "filtered_sum": {"sheet": "Data", "address": "C2"},
    }})
    calculation = _ok(client, "calculate")
    assert calculation["outputs"] == {"lookup": 20, "filtered_sum": 20}
    assert calculation["diagnostics"] == []
    observed = _ok(client, "read", {"sheet": "Data", "range": "C1:C2"})
    assert {cell["address"]: cell["content"]["formula"] for cell in observed["cells"]} == PREFIXED
    _ok(client, "export", {"filename": "preserved.xlsx", "expected_revision": 0})
    with zipfile.ZipFile(client.output_dir / "preserved.xlsx") as package:
        sheet = ET.fromstring(package.read("xl/worksheets/sheet1.xml"))
        formulas = {cell.attrib["r"]: cell.find(f"{{{MAIN}}}f").text
                    for cell in sheet.findall(f"{{{MAIN}}}sheetData/{{{MAIN}}}row/{{{MAIN}}}c")
                    if cell.find(f"{{{MAIN}}}f") is not None}
    assert formulas == PREFIXED
    assert source.read_bytes() == original


def test_supported_excel_prefixes_map_to_canonical_functions_in_extraction(extractor, tmp_path):
    path = tmp_path / "prefixes.xlsx"
    _source(path)
    report = extractor(path, patterns=["formula"], sheet="Data")
    assert report["diagnostics"] == []
    records = {record["cell"]: record for record in report["records"]}
    for address, names in [("C1", ["XLOOKUP"]), ("C2", ["FILTER", "SUM"])]:
        record = records[address]
        assert record["text"] == PREFIXED[address]
        assert record["data"]["effective_formula"] == PREFIXED[address]
        assert [function["name"] for function in record["data"]["analysis"]["functions"]] == names
        assert all(function["known"] and function["python"] == "conformance-tested" and function["rust"] == "conformance-tested"
                   for function in record["data"]["analysis"]["functions"])


@pytest.mark.parametrize("field", ["formula", "style"])
def test_discovery_does_not_advertise_null_only_edits(agent_factory, field):
    client = agent_factory()
    catalog = _ok(client, "discover")
    schema = next(operation["input_schema"] for operation in catalog["operations"] if operation["name"] == "edit")
    arguments = {"edits": [{"sheet": "Assumptions", "address": "B1", field: None}], "expected_revision": 0}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(arguments, schema)
    result = _call(client, "edit", arguments)
    assert result["ok"] is False
    assert _revision(client) == 0
    clear = {"edits": [{"sheet": "Forecast", "address": "A1", "value": None}], "expected_revision": 0}
    jsonschema.validate(clear, schema)
    assert _call(client, "edit", clear)["ok"] is True
    assert _revision(client) == 1


@pytest.mark.parametrize("payload", ["<is><t>conflict</t></is><v>2</v>", "<is><t>conflict</t></is><f>1+2</f>"])
def test_inline_string_cannot_coexist_with_scalar_or_formula_payload(extractor, tmp_path, payload):
    source = tmp_path / "mixed-payload.xlsx"
    _fixture(source, sheet_xml=f'<worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1" t="inlineStr">{payload}</c></row></sheetData></worksheet>')
    before = source.read_bytes()
    for options in ({}, {"patterns": ["shared_string"], "sheet": "Odd Sheet"}):
        with pytest.raises((ValueError, WorkbookError)):
            extractor(source, **options)
    if extractor.backend == "python":
        with pytest.raises((ValueError, WorkbookError)):
            import_xlsx(source)
    else:
        executable = extractor.rust_binary.with_name("agent_workbook.exe" if os.name == "nt" else "agent_workbook")
        result = subprocess.run([str(executable), str(source), "--output-dir", str(tmp_path / "exports")],
                                input=b"", capture_output=True, timeout=30)
        assert result.returncode != 0, "Rust import accepted conflicting cell representations"
    assert source.read_bytes() == before
