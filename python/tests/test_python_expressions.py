"""The typed Python expression layer works without importing the Rust module."""

from pathlib import Path
import subprocess
import sys

import pytest

from workbook_forge import ArrayValue, ErrorValue, evaluate, evaluate_result
from workbook_forge.expressions import (
    CellReference,
    Expression,
    analyze_formula,
    copy_formula,
    parse_expression,
)


def test_primitives_work_with_native_imports_blocked():
    package_root = Path(__file__).resolve().parents[1]
    source = '''
import importlib.abc
import sys
sys.path.insert(0, sys.argv[1])
class RejectNative(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "workbook_forge._native":
            raise AssertionError("Python primitives attempted to import Rust")
sys.meta_path.insert(0, RejectNative())
from workbook_forge.expressions import CellReference, Expression, analyze_formula, copy_formula
from workbook_forge import evaluate
expression = Expression.reference(CellReference(1, 1)) + Expression.literal(2)
assert evaluate(expression.formula, {"A1": 3}) == 5
assert copy_formula("=$A1+B$2", 1, 1) == "=($A2+C$2)"
assert analyze_formula("=SUM(A1:A3)")["functions"] == ["SUM"]
assert "workbook_forge._native" not in sys.modules
'''
    subprocess.run([sys.executable, "-I", "-c", source, str(package_root)], check=True)


def test_mixed_anchors_quoted_sheets_and_typed_analysis():
    formula = "=SUM(A1,$B2,C$3,$D$4,'Rates O''Brien'!E5:F$6)"
    copied = copy_formula(formula, row_delta=2, column_delta=3)
    assert copied == "=SUM(D3,$B4,F$3,$D$4,'Rates O''Brien'!H7:I$6)"
    analysis = analyze_formula(formula)
    assert analysis["formula"] == formula
    assert analysis["functions"] == ["SUM"]
    references = analysis["references"]
    assert references[1]["start"] == {
        "sheet": None, "row": 2, "column": 2,
        "absolute_row": False, "absolute_column": True,
    }
    assert references[-1]["end"]["absolute_row"]
    assert references[-1]["start"]["sheet"] == "Rates O'Brien"


def test_typed_construction_preserves_omissions_text_errors_and_arrays():
    omitted = Expression.call("IF", Expression.literal(True), None, Expression.literal(9))
    explicit = Expression.call("IF", Expression.literal(True), Expression.literal(""), Expression.literal(9))
    assert omitted.formula == "=IF(TRUE,,9)"
    assert explicit.formula == '=IF(TRUE,"",9)'
    assert Expression.call("IF", Expression.literal(True), Expression.missing(), Expression.literal(9)) == omitted
    assert Expression.literal('say "hello"').source == '"say ""hello"""'
    assert Expression.literal(ErrorValue("#DIV/0!")).formula == "=#DIV/0!"
    array = Expression.call("SORT", Expression.range(CellReference(1, 1), CellReference(3, 1)))
    assert evaluate_result(array.formula, {"A1": 3, "A2": 1, "A3": 2}) == ArrayValue(((1,), (2,), (3,)))


def test_rendering_preserves_numeric_tokens_and_excel_operator_meaning():
    assert copy_formula("=1e400+9007199254740993+01.2300") == "=((1e400+9007199254740993)+01.2300)"
    for formula in ("=-2^2", "=2^3^2", "=-25%+2*3", "=1-(2-3)"):
        assert evaluate(copy_formula(formula)) == evaluate(formula)
    with pytest.raises(ValueError):
        Expression.literal(float("inf"))
    with pytest.raises(ValueError):
        Expression.literal(10 ** 1000)


@pytest.mark.parametrize("formula,rows,columns", [
    ("=A1", -1, 0), ("=A1", 0, -1),
    ("=XFD1048576", 1, 0), ("=XFD1048576", 0, 1),
    ("=XFE1", 0, 0), ("=A1048577", 0, 0),
])
def test_copy_and_parse_reject_out_of_grid_references(formula, rows, columns):
    with pytest.raises(ValueError):
        copy_formula(formula, rows, columns)


def test_anchored_boundaries_stay_fixed_and_invalid_offsets_refuse():
    assert copy_formula("=$A$1+$XFD$1048576", -1000, 1000) == "=($A$1+$XFD$1048576)"
    with pytest.raises(TypeError):
        copy_formula("=A1", True, 0)
    with pytest.raises(TypeError):
        CellReference(1, 1, row_absolute="yes")


def test_construction_and_source_budgets_are_enforced():
    expression = Expression.literal(1)
    for _ in range(95):
        expression = expression + Expression.literal(1)
    assert expression.render().startswith("=")
    with pytest.raises(ValueError, match="depth limit"):
        expression + Expression.literal(1)
    # The root is level 1, so a 96-term chain is the deepest flat chain
    # accepted; Rust draws the line at the same term.
    assert parse_expression("=" + "+".join(["1"] * 96)) is not None
    with pytest.raises(ValueError, match="depth limit"):
        parse_expression("=" + "+".join(["1"] * 97))
    with pytest.raises(ValueError, match="8192"):
        Expression.literal("a" * 8191)
    with pytest.raises(ValueError, match="8192"):
        parse_expression("=" + "1" * 8193)
    call = Expression.literal(1)
    for _ in range(64):
        call = Expression.call("SUM", call)
    with pytest.raises(ValueError, match="64-level"):
        Expression.call("SUM", call)


def test_reference_ranges_and_function_analysis_have_explicit_sheet_binding():
    reference = CellReference.parse("$bc$19", "Input Data")
    assert reference == CellReference(19, 55, "Input Data", True, True)
    assert Expression.reference(reference).formula == "='Input Data'!$BC$19"
    expression = Expression.call(
        "sum", Expression.reference(reference),
        Expression.call("SUM", Expression.literal(3)),
    )
    assert expression.inspect()["functions"] == ["SUM"]
    with pytest.raises(ValueError, match="same sheet"):
        Expression.range(CellReference(1, 1, "First"), CellReference(2, 1, "Second"))


def test_cell_shaped_sheet_and_function_names_use_their_syntactic_context():
    assert copy_formula("=S1!A1", 1, 1) == "='S1'!B2"
    assert analyze_formula("=LOG10(100)")["functions"] == ["LOG10"]
    assert evaluate(copy_formula("=S1!A1"), {"S1!A1": 3}) == 3
