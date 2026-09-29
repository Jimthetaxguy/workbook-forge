"""Behavior, byte-budget and preservation tests for the Python agent adapter."""
import json
from threading import Thread
from xml.etree import ElementTree as ET
import zipfile

import jsonschema
import pytest

from workbook_forge.agent import AgentWorkbook, MAX_RESPONSE_BYTES, operation_catalog
from workbook_forge.toolkit import WorkbookModel, operating_scenario
from workbook_forge.xlsx import export_xlsx, import_xlsx


def adapter(tmp_path, *, cells=None, outputs=None):
    if cells is None:
        model = operating_scenario()
    else:
        model = WorkbookModel(document={"sheets": [{"id": "sheet", "name": "Data", "cells": cells}], "outputs": {name: {"sheet": "Data", "address": address} for name, address in (outputs or {}).items()}})
    return AgentWorkbook(model, output_dir=tmp_path), model


def success(agent, operation, arguments=None):
    response = agent.call(operation, arguments, request_id="test")
    assert response["ok"], response
    schema = next(item["output_schema"] for item in operation_catalog()["operations"] if item["name"] == operation)
    jsonschema.validate(response, schema)
    return response


def test_discover_describe_and_catalog_are_detached(tmp_path):
    agent, _ = adapter(tmp_path)
    catalog = success(agent, "discover")["result"]
    assert {item["name"] for item in catalog["operations"]} == {"discover", "describe", "read", "calculate", "explain", "preview_inputs", "set_inputs", "edit", "export"}
    catalog["operations"].clear()
    assert len(operation_catalog()["operations"]) == 9
    description = success(agent, "describe")["result"]
    assert description["source"] == {"kind": "authored"}
    assert len(description["sheets"]) == 2
    assert "cells" not in description and "dependencies" not in description
    assert set(description["inputs"]) == {"fixed_cost", "unit_cost", "unit_price"}


def test_complete_preview_apply_calculate_explain_export_journey(tmp_path):
    agent, model = adapter(tmp_path)
    before = model.to_dict()
    preview = success(agent, "preview_inputs", {"values": {"unit_price": 25}, "expected_revision": 0})["result"]
    assert preview["applied"] is False and preview["base_revision"] == 0 and preview["proposed_revision"] == 1
    assert preview["before"]["outputs"]["profit"] == 1440
    assert preview["after"]["outputs"]["profit"] == 3290
    assert preview["changes"] == [{"sheet": "Assumptions", "address": "B1", "before": 20, "after": 25}]
    assert model.to_dict() == before and agent.revision == 0
    receipt = success(agent, "set_inputs", {"values": {"unit_price": 25}, "expected_revision": 0})["result"]
    assert receipt == {"previous_revision": 0, "revision": 1, "changed_cells": [{"sheet": "Assumptions", "address": "B1", "fields": ["value"]}]}
    result = success(agent, "calculate")["result"]
    assert result["outputs"] == {"revenue": 9250, "profit": 3290, "break_even_units": 1000 / 17}
    assert "values" not in result
    explanation = success(agent, "explain", {"output": "profit"})["result"]
    assert explanation["value"] == 3290
    origins = {f"{cell['sheet']}!{cell['address']}": cell["value_origin"] for cell in explanation["cells"]}
    assert origins["Forecast!F3"] == "calculated" and origins["Assumptions!B1"] == "authored"
    exported = success(agent, "export", {"filename": "scenario.xlsx", "expected_revision": 1})["result"]
    assert exported["bytes"] == (tmp_path / "scenario.xlsx").stat().st_size
    bindings = model.to_dict()
    reopened = import_xlsx(tmp_path / "scenario.xlsx", inputs=bindings["inputs"], outputs=bindings["outputs"])
    assert reopened.calculate()["outputs"] == result["outputs"]


