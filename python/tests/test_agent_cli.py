"""Real JSON-lines process boundary checks, including recovery after bad input."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from workbook_forge.toolkit import operating_scenario
from workbook_forge.xlsx import export_xlsx, import_xlsx


def _request(operation, arguments=None, request_id="test"):
    return json.dumps({"id": request_id, "operation": operation, "arguments": arguments or {}}).encode() + b"\n"


def _run(tmp_path, lines, *source_args):
    command = [sys.executable, "-m", "workbook_forge.cli", "agent", *(source_args or ("--scenario",)), "--output-dir", str(tmp_path / "exports")]
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    result = subprocess.run(command, input=lines, capture_output=True, env=env, timeout=30)
    assert result.returncode == 0, result.stderr.decode()
    assert result.stderr == b""
    return [json.loads(line) for line in result.stdout.splitlines()]


@pytest.mark.parametrize("bad", [b"not json\n", b'[]\n', b'{"operation":"describe","arguments":{"x":NaN}}\n', b'\xff\n'])
def test_invalid_line_returns_error_and_following_request_still_runs(tmp_path, bad):
    error, good = _run(tmp_path, bad + _request("describe"))
    assert error["ok"] is False
    assert error["error"]["code"] == "invalid_request"
    assert good["ok"] and good["revision"] == 0
    assert "unit_price" in good["result"]["inputs"]


def test_oversized_line_is_one_error_and_never_becomes_partial_operations(tmp_path):
    attack = b" " * (1024 * 1024 + 20) + _request("set_inputs", {"values": {"unit_price": 99}, "expected_revision": 0})
    error, good = _run(tmp_path, attack + _request("calculate"))
    assert error["error"]["code"] == "resource_limit"
    assert good["revision"] == 0 and good["result"]["outputs"]["revenue"] == 7400


def test_raw_request_byte_limit_counts_whitespace_and_excludes_linefeed(tmp_path):
    request = _request("describe").removesuffix(b"\n")
    boundary = b" " * (1024 * 1024 - len(request)) + request
    good, too_large, recovered = _run(tmp_path, boundary + b"\n" + boundary + b" \n" + _request("describe"))
    assert good["ok"] and recovered["ok"]
    assert too_large["error"]["code"] == "resource_limit"


def test_source_bindings_preview_edit_export_and_reimport(tmp_path):
    model = operating_scenario()
    source = export_xlsx(model, tmp_path / "source.xlsx")
    original = source.read_bytes()
    document = model.to_dict()
    bindings = tmp_path / "bindings.json"
    bindings.write_text(json.dumps({name: document[name] for name in ("inputs", "outputs")}))
    requests = b"".join([
        _request("describe"),
        _request("preview_inputs", {"values": {"unit_price": 25}, "expected_revision": 0}),
        _request("set_inputs", {"values": {"unit_price": 25}, "expected_revision": 0}),
        _request("export", {"filename": "updated.xlsx", "expected_revision": 1}),
        _request("export", {"filename": "updated.xlsx", "expected_revision": 1}),
    ])
    described, preview, edit, exported, repeat = _run(tmp_path, requests, str(source), "--bindings", str(bindings))
    assert described["result"]["source"]["kind"] == "xlsx"
    assert preview["revision"] == 0 and preview["result"]["applied"] is False
    assert preview["result"]["after"]["outputs"]["profit"] == 3290
    assert edit["revision"] == exported["revision"] == 1
    assert exported["ok"] and not repeat["ok"]
    restored = import_xlsx(tmp_path / "exports/updated.xlsx", inputs=document["inputs"], outputs=document["outputs"])
    assert restored.calculate()["outputs"]["revenue"] == 9250
    assert source.read_bytes() == original
