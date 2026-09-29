"""A diagnostic must name the cell whose formula caused it."""
from __future__ import annotations

import json

import pytest

from workbook_forge.model import calculate, hydrate


def _cell(address, value=None, formula=None):
    data_type = "blank" if value is None else "number"
    cell = {"address": address, "value": value, "data_type": data_type}
    if formula is not None:
        cell["formula"] = {"expression": formula, "dependencies": [], "result": None}
    return cell


def _workbook(*sheets):
    return json.dumps({
        "schema_version": 1,
        "model_version": 1,
        "metadata": {},
        "sheets": [
            {"name": name, "cells": {cell["address"]: cell for cell in cells}}
            for name, cells in sheets
        ],
    })


@pytest.mark.parametrize(
    ("formula", "code"),
    [
        ("=A1:A2", "unsupported_formula"),
        ("=NOSUCHFUNCTION(1)", "unsupported_formula"),
        ("=1+", "parse_error"),
    ],
    ids=["array-result", "unsupported-function", "unparsable"],
)
def test_diagnostic_names_the_formula_cell_when_other_cells_follow_it(formula, code):
    """The formula is in S!C1. Cells after it, on this sheet and the next, must not take the blame."""
    document = _workbook(
        ("S", [_cell("A1", 1), _cell("A2", 2), _cell("C1", formula=formula), _cell("D9", 3)]),
        ("T", [_cell("Q7", 4)]),
    )
    result = calculate(hydrate(document))
    assert [(item.code, item.sheet, item.address) for item in result.diagnostics] == [(code, "S", "C1")]


@pytest.mark.parametrize(
    "formula",
    [
        "=XFE1",
        "=A1048577",
        "=XFE1+A1048578",
        "=SUM(XFD1:XFE1)",
        "=SUM(A1048576:A1048577)",
        # The cell past the sheet is the start of the range, not the end.
        "=SUM(XFE2:A2)",
        "=SUM(A1048577:A1)",
    ],
)
def test_a_reference_past_the_last_cell_is_an_invalid_reference(formula):
    """XFD1048576 is the last cell of a worksheet. Nothing beyond it can be a dependency."""
    document = _workbook(("S", [_cell("A1", 1), _cell("C1", formula=formula)]))
    result = calculate(hydrate(document))
    formula_cell = result.sheets[0].cells["C1"].formula
    assert [(item.code, item.sheet, item.address) for item in result.diagnostics] == [
        ("invalid_reference", "S", "C1")
    ]
    assert formula_cell.dependencies == ()
    assert formula_cell.result is None


@pytest.mark.parametrize(
    ("formula", "dependencies"),
    [
        ("=XFD1", ("S!XFD1",)),
        ("=A1048576", ("S!A1048576",)),
        ("=XFD1048576", ("S!XFD1048576",)),
        ("=SUM(XFC1:XFD1)", ("S!XFC1", "S!XFD1")),
        ("=SUM(XFD1:XFC1)", ("S!XFC1", "S!XFD1")),
    ],
)
def test_a_reference_to_the_last_cell_is_a_dependency(formula, dependencies):
    document = _workbook(("S", [_cell("A1", 1), _cell("C1", formula=formula)]))
    result = calculate(hydrate(document))
    assert result.diagnostics == ()
    assert result.sheets[0].cells["C1"].formula.dependencies == dependencies
