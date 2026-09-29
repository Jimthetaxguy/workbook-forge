"""ROUND, ROUNDUP, ROUNDDOWN and TRUNC on numbers as they are written.

A two-decimal amount such as 19.99 has no exact binary form. Rounding the
stored value exactly made TRUNC(19.99,2) return 19.98 and moved 573 of the
9,999 amounts from 0.01 to 99.99 by a cent.

No expected value here comes from an evaluator. Each is built from whole
cents with integer arithmetic and written out as decimal text.
"""
from __future__ import annotations

import pytest

from workbook_forge import evaluate

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
