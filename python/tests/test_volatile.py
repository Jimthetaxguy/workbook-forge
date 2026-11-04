"""Volatile functions: one context per pass, dirty on every pass, session
offset, and the pinned overrides. Mirrors rust/tests/volatile.rs.

Expected serials are independent arithmetic: 2026-01-15 is 1900-system serial
46037 (2026-01-01 = 46023) and 12:00 is .5; 2026-11-03 is 46329.
"""

import importlib
import importlib.util
import json

import pytest

from workbook_forge import CalcContext, evaluate
from workbook_forge import python_engine
from workbook_forge.model import calculate as canonical_calculate, hydrate
from workbook_forge.volatile import parse_instant, parse_seed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOON = 1_768_478_400_000  # 2026-01-15T12:00:00Z
BEFORE_MIDNIGHT = 1_793_768_399_999  # 2026-11-04T04:59:59.999Z = 23:59:59.999 at UTC-05:00


def _engines():
    engines = [python_engine]
    if importlib.util.find_spec("workbook_forge._native") is not None:
        engines.append(importlib.import_module("workbook_forge._native"))
    return engines


def _document(cells):
    return json.dumps(
        {
            "schema_version": 1,
            "revision": 0,
            "sheets": [{"id": "s", "name": "Sheet1", "cells": {a: {"formula": f} for a, f in cells.items()}}],
            "inputs": {},
            "outputs": {},
        }
    )


def test_instants_need_an_explicit_offset():
    assert parse_instant("0") == 0
    assert parse_instant("1970-01-01T00:00:00Z") == 0
    assert parse_instant("2026-11-03T20:00:00-05:00") == parse_instant("2026-11-04T01:00:00Z")
    assert parse_instant("1970-01-01T00:00:00.25Z") == 250
    assert parse_instant("2026-01-15T12:00:00Z") == NOON
    for bad in ("2026-11-03T20:00:00", "2026-02-30T00:00:00Z", "noon"):
        with pytest.raises(ValueError):
            parse_instant(bad)
    assert parse_seed("18446744073709551615") == 2**64 - 1
    with pytest.raises(ValueError):
        parse_seed("18446744073709551616")


def test_context_serial_rules_match_the_rust_unit_tests():
    assert CalcContext(BEFORE_MIDNIGHT, -300, 0).today_serial() == 46329
    assert CalcContext(BEFORE_MIDNIGHT + 1, -300, 0).today_serial() == 46330
    assert CalcContext(0, 0, 0).now_serial() == 25569.0
    assert CalcContext(43_200_000, 0, 0).now_serial() == 25569.5
    context = CalcContext(0, 0, 42)
    first = context.uniform("Sheet1!A1", 0, 0)
    assert 0.0 <= first < 1.0
    assert first == context.uniform("Sheet1!A1", 0, 0)
    assert len({first, context.uniform("Sheet1!A2", 0, 0), context.uniform("Sheet1!A1", 1, 0), context.uniform("Sheet1!A1", 0, 1)}) == 4
    with pytest.raises(ValueError):
        CalcContext(0, 841, 0)
    assert CalcContext.from_dict(CalcContext(5, -300, 2**64 - 1).to_dict()) == CalcContext(5, -300, 2**64 - 1)


def test_unpinned_capture_reads_the_clock_and_a_fresh_seed(monkeypatch):
    for name in ("FORGE_CALC_NOW", "FORGE_CALC_SEED", "FORGE_CALC_UTC_OFFSET_MINUTES"):
        monkeypatch.delenv(name, raising=False)
    first, second = CalcContext.capture(-300), CalcContext.capture(-300)
    assert first.utc_offset_minutes == -300
    assert first.now_ms > NOON
    assert first.seed != second.seed
    monkeypatch.setenv("FORGE_CALC_NOW", "2026-01-15T12:00:00Z")
    monkeypatch.setenv("FORGE_CALC_SEED", "9")
    monkeypatch.setenv("FORGE_CALC_UTC_OFFSET_MINUTES", "60")
    assert CalcContext.capture(-300) == CalcContext(NOON, 60, 9)
    # An invalid override is ignored, never guessed.
    monkeypatch.setenv("FORGE_CALC_NOW", "2026-01-15T12:00:00")
    monkeypatch.setenv("FORGE_CALC_UTC_OFFSET_MINUTES", "9999")
    captured = CalcContext.capture(-300)
    assert captured.now_ms > NOON and captured.utc_offset_minutes == -300


def test_the_suite_pins_the_clock_for_both_engines():
    for engine in _engines():
        report = json.loads(engine.calculate(_document({"A1": "=NOW()", "B1": "=TODAY()"})))
        assert report["values"] == {"Sheet1!A1": 46037.5, "Sheet1!B1": 46037.0}, engine.__name__
        assert report["calc"] == {"now_ms": NOON, "utc_offset_minutes": 0, "seed": "20260115"}


