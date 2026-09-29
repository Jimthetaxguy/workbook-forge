"""Intake: typed Workbook model from .xlsx."""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

from workbook_forge.intake import extract_dependencies, intake_workbook, main
from workbook_forge.model import Cell, Formula, Sheet, Workbook

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PKG = "http://schemas.openxmlformats.org/package/2006/content-types"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def write_intake_fixture(path: Path) -> Path:
    """Synthetic .xlsx: formula, cross-sheet ref, number format, empty cell."""
    parts = {
        "[Content_Types].xml": f"""<?xml version="1.0"?><Types xmlns="{PKG}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>""".encode(),
        "_rels/.rels": f"""<?xml version="1.0"?><Relationships xmlns="{PKG_REL}"><Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>""".encode(),
        "xl/workbook.xml": f"""<?xml version="1.0"?><workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/><sheet name="Sheet2" sheetId="2" r:id="rId2"/></sheets></workbook>""".encode(),
        "xl/_rels/workbook.xml.rels": f"""<?xml version="1.0"?><Relationships xmlns="{PKG_REL}"><Relationship Id="rId1" Type="{REL}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="{REL}/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="{REL}/styles" Target="styles.xml"/></Relationships>""".encode(),
        "xl/styles.xml": f"""<?xml version="1.0"?><styleSheet xmlns="{MAIN}"><numFmts count="1"><numFmt numFmtId="164" formatCode="0.00%"/></numFmts><fonts count="1"><font/></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="1"><border/></borders><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" applyNumberFormat="1"/></cellXfs></styleSheet>""".encode(),
        "xl/worksheets/sheet1.xml": f"""<?xml version="1.0"?><worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1"><v>10</v></c><c r="B1"><f>A1*2</f><v>20</v></c><c r="C1"/><c r="D1" s="1"><v>0.5</v></c><c r="E1"><f>Sheet2!A1</f><v>5</v></c></row></sheetData></worksheet>""".encode(),
        "xl/worksheets/sheet2.xml": f"""<?xml version="1.0"?><worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1"><v>5</v></c></row></sheetData></worksheet>""".encode(),
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return path


def test_core_types_are_importable_source_of_truth():
    assert Cell.__module__ == "workbook_forge.model"
    assert Formula.__module__ == "workbook_forge.model"
    assert Sheet.__module__ == "workbook_forge.model"
    assert Workbook.__module__ == "workbook_forge.model"


def test_intake_formula_cross_sheet_format_and_empty(tmp_path):
    path = write_intake_fixture(tmp_path / "intake.xlsx")
    wb = intake_workbook(path)
    assert isinstance(wb, Workbook)
    assert wb.source_path == str(path.resolve())
    assert {s.name for s in wb.sheets} == {"Sheet1", "Sheet2"}

    sheet1 = wb.sheet("Sheet1")
    assert sheet1 is not None

    # Formula cell
    b1 = sheet1.cells["B1"]
    assert b1.formula is not None
    assert b1.formula.expression == "=A1*2"
    assert "Sheet1!A1" in b1.formula.dependencies
    assert b1.formula.result == 20.0

    # Cross-sheet reference
    e1 = sheet1.cells["E1"]
    assert e1.formula is not None
    assert e1.formula.expression == "=Sheet2!A1"
    assert e1.formula.dependencies == ("Sheet2!A1",)

    # Number-formatted cell
    d1 = sheet1.cells["D1"]
    assert d1.value == 0.5
    assert d1.number_format == "0.00%"
    assert d1.data_type == "number"

    # Empty cell present in the map
    c1 = sheet1.cells["C1"]
    assert c1.value is None
    assert c1.formula is None
    assert c1.data_type == "blank"

    summary = wb.to_summary_dict()
    assert summary["sheet_count"] == 2
    assert summary["formula_count"] == 2
    assert summary["dependency_graph_size"] >= 2


def test_extract_dependencies_handles_sheet_bang():
    deps = extract_dependencies("=Sheet2!A1+B2", "Sheet1")
    assert deps == ("Sheet2!A1", "Sheet1!B2")


def test_cli_prints_json_summary(tmp_path):
    path = write_intake_fixture(tmp_path / "cli.xlsx")
    # module entry point
    proc = subprocess.run(
        [sys.executable, "-m", "workbook_forge.intake", str(path)],
        cwd=Path(__file__).resolve().parents[1],
        env={**dict(**__import__("os").environ), "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["sheet_count"] == 2
    assert payload["formula_count"] == 2
    assert "dependency_graph_size" in payload
