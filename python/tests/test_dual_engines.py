"""One behavioral contract for independent Python and Rust workbook engines.

These checks supplement the shared formula corpus. Native tests may be skipped
in a pure-Python installation; Python behavior and the no-native subprocess
proof are always required. Differential agreement is not an Excel oracle.
"""
from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import textwrap
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENGINE_MODULES = {
    "python": "workbook_forge.python_engine",
    "rust": "workbook_forge._native",
}


def _engine(name):
    module_name = ENGINE_MODULES[name]
    if name == "rust" and importlib.util.find_spec(module_name) is None:
        pytest.skip("optional Rust extension is not installed")
    return importlib.import_module(module_name)


@pytest.fixture(params=["python", "rust"])
def engine(request):
    return _engine(request.param)


def _model(cells, outputs=None, *, inputs=None, sheets=None):
    return {
        "schema_version": 1,
        "revision": 0,
        "sheets": sheets or [{"id": "data", "name": "Data", "cells": cells}],
        "inputs": inputs or {},
        "outputs": {
            name: {"sheet": "Data", "address": address}
            for name, address in (outputs or {}).items()
        },
    }


def _session(engine, document):
    return engine.Session(json.dumps(document, allow_nan=False))


def _calculate(session, workers=1):
    return json.loads(session.calculate(workers))


def _apply(session, edits, expected=None):
    return session.apply(json.dumps(edits, allow_nan=False), expected)


def _diagnostics(report):
    # Wording is not part of the cross-language behavior contract.
    return sorted((item["code"], item.get("sheet"), item.get("address")) for item in report["diagnostics"])


def _semantic(report):
    return {
        "revision": report["revision"],
        "outputs": report["outputs"],
        "values": report["values"],
        "diagnostics": _diagnostics(report),
        "stale": report["stale"],
    }


def test_typed_blank_error_date_and_array_results(engine):
    formulas = {
        "B1": "=A1", "C1": '=IF(TRUE,"",1)', "D1": "=ISBLANK(A1)",
        "E1": "=ISBLANK(C1)", "F1": "=1/0", "G1": "=IF(FALSE,1/0,42)",
        "H1": "=DATE(1900,3,1)", "I1": "=SEQUENCE(2,2,1,1)",
    }
    cells = {"A1": {"value": None}, **{address: {"formula": formula} for address, formula in formulas.items()}}
    outputs = {address: address for address in cells}
    report = _calculate(_session(engine, _model(cells, outputs)), workers=4)
    assert report["diagnostics"] == []
    values = report["outputs"]
    assert values["A1"] is None
    # Formula references to blank cells project to zero; the authored blank stays distinct.
    assert values["B1"] == 0
    assert values["C1"] == ""
    assert values["D1"] is True and values["E1"] is False
    assert values["F1"] == {"error": "#DIV/0!"}
    assert values["G1"] == 42
    assert values["H1"] == 61
    assert values["I1"] == {"rows": [[1, 2], [3, 4]]}
    assert report["stale"] is False


def test_authored_errors_propagate_as_values_not_engine_diagnostics(engine):
    model = _model({"A1": {"value": {"error": "#N/A"}}, "B1": {"formula": "=A1+1"}, "C1": {"formula": "=IFERROR(B1,7)"}}, {"error": "B1", "handled": "C1"})
    report = _calculate(_session(engine, model))
    assert not report["diagnostics"]
    assert report["outputs"] == {"error": {"error": "#N/A"}, "handled": 7}


@pytest.mark.parametrize("sheet_name", ["Q1", "A1", "XFD1048576"])
def test_cell_shaped_sheet_names_resolve_and_invalidate(engine, sheet_name):
    source = f"={sheet_name}!A1+1"
    document = _model({}, sheets=[
        {"id": "source", "name": sheet_name, "cells": {"A1": {"value": 7}}},
        {"id": "summary", "name": "Summary", "cells": {"B1": {"formula": source}}},
    ])
    document["outputs"] = {"result": {"sheet": "Summary", "address": "B1"}}
    session = _session(engine, document)
    first = _calculate(session)
    assert first["outputs"] == {"result": 8}
    assert first["diagnostics"] == []
    assert json.loads(session.snapshot())["sheets"][1]["cells"]["B1"]["formula"] == source
    _apply(session, [{"sheet": sheet_name, "address": "A1", "value": 10}], 0)
    changed = _calculate(session)
    assert changed["outputs"] == {"result": 11}
    assert changed["evaluated_cells"] == ["Summary!B1"]


