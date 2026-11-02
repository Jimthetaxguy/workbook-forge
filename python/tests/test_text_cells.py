"""Text cells round-trip exactly; a number written into a Text (@) cell is stored as text.

Cases are in tools/corpus/feature-cases.json (``text_writes``); the Rust engine
runs the same table in rust/tests/xlsx_features.rs.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from workbook_forge.xlsx import export_xlsx, import_xlsx

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "fixtures/corpus"
CASES = json.loads((ROOT / "tools/corpus/feature-cases.json").read_text(encoding="utf-8"))["text_writes"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c['address']}={c['value']!r}")
def test_text_writes_store_the_expected_type(case, tmp_path):
    model = import_xlsx(CORPUS / case["file"])
    model.set_value(case["sheet"], case["address"], case["value"])
    cell = next(s for s in model.to_dict()["sheets"] if s["name"] == case["sheet"])["cells"][case["address"]]
    assert cell["value"] == case["stored"]
    assert isinstance(cell["value"], str) == isinstance(case["stored"], str)
    target = tmp_path / "out.xlsx"
    export_xlsx(model, target)
    again = next(s for s in import_xlsx(target).to_dict()["sheets"] if s["name"] == case["sheet"])
    assert again["cells"][case["address"]]["value"] == case["stored"]


@pytest.mark.parametrize("name", ["hb_text_edge_cases.xlsx", "op_text_cells.xlsx", "xw_text_formats.xlsx"])
def test_text_round_trips_exactly(name, tmp_path):
    first = import_xlsx(CORPUS / name).to_dict()["sheets"][0]["name"]
    # Bind one cell as the output so formulas the engine does not calculate
    # are preserved rather than blocking export (the documented flow).
    model = import_xlsx(CORPUS / name, outputs={"edited": {"sheet": first, "address": "Z99"}})
    before = model.to_dict()
    target = tmp_path / "out.xlsx"
    model.set_value(first, "Z99", "edited")
    export_xlsx(model, target)
    after = import_xlsx(target).to_dict()
    for old, new in zip(before["sheets"], after["sheets"], strict=True):
        for address, cell in old["cells"].items():
            assert new["cells"][address]["value"] == cell["value"], f"{old['name']}!{address}"
            assert isinstance(new["cells"][address]["value"], str) == isinstance(cell["value"], str)


def test_long_and_astral_text_is_exact():
    cells = import_xlsx(CORPUS / "hb_text_edge_cases.xlsx").to_dict()["sheets"][0]["cells"]
    assert len(cells["A3"]["value"]) == 32767
    assert "\U0001F680" in cells["A4"]["value"] and "e\u0301" in cells["A4"]["value"]
    assert cells["B4"]["value"] == "  padded both ends  "
    assert cells["A1"]["value"] == "00123" and cells["B1"]["value"] == "00123"
    assert cells["A2"]["value"] == "=SUM(1,2)" and cells["A2"].get("formula") is None


def test_inserted_cells_inherit_text_rows_and_columns(tmp_path):
    """A new cell takes its row style when the row has customFormat, otherwise
    its column style. Text (@) there keeps numbers text."""
    model = import_xlsx(CORPUS / "hb_text_columns.xlsx")
    assert model.inspect()["xlsx"]["text_layouts"] == {
        "Codes": {"text_columns": [[1, 1]], "text_rows": [5], "general_rows": [6]}
    }
    model.apply([
        {"sheet": "Codes", "address": "A4", "value": 17},
        {"sheet": "Codes", "address": "Z5", "value": 1.25},
        {"sheet": "Codes", "address": "A6", "value": 8},
        {"sheet": "Codes", "address": "B4", "value": 5},
        {"sheet": "Codes", "address": "A3", "value": 7},
    ])
    cells = model.to_dict()["sheets"][0]["cells"]
    assert [cells[a]["value"] for a in ("A4", "Z5", "A6", "B4", "A3")] == ["17", "1.25", 8, 5, 7]
    # A later write into the inserted cell is still text, and so is the cell
    # after export and reopen (the writer gives it the inherited style).
    model.set_value("Codes", "A4", 18)
    assert model.to_dict()["sheets"][0]["cells"]["A4"]["value"] == "18"
    target = tmp_path / "out.xlsx"
    export_xlsx(model, target)
    again = import_xlsx(target)
    assert again.to_dict()["sheets"][0]["cells"]["A4"]["style"]["number_format"] == "@"
    again.set_value("Codes", "A4", 19)
    assert again.to_dict()["sheets"][0]["cells"]["A4"]["value"] == "19"


def test_a_styled_column_with_a_bad_span_is_skipped(tmp_path):
    """A styled <col> with a missing or non-numeric min/max is skipped.

    The well-formed ranges in the same sheet still apply.
    """
    source = CORPUS / "hb_text_columns.xlsx"
    damaged = tmp_path / "missing-min.xlsx"
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(damaged, "w") as out:
        for name in archive.namelist():
            payload = archive.read(name)
            if name == "xl/worksheets/sheet1.xml":
                payload = payload.replace(
                    b"<cols>",
                    b"<cols>"
                    b'<col max="2" width="8" style="1" customWidth="1"/>'
                    b'<col min="nope" max="9" width="8" style="1"/>'
                    b'<col min="5" max="wide" width="8" style="1"/>',
                    1,
                )
            out.writestr(name, payload)
    model = import_xlsx(damaged)
    assert model.inspect()["xlsx"]["text_layouts"] == {
        "Codes": {"text_columns": [[1, 1]], "text_rows": [5], "general_rows": [6]}
    }
    assert model.to_dict()["sheets"][0]["column_widths"] == {"A": 12.0, "C": 10.0, "D": 10.0}
