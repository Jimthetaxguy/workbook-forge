"""The generated-program compile and run budgets are configurable."""
import subprocess

import pytest

from workbook_forge import source_process
from workbook_forge.source_contract import SourceDiagnostic


def test_default_budgets(monkeypatch):
    monkeypatch.delenv(source_process.RUSTC_TIMEOUT_ENV, raising=False)
    monkeypatch.delenv(source_process.PROGRAM_TIMEOUT_ENV, raising=False)
    assert source_process.rustc_timeout_seconds() == 180.0
    assert source_process.program_timeout_seconds() == 60.0


def test_budgets_read_from_environment(monkeypatch):
    monkeypatch.setenv(source_process.RUSTC_TIMEOUT_ENV, "420")
    monkeypatch.setenv(source_process.PROGRAM_TIMEOUT_ENV, "2.5")
    assert source_process.rustc_timeout_seconds() == 420.0
    assert source_process.program_timeout_seconds() == 2.5
    monkeypatch.setenv(source_process.RUSTC_TIMEOUT_ENV, "  ")
    assert source_process.rustc_timeout_seconds() == 180.0


@pytest.mark.parametrize("raw", ["0", "-5", "abc", "nan", "inf"])
def test_invalid_budget_is_a_clear_error(monkeypatch, raw):
    monkeypatch.setenv(source_process.RUSTC_TIMEOUT_ENV, raw)
    with pytest.raises(ValueError, match=source_process.RUSTC_TIMEOUT_ENV):
        source_process.rustc_timeout_seconds()


def test_rustc_receives_configured_timeout_and_reports_resource_limit(monkeypatch, tmp_path):
    seen = []

    def fake_run(command, **kwargs):
        seen.append((command[0], kwargs["timeout"]))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setenv(source_process.RUSTC_TIMEOUT_ENV, "7")
    monkeypatch.setattr(source_process.subprocess, "run", fake_run)
    with pytest.raises(SourceDiagnostic) as raised:
        source_process.run_validated_source("fn main() {}", "rust", [], tmp_path/"artifact")
    assert raised.value.code == "resource_limit"
    assert seen == [("rustc", 7.0)]


def test_python_program_receives_configured_run_timeout(monkeypatch, tmp_path):
    seen = []

    def fake_run(command, **kwargs):
        seen.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setenv(source_process.PROGRAM_TIMEOUT_ENV, "3")
    monkeypatch.setattr(source_process.subprocess, "run", fake_run)
    with pytest.raises(SourceDiagnostic) as raised:
        source_process.run_validated_source("print(1)", "python", [], tmp_path/"artifact")
    assert raised.value.code == "resource_limit"
    assert seen == [3.0]
