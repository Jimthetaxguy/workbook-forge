"""Open round-trip seams. These checks fail until a receipt stops treating stand-ins as Excel observations."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("excel_oracle_seams", ROOT / "tools" / "excel_oracle.py")
oracle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oracle)


def _sdk_export(path):
    from workbook_forge.toolkit import WorkbookModel
    from workbook_forge.xlsx import export_xlsx

    model = WorkbookModel(sheets=["Main"])
    model.set_value("Main", "A1", 4)
    model.set_formula("Main", "B1", "=A1*2")
    export_xlsx(model, path)
    return path


def _add_excel_cache(path, *, input_value=5, output_value=10, formula="A1*2", output_type=None):
    with zipfile.ZipFile(path) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    root = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
    for item in root.iter(oracle._q("c")):
        address = item.get("r")
        if address == "A1" and input_value is not None:
            value = item.find(oracle._q("v"))
            if value is None:
                value = ET.SubElement(item, oracle._q("v"))
            value.text = str(input_value)
        elif address == "B1":
            expression = item.find(oracle._q("f"))
            if expression is not None:
                expression.text = formula
            value = item.find(oracle._q("v"))
            if value is None:
                value = ET.SubElement(item, oracle._q("v"))
            if output_type == "boolean":
                item.set("t", "b")
                value.text = "1" if output_value else "0"
            elif output_type == "text":
                item.set("t", "str")
                value.text = "" if output_value is None else str(output_value)
            else:
                value.text = str(output_value)
    parts["xl/worksheets/sheet1.xml"] = ET.tostring(root)
    with zipfile.ZipFile(path, "w") as target:
        for name, content in parts.items():
            target.writestr(name, content)


def _excel_metadata():
    return """excel_version=16.113.2
