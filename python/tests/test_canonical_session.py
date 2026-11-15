"""The calculation session over the owner model, on both backends (spec 09 step 1).

The contract is the session section of
``docs/specs/proposed/05-canonical-calculation-session.md``. Expected values
come from ``fixtures/session-input-change.v1.json``, which is hand arithmetic
and stated contract rules, or from rules written out beside the assertion;
none is taken from an engine run.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
from threading import Event

from jsonschema import Draft202012Validator
import pytest

from workbook_forge import python_engine
from workbook_forge.python_engine import ToolkitError
from workbook_forge.session import CalculationSession
from workbook_forge.volatile import CalcContext

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "fixtures" / "session-input-change.v1.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((ROOT / "schemas" / "calculation-report.v2.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)
TOLERANCE = FIXTURE["numeric_tolerance"]
CONTEXT = CalcContext.from_dict(FIXTURE["calc"])
SCENARIOS = {scenario["id"]: scenario for scenario in FIXTURE["scenarios"]}
PLANNER_FORMULAS = [f"Forecast!{column}{row}" for column in "CDE" for row in (2, 3, 4)] + ["Forecast!F2", "Forecast!F3", "Forecast!F4"]


@pytest.fixture(params=["python", "rust"])
def backend(request):
    if request.param == "rust" and importlib.util.find_spec("workbook_forge._native") is None:
        pytest.skip("optional Rust extension is not installed")
    return request.param


def _fingerprint(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _open(doc_id: str, backend: str) -> tuple[CalculationSession, str | None]:
    """Build the session a fixture document names and check its ``open`` block.

    The second item is the SHA-256 this test takes of the bytes it gives to
    ``open`` (None for the constructor entry). The fixture pins the same digest
    as a literal, so the session's fingerprint is checked against two values
    that do not come from a session.
    """
    entry = FIXTURE["documents"][doc_id]
    expected = entry["open"]
    if entry["entry"] == "model":
        session, fingerprint = CalculationSession(copy.deepcopy(entry["document"]), backend=backend), None
    else:
        assert entry["entry"] == "open"
        if "document_fixture" in entry:
            data = (ROOT / "fixtures" / entry["document_fixture"]).read_bytes()
        else:
            data = entry["document_text"].encode("utf-8")
            assert json.loads(data) == entry["document"]
        session = CalculationSession.open(data, input_kinds=entry.get("input_kinds"), backend=backend)
        fingerprint = _fingerprint(data)
    assert session.revision == expected["revision"]
    assert session.model_version == expected["model_version"]
    if expected["source_fingerprint"] is None:
        assert session.source_fingerprint is None
    else:
        assert session.source_fingerprint == fingerprint == expected["source_fingerprint"]
    if expected["conversion"] is None:
        assert session.conversion is None
    elif expected["conversion"] == "present":
        assert session.conversion is not None
    else:
        assert session.conversion.to_dict() == expected["conversion"]
    return session, fingerprint


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _integer(value) -> bool:
    return type(value) is int


def _same(actual, expected, where: str) -> None:
    """The fixture's comparison rule for one value, recursively."""
    if isinstance(expected, dict) and set(expected) == {"error"}:
        # An error value is compared on `error` only; a message may ride along.
        assert isinstance(actual, dict) and actual.get("error") == expected["error"], f"{where}: {actual!r} != {expected!r}"
    elif isinstance(expected, dict):
        assert isinstance(actual, dict) and set(actual) == set(expected), f"{where}: keys {actual!r} != {expected!r}"
        for key, value in expected.items():
            _same(actual[key], value, f"{where}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), f"{where}: {actual!r} != {expected!r}"
        for index, value in enumerate(expected):
            _same(actual[index], value, f"{where}[{index}]")
    elif _integer(expected) and _integer(actual):
        # Two integers (revisions, counts, the pinned clock) are the same
        # integer or they differ; the tolerance is for calculated numbers.
        assert actual == expected, f"{where}: {actual!r} != {expected!r}"
    elif _number(expected):
        assert _number(actual) and abs(actual - expected) <= TOLERANCE * max(1, abs(expected)), f"{where}: {actual!r} != {expected!r}"
    else:
        assert type(actual) is type(expected) and actual == expected, f"{where}: {actual!r} != {expected!r}"


def _compare(actual: dict, expected: dict, backend: str, fingerprint: str | None = None, where: str = "report") -> None:
    """Compare one calculation-report.v2 with the fixture's expectation."""
    VALIDATOR.validate(actual)
    assert set(actual) == set(expected) | {"engine"}, where
    assert actual["engine"] == backend, where
    for key, value in expected.items():
        if key == "diagnostics":
            assert len(actual[key]) == len(value), f"{where}.diagnostics: {actual[key]!r}"
            for index, (got, want) in enumerate(zip(actual[key], value)):
                found = (got["code"], got.get("sheet"), got.get("address"))
                assert found == (want["code"], want.get("sheet"), want.get("address")), f"{where}.diagnostics[{index}]: {got!r}"
                assert isinstance(got["message"], str) and got["message"], f"{where}.diagnostics[{index}]: {got!r}"
        elif key == "source_fingerprint" and value is not None:
            assert actual[key] == value == fingerprint, where
        else:
            _same(actual[key], value, f"{where}.{key}")


def _step(session: CalculationSession, step: dict, doc_id: str, backend: str, fingerprint: str | None) -> None:
    where = f"{doc_id}/{step['id']}"
    if step["op"] == "calculate":
        _compare(session.calculate(workers=1, context=CONTEXT), step["expect"][doc_id], backend, fingerprint, where)
    elif step["op"] == "set_inputs":
        if "error" in step:
            with pytest.raises(ToolkitError) as raised:
                session.set_inputs(step["values"], step["expected_revision"])
            assert raised.value.code == step["error"], where
            assert session.revision == step["revision_after"], where
        else:
            assert session.set_inputs(step["values"], step["expected_revision"]) == step["returns"], where
            assert session.revision == step["returns"], where
    else:
        assert step["op"] == "preview_inputs", where
        revision, snapshot = session.revision, session.snapshot()
        if "error" in step:
            with pytest.raises(ToolkitError) as raised:
                session.preview_inputs(step["values"], step["expected_revision"], context=CONTEXT)
            assert raised.value.code == step["error"], where
        else:
            preview = session.preview_inputs(step["values"], step["expected_revision"], context=CONTEXT)
            expected = step["expect"][doc_id]
            assert set(preview) == set(expected), where
            assert preview["applied"] is False, where
            assert (preview["base_revision"], preview["proposed_revision"]) == (expected["base_revision"], expected["proposed_revision"]), where
            _same(preview["changes"], expected["changes"], f"{where}.changes")
            _compare(preview["before"], expected["before"], backend, fingerprint, f"{where}.before")
            _compare(preview["after"], expected["after"], backend, fingerprint, f"{where}.after")
        assert session.revision == revision == step["revision_after"], where
        assert session.snapshot() == snapshot, where


def _replay(scenario_id: str, doc_id: str, backend: str, *, through: str | None = None, only: tuple[str, ...] = ()) -> CalculationSession:
    """Run a scenario's steps in order: all of them, those up to and including
    ``through``, and then the ``only`` steps picked out of the remainder."""
    session, fingerprint = _open(doc_id, backend)
    steps = SCENARIOS[scenario_id]["steps"]
    ids = [step["id"] for step in steps]
    cut = len(steps) if through is None else ids.index(through) + 1
    for step in steps[:cut]:
        _step(session, step, doc_id, backend, fingerprint)
    for step_id in only:
        assert ids.index(step_id) >= cut
        _step(session, steps[ids.index(step_id)], doc_id, backend, fingerprint)
    return session


def _code(call, *args, **kwargs) -> str:
    with pytest.raises(ToolkitError) as raised:
        call(*args, **kwargs)
    return raised.value.code


def test_new_session_starts_at_revision_zero(backend):
    for doc_id in ("planner", "canonical", "legacy_revision"):
        session, _ = _open(doc_id, backend)
        assert session.revision == 0
    # The legacy key is checked, left out of the model and reported.
    assert session.conversion.dropped == ("revision",)
    assert "revision" not in session.snapshot()


def test_preview_does_not_change_revision_or_values(backend):
    # s13 previews at the current revision; s14 then calculates the live
    # session and finds nothing to do.
    session = _replay("input-changes", "planner", backend, through="s14")
    assert session.revision == 3


def test_set_inputs_at_current_revision_increments_and_recalculates(backend):
    session = _replay("input-changes", "planner", backend, through="s06")
    assert session.revision == 3


def test_set_inputs_at_stale_revision_is_revision_conflict(backend):
    session = _replay("input-changes", "planner", backend, through="s06", only=("s07", "s12"))
    assert session.revision == 3


def test_unknown_binding_name_is_invalid_input(backend):
    # s11 is the pass after the three refusals: nothing was dirtied.
    session = _replay("input-changes", "planner", backend, through="s06", only=("s08", "s09", "s10", "s11"))
    assert session.revision == 3


def _budget_model(n: int) -> dict:
    """A workbook whose pass ends early when ``n`` is large.

    C1 and D1 each spill ``n`` rows. The pass may materialise at most 100000
    result cells (docs/refusal-codes.md, resource_limit), so n = 60000 needs
    120000 and the pass stops part-way; n = 1 or 3 fits easily. No outputs are
    bound, so the closure is every cell.
    """
    return {
        "schema_version": 1,
        "sheets": [{"id": "data", "name": "Data", "cells": {
            "A1": {"value": n},
            "B1": {"value": None, "formula": "=A1+1"},
            "C1": {"value": None, "formula": "=SEQUENCE(A1)"},
            "D1": {"value": None, "formula": "=SEQUENCE(A1)"},
        }}],
        "inputs": {"n": {"sheet": "Data", "address": "A1", "kind": "number", "required": True}},
        "outputs": {},
    }


def _codes(report: dict) -> set[str]:
    return {item["code"] for item in report["diagnostics"]}


def test_failed_calculation_publishes_no_partial_result(backend, monkeypatch):
    # A pass that ends early is returned and not published. Completed pass at
    # revision 0 first: B1 = 1 + 1.
    session = CalculationSession(_budget_model(1), backend=backend)
    first = session.calculate(context=CONTEXT)
    VALIDATOR.validate(first)
    assert first["base_revision"] is None and "resource_limit" not in _codes(first)
    assert first["values"]["Data!B1"] == 2
    assert session.set_inputs({"n": 60000}, 0) == 1
    failed = session.calculate(context=CONTEXT)
    VALIDATOR.validate(failed)
    assert failed["revision"] == 1 and failed["stale"] is False
    assert "resource_limit" in _codes(failed)
    # Recalculating changes nothing: the base is still the completed pass at
    # revision 0, and B1 (dirty since that base) runs again instead of being
    # reused from the failed pass.
    again = session.calculate(context=CONTEXT)
    assert again["base_revision"] == 0 and "resource_limit" in _codes(again)
    assert "Data!B1" in again["evaluated_cells"]
    # Fix the cause. The next pass still builds on revision 0, so every formula
    # downstream of A1 runs: B1 = 3 + 1.
    assert session.set_inputs({"n": 3}, 1) == 2
    fixed = session.calculate(context=CONTEXT)
    VALIDATOR.validate(fixed)
    assert fixed["revision"] == 2 and fixed["base_revision"] == 0
    assert fixed["evaluated_cells"] == ["Data!B1", "Data!C1", "Data!D1"]
    assert fixed["values"]["Data!B1"] == 4 and "resource_limit" not in _codes(fixed)
    assert (fixed["calculated_formulas"], fixed["unresolved_formulas"]) == (3, 0)
    settled = session.calculate(context=CONTEXT)
    assert settled["base_revision"] == 2 and settled["evaluated_cells"] == []

    # With no completed pass at all there is no base to report.
    never = CalculationSession(_budget_model(60000), backend=backend)
    assert "resource_limit" in _codes(never.calculate(context=CONTEXT))
    retry = never.calculate(context=CONTEXT)
    assert retry["base_revision"] is None and "Data!B1" in retry["evaluated_cells"]

    # A refused batch changes nothing and dirties nothing.
    planner = FIXTURE["documents"]["planner"]["document"]
    session = CalculationSession(copy.deepcopy(planner), backend=backend)
    session.calculate(context=CONTEXT)
    revision, snapshot = session.revision, session.snapshot()
    assert _code(session.set_inputs, {"unit_price": 30, "nope": 1}, revision) == "invalid_input"
    assert (session.revision, session.snapshot()) == (revision, snapshot)
    report = session.calculate(context=CONTEXT)
    assert report["evaluated_cells"] == [] and report["base_revision"] == revision

    # A pass overtaken by a commit is returned stale and not published. Only
    # the Python engine can be paused mid-pass from here.
    if backend == "python":
        session = CalculationSession(copy.deepcopy(planner), backend=backend)
        started, finish = Event(), Event()
        original = python_engine._calculate_document

        def gated(*args, **kwargs):
            started.set()
            assert finish.wait(5)
            return original(*args, **kwargs)

        monkeypatch.setattr(python_engine, "_calculate_document", gated)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(session.calculate, context=CONTEXT)
            assert started.wait(5)
            assert session.set_inputs({"unit_price": 25}, 0) == 1
            finish.set()
            stale = pending.result()
        monkeypatch.setattr(python_engine, "_calculate_document", original)
        VALIDATOR.validate(stale)
        assert stale["stale"] is True and stale["revision"] == 0
        current = session.calculate(context=CONTEXT)
        assert current["stale"] is False and current["revision"] == 1
        assert current["base_revision"] is None
        assert current["evaluated_cells"] == sorted(PLANNER_FORMULAS)


def _sheet_model(cells: dict, *, inputs: dict | None = None, outputs: dict | None = None) -> dict:
    """An owner model with one sheet ``S``. A string cell starting with ``=``
    is a formula; ``inputs`` maps a name to the address of a required number
    input and ``outputs`` a name to an address."""
    sheet = {
        address: {"value": None, "formula": cell} if isinstance(cell, str) and cell.startswith("=") else {"value": cell}
        for address, cell in cells.items()
    }
    return {
        "schema_version": 1,
        "sheets": [{"id": "s", "name": "S", "cells": sheet}],
        "inputs": {name: {"sheet": "S", "address": address, "kind": "number", "required": True} for name, address in (inputs or {}).items()},
        "outputs": {name: {"sheet": "S", "address": address} for name, address in (outputs or {}).items()},
    }


def test_graph_refused_pass_counts_every_formula_unresolved_and_publishes_nothing(backend):
    # C1 names two ranges of 60000 cells. The engines expand at most 100000
    # referenced cells per workbook (resource_limit) and 120000 is over, so the
    # graph is refused before any cell runs. Such a pass has no closure: it
    # reports no value and counts every formula cell of the document (A3, B1,
    # B2, C1: four) as unresolved (session contract, section 5).
    model = _sheet_model({"A1": 1, "A2": 2, "A3": "=A1+A2", "B1": "=A1*10", "B2": "=1+", "C1": "=SUM(A1:A60000)+SUM(D1:D60000)"})
    session = CalculationSession(model, backend=backend)
    for _ in range(2):
        # The second pass has the same null base: the first published nothing.
        report = session.calculate(context=CONTEXT)
        VALIDATOR.validate(report)
        assert [(item["code"], item.get("sheet"), item.get("address")) for item in report["diagnostics"]] == [("resource_limit", None, None)]
        assert report["values"] == {} and report["evaluated_cells"] == []
        assert (report["calculated_formulas"], report["unresolved_formulas"]) == (0, 4)
        assert (report["revision"], report["base_revision"], report["stale"]) == (0, None, False)


def test_early_ended_pass_lists_its_evaluated_cells_sorted(backend):
    # Depths: A1 and M1 are literals; Y1, Z1 and M2 read them; B2 reads Z1 and
    # M3 reads M2; M4 reads M3 (3 * 33000 = 99000 rows). A pass covers the
    # closure depth by depth. The first pass materialises A1, M1, Z1, M2, B2
    # and M3 (1 cell each), Y1 (10 rows) and M4 (99000 rows): 99016 result
    # cells, inside the budget of 100000.
    model = _sheet_model(
        {"A1": 10, "Y1": "=SEQUENCE(A1)", "Z1": "=A1+1", "B2": "=Z1+1", "M1": 1, "M2": "=M1+1", "M3": "=M2+1", "M4": "=SEQUENCE(M3*33000)"},
        inputs={"x": "A1"},
    )
    session = CalculationSession(model, backend=backend)
    first = session.calculate(context=CONTEXT)
    assert first["diagnostics"] == [] and first["evaluated_cells"] == ["S!B2", "S!M2", "S!M3", "S!M4", "S!Y1", "S!Z1"]
    # With x = 1000 the second pass runs the cells downstream of the input,
    # Y1 (1000 rows) and Z1 and then B2, and reuses the M column from its
    # base. Reaching M4 it holds 1 + 1 + 1000 + 1 + 1 + 1 + 1 = 1006 cells,
    # and 99000 more is over the budget, so it ends early. Y1 and Z1 ran
    # before B2; the list is sorted all the same.
    assert session.set_inputs({"x": 1000}, 0) == 1
    for _ in range(2):
        ended = session.calculate(context=CONTEXT)
        VALIDATOR.validate(ended)
        assert [(item["code"], item["sheet"], item["address"]) for item in ended["diagnostics"]] == [("resource_limit", "S", "M4")]
        assert ended["evaluated_cells"] == ["S!B2", "S!Y1", "S!Z1"]
        assert (ended["revision"], ended["base_revision"]) == (1, 0)
        # Z1 = 1000 + 1 and B2 = Z1 + 1.
        assert (ended["values"]["S!Z1"], ended["values"]["S!B2"]) == (1001, 1002)
        # Of the six formula cells only M4 has no value in this pass.
        assert "S!M4" not in ended["values"]
        assert (ended["calculated_formulas"], ended["unresolved_formulas"]) == (5, 1)


def test_bound_outputs_stop_at_the_output_result_budget(backend):
    # A1 is one array of 60000 cells; B1 is a scalar. Both fit the pass's
    # budget of 100000 result cells (60001). Bound outputs are copied in name
    # order under a second budget of the same size: `first` takes 60000,
    # `second` would make 120000 and is refused with resource_limit, and the
    # copy stops there, so `third` is not copied either. The legacy report
    # leaves both out of its outputs; calculation-report.v2 follows the same
    # rule and must not materialise the array once per binding.
    model = _sheet_model({"A1": "=SEQUENCE(60000)", "B1": "=1+1"}, outputs={"first": "A1", "second": "A1", "third": "B1"})
    session = CalculationSession(model, backend=backend)
    report = session.calculate(context=CONTEXT)
    VALIDATOR.validate(report)
    rows = {row["name"]: row for row in report["outputs"]}
    assert list(rows) == ["first", "second", "third"]
    assert rows["first"]["origin"] == "calculated"
    # SEQUENCE(n) is 1 to n down one column.
    assert len(rows["first"]["result"]["rows"]) == 60000
    assert (rows["first"]["result"]["rows"][0], rows["first"]["result"]["rows"][-1]) == ([1], [60000])
    assert (rows["second"]["origin"], rows["second"]["result"]) == ("not_calculated", None)
    assert (rows["third"]["origin"], rows["third"]["result"]) == ("not_calculated", None)
    assert [(item["code"], item["sheet"], item["address"]) for item in report["diagnostics"]] == [("resource_limit", "S", "A1")]
    # The cells themselves were calculated: `values` holds them once each.
    assert len(report["values"]["S!A1"]["rows"]) == 60000 and report["values"]["S!B1"] == 2
    assert (report["calculated_formulas"], report["unresolved_formulas"]) == (2, 0)
    # The pass covered its closure, so it is a completed pass and the next
    # one builds on it.
    again = session.calculate(context=CONTEXT)
    assert (again["base_revision"], again["evaluated_cells"]) == (0, [])
    assert [row["origin"] for row in again["outputs"]] == ["calculated", "not_calculated", "not_calculated"]
    # One array fits: with a single binding nothing is refused.
    single = CalculationSession(_sheet_model({"A1": "=SEQUENCE(60000)"}, outputs={"only": "A1"}), backend=backend).calculate(context=CONTEXT)
    assert single["diagnostics"] == [] and single["outputs"][0]["origin"] == "calculated"


def test_report_carries_revision_and_imported_cache_not_promoted(backend):
    _replay("imported-cache", "cache", backend)
    session, _ = _open("cache", backend)
    report = session.calculate(context=CONTEXT)
    report.pop("calc")  # the pinned clock is the only other number source

    def leaves(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from leaves(item)
        elif isinstance(value, list):
            for item in value:
                yield from leaves(item)
        else:
            yield value

    # The cached 999 and 5 of the two formula cells appear nowhere.
    found = [leaf for leaf in leaves(report) if _number(leaf)]
    assert 999 not in found and 5 not in found
    assert "999" not in json.dumps(report)


def test_shared_input_change_fixture(backend):
    for scenario in FIXTURE["scenarios"]:
        for doc_id in scenario["documents"]:
            _replay(scenario["id"], doc_id, backend)


def test_report_v2_schema_pins_the_version_fields():
    Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["properties"]["schema_version"] == {"const": 1}
    assert SCHEMA["properties"]["model_version"]["enum"] == [1, None]
    assert SCHEMA["additionalProperties"] is False
    assert sorted(SCHEMA["required"]) == sorted([
        "report_version", "stage", "engine", "behavior_profile", "schema_version", "model_version",
        "source_fingerprint", "revision", "base_revision", "calc", "inputs", "outputs", "values",
        "evaluated_cells", "stale", "diagnostics", "calculated_formulas", "unresolved_formulas",
    ])
    assert len(SCHEMA["required"]) == 18


def test_input_kinds_with_a_toolkit_document_is_invalid_request(backend):
    text = json.dumps(FIXTURE["documents"]["planner"]["document"])
    assert _code(CalculationSession.open, text, input_kinds={"unit_price": "number"}, backend=backend) == "invalid_request"


def test_canonical_open_without_input_kinds_lists_the_dropped_bindings(backend):
    data = (ROOT / "fixtures" / FIXTURE["documents"]["canonical"]["document_fixture"]).read_bytes()
    session = CalculationSession.open(data, backend=backend)
    assert {f"bindings.inputs.{name}" for name in ("unit_price", "unit_cost", "fixed_cost")} <= set(session.conversion.dropped)
    assert _code(session.set_inputs, {"unit_price": 25}, 0) == "invalid_input"
    assert session.revision == 0


def test_comparison_holds_integers_exactly():
    # The pinned clock is part of a pass's identity: one millisecond off is a
    # different context, though it is inside the tolerance for calculated
    # numbers (1e-12 of 1767225600000 is about 1.8).
    _same(1767225600000, 1767225600000, "calc.now_ms")
    with pytest.raises(AssertionError):
        _same(1767225600001, 1767225600000, "calc.now_ms")
    with pytest.raises(AssertionError):
        _same(4, 3, "revision")
    # A calculated value is a float and keeps the tolerance.
    _same(7400.0, 7400, "values.F2")
    _same(83.33333333333334, 1000 / 12, "values.F4")
    with pytest.raises(AssertionError):
        _same(7400.001, 7400, "values.F2")


def test_open_refusals_report_the_listed_code(backend):
    refusals = FIXTURE["open_refusals"]
    differ = []
    for case in refusals["cases"]:
        data = case["document"].encode("utf-8")
        assert _code(CalculationSession.open, data, backend=backend) == case[backend], case["name"]
        assert _code(CalculationSession.open, case["document"], backend=backend) == case[backend], case["name"]
        if case["python"] != case["rust"]:
            differ.append(case["name"])
    # The two engines' readers disagree on a code only where the contract
    # lists it.
    assert differ == refusals["reader_differences"]
    # A document that is not a JSON object is refused before input_kinds is
    # looked at; a JSON object that is not canonical refuses input_kinds.
    kinds = {"unit_price": "number"}
    for name in ("not-json", "byte-order-mark", "nan-constant", "array-root"):
        case = next(case for case in refusals["cases"] if case["name"] == name)
        assert _code(CalculationSession.open, case["document"].encode("utf-8"), input_kinds=kinds, backend=backend) == "invalid_model", name
    assert _code(CalculationSession.open, b"{}", input_kinds=kinds, backend=backend) == "invalid_request"
    assert _code(CalculationSession.open, 7, backend=backend) == "invalid_model"
    assert _code(CalculationSession.open, b"\xff\xfe", backend=backend) == "invalid_model"


def test_malformed_values_have_one_code_on_both_backends(backend):
    session, _ = _open("planner", backend)
    snapshot = session.snapshot()
    not_cell_values = ([1], {"a": 1}, {}, {"rows": [[1]]}, {"error": 5}, 10**400, float("nan"), float("inf"), "\ud800")
    # Checked before the revision is compared, so the code is the same at the
    # current revision and at a stale one.
    for value in not_cell_values:
        for revision in (0, 5):
            assert _code(session.set_inputs, {"unit_price": value}, revision) == "invalid_input", repr(value)
            assert _code(session.preview_inputs, {"unit_price": value}, revision, context=CONTEXT) == "invalid_input", repr(value)
    # A cell value the binding refuses is the binding's code, after the
    # revision check.
    for value in ("thirty", None, True, {"error": "#N/A"}):
        assert _code(session.set_inputs, {"unit_price": value}, 0) == "invalid_input", repr(value)
        assert _code(session.set_inputs, {"unit_price": value}, 5) == "revision_conflict", repr(value)
    # `values` maps input names, which are text, to values.
    for values in ({1: 9}, {None: 9}, {1: 9, "unit_price": 25}, [("unit_price", 25)], "unit_price", None):
        assert _code(session.set_inputs, values, 0) == "invalid_request", repr(values)
        assert _code(session.preview_inputs, values, 0, context=CONTEXT) == "invalid_request", repr(values)
    assert _code(session.set_inputs, {"\ud800": 1}, 0) == "invalid_input"
    assert (session.revision, session.snapshot()) == (0, snapshot)


def test_a_non_text_name_is_not_read_as_the_input_it_spells(backend):
    # The model binds an input named "1". The key 1 is not that name.
    model = _sheet_model({"C1": 1}, inputs={"1": "C1"})
    session = CalculationSession(model, backend=backend)
    assert _code(session.set_inputs, {1: 9}, 0) == "invalid_request"
    assert _code(session.preview_inputs, {1: 9}, 0, context=CONTEXT) == "invalid_request"
    assert session.revision == 0
    preview = session.preview_inputs({"1": 9}, 0, context=CONTEXT)
    assert preview["changes"] == [{"sheet": "S", "address": "C1", "before": 1, "after": 9}]
    assert session.set_inputs({"1": 9}, 0) == 1


def test_workers_and_context_are_checked_before_either_engine(backend):
    session, _ = _open("planner", backend)
    for workers in (-1, "2", 1.5, None, True):
        assert _code(session.calculate, workers=workers, context=CONTEXT) == "invalid_workers", repr(workers)
    # Above the engines' cap of eight workers is clamped, not refused, and 0
    # means one worker.
    for workers in (0, 2, 2**70):
        report = session.calculate(workers=workers, context=CONTEXT)
        assert report["values"]["Forecast!F2"] == 7400
    assert _code(session.calculate, context=FIXTURE["calc"]) == "invalid_request"
    assert _code(session.preview_inputs, {"unit_price": 25}, 0, context=FIXTURE["calc"]) == "invalid_request"


def test_engine_session_refuses_a_pinned_offset_outside_fourteen_hours(backend):
    # UTC offsets run from -14:00 to +14:00, 840 minutes either way
    # (docs/volatile-functions.md). A pinned context outside that is a
    # malformed request on both engine sessions.
    engine = importlib.import_module({"python": "workbook_forge.python_engine", "rust": "workbook_forge._native"}[backend])
    text = json.dumps(_sheet_model({"A1": "=NOW()"}))

    def calc(offset: int) -> str:
        return json.dumps({"now_ms": 0, "utc_offset_minutes": offset, "seed": "1"})

    for offset in (840, -840):
        report = json.loads(engine.Session(text).calculate_report(1, calc(offset)))
        assert report["calc"]["utc_offset_minutes"] == offset
    for offset in (841, -841, 2**31 - 1):
        with pytest.raises(ValueError) as raised:
            engine.Session(text).calculate_report(1, calc(offset))
        assert str(raised.value).startswith("invalid_request: "), offset


def test_expected_revision_must_be_an_unsigned_integer(backend):
    session, _ = _open("planner", backend)
    for bad in (True, -1, 2**64, None):
        assert _code(session.set_inputs, {"unit_price": 25}, bad) == "invalid_request"
        assert _code(session.preview_inputs, {"unit_price": 25}, bad) == "invalid_request"
    assert session.revision == 0
