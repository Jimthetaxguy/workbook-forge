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
    codes = set(json.loads(registry.read_text(encoding="utf-8"))["codes"]) if registry.exists() else None
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
