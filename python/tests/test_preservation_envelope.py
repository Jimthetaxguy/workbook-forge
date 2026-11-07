"""The in-memory PreservationEnvelope that import_xlsx assembles (spec 10 Design 1).

Every field is what the importer already held; the envelope names it. The
`handle` is session memory and never appears in describe().
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
import shutil

import pytest

from workbook_forge.envelope import PreservationEnvelope
from workbook_forge.model import content_fingerprint
from workbook_forge.toolkit import WorkbookModel
from workbook_forge.workbook import WorkbookError
from workbook_forge.xlsx import import_xlsx

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "fixtures" / "corpus"
MERGED = CORPUS / "op_merged_cells.xlsx"
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
    assert envelope.guards is model._feature_guard
    assert set(described["guards"]) == {"data_validations", "pivots", "charts"}
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


def _importable_corpus() -> list[Path]:
    return sorted(CORPUS.glob("*.xlsx"))


@pytest.mark.parametrize("path", _importable_corpus(), ids=lambda path: path.name)
def test_envelope_describe_is_json_and_sorted(path):
    try:
        model = import_xlsx(path)
    except WorkbookError as error:
        pytest.skip(f"import_xlsx refuses this workbook: {type(error).__name__}")
    envelope = model.envelope
    described = envelope.describe()
    text = json.dumps(described, sort_keys=True, allow_nan=False)
    assert json.loads(text) == described
    assert str(path.parent) not in text
    assert described["text"]["cells"] == sorted(described["text"]["cells"])
    assert described["text"]["cells"] == sorted([sheet, address] for sheet, address in envelope.text.cells)
    written_names = {sheet["name"] for sheet in described["baseline"]["document"]["sheets"]}
    # Layouts are described under the sheet name as written; the in-memory
    # dict is keyed casefolded.
    assert set(described["text"]["layouts"]) <= written_names
    assert set(described["text"]["layouts"]) == {
        name for name in written_names if name.casefold() in envelope.text.layouts
    }
    assert described["source"]["parts"] == sorted(described["baseline"]["parts"])
    assert set(described["source"]["sheet_parts"]) == written_names
    assert set(described["source"]["sheet_parts"]) <= set(described["source"]["sheet_entries"])
    assert described["date_system"] in {"1900", "1904"}
    assert isinstance(described["compatibility_version"], int)