def test_every_now_in_one_pass_reads_the_same_instant():
    report = json.loads(
        python_engine.calculate(
            _document({"A1": "=NOW()", "B1": "=NOW()", "C1": "=A1-B1", "D1": "=TODAY()"}),
            context=CalcContext(NOON, 0, 1),
        )
    )
    assert report["calc"] == {"now_ms": NOON, "utc_offset_minutes": 0, "seed": "1"}
    assert report["values"] == {"Sheet1!A1": 46037.5, "Sheet1!B1": 46037.5, "Sheet1!C1": 0.0, "Sheet1!D1": 46037.0}
    assert evaluate("=NOW()-NOW()", context=CalcContext(NOON, 0, 1)) == 0


def test_the_session_offset_moves_today_across_local_midnight():
    session = python_engine.Session(_document({"A1": "=TODAY()"}))
    session.utc_offset_minutes = -300
    assert session.utc_offset_minutes == -300
    with pytest.raises(python_engine.ToolkitError):
        session.utc_offset_minutes = 841
    before = json.loads(session.calculate(context=CalcContext(BEFORE_MIDNIGHT, -300, 0)))
    after = json.loads(session.calculate(context=CalcContext(BEFORE_MIDNIGHT + 1, -300, 0)))
    assert before["values"]["Sheet1!A1"] == 46329.0
    assert after["values"]["Sheet1!A1"] == 46330.0


def test_a_volatile_formula_and_its_dependents_recalculate_on_every_pass():
    session = python_engine.Session(_document({"A1": "=NOW()", "B1": "=A1+1", "C1": "=1+1"}))
    first = json.loads(session.calculate(context=CalcContext(NOON, 0, 1)))
    assert len(first["evaluated_cells"]) == 3
    session.apply(json.dumps([{"sheet": "Sheet1", "address": "D1", "value": 5}]))
    second = json.loads(session.calculate(context=CalcContext(NOON + 43_200_000, 0, 1)))
    assert second["evaluated_cells"] == ["Sheet1!A1", "Sheet1!B1"]
    assert second["values"]["Sheet1!A1"] == 46038.0
    assert second["values"]["Sheet1!B1"] == 46039.0
    third = json.loads(session.calculate(context=CalcContext(NOON, 0, 1)))
    assert third["evaluated_cells"] == ["Sheet1!A1", "Sheet1!B1"]
    assert third["values"]["Sheet1!A1"] == 46037.5


def test_a_report_without_volatile_cells_carries_no_context():
    for engine in _engines():
        assert "calc" not in json.loads(engine.calculate(_document({"A1": "=1+1"}))), engine.__name__


def test_canonical_calculation_takes_one_context_for_the_pass():
    workbook = hydrate((ROOT / "fixtures" / "operating-scenario.workbook.json").read_bytes())
    pinned = CalcContext(BEFORE_MIDNIGHT, -300, 0)
    calculated = canonical_calculate(workbook, context=pinned)
    assert calculated.sheet("Forecast").cells["J1"].formula.result == 46329.99999998843
    assert canonical_calculate(calculated, context=pinned).to_json() == calculated.to_json()


def _reference_uniform(seed, key, site, draw):
    """docs/volatile-functions.md's stream, written out independently."""
    mask, golden = (1 << 64) - 1, 0x9E3779B97F4A7C15

    def mix(z):
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & mask
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & mask
        return z ^ (z >> 31)

    z = 0xCBF29CE484222325
    for byte in key.encode():
        z = ((z ^ byte) * 0x100000001B3) & mask
    z = mix(((seed ^ z) + golden * (site + 1)) & mask)
    z = mix((z + golden * (draw + 1)) & mask)
    return (z >> 11) / 2**53


def test_random_draws_agree_across_engines_and_follow_the_documented_stream():
    document = _document({"A1": "=RAND()", "B1": "=RANDBETWEEN(1,6)", "C1": "=SUM(RANDARRAY(2,2))", "D1": "=A1+0"})
    seed = 20260115
    expected_a1 = _reference_uniform(seed, "Sheet1!A1", 0, 0)
    reports = [json.loads(engine.calculate(document)) for engine in _engines()]
    for report in reports:
        assert report["values"]["Sheet1!A1"] == expected_a1
        assert report["values"]["Sheet1!D1"] == expected_a1
        assert report["values"]["Sheet1!B1"] == 1 + int(_reference_uniform(seed, "Sheet1!B1", 0, 0) * 6)
        assert report["values"]["Sheet1!C1"] == pytest.approx(
            sum(_reference_uniform(seed, "Sheet1!C1", 0, draw) for draw in range(4)), abs=1e-12
        )
    assert all(report["values"] == reports[0]["values"] for report in reports)


def test_a_new_seed_rerolls_and_the_same_seed_repeats():
    session = python_engine.Session(_document({"A1": "=RAND()", "B1": "=A1*2"}))
    first = json.loads(session.calculate(context=CalcContext(NOON, 0, 1)))["values"]
    again = json.loads(session.calculate(context=CalcContext(NOON, 0, 1)))["values"]
    other = json.loads(session.calculate(context=CalcContext(NOON, 0, 2)))["values"]
    assert first == again
    assert other["Sheet1!A1"] != first["Sheet1!A1"]
    assert other["Sheet1!B1"] == other["Sheet1!A1"] * 2
