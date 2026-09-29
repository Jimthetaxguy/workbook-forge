#!/usr/bin/env python3
"""tools/jev_critic.py — TypeSafe Jev System One critic for Workbook Forge.

Zero third-party runtime dependencies (Python standard library only).
Evaluates formula behavior profiles, intake layout topology, and autoresearch scope.

Usage:
  ./tools/with-typesafe-key.sh python3 tools/jev_critic.py audit-profile \
      --formula "SORT" --fixture '{"formula": "=SORT(A1:B3)", "result": {"error": "#VALUE!"}}'
  ./tools/with-typesafe-key.sh python3 tools/jev_critic.py classify-layout \
      --window-json '[[{"val":"Date","bold":true},{"val":"Amount","bold":true}],[{"val":"2025-01-01"},{"val":100}]]'
  ./tools/with-typesafe-key.sh python3 tools/jev_critic.py eval-benchmark \
      --benchmark-file catalog/behavior_critic_benchmarks.json --gate-threshold 0.15
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "jev-latest"
DEFAULT_URL = "https://api.typesafe.ai/v1/systemone"


def resolve_api_key() -> str:
    """Resolve TYPESAFE_API_KEY from the environment, a .env file, or a command.

    Nothing about one machine is written here. TYPESAFE_ENV_FILE names a
    second .env file. TYPESAFE_KEY_COMMAND is a shell command that prints
    the key, for whatever secret store holds it.
    """
    key = os.environ.get("TYPESAFE_API_KEY")
    if key and key.strip():
        return key.strip()

    candidates = [ROOT / ".env"]
    if os.environ.get("TYPESAFE_ENV_FILE"):
        candidates.append(Path(os.environ["TYPESAFE_ENV_FILE"]))
    for candidate in candidates:
        if candidate.is_file():
            with open(candidate, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("TYPESAFE_API_KEY="):
                        k = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if k:
                            return k

    command = os.environ.get("TYPESAFE_KEY_COMMAND")
    if command:
        try:
            out = subprocess.check_output(command, shell=True, stderr=subprocess.DEVNULL, timeout=5)
        except (subprocess.SubprocessError, OSError) as error:
            # The command and its output may hold the key, so neither is shown.
            raise RuntimeError(f"TYPESAFE_KEY_COMMAND failed: {type(error).__name__}") from None
        k = out.decode("utf-8").strip()
        if k:
            return k

    raise RuntimeError(
        "TYPESAFE_API_KEY not found. Set TYPESAFE_API_KEY or run via tools/with-typesafe-key.sh"
    )


class JevClient:
    """Client for TypeSafe Jev System One REST API using pure Python standard library."""

    def __init__(self, api_key: Optional[str] = None, base_url: str = DEFAULT_URL, model: str = DEFAULT_MODEL):
        self.api_key = api_key or resolve_api_key()
        self.base_url = base_url
        self.model = model

    def ask(self, state: Any, questions: Dict[str, Any], retries: int = 4, timeout: float = 60.0) -> Dict[str, Any]:
        payload = json.dumps({"model": self.model, "state": state, "questions": questions}).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "WorkbookForge-JevCritic/1.0",
        }
        req = urllib.request.Request(self.base_url, data=payload, headers=headers, method="POST")
        last_err = None

        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status == 402:
                        raise RuntimeError("TypeSafe API: Payment Required (402)")
                    res_body = resp.read().decode("utf-8")
                    return json.loads(res_body)
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 402:
                    raise RuntimeError("TypeSafe API: Payment Required (402)")
                time.sleep(1.5 * (attempt + 1))
            except Exception as e:
                last_err = e
                time.sleep(1.5 * (attempt + 1))

        raise RuntimeError(f"TypeSafe API request failed after {retries} attempts: {last_err}")


def audit_formula_profile(client: JevClient, formula_name: str, proposed_fixture: Dict[str, Any]) -> Dict[str, Any]:
    state = {
        "formula": formula_name,
        "fixture": proposed_fixture,
        "target": "Microsoft Excel 365 Desktop calculation engine",
    }
    questions = {
        "behavior_provenance": {
            "type": "choice",
            "instructions": f"Does this proposed behavior for {formula_name} match authentic Excel 365 Desktop behavior or an artificial engine profile?",
            "criteria": {
                "excel_canonical_behavior": {
                    "what": "Exact match with documented Microsoft Excel 365 calculation results or verified Excel behavior.",
                    "examples": ["SUM('1', 2) coerces numeric text in arguments to numbers", "='a'>1 returns TRUE"],
                    "not_for": "Local non-Excel approximations, standard IEEE float mismatch, or language exceptions."
                },
                "artificial_local_profile": {
                    "what": "A behavioral choice introduced by local engine design that conflicts with Excel Desktop.",
                    "examples": ["='a'>1 returning #VALUE!", "=0.1+0.2=0.3 returning FALSE", "LEN('😀') returning 1"],
                    "not_for": "Documented Excel error behaviors."
                },
                "syntax_or_spill_boundary": {
                    "what": "An unsupported boundary error returned because dynamic array spill placement or formula-cell context is unmodeled.",
                    "examples": ["Spill reference A1# unsupported", "Range in strictly scalar parameter returning local #VALUE!"],
                    "not_for": "Standard scalar functions returning normal calculation results."
                }
            }
        }
    }
    return client.ask(state, questions)


def classify_table_layout(client: JevClient, grid_window: List[Any]) -> Dict[str, Any]:
    state = {"grid_window": grid_window}
    questions = {
        "region_role": {
            "type": "choice",
            "instructions": "Classify the primary structural role of this tabular grid snippet.",
            "criteria": {
                "table_header_row": {
                    "what": "Row of distinct field names labeling the data columns beneath them.",
                    "examples": ["['Date', 'Transaction', 'Category', 'Amount']"],
                    "not_for": "Worksheet titles, KPI summary cards, or data rows."
                },
                "data_record_block": {
                    "what": "Homogeneous tabular rows representing business data records.",
                    "examples": ["['2025-01-01', 'Deposit', 'Revenue', 5000.00]"],
                    "not_for": "Summary rows with sums, averages, or header labels."
                },
                "aggregation_summary_row": {
                    "what": "Aggregation row summing or averaging prior rows.",
                    "examples": ["['Total', '', '', 124500.00]"],
                    "not_for": "Individual transaction lines."
                },
                "metadata_title_banner": {
                    "what": "Top-level metadata, sheet banner, report title, or standalone parameters.",
                    "examples": ["['Q4 2025 Financial Statement']"],
                    "not_for": "Multi-column grid headers."
                }
            }
        }
    }
    return client.ask(state, questions)


def run_benchmark_eval(client: JevClient, benchmark_path: Path, gate_threshold: float = 0.15) -> Dict[str, Any]:
    with open(benchmark_path, "r", encoding="utf-8") as f:
        cases = json.load(f)

    results = []
    brier_sum = 0.0

    for case in cases:
        cid = case["id"]
        target_choice = case["ground_truth"]
        state = case["state"]
        questions = case["questions"]
        q_key = list(questions.keys())[0]

        resp = client.ask(state, questions)
        answer = resp.get("answers", {}).get(q_key, {})
        predicted_choice = answer.get("choice")
        probabilities = answer.get("probabilities", {})

        # Compute multi-class Brier score: sum((p_k - y_k)^2)
        case_brier = 0.0
        for opt, prob in probabilities.items():
            actual = 1.0 if opt == target_choice else 0.0
            case_brier += (prob - actual) ** 2
        brier_sum += case_brier

        is_correct = (predicted_choice == target_choice)
        results.append({
            "id": cid,
            "correct": is_correct,
            "predicted": predicted_choice,
            "truth": target_choice,
            "confidence": answer.get("confidence", 0.0),
            "case_brier": case_brier,
            "is_false_control": case.get("is_false_control", False)
        })

    n = len(results)
    mean_brier = brier_sum / n if n > 0 else 0.0
    accuracy = sum(1 for r in results if r["correct"]) / n if n > 0 else 0.0

    controls = [r for r in results if r["is_false_control"]]
    control_acc = (sum(1 for r in controls if r["correct"]) / len(controls)) if controls else 1.0
    passed = (mean_brier <= gate_threshold) and (control_acc >= 0.95)

    return {
        "total_cases": n,
        "accuracy": accuracy,
        "mean_brier_score": mean_brier,
        "gate_threshold": gate_threshold,
        "false_control_cases": len(controls),
        "false_control_rejection_rate": control_acc,
        "gate_passed": passed,
        "details": results
    }


def main():
    parser = argparse.ArgumentParser(description="TypeSafe Jev System One critic for Workbook Forge.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    audit_p = sub.add_parser("audit-profile")
    audit_p.add_argument("--formula", required=True)
    audit_p.add_argument("--fixture", required=True, help="JSON string or file path")

    layout_p = sub.add_parser("classify-layout")
    layout_p.add_argument("--window-json", required=True, help="JSON string of 2D row array")

    eval_p = sub.add_parser("eval-benchmark")
    eval_p.add_argument("--benchmark-file", default=str(ROOT / "catalog" / "behavior_critic_benchmarks.json"))
    eval_p.add_argument("--gate-threshold", type=float, default=0.15)

    args = parser.parse_args()
    client = JevClient()

    if args.cmd == "audit-profile":
        fixture_data = json.loads(args.fixture) if args.fixture.startswith("{") else json.loads(Path(args.fixture).read_text())
        res = audit_formula_profile(client, args.formula, fixture_data)
        print(json.dumps(res, indent=2))
    elif args.cmd == "classify-layout":
        window = json.loads(args.window_json)
        res = classify_table_layout(client, window)
        print(json.dumps(res, indent=2))
    elif args.cmd == "eval-benchmark":
        res = run_benchmark_eval(client, Path(args.benchmark_file), args.gate_threshold)
        print(json.dumps(res, indent=2))
        sys.exit(0 if res["gate_passed"] else 1)


if __name__ == "__main__":
    main()