def test_sheet_pages_follow_row_column_order_and_range_excludes_absent_cells(tmp_path):
    agent, _ = adapter(tmp_path, cells={"A10": {"value": 10}, "B2": {"value": 3}, "A2": {"value": 2}, "A1": {"value": 1}})
    first = success(agent, "read", {"sheet": "data", "limit": 2})["result"]
    assert [cell["address"] for cell in first["cells"]] == ["A1", "A2"]
    assert first["next_offset"] == 2 and first["truncated"]
    second = success(agent, "read", {"sheet": "Data", "limit": 2, "offset": 2, "expected_revision": 0})["result"]
    assert [cell["address"] for cell in second["cells"]] == ["B2", "A10"]
    assert second["next_offset"] is None and not second["truncated"]
    ranged = success(agent, "read", {"sheet": "Data", "range": "A2:B3"})["result"]
    assert ranged["total_cells"] == 2
    assert agent.call("read", {"sheet": "Data", "offset": 1})["error"]["code"] == "invalid_request"


def test_paging_revision_conflict_after_edit(tmp_path):
    agent, _ = adapter(tmp_path, cells={"A1": {"value": 1}, "A2": {"value": 2}})
    success(agent, "edit", {"edits": [{"sheet": "Data", "address": "A1", "value": 5}], "expected_revision": 0})
    response = agent.call("read", {"sheet": "Data", "offset": 1, "expected_revision": 0})
    assert response["error"]["code"] == "revision_conflict"


def test_read_dependency_closure_includes_blank_cells_and_keeps_cache_separate(tmp_path):
    agent, _ = adapter(tmp_path, cells={"B1": {"formula": "=A1+1", "cached_value": 999}}, outputs={"out": "B1"})
    page = success(agent, "read", {"output": "out"})["result"]
    assert [cell["address"] for cell in page["cells"]] == ["A1", "B1"]
    assert page["cells"][0]["content"] == {"value": None}
    assert page["cells"][1]["content"]["cached_value"] == 999
    explained = success(agent, "explain", {"output": "out"})["result"]
    assert explained["value"] == 1
    assert explained["cells"][0]["value_origin"] == "blank"
    assert explained["cells"][1]["calculated_value"] == 1


def test_direct_dependencies_are_bounded_with_accurate_counts(tmp_path):
    agent, _ = adapter(tmp_path, cells={"B1": {"formula": "=SUM(A1:A150)"}}, outputs={"total": "B1"})
    result = success(agent, "read", {"sheet": "Data"})["result"]
    cell = result["cells"][0]
    assert len(cell["dependencies"]) == 100
    assert cell["dependency_count"] == 150 and cell["dependencies_truncated"]


def test_selected_output_avoids_unrelated_unsupported_output_and_never_uses_cache(tmp_path):
    agent, _ = adapter(tmp_path, cells={"A1": {"value": 5}, "B1": {"formula": "=LET(x,1,x)", "cached_value": 123}}, outputs={"safe": "A1", "unknown": "B1"})
    selected = success(agent, "calculate", {"outputs": ["safe"]})["result"]
    assert selected["outputs"] == {"safe": 5} and selected["diagnostics"] == []
    failed = success(agent, "explain", {"output": "unknown"})["result"]
    assert failed["value"] is None and failed["diagnostic_count"] == 1
    assert failed["cells"][0]["value_origin"] == "unavailable"
    assert failed["cells"][0]["calculated_value"] is None
    assert failed["cells"][0]["content"]["cached_value"] == 123


def test_diagnostics_are_clipped_but_the_total_remains_visible(tmp_path):
    cells = {f"A{row}": {"formula": "=UNKNOWN()"} for row in range(1, 111)}
    agent, _ = adapter(tmp_path, cells=cells)
    result = success(agent, "read", {"sheet": "Data", "limit": 1})["result"]
    assert result["diagnostic_count"] == 110 and result["diagnostics_truncated"]
    assert len(result["diagnostics"]) == 100


@pytest.mark.parametrize("arguments,code", [({"outputs": []}, "invalid_request"), ({"outputs": ["profit", "profit"]}, "invalid_request"), ({"outputs": ["missing"]}, "invalid_input")])
def test_invalid_output_selections_are_rejected(tmp_path, arguments, code):
    agent, _ = adapter(tmp_path)
    assert agent.call("calculate", arguments)["error"]["code"] == code


def test_unbound_model_cannot_be_calculated_by_agent(tmp_path):
    agent, _ = adapter(tmp_path, cells={"A1": {"formula": "=1+2"}})
    assert agent.call("calculate")["error"]["code"] == "invalid_input"