def test_atomic_edits_expected_revision_and_detached_snapshots(engine):
    session = _session(engine, _model({"A1": {"value": 2}, "B1": {"formula": "=A1+1"}}, {"result": "B1"}))
    initial = json.loads(session.snapshot())
    detached = json.loads(session.snapshot())
    detached["sheets"][0]["cells"]["A1"]["value"] = 99
    assert _calculate(session)["outputs"]["result"] == 3
    assert _apply(session, [], expected=0) == 0
    for invalid in (
        [{"sheet": "Data", "address": "A1", "value": 5}, {"sheet": "Missing", "address": "B1", "value": 1}],
        [{"sheet": "Data", "address": "A1", "value": 5}, {"sheet": "data", "address": "$A$1", "value": 6}],
        [{"sheet": "Data", "address": "A1", "value": 5, "formula": "=1"}],
    ):
        with pytest.raises(ValueError):
            _apply(session, invalid, expected=0)
        assert json.loads(session.snapshot()) == initial
    assert _apply(session, [{"sheet": "Data", "address": "A1", "value": 5}], expected=0) == 1
    committed = json.loads(session.snapshot())
    with pytest.raises(ValueError):
        _apply(session, [{"sheet": "Data", "address": "A1", "value": 9}], expected=0)
    assert json.loads(session.snapshot()) == committed
    assert _calculate(session)["outputs"]["result"] == 6


def _constrained_model():
    return _model(
        {"A1": {"value": 4}, "B1": {"value": "Base"}, "C1": {"value": True}},
        {"result": "A1"},
        inputs={
            "quantity": {"sheet": "Data", "address": "A1", "kind": "number", "min": 1, "max": 10},
            "mode": {"sheet": "Data", "address": "B1", "kind": "text", "choices": ["Base", "Upside"]},
            "enabled": {"sheet": "Data", "address": "C1", "kind": "boolean", "required": False},
        },
    )


@pytest.mark.parametrize("invalid", [
    {"quantity": True}, {"quantity": "4"}, {"quantity": 0}, {"quantity": 11},
    {"quantity": None}, {"mode": "base"}, {"enabled": 1}, {"unknown": 1},
    {"quantity": 7, "mode": "unavailable"},
])
def test_input_constraints_are_typed_and_atomic(engine, invalid):
    session = _session(engine, _constrained_model())
    before = session.snapshot()
    with pytest.raises(ValueError):
        session.set_inputs(json.dumps(invalid), 0)
    assert session.snapshot() == before
    assert _calculate(session)["outputs"]["result"] == 4


def test_optional_blank_and_authored_defaults(engine):
    session = _session(engine, _constrained_model())
    assert session.set_inputs(json.dumps({"enabled": None, "quantity": 10}), 0) == 1
    state = json.loads(session.snapshot())
    assert state["sheets"][0]["cells"]["C1"]["value"] is None
    assert state["sheets"][0]["cells"]["B1"]["value"] == "Base"
    assert _calculate(session)["outputs"]["result"] == 10
    missing = _constrained_model()
    missing["sheets"][0]["cells"]["A1"]["value"] = None
    report = _calculate(_session(engine, missing))
    assert "invalid_input" in {item["code"] for item in report["diagnostics"]}
    assert report["outputs"] == {}


