"""Canonical calculation stays linear in the number of formulas.

fixtures/corpus/op_large_60k.xlsx has 10,000 rows of four readings and an
AVERAGE per row. Before the dependency-only environment, every formula built
an environment of the whole workbook, so this took many minutes. Expected
means are independent arithmetic over the stored readings.
"""
from __future__ import annotations

from pathlib import Path
import time

from workbook_forge.intake import intake_workbook_model
from workbook_forge.model import calculate

ROOT = Path(__file__).resolve().parents[2]


def test_ten_thousand_row_averages_calculate_quickly_and_correctly():
    workbook = intake_workbook_model(ROOT / "fixtures/corpus/op_large_60k.xlsx")
    started = time.perf_counter()
    calculated = calculate(workbook)
    elapsed = time.perf_counter() - started
    assert elapsed < 60, f"calculation took {elapsed:.1f} s"
    sheet = calculated.sheet("Readings")
    for row in (2, 5000, 10001):
        readings = [sheet.cells[f"{column}{row}"].value for column in "BCDE"]
        cell = sheet.cells[f"F{row}"]
        assert abs(cell.formula.result - sum(readings) / 4) < 1e-9
