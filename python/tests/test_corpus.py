"""Robustness corpus: every fixtures/corpus workbook through every engine.

tools/corpus_check.py runs open, inspect, calculate, a value edit, an
absent-cell insert, export and reimport. This test holds the matrix to
tools/corpus/expectations.json (anything not listed must pass) and never
accepts a crash, a corrupt package or a lost value. The checks are about
Workbook Forge's own reader and writers, not Excel results.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import corpus_check  # noqa: E402

DEFECTS = {"crash", "corrupt", "lossy"}


@pytest.fixture(scope="module")
def rust_bin() -> Path:
    cargo = shutil.which("cargo")
    if cargo is None:
        pytest.skip("Rust toolchain is unavailable; the Rust corpus engine was not run")
    target = ROOT / "rust" / "target"
    result = subprocess.run(
        [cargo, "build", "--manifest-path", str(ROOT / "rust/Cargo.toml"), "--locked", "--offline",
         "--example", "corpus_roundtrip"],
        env=dict(os.environ, CARGO_TARGET_DIR=str(target)), text=True, capture_output=True, timeout=180,
    )
    assert result.returncode == 0, result.stderr
    return target / "debug" / "examples" / ("corpus_roundtrip.exe" if os.name == "nt" else "corpus_roundtrip")


def test_corpus_manifest_lists_every_file_and_only_synthetic_writers():
    manifest = json.loads((ROOT / "fixtures/corpus/MANIFEST.json").read_text(encoding="utf-8"))["files"]
    files = {path.name for path in corpus_check.corpus_files([])}
    assert files == set(manifest)
    assert {item["writer"] for item in manifest.values()} <= {"openpyxl", "xlsxwriter", "handbuilt", "libreoffice"}
    assert len(files) >= 50


def test_every_expectation_names_a_corpus_file_and_a_registered_code():
    expectations = json.loads(corpus_check.EXPECTATIONS.read_text(encoding="utf-8"))
    registry = ROOT / "catalog/refusal-codes.json"
    codes = None
    if registry.exists():
        table = json.loads(registry.read_text(encoding="utf-8"))
        codes = set(table["codes"]) | set(table["inherited"]["codes"])
    files = {path.name for path in corpus_check.corpus_files([])}
    for name, engines in expectations.items():
        if name.startswith("_"):
            continue
        assert name in files, name
        for engine, outcome in engines.items():
            assert engine in {"py_toolkit", "py_canonical", "rust"}
            assert outcome.split(":", 1)[0] in {"pass", "refuse", "untyped"}, outcome
            if outcome.startswith("refuse:"):
                code = outcome.split(":", 1)[1]
                assert codes is None or code in codes, code


def test_corpus_matrix_matches_expectations(rust_bin):
    files = corpus_check.corpus_files([])
    results = corpus_check.run_isolated(files, ["py_toolkit", "py_canonical", "rust"], rust_bin)
    defects = [
        f"{name} {engine}: {record['outcome']} at {record.get('stage')}: {record.get('error')}"
        for name, row in results.items()
        for engine, record in row.items()
        if record["outcome"] in DEFECTS
    ]
    assert not defects, "\n".join(defects)
    expectations = json.loads(corpus_check.EXPECTATIONS.read_text(encoding="utf-8"))
    mismatches = corpus_check.expectation_mismatches(results, expectations)
    assert not mismatches, "\n".join(mismatches)


def test_refusal_code_table_is_shared_and_complete():
    catalog = (ROOT / "catalog/refusal-codes.json").read_bytes()
    assert (ROOT / "rust/src/refusal_codes.json").read_bytes() == catalog
    table = json.loads(catalog)
    assert {rule["code"] for rule in table["rules"]} <= set(table["codes"])
    documented = (ROOT / "docs/refusal-codes.md").read_text(encoding="utf-8")
    for code in table["codes"]:
        assert f"`{code}`" in documented, code


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("VBA or macro-enabled package content is not accepted", "macro_content"),
        ("v0.1 accepts .xlsx only; register an audited adapter for other formats", "not_xlsx"),
        ("cannot export calculation diagnostics: unsupported function CONCATENATE", "calculation_diagnostics"),
        ("workbook exceeds uncompressed size limit", "package_limit"),
        ("refusing to overwrite the source workbook", "output_exists"),
        ("cannot open workbook: File is not a zip file", "package_damaged"),
    ],
)
def test_untagged_refusals_are_classified(message, code):
    from workbook_forge.workbook import UnsupportedWorkbook, WorkbookError

    assert WorkbookError(message).code == code
    assert UnsupportedWorkbook(message).code == code
    assert WorkbookError(message, code="merged_cell").code == "merged_cell"
    assert UnsupportedWorkbook("nothing in the table matches this").code == "unsupported_package"
    assert WorkbookError("nothing in the table matches this").code == "workbook_error"


def test_strict_ooxml_is_refused_with_its_code():
    from workbook_forge.workbook import UnsupportedWorkbook
    from workbook_forge.xlsx import import_xlsx

    with pytest.raises(UnsupportedWorkbook) as caught:
        import_xlsx(ROOT / "fixtures/corpus/hb_strict_ooxml.xlsx")
    assert caught.value.code == "strict_ooxml"


def test_chartsheets_are_listed_and_preserved(tmp_path):
    import zipfile

    from workbook_forge.workbook import Workbook
    from workbook_forge.xlsx import export_xlsx, import_xlsx

    source = ROOT / "fixtures/corpus/xw_chartsheet.xlsx"
    with Workbook.open(source) as workbook:
        chartsheets = workbook.chartsheets
        assert len(chartsheets) == 1
        assert len(workbook.sheet_entry_names) == len(workbook.sheet_names) + 1
    part = next(iter(chartsheets.values()))
    target = tmp_path / "out.xlsx"
    export_xlsx(import_xlsx(source), target)
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(target) as after:
        assert before.read(part) == after.read(part)


def test_newer_error_values_refuse_with_their_code():
    from workbook_forge.workbook import UnsupportedWorkbook
    from workbook_forge.xlsx import import_xlsx

    with pytest.raises(UnsupportedWorkbook) as caught:
        import_xlsx(ROOT / "fixtures/corpus/hb_errors_newer_values.xlsx")
    assert caught.value.code == "unsupported_error_value"
    import_xlsx(ROOT / "fixtures/corpus/hb_errors_as_values.xlsx")


# Sentences both engines classify. Rust locks the same pairs in
# xlsx::refusal_code_tests::prefixed_and_classified_codes.
_SHARED_REFUSAL_CODES = (
    (
        "out_of_bounds: Sheet1!A1 is outside this session's grid of 10 rows x 10 columns",
        "out_of_bounds",
    ),
    (
        "unsupported_edit: Sheet1!B2 is inside a merged range; only its top-left cell can hold content",
        "unsupported_edit",
    ),
    (
        "invalid_bound: insertion bound must be within 1..=1048576 rows and 1..=16384 columns",
        "invalid_bound",
    ),
    ("not_a_code: the value is invalid", "package_damaged"),
    ("VBA content is forbidden", "macro_content"),
    ("VBA or macro-enabled package content is not accepted", "macro_content"),
    ("macro-enabled workbook content is not accepted", "macro_content"),
    ("macro content is forbidden", "macro_content"),
    ("macro-free transitional workbook content type required", "unsupported_package"),
    ("1904 formula edits are unsupported", "date_1904_unsupported"),
    (
        "1904 date-system formula caches cannot be calculated or exported",
        "date_1904_unsupported",
    ),
    (
        "formula calculation does not support the workbook's 1904 date system",
        "date_1904_unsupported",
    ),
    (
        "formula edits in imported 1904 date-system workbooks are unsupported",
        "date_1904_unsupported",
    ),
    (
        "formula references unknown worksheet 'Other' at Sheet1!A1",
        "formula_unsupported",
    ),
    ("invalid Excel error value at A10", "package_damaged"),
)


@pytest.mark.parametrize(("message", "code"), _SHARED_REFUSAL_CODES)
def test_prefixed_and_narrowed_refusals_share_one_code(message, code):
    """Registered prefixes and the narrowed phrases match Rust's XlsxError::code()."""
    from workbook_forge.workbook import UnsupportedWorkbook, WorkbookError, refusal_code

    assert refusal_code(message) == code
    assert WorkbookError(message).code == code
    assert UnsupportedWorkbook(message).code == code
    assert WorkbookError(message, code="merged_cell").code == "merged_cell"