def test_incremental_invalidation_rebuilds_changed_dependency_edges(engine):
    session = _session(engine, _model({
        "A1": {"value": 2}, "B1": {"formula": "=A1+1"}, "C1": {"formula": "=B1*2"},
        "D1": {"value": 100}, "E1": {"formula": "=D1+1"},
    }, {"left": "C1", "right": "E1"}))
    first = _calculate(session, 4)
    assert first["outputs"] == {"left": 6, "right": 101}
    assert set(first["evaluated_cells"]) == {"Data!B1", "Data!C1", "Data!E1"}
    _apply(session, [{"sheet": "Data", "address": "A1", "value": 3}])
    changed = _calculate(session, 4)
    assert changed["outputs"] == {"left": 8, "right": 101}
    assert set(changed["evaluated_cells"]) == {"Data!B1", "Data!C1"}
    _apply(session, [{"sheet": "Data", "address": "B1", "formula": "=D1+5"}])
    assert _calculate(session)["outputs"] == {"left": 210, "right": 101}
    _apply(session, [{"sheet": "Data", "address": "A1", "value": 999}])
    obsolete = _calculate(session)
    assert obsolete["evaluated_cells"] == []
    assert obsolete["outputs"] == {"left": 210, "right": 101}
    _apply(session, [{"sheet": "Data", "address": "D1", "value": 5}])
    changed = _calculate(session, 8)
    assert changed["outputs"] == {"left": 20, "right": 6}
    fresh = json.loads(engine.calculate(session.snapshot(), 1))
    assert _semantic(changed) == _semantic(fresh)


def test_cycles_and_unsupported_formulas_only_block_selected_closures(engine):
    document = _model({
        "A1": {"value": 4}, "B1": {"formula": "=A1+1"},
        "C1": {"formula": "=D1+1"}, "D1": {"formula": "=C1+1"},
        "E1": {"formula": "=UNSUPPORTED_FUNCTION(1)"},
    }, {"result": "B1"})
    report = _calculate(_session(engine, document))
    assert report["outputs"] == {"result": 5} and not report["diagnostics"]
    cyclic = copy.deepcopy(document)
    cyclic["outputs"]["result"]["address"] = "C1"
    report = _calculate(_session(engine, cyclic))
    assert report["outputs"] == {}
    assert {item["code"] for item in report["diagnostics"]} == {"dependency_cycle"}
    unsupported = copy.deepcopy(document)
    unsupported["outputs"]["result"]["address"] = "E1"
    report = _calculate(_session(engine, unsupported))
    assert report["outputs"] == {}
    assert "unsupported_formula" in {item["code"] for item in report["diagnostics"]}


def test_blocked_imported_content_never_uses_a_stored_cache_as_input(engine):
    document = _model({"A1": {"value": None, "cached_value": 100, "blocked_reason": "unsupported imported formula region"}, "B1": {"formula": "=A1+1"}}, {"result": "B1"})
    session = _session(engine, document)
    report = _calculate(session)
    assert report["outputs"] == {}
    assert report["diagnostics"]
    before = session.snapshot()
    with pytest.raises(ValueError):
        _apply(session, [{"sheet": "Data", "address": "A1", "value": 1}])
    assert session.snapshot() == before


def test_mixed_reference_copy_retains_axis_anchors(engine):
    formula = "=$A1+B$2+'Rates Sheet'!$C$3"
    copied = engine.copy_formula(formula, 1, 1)
    analysis = json.loads(engine.analyze_formula(copied))
    starts = [reference["start"] for reference in analysis["references"]]
    assert {(ref["row"], ref["column"], ref["absolute_row"], ref["absolute_column"]) for ref in starts} == {
        (2, 1, False, True), (2, 3, True, False), (3, 3, True, True),
    }
    assert any(ref.get("sheet") == "Rates Sheet" for ref in starts)
    for formula, rows, columns in (("=A1", -1, 0), ("=A1", 0, -1), ("=XFD1048576", 1, 0), ("=XFD1048576", 0, 1)):
        with pytest.raises(ValueError):
            engine.copy_formula(formula, rows, columns)
    anchored = engine.copy_formula("=$A$1", -100, -100)
    assert "$A$1" in anchored


