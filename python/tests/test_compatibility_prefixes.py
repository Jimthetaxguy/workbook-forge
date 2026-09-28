from __future__ import annotations

import pytest

from workbook_forge import ArrayValue, ErrorValue, evaluate_result
from workbook_forge.expressions import analyze_formula, copy_formula


@pytest.mark.parametrize("prefix", ["_xlfn.", "_xlws.", "_xlfn._xlws.", "_XlFn._XlWs."])
def test_known_ooxml_function_prefixes_are_evaluated_and_mapped(prefix):
    formula = f"={prefix}XLOOKUP(2,A1:A2,B1:B2)"
    assert evaluate_result(formula, {"A1": 1, "A2": 2, "B1": 10, "B2": 20}) == 20
    analysis = analyze_formula(formula)
    assert analysis["formula"] == formula
    assert analysis["functions"] == ["XLOOKUP"]
    assert prefix.upper() in analysis["rendered"]
    assert prefix.upper() in copy_formula(formula, row_delta=1)


def test_prefixed_dynamic_array_retains_array_shape():
    result = evaluate_result("=_xlfn._xlws.FILTER(A1:A2,A1:A2>1)", {"A1": 1, "A2": 2})
    assert result == ArrayValue(((2,),))


@pytest.mark.parametrize("function", ["_custom.SUM", "_xlws._xlfn.SUM", "_xlfn._xlfn.SUM", "_xlfn.UNKNOWN"])
def test_unknown_and_repeated_prefixes_remain_unsupported(function):
    result = evaluate_result(f"={function}(1,2)")
    assert isinstance(result, ErrorValue) and result.code == "#NAME?"
    assert analyze_formula(f"={function}(1,2)")["functions"] == [function.upper()]
