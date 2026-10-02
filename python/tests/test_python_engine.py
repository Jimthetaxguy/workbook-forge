"""Exercise the independent Python workbook implementation without Rust calls."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event, Thread

import pytest

from workbook_forge import python_engine as engine


def model(cells=None, outputs=None, inputs=None, sheets=None):
    document = {"schema_version": 1, "sheets": sheets or [{"id": "Data", "name": "Data", "cells": cells or {}}], "outputs": {name: {"sheet": "Data", "address": address} for name, address in (outputs or {}).items()}, "inputs": inputs or {}}
    return json.dumps(document)


def report(session, workers=1):
    return json.loads(session.calculate(workers))


def edit(session, edits, expected=None):
    return session.apply(json.dumps(edits), expected)


def test_scenario_and_input_changes_are_computed_in_python():
    session = engine.Session(engine.scenario())
    initial = report(session)
    assert initial["outputs"] == {"revenue": 7400, "profit": 1440, "break_even_units": 1000 / 12}
    session.set_inputs('{"unit_price":25,"fixed_cost":1200}')
    result = report(session, 4)
    assert not result["diagnostics"]
    assert result["outputs"]["revenue"] == 9250
    assert result["outputs"]["profit"] == 2690
    assert result["outputs"]["break_even_units"] == 1200 / 17
    assert result["outputs"] == json.loads(engine.calculate(session.snapshot()))["outputs"]


def test_formula_reference_to_blank_and_error_propagation():
    session = engine.Session(model({"A1": {}, "B1": {"formula": "=A1"}, "C1": {"formula": "=B1+1"}, "D1": {"formula": "=1/0"}}, {"blank": "A1", "zero": "B1", "one": "C1", "error": "D1"}))
    result = report(session)
    assert result["outputs"] == {"blank": None, "zero": 0, "one": 1, "error": {"error": "#DIV/0!"}}
    assert result["diagnostics"] == []


def test_no_positive_margin_has_no_finite_break_even():
    session = engine.Session(engine.scenario())
    for price in (8, 7, 0):
        session.set_inputs(json.dumps({"unit_price": price}))
        assert report(session)["outputs"]["break_even_units"] == {"error": "#N/A"}


def test_installed_scalar_caches_are_never_authoritative():
    session = engine.Session(model({"A1": {"value": 2}, "B1": {"formula": "=A1+1", "cached_value": 999}, "C1": {"formula": "=B1*2"}}, {"out": "C1"}))
    assert report(session)["outputs"]["out"] == 6
    assert json.loads(session.snapshot())["sheets"][0]["cells"]["B1"]["cached_value"] == 999


def test_unknown_and_oversized_formulas_only_block_selected_closure():
    source = model({"A1": {"value": 3}, "B1": {"formula": "=UNKNOWN(A1)", "cached_value": 77}, "C1": {"formula": "=SUM(A1:XFD1048576)"}}, {"safe": "A1"})
    assert json.loads(engine.calculate(source))["diagnostics"] == []
    inspection = json.loads(engine.inspect(source))
    assert {item["code"] for item in inspection["diagnostics"]} == {"unsupported_formula", "resource_limit"}
    data = json.loads(source)
    data["outputs"]["unknown"] = {"sheet": "Data", "address": "B1"}
    result = json.loads(engine.calculate(json.dumps(data)))
    assert result["outputs"] == {"safe": 3}
    assert result["diagnostics"][0]["code"] == "unsupported_formula"


def test_preserved_error_codes_and_blocked_cell_diagnostics():
    session = engine.Session(model({"A1": {"value": {"error": "#SPILL!"}}, "B1": {"value": 2, "blocked_reason": "table formula region"}, "C1": {"value": {"error": "#N/A!"}}, "D1": {"formula": "=IFNA(C1,4)"}}, {"out": "D1"}))
    assert report(session)["outputs"]["out"] == 4
    before = session.snapshot()
    with pytest.raises(engine.ToolkitError, match="unsupported_edit"):
        edit(session, [{"sheet": "Data", "address": "B1", "value": 3}])
    assert session.snapshot() == before
    assert "#SPILL!" in before and "#N/A!" in before


def test_array_results_are_explicit_and_array_dependencies_refused():
    source = model({"A1": {"formula": "=SEQUENCE(2,2)"}, "B1": {"formula": "=A1+1"}}, {"array": "A1"})
    assert json.loads(engine.calculate(source))["outputs"]["array"] == {"rows": [[1, 2], [3, 4]]}
    document = json.loads(source)
    document["outputs"]["invalid"] = {"sheet": "Data", "address": "B1"}
    result = json.loads(engine.calculate(json.dumps(document)))
    assert any(item["code"] == "unsupported_array_reference" for item in result["diagnostics"])


def test_incremental_dependency_replacement_and_unrelated_work():
    session = engine.Session(model({"A1": {"value": 1}, "A2": {"value": 10}, "B1": {"formula": "=A1+1"}, "C1": {"formula": "=B1*2"}, "D1": {"formula": "=A2+1"}}, {"main": "C1", "other": "D1"}))
    assert len(report(session)["evaluated_cells"]) == 3
    edit(session, [{"sheet": "Data", "address": "A1", "value": 2}])
    assert report(session)["evaluated_cells"] == ["Data!B1", "Data!C1"]
    edit(session, [{"sheet": "Data", "address": "B1", "formula": "=A2+1"}])
    assert report(session)["outputs"]["main"] == 22
    edit(session, [{"sheet": "Data", "address": "A1", "value": 3}])
    assert report(session)["evaluated_cells"] == []
    edit(session, [{"sheet": "Data", "address": "A2", "value": 20}])
    incremental = report(session)
    assert incremental["evaluated_cells"] == ["Data!B1", "Data!C1", "Data!D1"]
    assert incremental["values"] == json.loads(engine.calculate(session.snapshot()))["values"]


def test_atomic_batch_rolls_back_bounds_bad_formula_and_duplicate_edits():
    session = engine.Session(engine.scenario())
    for bad in ({"sheet": "Assumptions", "address": "B2", "value": -1}, {"sheet": "Forecast", "address": "E2", "formula": "=("}, {"sheet": "Assumptions", "address": "B1", "value": 5}):
        before = session.snapshot()
        with pytest.raises(ValueError):
            edit(session, [{"sheet": "Assumptions", "address": "B1", "value": 25}, bad])
        assert session.snapshot() == before


def test_revision_conflicts_and_explicit_null_clear():
    session = engine.Session(model({"A1": {"value": 2}}))
    assert edit(session, [{"sheet": "Data", "address": "A1", "value": None}], 0) == 1
    assert json.loads(session.snapshot())["sheets"][0]["cells"]["A1"] == {"value": None}
    with pytest.raises(ValueError, match="revision_conflict"):
        edit(session, [], 0)
    with pytest.raises(ValueError):
        session.set_inputs("{}", True)


def test_old_calculation_cannot_publish_after_concurrent_edit(monkeypatch):
    session = engine.Session(engine.scenario())
    started, finish = Event(), Event()
    original = engine._calculate_document

    def gated(*args, **kwargs):
        started.set()
        assert finish.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(engine, "_calculate_document", gated)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(session.calculate)
        assert started.wait(5)
        session.set_inputs('{"unit_price":25}')
        finish.set()
        stale = json.loads(pending.result())
    assert stale["stale"] and stale["revision"] == 0
    assert stale["outputs"]["revenue"] == 7400
    assert session._published is None
    current = report(session)
    assert not current["stale"] and current["revision"] == 1
    assert current["outputs"]["revenue"] == 9250


def test_concurrent_atomic_edits_never_publish_mixed_cell_pair():
    session = engine.Session(model({"A1": {"value": 0}, "B1": {"value": 0}, "C1": {"formula": "=A1-B1"}}, {"difference": "C1"}))
    results = []

    def calculate_many():
        for _ in range(30):
            results.append(report(session, 2))

    thread = Thread(target=calculate_many)
    thread.start()
    for value in range(1, 20):
        edit(session, [{"sheet": "Data", "address": "A1", "value": value}, {"sheet": "Data", "address": "B1", "value": value}])
    thread.join()
    assert all(item["outputs"]["difference"] == 0 and not item["diagnostics"] for item in results)


def test_required_input_can_be_authored_then_filled():
    session = engine.Session(model(inputs={"amount": {"sheet": "Data", "address": "A1", "kind": "number"}}))
    assert report(session)["diagnostics"][0]["code"] == "invalid_input"
    session.set_inputs('{"amount":7}')
    assert report(session)["diagnostics"] == []
    with pytest.raises(ValueError):
        edit(session, [{"sheet": "Data", "address": "A1", "value": True}])


@pytest.mark.parametrize("invalid", [True, "20", None, -1, {"error": "#N/A"}])
def test_input_types_and_bounds_are_enforced_on_overrides(invalid):
    session = engine.Session(engine.scenario())
    before = session.snapshot()
    with pytest.raises(ValueError):
        session.set_inputs(json.dumps({"unit_price": invalid}))
    assert session.snapshot() == before


def test_text_choices_remain_case_sensitive():
    session = engine.Session(model({"A1": {"value": "Blue"}}, inputs={"color": {"sheet": "Data", "address": "A1", "kind": "text", "choices": ["Blue", "Red"]}}))
    with pytest.raises(ValueError, match="allowed choice"):
        session.set_inputs('{"color":"blue"}')
    session.set_inputs('{"color":"Red"}')


@pytest.mark.parametrize("workers", [1, 8])
def test_materialized_array_budget_and_output_alias_budget(workers):
    result = json.loads(engine.calculate(model({"A1": {"formula": "=SEQUENCE(60000)"}, "B1": {"formula": "=SEQUENCE(60000)"}}), workers))
    assert any(item["code"] == "resource_limit" for item in result["diagnostics"])
    retained = sum(sum(map(len, value["rows"])) if isinstance(value, dict) and "rows" in value else 1 for value in result["values"].values())
    assert retained <= engine.MAX_RESULT_CELLS
    aliased = json.loads(engine.calculate(model({"A1": {"formula": "=SEQUENCE(60000)"}}, {"one": "A1", "two": "A1"}), workers))
    assert len(aliased["outputs"]) == 1
    assert any(item["code"] == "resource_limit" for item in aliased["diagnostics"])


def test_cell_shaped_sheet_name_is_supported_without_mutating_source():
    source = model(sheets=[{"id": "1", "name": "S1", "cells": {"A1": {"value": 3}}}, {"id": "2", "name": "S2", "cells": {"B1": {"formula": "=S1!A1+2"}}}])
    session = engine.Session(source)
    result = report(session)
    assert result["diagnostics"] == []
    assert result["values"]["S2!B1"] == 5
    assert json.loads(session.snapshot())["sheets"][1]["cells"]["B1"]["formula"] == "=S1!A1+2"


@pytest.mark.parametrize("source", ['{"sheets":[],"revision":true}', '{"sheets":[],"schema_version":true}', '{"sheets":[],"extra":1}', '{"schema_version":1,"sheets":[{"id":"1","name":"S","cells":{"a1":{"value":1}}}]}'])
def test_model_contract_rejects_invalid_scalar_and_schema_types(source):
    with pytest.raises(ValueError):
        engine.Session(source)


def test_a_document_without_a_version_is_refused_with_a_typed_diagnostic():
    source = '{"sheets":[{"id":"1","name":"S","cells":{"A1":{"value":1}}}],"outputs":{}}'
    for entry in (engine.Session, engine.calculate, engine.inspect):
        with pytest.raises(engine.ToolkitError) as caught:
            entry(source)
        assert caught.value.code == "schema_version"
        assert caught.value.message == "schema_version is required"


def test_integral_reference_arguments_and_stored_error_messages():
    session = engine.Session(model({"A1": {"value": 11}, "A2": {"value": 22}, "B1": {"value": 2}, "C1": {"formula": "=INDEX(A1:A2,B1)"}, "D1": {"value": {"error": "#NAME?", "message": "unsupported function user-note"}}, "E1": {"formula": "=D1"}}, {"index": "C1", "error": "E1"}))
    result = report(session)
    assert not result["diagnostics"]
    assert result["outputs"] == {"index": 22, "error": {"error": "#NAME?"}}


def test_unsupported_let_name_is_a_capability_diagnostic_not_parse_error():
    source = model({"A1": {"formula": "=LET(x,1,x)"}}, {"out": "A1"})
    result = json.loads(engine.calculate(source))
    assert result["outputs"] == {}
    assert result["diagnostics"][0]["code"] == "unsupported_formula"
    assert "unsupported name" in result["diagnostics"][0]["message"]
