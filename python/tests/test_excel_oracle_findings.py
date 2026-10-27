"""Regressions for the Excel oracle findings of 2026-10-25, plus doc-derived cases.

Only the five findings named by the 2026-10-25 oracle run were observed in
desktop Excel: COUNT over typed logicals and numeric text, AND/OR ignoring
text in references, SEARCH wildcards, INDEX with a 0 row or column, and the
Rust XLOOKUP #N/A on a blank or text lookup range. That oracle data lives in
the private workbook-forge-evals repository and no receipt is cited here.

Every other case (reference provenance through INDEX, XLOOKUP and the IF
family, the extra SEARCH and INDEX cases, and the later parity cases) comes
from Microsoft's function documentation and independent reasoning. Under
docs/evidence-grades.md those are expected values, not Excel observations.
"""

import pytest

from workbook_forge import ArrayValue, ErrorValue, evaluate, evaluate_result

CELLS = {
    "A1": 1,
    "A2": True,
    "A3": "text",
    "B1": "2",
    "D1": 10,
    "D2": 20,
    "E1": 30,
    "E2": 40,
}


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        # Typed logical values and numeric text are counted.
        ("=COUNT(1,TRUE)", 2),
        ('=COUNT(1,"2","x","")', 2),
        # Inside a reference only numbers count.
        ("=COUNT(A1:A3)", 1),
        ("=COUNT(A2)", 0),
        ("=COUNT(B1)", 0),
        # INDEX over a range returns a reference, so its cell is not typed.
        ("=COUNT(INDEX(A1:A2,2,1))", 0),
        ("=COUNT(INDEX(B1:B1,1,1))", 0),
    ],
)
def test_count_counts_typed_logicals_but_not_referenced_ones(formula, expected):
    assert evaluate(formula, CELLS) == expected


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        ("=OR(A2:A3)", True),
        ("=AND(A1:A3)", True),
        ("=OR(FALSE,A3:A3)", False),
        ("=AND(TRUE,A3)", True),
        ("=OR(TRUE,INDEX(A1:A3,3,1))", True),
    ],
)
def test_and_or_ignore_text_in_references(formula, expected):
    assert evaluate(formula, CELLS) is expected


@pytest.mark.parametrize("formula", ["=OR(A3:A3)", "=AND(A3)", '=OR("text")'])
def test_and_or_without_a_logical_value_is_value_error(formula):
    result = evaluate(formula, CELLS)
    assert isinstance(result, ErrorValue)
    assert result.code == "#VALUE!"


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        # A successful XLOOKUP over a referenced return array is a reference.
        ("=COUNT(XLOOKUP(1,A1:A1,A2:A2))", 0),
        ("=COUNT(XLOOKUP(1,A1:A1,B1:B1))", 0),
        ("=COUNT(XLOOKUP(1,A1:A1,D1:D1))", 1),
        # A scalar if_not_found fallback is a direct value, so logicals and
        # numeric text count. A cell used as the fallback stays a reference.
        ("=COUNT(XLOOKUP(9,A1:A1,A2:A2,TRUE))", 1),
        ('=COUNT(XLOOKUP(9,A1:A1,B1:B1,"2"))', 1),
        ("=COUNT(XLOOKUP(9,A1:A1,D1:D1,A2))", 0),
        # The looked-up value itself is unchanged when nothing inspects provenance.
        ("=XLOOKUP(1,A1:A1,A2:A2)", True),
        ("=XLOOKUP(9,A1:A1,A2:A2,TRUE)", True),
        ("=XLOOKUP(1,A1:A1,D1:D1)+1", 11),
    ],
)
def test_count_over_xlookup_distinguishes_reference_hits_from_scalar_fallbacks(
    formula, expected
):
    assert evaluate(formula, CELLS) == expected