@pytest.mark.parametrize("change", [
    lambda d: d.update(schema_version=2),
    lambda d: d.update(unknown_field=True),
    lambda d: d.update(revision=-1),
    lambda d: d.update(revision=True),
    lambda d: d.update(schema_version=True),
    lambda d: d["sheets"][0].update(name="invalid/name"),
    lambda d: d["sheets"][0]["cells"].update({"A0": {"value": 1}}),
    lambda d: d["sheets"][0]["cells"].update({"XFE1": {"value": 1}}),
    lambda d: d["sheets"][0]["cells"].update({"A1048577": {"value": 1}}),
    lambda d: d["sheets"][0]["cells"].update({"B1": {"value": {"error": "#NOT-EXCEL!"}}}),
    lambda d: d["sheets"][0]["cells"].update({"B1": {"value": "x" * 32768}}),
])
def test_document_schema_and_resource_boundaries(engine, change):
    document = _model({"A1": {"value": 1}})
    change(document)
    with pytest.raises(ValueError):
        _session(engine, document)


def test_oversized_edit_batch_is_atomic(engine):
    session = _session(engine, _model({"A1": {"value": 1}}))
    before = session.snapshot()
    with pytest.raises(ValueError):
        _apply(session, [{"sheet": "Data", "address": f"A{row}", "value": row} for row in range(1, 10002)])
    assert session.snapshot() == before



def test_parallel_reads_observe_atomic_edit_batches(engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    cells = {"A1": {"value": 0}, "A2": {"value": 0}}
    cells.update({f"B{row}": {"formula": "=A1-A2"} for row in range(1, 17)})
    session = _session(engine, _model(cells))
    start = Barrier(2)

    def edit():
        start.wait(timeout=10)
        for value in range(1, 25):
            _apply(session, [{"sheet": "Data", "address": "A1", "value": value},
                             {"sheet": "Data", "address": "A2", "value": value}])

    def read():
        start.wait(timeout=10)
        reports = []
        for _ in range(24):
            report = _calculate(session, workers=4)
            assert not report["diagnostics"]
            assert all(report["values"][f"Data!B{row}"] == 0 for row in range(1, 17))
            reports.append(report)
        return reports

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer, reader = pool.submit(edit), pool.submit(read)
        writer.result(timeout=30)
        reports = reader.result(timeout=30)
    assert all(0 <= report["revision"] <= 24 for report in reports)
    final = _calculate(session)
    assert final["revision"] == 24 and final["stale"] is False
    assert final["values"]["Data!A1"] == final["values"]["Data!A2"] == 24
    assert final["values"]["Data!B1"] == 0


@pytest.mark.parametrize("nonfinite", ["NaN", "Infinity", "1e999"])
def test_nonfinite_json_input_cannot_enter_a_session(engine, nonfinite):
    source = '{"sheets":[{"id":"d","name":"Data","cells":{"A1":{"value":' + nonfinite + '}}}]}'
    with pytest.raises(ValueError):
        engine.Session(source)


def _assert_fixture_value(actual, expected):
    if type(expected) in (int, float):
        assert type(actual) in (int, float)
        assert actual == pytest.approx(expected, rel=1e-12, abs=1e-12)
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected)
        for actual_item, expected_item in zip(actual, expected, strict=True):
            _assert_fixture_value(actual_item, expected_item)
    elif isinstance(expected, dict):
        assert isinstance(actual, dict) and actual.keys() == expected.keys()
        for key in expected:
            _assert_fixture_value(actual[key], expected[key])
    else:
        assert type(actual) is type(expected) and actual == expected


