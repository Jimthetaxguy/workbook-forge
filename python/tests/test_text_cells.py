"""Text cells round-trip exactly; a number written into a Text (@) cell is stored as text.

Cases are in tools/corpus/feature-cases.json (``text_writes``); the Rust engine
runs the same table in rust/tests/xlsx_features.rs.
"""
from __future__ import annotations

import json
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
