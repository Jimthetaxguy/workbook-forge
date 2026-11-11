"""The in-memory PreservationEnvelope that import_xlsx assembles (spec 10 Design 1).

Every field is what the importer already held; the envelope names it. The
`handle` is session memory and never appears in describe().
"""
from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from workbook_forge import xlsx
from workbook_forge import xlsx_features as features
from workbook_forge.envelope import PreservationEnvelope
from workbook_forge.model import content_fingerprint
from workbook_forge.toolkit import WorkbookModel
from workbook_forge.workbook import WorkbookError
from workbook_forge.xlsx import import_xlsx

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "fixtures" / "corpus"
MERGED = CORPUS / "op_merged_cells.xlsx"
# The Python importer refuses these eight corpus workbooks. The describe loop
# names the set instead of counting successes against a floor.
PYTHON_IMPORT_REFUSALS = {
    "hb_errors_newer_values.xlsx",
    "hb_in_cell_array_formula.xlsx",
    "hb_in_cell_list.xlsx",
    "hb_macro_content_in_xlsx.xlsx",
    "hb_nested_array.xlsx",
    "hb_no_cell_references.xlsx",
    "hb_rows_without_references.xlsx",
    "hb_strict_ooxml.xlsx",
}
DESCRIBE_KEYS = {
    "source",
    "baseline",
    "tables",
    "merged",
    "date_system",
    "compatibility_version",
    "guards",
    "text",
    "insertion_bound",
}


def _import_copy(tmp_path: Path, name: str) -> tuple[WorkbookModel, bytes]:
    folder = tmp_path / "private-dir-xyz"
    folder.mkdir()
    copy = folder / name
    shutil.copyfile(CORPUS / name, copy)
    return import_xlsx(copy), copy.read_bytes()


def test_envelope_is_assembled_from_import_and_holds_no_path(tmp_path):
    model, data = _import_copy(tmp_path, MERGED.name)
    envelope = model.envelope
    assert isinstance(envelope, PreservationEnvelope)
    described = envelope.describe()
    assert "private-dir-xyz" not in json.dumps(described)
    assert set(described) == DESCRIBE_KEYS
    assert envelope.source.fingerprint == content_fingerprint(data) == described["source"]["fingerprint"]
    assert envelope.source.parts == tuple(sorted(envelope.baseline.parts))
    assert envelope.source.workbook_part in envelope.baseline.parts
    assert envelope.source.sheet_parts == {"Report": "xl/worksheets/sheet1.xml"}
    assert envelope.source.sheet_entries == ("Report",)
    assert envelope.source.chartsheets == {}
    # The handle is kept for the overwrite refusal and nowhere else.
    assert envelope.source.handle == (tmp_path / "private-dir-xyz" / MERGED.name).resolve()
    assert "handle" not in described["source"]
    assert envelope.baseline.document == model.to_dict() == described["baseline"]["document"]
    assert described["baseline"]["parts"] == {
        name: content_fingerprint(part) for name, part in envelope.baseline.parts.items()
    }
    assert envelope.baseline.container is not None
    assert described["baseline"]["container"] == {
        "entries": len(envelope.baseline.container.infos),
        "comment_bytes": len(envelope.baseline.container.comment),
    }
    assert envelope.merged["Report"] == ((1, 1, 4, 1), (1, 4, 1, 6))
    assert described["merged"] == {"Report": [[1, 1, 4, 1], [1, 4, 1, 6]]}
    assert envelope.date_system == "1900"
    assert envelope.compatibility_version == 1
    assert envelope.tables == ()
    # The guard is the envelope's own copy; only its public projection is shared.
    assert envelope.guards is not model._feature_guard
    for name in ("validations", "pivots", "charts"):
        assert features.public(getattr(envelope.guards, name)) == features.public(
            getattr(model._feature_guard, name)
        )
    assert set(described["guards"]) == {"data_validations", "pivots", "charts"}
    assert "private-dir-xyz" not in repr(envelope)
    assert envelope.text.cells == model._text_cells
    assert described["text"] == {"cells": [], "layouts": {}}
    assert envelope.insertion_bound == (1_048_576, 16_384)
    assert described["insertion_bound"] == [1_048_576, 16_384]


def test_authored_model_has_no_envelope():
    assert WorkbookModel(["Sheet1"]).envelope is None


def test_envelope_is_immutable(tmp_path):
    model, _data = _import_copy(tmp_path, MERGED.name)
    envelope = model.envelope
    with pytest.raises(dataclasses.FrozenInstanceError):
        envelope.date_system = "1904"
    with pytest.raises(dataclasses.FrozenInstanceError):
        envelope.source.fingerprint = "sha256:" + "0" * 64
    with pytest.raises(dataclasses.FrozenInstanceError):
        envelope.baseline.document = {}
    with pytest.raises(dataclasses.FrozenInstanceError):
        envelope.text.cells = frozenset()
    # Immutability goes below the field: the mappings are read-only views.
    with pytest.raises(TypeError):
        envelope.source.sheet_parts["Ghost"] = "x"
    with pytest.raises(TypeError):
        envelope.source.chartsheets["Ghost"] = "x"
    with pytest.raises(TypeError):
        envelope.baseline.parts["ghost.xml"] = b""
    with pytest.raises(TypeError):
        envelope.merged["Ghost"] = ()
    with pytest.raises(TypeError):
        envelope.text.layouts["ghost"] = None
    # The document comes out as a fresh copy each time, so a caller's edits
    # never reach the baseline.
    document = envelope.baseline.document
    document["sheets"].clear()
    document["revision"] = 99
    assert envelope.baseline.document == model.to_dict()
    assert envelope.describe()["baseline"]["document"] == model.to_dict()


