"""Registered refusal prefixes and the relabeled workbook sentences.

Python honors a leading ``token: `` prefix when the token is in the catalog
(``codes`` or ``inherited``). An explicit ``code=`` wins over that prefix and
over the phrase table.
"""

from __future__ import annotations

import pytest

from test_workbook import make_xlsx, write_parts
from workbook_forge.workbook import UnsupportedWorkbook, Workbook, WorkbookError, refusal_code
from workbook_forge.xlsx import _check_insertion

OUT_OF_BOUNDS = (
    "out_of_bounds: Sheet!XFD1048577 is outside this session's grid of 1048576 rows x 16384 columns"
)
UNSUPPORTED_EDIT = (
    "unsupported_edit: Sheet!B1 is inside a merged range; only its top-left cell can hold content"
)
INVALID_BOUND = (
    "invalid_bound: insertion bound must be within 1..=1048576 rows and 1..=16384 columns"
)
FORMULA_SHEET = "formula references unknown worksheet 'Other' at Sheet!A1"
MACRO_FREE = "workbook part must use the macro-free XLSX content type"
DATE1904 = "workbook date1904 setting is not a recognized Boolean"
NON_WORKBOOK = "strict OOXML or non-workbook XML is outside the XLSX adapter profile"
NON_WORKSHEET = "strict OOXML or a non-worksheet part is outside the adapter profile"
CHARTSHEET_EXTERNAL = "chartsheet relationships must be internal"
CHARTSHEET_ABSENT = "chartsheet part is absent: xl/chartsheets/gone.xml"
FILTER = "invalid_reference: unknown worksheet filter"


def test_registered_prefixes_win_over_the_phrase_table():
    assert refusal_code(OUT_OF_BOUNDS) == "out_of_bounds"
    assert WorkbookError(OUT_OF_BOUNDS).code == "out_of_bounds"
    assert refusal_code(UNSUPPORTED_EDIT) == "unsupported_edit"
    assert WorkbookError(UNSUPPORTED_EDIT).code == "unsupported_edit"
    assert refusal_code(INVALID_BOUND) == "invalid_bound"
    assert WorkbookError(INVALID_BOUND).code == "invalid_bound"
    assert refusal_code(FILTER) == "invalid_reference"


def test_unregistered_prefix_is_ignored():
    sized = "export_error: workbook exceeds uncompressed size limit"
    assert refusal_code(sized) == "package_limit"
    assert WorkbookError(sized).code == "package_limit"
    plain = "export_error: nothing in the table matches this"
    assert refusal_code(plain) is None
    assert WorkbookError(plain).code == "workbook_error"
    assert UnsupportedWorkbook(plain).code == "unsupported_package"


def test_explicit_code_wins_over_prefix_and_phrase():
    error = WorkbookError(OUT_OF_BOUNDS, code="merged_cell")
    assert error.code == "merged_cell"
    assert UnsupportedWorkbook(MACRO_FREE, code="package_damaged").code == "package_damaged"


def test_insertion_raises_use_the_prefixed_codes():
    with pytest.raises(UnsupportedWorkbook) as bounds:
        _check_insertion("Sheet", 1, 1_048_577, ())
    assert bounds.value.code == "out_of_bounds"
    assert refusal_code(str(bounds.value)) == "out_of_bounds"

    merged = (("Sheet", ((1, 1, 2, 2),)),)
    with pytest.raises(UnsupportedWorkbook) as edit:
        _check_insertion("Sheet", 2, 1, merged)
    assert edit.value.code == "unsupported_edit"
    assert refusal_code(str(edit.value)) == "unsupported_edit"


def test_unknown_worksheet_phrase_is_the_formula_sentence_only():
    assert refusal_code(FORMULA_SHEET) == "formula_unsupported"
    assert WorkbookError(FORMULA_SHEET).code == "formula_unsupported"
    assert refusal_code("unknown worksheet") is None
    assert WorkbookError("unknown worksheet").code == "workbook_error"
    assert refusal_code("unknown worksheet filter") is None


def _open(path, parts):
    write_parts(path, parts)
    with pytest.raises((WorkbookError, UnsupportedWorkbook)) as caught:
        Workbook.open(path)
    return caught.value


def test_macro_free_content_type_is_unsupported_package(tmp_path):
    path = tmp_path / "type.xlsx"
    parts = make_xlsx(path)
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
        b"spreadsheetml.sheet.main+xml",
        b"spreadsheetml.template.main+xml",
    )
    error = _open(path, parts)
    assert error.code == "unsupported_package"
    assert str(error) == MACRO_FREE


def test_unrecognized_date1904_boolean_is_package_damaged(tmp_path):
    path = tmp_path / "date.xlsx"
    parts = make_xlsx(path)
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(
        b"<sheets>",
        b'<workbookPr date1904="nope"/><sheets>',
    )
    error = _open(path, parts)
    assert error.code == "package_damaged"
    assert str(error) == DATE1904


def test_non_workbook_root_is_package_damaged(tmp_path):
    path = tmp_path / "root.xlsx"
    parts = make_xlsx(path)
    xml = parts["xl/workbook.xml"]
    parts["xl/workbook.xml"] = xml.replace(b"<workbook ", b"<package ", 1).replace(
        b"</workbook>", b"</package>"
    )
    error = _open(path, parts)
    assert error.code == "package_damaged"
    assert str(error) == NON_WORKBOOK


def test_non_worksheet_root_is_package_damaged(tmp_path):
    path = tmp_path / "sheet.xlsx"
    parts = make_xlsx(path)
    xml = parts["xl/worksheets/sheet1.xml"]
    parts["xl/worksheets/sheet1.xml"] = xml.replace(b"<worksheet ", b"<package ", 1).replace(
        b"</worksheet>", b"</package>"
    )
    error = _open(path, parts)
    assert error.code == "package_damaged"
    assert str(error) == NON_WORKSHEET


def test_external_chartsheet_relationship_is_package_damaged(tmp_path):
    path = tmp_path / "external.xlsx"
    parts = make_xlsx(path)
    parts["xl/_rels/workbook.xml.rels"] = (
        parts["xl/_rels/workbook.xml.rels"]
        .replace(
            b"relationships/worksheet",
            b"relationships/chartsheet",
        )
        .replace(b"/>", b' TargetMode="External"/>', 1)
    )
    error = _open(path, parts)
    assert error.code == "package_damaged"
    assert str(error) == CHARTSHEET_EXTERNAL


def test_absent_chartsheet_part_is_package_damaged(tmp_path):
    path = tmp_path / "absent.xlsx"
    parts = make_xlsx(path)
    parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace(
        b'relationships/worksheet" Target="worksheets/sheet1.xml"/>',
        b'relationships/chartsheet" Target="chartsheets/gone.xml"/>',
    )
    error = _open(path, parts)
    assert error.code == "package_damaged"
    assert str(error) == CHARTSHEET_ABSENT
