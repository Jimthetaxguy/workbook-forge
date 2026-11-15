"""The document revision rule, on both backends (spec 09 step 1).

The revision is session state. A v1 toolkit document may still carry a root
``revision`` key: every reader checks it, no writer emits it, an engine session
built from document text starts at it, and the calculation session starts at 0
and reports the key as dropped. The accept/refuse table is the ``revision_key``
block of ``fixtures/session-input-change.v1.json``; revision numbers asserted
here follow from the rule (start, plus one per committed batch).
"""
from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path

import pytest

from workbook_forge.agent import AgentWorkbook
from workbook_forge.convert import to_canonical, to_toolkit
from workbook_forge.model import hydrate
from workbook_forge.python_engine import ToolkitError
from workbook_forge.session import CalculationSession
from workbook_forge.toolkit import WorkbookModel, operating_scenario
from workbook_forge.xlsx import import_xlsx

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "fixtures" / "session-input-change.v1.json").read_text(encoding="utf-8"))
KEY = FIXTURE["revision_key"]
LEGACY = FIXTURE["documents"]["legacy_revision"]["document"]
ENGINE_MODULES = {"python": "workbook_forge.python_engine", "rust": "workbook_forge._native"}
EDIT = json.dumps([{"sheet": "S", "address": "A1", "value": 2}])


@pytest.fixture(params=["python", "rust"])
def backend(request):
    if request.param == "rust" and importlib.util.find_spec(ENGINE_MODULES["rust"]) is None:
        pytest.skip("optional Rust extension is not installed")
    return request.param


def _engine(backend: str):
    return importlib.import_module(ENGINE_MODULES[backend])


def _spliced(spelling: str) -> str:
    return KEY["template"].replace("@", spelling)


def _text_readers(backend: str):
    """The readers that take document text as it was written."""
    engine = _engine(backend)
    return {
        "Session": lambda text: engine.Session(text),
        "calculate": lambda text: engine.calculate(text),
        "inspect": lambda text: engine.inspect(text),
        "CalculationSession.open": lambda text: CalculationSession.open(text, backend=backend),
    }


def test_revision_key_table_holds_for_every_reader(backend):
    engine = _engine(backend)
    for spelling in [*KEY["accept"], None]:
        text = KEY["absent"] if spelling is None else _spliced(spelling)
        # An engine session and the stateless calls keep a v1 document's
        # number; without the key they are at 0.
        carried = 0 if spelling is None else int(spelling)
        assert engine.Session(text).revision == carried
        assert json.loads(engine.calculate(text))["revision"] == carried
        assert json.loads(engine.inspect(text))["revision"] == carried
        assert WorkbookModel(document=json.loads(text), backend=backend).revision == carried
        # The calculation session never uses it.
        session = CalculationSession.open(text, backend=backend)
        assert session.revision == KEY["opens_at"] == 0
        if spelling is None:
            assert session.conversion is None
        else:
            assert session.conversion.dropped == ("revision",)
        workbook, conversion = to_canonical(json.loads(text))
        assert "revision" not in conversion.dropped
    for spelling in KEY["refuse"]:
        text = _spliced(spelling)
        readers = _text_readers(backend)
        readers["WorkbookModel"] = lambda text: WorkbookModel(document=json.loads(text), backend=backend)
        readers["to_canonical"] = lambda text: to_canonical(json.loads(text))
        for name, reader in readers.items():
            with pytest.raises(ValueError) as raised:
                reader(text)
                pytest.fail(f"{name} accepted revision {spelling}")
            if name == "CalculationSession.open":
                # The session contract gives the refusal one code on both
                # backends.
                assert isinstance(raised.value, ToolkitError) and raised.value.code == "invalid_model", spelling


def test_revision_key_reader_differences_are_the_listed_ones(backend):
    # Two spellings the JSON readers themselves treat differently; neither is
    # revision logic. Only readers handed the raw text can tell them apart.
    assert [case["name"] for case in KEY["reader_differences"]] == ["negative-zero", "duplicate-key"]
    for case in KEY["reader_differences"]:
        for name, reader in _text_readers(backend).items():
            if case[backend] == "accept":
                reader(case["document"])
            else:
                assert case[backend] == "refuse"
                with pytest.raises(ValueError):
                    reader(case["document"])
                    pytest.fail(f"{name} accepted {case['name']}")


def test_no_writer_emits_a_revision_key(backend, tmp_path):
    engine = _engine(backend)
    session = engine.Session(_spliced("4"))
    assert "revision" not in json.loads(session.snapshot())
    assert session.apply(EDIT, 4) == 5
    assert "revision" not in json.loads(session.snapshot())
    assert "revision" not in json.loads(session.snapshot_with_revision()[0])
    assert "revision" not in json.loads(engine.scenario())
    assert "revision" not in WorkbookModel(backend=backend).to_dict()
    scenario = operating_scenario(backend=backend)
    assert scenario.set_inputs({"unit_price": 25}) == 1
    assert "revision" not in scenario.to_dict()
    assert "revision" not in scenario.snapshot_with_revision()[0]
    canonical = FIXTURE["documents"]["canonical"]
    data = (ROOT / "fixtures" / canonical["document_fixture"]).read_bytes()
    assert "revision" not in to_toolkit(hydrate(data), input_kinds=canonical["input_kinds"])[0]
    imported = import_xlsx(ROOT / "fixtures" / "corpus" / "op_merged_cells.xlsx", backend=backend)
    assert "revision" not in imported.to_dict()
    assert "revision" not in imported.envelope.baseline.document
    assert "revision" not in imported.envelope.describe()["baseline"]["document"]
    assert "revision" not in CalculationSession(LEGACY, backend=backend).snapshot()
    opened = CalculationSession.open(json.dumps(LEGACY), backend=backend)
    assert opened.set_inputs({"a": 10}, 0) == 1
    assert "revision" not in opened.snapshot()


