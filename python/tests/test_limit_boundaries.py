"""Each documented limit, tested at the limit and one step past it.

Deliberately changing any of these comparisons by one used to leave every test
passing. The last worksheet cell is XFD1048576: column 16384, row 1048576.
"""
from __future__ import annotations

import copy
import importlib
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest

from workbook_forge import python_engine, workbook
from workbook_forge.model import MAX_RANGE_CELLS, ModelError, calculate, hydrate
from workbook_forge.workbook import UnsupportedWorkbook, Workbook

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "fixtures" / "operating-scenario.workbook.json").read_text(encoding="utf-8"))
LAST_ROW = 1_048_576
LAST_COLUMN = 16_384
UINT64_MAX = (1 << 64) - 1

INSIDE = ["A1", "XFD1", "A1048576", "XFD1048576"]
OUTSIDE = ["XFE1", "A1048577", "XFE1048577"]


def _document(change) -> str:
    document = copy.deepcopy(FIXTURE)
    change(document)
    return json.dumps(document)


def _cell(sheet: dict, address: str) -> dict:
    cells = sheet["cells"]
    if isinstance(cells, dict):
        return cells[address]
    return next(cell for cell in cells if cell["address"] == address)


# Canonical model


@pytest.mark.parametrize("address", INSIDE)
def test_model_accepts_a_binding_inside_the_sheet(address):
    document = _document(lambda data: data["bindings"]["outputs"].update(edge={"sheet": "Forecast", "address": address}))
    assert hydrate(document).bindings.outputs["edge"].address == address


@pytest.mark.parametrize("address", OUTSIDE)
def test_model_refuses_a_binding_outside_the_sheet(address):
    document = _document(lambda data: data["bindings"]["outputs"].update(edge={"sheet": "Forecast", "address": address}))
    with pytest.raises(ModelError, match="not a canonical A1 reference"):
        hydrate(document)


@pytest.mark.parametrize(
    "dimensions",
    [[1, LAST_ROW, 1, 3], [1, 3, 1, LAST_COLUMN], [1, LAST_ROW, 1, LAST_COLUMN], [1, 1, 1, 1]],
)
def test_model_accepts_dimensions_up_to_the_last_cell(dimensions):
    document = _document(lambda data: data["sheets"][0].update(dimensions=dimensions))
    assert hydrate(document).sheets[0].dimensions == tuple(dimensions)


@pytest.mark.parametrize(
    "dimensions",
    [
        [1, LAST_ROW + 1, 1, 3],
        [1, 3, 1, LAST_COLUMN + 1],
        [3, 1, 1, 3],
        [1, 3, 3, 1],
    ],
    ids=["row-past-last", "column-past-last", "rows-reversed", "columns-reversed"],
)
def test_model_refuses_dimensions_outside_the_sheet_or_reversed(dimensions):
    document = _document(lambda data: data["sheets"][0].update(dimensions=dimensions))
    with pytest.raises(ModelError, match="dimensions"):
        hydrate(document)


def _range_diagnostics(cell_count: int) -> list[tuple[str, str]]:
    def change(data):
        _cell(data["sheets"][1], "F2")["formula"]["expression"] = f"=SUM(Assumptions!A1:A{cell_count})"

    result = calculate(hydrate(_document(change)))
    return [(item.code, item.address) for item in result.diagnostics if item.address == "F2"]


def test_model_calculates_a_range_of_exactly_the_limit():
    assert _range_diagnostics(MAX_RANGE_CELLS) == []


def test_model_refuses_a_range_one_cell_over_the_limit():
    assert _range_diagnostics(MAX_RANGE_CELLS + 1) == [("resource_limit", "F2")]


# Workbook package reader


def _package(path: Path, extra: dict[str, bytes] | None = None) -> int:
    """Write a small workbook. Return the total size of its parts."""
    parts = {
        "[Content_Types].xml": b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="bin" ContentType="application/octet-stream"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": b'<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": b'<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><v>1</v></c></row></sheetData></worksheet>',
    }
    parts.update(extra or {})
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return sum(len(data) for data in parts.values())


@pytest.mark.parametrize("address", INSIDE)
def test_workbook_accepts_an_edit_inside_the_sheet(tmp_path, address):
    _package(tmp_path / "book.xlsx")
    with Workbook.open(tmp_path / "book.xlsx") as book:
        book.set_value("Sheet1", address, 5)
        assert book.get("Sheet1", address).value == 5


@pytest.mark.parametrize("address", OUTSIDE)
def test_workbook_refuses_an_edit_outside_the_sheet(tmp_path, address):
    _package(tmp_path / "book.xlsx")
    with Workbook.open(tmp_path / "book.xlsx") as book:
        with pytest.raises(ValueError, match="outside Excel worksheet bounds"):
            book.set_value("Sheet1", address, 5)


def test_package_limit_counts_all_parts_together(tmp_path, monkeypatch):
    """Two parts that each fit must still be refused when their total does not."""
    filler = b"x" * 4096
    total = _package(tmp_path / "book.xlsx", {"custom/a.bin": filler, "custom/b.bin": filler})

    monkeypatch.setattr(workbook, "MAX_PACKAGE_BYTES", total)
    with Workbook.open(tmp_path / "book.xlsx") as book:
        assert book.sheet_names == ("Sheet1",)

    monkeypatch.setattr(workbook, "MAX_PACKAGE_BYTES", total - 1)
    assert total - 1 > len(filler)
    with pytest.raises(UnsupportedWorkbook, match="size limit"):
        Workbook.open(tmp_path / "book.xlsx")


# Calculation engines


ENGINES = {"python": "workbook_forge.python_engine", "rust": "workbook_forge._native"}


@pytest.fixture(params=sorted(ENGINES))
def engine(request):
    name = ENGINES[request.param]
    if request.param == "rust" and importlib.util.find_spec(name) is None:
        pytest.skip("optional Rust extension is not installed")
    return importlib.import_module(name)


def _model(revision: int = 0, cells: dict | None = None) -> str:
    return json.dumps({
        "schema_version": 1,
        "revision": revision,
        "sheets": [{"id": "data", "name": "Data", "cells": cells or {"A1": {"value": 1}}}],
        "inputs": {},
        "outputs": {},
    })


EDIT = json.dumps([{"sheet": "Data", "address": "A1", "value": 2}])


def test_revision_counter_reaches_its_last_value(engine):
    session = engine.Session(_model(UINT64_MAX - 1))
    assert session.apply(EDIT, UINT64_MAX - 1) == UINT64_MAX


def test_revision_counter_stops_at_its_last_value(engine):
    session = engine.Session(_model(UINT64_MAX))
    with pytest.raises(ValueError, match="resource_limit"):
        session.apply(EDIT, UINT64_MAX)
    assert json.loads(session.snapshot())["revision"] == UINT64_MAX


@pytest.mark.parametrize("address", INSIDE)
def test_engine_accepts_a_cell_inside_the_sheet(engine, address):
    session = engine.Session(_model(cells={address: {"value": 1}}))
    assert address in json.loads(session.snapshot())["sheets"][0]["cells"]


@pytest.mark.parametrize("address", OUTSIDE)
def test_engine_refuses_a_cell_outside_the_sheet(engine, address):
    with pytest.raises(ValueError):
        engine.Session(_model(cells={address: {"value": 1}}))


def test_python_engine_is_the_module_under_test():
    # The engine fixture must not silently fall back to one implementation.
    assert importlib.import_module(ENGINES["python"]) is python_engine