calculation_version=1
calculation_mode_before=automatic
calculation_mode_after=automatic
iteration_before=false
iteration_after=false
max_iterations_before=100
max_iterations_after=100
max_change_before=0.001
max_change_after=0.001
full_rebuild_invoked=true
saved=true
owned_workbook_closed=true"""


def _scenario_contract():
    return oracle.validate_roundtrip_contract({
        "schema_version": 1,
        "id": "test-scenario",
        "required_fixture_classes": ["scenario"],
        "edits": [{"sheet": "Main", "address": "A1", "value": 5}],
        "checks": [{
            "id": "output",
            "class": "scenario",
            "sheet": "Main",
            "address": "B1",
            "expected_formula": "=A1*2",
            "expected_result": {"type": "number", "value": 10, "tolerance": {"absolute": 0, "relative": 0}},
        }],
    })


def _banner(args, **kwargs):
    return oracle.subprocess.CompletedProcess(args[0], 0, _excel_metadata(), "")


def test_successful_banner_with_unchanged_hash_is_not_observed(tmp_path, monkeypatch):
    source = _sdk_export(tmp_path / "sdk-generated.xlsx")
    # The copy already matches the contract, so the banner alone cannot be evidence.
    _add_excel_cache(source, input_value=5, output_value=10, formula="A1*2")
    monkeypatch.setattr(oracle.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(oracle.subprocess, "run", _banner)
    _, receipt = oracle.run_roundtrip(source, _scenario_contract(), tmp_path / "receipts", run_excel=True)
    assert receipt["working_copy_sha256_before_excel"] == receipt["saved_workbook_sha256"]
    assert receipt["excel"]["full_rebuild_invoked"] == "true"
    assert receipt["excel"]["saved"] == "true"
    outcome = receipt["observations"][0]
    assert outcome["formula_matches"] is True
    assert outcome["result_matches"] is True
    assert receipt["status"] != "observed", (
        "a successful metadata banner with an unchanged workbook hash must not be status observed"
    )


def test_planted_cache_with_unchanged_formula_is_not_a_recalc_observation(tmp_path, monkeypatch):
    source = _sdk_export(tmp_path / "sdk-generated.xlsx")
    monkeypatch.setattr(oracle.platform, "system", lambda: "Darwin")

    def plant_cache(args, **kwargs):
        _add_excel_cache(Path(args[2]), input_value=5, output_value=10, formula="A1*2")
        return _banner(args, **kwargs)

    monkeypatch.setattr(oracle.subprocess, "run", plant_cache)
    _, receipt = oracle.run_roundtrip(source, _scenario_contract(), tmp_path / "receipts", run_excel=True)
    outcome = receipt["observations"][0]
    assert outcome["before_recalc"]["formula"] == "=A1*2"
    assert outcome["after_recalc_and_reimport"]["formula"] == "=A1*2"
    assert outcome["after_recalc_and_reimport"]["result"] == {"type": "number", "value": 10.0}
    assert outcome["result_matches"] is False, (
        "a planted cached_value with an unchanged formula must not count as a recalc observation"
    )
    assert outcome["mismatches"], "a planted cache must be recorded as a mismatch"
    assert receipt["mismatches"]
    assert receipt["status"] != "observed"


def test_numeric_constant_allowlist_entry_is_not_a_formula_match(tmp_path, monkeypatch):
    source = _sdk_export(tmp_path / "sdk-generated.xlsx")
    monkeypatch.setattr(oracle.platform, "system", lambda: "Darwin")
    contract = _scenario_contract()
    contract["allowlist"] = [{
        "check_id": "output",
        "field": "formula",
        "accepted_values": ["=9250"],
        "reason": "A numeric constant is not the contracted formula.",
    }]

    def plant_constant(args, **kwargs):
        _add_excel_cache(Path(args[2]), input_value=5, output_value=10, formula="9250")
        return _banner(args, **kwargs)

    monkeypatch.setattr(oracle.subprocess, "run", plant_constant)
    _, receipt = oracle.run_roundtrip(source, contract, tmp_path / "receipts", run_excel=True)
    outcome = receipt["observations"][0]
    assert outcome["after_recalc_and_reimport"]["formula"] == "=9250"
    assert outcome["formula_matches"] is False, (
        "a formula accepted_values entry that is a numeric constant must not count as a formula match"
    )


def _export_case(path, value, formula):
    from workbook_forge.toolkit import WorkbookModel
    from workbook_forge.xlsx import export_xlsx

    model = WorkbookModel(sheets=["Main"])
    model.set_value("Main", "A1", value)
    model.set_formula("Main", "B1", formula)
    export_xlsx(model, path)
    return path


def _typed_contract(expected_result, formula="=A1*2"):
    return oracle.validate_roundtrip_contract({
        "schema_version": 1,
        "id": "test-scenario",
        "required_fixture_classes": ["scenario"],
        "checks": [{
            "id": "output",
            "class": "scenario",
            "sheet": "Main",
            "address": "B1",
            "expected_formula": formula,
            "expected_result": expected_result,
        }],
    })


def test_planted_predicate_text_and_boolean_caches_are_not_observed(tmp_path, monkeypatch):
    monkeypatch.setattr(oracle.platform, "system", lambda: "Darwin")
    cases = (
        {
            "value": 4,
            "formula": "=A1*2",
            "stored_formula": "A1*2",
            "expected_result": {"type": "number", "predicate": {"minimum": 9, "maximum": 11}},
            "output_value": 10,
            "output_type": None,
        },
        {
            "value": "before",
            "formula": "=A1",
            "stored_formula": "A1",
            "expected_result": {"type": "text", "value": "after"},
            "output_value": "after",
            "output_type": "text",
        },
        {
            "value": False,
            "formula": "=A1",
            "stored_formula": "A1",
            "expected_result": {"type": "boolean", "value": True},
            "output_value": True,
            "output_type": "boolean",
        },
    )
    for index, case in enumerate(cases):
        source = _export_case(tmp_path / f"sdk-{index}.xlsx", case["value"], case["formula"])
        contract = _typed_contract(case["expected_result"], case["formula"])

        def plant_cache(args, case=case, **kwargs):
            _add_excel_cache(
                Path(args[2]),
                input_value=None,
                output_value=case["output_value"],
                formula=case["stored_formula"],
                output_type=case["output_type"],
            )
            return _banner(args, **kwargs)

        monkeypatch.setattr(oracle.subprocess, "run", plant_cache)
        _, receipt = oracle.run_roundtrip(source, contract, tmp_path / f"receipts-{index}", run_excel=True)
        outcome = receipt["observations"][0]
        assert outcome["result_matches"] is False
        assert outcome["mismatches"]
        assert receipt["mismatches"]
        assert receipt["status"] != "observed"


def test_disguised_numeric_allowlist_spellings_are_not_formula_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(oracle.platform, "system", lambda: "Darwin")
    for index, spelling in enumerate(("=++9250", "=--9250", "=9250%")):
        assert oracle._numeric_constant_formula(spelling) is True
        assert oracle._formula_is_accepted(spelling, "=A1*2", [spelling]) is False
        source = _sdk_export(tmp_path / f"sdk-{index}.xlsx")
        contract = _scenario_contract()
        contract["allowlist"] = [{
            "check_id": "output",
            "field": "formula",
            "accepted_values": [spelling],
            "reason": "A numeric constant is not the contracted formula.",
        }]

        def plant_constant(args, spelling=spelling, **kwargs):
            _add_excel_cache(Path(args[2]), input_value=5, output_value=10, formula=spelling[1:])
            return _banner(args, **kwargs)

        monkeypatch.setattr(oracle.subprocess, "run", plant_constant)
        _, receipt = oracle.run_roundtrip(source, contract, tmp_path / f"receipts-{index}", run_excel=True)
        outcome = receipt["observations"][0]
        assert outcome["after_recalc_and_reimport"]["formula"] == spelling
        assert outcome["formula_matches"] is False
        assert receipt["status"] != "observed"
