"""CSV/TSV import (docs/csv-import.md). The case table is shared with rust/tests/delimited.rs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workbook_forge.delimited import decode, field_value, import_csv, parse
from workbook_forge.workbook import WorkbookError
from workbook_forge.xlsx import export_xlsx, import_xlsx

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "tools/corpus/csv-cases.json").read_text(encoding="utf-8"))["cases"]


def _input(case: dict) -> bytes:
    return bytes.fromhex(case["hex"]) if "hex" in case else case["text"].encode("utf-8")


def _cells(model) -> dict:
    return {
        address: cell["value"] for address, cell in model.to_dict()["sheets"][0]["cells"].items()
    }


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_shared_cases(case: dict) -> None:
    options = case.get("options", {})
    if "refuse" in case:
        with pytest.raises(WorkbookError) as raised:
            import_csv(_input(case), **options)
        assert raised.value.code == case["refuse"]
        assert str(raised.value).startswith(case["refuse"] + ": ")
        return
    model = import_csv(_input(case), **options)
    assert _cells(model) == case["cells"]
    for address, expected in case["cells"].items():
        got = _cells(model)[address]
        assert type(got) is (str if isinstance(expected, str) else float), (address, got)


def test_shared_cases_match_on_the_rust_backend() -> None:
    pytest.importorskip("workbook_forge._native")
    for case in CASES:
        if "refuse" in case:
            continue
        model = import_csv(_input(case), backend="rust", **case.get("options", {}))
        assert _cells(model) == case["cells"], case["name"]


def test_refusal_codes_are_registered() -> None:
    codes = json.loads((ROOT / "catalog/refusal-codes.json").read_text(encoding="utf-8"))["codes"]
    used = {case["refuse"] for case in CASES if "refuse" in case}
    assert used == {"csv_encoding", "csv_malformed"}
    assert used <= set(codes)


def test_tsv_suffix_picks_a_tab(tmp_path: Path) -> None:
    source = tmp_path / "parts.tsv"
    source.write_bytes(b"a,b\tc\n")
    assert _cells(import_csv(source)) == {"A1": "a,b", "B1": "c"}
    other = tmp_path / "parts.csv"
    other.write_bytes(b"a,b\tc\n")
    assert _cells(import_csv(other)) == {"A1": "a", "B1": "b\tc"}


def test_export_round_trip_keeps_text_text(tmp_path: Path) -> None:
    model = import_csv(b"id,amount,note\n00123,1.5,=SUM(1;2)\n007,-2,TRUE\n", sheet_name="Data")
    output = export_xlsx(model, tmp_path / "out.xlsx")
    reopened = import_xlsx(output).to_dict()
    sheet = reopened["sheets"][0]
    assert sheet["name"] == "Data"
    values = {address: cell.get("value") for address, cell in sheet["cells"].items()}
    assert values == {
        "A1": "id",
        "B1": "amount",
        "C1": "note",
        "A2": "00123",
        "B2": 1.5,
        "C2": "=SUM(1;2)",
        "A3": "007",
        "B3": -2,
        "C3": "TRUE",
    }
    assert all(cell.get("formula") is None for cell in sheet["cells"].values())


def test_imported_model_edits_and_calculates() -> None:
    model = import_csv(b"qty,price\n3,2.5\n")
    model.apply([{"sheet": "Sheet1", "address": "C2", "formula": "A2*B2"}])
    report = model.calculate()
    assert report["values"]["Sheet1!C2"] == 7.5


def test_grid_limits_refuse_out_of_bounds() -> None:
    with pytest.raises(WorkbookError) as raised:
        import_csv(("," * 16_384 + "\n").encode())
    assert raised.value.code == "out_of_bounds"


def test_helpers_are_consistent() -> None:
    assert decode(b"\xef\xbb\xbfx") == "x"
    assert parse('a,"b"\r\n', ",") == [["a", "b"]]
    assert field_value("0") == 0.0 and field_value("") is None
    with pytest.raises(ValueError):
        parse("a", ":")