@pytest.mark.parametrize("operation,arguments", [("set_inputs", {"values": {"unit_price": 25}, "expected_revision": True}), ("calculate", {"workers": True}), ("calculate", {"workers": 9}), ("read", {"sheet": "Forecast", "offset": 1.0}), ("read", {"sheet": "Forecast", "limit": 0})])
def test_exact_integer_requirements_reject_booleans_and_fractional_values(tmp_path, operation, arguments):
    agent, _ = adapter(tmp_path)
    assert agent.call(operation, arguments)["error"]["code"] == "invalid_request"


def test_edit_and_set_inputs_are_atomic_and_reject_unknown_properties(tmp_path):
    agent, model = adapter(tmp_path)
    before = model.to_dict()
    for operation, arguments in [("set_inputs", {"values": {"unit_price": 25, "unit_cost": -1}, "expected_revision": 0}), ("edit", {"edits": [{"sheet": "Assumptions", "address": "B1", "value": 25}, {"sheet": "Forecast", "address": "C2", "formula": "=("}], "expected_revision": 0}), ("edit", {"edits": [{"sheet": "Assumptions", "address": "B1", "value": 25, "mystery": 1}], "expected_revision": 0})]:
        assert not agent.call(operation, arguments)["ok"]
        assert model.to_dict() == before
    unknown = agent.call("set_inputs", {"values": {"unit_price": 25}, "expected_revision": 0, "extra": True})
    assert unknown["error"]["code"] == "invalid_request"


def test_large_mutation_has_small_receipt_and_large_read_fails_without_more_changes(tmp_path):
    agent, model = adapter(tmp_path, cells={})
    edits = [{"sheet": "Data", "address": f"A{row}", "value": "x" * 32767} for row in range(1, 10)]
    receipt = success(agent, "edit", {"edits": edits, "expected_revision": 0})
    assert len(json.dumps(receipt).encode()) < 5000 and agent.revision == 1
    response = agent.call("read", {"sheet": "Data"})
    assert response["error"]["code"] == "resource_limit"
    assert len(json.dumps(response).encode()) < MAX_RESPONSE_BYTES
    assert model.revision == 1


def test_oversized_request_is_rejected_before_mutation(tmp_path):
    agent, model = adapter(tmp_path)
    response = agent.call("edit", {"edits": [{"sheet": "Forecast", "address": "H1", "value": "x" * (1024 * 1024)}], "expected_revision": 0})
    assert response["error"]["code"] == "resource_limit"
    assert model.revision == 0


@pytest.mark.parametrize("payload", [{"operation": "describe", "extra": 1}, {"operation": "describe", "arguments": []}, {"operation": "describe", "id": 3}, {"operation": "describe", "id": "🙂" * 33}, {"operation": None}, []])
def test_malformed_envelope_remains_bounded_and_next_call_works(tmp_path, payload):
    agent, _ = adapter(tmp_path)
    response = agent.handle(payload)
    assert not response["ok"] and response["error"]["code"] == "invalid_request"
    assert response["id"] is None
    assert response["operation"] == ("describe" if isinstance(payload, dict) and payload.get("operation") == "describe" else None)
    success(agent, "describe")


@pytest.mark.parametrize("filename", ["../escape.xlsx", "/tmp/escape.xlsx", "bad/file.xlsx", ".hidden.xlsx", "book.XLSX", "", "x" * 121 + ".xlsx"])
def test_export_rejects_unsafe_names_without_files(tmp_path, filename):
    agent, _ = adapter(tmp_path)
    response = agent.call("export", {"filename": filename, "expected_revision": 0})
    assert response["error"]["code"] == "invalid_request"
    assert list(tmp_path.iterdir()) == []


def test_export_rejects_existing_and_dangling_symlink_targets(tmp_path):
    agent, _ = adapter(tmp_path)
    existing = tmp_path / "existing.xlsx"
    existing.write_bytes(b"preserve me")
    assert agent.call("export", {"filename": existing.name, "expected_revision": 0})["error"]["code"] == "export_error"
    assert existing.read_bytes() == b"preserve me"
    outside = tmp_path.parent / (tmp_path.name + "-outside.xlsx")
    link = tmp_path / "dangling.xlsx"
    link.symlink_to(outside)
    assert agent.call("export", {"filename": link.name, "expected_revision": 0})["error"]["code"] == "export_error"
    assert not outside.exists()


