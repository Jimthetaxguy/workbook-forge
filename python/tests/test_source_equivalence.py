"""Engine versus generated-runtime equivalence for source profile 6.

One parametrized case per documented mode of SUM, IF, IFERROR, AVERAGE,
XLOOKUP and the profile-6 operators. Each case authors a small toolkit
workbook and compares five results: ``workbook_forge.evaluate_result`` on the
Python engine, the Python engine behind a toolkit session, the Rust engine
through the native bridge, and the profile-6 Python and Rust programs the
source compiler generates from that workbook, run through
``run_validated_source``.

The case table, the hand-derived expected values with their basis, the
runners and the classification live in ``tools/source_equivalence_probe.py``
so the probe script emits exactly the matrix this test asserts. ``DIVERGENCES``
is that module's table of pinned disagreements; a disagreement is never hidden
by loosening an assertion. This test fails when an unpinned disagreement
appears, when a pinned one changes, or when a pinned entry names a case or a
classification that does not exist.

The Rust engine column is compared only when the optional bridge is installed
(the guard shared with test_dual_engines); the generated Rust column only when
rustc is on PATH (the guard shared with test_compiler_seeded). Both are present
under tools/gate.sh, which fails on a skipped test.
"""
import importlib.util
import json
import shutil

import pytest

from test_compiler_benchmark import tool

probe = tool("source_equivalence_probe")
CASES = probe.CASES
DIVERGENCES = probe.DIVERGENCES
RUST_ENGINE = importlib.util.find_spec("workbook_forge._native") is not None
GENERATED_RUST = shutil.which("rustc") is not None


@pytest.mark.parametrize("case", CASES, ids=probe.case_id)
def test_engine_and_generated_runtime_agree(tmp_path, case):
    row = probe.run_case(case, tmp_path, rust_engine=RUST_ENGINE, generated_rust=GENERATED_RUST)
    observed = {column: row[column] for column in probe.COLUMNS}
    assert row["classification"] != "unclassified", (observed, row["note"])
    pinned = DIVERGENCES.get((case.function, case.mode))
    assert row["generated_route"] == row["expected_route"], observed
    if pinned is None:
        assert row["agreement"] and row["matches_expectation"], observed
        assert row["classification"] == (
            "refusal by design" if isinstance(case.expected, probe.Refused) else "agreement"
        )
        assert row["python_engine"] == row["expected_formula_api"]
        for column in probe.CELL_COLUMNS:
            assert row[column] in {probe.UNAVAILABLE, row["expected"]}, (column, observed)
    else:
        # The disagreement stands exactly as recorded, with its one classification.
        assert row["classification"] == pinned.classification, observed
        for column, value in pinned.observed.items():
            assert row[column] in {probe.UNAVAILABLE, value}, (column, observed)
    if not RUST_ENGINE:
        pytest.skip("optional Rust extension is not installed; rust_engine column not compared")
    if not GENERATED_RUST:
        pytest.skip("Rust toolchain unavailable; generated_rust column not compared")


def test_divergence_table_names_real_cases_and_classifications():
    ids = {(case.function, case.mode) for case in CASES}
    assert len(ids) == len(CASES), "duplicate case identity"
    for key, divergence in DIVERGENCES.items():
        assert key in ids, key
        assert divergence.classification in {
            "semantic difference", "test error", "shared deviation"
        }
        assert set(divergence.observed) <= set(probe.COLUMNS), key
        assert divergence.note


def test_every_profile_six_function_and_the_operators_have_cases():
    from workbook_forge.source_graph import FUNCTIONS_BY_VERSION

    functions = {case.function for case in CASES}
    assert set(FUNCTIONS_BY_VERSION[6]) | {"OPERATORS"} == functions


def test_render_is_one_spelling_per_value_kind():
    from workbook_forge import ErrorValue

    assert probe.render(3) == probe.render(3.0) == "3.0"
    assert probe.render(-0.0) == "-0.0"
    assert probe.render(True) == "TRUE" and probe.render(0) != probe.render(False)
    assert probe.render(None) == "blank"
    assert probe.render("") == '""'
    assert probe.render(ErrorValue("#N/A")) == probe.render({"error": "#N/A"}) == "#N/A"
    assert probe.render(probe.REFUSED) == "refused:unsupported_formula"
    with pytest.raises(TypeError):
        probe.render(object())


def test_probe_writes_the_matrix_for_one_function(tmp_path):
    output = tmp_path / "matrix.json"
    assert probe.main(["--output", str(output), "--function", "SUM", "--no-generated-rust",
                       "--no-rust-engine"]) == 0
    matrix = json.loads(output.read_text(encoding="utf-8"))
    assert matrix["functions"] == ["SUM"]
    assert matrix["rust_engine"] == matrix["generated_rust"] == probe.UNAVAILABLE
    assert matrix["cases"] == len(matrix["rows"]) == sum(c.function == "SUM" for c in CASES)
    assert matrix["summary"]["unclassified"] == 0
    assert matrix["summary"]["agreement"] == matrix["cases"]
    row = matrix["rows"][0]
    assert set(row) >= {"function", "mode", "formula", "expected", "basis", "python_engine",
                        "rust_engine", "generated_python", "agreement", "classification"}
    assert row["rust_engine"] == row["generated_rust"] == probe.UNAVAILABLE
