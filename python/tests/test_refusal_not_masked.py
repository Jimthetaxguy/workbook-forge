"""A formula the evaluator cannot calculate is refused, whatever surrounds it.

Workbook Forge's rule for an unsupported construct is a typed refusal, never a
silent approximation. A refusal is not an Excel error value: ``IFERROR`` must
not replace it, ``ISERROR`` must not answer TRUE for it, and another argument's
``#N/A`` or ``#VALUE!`` must not be returned in its place. The expectations
here follow from that rule, not from either engine's output.

The controls pin the other half of the rule: a branch that is never evaluated
does not refuse the formula.

One class is left open on purpose. Where an error value comes before the
unsupported call, as in ``=1/0+XLOOKUP(...)``, Excel's result is that error
whatever the call would have returned, so both the error and a refusal are
sound. The Rust engine stops at the error; the Python engine evaluates the
call and refuses. The last test pins only that neither returns anything else.
"""
from __future__ import annotations

import importlib
import importlib.util
import json

import pytest

from workbook_forge import ErrorValue, evaluate, evaluate_result

ENGINE_MODULES = {
    "python": "workbook_forge.python_engine",
    "rust": "workbook_forge._native",
}

# Two XLOOKUP modes both engines parse and neither calculates.
MATCH_MODE = "XLOOKUP(1,B1:B1,C1:C1,,1)"
SEARCH_MODE = "XLOOKUP(1,B1:B1,C1:C1,,0,2)"
XMATCH_MODE = "XMATCH(1,B1:B1,1)"

# Every wrapper evaluates the unsupported call. ``{x}`` is replaced by it.
MASKING_WRAPPERS = [
    "=IFERROR({x},5)",
    "=IFERROR(IFERROR({x},1/0),7)",
    "=IFNA({x},5)",
    "=ISERROR({x})",
    "=ISERR({x})",
    "=ISNA({x})",
    "=ISNUMBER({x})",
    "=ISTEXT({x})",
    "=ISBLANK({x})",
    "=ISLOGICAL({x})",
    "=COUNT({x})",
    "=COUNTA({x})",
    "=COUNTIF(D1:D2,{x})",
    "=SUMIF(D1:D2,{x})",
    "=ROUND(D1:D2,{x})",
    "=ROUND({x},D1:D2)",
    "=MOD(NA(),{x})",
    "=GCD(NA(),{x})",
    "=LEFT(D1:D2,{x})",
    "=POWER(D1:D2,{x})",
    "=EXACT(NA(),{x})",
    "=REPT(D1:D2,{x})",
    "=SUMPRODUCT(D1:D2,{x})",
    "={x}",
    "={x}+1/0",
    "=SUM({x},1/0)",
    "=IF({x},1,2)",
    "=IF(FALSE,1,{x})",
    "=IFERROR(1/0,{x})",
]

# The unsupported call is never evaluated.
CONTROLS = [
    ("=IF(TRUE,1,{x})", 1),
    ("=IFERROR(1,{x})", 1),
    ("=IFS(TRUE,1,TRUE,{x})", 1),
    ("=SWITCH(1,1,2,{x})", 2),
    ("=XLOOKUP(1,B1:B1,C1:C1,{x})", 2),
]

# An error value comes first and decides the result in Excel.
ERROR_FIRST = [
    ("=1/0+{x}", {"error": "#DIV/0!"}),
    ("=SUM(1/0,{x})", {"error": "#DIV/0!"}),
    ("=AND(NA(),{x})", {"error": "#N/A"}),
    ("=MAX(E1,{x})", {"error": "#REF!"}),
]


def _engine(name):
    module_name = ENGINE_MODULES[name]
    if name == "rust" and importlib.util.find_spec(module_name) is None:
        pytest.skip("optional Rust extension is not installed")
    return importlib.import_module(module_name)


@pytest.fixture(params=["python", "rust"])
def engine(request):
    return _engine(request.param)


