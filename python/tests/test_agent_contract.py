"""Shared behavior contracts for independent agent workbook SDK adapters.

Python calls its SDK directly. Rust runs a compiled JSON-lines process without
PyO3. These contracts use actual engines, sessions, and XLSX adapters.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
from threading import Thread
import zipfile

import jsonschema
import pytest

from workbook_forge.toolkit import WorkbookModel, operating_scenario
from workbook_forge.xlsx import export_xlsx, import_xlsx

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "catalog/agent-operations.json"


@pytest.fixture(scope="module")
def rust_agent_binary():
    cargo = shutil.which("cargo")
    if cargo is None:
        pytest.skip("Rust toolchain unavailable; standalone agent contract was not run")
    target = ROOT / ".verification/agent-contract-target"
    result = subprocess.run(
        [cargo, "build", "--manifest-path", str(ROOT / "rust/Cargo.toml"), "--locked", "--offline", "--example", "agent_workbook"],
        env=dict(os.environ, CARGO_TARGET_DIR=str(target)), capture_output=True,
        text=True, timeout=180,
    )
    assert result.returncode == 0, result.stderr
    return target / "debug/examples" / ("agent_workbook.exe" if os.name == "nt" else "agent_workbook")


class _PythonClient:
    def __init__(self, model, output_dir):
        self.adapter = importlib.import_module("workbook_forge.agent").AgentWorkbook(model, output_dir=output_dir)

    def handle(self, request):
        return self.adapter.handle(request)

    def close(self):
        pass


class _RustClient:
    def __init__(self, binary, arguments):
        self.process = subprocess.Popen(
            [str(binary), *map(str, arguments)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        self.lines = Queue()
        self.reader = Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def handle(self, request):
        self.process.stdin.write(json.dumps(request, ensure_ascii=False, allow_nan=False) + "\n")
        self.process.stdin.flush()
        try:
            line = self.lines.get(timeout=15)
        except Empty as error:
            raise AssertionError("Rust agent did not return one JSON response") from error
        if line is None:
            raise AssertionError(f"Rust agent exited: {self.process.stderr.read()}")
        return json.loads(line)

    def close(self):
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=10)
        self.process.stdout.close()
        self.process.stderr.close()
        self.reader.join(timeout=1)


@pytest.fixture(params=["python", "rust"])
def agent_factory(request, tmp_path):
    clients = []
    counter = 0

    def create(document=None, *, source=None, bindings=None):
        nonlocal counter
        counter += 1
        directory = tmp_path / f"session-{counter}"
        directory.mkdir()
        output_dir = directory / "exports"
        if request.param == "python":
            if source is not None:
                model = import_xlsx(source, backend="python", **(bindings or {}))
            else:
                model = WorkbookModel(document=document, backend="python") if document is not None else operating_scenario(backend="python")
            client = _PythonClient(model, output_dir)
        else:
            binary = request.getfixturevalue("rust_agent_binary")
            if source is not None:
                bindings_path = directory / "bindings.json"
                bindings_path.write_text(json.dumps(bindings or {}))
                arguments = [source, "--bindings", bindings_path]
            elif document is not None:
                document_path = directory / "model.json"
                document_path.write_text(json.dumps(document))
                arguments = [document_path]
            else:
                arguments = ["--scenario"]
            client = _RustClient(binary, [*arguments, "--output-dir", output_dir])
        client.output_dir = output_dir
        clients.append(client)
        return client

    yield create
    for client in clients:
        client.close()


def _call(client, operation, arguments=None, *, request_id="contract-call"):
    response = client.handle({"id": request_id, "operation": operation, "arguments": arguments or {}})
    assert response["schema_version"] == 1
    assert response["id"] == request_id
    assert response["operation"] == operation
    assert type(response["revision"]) is int
    assert type(response["ok"]) is bool
    assert ("result" in response) != ("error" in response)
    assert len(json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode()) <= 256 * 1024
    catalog = json.loads(CATALOG_PATH.read_text())
    entry = next((item for item in catalog["operations"] if item["name"] == operation), None)
    if entry is not None:
        jsonschema.validate(response, entry["output_schema"])
    return response


def _ok(client, operation, arguments=None):
    response = _call(client, operation, arguments)
    assert response["ok"], response
    return response["result"]


def _error(client, operation, arguments=None, code=None):
    response = _call(client, operation, arguments)
    assert not response["ok"], response
    if code is not None:
        assert response["error"]["code"] == code, response
    return response


def _model(cells, outputs=None, inputs=None):
    return {
        "schema_version": 1,
        "sheets": [{"id": "data", "name": "Data", "cells": cells}],
        "outputs": {name: {"sheet": "Data", "address": address} for name, address in (outputs or {}).items()},
        "inputs": inputs or {},
    }


def _revision(client):
    return _call(client, "describe")["revision"]


def test_operation_catalog_is_valid_and_embedded_without_drift(agent_factory):
    expected = json.loads(CATALOG_PATH.read_text())
    assert expected == json.loads((ROOT / "rust/src/agent_operations.json").read_text())
    assert expected["schema_version"] == 1 and expected["profile"] == "agent-workbook-v1"
    names = [item["name"] for item in expected["operations"]]
    assert len(names) == len(set(names))
    assert set(names) == {"discover", "describe", "read", "calculate", "explain", "preview_inputs", "set_inputs", "edit", "export"}
    for operation in expected["operations"]:
        assert operation["description"] and operation["effect"]
        jsonschema.Draft202012Validator.check_schema(operation["input_schema"])
        jsonschema.Draft202012Validator.check_schema(operation["output_schema"])
    client = agent_factory()
    assert _ok(client, "discover") == expected
    assert _revision(client) == 0


def test_describe_exposes_bindings_without_dumping_cells(agent_factory):
    result = _ok(agent_factory(), "describe")
    assert [sheet["name"] for sheet in result["sheets"]] == ["Assumptions", "Forecast"]
    assert all(set(sheet) == {"id", "name", "populated_cells"} for sheet in result["sheets"])
    assert {"unit_price", "unit_cost", "fixed_cost"} == set(result["inputs"])
    assert {"revenue", "profit", "break_even_units"} == set(result["outputs"])
    assert result["source"]["kind"] == "authored"
    assert "cells" not in result and "dependencies" not in result


@pytest.mark.parametrize("envelope", [
    None, [], {}, {"operation": "describe", "extra": True},
    {"operation": 1}, {"operation": ""}, {"operation": "describe", "arguments": []},
    {"operation": "describe", "id": 7},
    {"operation": "describe", "id": "é" * 65},
])
def test_malformed_envelopes_leave_adapter_usable(agent_factory, envelope):
    client = agent_factory()
    response = client.handle(envelope)
    assert response["ok"] is False and response["error"]["code"] == "invalid_request"
    assert response["schema_version"] == 1 and response["revision"] == 0
    assert _revision(client) == 0


def test_request_errors_retain_valid_correlation_metadata(agent_factory):
    client = agent_factory()
    for invalid in (
        {"id": "correlation", "operation": "describe", "arguments": []},
        {"id": "correlation", "operation": "describe", "extra": 1},
    ):
        response = client.handle(invalid)
        assert response["id"] == "correlation" and response["operation"] == "describe"
        assert not response["ok"] and response["error"]["code"] == "invalid_request"
        assert response["revision"] == 0


def test_unknown_operations_and_arguments_are_explicit(agent_factory):
    client = agent_factory()
    _error(client, "run_shell", {}, "unknown_operation")
    _error(client, "describe", {"include_cells": True}, "invalid_request")
    _error(client, "calculate", {"outputs": ["profit"], "extra": True}, "invalid_request")
    _error(client, "set_inputs", {"values": {"unit_price": 25}, "expected_revision": 0, "extra": True}, "invalid_request")
    assert _revision(client) == 0


@pytest.mark.parametrize("operation,arguments", [
    ("read", {"sheet": "Forecast", "limit": True}),
    ("read", {"sheet": "Forecast", "limit": 0}),
    ("read", {"sheet": "Forecast", "limit": 101}),
    ("read", {"sheet": "Forecast", "offset": -1}),
    ("read", {"sheet": "Forecast", "offset": 1}),
    ("read", {"sheet": "Forecast", "offset": 1, "expected_revision": False}),
    ("calculate", {"workers": True}),
    ("calculate", {"workers": 0}),
    ("calculate", {"workers": 9}),
    ("calculate", {"outputs": []}),
    ("calculate", {"outputs": ["profit", "profit"]}),
    ("set_inputs", {"values": {"unit_price": 25}, "expected_revision": True}),
    ("set_inputs", {"values": {"unit_price": 25}, "expected_revision": 2**64}),
    ("edit", {"edits": [], "expected_revision": 0}),
])
def test_exact_integer_and_collection_boundaries(agent_factory, operation, arguments):
    client = agent_factory()
    _error(client, operation, arguments, "invalid_request")
    assert _revision(client) == 0


def test_preview_apply_calculate_explain_and_export_journey(agent_factory):
    client = agent_factory()
    before = _ok(client, "calculate")
    assert before["outputs"]["profit"] == 1440
    preview = _ok(client, "preview_inputs", {"values": {"unit_price": 25}, "expected_revision": 0})
    assert preview["applied"] is False
    assert preview["base_revision"] == 0 and preview["proposed_revision"] == 1
    assert preview["before"]["outputs"]["revenue"] == 7400
    assert preview["after"]["outputs"]["revenue"] == 9250
    assert _revision(client) == 0
    assert _ok(client, "calculate")["outputs"] == before["outputs"]
    receipt = _ok(client, "set_inputs", {"values": {"unit_price": 25}, "expected_revision": 0})
    assert receipt["previous_revision"] == 0 and receipt["revision"] == 1
    assert receipt["changed_cells"] == [{"sheet": "Assumptions", "address": "B1", "fields": ["value"]}]
    report = _ok(client, "calculate", {"outputs": ["revenue", "profit", "break_even_units"], "workers": 2})
    assert report["outputs"] == pytest.approx({"revenue": 9250, "profit": 3290, "break_even_units": 1000 / 17})
    assert report["diagnostics"] == [] and "values" not in report
    explained = _ok(client, "explain", {"output": "profit"})
    assert explained["value"] == 3290
    assert {("Assumptions", "B1"), ("Forecast", "F3")} <= {(cell["sheet"], cell["address"]) for cell in explained["cells"]}
    exported = _ok(client, "export", {"filename": "scenario.xlsx", "expected_revision": 1})
    saved = client.output_dir / exported["filename"]
    assert exported["bytes"] == saved.stat().st_size > 0 and exported["revision"] == 1
    bindings = operating_scenario().to_dict()
    loaded = import_xlsx(saved, inputs=bindings["inputs"], outputs=bindings["outputs"], backend="python")
    assert loaded.calculate()["outputs"] == report["outputs"]
    assert _revision(client) == 1


def test_paged_dependency_closure_has_natural_order_blanks_and_provenance(agent_factory):
    document = {
        "schema_version": 1,
        "sheets": [
            {"id": "z", "name": "Zeta", "cells": {"B2": {"formula": "=SUM(A1:A3)+Alpha!B1"}, "A3": {"value": 3}, "A1": {"value": 1}}},
            {"id": "a", "name": "Alpha", "cells": {"B1": {"value": 7}}},
        ], "outputs": {"total": {"sheet": "Zeta", "address": "B2"}},
    }
    client = agent_factory(document)
    first = _ok(client, "read", {"output": "total", "limit": 2})
    assert first["total_cells"] == 5 and first["truncated"] is True
    assert first["next_offset"] == 2
    second = _ok(client, "read", {"output": "total", "limit": 2, "offset": 2, "expected_revision": 0})
    third = _ok(client, "read", {"output": "total", "limit": 2, "offset": 4, "expected_revision": 0})
    combined = first["cells"] + second["cells"] + third["cells"]
    assert [(cell["sheet"], cell["address"]) for cell in combined] == [("Zeta", "A1"), ("Zeta", "A2"), ("Zeta", "B2"), ("Zeta", "A3"), ("Alpha", "B1")]
    assert combined[1]["content"] == {"value": None}
    assert third["next_offset"] is None and third["truncated"] is False
    sheet = _ok(client, "read", {"sheet": "Zeta", "range": "A1:A3"})
    assert [cell["address"] for cell in sheet["cells"]] == ["A1", "A3"]
    explained = _ok(client, "explain", {"output": "total"})
    assert explained["value"] == 11
    origins = {(cell["sheet"], cell["address"]): cell["value_origin"] for cell in explained["cells"]}
    assert origins[("Zeta", "A1")] == "authored"
    assert origins[("Zeta", "A2")] == "blank"
    assert origins[("Zeta", "B2")] == "calculated"
    _ok(client, "edit", {"edits": [{"sheet": "Zeta", "address": "A1", "value": 2}], "expected_revision": 0})
    _error(client, "read", {"output": "total", "offset": 2, "expected_revision": 0}, "revision_conflict")


def test_selected_calculation_and_explain_never_use_unsupported_caches(agent_factory):
    client = agent_factory(_model({"A1": {"value": 4}, "B1": {"formula": "=A1+1"}, "C1": {"formula": "=UNKNOWN_FUNCTION(1)", "cached_value": 999}}, {"safe": "B1", "unsafe": "C1"}))
    safe = _ok(client, "calculate", {"outputs": ["safe"]})
    assert safe["outputs"] == {"safe": 5} and safe["diagnostics"] == []
    unsupported = _ok(client, "explain", {"output": "unsafe"})
    assert unsupported["value"] is None and unsupported["diagnostic_count"] > 0
    cell = unsupported["cells"][0]
    assert cell["content"]["cached_value"] == 999
    assert cell["calculated_value"] is None and cell["value_origin"] == "unavailable"
    failed = _ok(client, "calculate", {"outputs": ["unsafe"]})
    assert failed["outputs"] == {} and failed["diagnostics"]
    _error(client, "export", {"filename": "unsupported.xlsx", "expected_revision": 0})
    assert not (client.output_dir / "unsupported.xlsx").exists()


def test_dependency_and_diagnostic_clipping_are_explicit(agent_factory):
    cells = {f"A{row}": {"value": row} for row in range(1, 106)}
    cells["B1"] = {"formula": "=SUM(A1:A105)"}
    client = agent_factory(_model(cells, {"total": "B1"}))
    page = _ok(client, "read", {"output": "total", "limit": 100})
    formula = next(cell for cell in page["cells"] if cell["address"] == "B1")
    assert formula["dependency_count"] == 105
    assert len(formula["dependencies"]) == 100 and formula["dependencies_truncated"] is True
    broken = {f"A{row}": {"formula": "=UNKNOWN_FUNCTION(1)"} for row in range(1, 121)}
    broken["B1"] = {"formula": "=SUM(A1:A120)"}
    report = _ok(agent_factory(_model(broken, {"total": "B1"})), "calculate")
    assert report["diagnostic_count"] > 100 and len(report["diagnostics"]) == 100
    assert report["diagnostics_truncated"] is True


def test_mutations_fail_atomically_and_stale_revisions_do_not_commit(agent_factory):
    client = agent_factory()
    original = _ok(client, "calculate")["outputs"]
    _error(client, "set_inputs", {"values": {"unit_price": 25, "unit_cost": -1}, "expected_revision": 0}, "invalid_input")
    _error(client, "edit", {"edits": [{"sheet": "Assumptions", "address": "B1", "value": 25}, {"sheet": "Missing", "address": "A1", "value": 1}], "expected_revision": 0})
    assert _revision(client) == 0 and _ok(client, "calculate")["outputs"] == original
    _ok(client, "set_inputs", {"values": {"unit_price": 25}, "expected_revision": 0})
    _error(client, "set_inputs", {"values": {"unit_price": 30}, "expected_revision": 0}, "revision_conflict")
    _error(client, "preview_inputs", {"values": {"unit_price": 30}, "expected_revision": 0}, "revision_conflict")
    assert _revision(client) == 1 and _ok(client, "calculate")["outputs"]["revenue"] == 9250


def test_large_read_fails_without_preventing_compact_mutation_receipts(agent_factory):
    cells = {f"A{row}": {"value": "x" * 32767} for row in range(1, 10)}
    client = agent_factory(_model(cells, {"first": "A1"}))
    _error(client, "read", {"sheet": "Data"}, "resource_limit")
    receipt = _ok(client, "edit", {"edits": [{"sheet": "Data", "address": "A1", "value": "y" * 32767}], "expected_revision": 0})
    assert receipt["revision"] == 1
    assert "y" * 100 not in json.dumps(receipt)
    assert _ok(client, "read", {"sheet": "Data", "range": "A1"})["cells"][0]["content"]["value"] == "y" * 32767


@pytest.mark.parametrize("filename", ["../escape.xlsx", "/tmp/escape.xlsx", "nested/file.xlsx", "nested\\file.xlsx", "file.xlsm", ".xlsx"])
def test_export_filename_scope_and_no_overwrite(agent_factory, filename):
    client = agent_factory()
    _error(client, "export", {"filename": filename, "expected_revision": 0}, "invalid_request")
    assert not client.output_dir.exists() or list(client.output_dir.iterdir()) == []
    assert _revision(client) == 0


def test_imported_export_preserves_opaque_parts_source_and_existing_targets(agent_factory, tmp_path):
    model = operating_scenario(backend="python")
    generated = export_xlsx(model, tmp_path / "generated.xlsx")
    with zipfile.ZipFile(generated) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts["custom/opaque.xml"] = b'<opaque xmlns="urn:forge:agent-test">keep &amp; exact bytes</opaque>'
    source = tmp_path / "source.xlsx"
    with zipfile.ZipFile(source, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    original_bytes = source.read_bytes()
    data = model.to_dict()
    bindings = {"inputs": data["inputs"], "outputs": data["outputs"]}
    client = agent_factory(source=source, bindings=bindings)
    assert _ok(client, "describe")["source"]["kind"] == "xlsx"
    _ok(client, "set_inputs", {"values": {"unit_price": 25}, "expected_revision": 0})
    receipt = _ok(client, "export", {"filename": "updated.xlsx", "expected_revision": 1})
    destination = client.output_dir / receipt["filename"]
    with zipfile.ZipFile(destination) as archive:
        assert archive.read("custom/opaque.xml") == parts["custom/opaque.xml"]
        assert archive.read("xl/styles.xml") == parts["xl/styles.xml"]
    assert source.read_bytes() == original_bytes
    reopened = import_xlsx(destination, backend="python", **bindings)
    assert reopened.calculate()["outputs"]["revenue"] == 9250
    saved_bytes = destination.read_bytes()
    _error(client, "export", {"filename": "updated.xlsx", "expected_revision": 1}, "export_error")
    assert destination.read_bytes() == saved_bytes


def test_calculation_requires_explicit_business_outputs(agent_factory):
    client = agent_factory(_model({"A1": {"formula": "=1+2"}}))
    _error(client, "calculate", {}, "invalid_input")
    assert _ok(client, "read", {"sheet": "Data"})["total_cells"] == 1


def test_discovery_edit_schema_rejects_empty_and_conflicting_content():
    catalog = json.loads(CATALOG_PATH.read_text())
    schema = next(item["input_schema"] for item in catalog["operations"] if item["name"] == "edit")
    for edit in ({"sheet": "Data", "address": "A1"}, {"sheet": "Data", "address": "A1", "value": 1, "formula": "=2"}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"edits": [edit], "expected_revision": 0}, schema)
    for content in ({"value": None}, {"formula": "=1+2"}, {"style": {"bold": True}}):
        jsonschema.validate({"edits": [{"sheet": "Data", "address": "A1", **content}], "expected_revision": 0}, schema)


def _runner_command(runner, request, output_dir, source=None):
    if runner == "rust":
        command = [str(request.getfixturevalue("rust_agent_binary"))]
    else:
        import sys
        command = [sys.executable, "-m", "workbook_forge.cli", "agent"]
    return [*command, str(source)] + ["--output-dir", str(output_dir)] if source else [*command, "--scenario", "--output-dir", str(output_dir)]


@pytest.mark.parametrize("runner", ["python", "rust"])
def test_jsonl_runners_recover_after_invalid_and_oversized_lines(runner, request, tmp_path):
    mutation = json.dumps({"operation": "set_inputs", "arguments": {"values": {"unit_price": 99}, "expected_revision": 0}}).encode()
    lines = [b"{", b"\xff", b'{"operation":"calculate","arguments":{"workers":NaN}}', mutation + b" " * (1024 * 1024), b"[" * 2000 + b"]" * 2000, b'{"id":"still-usable","operation":"calculate"}']
    process = subprocess.run(
        _runner_command(runner, request, tmp_path / "exports"),
        input=b"\n".join(lines) + b"\n", capture_output=True, timeout=30,
    )
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    assert len(responses) == len(lines)
    assert [item["error"]["code"] for item in responses[:-1]] == ["invalid_request", "invalid_request", "invalid_request", "resource_limit", "invalid_request"]
    assert responses[-1]["ok"] and responses[-1]["id"] == "still-usable"
    assert responses[-1]["revision"] == 0
    assert responses[-1]["result"]["outputs"]["revenue"] == 7400


@pytest.mark.parametrize("runner", ["python", "rust"])
def test_jsonl_serialization_respects_actual_response_byte_budget(runner, request, tmp_path):
    cells = {f"A{row}": {"value": "x" * 32767} for row in range(1, 8)}
    cells["A8"] = {"value": "x" * 31250}
    source = tmp_path / "large-model.json"
    source.write_text(json.dumps(_model(cells)))
    process = subprocess.run(
        _runner_command(runner, request, tmp_path / "exports", source),
        input=b'{"id":"cap","operation":"read","arguments":{"sheet":"Data"}}\n',
        capture_output=True, timeout=30,
    )
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    lines = process.stdout.splitlines()
    assert len(lines) == 1 and len(lines[0]) <= 256 * 1024
    response = json.loads(lines[0])
    assert response["ok"], response
    assert response["result"]["total_cells"] == 8


def test_imported_agent_profile_refuses_unexportable_edits_before_commit(agent_factory, tmp_path):
    model = operating_scenario()
    source = export_xlsx(model, tmp_path / "source.xlsx")
    document = model.to_dict()
    bindings = {"inputs": document["inputs"], "outputs": document["outputs"]}
    client = agent_factory(source=source, bindings=bindings)
    for unsupported in (
        {"sheet": "Assumptions", "address": "A1", "style": {"bold": True}},
        {"sheet": "Assumptions", "address": "A10", "value": 10},
    ):
        _error(client, "edit", {"edits": [
            {"sheet": "Assumptions", "address": "B1", "value": 25}, unsupported,
        ], "expected_revision": 0}, "unsupported_operation")
        assert _revision(client) == 0
        assert _ok(client, "calculate")["outputs"]["revenue"] == 7400


def test_preview_and_set_inputs_share_imported_cell_restrictions(agent_factory, tmp_path):
    model = operating_scenario()
    source = export_xlsx(model, tmp_path / "source.xlsx")
    document = model.to_dict()
    document["inputs"]["new_input"] = {"sheet": "Assumptions", "address": "A10", "kind": "number", "required": False}
    client = agent_factory(source=source, bindings={"inputs": document["inputs"], "outputs": document["outputs"]})
    for operation in ("preview_inputs", "set_inputs"):
        _error(client, operation, {"values": {"unit_price": 25, "new_input": 7}, "expected_revision": 0}, "unsupported_operation")
        assert _revision(client) == 0
        assert _ok(client, "calculate")["outputs"]["revenue"] == 7400
