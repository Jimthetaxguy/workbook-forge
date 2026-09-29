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
    before_cells: str = "",
    parts: dict[str, tuple[str, str]] | None = None,
    sheet_relationships: str = "",
    date1904: bool = False,
) -> Path:
    """Write a workbook. `parts` maps a part name to its content type and text."""
    names = list(sheets)
    states = states or {}
    extra = parts or {}
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, len(names) + 1)
    )
    written = {
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
            + "".join(f'<Override PartName="/{name}" ContentType="{kind}"/>' for name, (kind, _) in extra.items())
            + f"{overrides}</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{RELATIONSHIPS}/officeDocument" Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            f'<?xml version="1.0"?><workbook xmlns="{MAIN}" xmlns:r="{RELATIONSHIPS}">'
            + ('<workbookPr date1904="1"/>' if date1904 else "")
            + "<sheets>"
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
        written["xl/styles.xml"] = styles
    for index, name in enumerate(names, start=1):
        written[f"xl/worksheets/sheet{index}.xml"] = (
            f'<?xml version="1.0"?><worksheet xmlns="{MAIN}">{before_cells}<sheetData>{sheets[name]}</sheetData>{after_cells}</worksheet>'
        )
        if sheet_relationships:
            written[f"xl/worksheets/_rels/sheet{index}.xml.rels"] = (
                '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f"{sheet_relationships}</Relationships>"
            )
    for name, (_, text) in extra.items():
        written[name] = text
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in written.items():
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
        {"where": "package", "what": "cell styles other than number formats", "count": 1},
        {"where": "sheet", "what": "number format 7", "count": 2, "sheet": "Data"},
    ]


# ECMA-376 part 1, 18.8.30. Written out here from the standard, not read from the code.
SAME_IN_EVERY_LANGUAGE = {
    1: "0", 2: "0.00", 3: "#,##0", 4: "#,##0.00", 9: "0%", 10: "0.00%", 11: "0.00E+00", 12: "# ?/?",
    13: "# ??/??", 14: "mm-dd-yy", 15: "d-mmm-yy", 16: "d-mmm", 17: "mmm-yy", 18: "h:mm AM/PM",
    19: "h:mm:ss AM/PM", 20: "h:mm", 21: "h:mm:ss", 22: "m/d/yy h:mm", 37: "#,##0 ;(#,##0)",
    38: "#,##0 ;[Red](#,##0)", 39: "#,##0.00;(#,##0.00)", 40: "#,##0.00;[Red](#,##0.00)", 45: "mm:ss",
    46: "[h]:mm:ss", 47: "mmss.0", 48: "##0.0E+0", 49: "@",
}


@pytest.mark.parametrize("format_id", range(0, 60))
def test_every_built_in_number_format(tmp_path, format_id):
    styles = (
        f'<?xml version="1.0"?><styleSheet xmlns="{MAIN}"><fonts count="1"><font/></fonts>'
        '<fills count="1"><fill/></fills><borders count="1"><border/></borders>'
        f'<cellXfs count="1"><xf numFmtId="{format_id}"/></cellXfs></styleSheet>'
    )
    book = intake_workbook_model(
        _write(tmp_path / "one.xlsx", {"Data": '<row r="1"><c r="A1" s="0"><v>1</v></c></row>'}, styles=styles)
    )
    listed = [entry for entry in book.metadata["intake"]["not_carried"] if entry["where"] == "sheet"]
    assert book.sheet("Data").cells["A1"].number_format == SAME_IN_EVERY_LANGUAGE.get(format_id)
    if format_id == 0 or format_id in SAME_IN_EVERY_LANGUAGE:
        assert listed == []
    else:
        assert listed == [{"where": "sheet", "what": f"number format {format_id}", "count": 1, "sheet": "Data"}]


# What version 1 does not carry is listed


ONE_CELL = '<row r="1"><c r="A1"><v>1</v></c></row>'
X14 = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
OFFICE = "application/vnd.openxmlformats-officedocument"