def _report(engine, formula):
    document = {
        "schema_version": 1,
        "revision": 0,
        "inputs": {},
        "outputs": {"result": {"sheet": "Data", "address": "A9"}},
        "sheets": [
            {
                "id": "data",
                "name": "Data",
                "cells": {
                    "B1": {"value": 1},
                    "C1": {"value": 2},
                    "D1": {"value": 3},
                    "D2": {"value": 4},
                    "E1": {"value": {"error": "#REF!"}},
                    "A9": {"formula": formula},
                },
            }
        ],
    }
    return json.loads(engine.calculate(json.dumps(document)))


@pytest.mark.parametrize("unsupported", [MATCH_MODE, SEARCH_MODE, XMATCH_MODE])
@pytest.mark.parametrize("wrapper", MASKING_WRAPPERS)
def test_an_evaluated_unsupported_call_refuses_the_formula(engine, wrapper, unsupported):
    report = _report(engine, wrapper.format(x=unsupported))
    assert report["outputs"] == {}, report
    assert "Data!A9" not in report["values"], report
    assert [(item["code"], item["address"]) for item in report["diagnostics"]] == [
        ("unsupported_formula", "A9")
    ], report


@pytest.mark.parametrize("unsupported", [MATCH_MODE, SEARCH_MODE, XMATCH_MODE])
@pytest.mark.parametrize(("wrapper", "expected"), CONTROLS)
def test_an_unevaluated_unsupported_call_does_not_refuse(engine, wrapper, expected, unsupported):
    report = _report(engine, wrapper.format(x=unsupported))
    assert report["diagnostics"] == [], report
    assert report["outputs"] == {"result": expected}, report


@pytest.mark.parametrize(
    "formula",
    [
        "=IFERROR(NOSUCHFUNCTION(1),5)",
        "=ISERROR(NOSUCHFUNCTION(1))",
        "=COUNT(NOSUCHFUNCTION(1))",
        "=MOD(NA(),NOSUCHFUNCTION(1))",
        "=ROUND(A1:A2,NOSUCHFUNCTION(1))",
    ],
)
def test_standalone_evaluate_keeps_an_unknown_function_refusal(formula):
    for result in (evaluate(formula, {"A1": 1, "A2": 2}), evaluate_result(formula, {"A1": 1, "A2": 2})):
        assert result == ErrorValue("#NAME?", "unsupported function NOSUCHFUNCTION")


@pytest.mark.parametrize("wrapper", ["=IFERROR({x},5)", "=ISERROR({x})", "=MOD(NA(),{x})"])
def test_standalone_evaluate_keeps_an_unsupported_mode_refusal(wrapper):
    cells = {"B1": 1, "C1": 2}
    result = evaluate(wrapper.format(x=MATCH_MODE), cells)
    assert result == ErrorValue("#VALUE!", "XLOOKUP supports exact match_mode=0 only")
    result = evaluate(wrapper.format(x=SEARCH_MODE), cells)
    assert result == ErrorValue("#VALUE!", "XLOOKUP search_mode must be 1 or -1")


def test_standalone_evaluate_still_skips_an_unevaluated_unknown_function():
    assert evaluate("=IF(TRUE,1,NOSUCHFUNCTION(1))") == 1
    assert evaluate("=IFERROR(1,NOSUCHFUNCTION(1))") == 1


@pytest.mark.parametrize("unsupported", [MATCH_MODE, SEARCH_MODE, XMATCH_MODE])
@pytest.mark.parametrize(("wrapper", "error"), ERROR_FIRST)
def test_an_error_before_an_unsupported_call_is_that_error_or_a_refusal(
    engine, wrapper, error, unsupported
):
    report = _report(engine, wrapper.format(x=unsupported))
    if report["diagnostics"]:
        assert [item["code"] for item in report["diagnostics"]] == ["unsupported_formula"], report
        assert report["outputs"] == {}, report
    else:
        assert report["outputs"] == {"result": error}, report
