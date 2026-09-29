"""Reading an Excel workbook into the canonical model.

The workbooks are written here as XML, so every expected value below is one
this file put into the workbook, not one the reader produced.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import zipfile

import jsonschema
import pytest

from workbook_forge.intake import intake_workbook, intake_workbook_model, summarize
from workbook_forge.model import calculate, hydrate
from workbook_forge.workbook import WorkbookError

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "schemas" / "workbook-model.v1.schema.json").read_text(encoding="utf-8"))
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RELATIONSHIPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

INPUTS = (
    '<row r="1"><c r="A1" t="inlineStr"><is><t>Units</t></is></c><c r="B1"><v>40</v></c></row>'
    '<row r="2"><c r="A2" t="inlineStr"><is><t>Price</t></is></c><c r="B2"><v>2.5</v></c></row>'
)
# The stored value 999 is deliberately not 40 * 2.5. It is what Excel is
# pretending to have calculated, and it must be kept apart from a result.
TOTALS = (
    '<row r="1"><c r="A1"><f>Inputs!B1*Inputs!B2</f><v>999</v></c>'
    '<c r="B1"><f>A1+1</f></c></row>'
)


def _write(
    path: Path,
    sheets: dict[str, str],
    *,
    after_cells: str = "",
    in_workbook: str = "",
    states: dict[str, str] | None = None,
    styles: str | None = None,
) -> Path:
    names = list(sheets)
    states = states or {}
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, len(names) + 1)
    )
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + (
                '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                if styles is not None
                else ""
            )
            + f"{overrides}</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{RELATIONSHIPS}/officeDocument" Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            f'<?xml version="1.0"?><workbook xmlns="{MAIN}" xmlns:r="{RELATIONSHIPS}"><sheets>'
            + "".join(
                f'<sheet name="{name}" sheetId="{index}"'
                + (f' state="{states[name]}"' if name in states else "")
                + f' r:id="rId{index}"/>'
                for index, name in enumerate(names, start=1)
            )
            + f"</sheets>{in_workbook}</workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(
                f'<Relationship Id="rId{index}" Type="{RELATIONSHIPS}/worksheet" Target="worksheets/sheet{index}.xml"/>'
                for index in range(1, len(names) + 1)
            )
            + (f'<Relationship Id="rIdStyles" Type="{RELATIONSHIPS}/styles" Target="styles.xml"/>' if styles is not None else "")
            + "</Relationships>"
        ),
    }
    if styles is not None:
        parts["xl/styles.xml"] = styles
    for index, name in enumerate(names, start=1):
        parts[f"xl/worksheets/sheet{index}.xml"] = (
            f'<?xml version="1.0"?><worksheet xmlns="{MAIN}"><sheetData>{sheets[name]}</sheetData>{after_cells}</worksheet>'
        )
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in parts.items():
            archive.writestr(name, text)
    return path


@pytest.fixture
def book(tmp_path):
    return _write(tmp_path / "plan.xlsx", {"Inputs": INPUTS, "Totals": TOTALS})


def test_literals_are_read_as_they_were_written(book):
    inputs = intake_workbook_model(book).sheet("Inputs")
    assert [(a, c.value, c.data_type) for a, c in sorted(inputs.cells.items())] == [
        ("A1", "Units", "text"),
        ("A2", "Price", "text"),
        ("B1", 40, "number"),
        ("B2", 2.5, "number"),
    ]
    assert all(cell.formula is None for cell in inputs.cells.values())


def test_a_stored_formula_value_is_kept_apart_from_a_result(book):
    total = intake_workbook_model(book).sheet("Totals").cells["A1"]
    assert total.formula.expression == "=Inputs!B1*Inputs!B2"
    assert total.value == 999
    assert total.provenance.origin == "imported"
    assert total.formula.result is None
    assert total.formula.dependencies == ()


def test_a_formula_with_no_stored_value_has_none(book):
    follower = intake_workbook_model(book).sheet("Totals").cells["B1"]
    assert follower.formula.expression == "=A1+1"
    assert (follower.value, follower.data_type) == (None, "blank")


def test_calculation_replaces_nothing_it_was_given(book):
    calculated = calculate(intake_workbook_model(book))
    total = calculated.sheet("Totals").cells["A1"]
    follower = calculated.sheet("Totals").cells["B1"]
    assert calculated.diagnostics == ()
    assert total.formula.result == 100  # 40 * 2.5
    assert total.formula.dependencies == ("Inputs!B1", "Inputs!B2")
    assert total.value == 999  # what the workbook stored is still there
    assert follower.formula.result == 101  # from the calculated 100, not the stored 999


def test_the_document_matches_the_schema_and_reads_back_the_same(book):
    document = intake_workbook(book)
    jsonschema.validate(json.loads(document), SCHEMA)
    assert hydrate(document).to_json().encode("utf-8") == document
    assert json.loads(document)["schema_version"] == 1
    assert json.loads(document)["model_version"] == 1


def test_the_summary_counts_what_the_workbook_holds(book):
    summary = summarize(intake_workbook_model(book))
    assert summary["schema_version"] == 1 and summary["model_version"] == 1
    assert (summary["cell_count"], summary["formula_count"]) == (6, 2)
    assert [(s["name"], s["cells"], s["formulas"], s["dimensions"]) for s in summary["sheets"]] == [
        ("Inputs", 4, 0, [1, 2, 1, 2]),
        ("Totals", 2, 2, [1, 1, 1, 2]),
    ]


@pytest.mark.parametrize("name", ["plan.xlsm", "plan.csv", "plan"])
def test_other_kinds_of_file_are_refused(tmp_path, name):
    (tmp_path / name).write_bytes(b"not a workbook")
    with pytest.raises(WorkbookError, match="reads .xlsx files"):
        intake_workbook_model(tmp_path / name)


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(WorkbookError, match="not found"):
        intake_workbook_model(tmp_path / "absent.xlsx")


def _cli(*arguments):
    return subprocess.run(
        [sys.executable, "-m", "workbook_forge.cli", "intake", *map(str, arguments)],
        cwd=ROOT, env={"PYTHONPATH": str(ROOT / "python")}, capture_output=True, text=True, check=False,
    )


def _expected_document(source: str) -> dict:
    """The document for `book`, written out from what `book` was given."""
    origin = {"origin": "imported", "source": source}

    def cell(address, data_type, value, expression=None):
        written = {"address": address, "data_type": data_type, "provenance": origin, "value": value}
        if expression is not None:
            written["formula"] = {"dependencies": [], "expression": expression, "result": None}
        return written

    return {
        "schema_version": 1,
        "model_version": 1,
        "source_path": source,
        "provenance": origin,
        "metadata": {"intake": {"not_carried": []}},
        "sheets": [
            {
                "name": "Inputs",
                "dimensions": [1, 2, 1, 2],
                "cells": {
                    "A1": cell("A1", "text", "Units"),
                    "A2": cell("A2", "text", "Price"),
                    "B1": cell("B1", "number", 40),
                    "B2": cell("B2", "number", 2.5),
                },
            },
            {
                "name": "Totals",
                "dimensions": [1, 1, 1, 2],
                "cells": {
                    "A1": cell("A1", "number", 999, "=Inputs!B1*Inputs!B2"),
                    "B1": cell("B1", "blank", None, "=A1+1"),
                },
            },
        ],
    }


def test_the_command_prints_the_model(book):
    finished = _cli(book)
    assert finished.returncode == 0, finished.stderr
    assert json.loads(finished.stdout) == _expected_document(str(book.resolve()))


def test_the_function_returns_the_same_document_as_the_command(book):
    assert json.loads(intake_workbook(book)) == _expected_document(str(book.resolve()))


def test_the_command_prints_the_model_version_in_its_summary(book):
    finished = _cli(book, "--summary")
    assert finished.returncode == 0, finished.stderr
    summary = json.loads(finished.stdout)
    assert summary["model_version"] == 1 and summary["formula_count"] == 2


def test_the_command_refuses_an_array_formula_with_a_reason(tmp_path):
    spill = '<row r="1"><c r="A1"><f t="array" ref="A1:A2">SEQUENCE(2)</f><v>1</v></c></row><row r="2"><c r="A2"><v>2</v></c></row>'
    finished = _cli(_write(tmp_path / "spill.xlsx", {"Data": spill}))
    assert finished.returncode == 1 and finished.stdout == ""
    assert "array_spill_refused" in json.loads(finished.stderr)["error"]


# Stored values of every kind


KINDS = (
    '<row r="1">'
    '<c r="A1" t="str"><f>"a"&amp;"b"</f><v>stale text</v></c>'
    '<c r="B1" t="b"><f>1=1</f><v>0</v></c>'
    '<c r="C1" t="e"><f>1/0</f><v>#N/A</v></c>'
    '<c r="D1" t="b"><v>1</v></c>'
    '<c r="E1" t="e"><v>#DIV/0!</v></c>'
    '<c r="F1" t="inlineStr"><is><t>007</t></is></c>'
    "</row>"
)


def test_a_stored_value_of_any_kind_is_never_a_result(tmp_path):
    cells = intake_workbook_model(_write(tmp_path / "kinds.xlsx", {"Data": KINDS})).sheet("Data").cells
    stored = {address: (cell.data_type, cell.value) for address, cell in cells.items()}
    assert stored["A1"] == ("text", "stale text")
    assert stored["B1"] == ("boolean", False)
    assert stored["D1"] == ("boolean", True)
    assert stored["F1"] == ("text", "007")
    assert (cells["C1"].data_type, cells["C1"].value["error"]) == ("error", "#N/A")
    assert (cells["E1"].data_type, cells["E1"].value["error"]) == ("error", "#DIV/0!")
    for address in ("A1", "B1", "C1"):
        assert cells[address].formula.result is None, address
        assert cells[address].formula.dependencies == (), address
    for address in ("D1", "E1", "F1"):
        assert cells[address].formula is None, address
    assert {cell.provenance.origin for cell in cells.values()} == {"imported"}


STYLES = (
    f'<?xml version="1.0"?><styleSheet xmlns="{MAIN}">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="0.000&quot; kg&quot;"/></numFmts>'
    '<fonts count="1"><font/></fonts><fills count="1"><fill/></fills><borders count="1"><border/></borders>'
    '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
    '<cellXfs count="5"><xf numFmtId="0"/><xf numFmtId="22"/><xf numFmtId="4"/><xf numFmtId="164"/><xf numFmtId="7"/></cellXfs>'
    "</styleSheet>"
)
STYLED = (
    '<row r="1"><c r="A1" s="0"><v>1</v></c><c r="B1" s="1"><v>45306.5</v></c><c r="C1" s="2"><v>1234.5</v></c>'
    '<c r="D1" s="3"><v>2</v></c><c r="E1" s="4"><v>3</v></c><c r="F1" s="4"><v>4</v></c></row>'
)


def test_number_formats_are_kept_and_one_that_cannot_be_named_is_listed(tmp_path):
    book = intake_workbook_model(_write(tmp_path / "styled.xlsx", {"Data": STYLED}, styles=STYLES))
    formats = {address: cell.number_format for address, cell in book.sheet("Data").cells.items()}
    assert formats == {
        "A1": None,  # General
        "B1": "m/d/yy h:mm",  # built-in 22
        "C1": "#,##0.00",  # built-in 4
        "D1": '0.000" kg"',  # the workbook's own format 164
        "E1": None,  # built-in 7 depends on the locale
        "F1": None,
    }
    assert book.metadata["intake"]["not_carried"] == [
        {"kind": "number_format", "sheet": "Data", "format_id": 7, "count": 2}
    ]


# What version 1 does not carry is listed


def test_content_with_no_place_in_the_model_is_listed(tmp_path):
    book = _write(
        tmp_path / "rich.xlsx",
        {"Shown": '<row r="1"><c r="A1"><v>1</v></c></row>', "Kept back": '<row r="1"><c r="A1"><v>2</v></c></row>'},
        after_cells=(
            '<mergeCells count="2"><mergeCell ref="C1:D1"/><mergeCell ref="C2:D2"/></mergeCells>'
            '<dataValidations count="1"><dataValidation type="whole" sqref="A1"/></dataValidations>'
        ),
        in_workbook='<definedNames><definedName name="Rate">Shown!$A$1</definedName></definedNames>',
        states={"Kept back": "hidden"},
    )
    listed = intake_workbook_model(book).metadata["intake"]["not_carried"]
    assert listed == [
        {"kind": "defined_names", "count": 1},
        {"kind": "hidden_sheet", "sheet": "Kept back"},
        {"kind": "merged_cells", "sheet": "Shown", "count": 2},
        {"kind": "data_validations", "sheet": "Shown", "count": 1},
        {"kind": "merged_cells", "sheet": "Kept back", "count": 2},
        {"kind": "data_validations", "sheet": "Kept back", "count": 1},
    ]
    assert summarize(intake_workbook_model(book))["not_carried"] == listed
    jsonschema.validate(json.loads(intake_workbook(book)), SCHEMA)


# Refusals


def _refused(tmp_path, cells, name="Data"):
    finished = _cli(_write(tmp_path / "refused.xlsx", {name: cells}))
    assert finished.returncode == 1 and finished.stdout == "", finished.stdout
    assert "Traceback" not in finished.stderr
    return json.loads(finished.stderr)["error"]


def test_a_filled_down_formula_is_refused_as_a_group_and_not_as_a_spill(tmp_path):
    shared = (
        '<row r="1"><c r="A1"><v>1</v></c><c r="B1"><f t="shared" ref="B1:B2" si="0">A1*2</f><v>2</v></c></row>'
        '<row r="2"><c r="A2"><v>2</v></c><c r="B2"><f t="shared" si="0"/><v>4</v></c></row>'
    )
    reason = _refused(tmp_path, shared)
    assert reason.startswith("unsupported_formula_group") and "spill" not in reason


def test_a_data_table_is_refused_as_a_group_and_not_as_a_spill(tmp_path):
    table = '<row r="1"><c r="A1"><f t="dataTable" ref="A1:B2" r1="D1">0</f><v>1</v></c></row>'
    reason = _refused(tmp_path, table)
    assert reason.startswith("unsupported_formula_group") and "spill" not in reason


def test_a_formula_cell_with_no_text_is_not_read_as_a_literal(tmp_path):
    reason = _refused(tmp_path, '<row r="1"><c r="A1"><f/><v>5</v></c><c r="B1"><f>A1*2</f></c></row>')
    assert reason.startswith("unsupported_formula") and "Data!A1" in reason


def test_a_cell_with_no_address_is_refused_and_not_dropped(tmp_path):
    reason = _refused(tmp_path, '<row r="1"><c r="A1"><v>111</v></c><c><v>222</v></c></row>')
    assert "no address" in reason


@pytest.mark.parametrize("stored", ["1e999", "-1e999", "NaN"])
def test_a_number_that_is_not_finite_is_refused_by_name_of_cell(tmp_path, stored):
    cells = f'<row r="7"><c r="C7"><v>{stored}</v></c></row>'
    assert "Data!C7" in _refused(tmp_path, cells)
    summary = _cli(_write(tmp_path / "again.xlsx", {"Data": cells}), "--summary")
    assert summary.returncode == 1 and summary.stdout == ""


def test_an_error_excel_does_not_have_is_refused(tmp_path):
    reason = _refused(tmp_path, '<row r="1"><c r="A1" t="e"><v>#N/A!</v></c></row>')
    assert "Traceback" not in reason


def test_a_sheet_name_the_model_cannot_hold_is_refused_with_the_reason(tmp_path):
    reason = _refused(tmp_path, '<row r="1"><c r="A1"><v>1</v></c></row>', name="Q1!")
    assert "model version 1" in reason and "'!'" in reason


def test_a_damaged_package_is_refused_in_one_line(tmp_path):
    good = _write(tmp_path / "good.xlsx", {"Data": '<row r="1"><c r="A1"><v>' + "7" * 4000 + "</v></c></row>"})
    damaged = tmp_path / "damaged.xlsx"
    with zipfile.ZipFile(good) as source, zipfile.ZipFile(damaged, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            target.writestr(item.filename, source.read(item.filename))
    raw = bytearray(damaged.read_bytes())
    with zipfile.ZipFile(damaged) as archive:
        sheet = archive.getinfo("xl/worksheets/sheet1.xml")
    start = sheet.header_offset + 30 + len(sheet.filename)
    for offset in range(start + 4, start + 12):
        raw[offset] ^= 0xFF
    damaged.write_bytes(bytes(raw))
    finished = _cli(damaged)
    assert finished.returncode == 1 and finished.stdout == ""
    assert "Traceback" not in finished.stderr
    assert len(finished.stderr.strip().splitlines()) == 1
    assert "error" in json.loads(finished.stderr)


def test_whatever_intake_returns_the_reader_accepts(tmp_path):
    for name, cells in {"Data": KINDS, "Sums": TOTALS.replace("Inputs!", "")}.items():
        document = intake_workbook(_write(tmp_path / f"{name}.xlsx", {name: cells}))
        jsonschema.validate(json.loads(document), SCHEMA)
        assert hydrate(document).to_json().encode("utf-8") == document
