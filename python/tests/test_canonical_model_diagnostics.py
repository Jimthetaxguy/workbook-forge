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
