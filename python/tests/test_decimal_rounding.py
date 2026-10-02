"""The rounding functions on numbers as they are written.

A two-decimal amount such as 19.99 has no exact binary form. Rounding the
stored value exactly made TRUNC(19.99,2) return 19.98 and moved 573 of the
9,999 amounts from 0.01 to 99.99 by a cent.

No expected value here comes from an evaluator. Each is built with integer
or decimal arithmetic on the number as it is written in the formula.
"""
from __future__ import annotations

import decimal
import importlib
import importlib.util
import json
import math
import random
import struct

import pytest

from workbook_forge import evaluate, python_engine

CENTS = range(1, 10_000)


def _amount(cents: int, extra: str = "") -> str:
    """Whole cents as decimal text, with optional further digits."""
    return f"{cents // 100}.{cents % 100:02d}{extra}"


def _moved(template: str, extra: str, step: int, sign: str) -> list[str]:
    moved = []
    for cents in CENTS:
        written = sign + _amount(cents, extra)
        expected = float(sign + _amount(cents + step))
        if evaluate(template.format(written)) != expected:
            moved.append(written)
    return moved


@pytest.mark.parametrize("sign", ["", "-"])
@pytest.mark.parametrize("template", ["=ROUND({},2)", "=ROUNDUP({},2)", "=ROUNDDOWN({},2)", "=TRUNC({},2)"])
def test_a_two_decimal_amount_rounded_to_two_decimals_is_itself(template, sign):
    assert _moved(template, "", 0, sign) == []


@pytest.mark.parametrize("sign", ["", "-"])
@pytest.mark.parametrize(
    ("template", "extra", "step"),
    [
        # Half a cent goes away from zero.
        ("=ROUND({},2)", "5", 1),
        ("=ROUND({},2)", "4999", 0),
        # Anything past the cent goes away from zero.
        ("=ROUNDUP({},2)", "001", 1),
        # Nothing short of the next cent reaches it.
        ("=ROUNDDOWN({},2)", "999", 0),
        ("=TRUNC({},2)", "999", 0),
    ],
)
def test_digits_past_the_cent_move_the_amount_as_decimal_arithmetic_says(template, extra, step, sign):
    assert _moved(template, extra, step, sign) == []


@pytest.mark.parametrize("sign", ["", "-"])
def test_whole_units_are_kept_by_every_function_at_no_decimals(sign):
    for template in ["=ROUND({},0)", "=ROUNDUP({},0)", "=ROUNDDOWN({},0)", "=TRUNC({})"]:
        for units in [1, 7, 99, 100, 123_456_789, 999_999_999_999_999, 4_503_599_627_370_497, 9_007_199_254_740_992]:
            assert evaluate(template.format(f"{sign}{units}")) == float(f"{sign}{units}"), (template, units)


def test_a_caller_changing_decimal_settings_does_not_change_a_result():
    import decimal

    with decimal.localcontext() as context:
        context.prec = 3
        context.rounding = decimal.ROUND_CEILING
        assert evaluate("=ROUND(12345.675,2)") == 12345.68
        assert evaluate("=TRUNC(19.99,2)") == 19.99


# Every magnitude and every place, for numbers of up to 15 digits

ROUNDINGS = {
    "ROUND": decimal.ROUND_HALF_UP,
    "ROUNDUP": decimal.ROUND_UP,
    "ROUNDDOWN": decimal.ROUND_DOWN,
    "TRUNC": decimal.ROUND_DOWN,
}
ARITHMETIC = decimal.Context(prec=400)


def _written_numbers(seed: int, count: int, most_digits: int = 15):
    """Numbers written with 1 to `most_digits` digits, from 1E-14 to 1E+16, and a place for each."""
    chooser = random.Random(seed)
    for _ in range(count):
        length = chooser.randint(1, most_digits)
        digits = str(chooser.randint(1, 9)) + "".join(chooser.choice("0123456789") for _ in range(length - 1))
        sign = "-" if chooser.random() < 0.4 else ""
        value = decimal.Decimal(sign + digits).scaleb(chooser.randint(-14, 16) - length + 1)
        yield value, chooser.randint(-8, 17)


def _by_decimal_arithmetic(value: decimal.Decimal, places: int, rounding: str) -> float:
    if value.as_tuple().exponent >= -places:
        return float(value)  # nothing is written below the place
    return float(value.quantize(decimal.Decimal((0, (1,), -places)), rounding=rounding, context=ARITHMETIC))


