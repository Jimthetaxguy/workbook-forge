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


@pytest.mark.parametrize("name", ["hb_no_cell_references.xlsx", "hb_rows_without_references.xlsx"])
def test_rows_and_cells_without_references_refuse_before_any_export(name):
    from workbook_forge.workbook import POSITIONAL_CELLS_MESSAGE, UnsupportedWorkbook, Workbook
    from workbook_forge.xlsx import import_xlsx

    for reader in (Workbook.open, import_xlsx):
        with pytest.raises(UnsupportedWorkbook) as caught:
            reader(ROOT / "fixtures/corpus" / name)
        assert caught.value.code == "positional_cells"
        assert str(caught.value) == POSITIONAL_CELLS_MESSAGE