def _listed(tmp_path, cells=ONE_CELL, **written):
    path = _write(tmp_path / "rich.xlsx", {"Shown": cells}, **written)
    jsonschema.validate(json.loads(intake_workbook(path)), SCHEMA)
    listed = intake_workbook_model(path).metadata["intake"]["not_carried"]
    assert summarize(intake_workbook_model(path))["not_carried"] == listed
    return [(entry["where"], entry.get("sheet"), entry["what"], entry["count"]) for entry in listed]


def test_a_plain_workbook_has_nothing_to_list(tmp_path):
    assert _listed(tmp_path) == []


@pytest.mark.parametrize(
    ("markup", "what", "count"),
    [
        ('<mergeCells count="2"><mergeCell ref="C1:D1"/><mergeCell ref="C2:D2"/></mergeCells>', "mergeCells", 2),
        ('<dataValidations count="1"><dataValidation type="whole" sqref="A1"/></dataValidations>', "dataValidations", 1),
        ('<conditionalFormatting sqref="A1"><cfRule type="cellIs" priority="1"/></conditionalFormatting>', "conditionalFormatting", 1),
        ('<hyperlinks><hyperlink ref="A1" location="Shown!A1"/></hyperlinks>', "hyperlinks", 1),
        ('<autoFilter ref="A1:A1"/>', "autoFilter", 1),
        ('<sheetProtection sheet="1"/>', "sheetProtection", 1),
        ('<protectedRanges><protectedRange sqref="A1" name="r"/></protectedRanges>', "protectedRanges", 1),
        ('<scenarios><scenario name="s"/></scenarios>', "scenarios", 1),
        ('<headerFooter><oddHeader>Draft</oddHeader></headerFooter>', "headerFooter", 1),
        ('<pageSetup orientation="landscape"/>', "pageSetup", 1),
        ('<oleObjects><oleObject progId="x" shapeId="1"/></oleObjects>', "oleObjects", 1),
        ('<an_element_nobody_has_heard_of/>', "an_element_nobody_has_heard_of", 1),
        # Excel 2010 and later keep newer forms of validation, formats and sparklines here.
        (f'<extLst><ext uri="x"><x14:dataValidations xmlns:x14="{X14}" count="1"/></ext><ext uri="y"/></extLst>', "extLst", 2),
        (f'<mc:AlternateContent xmlns:mc="{MC}"><mc:Choice Requires="x14"/></mc:AlternateContent>', "AlternateContent (extension)", 1),
    ],
)
def test_whatever_follows_the_cells_of_a_sheet_is_listed(tmp_path, markup, what, count):
    assert _listed(tmp_path, after_cells=markup) == [("sheet", "Shown", what, count)]


@pytest.mark.parametrize(
    ("markup", "what", "count"),
    [
        ('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" state="frozen"/></sheetView></sheetViews>', "sheetViews", 1),
        ('<cols><col min="2" max="2" hidden="1"/><col min="4" max="4" width="30"/></cols>', "cols", 2),
        ('<sheetPr><tabColor rgb="FFFF0000"/></sheetPr>', "sheetPr", 1),
    ],
)
def test_whatever_comes_before_the_cells_of_a_sheet_is_listed(tmp_path, markup, what, count):
    assert _listed(tmp_path, before_cells=markup) == [("sheet", "Shown", what, count)]


@pytest.mark.parametrize(
    ("cells", "what", "count"),
    [
        ('<row r="1" hidden="1"><c r="A1"><v>1</v></c></row><row r="2" hidden="1"><c r="A2"><v>1</v></c></row>', "row attribute hidden", 2),
        ('<row r="1" ht="40" customHeight="1"><c r="A1"><v>1</v></c></row>', "row attribute ht", 1),
        ('<row r="1"><c r="A1" vm="1"><v>1</v></c></row>', "cell attribute vm", 1),
        ('<row r="1"><c r="A1" ph="1"><v>1</v></c></row>', "cell attribute ph", 1),
        ('<row r="1"><c r="A1"><v>1</v><extLst><ext uri="x"/></extLst></c></row>', "cell element extLst", 1),
        ('<row r="1"><c r="A1" t="inlineStr"><is><r><rPr><b/></rPr><t>bold</t></r><r><t> plain</t></r></is></c></row>', "text formatting within a cell", 1),
        ('<row r="1"><c r="A1" t="inlineStr"><is><t>kanji</t><rPh sb="0" eb="1"><t>kana</t></rPh></is></c></row>', "phonetic text", 1),
    ],
)
def test_what_a_row_or_a_cell_holds_beyond_its_value_is_listed(tmp_path, cells, what, count):
    listed = _listed(tmp_path, cells=cells)
    assert ("sheet", "Shown", what, count) in listed
    assert all(entry[2].split()[0] in {"row", "cell", "text", "phonetic"} for entry in listed)


