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


def _write(path: Path, sheets: dict[str, str]) -> Path:
    names = list(sheets)
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
            f"{overrides}</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{RELATIONSHIPS}/officeDocument" Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            f'<?xml version="1.0"?><workbook xmlns="{MAIN}" xmlns:r="{RELATIONSHIPS}"><sheets>'
            + "".join(
                f'<sheet name="{name}" sheetId="{index}" r:id="rId{index}"/>'
                for index, name in enumerate(names, start=1)
            )
            + "</sheets></workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(
                f'<Relationship Id="rId{index}" Type="{RELATIONSHIPS}/worksheet" Target="worksheets/sheet{index}.xml"/>'
                for index in range(1, len(names) + 1)
            )
            + "</Relationships>"
        ),
    }
    for index, name in enumerate(names, start=1):
        parts[f"xl/worksheets/sheet{index}.xml"] = (
            f'<?xml version="1.0"?><worksheet xmlns="{MAIN}"><sheetData>{sheets[name]}</sheetData></worksheet>'
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


def test_the_command_prints_the_model(book):
    finished = _cli(book)
    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.encode("utf-8") == intake_workbook(book) + b"\n"


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