def test_formula_corpus_survives_workbook_model_boundaries(engine):
    # The source corpus remains authoritative. Only these three resource cases
    # use a different boundary: workbook model/graph validation refuses them
    # before the standalone evaluator can return an ordinary Excel error value.
    model_refusals = {"text-literal-supplementary-utf16-limit"}
    graph_refusals = {
        "sort-range-over-100000-cell-profile",
        "unique-range-over-100000-cell-profile",
    }
    seen_refusals = set()
    cases = [json.loads(line) for line in (ROOT / "fixtures/formula-cases.jsonl").read_text().splitlines()]
    for case in cases:
        sheets = {"Data": {}}
        for reference, value in case["cells"].items():
            sheet, address = reference.rsplit("!", 1) if "!" in reference else ("Data", reference)
            sheets.setdefault(sheet, {})[address] = {"value": value}
        # Keep unqualified references in Data and avoid the fixture inputs.
        assert "XFD1048576" not in sheets["Data"], case["id"]
        sheets["Data"]["XFD1048576"] = {"formula": case["formula"]}
        document = {
            "sheets": [{"id": str(index), "name": name, "cells": cells} for index, (name, cells) in enumerate(sheets.items())],
            "outputs": {"result": {"sheet": "Data", "address": "XFD1048576"}},
        }
        if case["id"] in model_refusals:
            with pytest.raises(ValueError, match="resource_limit"):
                engine.calculate(json.dumps(document))
            seen_refusals.add(case["id"])
            continue
        report = json.loads(engine.calculate(json.dumps(document)))
        if case["id"] in graph_refusals:
            assert {item["code"] for item in report["diagnostics"]} == {"resource_limit"}, case["id"]
            assert report["outputs"] == {}, case["id"]
            seen_refusals.add(case["id"])
            continue
        assert report["diagnostics"] == [], case["id"]
        expected = case["expected"]
        kind, target = next(iter(expected.items()))
        if kind == "array":
            target = {"rows": target}
        elif kind == "error":
            target = {"error": target}
        elif kind == "blank":
            # A scalar blank formula result is stored as zero in a worksheet.
            target = 0
        elif kind == "exact_number":
            actual = report["outputs"]["result"]
            assert type(actual) in (int, float) and actual == int(target), case["id"]
            continue
        else:
            assert kind in {"number", "text", "bool"}, case["id"]
        try:
            _assert_fixture_value(report["outputs"]["result"], target)
        except AssertionError as error:
            raise AssertionError(f"workbook corpus case {case['id']}: {error}") from error
    assert seen_refusals == model_refusals | graph_refusals


def test_independent_engines_agree_after_seeded_mixed_edits():
    engines = [_engine(name) for name in ("python", "rust")]
    document = _model({
        "A1": {"value": 2}, "A2": {"value": 3}, "A3": {"value": 5},
        "B1": {"formula": "=SUM(A1:A3)"}, "B2": {"formula": "=B1*2"},
        "C1": {"formula": '=IF(B2>0,"positive","nonpositive")'},
    }, {"sum": "B1", "double": "B2", "label": "C1"})
    sessions = [_session(engine, document) for engine in engines]
    rng = random.Random(731)
    for index in range(16):
        edits = [{"sheet": "Data", "address": f"A{rng.randint(1,3)}", "value": rng.randint(-20,20)}]
        if index in {5, 11}:
            edits.append({"sheet": "Data", "address": "B2", "formula": "=B1*3" if index == 5 else "=B1-A1"})
        reports = []
        for engine, session in zip(engines, sessions):
            _apply(session, edits, expected=index)
            report = _calculate(session, workers=1 if index % 2 else 4)
            assert not report["diagnostics"]
            fresh = json.loads(engine.calculate(session.snapshot(), 1))
            assert _semantic(report) == _semantic(fresh)
            reports.append(report)
        assert _semantic(reports[0]) == _semantic(reports[1])
        assert reports[0]["evaluated_cells"] == reports[1]["evaluated_cells"]


@pytest.mark.parametrize("backend", ["python", "rust"])
def test_public_backend_scenario_and_xlsx_roundtrip(backend, tmp_path):
    _engine(backend)
    from workbook_forge.toolkit import operating_scenario
    from workbook_forge.xlsx import export_xlsx, import_xlsx

    model = operating_scenario(backend=backend)
    model.set_inputs({"unit_price": 25})
    report = model.calculate(workers=2)
    assert report["outputs"] == pytest.approx({"revenue": 9250, "profit": 3290, "break_even_units": 1000 / 17})
    document = model.to_dict()
    path = export_xlsx(model, tmp_path / f"{backend}.xlsx")
    reopened = import_xlsx(path, inputs=document["inputs"], outputs=document["outputs"], backend=backend)
    assert reopened.calculate()["outputs"] == report["outputs"]
    model.set_inputs({"unit_price": 8})
    assert model.calculate()["outputs"]["break_even_units"] == {"error": "#N/A"}