def _importable_corpus() -> list[Path]:
    return sorted(CORPUS.glob("*.xlsx"))


def test_envelope_describe_is_json_and_sorted_for_every_importable_corpus_workbook():
    # One loop, not a parametrisation: the gate forbids skipped tests, and a
    # workbook the importer refuses has no envelope to describe. The refused
    # names are the set below.
    refused = []
    for path in _importable_corpus():
        try:
            model = import_xlsx(path)
        except WorkbookError:
            refused.append(path.name)
            continue
        where = path.name
        envelope = model.envelope
        described = envelope.describe()
        text = json.dumps(described, sort_keys=True, allow_nan=False)
        assert json.loads(text) == described, where
        assert str(path.parent) not in text, where
        assert described["text"]["cells"] == sorted(described["text"]["cells"]), where
        assert described["text"]["cells"] == sorted(
            [sheet, address] for sheet, address in envelope.text.cells
        ), where
        written_names = {sheet["name"] for sheet in described["baseline"]["document"]["sheets"]}
        # Layouts are described under the sheet name as written; the in-memory
        # dict is keyed casefolded.
        assert set(described["text"]["layouts"]) <= written_names, where
        assert set(described["text"]["layouts"]) == {
            name for name in written_names if name.casefold() in envelope.text.layouts
        }, where
        assert described["source"]["parts"] == sorted(described["baseline"]["parts"]), where
        assert set(described["source"]["sheet_parts"]) == written_names, where
        assert set(described["source"]["sheet_parts"]) <= set(
            described["source"]["sheet_entries"]
        ), where
        assert described["date_system"] in {"1900", "1904"}, where
        assert isinstance(described["compatibility_version"], int), where
    assert set(refused) == PYTHON_IMPORT_REFUSALS, refused


# --- cross-language agreement -----------------------------------------------

# What both importers must hold identically for the same package bytes. The
# baseline document, tables, guards and text layouts are spec 10's own lane
# (its refutation record lists where the two decoders differ today) and are
# compared there, not here.
_AGREED_KEYS = ("source", "merged", "date_system", "compatibility_version", "insertion_bound")


def test_fingerprint_names_the_bytes_that_were_decoded(tmp_path, monkeypatch):
    # The fingerprint is taken from the bytes the package reader decoded, not
    # from a second read of the file, which could see different bytes.
    folder = tmp_path / "private-dir-xyz"
    folder.mkdir()
    copy = folder / MERGED.name
    shutil.copyfile(MERGED, copy)
    original = copy.read_bytes()
    real_open = xlsx.Workbook.open

    def open_then_change_the_file(path):
        workbook = real_open(path)
        with Path(path).open("ab") as handle:
            handle.write(b"written after the package was read")
        return workbook

    monkeypatch.setattr(xlsx.Workbook, "open", staticmethod(open_then_change_the_file))
    envelope = import_xlsx(copy).envelope
    assert copy.read_bytes() != original
    assert envelope.source.fingerprint == content_fingerprint(original)
    assert envelope.source.fingerprint != content_fingerprint(copy.read_bytes())


def _python_envelopes() -> dict[str, dict]:
    described = {}
    for path in _importable_corpus():
        try:
            model = import_xlsx(path)
        except WorkbookError:
            continue
        described[path.name] = model.envelope.describe()
    return described


def _rust_envelopes(paths: list[Path]) -> dict[str, dict]:
    cargo = shutil.which("cargo")
    if cargo is None:
        pytest.skip("Rust toolchain is unavailable; the Rust envelope was not built")
    target = ROOT / "rust" / "target"
    env = dict(os.environ, CARGO_TARGET_DIR=str(target))
    built = subprocess.run(
        [cargo, "build", "--quiet", "--manifest-path", str(ROOT / "rust" / "Cargo.toml"),
         "--locked", "--offline", "--example", "envelope_describe"],
        env=env, text=True, capture_output=True, timeout=600,
    )
    assert built.returncode == 0, built.stderr
    binary = target / "debug" / "examples" / (
        "envelope_describe.exe" if os.name == "nt" else "envelope_describe"
    )
    completed = subprocess.run(
        [str(binary), *(str(path) for path in paths)],
        cwd=ROOT, text=True, capture_output=True, timeout=600,
    )
    assert completed.returncode == 0, completed.stderr
    described = {}
    for line in completed.stdout.splitlines():
        item = json.loads(line)
        described[paths[item["index"]].name] = item
    return described


def test_python_and_rust_envelopes_agree_on_identity_and_baseline_bytes():
    python_side = _python_envelopes()
    paths = [CORPUS / name for name in python_side]
    rust_side = _rust_envelopes(paths)
    differences = []
    refused_by_rust = []
    for name, left in python_side.items():
        item = rust_side[name]
        if "error" in item:
            refused_by_rust.append(f"{name}: {item['error']}")
            continue
        right = item["describe"]
        for key in _AGREED_KEYS:
            if left[key] != right[key]:
                differences.append(f"{name}: {key}: python {left[key]!r} rust {right[key]!r}")
        if left["text"]["cells"] != right["text"]["cells"]:
            differences.append(f"{name}: text.cells")
        if left["baseline"]["parts"] != right["baseline"]["parts"]:
            differences.append(f"{name}: baseline.parts")
    assert not differences, "\n".join(differences)
    # Rust imports every workbook this test described in Python.
    assert refused_by_rust == [], refused_by_rust