def test_narrowed_phrases_do_not_mislabel_adjacent_messages():
    from workbook_forge.workbook import UnsupportedWorkbook, WorkbookError, refusal_code

    macro_free = "workbook part must use the macro-free XLSX content type"
    assert refusal_code(macro_free) is None
    assert UnsupportedWorkbook(macro_free).code == "unsupported_package"
    assert "macro_content" != UnsupportedWorkbook(macro_free).code

    date1904 = "workbook date1904 setting is not a recognized Boolean"
    assert refusal_code(date1904) is None
    assert UnsupportedWorkbook(date1904).code == "unsupported_package"
    assert WorkbookError(date1904).code == "workbook_error"

    assert refusal_code("unknown worksheet") is None
    assert WorkbookError("unknown worksheet").code == "workbook_error"


def _package_parts(workbook_xml, worksheet_xml, workbook_type=None):
    content_type = (
        workbook_type
        or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
    )
    return {
        "[Content_Types].xml": (
            '<?xml version="1.0"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/xl/workbook.xml" ContentType="{content_type}"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>"
        ).encode(),
        "_rels/.rels": (
            '<?xml version="1.0"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/>'
            "</Relationships>"
        ).encode(),
        "xl/workbook.xml": workbook_xml,
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/>'
            "</Relationships>"
        ).encode(),
        "xl/worksheets/sheet1.xml": worksheet_xml,
    }