@pytest.mark.parametrize("name", sorted(ROUNDINGS))
def test_a_number_of_up_to_15_digits_rounds_as_decimal_arithmetic_says(name):
    wrong = []
    for value, places in _written_numbers(20260929, 6000):
        expected = _by_decimal_arithmetic(value, places, ROUNDINGS[name])
        actual = evaluate(f"={name}({value:f},{places})")
        if actual != expected:
            wrong.append((f"{value:f}", places, actual, expected))
    assert wrong == []


def test_the_whole_number_functions_round_as_integer_arithmetic_says():
    wrong = []
    for cents in range(1, 10_000):
        # 19.99*100 is stored as 1998.9999999999998. The amount in cents is 1999.
        for sign in ("", "-"):
            product = f"{sign}{_amount(cents)}*100"
            floor = -cents if sign else cents
            away = cents
            expected = {
                f"=INT({product})": floor,
                f"=QUOTIENT({product},1)": -cents if sign else cents,
                f"=TRUNC({product})": -cents if sign else cents,
                f"=EVEN({product})": (away + away % 2) * (-1 if sign else 1),
                f"=ODD({product})": (away + 1 - away % 2) * (-1 if sign else 1),
            }
            wrong.extend((formula, evaluate(formula), value) for formula, value in expected.items() if evaluate(formula) != value)
    assert wrong == []


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        # More than two binary64 values from the boundary: rounded exactly.
        ("=ROUNDUP(0.0700000000000001,2)", 0.08),
        ("=ROUNDDOWN(19.9899999999999,2)", 19.98),
        ("=TRUNC(19.9899999999999,2)", 19.98),
        ("=ROUND(1.00499999999999,2)", 1.0),
        ("=INT(434.999999999999)", 434),
        ("=ROUND(123456789012.3449,2)", 123456789012.34),
        # A whole number below 2^53 is stored exactly.
        ("=ROUND(1000000000000049,-2)", 1000000000000000.0),
        ("=ROUNDUP(1000000000000004,-2)", 1000000000000100.0),
        ("=ROUNDDOWN(1000000000000096,-2)", 1000000000000000.0),
        ("=ROUNDUP(3447688120018501,-2)", 3447688120018600.0),
        ("=ROUND(4910111281310649,-2)", 4910111281310600.0),
        ("=ROUND(4910111281310650,-2)", 4910111281310700.0),
    ],
)
def test_a_number_that_is_not_next_to_a_boundary_is_rounded_exactly(formula, expected):
    assert evaluate(formula) == expected


@pytest.mark.parametrize("places", range(5, 17))
def test_asking_for_more_places_than_a_number_has_gives_it_back(places):
    for written in ("19.99", "6333.47", "0.07", "764.878", "-4557.25787"):
        for name in ROUNDINGS:
            assert evaluate(f"={name}({written},{places})") == float(written), (name, written, places)


@pytest.mark.parametrize("name", sorted(ROUNDINGS))
def test_rounding_what_is_already_rounded_changes_nothing(name):
    for value, places in _written_numbers(7, 1500, most_digits=17):
        once = evaluate(f"={name}({value:f},{places})")
        if isinstance(once, float) and math.isfinite(once):
            assert evaluate(f"={name}({once!r},{places})") == once, (f"{value:f}", places)


def test_the_directed_functions_stay_in_order():
    for value, places in _written_numbers(11, 3000, most_digits=17):
        down, nearest, up = (evaluate(f"={name}({value:f},{places})") for name in ("ROUNDDOWN", "ROUND", "ROUNDUP"))
        assert abs(down) <= abs(nearest) <= abs(up), (f"{value:f}", places, down, nearest, up)
        assert evaluate(f"=TRUNC({value:f},{places})") == down


@pytest.mark.parametrize("formula", ["=TRUNC(-0.5)", "=ROUNDDOWN(-0.004,2)", "=ROUND(-0.001,2)", "=ROUND(-0,2)", "=INT(0.4)"])
def test_a_result_of_zero_has_no_sign(formula):
    result = evaluate(formula)
    assert result == 0 and math.copysign(1.0, result) == 1.0


def test_nothing_but_zero_is_taken_as_zero():
    assert evaluate("=ROUNDUP(5E-324,308)") == 1e-308
    assert evaluate("=ROUNDUP(1E-320,2)") == 0.01
    assert evaluate("=ROUNDDOWN(5E-324,308)") == 0


