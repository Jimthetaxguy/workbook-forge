"""Refusal messages never carry a machine path (AGENTS.md house rule).

Every writer refuses an existing destination twice: an advisory ``exists()``
check and the atomic ``os.link`` that fails when another writer won the race.
Both refusals, and the agent's ``export_error`` envelope around them, must be
free of the destination's path.
"""
import os
from pathlib import Path

import pytest

from workbook_forge.agent import AgentWorkbook
from workbook_forge.canonical_xlsx import export_canonical
from workbook_forge.model import hydrate
from workbook_forge.toolkit import operating_scenario
from workbook_forge.workbook import Workbook
from workbook_forge.xlsx import export_xlsx

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "fixtures" / "operating-scenario.workbook.json"
TOKEN = "private-home-7f3a9c"


@pytest.fixture
def private_dir(tmp_path):
    folder = tmp_path / TOKEN
    folder.mkdir()
    return folder


def lose_the_race(monkeypatch, target):
    """``target`` appears between the advisory check and the hard link.

    Only the final link to ``target`` is raced; the writers' own staging links
    inside their temporary directories are left alone.
    """
    real_link = os.link
    target = Path(target).resolve()

    def racing_link(src, dst, *args, **kwargs):
        if Path(dst).resolve() == target:
            target.write_bytes(b"other writer")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", racing_link)


def test_export_xlsx_refuses_an_existing_target_without_a_path(private_dir):
    target = private_dir / "out.xlsx"
    target.write_bytes(b"keep")
    with pytest.raises(FileExistsError) as caught:
        export_xlsx(operating_scenario(), target)
    assert TOKEN not in str(caught.value)
    assert target.read_bytes() == b"keep"


def test_export_xlsx_losing_the_race_reports_no_path(private_dir, monkeypatch):
    target = private_dir / "out.xlsx"
    lose_the_race(monkeypatch, target)
    with pytest.raises(FileExistsError) as caught:
        export_xlsx(operating_scenario(), target)
    assert TOKEN not in str(caught.value)
    assert target.read_bytes() == b"other writer"


def test_workbook_save_as_refuses_an_existing_target_without_a_path(private_dir):
    source = export_xlsx(operating_scenario(), private_dir / "source.xlsx")
    taken = private_dir / "taken.xlsx"
    taken.write_bytes(b"keep")
    with Workbook.open(source) as workbook:
        with pytest.raises(FileExistsError) as caught:
            workbook.save_as(taken)
    assert TOKEN not in str(caught.value)
    assert taken.read_bytes() == b"keep"


def test_workbook_save_as_losing_the_race_reports_no_path(private_dir, monkeypatch):
    source = export_xlsx(operating_scenario(), private_dir / "source.xlsx")
    lose_the_race(monkeypatch, private_dir / "copy.xlsx")
    with Workbook.open(source) as workbook:
        with pytest.raises(FileExistsError) as caught:
            workbook.save_as(private_dir / "copy.xlsx")
    assert TOKEN not in str(caught.value)
    assert (private_dir / "copy.xlsx").read_bytes() == b"other writer"


def test_export_canonical_refuses_an_existing_target_without_a_path(private_dir):
    target = private_dir / "canonical.xlsx"
    target.write_bytes(b"keep")
    with pytest.raises(FileExistsError) as caught:
        export_canonical(hydrate(FIXTURE.read_bytes()), target)
    assert TOKEN not in str(caught.value)
    assert target.read_bytes() == b"keep"


def test_export_canonical_losing_the_race_reports_no_path(private_dir, monkeypatch):
    lose_the_race(monkeypatch, private_dir / "canonical.xlsx")
    with pytest.raises(FileExistsError) as caught:
        export_canonical(hydrate(FIXTURE.read_bytes()), private_dir / "canonical.xlsx")
    assert TOKEN not in str(caught.value)
    assert (private_dir / "canonical.xlsx").read_bytes() == b"other writer"


def _export(agent, filename):
    response = agent.call("export", {"filename": filename, "expected_revision": 0}, request_id="test")
    assert response["ok"] is False, response
    assert response["error"]["code"] == "export_error", response
    return response["error"]["message"]


def test_agent_export_error_for_an_existing_target_carries_no_path(private_dir):
    agent = AgentWorkbook(operating_scenario(), output_dir=private_dir)
    (private_dir / "existing.xlsx").write_bytes(b"keep")
    assert TOKEN not in _export(agent, "existing.xlsx")


def test_agent_export_error_after_losing_the_race_carries_no_path(private_dir, monkeypatch):
    agent = AgentWorkbook(operating_scenario(), output_dir=private_dir)
    lose_the_race(monkeypatch, private_dir / "raced.xlsx")
    message = _export(agent, "raced.xlsx")
    assert TOKEN not in message
    assert "already exists" in message
    assert (private_dir / "raced.xlsx").read_bytes() == b"other writer"


def test_agent_export_error_for_an_os_error_carries_no_path(private_dir, monkeypatch):
    agent = AgentWorkbook(operating_scenario(), output_dir=private_dir)

    def denied(src, dst, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(dst))

    monkeypatch.setattr(os, "link", denied)
    message = _export(agent, "denied.xlsx")
    assert TOKEN not in message
    assert "Permission denied" in message