def _write_package(path, parts):
    import zipfile

    with zipfile.ZipFile(path, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)


_WORKBOOK = (
    b'<?xml version="1.0"?>'
    b'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    b'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    b'<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
)
_WORKSHEET = (
    b'<?xml version="1.0"?>'
    b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    b'<sheetData><row r="1"><c r="A1"><v>1</v></c></row></sheetData></worksheet>'
)


def test_wrong_roots_and_settings_are_not_strict_or_1904_or_macro(tmp_path):
    """The phrase table must not stamp these raise sites with a neighboring code."""
    from workbook_forge.workbook import UnsupportedWorkbook, Workbook

    cases = {
        "root.xlsx": (
            _package_parts(
                _WORKBOOK.replace(b"<workbook ", b"<notWorkbook ").replace(
                    b"</workbook>", b"</notWorkbook>"
                ),
                _WORKSHEET,
            ),
            "unsupported_package",
        ),
        "part.xlsx": (
            _package_parts(
                _WORKBOOK,
                _WORKSHEET.replace(b"<worksheet ", b"<notWorksheet ").replace(
                    b"</worksheet>", b"</notWorksheet>"
                ),
            ),
            "unsupported_package",
        ),
        "date1904.xlsx": (
            _package_parts(
                _WORKBOOK.replace(
                    b"<sheets>",
                    b'<workbookPr date1904="maybe"/><sheets>',
                ),
                _WORKSHEET,
            ),
            "unsupported_package",
        ),
        "content-type.xlsx": (
            _package_parts(_WORKBOOK, _WORKSHEET, workbook_type="application/xml"),
            "unsupported_package",
        ),
    }
    for name, (parts, code) in cases.items():
        path = tmp_path / name
        _write_package(path, parts)
        with pytest.raises(UnsupportedWorkbook) as caught:
            Workbook.open(path)
        assert caught.value.code == code, name
        assert caught.value.code != "strict_ooxml"
        assert caught.value.code != "date_1904_unsupported"
        assert caught.value.code != "macro_content"
