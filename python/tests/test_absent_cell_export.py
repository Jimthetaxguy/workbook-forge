"""Absent-cell export parity with the Rust writer.

fixtures/absent-cell-insertion.xlsx has a dimension, row spans, a customFormat
row style, a column style, and two merges. Inserted values are the inputs
under test. Agreement between the engines is a package-shape check, not an
Excel result.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile
from xml.etree import ElementTree as ET

import pytest

from workbook_forge.agent import AgentWorkbook
from workbook_forge.workbook import UnsupportedWorkbook
from workbook_forge.xlsx import export_snapshot, export_xlsx, import_xlsx

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures/absent-cell-insertion.xlsx"
SPIKE = ROOT / "fixtures/univer-spike-chain.xlsx"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
# C5 is a new row after the last source row. A new row just before a
# rewritten row (C3 with C4) has its own parity test below.
INSERTIONS = {"C1": 9, "C4": 7, "C5": 8}


def _with_cells(document, cells):
    updated = json.loads(json.dumps(document))
    for address, value in cells.items():
        updated["sheets"][0]["cells"][address] = {"value": value}
    return updated


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _number(text: str | None):
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return text


def sheet_view(path: Path) -> dict:
    """Parsed worksheet shape. Whole-number text is compared numerically.

    The Python writer spells a new number with ``repr`` (``9.0``). The Rust
    writer uses the shortest decimal (``9``). The stored number is the same.
    """
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    dimension = next(
        (child.attrib.get("ref") for child in root if _local(child.tag) == "dimension"),
        None,
    )
    merges: list[str] = []
    columns = []
    for child in root:
        if _local(child.tag) == "mergeCells":
            merges = [
                node.attrib.get("ref", "")
                for node in child
                if _local(node.tag) == "mergeCell"
            ]
        elif _local(child.tag) == "cols":
            columns = [
                {
                    "min": node.attrib.get("min"),
                    "max": node.attrib.get("max"),
                    "style": node.attrib.get("style"),
                    "width": node.attrib.get("width"),
                }
                for node in child
                if _local(node.tag) == "col"
            ]
    rows = []
    data = next(child for child in root if _local(child.tag) == "sheetData")
    for row in data:
        if _local(row.tag) != "row":
            continue
        cells = []
        for cell in row:
            if _local(cell.tag) != "c":
                continue
            value = cell.find(f"{{{MAIN}}}v")
            formula = cell.find(f"{{{MAIN}}}f")
            cells.append(
                {
                    "r": cell.attrib.get("r"),
                    "s": cell.attrib.get("s"),
                    "v": _number(None if value is None else value.text),
                    "f": None if formula is None else formula.text,
                }
            )
        rows.append(
            {
                "r": row.attrib.get("r"),
                "spans": row.attrib.get("spans"),
                "s": row.attrib.get("s"),
                "customFormat": row.attrib.get("customFormat"),
                "cells": cells,
            }
        )
    return {"dimension": dimension, "merges": merges, "columns": columns, "rows": rows}


EXPECTED_INSERTION = {
    "dimension": "A1:C5",
    "merges": ["A1:A2", "F1:G2"],
    "columns": [{"min": "3", "max": "3", "style": "0", "width": "12"}],
    "rows": [
        {
            "r": "1",
            "spans": "1:3",
            "s": "1",
            "customFormat": "1",
            "cells": [
                {"r": "A1", "s": "1", "v": 1.0, "f": None},
                {"r": "B1", "s": "1", "v": 2.0, "f": None},
                {"r": "C1", "s": "1", "v": 9.0, "f": None},
            ],
        },
        {
            "r": "4",
            "spans": "1:3",
            "s": None,
            "customFormat": None,
            "cells": [
                {"r": "A4", "s": None, "v": 4.0, "f": None},
                {"r": "C4", "s": "0", "v": 7.0, "f": None},
            ],
        },
        {
            "r": "5",
            "spans": "3:3",
            "s": None,
            "customFormat": None,
            "cells": [{"r": "C5", "s": "0", "v": 8.0, "f": None}],
        },
    ],
}


def test_import_metadata_names_absent_cell_insertion():
    metadata = import_xlsx(FIXTURE).inspect()["xlsx"]
    assert (
        "absent-cell value/formula insertion within the session grid bound"
        in metadata["supported"]
    )
    assert "structural edits" not in metadata["rejected"]
    assert (
        "insertion into merged non-anchor cells or outside the session grid bound"
        in metadata["rejected"]
    )


def test_snapshot_export_keeps_source_cells_and_allows_insertions(tmp_path):
    imported = import_xlsx(SPIKE)
    document = _with_cells(imported.to_dict(), {"A2": 5})
    output = export_snapshot(imported, document, tmp_path / "inserted.xlsx")
    with zipfile.ZipFile(output) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
    assert 'r="A2"' in sheet
    removed = imported.to_dict()
    del removed["sheets"][0]["cells"]["A1"]
    with pytest.raises(UnsupportedWorkbook, match="retain all source cell addresses"):
        export_snapshot(imported, removed, tmp_path / "removed.xlsx")
    assert not (tmp_path / "removed.xlsx").exists()
    assert imported.to_dict()["sheets"][0]["cells"]["A1"]["value"] == 10


def test_snapshot_export_refuses_merged_non_anchor_and_added_guards(tmp_path):
    imported = import_xlsx(FIXTURE)
    covered = _with_cells(imported.to_dict(), {"A2": 8})
    with pytest.raises(UnsupportedWorkbook, match="inside a merged range"):
        export_snapshot(imported, covered, tmp_path / "merged.xlsx")
    blank = imported.to_dict()
    blank["sheets"][0]["cells"]["G2"] = {"value": None}
    with pytest.raises(UnsupportedWorkbook, match="inside a merged range"):
        export_snapshot(imported, blank, tmp_path / "blank.xlsx")
    guarded = _with_cells(imported.to_dict(), {"C5": 1})
    guarded["sheets"][0]["cells"]["C5"]["blocked_reason"] = "added guard"
    with pytest.raises(UnsupportedWorkbook, match="cannot add preserved-only guards"):
        export_snapshot(imported, guarded, tmp_path / "guard.xlsx")
    assert list(tmp_path.iterdir()) == []


def test_agent_refuses_merged_non_anchor_and_keeps_the_out_of_grid_code(tmp_path):
    imported = import_xlsx(FIXTURE)
    agent = AgentWorkbook(imported, output_dir=tmp_path)
    outside = agent.call(
        "edit",
        {"edits": [{"sheet": "Sheet1", "address": "XFE1", "value": 1}], "expected_revision": 0},
    )
    assert outside["error"]["code"] == "invalid_reference"
    assert imported.revision == 0
    covered = agent.call(
        "edit",
        {"edits": [{"sheet": "Sheet1", "address": "A2", "value": 8}], "expected_revision": 0},
    )
    assert covered["error"]["code"] == "unsupported_operation"
    assert "inside a merged range" in covered["error"]["message"]
    assert "A2" in covered["error"]["message"]
    assert imported.revision == 0
    # F1 anchors F1:G2 and is absent, so the insertion is allowed. G2 is not.
    anchor = agent.call(
        "edit",
        {"edits": [{"sheet": "Sheet1", "address": "F1", "value": 1}], "expected_revision": 0},
    )
    assert anchor["ok"], anchor
    assert imported.revision == 1
    other = agent.call(
        "edit",
        {"edits": [{"sheet": "Sheet1", "address": "G2", "value": 1}], "expected_revision": 1},
    )
    assert other["error"]["code"] == "unsupported_operation"
    assert imported.revision == 1


def test_inserted_cells_update_dimension_spans_and_inherited_style(tmp_path):
    imported = import_xlsx(FIXTURE)
    document = _with_cells(imported.to_dict(), INSERTIONS)
    output = export_snapshot(imported, document, tmp_path / "inserted.xlsx")
    assert sheet_view(output) == EXPECTED_INSERTION
    # A formula in an empty column-styled cell takes that column's style index.
    imported.set_formula("Sheet1", "C2", "=A1+1")
    formula_output = export_xlsx(imported, tmp_path / "formula.xlsx")
    view = sheet_view(formula_output)
    row2 = next(row for row in view["rows"] if row["r"] == "2")
    assert row2["spans"] == "3:3"
    assert row2["cells"] == [{"r": "C2", "s": "0", "v": 2.0, "f": "A1+1"}]


def test_new_row_between_existing_rows_gets_spans_and_column_style(tmp_path):
    imported = import_xlsx(FIXTURE)
    document = _with_cells(imported.to_dict(), {"C3": 8})
    output = export_snapshot(imported, document, tmp_path / "middle.xlsx")
    view = sheet_view(output)
    assert view["dimension"] == "A1:C4"
    assert [row["r"] for row in view["rows"]] == ["1", "3", "4"]
    assert view["rows"][0]["spans"] == "1:2"
    assert view["rows"][1] == {
        "r": "3",
        "spans": "3:3",
        "s": None,
        "customFormat": None,
        "cells": [{"r": "C3", "s": "0", "v": 8.0, "f": None}],
    }
    assert view["rows"][2]["spans"] == "1:2"
    assert view["merges"] == ["A1:A2", "F1:G2"]


def test_new_row_omits_spans_when_the_sheet_has_none(tmp_path):
    from test_workbook import make_xlsx

    source = tmp_path / "plain.xlsx"
    make_xlsx(source)
    imported = import_xlsx(source)
    imported.set_value("Sheet1", "A2", 5)
    output = export_xlsx(imported, tmp_path / "out.xlsx")
    view = sheet_view(output)
    assert view["dimension"] is None
    row2 = next(row for row in view["rows"] if row["r"] == "2")
    assert row2["spans"] is None
    assert row2["cells"][0]["r"] == "A2"
    assert row2["cells"][0]["s"] is None


@pytest.fixture(scope="module")
def compiler_roundtrip():
    cargo = shutil.which("cargo")
    if cargo is None:
        pytest.skip("Rust toolchain is unavailable; sheet comparison was not run")
    target = ROOT / "rust" / "target"
    result = subprocess.run(
        [
            cargo,
            "build",
            "--manifest-path",
            str(ROOT / "rust/Cargo.toml"),
            "--locked",
            "--offline",
            "--example",
            "compiler_roundtrip",
        ],
        env={**os.environ, "CARGO_TARGET_DIR": str(target)},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    return target / "debug" / "examples" / "compiler_roundtrip"


def test_python_and_rust_sheet_xml_agree_on_the_same_insertions(tmp_path, compiler_roundtrip):
    imported = import_xlsx(FIXTURE)
    document = _with_cells(imported.to_dict(), INSERTIONS)
    python_output = export_snapshot(imported, document, tmp_path / "python.xlsx")
    ir = tmp_path / "model.json"
    ir.write_text(json.dumps(document))
    rust_output = tmp_path / "rust.xlsx"
    completed = subprocess.run(
        [str(compiler_roundtrip), "emit", str(FIXTURE), str(ir), str(rust_output)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert sheet_view(python_output) == sheet_view(rust_output) == EXPECTED_INSERTION


def test_toolkit_insertion_on_merged_non_anchor_is_refused_at_export(tmp_path):
    # The agent adapter refuses this edit up front. A direct toolkit edit
    # reaches export, which applies the same guard as the Rust session.
    imported = import_xlsx(FIXTURE)
    imported.set_value("Sheet1", "G2", 1)
    with pytest.raises(UnsupportedWorkbook, match="inside a merged range"):
        export_xlsx(imported, tmp_path / "merged.xlsx")
    assert not (tmp_path / "merged.xlsx").exists()


def test_snapshot_export_refuses_renamed_sheet_changed_guard_and_out_of_grid(tmp_path):
    imported = import_xlsx(FIXTURE)
    renamed = imported.to_dict()
    renamed["sheets"][0]["name"] = "Renamed"
    with pytest.raises(UnsupportedWorkbook, match="retain all source cell addresses"):
        export_snapshot(imported, renamed, tmp_path / "renamed.xlsx")
    guarded = imported.to_dict()
    guarded["sheets"][0]["cells"]["A1"]["blocked_reason"] = "changed guard"
    with pytest.raises(UnsupportedWorkbook, match="cannot change preserved-only guards"):
        export_snapshot(imported, guarded, tmp_path / "guard.xlsx")
    outside = _with_cells(imported.to_dict(), {"XFE1": 1})
    with pytest.raises((UnsupportedWorkbook, ValueError)):
        export_snapshot(imported, outside, tmp_path / "outside.xlsx")
    assert list(tmp_path.iterdir()) == []


def test_agent_session_insertion_survives_snapshot_handoff_and_reimport(tmp_path):
    """The reproduced case: a Python agent session edits absent Forecast!Z100,
    then its snapshot is handed to export_snapshot and the result reimported."""
    from workbook_forge.toolkit import operating_scenario

    scenario = operating_scenario()
    source = export_xlsx(scenario, tmp_path / "source.xlsx")
    bindings = scenario.to_dict()
    session_model = import_xlsx(source, inputs=bindings["inputs"], outputs=bindings["outputs"])
    forecast = next(
        sheet for sheet in session_model.to_dict()["sheets"] if sheet["name"] == "Forecast"
    )
    assert "Z100" not in forecast["cells"] and "Z101" not in forecast["cells"]
    agent = AgentWorkbook(session_model, output_dir=tmp_path / "agent")
    response = agent.call(
        "edit",
        {
            "edits": [
                {"sheet": "Forecast", "address": "Z100", "value": 123},
                {"sheet": "Forecast", "address": "Z101", "formula": "=Z100*2"},
            ],
            "expected_revision": 0,
        },
    )
    assert response["ok"], response
    handoff = import_xlsx(source, inputs=bindings["inputs"], outputs=bindings["outputs"])
    output = export_snapshot(handoff, session_model.to_dict(), tmp_path / "handoff.xlsx")
    reopened = import_xlsx(output, inputs=bindings["inputs"], outputs=bindings["outputs"])
    cells = next(
        sheet for sheet in reopened.to_dict()["sheets"] if sheet["name"] == "Forecast"
    )["cells"]
    assert cells["Z100"]["value"] == 123
    assert cells["Z101"]["formula"].removeprefix("=") == "Z100*2"
    # Source cells and outputs are unchanged by the insertion.
    assert reopened.calculate()["outputs"] == scenario.calculate()["outputs"]
    # The exported package holds Z101's calculated cache next to its formula.
    with zipfile.ZipFile(output) as archive:
        sheets = [
            ET.fromstring(archive.read(name))
            for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet")
        ]
    z101 = [
        cell
        for root in sheets
        for cell in root.iter(f"{{{MAIN}}}c")
        if cell.attrib.get("r") == "Z101"
    ]
    assert len(z101) == 1
    assert z101[0].find(f"{{{MAIN}}}f").text == "Z100*2"
    # Calculated on reimport from the inserted Z100 value.
    assert import_xlsx(output).calculate()["values"]["Forecast!Z101"] == 246


def test_python_and_rust_agree_on_a_new_row_before_a_rewritten_row(tmp_path, compiler_roundtrip):
    # C3 creates row 3 just before row 4, which C4 rewrites. Both engines must
    # place row 3 first (the Rust patcher once reported overlapping patches).
    imported = import_xlsx(FIXTURE)
    document = _with_cells(imported.to_dict(), {"C3": 6, "C4": 7})
    python_output = export_snapshot(imported, document, tmp_path / "python.xlsx")
    ir = tmp_path / "model.json"
    ir.write_text(json.dumps(document))
    rust_output = tmp_path / "rust.xlsx"
    completed = subprocess.run(
        [str(compiler_roundtrip), "emit", str(FIXTURE), str(ir), str(rust_output)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    python_view, rust_view = sheet_view(python_output), sheet_view(rust_output)
    assert python_view == rust_view
    assert [row["r"] for row in python_view["rows"]] == ["1", "3", "4"]
