"""Refusal messages never carry a machine path (AGENTS.md house rule).

Every writer refuses an existing destination twice: an advisory ``exists()``
check and the atomic ``os.link`` that fails when another writer won the race.
Both refusals, and the agent's ``export_error`` envelope around them, must be
free of the destination's path. Opening a workbook, a link that fails for
another reason, and the source CLI follow the same rule.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from workbook_forge.agent import AgentWorkbook
from workbook_forge.canonical_roundtrip import CanonicalReceiptError, build_canonical_receipt
from workbook_forge.canonical_xlsx import export_canonical
from workbook_forge.model import hydrate
from workbook_forge.toolkit import operating_scenario
from workbook_forge.workbook import Workbook, WorkbookError, link_new
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


def test_agent_export_error_with_an_empty_strerror_carries_no_path(private_dir, monkeypatch):
    agent = AgentWorkbook(operating_scenario(), output_dir=private_dir)

    def denied(src, dst, *args, **kwargs):
        raise OSError(13, "", str(dst))

    monkeypatch.setattr(os, "link", denied)
    assert TOKEN not in _export(agent, "denied.xlsx")


def test_workbook_open_of_a_missing_file_carries_no_path(private_dir):
    missing = private_dir / "nope.xlsx"
    with pytest.raises(WorkbookError) as caught:
        Workbook.open(missing)
    message = str(caught.value)
    assert TOKEN not in message
    assert message == "cannot open workbook: No such file or directory"


def test_workbook_open_of_a_non_zip_carries_no_path(private_dir):
    bogus = private_dir / "bogus.xlsx"
    bogus.write_bytes(b"this is not a zip")
    with pytest.raises(WorkbookError) as caught:
        Workbook.open(bogus)
    message = str(caught.value)
    assert TOKEN not in message
    assert bogus.name not in message


def test_workbook_open_keeps_the_bad_zip_sentence(private_dir, monkeypatch):
    import zipfile

    bogus = private_dir / "bogus.xlsx"
    bogus.write_bytes(b"this is not a zip")
    monkeypatch.setattr(
        "workbook_forge.workbook._preflight_archive_directory",
        lambda stream, size: 1,
    )

    def bad_zip(*args, **kwargs):
        raise zipfile.BadZipFile("File is not a zip file")

    monkeypatch.setattr("workbook_forge.workbook.zipfile.ZipFile", bad_zip)
    with pytest.raises(WorkbookError) as caught:
        Workbook.open(bogus)
    assert str(caught.value) == "cannot open workbook: File is not a zip file"
    assert TOKEN not in str(caught.value)


def test_workbook_open_os_error_during_zip_read_carries_no_path(private_dir, monkeypatch):
    bogus = private_dir / "bogus.xlsx"
    bogus.write_bytes(b"this is not a zip")
    monkeypatch.setattr(
        "workbook_forge.workbook._preflight_archive_directory",
        lambda stream, size: 1,
    )

    def denied(*args, **kwargs):
        raise OSError(13, "Permission denied", str(bogus))

    monkeypatch.setattr("workbook_forge.workbook.zipfile.ZipFile", denied)
    with pytest.raises(WorkbookError) as caught:
        Workbook.open(bogus)
    message = str(caught.value)
    assert message == "cannot open workbook: Permission denied"
    assert TOKEN not in message


def test_zip_preflight_os_error_carries_no_path(private_dir, monkeypatch):
    bogus = private_dir / "bogus.xlsx"
    bogus.write_bytes(b"this is not a zip")

    def denied(stream):
        raise OSError(13, "Permission denied", str(bogus))

    monkeypatch.setattr("workbook_forge.workbook.zipfile._EndRecData", denied)
    with pytest.raises(WorkbookError) as caught:
        Workbook.open(bogus)
    message = str(caught.value)
    assert message == "cannot inspect workbook ZIP directory: Permission denied"
    assert TOKEN not in message


def test_link_new_permission_error_carries_no_path(private_dir, monkeypatch):
    staged = private_dir / "staged.xlsx"
    staged.write_bytes(b"staged")
    target = private_dir / "out.xlsx"

    def denied(src, dst, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(dst))

    monkeypatch.setattr(os, "link", denied)
    with pytest.raises(PermissionError) as caught:
        link_new(staged, target)
    message = str(caught.value)
    assert TOKEN not in message
    assert "Permission denied" in message
    assert caught.value.filename is None


def test_link_new_os_error_with_empty_strerror_carries_no_path(private_dir, monkeypatch):
    staged = private_dir / "staged.xlsx"
    staged.write_bytes(b"staged")
    target = private_dir / "out.xlsx"

    def denied(src, dst, *args, **kwargs):
        raise OSError(13, "", str(dst))

    monkeypatch.setattr(os, "link", denied)
    with pytest.raises(OSError) as caught:
        link_new(staged, target)
    assert TOKEN not in str(caught.value)
    assert caught.value.filename is None


def test_canonical_receipt_refusal_carries_no_path(private_dir):
    empty = private_dir / "receipt"
    empty.mkdir()
    with pytest.raises(CanonicalReceiptError) as caught:
        build_canonical_receipt(FIXTURE, empty, rust=False)
    message = str(caught.value)
    assert TOKEN not in message
    assert str(empty.resolve()) not in message
    assert "empty directory" in message


def test_source_cli_process_and_os_errors_use_fixed_sentences(tmp_path, monkeypatch, capsys):
    from workbook_forge import source_cli

    token_dir = tmp_path / TOKEN
    token_dir.mkdir()
    source = token_dir / "in.xlsx"
    bundle = token_dir / "bundle"
    compiler = str(token_dir / "compiler")
    cases = [
        (
            OSError(13, "Permission denied", compiler),
            "missing_toolchain",
            "Rust compiler could not be launched",
        ),
        (
            subprocess.CalledProcessError(
                1,
                [compiler, "emit-fresh", str(token_dir / "model.json"), str(token_dir / "candidate.xlsx")],
            ),
            "compilation_failed",
            "generated Rust did not compile",
        ),
        (
            subprocess.TimeoutExpired([compiler, str(token_dir / "program.rs")], 60),
            "resource_limit",
            "Rust compilation exceeded its timeout",
        ),
        (
            FileExistsError(17, "File exists", str(token_dir / "out.xlsx")),
            "artifact_exists",
            "binary artifact path must be new",
        ),
    ]
    for error, code, message in cases:
        def fail(*args, error=error, **kwargs):
            raise error

        monkeypatch.setattr(source_cli.source_graph, "compile_workbook", fail)
        assert source_cli.main(
            ["compile", str(source), str(bundle), "--language", "python", "--profile", "graph-v6"]
        ) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload == {"ok": False, "code": code, "message": message}
        assert TOKEN not in json.dumps(payload)