def test_export_uses_the_exact_checked_snapshot_during_external_host_edit(tmp_path, monkeypatch):
    import workbook_forge.agent as module

    agent, model = adapter(tmp_path)
    original = module.export_xlsx

    def racing_export(snapshot, path):
        model.set_inputs({"unit_price": 25})
        return original(snapshot, path)

    monkeypatch.setattr(module, "export_xlsx", racing_export)
    result = success(agent, "export", {"filename": "old-revision.xlsx", "expected_revision": 0})
    assert result["revision"] == result["result"]["revision"] == 0
    assert agent.revision == 1
    bindings = model.to_dict()
    reopened = import_xlsx(tmp_path / "old-revision.xlsx", inputs=bindings["inputs"], outputs=bindings["outputs"])
    assert reopened.calculate()["outputs"]["revenue"] == 7400


def test_imported_preview_keeps_baseline_and_export_preserves_opaque_part(tmp_path):
    model = operating_scenario()
    source = export_xlsx(model, tmp_path / "source.xlsx")
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr("custom/opaque.bin", b"preserved opaque content")
    bindings = model.to_dict()
    imported = import_xlsx(source, inputs=bindings["inputs"], outputs=bindings["outputs"])
    agent = AgentWorkbook(imported, output_dir=tmp_path)
    baseline = source.read_bytes()
    assert success(agent, "describe")["result"]["source"] == {"kind": "xlsx"}
    success(agent, "preview_inputs", {"values": {"unit_price": 25}, "expected_revision": 0})
    assert source.read_bytes() == baseline and agent.revision == 0
    success(agent, "set_inputs", {"values": {"unit_price": 25}, "expected_revision": 0})
    success(agent, "export", {"filename": "changed.xlsx", "expected_revision": 1})
    with zipfile.ZipFile(tmp_path / "changed.xlsx") as archive:
        assert archive.read("custom/opaque.bin") == b"preserved opaque content"
    assert source.read_bytes() == baseline


def test_imported_1904_formula_edit_guard_is_retained(tmp_path):
    source = export_xlsx(operating_scenario(), tmp_path / "base.xlsx")
    with zipfile.ZipFile(source) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    workbook = ET.fromstring(parts["xl/workbook.xml"])
    ET.SubElement(workbook, "{" + namespace + "}workbookPr", {"date1904": "1"})
    parts["xl/workbook.xml"] = ET.tostring(workbook)
    dated = tmp_path / "dated.xlsx"
    with zipfile.ZipFile(dated, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    imported = import_xlsx(dated)
    agent = AgentWorkbook(imported, output_dir=tmp_path)
    response = agent.call("edit", {"edits": [{"sheet": "Forecast", "address": "H1", "formula": "=1+2"}], "expected_revision": 0})
    assert response["error"]["code"] == "unsupported_operation"
    assert imported.revision == 0


def test_adapter_serializes_conflicting_agent_mutations(tmp_path):
    agent, _ = adapter(tmp_path)
    responses = []
    threads = [Thread(target=lambda: responses.append(agent.call("set_inputs", {"values": {"unit_price": 25}, "expected_revision": 0}))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(response["ok"] for response in responses) == 1
    assert [response["error"]["code"] for response in responses if not response["ok"]] == ["revision_conflict"]
    assert agent.revision == 1


def test_error_response_keeps_each_valid_correlation_field(tmp_path):
    agent, _ = adapter(tmp_path)
    for payload in ({"id": "trace-1", "operation": "describe", "extra": 1}, {"id": "trace-1", "operation": "describe", "arguments": []}):
        response = agent.handle(payload)
        assert response["id"] == "trace-1" and response["operation"] == "describe"
        assert response["error"]["code"] == "invalid_request"
    invalid_operation = agent.handle({"id": "trace-2", "operation": ""})
    assert invalid_operation["id"] == "trace-2" and invalid_operation["operation"] is None
