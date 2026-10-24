"""Feed the canonical workbook fixture into the Excel round-trip harness.

``tools/excel_oracle.py`` owns the contract, full-rebuild script, reimport, and
formula-diff receipt. This module only exports
``fixtures/operating-scenario.workbook.json`` and calls that harness. It does
not mark Spec 2 passed, and it does not mark array-spill placement passed.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
from typing import Any

from workbook_forge.canonical_xlsx import compare_structure, export_canonical, import_canonical
from workbook_forge.model import calculate, content_fingerprint, hydrate, with_input

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = ROOT / "fixtures" / "operating-scenario.workbook.json"
SCENARIO_CONTRACT = ROOT / "fixtures" / "operating-scenario.excel-roundtrip.contract.json"
RED_FLAG_CONTRACT = ROOT / "fixtures" / "excel-roundtrip-red-flags.contract.json"
DESKTOP_COMMAND = (
    "python3 tools/canonical_excel_receipt.py "
    "--fixture fixtures/operating-scenario.workbook.json "
    "--output-dir receipts/canonical-operating-scenario-excel-new-run "
    "--excel"
)

_RECEIPT_ENTRIES = frozenset({
    "receipt.json",
    "receipt.md",
    "operating-scenario.exported.xlsx",
    "scenario-roundtrip.json",
    "red-flag-roundtrip.json",
    "scenario",
    "red-flags",
})


class CanonicalReceiptError(FileExistsError):
    """An existing path cannot be reused for canonical receipt output."""


def _is_repository_root_or_parent(path: Path) -> bool:
    root = ROOT.resolve()
    return path == root or root.is_relative_to(path)


def _existing_output_detail(path: Path) -> str:
    if path.is_symlink():
        return f"refusing an existing symbolic link ({path})"
    resolved = path.resolve()
    if _is_repository_root_or_parent(resolved):
        return f"refusing the repository root or a parent of it ({resolved})"
    if not path.is_dir():
        return f"existing path is not a canonical receipt directory ({path})"
    entries = list(path.iterdir())
    if not entries:
        return f"refusing an existing empty directory ({path})"
    unexpected = sorted(
        entry.name
        for entry in entries
        if entry.name not in _RECEIPT_ENTRIES or entry.is_symlink()
    )
    if unexpected:
        return f"refusing an existing directory containing {', '.join(unexpected)} ({path})"
    return f"refusing an existing canonical receipt directory ({path})"

def build_canonical_receipt(
    fixture: str | Path,
    output_dir: str | Path,
    *,
    run_excel: bool = False,
    rust: bool = True,
    timeout: float = 180,
) -> dict[str, Any]:
    """Export into a new directory, retaining all harness evidence."""
    requested = Path(output_dir)
    if requested.exists() or requested.is_symlink():
        detail = _existing_output_detail(requested)
        raise CanonicalReceiptError(
            f"{detail}; receipt output directory must be new, existing contents are never replaced"
        )
    fixture_path = Path(fixture).resolve()
    raw = fixture_path.read_bytes()
    original = hydrate(raw)
    directory = requested.resolve()
    # Exclusive creation also refuses a concurrent creator; no recursive cleanup.
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise CanonicalReceiptError(
            "receipt output directory must be new; another process created it first"
        ) from error
    oracle = _oracle()
    python_before = calculate(original)
    changed = with_input(original, "unit_price", 25)
    python_after = calculate(changed)
    engines = {
        "before_input_change": _engine_pair(python_before, raw, rust),
        "after_input_change_preview": _engine_pair(
            python_after, changed.to_json().encode("utf-8"), rust
        ),
    }
    exported = directory / "operating-scenario.exported.xlsx"
    export_canonical(original, exported)
    exported.chmod(0o644)
    structure = compare_structure(original, import_canonical(exported))
    scenario_contract = oracle.load_roundtrip_contract(SCENARIO_CONTRACT)
    red_flag_contract = oracle.load_roundtrip_contract(RED_FLAG_CONTRACT)
    scenario_root = directory / "scenario"
    spill_root = directory / "red-flags"
    scenario_receipt_path, scenario = oracle.run_roundtrip(
        exported, scenario_contract, scenario_root, run_excel=run_excel, timeout=timeout
    )
    spill_receipt_path, spill = oracle.run_roundtrip(
        exported, red_flag_contract, spill_root, run_excel=False, timeout=timeout
    )
    stable_scenario = directory / "scenario-roundtrip.json"
    stable_spill = directory / "red-flag-roundtrip.json"
    shutil.copyfile(scenario_receipt_path, stable_scenario)
    shutil.copyfile(spill_receipt_path, stable_spill)
    coverage = {item["class"]: item["status"] for item in spill.get("fixture_coverage", [])}
    spill_blocked = spill.get("status") == "blocked" and spill.get("blocker_kind") == "unsupported_capability"
    scenario_status = scenario.get("status")
    excel_pending = scenario_status in {"prepared", "blocked"}
    spec2_passed = scenario_status == "observed" and not spill_blocked
    receipt = {
        "schema_version": 1,
        "kind": "canonical-excel-receipt",
        "harness": "excel_export_roundtrip_v1",
        "export_lane": {
            "reader": "python:model.hydrate",
            "evaluator": "python:model.calculate",
            "writer": "python:canonical_xlsx.export_canonical",
            "rust_role": "independent calculation comparison only" if rust else "not used",
            "end_to_end_rust_only": False,
            "fixture_sha256": hashlib.sha256(raw).hexdigest(),
            "exported_sha256": hashlib.sha256(exported.read_bytes()).hexdigest(),
        },
        "artifact_retention": {"scenario": "scenario", "red_flags": "red-flags", "cleanup": "none"},
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "spec2_passed": spec2_passed,
        "spec2_note": (
            "Spec 2 needs Excel Desktop full rebuild plus every required red-flag class. "
            "A prepared package check is not that. array_spill stays blocked until spill "
            "placement exists, so this receipt does not pass Spec 2."
        ),
        "fixture": _display(fixture_path),
        "exported_xlsx": _display(exported),
        "input_change": {"name": "unit_price", "sheet": "Assumptions", "address": "B1", "before": 20, "after": 25},
        "scenario_contract": _display(SCENARIO_CONTRACT),
        "scenario_roundtrip": {
            "status": scenario.get("status"),
            "stage": scenario.get("stage"),
            "reason": scenario.get("reason"),
            "receipt": _display(stable_scenario),
            "formula_diffs": scenario.get("formula_diffs", []),
            "mismatches": scenario.get("mismatches", []),
            "excel_desktop": scenario_status if scenario_status in {"observed", "mismatch"} else "pending",
        },
        "array_spill": {
            "status": "blocked",
            "gate": "not_passed",
            "blocker_kind": "unsupported_capability",
            "capability": "worksheet_dynamic_array_spill_placement",
            "coverage": coverage.get("array_spill"),
            "receipt": _display(stable_spill),
            "reason": (
                "The red-flag contract names Red Flags!B7 =SEQUENCE(3) and spill cells "
                "B7:B9. The round-trip harness blocks that class before Excel opens "
                "because export refuses worksheet array spill caches. Scalar "
                "SUM(SEQUENCE(...)) does not count as spill placement."
            ),
        },
        "red_flag_roundtrip": {
            "status": spill.get("status"),
            "blocker_kind": spill.get("blocker_kind"),
            "preflight_skipped": spill.get("preflight_skipped"),
            "fixture_coverage": spill.get("fixture_coverage"),
            "receipt": _display(stable_spill),
        },
        "engines": {
            "before_input_change": {
                "python_rust_match": engines["before_input_change"]["match"],
                "outputs": _bound_outputs(python_before),
            },
            "after_input_change_preview": {
                "source": "python_and_rust_not_excel" if rust else "python_only_not_excel",
                "python_rust_match": engines["after_input_change_preview"]["match"],
                "outputs": _bound_outputs(python_after),
                "note": "Model calculation after unit_price=25. Not an Excel recalc.",
            },
        },
        "structural_reimport": {
            "match": structure["match"],
            "mismatches": structure["mismatches"],
            "formula_rewrites": structure["formula_rewrites"],
            "note": (
                "Canonical reimport of the exported package before Excel opens it. "
                "Excel formula rewrites, when Desktop runs, are scenario_roundtrip.formula_diffs."
            ),
        },
        "excel_desktop_pending": excel_pending or scenario_status not in {"observed", "mismatch"},
        "desktop_command": DESKTOP_COMMAND,
        "agent_headless": {
            "status": "not_started",
            "reason": "Headless agent composition waits until an Excel Desktop receipt exists.",
        },
    }
    (directory / "receipt.json").write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (directory / "receipt.md").write_text(_markdown(receipt), encoding="utf-8")
    return receipt


def _oracle():
    path = ROOT / "tools" / "excel_oracle.py"
    spec = importlib.util.spec_from_file_location("excel_oracle_harness", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _match_text(match: bool | None) -> str:
    return "not compared" if match is None else str(match).lower()


def _engine_pair(python_workbook, raw: bytes, rust: bool) -> dict[str, Any]:
    if not rust:
        # Not compared is not the same as agreed.
        return {"match": None}
    rust_workbook = _rust_calculate(raw)
    return {"match": _canonical(python_workbook.to_dict()) == _canonical(rust_workbook)}


def _rust_calculate(raw: bytes) -> dict[str, Any]:
    import os
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(prefix="canonical-rust-") as folder:
        path = Path(folder) / "workbook.json"
        path.write_bytes(raw)
        env = os.environ.copy()
        env["CARGO_TARGET_DIR"] = str(ROOT / "rust" / "target")
        completed = subprocess.run(
            [
                "cargo", "run", "--quiet", "--manifest-path", str(ROOT / "rust" / "Cargo.toml"),
                "--locked", "--example", "canonical_calc", "--", str(path), "calculate",
            ],
            cwd=ROOT, check=False, capture_output=True, text=True, env=env,
        )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "rust canonical_calc failed")
    return json.loads(completed.stdout)["workbook"]


def _canonical(value: Any) -> str:
    return json.dumps(_normalized(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _normalized(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer() and abs(value) <= 2**53:
            return int(value)
        return value
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalized(item) for key, item in value.items()}
    raise TypeError(type(value))


def _bound_outputs(workbook) -> dict[str, Any]:
    outputs = {}
    assert workbook.bindings is not None
    for name, cell_id in workbook.bindings.outputs.items():
        sheet = workbook.sheet(cell_id.sheet)
        assert sheet is not None
        cell = sheet.cells[cell_id.address]
        outputs[name] = {
            "sheet": cell_id.sheet,
            "address": cell_id.address,
            "result": None if cell.formula is None else cell.formula.result,
        }
    return outputs


def _display(path: Path) -> str:
    """Repo-relative path, or a content fingerprint when the file is outside the checkout."""
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return content_fingerprint(resolved.read_bytes())


def _markdown(receipt: dict[str, Any]) -> str:
    scenario = receipt["scenario_roundtrip"]
    spill = receipt["array_spill"]
    before = receipt["engines"]["before_input_change"]
    preview = receipt["engines"]["after_input_change_preview"]
    lines = [
        "# Canonical Excel receipt",
        "",
        "Fixture: `fixtures/operating-scenario.workbook.json`.",
        "Harness: `tools/excel_oracle.py` round-trip (`excel_export_roundtrip_v1`).",
        "The typed workbook model is the source of truth. This note is a scan of one run.",
        "",
        f"Spec 2 passed: `{str(receipt['spec2_passed']).lower()}`.",
        receipt["spec2_note"],
        "",
        "## Package",
        "",
        f"- Exported xlsx: `{receipt['exported_xlsx']}`",
        f"- Scenario contract: `{receipt['scenario_contract']}`",
        f"- Scenario harness receipt: `{scenario['receipt']}`",
        f"- Scenario status: `{scenario['status']}`",
        f"- Excel Desktop: `{scenario['excel_desktop']}`",
        "",
        "Input change declared in the contract: `unit_price` at `Assumptions!B1`, 20 → 25.",
        "",
        "Excel formula rewrites recorded by the harness on this run:",
        "",
    ]
    status = scenario.get("status")
    formula_diffs = scenario.get("formula_diffs", [])
    if formula_diffs:
        for item in formula_diffs:
            lines.append(f"- `{json.dumps(item, ensure_ascii=False)}`")
        if status == "mismatch":
            lines.append("- Excel ran; comparison status was `mismatch`.")
    elif status == "prepared":
        lines.append("- None. Excel was not run.")
    elif status == "mismatch":
        lines.append("- No formula rewrites were reported; Excel ran and the comparison status was `mismatch`.")
    elif status == "observed":
        lines.append("- None. Excel has not rewritten this package.")
    else:
        lines.append(f"- No completed Excel comparison is available (status `{status or 'unknown'}`).")
    structural = receipt["structural_reimport"]
    lines.append("")
    lines.append(
        f"Structural reimport before Excel: match `{str(structural['match']).lower()}`. "
        f"Formula rewrites: `{json.dumps(structural['formula_rewrites'])}`."
    )
    if scenario.get("reason"):
        lines.extend(["", scenario["reason"]])
    lines.extend(["", "## Engine values", ""])
    lines.append(f"Before the input change, Python/Rust match: `{_match_text(before['python_rust_match'])}`.")
    lines.append("")
    lines.append("| Output | Result |")
    lines.append("| --- | --- |")
    for name, item in before["outputs"].items():
        lines.append(f"| {name} | `{json.dumps(item['result'])}` |")
    lines.extend([
        "",
        f"After unit_price=25, engine preview only. Python/Rust match: `{_match_text(preview['python_rust_match'])}`. {preview['note']}",
        "",
        "| Output | Result |",
        "| --- | --- |",
    ])
    for name, item in preview["outputs"].items():
        lines.append(f"| {name} | `{json.dumps(item['result'])}` |")
    lines.extend([
        "",
        "## Array spill",
        "",
        f"Status: `{spill['status']}`. Gate: `{spill['gate']}`. Coverage: `{spill['coverage']}`.",
        spill["reason"],
        f"Red-flag harness receipt: `{spill['receipt']}`.",
        "",
        "## Desktop command",
        "",
        "```",
        receipt["desktop_command"],
        "```",
        "",
        receipt["agent_headless"]["reason"],
        "",
    ])
    return "\n".join(lines)