@pytest.mark.parametrize(
    ("markup", "what", "count"),
    [
        ('<definedNames><definedName name="Rate">Shown!$A$1</definedName><definedName name="_xlnm.Print_Area">Shown!$A$1</definedName></definedNames>', "definedNames", 2),
        ('<calcPr calcMode="manual" iterate="1"/>', "calcPr", 1),
        ('<workbookProtection lockStructure="1"/>', "workbookProtection", 1),
        ('<externalReferences><externalReference xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="rId9"/></externalReferences>', "externalReferences", 1),
        ('<pivotCaches><pivotCache cacheId="1" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="rId9"/></pivotCaches>', "pivotCaches", 1),
        ('<bookViews><workbookView/></bookViews>', "bookViews", 1),
    ],
)
def test_whatever_else_the_workbook_part_holds_is_listed(tmp_path, markup, what, count):
    assert _listed(tmp_path, in_workbook=markup) == [("workbook", None, what, count)]


@pytest.mark.parametrize("state", ["hidden", "veryHidden"])
def test_a_sheet_that_is_not_shown_is_listed(tmp_path, state):
    path = _write(tmp_path / "two.xlsx", {"Shown": ONE_CELL, "Kept back": ONE_CELL}, states={"Kept back": state})
    assert intake_workbook_model(path).metadata["intake"]["not_carried"] == [
        {"where": "workbook", "what": f"sheet state {state}", "count": 1, "sheet": "Kept back"}
    ]


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("xl/comments1.xml", f"{OFFICE}.spreadsheetml.comments+xml"),
        ("xl/drawings/drawing1.xml", f"{OFFICE}.drawing+xml"),
        ("xl/charts/chart1.xml", f"{OFFICE}.drawingml.chart+xml"),
        ("xl/externalLinks/externalLink1.xml", f"{OFFICE}.spreadsheetml.externalLink+xml"),
        ("xl/connections.xml", f"{OFFICE}.spreadsheetml.connections+xml"),
        ("xl/theme/theme1.xml", f"{OFFICE}.theme+xml"),
        ("xl/something/new.xml", "application/x-not-yet-invented"),
    ],
)
def test_a_part_with_no_place_in_the_model_is_listed(tmp_path, name, kind):
    assert _listed(tmp_path, parts={name: (kind, "<x/>")}) == [("package", None, f"part of type {kind}", 1)]


def test_a_part_joined_to_a_sheet_is_listed_by_how_it_is_joined(tmp_path):
    joined = "".join(
        f'<Relationship Id="rId{n}" Type="{RELATIONSHIPS}/{kind}" Target="{target}"{mode}/>'
        for n, (kind, target, mode) in enumerate(
            [("comments", "../comments1.xml", ""), ("hyperlink", "https://example.invalid/", ' TargetMode="External"')], start=1
        )
    )
    listed = _listed(
        tmp_path, sheet_relationships=joined, parts={"xl/comments1.xml": (f"{OFFICE}.spreadsheetml.comments+xml", "<x/>")}
    )
    assert listed == [
        ("package", None, f"part of type {OFFICE}.spreadsheetml.comments+xml", 1),
        ("package", None, "relationship comments", 1),
        ("package", None, "relationship hyperlink", 1),
    ]


def test_the_list_holds_names_from_the_file_and_no_paths(tmp_path):
    path = _write(tmp_path / "rich.xlsx", {"Shown": ONE_CELL}, after_cells='<autoFilter ref="A1:A1"/>')
    assert str(tmp_path) not in json.dumps(intake_workbook_model(path).metadata)


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