def test_a_caller_who_traps_float_conversion_does_not_change_a_result():
    with decimal.localcontext() as context:
        context.traps[decimal.FloatOperation] = True
        assert evaluate("=ROUND(4503599627370497,0)") == 4503599627370497
        assert evaluate("=ROUNDDOWN(1000000000000.07,2)") == 1000000000000.07
        assert evaluate("=TRUNC(19.99,13)") == 19.99


def test_depreciation_rounds_its_rate_as_round_does():
    # The rate is 1 - (0.5)^(1/1) = 0.5 exactly, and 1 - 0.25^(1/2) = 0.5.
    assert evaluate("=DB(1000,500,1,1)") == 500
    assert evaluate("=DB(1000,250,2,1)") == 500


# The two engines, bit for bit


def test_both_engines_give_the_same_bits_for_written_and_stored_numbers():
    if importlib.util.find_spec("workbook_forge._native") is None:
        pytest.skip("optional Rust extension is not installed")
    native = importlib.import_module("workbook_forge._native")
    cells, outputs = {}, {}
    numbers = list(_written_numbers(3, 1200, most_digits=17))
    for row, (value, places) in enumerate(numbers, start=1):
        cells[f"A{row}"] = {"value": float(value)}
        for column, template in zip("BCDEFGHI", (
            "=ROUND(A{0},{1})", "=ROUNDUP(A{0},{1})", "=ROUNDDOWN(A{0},{1})", "=TRUNC({2},{1})",
            "=INT(A{0})", "=QUOTIENT({2},1)", "=EVEN(A{0})", "=A{0}",
        )):
            cells[f"{column}{row}"] = {"formula": template.format(row, places, f"{value:f}")}
            outputs[f"{column}{row}"] = {"sheet": "Data", "address": f"{column}{row}"}
    document = json.dumps({"schema_version": 1, "sheets": [{"id": "0", "name": "Data", "cells": cells}], "outputs": outputs})
    ours = json.loads(python_engine.calculate(document))
    theirs = json.loads(native.calculate(document))
    assert ours["diagnostics"] == [] and theirs["diagnostics"] == []

    def bits(value):
        return struct.pack(">d", value).hex() if isinstance(value, (int, float)) and abs(value) < 1e308 else value

    differing = {
        name: (ours["outputs"][name], theirs["outputs"][name])
        for name in outputs
        if bits(ours["outputs"][name]) != bits(theirs["outputs"][name])
    }
    assert differing == {}
    # A number stored in a cell is read back as it was stored, in both.
    for row, (value, _) in enumerate(numbers, start=1):
        assert ours["outputs"][f"I{row}"] == float(value) == theirs["outputs"][f"I{row}"]


# Where the allowance ends


def _moved_by(value: float, steps: int) -> float:
    """The binary64 value `steps` values above `value`, or below when negative."""
    for _ in range(abs(steps)):
        value = math.nextafter(value, math.inf if steps > 0 else -math.inf)
    return value


@pytest.mark.parametrize("boundary", ["19.99", "0.07", "435", "1000000000000.07", "0.3"])
@pytest.mark.parametrize("steps", [-3, -2, -1, 0, 1, 2, 3])
def test_two_values_from_a_multiple_is_on_it_and_three_is_not(boundary, steps):
    places = len(boundary.partition(".")[2])
    on_it = float(boundary)
    stored = _moved_by(on_it, steps)
    unit = decimal.Decimal((0, (1,), -places))
    below = float(decimal.Decimal(boundary) - unit)
    above = float(decimal.Decimal(boundary) + unit)
    down, up = (evaluate(f"={name}({stored!r},{places})") for name in ("ROUNDDOWN", "ROUNDUP"))
    if abs(steps) <= 2:
        assert (down, up) == (on_it, on_it)
    elif steps < 0:
        assert (down, up) == (below, on_it)
    else:
        assert (down, up) == (on_it, above)


@pytest.mark.parametrize("half", ["1.005", "0.285", "2.675", "1234.5675"])
@pytest.mark.parametrize("steps", [-3, -2, -1, 0, 1, 2, 3])
def test_two_values_from_a_half_is_on_it_and_three_is_not(half, steps):
    places = len(half.partition(".")[2]) - 1
    unit = decimal.Decimal((0, (1,), -places))
    below = decimal.Decimal(half).quantize(unit, rounding=decimal.ROUND_DOWN)
    stored = _moved_by(float(half), steps)
    expected = float(below) if steps < -2 else float(below + unit)
    assert evaluate(f"=ROUND({stored!r},{places})") == expected