def test_engine_session_seed_paired_read_and_fork(backend):
    engine = _engine(backend)
    # A legacy key seeds an engine session built from document text.
    legacy = engine.Session(json.dumps(LEGACY))
    assert LEGACY["revision"] == 7 and legacy.revision == 7
    assert "revision" not in json.loads(legacy.snapshot())
    # An explicit seed wins over the key, and is the seed without one.
    assert engine.Session(json.dumps(LEGACY), revision=2).revision == 2
    session = engine.Session(KEY["absent"], revision=5)
    assert session.revision == 5
    assert session.apply(EDIT, 5) == 6
    text, revision = session.snapshot_with_revision()
    assert revision == 6 and "revision" not in json.loads(text)
    fork = session.fork()
    assert fork.revision == 6 and fork.snapshot() == text
    assert fork.apply(json.dumps([{"sheet": "S", "address": "A1", "value": 3}]), 6) == 7
    assert session.revision == 6 and session.snapshot() == text
    assert json.loads(fork.snapshot())["sheets"][0]["cells"]["A1"]["value"] == 3


def test_engine_session_refuses_a_seed_that_is_not_an_unsigned_integer(backend):
    # The seed argument follows the rule for the key: an integer from 0 to
    # 2**64 - 1, and a boolean is not an integer.
    engine = _engine(backend)
    for seed in (True, False, -1, 2**64, 1.0, "3"):
        with pytest.raises(ValueError) as raised:
            engine.Session(KEY["absent"], revision=seed)
        assert str(raised.value).startswith("invalid_request: "), repr(seed)
        with pytest.raises(ValueError) as raised:
            WorkbookModel(document=json.loads(KEY["absent"]), backend=backend, _revision=seed)
        assert str(raised.value).startswith("invalid_request: "), repr(seed)
    assert engine.Session(KEY["absent"], revision=2**64 - 1).revision == 2**64 - 1


def _result(response: dict) -> dict:
    assert response["ok"] is True, response
    return response["result"]


def test_agent_keeps_its_revisions_at_a_base_above_zero(backend, tmp_path):
    agent = AgentWorkbook(operating_scenario(backend=backend), output_dir=tmp_path)
    first = agent.handle({"operation": "set_inputs", "arguments": {"values": {"unit_price": 25}, "expected_revision": 0}})
    assert first["revision"] == 1 and _result(first)["revision"] == 1
    second = agent.handle({"operation": "set_inputs", "arguments": {"values": {"unit_cost": 10}, "expected_revision": 1}})
    assert second["revision"] == 2 and _result(second) | {"changed_cells": None} == {"previous_revision": 1, "revision": 2, "changed_cells": None}
    preview = agent.handle({"operation": "preview_inputs", "arguments": {"values": {"unit_price": 30}, "expected_revision": 2}})
    result = _result(preview)
    assert preview["revision"] == 2
    assert (result["base_revision"], result["proposed_revision"]) == (2, 3)
    assert (result["before"]["revision"], result["after"]["revision"]) == (2, 3)
    calculated = agent.handle({"operation": "calculate"})
    assert calculated["revision"] == 2 and _result(calculated)["revision"] == 2
    assert _result(calculated)["stale"] is False
    exported = agent.handle({"operation": "export", "arguments": {"filename": "out.xlsx", "expected_revision": 2}})
    assert exported["revision"] == 2 and _result(exported)["revision"] == 2
    described = agent.handle({"operation": "describe"})
    assert described["revision"] == 2 and _result(described)
    assert agent.revision == 2


def test_agent_answers_a_legacy_document_at_the_revision_it_carried(backend, tmp_path):
    # A v1 document that answered wire revision 5 before the key left the
    # document still does, and commits from there.
    document = json.loads(_engine(backend).scenario())
    document["revision"] = 5
    model = WorkbookModel(document=document, backend=backend)
    assert model.revision == 5 and "revision" not in model.to_dict()
    agent = AgentWorkbook(model, output_dir=tmp_path)
    assert agent.handle({"operation": "describe"})["revision"] == 5
    stale = agent.handle({"operation": "set_inputs", "arguments": {"values": {"unit_price": 25}, "expected_revision": 0}})
    assert stale["ok"] is False and stale["error"]["code"] == "revision_conflict" and stale["revision"] == 5
    committed = agent.handle({"operation": "set_inputs", "arguments": {"values": {"unit_price": 25}, "expected_revision": 5}})
    assert committed["revision"] == 6
    assert (_result(committed)["previous_revision"], _result(committed)["revision"]) == (5, 6)
    assert _result(agent.handle({"operation": "calculate"}))["revision"] == 6


def test_workbook_model_inspect_reports_the_live_revision(backend):
    model = operating_scenario(backend=backend)
    assert model.inspect()["revision"] == model.revision == 0
    assert model.set_inputs({"unit_price": 25}) == 1
    assert model.inspect()["revision"] == model.revision == 1
