"""What the separate copies of two small routines do today.

Cell addresses are parsed in five places and spreadsheet error codes are
listed in five. The copies do not agree. These tests record each copy's
present behaviour, disagreements included, so that replacing them with one
routine is a visible decision about which behaviour wins and not a silent
change.

They test private functions on purpose. When a copy is removed or merged,
delete its rows here in the same change.
"""
from __future__ import annotations

import pytest

import workbook_forge as evaluator
from workbook_forge import expressions, model, primitives, python_engine, workbook

LAST_COLUMN = 16_384
LAST_ROW = 1_048_576


def _expression_reference(address: str) -> tuple[int, int]:
    reference = expressions.CellReference.parse(address)
    return reference.row, reference.column


def _workbook_position(address: str) -> tuple[int, int]:
    column, row = workbook._cell_position(address)
    return row, column


def _model_address(address: str) -> tuple[int, int]:
    if model._canonical_address(address) is None:
        raise ValueError(address)
    return model._parts(address)


# Each parser, adapted to return (row, column) and to raise ValueError.
PARSERS = {
    "evaluator": evaluator._coordinate,
    "workbook": _workbook_position,
    "engine": python_engine._coordinates,
    "expressions": _expression_reference,
    "model": _model_address,
}

# Every copy agrees on these.
ACCEPTED_BY_ALL = {
    "A1": (1, 1),
    "XFD1048576": (LAST_ROW, LAST_COLUMN),
}
REFUSED_BY_ALL = ["A0", "A01", "AAAA1", "1A", "A", "", "Sheet1!A1"]

# The copies disagree on these. True means the copy accepts the address.
DISAGREEMENTS = {
    # Past the last column or row. Only the formula evaluator accepts them.
    "XFE1": {"evaluator": True, "workbook": False, "engine": False, "expressions": False, "model": False},
    "A1048577": {"evaluator": True, "workbook": False, "engine": False, "expressions": False, "model": False},
    "ZZZ1": {"evaluator": True, "workbook": False, "engine": False, "expressions": False, "model": False},
    "A10000000": {"evaluator": True, "workbook": False, "engine": False, "expressions": False, "model": False},
    # Lower case and absolute markers. Only the canonical model refuses them.
    "a1": {"evaluator": True, "workbook": True, "engine": True, "expressions": True, "model": False},
    "$A$1": {"evaluator": True, "workbook": True, "engine": True, "expressions": True, "model": False},
}


def _accepts(parser, address: str) -> bool:
    try:
        parser(address)
    except ValueError:
        return False
    return True


@pytest.mark.parametrize("name", sorted(PARSERS))
@pytest.mark.parametrize("address", sorted(ACCEPTED_BY_ALL))
def test_every_copy_reads_an_ordinary_address(name, address):
    assert tuple(PARSERS[name](address)) == ACCEPTED_BY_ALL[address]


@pytest.mark.parametrize("name", sorted(PARSERS))
@pytest.mark.parametrize("address", REFUSED_BY_ALL)
def test_every_copy_refuses_a_malformed_address(name, address):
    assert not _accepts(PARSERS[name], address)


@pytest.mark.parametrize("name", sorted(PARSERS))
@pytest.mark.parametrize("address", sorted(DISAGREEMENTS))
def test_where_the_copies_disagree(name, address):
    assert _accepts(PARSERS[name], address) is DISAGREEMENTS[address][name]


def test_the_engine_reports_a_code_and_the_others_do_not():
    with pytest.raises(python_engine.ToolkitError) as refused:
        python_engine._coordinates("XFE1")
    assert refused.value.code == "invalid_reference"


# Error codes

STANDARD = {"#CALC!", "#DIV/0!", "#N/A", "#NAME?", "#NUM!", "#REF!", "#VALUE!"}
ERROR_CODES = {
    "workbook": (workbook.EXCEL_ERROR_CODES, STANDARD | {"#NULL!", "#SPILL!", "#N/A!"}),
    "engine": (python_engine._ERRORS, STANDARD | {"#NULL!", "#SPILL!", "#N/A!"}),
    "expressions": (expressions._ERROR_CODES, STANDARD | {"#NULL!", "#SPILL!"}),
    "model": (model._ERROR_CODES, STANDARD | {"#NULL!", "#SPILL!"}),
    "primitives": (primitives._ERRORS, STANDARD),
}


@pytest.mark.parametrize("name", sorted(ERROR_CODES))
def test_each_list_of_error_codes(name):
    actual, expected = ERROR_CODES[name]
    assert set(actual) == expected


def test_the_lists_differ_in_three_codes():
    every = [set(actual) for actual, _ in ERROR_CODES.values()]
    assert set.union(*every) - set.intersection(*every) == {"#NULL!", "#SPILL!", "#N/A!"}
