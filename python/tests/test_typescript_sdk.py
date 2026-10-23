"""Optional TS client acceptance against real independent Python/Rust runners."""

import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from workbook_forge.xlsx import import_xlsx

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def ts_build():
    if not shutil.which("node") or not shutil.which("tsc"):
        pytest.skip("TypeScript/Node tools unavailable; TS SDK was not tested")
    subprocess.run(
        ["tsc", "-p", "typescript/tsconfig.json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=30,
    )
    subprocess.run(
        [
            "tsc",
            "--noEmit",
            "--strict",
            "--module",
            "nodenext",
            "--moduleResolution",
            "nodenext",
            "--target",
            "ES2022",
            "typescript/tests/typecheck.ts",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=30,
    )
    return shutil.which("node")


@pytest.fixture(scope="module")
def rust_sdk_binary():
    if not shutil.which("cargo"):
        pytest.skip("Rust toolchain unavailable; Rust adapter acceptance not run")
    target = ROOT / ".verification/agent-contract-target"
    subprocess.run(
        [
            "cargo",
            "build",
            "--manifest-path",
            str(ROOT / "rust/Cargo.toml"),
            "--locked",
            "--offline",
            "--example",
            "agent_workbook",
        ],
        cwd=ROOT,
        env={**os.environ, "CARGO_TARGET_DIR": str(target)},
        check=True,
        capture_output=True,
        timeout=180,
    )
    return target / "debug/examples/agent_workbook"


def fixture(directory):
    spec = importlib.util.spec_from_file_location(
        "ts_client_fixture", ROOT / "examples/ts_client_fixture.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.write_fixture(directory)


def test_generated_ts_constants_match_canonical_catalog():
    subprocess.run(
        [sys.executable, "tools/generate_ts_contract.py", "--check"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=10,
    )


def test_ts_client_unit_protocol_and_transport_checks(ts_build):
    subprocess.run(
        [ts_build, "--test", "typescript/tests/client.test.mjs"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=30,
    )


@pytest.mark.parametrize("backend", ["python", "rust"])
@pytest.mark.parametrize("source_kind", ["authored", "xlsx"])
def test_actual_ts_client_edit_recalculate_export_with_both_backends(
    ts_build, tmp_path, backend, source_kind, request
):
    model = fixture(tmp_path / "fixture")
    source = tmp_path / "fixture" / ("model.json" if source_kind == "authored" else "original.xlsx")
    original = source.read_bytes()
    if backend == "python":
        command = [sys.executable, "-m", "workbook_forge.cli", "agent"]
    else:
        command = [str(request.getfixturevalue("rust_sdk_binary"))]
    command += [str(source), "--output-dir", str(tmp_path / "exports")]
    if source_kind == "xlsx":
        command += ["--bindings", str(tmp_path / "fixture/bindings.json")]
    receipt = tmp_path / "receipt.json"
    result = subprocess.run(
        [ts_build, "typescript/examples/synthetic.mjs"],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "python"),
            "WORKBOOK_ENGINE_COMMAND": json.dumps(command),
            "WORKBOOK_SDK_RECEIPT": str(receipt),
        },
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(receipt.read_text())
    assert (
        data["revision"] == 3
        and data["outputs"]["total"] == 10
        and data["outputs"]["doubled"] == 30
    )
    assert data["outputs"]["error"]["error"] == "#DIV/0!"
    assert data["negative_zero_observed"] and data["source_kind"] == source_kind
    assert data["backend_execution"] == "real child process"
    assert source.read_bytes() == original
    restored = import_xlsx(
        tmp_path / "exports/sdk-example.xlsx", outputs=model.to_dict()["outputs"]
    )
    assert math.copysign(1, restored.to_dict()["sheets"][0]["cells"]["A6"]["value"]) == -1
    assert type(restored.to_dict()["sheets"][0]["cells"]["A4"]["value"]) in (int, float)
    assert any("-0.0" in line for line in data["encoded_requests"])
    report = restored.calculate()
    assert report["outputs"]["total"] == 10 and report["outputs"]["doubled"] == 30
    assert report["outputs"]["error"]["error"] == "#DIV/0!"


@pytest.mark.parametrize("backend", ["python", "rust"])
def test_ts_unavailable_formula_never_uses_imported_cache(ts_build, tmp_path, backend, request):
    model = fixture(tmp_path / "fixture")
    document = model.to_dict()
    document["sheets"][0]["cells"]["B1"]["formula"] = "=UNKNOWN_FUNCTION(1)"
    source = tmp_path / "unsupported.json"
    source.write_text(json.dumps(document))
    if backend == "python":
        command = [sys.executable, "-m", "workbook_forge.cli", "agent"]
    else:
        command = [str(request.getfixturevalue("rust_sdk_binary"))]
    command += [str(source), "--output-dir", str(tmp_path / "exports")]
    result = subprocess.run(
        [ts_build, "typescript/tests/unsupported.mjs"],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "python"),
            "WORKBOOK_ENGINE_COMMAND": json.dumps(command),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["unavailable_is_not_cached"]
    assert not (tmp_path / "exports/unsupported.xlsx").exists()