@pytest.mark.parametrize(
    "formula",
    [
        "=AND(XLOOKUP(1,A1:A1,A3:A3))",
        "=OR(XLOOKUP(1,A1:A1,A3:A3))",
    ],
)
def test_and_or_over_xlookup_text_reference_is_value_error(formula):
    result = evaluate(formula, CELLS)
    assert isinstance(result, ErrorValue)
    assert result.code == "#VALUE!"


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        ("=AND(TRUE,XLOOKUP(1,A1:A1,A3:A3))", True),
        ("=OR(TRUE,XLOOKUP(1,A1:A1,A3:A3))", True),
        ("=OR(FALSE,XLOOKUP(1,A1:A1,A3:A3))", False),
    ],
)
def test_and_or_ignore_text_from_an_xlookup_hit(formula, expected):
    assert evaluate(formula, CELLS) is expected


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        # IF, IFERROR, IFNA, IFS and SWITCH pass the chosen branch's reference
        # through. A scalar branch stays a direct value.
        ("=COUNT(IF(TRUE,A2))", 0),
        ("=COUNT(IF(FALSE,A2,TRUE))", 1),
        ("=COUNT(IF(TRUE,A1:A3))", 1),
        ("=COUNT(IF(TRUE,XLOOKUP(1,A1:A1,A2:A2)))", 0),
        ("=COUNT(IF(FALSE,XLOOKUP(1,A1:A1,A2:A2),TRUE))", 1),
        ("=COUNT(IFERROR(A2,0))", 0),
        ("=COUNT(IFERROR(1/0,TRUE))", 1),
        ("=COUNT(IFERROR(XLOOKUP(1,A1:A1,B1:B1),0))", 0),
        ("=COUNT(IFNA(A2,0))", 0),
        ("=COUNT(IFNA(NA(),TRUE))", 1),
        ("=COUNT(IFS(TRUE,A2))", 0),
        ("=COUNT(IFS(FALSE,A2,TRUE,TRUE))", 1),
        ("=COUNT(SWITCH(1,1,A2))", 0),
        ("=COUNT(SWITCH(1,2,A2,TRUE))", 1),
        ("=AND(TRUE,IF(TRUE,A3))", True),
        ("=OR(IF(TRUE,A3))", None),
    ],
)
def test_selection_functions_keep_reference_provenance(formula, expected):
    result = evaluate(formula, CELLS)
    if expected is None:
        assert isinstance(result, ErrorValue)
        assert result.code == "#VALUE!"
    else:
        assert result == expected


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        ('=SEARCH("*","a*b")', 1),
        ('=SEARCH("b?d","abcd")', 2),
        ('=SEARCH("C*E","abcde")', 3),
        ('=SEARCH("~*","a*b")', 2),
        ('=SEARCH("~?","a?b")', 2),
        ('=SEARCH("?","abc",2)', 2),
        ('=SEARCH("a*","xxabc")', 3),
    ],
)
def test_search_supports_wildcards(formula, expected):
    assert evaluate(formula, CELLS) == expected


def test_search_wildcard_not_found_is_value_error():
    result = evaluate('=SEARCH("x*","abc")', CELLS)
    assert isinstance(result, ErrorValue)
    assert result.code == "#VALUE!"


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        ("=SUM(INDEX(D1:E2,0,2))", 70),
        ("=SUM(INDEX(D1:E2,2,0))", 60),
        ("=SUM(INDEX(D1:E2,0,0))", 100),
        ("=INDEX(D1:E2,2,2)", 40),
    ],
)
def test_index_zero_returns_whole_column_or_row(formula, expected):
    assert evaluate(formula, CELLS) == expected


def test_index_zero_row_keeps_column_shape():
    result = evaluate_result("=INDEX(D1:E2,0,2)", CELLS)
    value = getattr(result, "value", result)
    assert isinstance(value, ArrayValue)
    assert value.rows == ((30,), (40,))


def test_xlookup_on_blank_lookup_range_is_na():
    result = evaluate("=XLOOKUP(1,F1:F3,G1:G3)", CELLS)
    assert isinstance(result, ErrorValue)
    assert result.code == "#N/A"


def test_xlookup_on_empty_text_lookup_range_is_na():
    cells = {**CELLS, "F1": "", "F2": "x"}
    result = evaluate("=XLOOKUP(1,F1:F2,D1:D2)", cells)
    assert isinstance(result, ErrorValue)
    assert result.code == "#N/A"


# Parity cases found in review (doc-derived, not Excel observations).


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        # Every AND/OR argument is evaluated, so a later typed text or error
        # still decides the result after an earlier deciding value.
        ('=OR(A1:A3,"x")', "#VALUE!"),
        ("=OR(TRUE,1/0)", "#DIV/0!"),
        ("=AND(FALSE,1/0)", "#DIV/0!"),
    ],
)
def test_and_or_evaluate_every_argument(formula, expected):
    result = evaluate(formula, CELLS)
    assert isinstance(result, ErrorValue)
    assert result.code == expected


def test_count_skips_an_error_argument():
    assert evaluate("=COUNT(1,1/0)", CELLS) == 1


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        ("=INDEX(D1:E2,1.9,1)", 10),
        ("=SUM(INDEX(D1:E2,0.5,1))", 30),
    ],
)
def test_index_truncates_fractional_positions(formula, expected):
    assert evaluate(formula, CELLS) == expected


def test_lookups_do_not_equate_true_with_one():
    assert evaluate("=XLOOKUP(TRUE,A1:A2,D1:D2)", CELLS) == 20
    result = evaluate("=MATCH(1,A2:A3,0)", CELLS)
    assert isinstance(result, ErrorValue)
    assert result.code == "#N/A"


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        # Specialized argument readers see the plain value of a selected
        # reference, so a text weekend mask stays a mask.
        ("=WORKDAY.INTL(45002,1,IF(TRUE,C1))", 45005),
        ("=WORKDAY.INTL(45002,1,XLOOKUP(1,A1:A1,C1:C1))", 45005),
        ("=SUM(UNIQUE(D1:D2,IF(TRUE,A2)))", 30),
    ],
)
def test_specialized_readers_unwrap_reference_provenance(formula, expected):
    cells = {**CELLS, "C1": "0000011"}
    assert evaluate(formula, cells) == expected
