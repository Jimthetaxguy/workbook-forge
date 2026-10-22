"""First-class evidence grades: profile, dual_engine, expected_value, excel_observed.

Two engines agreeing is not evidence against Excel. These tests pin the three
failure cases from the evidence-grade design:

- a function with only dual-engine agreement cannot be marked Excel-compatible;
- an Excel receipt whose observed workbook hash changed is flagged stale;
- strict export refuses, with a typed error, a cell whose formula depends on a
  semantic (or not yet classified) profile rule without a current Excel
  observation.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

import jsonschema
import pytest

from workbook_forge import analyze_formula, catalog
from workbook_forge.canonical_xlsx import (
    CanonicalPackageError,
    EvidenceGateRefused,
    export_canonical,
)
from workbook_forge.evidence import (
    EVIDENCE_GRADES,
    EvidenceError,
    ExcelObservation,
    FunctionEvidence,
    check_observation,
    is_excel_compatible,
    observation_from_receipt,
    validate_case_evidence,
    validate_function_evidence,
)
from workbook_forge.model import hydrate

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures" / "operating-scenario.workbook.json"
SCHEMA = json.loads((ROOT / "catalog" / "formula-support.schema.json").read_text(encoding="utf-8"))
FORMULAS = json.loads((ROOT / "catalog" / "formulas.json").read_text(encoding="utf-8"))

_SPEC = importlib.util.spec_from_file_location("excel_oracle_evidence", ROOT / "tools" / "excel_oracle.py")
oracle = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(oracle)
_SEAMS_SPEC = importlib.util.spec_from_file_location(
    "excel_oracle_seams_for_evidence", ROOT / "python" / "tests" / "test_excel_oracle_seams.py",
)
seams = importlib.util.module_from_spec(_SEAMS_SPEC)
_SEAMS_SPEC.loader.exec_module(seams)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entry(**overrides):
    entry = copy.deepcopy(next(item for item in FORMULAS["functions"] if item["name"] == "SUM"))
    entry.update(overrides)
    return entry


def _observed_repo(tmp_path: Path) -> tuple[Path, ExcelObservation]:
    """A repository root holding one observed receipt and the workbook Excel saved."""
    root = tmp_path / "repo"
    directory = root / "receipts" / "sum-excel"
    directory.mkdir(parents=True)
    workbook = directory / "roundtrip.xlsx"
    workbook.write_bytes(b"PK-excel-saved-bytes")
    receipt = {
        "schema_version": 1,
        "harness": "excel_export_roundtrip_v1",
        "recorded_at_utc": "2026-10-14T18:00:05+00:00",
        "status": "observed",
        "saved_workbook_sha256": _sha(workbook),
        "excel": {"excel_version": "16.113.4"},
        "excel_observation": {
            "kind": "excel_observed",
            "workbook": "roundtrip.xlsx",
            "workbook_sha256": _sha(workbook),
            "excel_version": "16.113.4",
            "event_time": "2026-10-14T18:00:03Z",
        },
    }
    receipt_path = directory / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return root, observation_from_receipt(receipt_path, root)


def _workbook():
    return hydrate(FIXTURE.read_bytes())


def _called_functions(workbook) -> set[str]:
    names: set[str] = set()
    for sheet in workbook.sheets:
        for cell in sheet.cells.values():
            if cell.formula is None:
                continue
            try:
                names.update(analyze_formula(cell.formula.expression).functions)
            except ValueError:
                continue  # e.g. the fixture's deliberate `=1+`, exported without a cache
    return names


# --- catalog and schema ----------------------------------------------------


def test_every_catalog_function_has_a_first_class_grade_and_none_claims_excel():
    jsonschema.Draft202012Validator(SCHEMA).validate(FORMULAS)
    graded = catalog.evidence_by_function()
    assert len(graded) == len(FORMULAS["functions"])
    assert {item.evidence_grade for item in graded.values()} <= set(EVIDENCE_GRADES)
    # No Excel receipt is committed for any function yet, so nothing is excel_observed.
    assert all(item.evidence_grade != "excel_observed" for item in graded.values())
    # The cosmetic/semantic review has not happened; every function says so.
    assert {item.profile_class for item in graded.values()} == {"unclassified"}
    assert not any(catalog.is_excel_compatible(name) for name in graded)
    assert catalog.function_evidence("SUM").evidence_grade == "dual_engine"


def test_profile_class_defaults_to_unclassified():
    entry = _entry()
    entry.pop("profile_class")
    assert validate_function_evidence(entry).profile_class == "unclassified"


def test_evidence_grade_is_required_by_schema_and_loader():
    entry = _entry()
    entry.pop("evidence_grade")
    with pytest.raises(EvidenceError) as caught:
        validate_function_evidence(entry)
    assert caught.value.code == "invalid_evidence_grade"
    document = copy.deepcopy(FORMULAS)
    document["functions"][0].pop("evidence_grade")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(SCHEMA).validate(document)


# --- failure test 1: dual_engine cannot be Excel-compatible ---------------


def test_dual_engine_only_cannot_be_marked_excel_compatible():
    entry = _entry(evidence_grade="dual_engine", excel_compatible=True)
    with pytest.raises(EvidenceError) as caught:
        validate_function_evidence(entry)
    assert caught.value.code == "excel_compatibility_without_observation"

    document = copy.deepcopy(FORMULAS)
    document["functions"][0].update(evidence_grade="dual_engine", excel_compatible=True)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(SCHEMA).validate(document)

    for grade in ("profile", "dual_engine", "expected_value"):
        with pytest.raises(EvidenceError):
            validate_function_evidence(_entry(evidence_grade=grade, excel_compatible=True))
        evidence = validate_function_evidence(_entry(evidence_grade=grade))
        assert is_excel_compatible(evidence, ROOT) is False


def test_dual_engine_cannot_carry_an_excel_observation(tmp_path):
    _, observation = _observed_repo(tmp_path)
    with pytest.raises(EvidenceError) as caught:
        validate_function_evidence(_entry(evidence_grade="dual_engine", excel_observation=observation.to_record()))
    assert caught.value.code == "grade_mismatch"


def test_excel_observed_requires_a_well_formed_receipt(tmp_path):
    with pytest.raises(EvidenceError) as caught:
        validate_function_evidence(_entry(evidence_grade="excel_observed"))
    assert caught.value.code == "missing_receipt"
    _, observation = _observed_repo(tmp_path)
    for field, bad in (
        ("receipt_sha256", "not-a-hash"),
        ("workbook", "/Users/someone/private/roundtrip.xlsx"),
        ("receipt", "../outside/receipt.json"),
        ("event_time", "2026-10-14 18:00"),
        ("excel_version", ""),
    ):
        record = observation.to_record()
        record[field] = bad
        with pytest.raises(EvidenceError) as caught:
            validate_function_evidence(_entry(evidence_grade="excel_observed", excel_observation=record))
        assert caught.value.code == "invalid_observation", field
    graded = validate_function_evidence(_entry(
        evidence_grade="excel_observed", excel_observation=observation.to_record(), excel_compatible=True,
    ))
    assert graded.excel_observation == observation


# --- failure test 2: stale receipts ---------------------------------------


def test_receipt_is_stale_when_the_observed_workbook_hash_changes(tmp_path):
    root, observation = _observed_repo(tmp_path)
    evidence = FunctionEvidence("SUM", "excel_observed", "semantic", observation)
    assert check_observation(observation, root).state == "current"
    assert is_excel_compatible(evidence, root) is True

    (root / observation.workbook).write_bytes(b"PK-re-exported-different-bytes")
    check = check_observation(observation, root)
    assert check.state == "stale"
    assert any("hash changed" in reason for reason in check.reasons)
    assert is_excel_compatible(evidence, root) is False

    freshness = oracle.receipt_freshness(root / observation.receipt)
    assert freshness["state"] == "stale"
    assert oracle.main(["--check-receipt", str(root / observation.receipt)]) == 2

    (root / observation.workbook).unlink()
    assert check_observation(observation, root).state == "missing"
    assert oracle.receipt_freshness(root / observation.receipt)["state"] == "missing"


def test_edited_receipt_is_stale(tmp_path):
    root, observation = _observed_repo(tmp_path)
    receipt = root / observation.receipt
    receipt.write_text(receipt.read_text(encoding="utf-8").replace("16.113.4", "16.200.0"), encoding="utf-8")
    assert check_observation(observation, root).state == "stale"


def test_current_receipt_reports_current(tmp_path):
    root, observation = _observed_repo(tmp_path)
    assert oracle.receipt_freshness(root / observation.receipt) == {
        "state": "current", "status": "observed", "reasons": [],
    }
    assert oracle.main(["--check-receipt", str(root / observation.receipt)]) == 0
    assert observation.event_time == "2026-10-14T18:00:03Z"
    assert observation.excel_version == "16.113.4"


def test_prepared_receipt_is_not_an_observation():
    prepared = ROOT / "receipts" / "canonical-operating-scenario" / "scenario-roundtrip.json"
    with pytest.raises(EvidenceError) as caught:
        observation_from_receipt(prepared, ROOT)
    assert caught.value.code == "not_an_observation"
    assert oracle.receipt_freshness(prepared)["state"] == "not_an_observation"


def test_oracle_pins_an_observed_roundtrip_to_the_saved_workbook(tmp_path, monkeypatch):
    source = seams._sdk_export(tmp_path / "sdk-generated.xlsx")
    monkeypatch.setattr(oracle.platform, "system", lambda: "Darwin")

    def excel_recalculates(args, **kwargs):
        saved = Path(args[2])
        seams._add_excel_cache(saved, input_value=5, output_value=10, formula="A1*2")
        with zipfile.ZipFile(saved, "a") as archive:
            archive.writestr(
                "xl/calcChain.xml",
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<c r="B1" i="1"/></calcChain>',
            )
        return oracle.subprocess.CompletedProcess(args[0], 0, seams._excel_metadata(), "")

    monkeypatch.setattr(oracle.subprocess, "run", excel_recalculates)
    receipt_path, receipt = oracle.run_roundtrip(
        source, seams._scenario_contract(), tmp_path / "receipts", run_excel=True,
    )
    assert receipt["status"] == "observed", receipt.get("mismatches")
    block = receipt["excel_observation"]
    assert block["kind"] == "excel_observed"
    assert block["workbook"] == "roundtrip.xlsx"
    assert block["workbook_sha256"] == receipt["saved_workbook_sha256"]
    assert block["excel_version"] == "16.113.2"
    assert block["event_time"].endswith("Z") and block["event_time"] != receipt["recorded_at_utc"]
    assert oracle.receipt_freshness(receipt_path)["state"] == "current"
    observation = observation_from_receipt(receipt_path, tmp_path)
    assert observation.workbook_sha256 == receipt["saved_workbook_sha256"]
    (receipt_path.parent / "roundtrip.xlsx").write_bytes(b"re-exported")
    assert oracle.receipt_freshness(receipt_path)["state"] == "stale"
    assert check_observation(observation, tmp_path).state == "stale"


def test_unobserved_roundtrip_receipt_carries_no_observation_block(tmp_path):
    source = seams._sdk_export(tmp_path / "sdk-generated.xlsx")
    _, receipt = oracle.run_roundtrip(source, seams._scenario_contract(), tmp_path / "receipts")
    assert receipt["status"] == "prepared"
    assert "excel_observation" not in receipt


# --- failure test 3: strict export refusal --------------------------------


def test_default_export_is_unchanged_by_the_evidence_gate(tmp_path):
    assert export_canonical(_workbook(), tmp_path / "default.xlsx").is_file()


def test_strict_export_refuses_semantic_rule_without_excel_observation(tmp_path):
    target = tmp_path / "strict.xlsx"
    with pytest.raises(EvidenceGateRefused) as caught:
        export_canonical(_workbook(), target, strict_evidence=True)
    error = caught.value
    assert isinstance(error, CanonicalPackageError)
    assert error.code == "evidence_insufficient"
    by_function = {item.function: item for item in error.refusals}
    # SUM, IF and NA are catalogued as dual_engine/unclassified; NOW (volatile)
    # has no implementation entry at all.
    assert {by_function[name].reason for name in ("SUM", "IF", "NA")} == {"no_excel_observation"}
    assert {by_function[name].evidence_grade for name in ("SUM", "IF", "NA")} == {"dual_engine"}
    assert by_function["NOW"].reason == "uncatalogued"
    assert {item.profile_class for item in error.refusals} == {"unclassified"}
    assert "Forecast!F2" in error.message
    assert not target.exists()
    assert not list(tmp_path.iterdir())


def test_strict_export_refuses_semantic_and_passes_cosmetic(tmp_path):
    workbook = _workbook()
    names = _called_functions(workbook)
    assert names, "the canonical fixture calls at least one function"
    semantic = {name: FunctionEvidence(name, "dual_engine", "semantic", None) for name in names}
    with pytest.raises(EvidenceGateRefused) as caught:
        export_canonical(workbook, tmp_path / "semantic.xlsx", strict_evidence=True, evidence=semantic)
    assert {item.profile_class for item in caught.value.refusals} == {"semantic"}
    assert {item.evidence_grade for item in caught.value.refusals} == {"dual_engine"}
    assert not (tmp_path / "semantic.xlsx").exists()

    cosmetic = {name: FunctionEvidence(name, "dual_engine", "cosmetic", None) for name in names}
    assert export_canonical(
        workbook, tmp_path / "cosmetic.xlsx", strict_evidence=True, evidence=cosmetic,
    ).is_file()


def test_strict_export_accepts_current_observation_and_refuses_stale(tmp_path):
    root, observation = _observed_repo(tmp_path)
    workbook = _workbook()
    names = _called_functions(workbook)
    observed = {name: FunctionEvidence(name, "excel_observed", "semantic", observation) for name in names}
    assert export_canonical(
        workbook, tmp_path / "observed.xlsx", strict_evidence=True, evidence=observed, evidence_root=root,
    ).is_file()

    (root / observation.workbook).write_bytes(b"PK-re-exported-different-bytes")
    with pytest.raises(EvidenceGateRefused) as caught:
        export_canonical(
            workbook, tmp_path / "stale.xlsx", strict_evidence=True, evidence=observed, evidence_root=root,
        )
    assert {item.reason for item in caught.value.refusals} == {"excel_observation_stale"}
    assert not (tmp_path / "stale.xlsx").exists()


def test_strict_export_refuses_uncatalogued_functions(tmp_path):
    with pytest.raises(EvidenceGateRefused) as caught:
        export_canonical(_workbook(), tmp_path / "none.xlsx", strict_evidence=True, evidence={})
    assert {item.reason for item in caught.value.refusals} == {"uncatalogued"}


# --- per-case grades -------------------------------------------------------


def test_fixture_cases_accept_an_optional_evidence_grade(tmp_path):
    lines = (ROOT / "fixtures" / "formula-cases.jsonl").read_text(encoding="utf-8").splitlines()
    grades = {validate_case_evidence(json.loads(line)) for line in lines if line.strip()}
    assert grades <= {None, *EVIDENCE_GRADES}
    assert validate_case_evidence({"id": "x", "evidence": {"grade": "expected_value"}}) == "expected_value"
    with pytest.raises(EvidenceError):
        validate_case_evidence({"id": "x", "evidence": {"grade": "excel_observed"}})
    with pytest.raises(EvidenceError):
        validate_case_evidence({"id": "x", "evidence": {"grade": "two_engines_agree"}})
    _, observation = _observed_repo(tmp_path)
    with pytest.raises(EvidenceError):
        validate_case_evidence({
            "id": "x", "evidence": {"grade": "dual_engine", "excel_observation": observation.to_record()},
        })
    assert validate_case_evidence({
        "id": "x", "evidence": {"grade": "excel_observed", "excel_observation": observation.to_record()},
    }) == "excel_observed"
