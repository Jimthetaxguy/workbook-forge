#!/usr/bin/env python3
"""Export the canonical operating scenario and record an Excel receipt.

The round-trip harness is ``tools/excel_oracle.py``. This command only exports
``fixtures/operating-scenario.workbook.json`` and calls that harness.

Without ``--excel`` the package is checked and Excel Desktop stays pending.
``--excel`` is the Mac command that opens Excel, edits the declared input,
runs a full rebuild, saves, and reimports. A prepared receipt does not pass
Spec 2. Array-spill placement stays blocked.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from workbook_forge.canonical_roundtrip import DEFAULT_FIXTURE, build_canonical_receipt  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--set",
        dest="assignment",
        default="unit_price=25",
        help="declared input assignment; the scenario contract owns unit_price=25",
    )
    parser.add_argument(
        "--excel",
        action="store_true",
        help="open the export in Excel Desktop, edit, full-recalc, save, and reimport",
    )
    parser.add_argument(
        "--no-rust",
        action="store_true",
        help="skip the Rust calculation check",
    )
    args = parser.parse_args(argv)
    if args.assignment != "unit_price=25":
        parser.error("the canonical scenario contract declares unit_price=25")
    receipt = build_canonical_receipt(
        args.fixture,
        args.output_dir,
        run_excel=args.excel,
        rust=not args.no_rust,
    )
    print(args.output_dir / "receipt.json")
    engines = receipt["engines"]
    if not engines["before_input_change"]["python_rust_match"]:
        return 1
    if not engines["after_input_change_preview"]["python_rust_match"]:
        return 1
    if not receipt["structural_reimport"]["match"]:
        return 1
    scenario_status = receipt["scenario_roundtrip"]["status"]
    if receipt["red_flag_roundtrip"]["status"] != "blocked":
        return 1
    if args.excel and scenario_status != "observed":
        return 1
    if scenario_status not in {"prepared", "observed"}:
        return 1
    summary = {
        "status": scenario_status,
        "spec2_passed": receipt["spec2_passed"],
        "array_spill": receipt["array_spill"]["status"],
        "receipt": str(args.output_dir / "receipt.json"),
    }
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
