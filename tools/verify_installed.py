"""Verify a wheel installation from outside the source checkout.

Run with the fresh environment's interpreter and a new output directory.
No test double, repository import path, or running Excel instance is required.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys

import workbook_forge
from workbook_forge import evaluate, evaluate_result
from workbook_forge.catalog import lookup_function, workbook_capabilities
from workbook_forge.expressions import CellReference, Expression
from workbook_forge.toolkit import operating_scenario
from workbook_forge.xlsx import export_xlsx, import_xlsx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--backend", choices=("python", "rust"), default="python")
    parser.add_argument("--require-pure", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    assert "site-packages" in Path(workbook_forge.__file__).parts, "must test the installed wheel"
    if args.require_pure:
        assert importlib.util.find_spec("workbook_forge._native") is None, "pure wheel must not contain a Rust extension"
    assert evaluate("=SUM(1,2,3)") == 6
    assert evaluate_result("=SEQUENCE(2,2)").shape == (2, 2)
    assert lookup_function("SUM")["name"] == "SUM"
    assert workbook_capabilities()["profile"] == "workbook-toolkit-v1"
    expression = Expression.reference(CellReference(1, 1, column_absolute=True))
    assert "$A2" in expression.copy(rows=1, columns=1).formula
    model = operating_scenario(backend=args.backend)
    model.set_inputs({"unit_price": 25})
    result = model.calculate(workers=2)
    assert not result["diagnostics"] and not result["stale"]
    assert result["outputs"]["revenue"] == 9250
    assert result["outputs"]["profit"] == 3290
    assert math.isclose(result["outputs"]["break_even_units"], 1000 / 17)
    output = export_xlsx(model, args.output / "scenario.xlsx", report=result)
    document = model.to_dict()
    imported = import_xlsx(output, inputs=document["inputs"], outputs=document["outputs"], backend=args.backend)
    assert imported.calculate()["outputs"] == result["outputs"]
    command = subprocess.run(
        [sys.executable, "-m", "workbook_forge.cli", "scenario", "--backend", args.backend, "--inputs", '{"unit_price":25}'],
        check=True, capture_output=True, text=True,
    )
    assert json.loads(command.stdout)["outputs"] == result["outputs"]
    receipt = {"status": "passed", "python": sys.version.split()[0], "backend": args.backend, "package": importlib.metadata.version("workbook_forge"), "outputs": result["outputs"], "checks": ["independent engine", "legacy APIs", "installed catalogs", "typed expressions", "CLI", "XLSX generation and reimport"]}
    (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
