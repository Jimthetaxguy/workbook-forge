"""The capability matrix is generated from the catalogs and committed.

tools/capability_matrix.py joins catalog/formulas.json, the corpus report,
the preserved-only tier, the source compiler's function table, the agent
operation catalog, the browser protocol and the primitive table into one
feature x route x operation table. These tests keep the committed artifact in
step with a fresh generation (the pattern test_capabilities_document_is_current
uses) and hold the matrix to its vocabulary and to the counts the catalogs
establish. They never run an engine.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "corpus"))
import capabilities  # noqa: E402
import capability_matrix  # noqa: E402

from workbook_forge.source_graph import FUNCTIONS_BY_VERSION  # noqa: E402

COMMITTED = ROOT / "catalog" / "capability-matrix.v0.json"
EVIDENCE_GRADES = {"profile", "dual_engine", "expected_value", "excel_observed", "none"}
REFUSED = re.compile(r"^refused:[a-z0-9_]+(,[a-z0-9_]+)*$")


@pytest.fixture(scope="module")
def matrix() -> dict:
    return json.loads(COMMITTED.read_text(encoding="utf-8"))


def by_feature(matrix: dict, kind: str) -> dict[str, dict[tuple[str, str], str]]:
    """{feature: {(route, operation): status}} for one kind of feature."""
    table: dict[str, dict[tuple[str, str], str]] = {}
    for row in matrix["rows"]:
        if row["kind"] == kind:
            table.setdefault(row["feature"], {})[(row["route"], row["operation"])] = row["status"]
    return table


def test_committed_matrix_is_current():
    """Regenerate with `python tools/capability_matrix.py --write` after an input changes."""
    assert COMMITTED.read_text(encoding="utf-8") == capability_matrix.render(), (
        "catalog/capability-matrix.v0.json is stale: run python tools/capability_matrix.py --write"
    )


def test_check_flag_reports_drift(tmp_path):
    tool = ROOT / "tools" / "capability_matrix.py"
    current = subprocess.run([sys.executable, str(tool), "--check"], capture_output=True, text=True)
    assert current.returncode == 0, current.stdout + current.stderr
    stale = tmp_path / "matrix.json"
    stale.write_text(COMMITTED.read_text(encoding="utf-8").replace('"supported"', '"supportd"', 1))
    drift = subprocess.run(
        [sys.executable, str(tool), "--check", "--out", str(stale)], capture_output=True, text=True,
    )
    assert drift.returncode == 1
    assert "differs" in drift.stdout + drift.stderr


def test_rendered_text_round_trips_the_document():
    assert json.loads(capability_matrix.render()) == capability_matrix.build()


def test_generated_from_names_every_input_with_its_current_hash(matrix):
    assert matrix["schema_version"] == 0
    recorded = {entry["path"]: entry["sha256"] for entry in matrix["generated_from"]}
    assert set(recorded) == set(capability_matrix.INPUTS.values())
    for path, digest in recorded.items():
        assert digest == hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), path
    assert [entry["path"] for entry in matrix["generated_from"]] == sorted(recorded)


def test_every_catalog_function_evaluates_on_both_engines(matrix):
    catalog = json.loads((ROOT / "catalog" / "formulas.json").read_text(encoding="utf-8"))
    names = {entry["name"] for entry in catalog["functions"]}
    functions = by_feature(matrix, "function")
    assert set(functions) == names
    evaluated = {
        name for name, cells in functions.items()
        if cells[("python-toolkit", "evaluate")] == "supported"
        and cells[("rust", "evaluate")] == "supported"
    }
    assert len(evaluated) == 139
    assert evaluated == names
    for name in names:
        assert functions[name][("python-canonical", "evaluate")] == "supported"
        assert functions[name][("wasm-browser", "evaluate")] == "untested"
        assert functions[name][("browser-protocol", "evaluate")] == "untested"
    # The agent catalog has no calculate effect, so that route still has no evaluate operation.
    assert functions["SUM"][("agent-protocol", "evaluate")] == "not-applicable"
    assert functions["SUM"][("browser-protocol", "inspect")] == "not-applicable"


def test_exactly_five_functions_emit_source(matrix):
    functions = by_feature(matrix, "function")
    for route in ("source-python", "source-rust"):
        emitted = {
            name for name, cells in functions.items() if cells[(route, "emit-source")] == "supported"
        }
        assert len(emitted) == 5
        assert emitted == set(FUNCTIONS_BY_VERSION[6])
        refused = {
            name for name, cells in functions.items()
            if cells[(route, "emit-source")] == "refused:unsupported_formula"
        }
        assert refused == set(functions) - emitted
    # Evaluation through emitted source is a WA2 probe, not a catalog entry yet.
    assert functions["SUM"][("source-python", "evaluate")] == "untested"
    assert functions["ABS"][("source-python", "evaluate")] == "not-applicable"


def test_parsed_function_table_matches_the_module():
    assert capability_matrix.read_functions_by_version() == FUNCTIONS_BY_VERSION


def test_every_corpus_category_appears(matrix):
    _, manifest = capabilities.parse_report((ROOT / "docs" / "corpus-report.md").read_text(encoding="utf-8"))
    reported = {category for info in manifest.values() for category in info["categories"]}
    assert set(by_feature(matrix, "category")) == reported
    assert matrix["summary"]["features"]["category"] == len(reported)


def test_category_rows_follow_the_report(matrix):
    categories = by_feature(matrix, "category")
    # Refused in every engine; the two engines name the same code.
    for route in ("python-toolkit", "python-canonical", "rust", "wasm-browser"):
        assert categories["strict-ooxml"][(route, "inspect")] == "refused:strict_ooxml"
    # Refused with two codes on the Python engines, one on Rust (xw_macro.xlsm is not .xlsx).
    assert categories["vba"][("python-toolkit", "inspect")] == "refused:macro_content,not_xlsx"
    assert categories["vba"][("rust", "inspect")] == "refused:macro_content"
    # Refused only by the canonical importer.
    assert categories["shared-formulas"][("python-canonical", "edit")] == "refused:unsupported_formula_group"
    assert categories["shared-formulas"][("python-toolkit", "edit")] == "supported"
    # Preserved only: kept, inspected, not edited or calculated; not carried by the canonical writer.
    assert categories["pivot"][("rust", "preserve")] == "supported"
    assert categories["pivot"][("rust", "edit")] == "preserved"
    assert categories["pivot"][("rust", "evaluate")] == "preserved"
    assert categories["pivot"][("wasm-browser", "edit")] == "preserved"
    assert categories["pivot"][("python-canonical", "inspect")] == "supported"
    assert categories["pivot"][("python-canonical", "export")] == "not-applicable"
    # Supported: the corpus run does not report calculation per category.
    assert categories["defined-names"][("python-toolkit", "edit")] == "supported"
    assert categories["defined-names"][("python-toolkit", "evaluate")] == "untested"
    # The canonical writer builds a fresh package, so it has no preserve operation.
    assert categories["defined-names"][("python-canonical", "preserve")] == "not-applicable"
    # No catalog records category behaviour through the protocols or the source compiler.
    assert categories["defined-names"][("agent-protocol", "edit")] == "untested"
    assert categories["defined-names"][("browser-protocol", "inspect")] == "untested"
    assert categories["defined-names"][("browser-protocol", "evaluate")] == "untested"
    assert categories["defined-names"][("source-python", "emit-source")] == "untested"


def test_primitive_rows_follow_the_capability_table(matrix):
    table = json.loads((ROOT / "catalog" / "workbook-capabilities.json").read_text(encoding="utf-8"))
    primitives = by_feature(matrix, "primitive")
    assert set(primitives) == {entry["id"] for entry in table["primitives"]}
    assert len(primitives) == 11
    for entry in table["primitives"]:
        for route in ("python-toolkit", "rust"):
            for flag, operation in capability_matrix.PRIMITIVE_FLAGS.items():
                expected = "supported" if entry[flag] else "not-applicable"
                assert primitives[entry["id"]][(route, operation)] == expected, (entry["id"], route, flag)
    assert primitives["arrays"][("rust", "edit")] == "not-applicable"
    assert primitives["scalar-cells"][("python-canonical", "evaluate")] == "not-applicable"
    # The browser calculate command is evaluate, and the primitive table does not grade it.
    assert primitives["scalar-cells"][("browser-protocol", "evaluate")] == "untested"
    assert primitives["scalar-cells"][("browser-protocol", "inspect")] == "not-applicable"


def test_rows_use_the_vocabulary(matrix):
    sources = set(capability_matrix.INPUTS.values()) | {"tools/capability_matrix.py"}
    catalog = json.loads((ROOT / "catalog" / "formulas.json").read_text(encoding="utf-8"))
    function_grades = {entry["name"]: entry["evidence_grade"] for entry in catalog["functions"]}
    engine_evaluate = set(capability_matrix.FUNCTION_STATUS_LANGUAGE)
    for row in matrix["rows"]:
        assert list(row) == ["kind", "feature", "route", "operation", "status", "evidence_grade", "source"]
        assert row["kind"] in capability_matrix.KINDS, row
        assert row["route"] in capability_matrix.ROUTES, row
        assert row["operation"] in capability_matrix.OPERATIONS, row
        status = row["status"]
        assert status in capability_matrix.STATUSES or REFUSED.match(status), row
        assert row["evidence_grade"] in EVIDENCE_GRADES, row
        assert row["source"] in sources, row
        # The catalog grade belongs only on the evaluate rows it established.
        established = (
            row["kind"] == "function"
            and row["operation"] == "evaluate"
            and row["route"] in engine_evaluate
            and status == "supported"
        )
        if established:
            assert row["evidence_grade"] == function_grades[row["feature"]], row
            assert row["source"] == capability_matrix.INPUTS["formulas"], row
        else:
            assert row["evidence_grade"] == "none", row
    assert matrix["routes"] == {route: list(ops) for route, ops in capability_matrix.ROUTE_OPERATIONS.items()}
    assert matrix["operations"] == list(capability_matrix.OPERATIONS)


def test_refusal_codes_are_catalogued(matrix):
    catalog = json.loads((ROOT / "catalog" / "refusal-codes.json").read_text(encoding="utf-8"))
    known = set(catalog["codes"]) | set(catalog["inherited"]["codes"])
    seen = set()
    for row in matrix["rows"]:
        if row["status"].startswith("refused:"):
            seen.update(row["status"].split(":", 1)[1].split(","))
    assert seen
    assert seen <= known, seen - known


def test_matrix_is_total_and_sorted(matrix):
    rows = matrix["rows"]
    triples = [(row["kind"], row["feature"], row["route"], row["operation"]) for row in rows]
    assert len(set(triples)) == len(triples)
    features = {(row["kind"], row["feature"]) for row in rows}
    assert len(rows) == len(features) * len(capability_matrix.ROUTES) * len(capability_matrix.OPERATIONS)
    assert rows == sorted(rows, key=capability_matrix.row_key)
    assert matrix["summary"]["rows"] == len(rows)
    for operation in capability_matrix.OPERATIONS:
        offered = {route for route, ops in capability_matrix.ROUTE_OPERATIONS.items() if operation in ops}
        for row in rows:
            if row["operation"] == operation and row["route"] not in offered:
                assert row["status"] == "not-applicable", row
