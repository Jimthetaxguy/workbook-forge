"""Deterministic SDK replay of the agent workflow; this script is not an LLM.

Use a new output directory. The transcript records actual operation results and
the generated XLSX is reimported to check supported meaning.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from workbook_forge.agent import AgentWorkbook
from workbook_forge.toolkit import operating_scenario
from workbook_forge.xlsx import import_xlsx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--backend", choices=("python", "rust"), default="python")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    adapter = AgentWorkbook(operating_scenario(backend=args.backend), output_dir=args.output)
    transcript = []

    def call(operation, arguments=None):
        request = {"id": f"step-{len(transcript) + 1}", "operation": operation, "arguments": arguments or {}}
        response = adapter.handle(request)
        transcript.append({"request": request, "response": response})
        assert response["ok"], response
        return response["result"]

    catalog = call("discover")
    assert {item["name"] for item in catalog["operations"]} >= {"preview_inputs", "set_inputs", "explain", "export"}
    description = call("describe")
    assert "unit_price" in description["inputs"]
    context = call("read", {"output": "profit", "limit": 4})
    while context["next_offset"] is not None:
        context = call("read", {"output": "profit", "offset": context["next_offset"], "limit": 4, "expected_revision": 0})
    proposal = {"values": {"unit_price": 25}, "expected_revision": 0}
    preview = call("preview_inputs", proposal)
    assert not preview["applied"] and adapter.revision == 0
    call("set_inputs", proposal)
    report = call("calculate")
    assert not report["diagnostics"]
    assert report["outputs"]["revenue"] == 9250
    assert report["outputs"]["profit"] == 3290
    assert math.isclose(report["outputs"]["break_even_units"], 1000 / 17)
    explanation = call("explain", {"output": "profit", "expected_revision": 1})
    assert explanation["value"] == 3290
    call("export", {"filename": "agent-scenario.xlsx", "expected_revision": 1})
    imported = import_xlsx(args.output / "agent-scenario.xlsx", inputs=description["inputs"], outputs=description["outputs"], backend=args.backend)
    assert imported.calculate()["outputs"] == report["outputs"]
    receipt = {"kind": "deterministic-sdk-replay", "backend": args.backend, "status": "passed", "outputs": report["outputs"], "transcript": transcript}
    with (args.output / "transcript.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": "passed", "outputs": report["outputs"], "workbook": str(args.output / "agent-scenario.xlsx"), "transcript": str(args.output / "transcript.json")}))


if __name__ == "__main__":
    main()