@pytest.mark.parametrize("backend", ["python", "rust"])
def test_imported_1904_preservation_boundary_is_backend_independent(backend, tmp_path):
    _engine(backend)
    from test_workbook import make_xlsx, write_parts
    from workbook_forge.workbook import UnsupportedWorkbook, Workbook
    from workbook_forge.xlsx import export_xlsx, import_xlsx

    source = tmp_path / "date1904.xlsx"
    parts = make_xlsx(source)
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(b"<sheets>", b'<workbookPr date1904="1"/><sheets>')
    write_parts(source, parts)
    model = import_xlsx(source, outputs={"literal": {"sheet": "Sheet1", "address": "A1"}}, backend=backend)
    before = model.to_dict()
    with pytest.raises(UnsupportedWorkbook, match="1904"):
        model.set_formula("Sheet1", "C1", "=DATE(2024,1,1)")
    assert model.to_dict() == before
    assert model.calculate()["outputs"] == {"literal": "hello"}
    output = export_xlsx(model, tmp_path / "preserved.xlsx")
    with zipfile.ZipFile(output) as archive:
        assert archive.read("xl/worksheets/sheet1.xml") == parts["xl/worksheets/sheet1.xml"]
    with Workbook.open(output) as workbook:
        assert workbook._uses_1904_date_system
    selected = import_xlsx(source, outputs={"formula": {"sheet": "Sheet1", "address": "B1"}}, backend=backend)
    assert selected.calculate()["diagnostics"]


def test_python_workflow_never_imports_native_in_a_fresh_process(tmp_path):
    program = textwrap.dedent('''
        import importlib.abc
        import json
        from pathlib import Path
        import sys

        class RejectNative(importlib.abc.MetaPathFinder):
            attempts = []
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "workbook_forge._native":
                    self.attempts.append(fullname)
                    raise ImportError("native deliberately unavailable in independence test")
                return None

        blocker = RejectNative()
        sys.meta_path.insert(0, blocker)
        from workbook_forge.toolkit import WorkbookModel, operating_scenario
        from workbook_forge.expressions import CellReference, Expression
        from workbook_forge.xlsx import export_xlsx, import_xlsx

        model = WorkbookModel(["Data"])
        model.apply([{"sheet":"Data", "address":"A1", "value":2},
                     {"sheet":"Data", "address":"A2", "value":7},
                     {"sheet":"Data", "address":"B1", "formula":"=A1+$A$1"}])
        model.copy_formula("Data", "B1", "B2")
        assert model.calculate()["values"]["Data!B2"] == 9
        expression = Expression.reference(CellReference(1,1,column_absolute=True)) + Expression.literal(3)
        assert "$A2" in expression.copy(rows=1).formula
        scenario = operating_scenario()
        scenario.set_inputs({"unit_price":25})
        report = scenario.calculate(workers=2)
        assert report["outputs"]["profit"] == 3290
        document = scenario.to_dict()
        path = export_xlsx(scenario, Path(sys.argv[1]) / "pure-python.xlsx")
        extracted = import_xlsx(path, inputs=document["inputs"], outputs=document["outputs"])
        assert extracted.calculate()["outputs"] == report["outputs"]
        assert blocker.attempts == [], blocker.attempts
        assert "workbook_forge._native" not in sys.modules
        print(json.dumps({"profit":report["outputs"]["profit"],"native_import_attempts":blocker.attempts}))
    ''')
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "python")
    result = subprocess.run([sys.executable, "-c", program, str(tmp_path)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"profit": 3290, "native_import_attempts": []}