@pytest.mark.parametrize(
    "formula",
    [
        '<f t="array" ref="A1">SUM(B1:B2*2)</f>',  # an array formula in one cell
        '<f t="array" ref="A1:A3">SEQUENCE(3)</f>',
        '<f t="shared" ref="A1:A3" si="0">ROW()</f>',
    ],
)
def test_no_array_or_grouped_formula_gets_through(tmp_path, formula):
    reason = _refused(tmp_path, f'<row r="1"><c r="A1">{formula}<v>1</v></c></row>')
    assert reason.startswith(("array_spill_refused", "unsupported_formula_group"))


def test_a_dynamic_array_marked_only_by_cell_metadata_is_refused(tmp_path):
    reason = _refused(tmp_path, '<row r="1"><c r="A1" cm="1"><f>SEQUENCE(3)</f><v>1</v></c></row>')
    assert reason.startswith("array_spill_refused")


def test_a_grouped_formula_that_is_also_marked_as_dynamic_is_refused(tmp_path):
    cells = '<row r="1"><c r="A1" cm="1"><f t="shared" ref="A1:A2" si="0">ROW()</f><v>1</v></c></row>'
    assert _refused(tmp_path, cells).startswith(("array_spill_refused", "unsupported_formula_group"))


def test_a_workbook_that_counts_dates_from_1904_is_refused(tmp_path):
    finished = _cli(_write(tmp_path / "old.xlsx", {"Data": ONE_CELL}, date1904=True))
    assert finished.returncode == 1 and finished.stdout == ""
    assert "1904" in json.loads(finished.stderr)["error"]


@pytest.mark.parametrize(
    ("cell", "reason"),
    [
        ('<c r="A1" t="d"><v>2024-01-15T00:00:00</v></c>', "a date written as text"),
        ('<c r="A1" t="zz"><v>12</v></c>', "type 'zz'"),
        ('<c r="A1"><v>1_000</v></c>', "not written as a number"),
        ('<c r="A1"><v> 12 </v></c>', "not written as a number"),
        ('<c r="A1"><v>\uff11\uff12</v></c>', "not written as a number"),
        ('<c r="A1" t="n"><v>0x10</v></c>', "not written as a number"),
        ('<c r="A1"><v>Infinity</v></c>', "not written as a number"),
    ],
)
def test_a_stored_value_that_would_be_misread_is_refused(tmp_path, cell, reason):
    refusal = _refused(tmp_path, f'<row r="1">{cell}</row>')
    assert refusal.startswith("unsupported_cell") and reason in refusal and "Data!A1" in refusal


@pytest.mark.parametrize(
    ("stored", "value"),
    [("12", 12), ("-0.5", -0.5), ("1.5E+3", 1500), ("2.5e-3", 0.0025), (".5", 0.5), ("7.", 7), ("+3", 3)],
)
def test_a_number_as_a_workbook_writes_it_is_read(tmp_path, stored, value):
    book = intake_workbook_model(_write(tmp_path / "n.xlsx", {"Data": f'<row r="1"><c r="A1"><v>{stored}</v></c></row>'}))
    assert book.sheet("Data").cells["A1"].value == value


def test_the_reader_every_command_shares_refuses_a_cell_with_no_address(tmp_path):
    from workbook_forge.workbook import UnsupportedWorkbook, Workbook as Package

    path = _write(tmp_path / "placed.xlsx", {"Data": '<row r="1"><c r="A1"><v>111</v></c><c><v>222</v></c></row>'})
    with pytest.raises(UnsupportedWorkbook, match="no address"):
        Package.open(path)


def test_a_workbook_with_macros_is_refused(tmp_path):
    path = _write(tmp_path / "macro.xlsx", {"Data": ONE_CELL}, parts={"xl/vbaProject.bin": ("application/vnd.ms-office.vbaProject", "x")})
    finished = _cli(path)
    assert finished.returncode == 1 and finished.stdout == ""
    assert "macro" in json.loads(finished.stderr)["error"]
