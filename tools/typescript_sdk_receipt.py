"""Replay the optional TypeScript client against both real existing backend runners."""

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys

from workbook_forge.xlsx import import_xlsx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--rust-binary", type=Path, required=True)
    args = parser.parse_args()
    binary = args.rust_binary.resolve(strict=True)
    args.directory.mkdir(parents=True, exist_ok=False)
    directory = args.directory.resolve()
    subprocess.run(["tsc", "-p", "typescript/tsconfig.json"], cwd=ROOT, check=True, timeout=30)
    spec = importlib.util.spec_from_file_location(
        "ts_client_fixture", ROOT / "examples/ts_client_fixture.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    model = module.write_fixture(directory / "fixture")
    lanes = []
    for backend in ("python", "rust"):
        for kind in ("authored", "xlsx"):
            lane = directory / (backend + "-" + kind)
            lane.mkdir()
            source = (
                directory / "fixture" / ("model.json" if kind == "authored" else "original.xlsx")
            )
            initial = source.read_bytes()
            command = (
                [sys.executable, "-m", "workbook_forge.cli", "agent"]
                if backend == "python"
                else [str(binary)]
            )
            command += [str(source), "--output-dir", str(lane / "exports")]
            if kind == "xlsx":
                command += ["--bindings", str(directory / "fixture/bindings.json")]
            result = subprocess.run(
                ["node", "typescript/examples/synthetic.mjs"],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PYTHONPATH": str(ROOT / "python"),
                    "WORKBOOK_ENGINE_COMMAND": json.dumps(command),
                    "WORKBOOK_SDK_RECEIPT": str(lane / "client.json"),
                },
                capture_output=True,
                text=True,
                timeout=90,
            )
            (lane / "stdout.log").write_text(result.stdout)
            (lane / "stderr.log").write_text(result.stderr)
            if result.returncode:
                raise RuntimeError(f"{backend}/{kind} failed: {result.stderr}")
            restored = import_xlsx(
                lane / "exports/sdk-example.xlsx", outputs=model.to_dict()["outputs"]
            )
            assert math.copysign(1, restored.to_dict()["sheets"][0]["cells"]["A6"]["value"]) == -1
            assert type(restored.to_dict()["sheets"][0]["cells"]["A4"]["value"]) in (int, float)
            actual = restored.calculate()["outputs"]
            assert (
                actual["total"] == 10
                and actual["doubled"] == 30
                and actual["error"]["error"] == "#DIV/0!"
            )
            assert source.read_bytes() == initial
            lanes.append(
                {
                    "backend": backend,
                    "source_kind": kind,
                    "actual_calculation": "Python SDK"
                    if backend == "python"
                    else "Rust process ran; values validated by Python SDK reimport",
                    "reader_and_writer": backend,
                    "client": "compiled TypeScript",
                    "transport": "Node child process JSON-lines",
                    "request_receipt": str((lane / "client.json").relative_to(directory)),
                    "output_sha256": hashlib.sha256(
                        (lane / "exports/sdk-example.xlsx").read_bytes()
                    ).hexdigest(),
                    "reimport_validation": "Python SDK",
                    "passed": True,
                }
            )
    summary = {
        "profile": "agent-workbook-v1",
        "lanes": lanes,
        "rust_binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "excel_observed": False,
        "tauri_fastapi_websocket": "not implemented or exercised",
        "installed_ts_package": False,
    }
    (directory / "receipt.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
