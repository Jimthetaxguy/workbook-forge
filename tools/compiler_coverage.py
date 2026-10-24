"""Map every row of the pinned Microsoft inventory across independent runtimes.

Parsing NAME() tests syntax recognition only, never valid arity or semantics.
The versioned mapping deliberately keeps preservation and source translation
claims separate from registry membership and shared test-fixture evidence.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import subprocess
from pathlib import Path

from workbook_forge.expressions import analyze_formula
from workbook_forge.source_graph import ERRORS, SOURCE_FUNCTIONS

ROOT = Path(__file__).resolve().parents[1]


def build(binary: Path) -> dict:
    raw = (ROOT / "catalog/function_inventory.json").read_bytes()
    inventory = json.loads(raw)
    catalog = json.loads((ROOT / "catalog/formulas.json").read_bytes())
    implementations = {row["name"]: row for row in catalog["functions"]}
    rust_registry = (ROOT / "rust/src/toolkit.rs").read_text().split("pub const SUPPORTED_FUNCTIONS:", 1)[1].split("];", 1)[0]
    rust_names = set(re.findall(r'"([A-Z][A-Z0-9_.]*)"', rust_registry))
    fixture_bytes = (ROOT / "fixtures/formula-cases.jsonl").read_bytes()
    cases = collections.defaultdict(list)
    for line in fixture_bytes.splitlines():
        case = json.loads(line)
        try:
            for name in analyze_formula(case["formula"])["functions"]:
                cases[name].append(case["id"])
        except ValueError:
            pass  # Deliberately malformed syntax is not a positive parse probe.
    rows = []
    for entry in inventory["functions"]:
        name = entry["name"]
        languages = {}
        for language in ("python", "rust"):
            try:
                if language == "python":
                    analyze_formula(f"={name}()")
                    parsed = True
                else:
                    probe = subprocess.run([str(binary), "analyze", f"={name}()"],
                                           capture_output=True, text=True, timeout=10)
                    parsed = probe.returncode == 0
            except ValueError:
                parsed = False
            status = implementations.get(name, {}).get("status", {}).get(language, "catalogued")
            executable = status in {"implemented", "evaluated", "conformance-tested"}
            if language == "rust":
                assert executable == (name in rust_names), f"Rust registry drift: {name}"
            languages[language] = {
                "recognized": "catalogued", "parsed_syntax_probe": parsed,
                "preserved": "generic_normal_formula_path; per_function_unverified",
                "translated_source": "graph_source_subset_v5" if name in SOURCE_FUNCTIONS else "not_implemented",
                "executable": "implemented_profile" if executable else "planned",
                "tested_against_reference": "shared_expected_fixtures" if executable and cases[name] else "not_established",
                "excel_observed": "not_established_by_this_mapping",
                "unsupported_now": not executable,
            }
        rows.append({"name": name, "category": entry["category"],
                     "availability": entry["excel_365_desktop_availability"],
                     "introduced_version_marker": entry["introduced_version_marker"],
                     "shared_case_ids": sorted(cases[name]), "languages": languages,
                     "runtime_contract": "explicit_provider_binding_planned" if entry["category"] in
                     {"Add-in and Automation", "Cube", "Web"} or name in {"NOW", "TODAY", "RAND", "RANDBETWEEN", "RANDARRAY", "RTD", "INDIRECT", "OFFSET"}
                     else "deterministic_excel_semantics_target"})
    assert len(rows) == inventory["function_count"]
    assert len({r["name"] for r in rows}) == len(rows)
    return {"mapping_version": 1, "target": "full custom Excel language and runtime coverage",
            "scope": "every entry of the pinned Microsoft inventory, not every possible formula expression or UDF",
            "inventory_version": inventory["inventory_version"], "inventory_retrieved": inventory["retrieved"],
            "inventory_sha256": hashlib.sha256(raw).hexdigest(), "inventory_refresh_required": inventory["refresh_required"],
            "formula_fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
            "primary_source": inventory["source"]["url"],
            "target_build": "Microsoft 365 desktop; build/channel availability unverified",
            "udfs": "arbitrary add-ins/UDFs are in long-term scope through explicit typed bindings; not enumerable built-ins",
            "function_count": len(rows), "functions": rows,
            "grammar_features": catalog["syntax_features"],
            "grammar_evidence_note": "existing catalog statuses, not newly verified translation coverage",
            "reference_evidence_note": "fixture expected values include documented examples, independent arithmetic and local profiles; case membership or engine agreement is not an Excel oracle",
            "source_translation": {"rust_to_python": "graph_source_subset_v5", "python_to_rust": "graph_source_subset_v5",
                "functions": list(SOURCE_FUNCTIONS), "function_count": len(SOURCE_FUNCTIONS),
                "comparison_operators": ["=","<>","<","<=",">",">="],
                "evidence_audits": ["catalog/compiler-comparison-evidence.v1.json","catalog/average-evidence.v1.json"],
                "average_direct_policy": "provisional; primary sources conflict; no Excel observation",
                "error_scope": "nine existing scalar codes; modern service/security/Python/provider error contracts remain planned",
                "typed_error_codes": sorted(ERRORS), "remaining_error_codes": [],
                "subset": "multiple sheets, + - * /, SUM/AVERAGE ranges, lazy IF/IFERROR, six typed comparison operators, mixed/absolute/quoted references and nine typed scalar errors; numeric-v1 and graph-v2 retained",
                "arbitrary_source_programs": "refused outside versioned scaffold; no restoration of embedded XML"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rust-binary", type=Path, default=ROOT / "rust/target/release/examples/compiler_roundtrip")
    parser.add_argument("--output", type=Path, default=ROOT / "catalog/compiler-coverage.v1.json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(build(args.rust_binary.resolve()), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if args.output.read_text() != content:
            raise SystemExit("coverage mapping is stale")
    else:
        args.output.write_text(content)


if __name__ == "__main__":
    main()
