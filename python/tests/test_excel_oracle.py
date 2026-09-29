"""Harness contract tests; these do not claim to simulate or verify Excel."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("excel_oracle", ROOT / "tools" / "excel_oracle.py")
oracle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oracle)


def cell(xml):
    return ET.fromstring(xml.replace('<c', f'<c xmlns="{oracle.MAIN}"', 1))


@pytest.mark.parametrize(('xml', 'expected'), [
    ('<c><v>3.25</v></c>', {'type': 'number', 'value': 3.25}),
    ('<c t="b"><v>0</v></c>', {'type': 'boolean', 'value': False}),
    ('<c t="e"><f>1/0</f><v>#DIV/0!</v></c>', {'type': 'error', 'value': '#DIV/0!'}),
    ('<c t="str"><f>""</f><v/></c>', {'type': 'text', 'value': ''}),
    ('<c t="s"><v>0</v></c>', {'type': 'text', 'value': 'shared text'}),
    ('<c t="inlineStr"><is><t>inline text</t></is></c>', {'type': 'text', 'value': 'inline text'}),
    ('<c/>', {'type': 'blank'}),
])
def test_saved_result_normalization_preserves_types(xml, expected):
    assert oracle.normalize_cell(cell(xml), ['shared text']) == expected


def test_formula_without_cache_is_not_an_observation():
    with pytest.raises(ValueError, match='lacks an observed'):
        oracle.normalize_cell(cell('<c><f>2+2</f></c>'), [])
    assert oracle.normalize_cell(None, []) == {'type': 'blank'}


def test_tolerance_is_explicit_and_does_not_erase_types():
    case = {'expected': {'type': 'number', 'value': 0.3}}
    observation = {'type': 'number', 'value': 0.1 + 0.2}
    assert not oracle.matches_expectation(observation, case)
    assert oracle.matches_expectation(observation, {**case, 'absolute_tolerance': 1e-12})
    assert not oracle.matches_expectation({'type': 'boolean', 'value': False}, {'expected': {'type': 'number', 'value': 0}})
    assert not oracle.matches_expectation({'type': 'blank'}, {'expected': {'type': 'text', 'value': ''}})


def test_default_run_generates_unique_uncached_workbook_without_automation(tmp_path, monkeypatch):
    def unexpected_automation(*args, **kwargs):
        raise AssertionError('default generation must not launch an external process')
    monkeypatch.setattr(oracle.subprocess, 'run', unexpected_automation)
    data = oracle.load_cases()
    first, receipt = oracle.run_observations(data, tmp_path)
    second, _ = oracle.run_observations(data, tmp_path)
    assert first.parent != second.parent
    assert receipt['status'] == 'generated'
    assert receipt['observations'] == []
    assert 'excel' not in receipt
    workbook = first.parent / receipt['workbook']
    with zipfile.ZipFile(workbook) as archive:
        assert all('externalLink' not in name and 'vba' not in name for name in archive.namelist())
        sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        assert all(node.find(oracle._q('v')) is None for node in sheet.iter(oracle._q('c')) if node.find(oracle._q('f')) is not None)
    with pytest.raises(FileExistsError):
        oracle.write_synthetic_workbook(workbook, data)


def test_case_corpus_rejects_external_formula_and_unattributed_expectation(tmp_path):
    data = oracle.load_cases()
    path = tmp_path / 'cases.json'
    changed = copy.deepcopy(data)
    changed['sheets']['Primitives']['A1'] = {'formula': '=WEBSERVICE("https://example.com")'}
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match='external-data'):
        oracle.load_cases(path)
    changed = copy.deepcopy(data)
    changed['cases'][0]['expectation_basis'] = {'kind': 'documented'}
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match='source'):
        oracle.load_cases(path)


def test_saved_formula_copy_is_checked_independently_of_numeric_result(tmp_path):
    data = oracle.load_cases()
    path = tmp_path / 'saved.xlsx'
    # Deliberately wrong formula with a matching cache: a value-only check would pass.
    copied_case = next(case for case in data['cases'] if case['id'] == 'mixed_anchors_excel_copy')
    data['cases'] = [copied_case]
    data['sheets']['Primitives']['E5'] = {'formula': '=22'}
    oracle.write_synthetic_workbook(path, data)
    with zipfile.ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    root = ET.fromstring(parts['xl/worksheets/sheet1.xml'])
    for node in root.iter(oracle._q('c')):
        if node.get('r') == 'E5':
            ET.SubElement(node, oracle._q('v')).text = '22'
    parts['xl/worksheets/sheet1.xml'] = ET.tostring(root)
    saved = tmp_path / 'result.xlsx'
    with zipfile.ZipFile(saved, 'w') as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    result = oracle.observe_saved_workbook(saved, data)[0]
    assert result['observed'] == {'type': 'number', 'value': 22.0}
    assert result['copied_formula_matches'] is False
    assert result['matches_expectation'] is False
    assert result['expectation_basis']['kind'] == 'independently_derived'


def test_metadata_requires_excel_identity_and_confirmed_close():
    with pytest.raises(ValueError, match='excel_version'):
        oracle.parse_metadata('owned_workbook_closed=true')
    with pytest.raises(ValueError, match='confirm closing'):
        oracle.parse_metadata('excel_version=16.0\ncalculation_version=1\ncalculation_mode_before=automatic\ncalculation_mode_during=automatic\ncalculation_mode_after=automatic\niteration=false\nmax_iterations=100\nmax_change=0.001\ndate_1904=false\nprecision_as_displayed=false\nowned_workbook_closed=false')


def test_roundtrip_metadata_does_not_claim_unavailable_excel_settings_are_unchanged():
    raw = _excel_metadata().replace('calculation_mode_before=automatic', 'calculation_mode_before=missing value')
    raw = raw.replace('calculation_mode_after=automatic', 'calculation_mode_after=missing value')
    raw = raw.replace('iteration_before=false', 'iteration_before=missing value')
    raw = raw.replace('iteration_after=false', 'iteration_after=missing value')
    metadata = oracle._metadata_from_roundtrip(raw)
    assert metadata['global_settings_observable'] == 'false'
    assert metadata['global_settings_unchanged'] == 'unverified'
    assert 'iteration' in metadata['global_settings_unavailable']


def test_unobservable_iteration_settings_are_an_environment_blocker(tmp_path, monkeypatch):
    source = _sdk_export(tmp_path / 'sdk-generated.xlsx')
    contract = oracle.validate_roundtrip_contract({
        'schema_version': 1,
        'id': 'iteration-settings-check',
        'required_fixture_classes': ['iteration'],
        'calculation_settings': {'iteration': True, 'max_iterations': 100, 'max_change': 0.001},
        'checks': [{
            'id': 'iterative-value', 'class': 'iteration', 'sheet': 'Main', 'address': 'B1',
            'expected_formula': '=B1/2+1',
            'expected_result': {'type': 'number', 'value': 2, 'tolerance': {'absolute': 0.001, 'relative': 0}},
        }],
    })
    monkeypatch.setattr(oracle.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(oracle, '_run_preflight', lambda *args: [])
    monkeypatch.setattr(oracle.subprocess, 'run', lambda *args, **kwargs: oracle.subprocess.CompletedProcess(
        args[0], 1, '', 'execution error: Excel iteration setting is not observable (-50)'
    ))
    _, receipt = oracle.run_roundtrip(source, contract, tmp_path / 'receipts', run_excel=True)
    assert receipt['status'] == 'blocked'
    assert receipt['reason'] == 'Excel iteration settings are not observable; harness will not change global settings'


def test_applescript_scope_uses_identity_checked_owned_workbook():
    script = oracle.build_applescript(oracle.load_cases())
    assert 'calculate full' not in script
    assert 'active workbook' not in script
    assert 'close every' not in script
    assert not re.search(r'(?i)set\s+(calculation|iteration|max iterations|max change)\s+to\b', script)
    assert 'open workbookFile' in script
    assert 'Synthetic workbook name is already open' in script
    assert script.index('Synthetic workbook path mismatch') < script.index('copy range')
    assert script.count('close targetBook saving no') == 1
    assert 'on error' not in script


def test_automation_denial_is_blocked_without_an_observation(tmp_path, monkeypatch):
    monkeypatch.setattr(oracle.platform, 'system', lambda: 'Darwin')
    def denied(*args, **kwargs):
        return oracle.subprocess.CompletedProcess(args[0], 1, '', 'Not authorized to send Apple events. (-1743)')
    monkeypatch.setattr(oracle.subprocess, 'run', denied)
    _, receipt = oracle.run_observations(oracle.load_cases(), tmp_path, run_excel=True)
    assert receipt['status'] == 'blocked'
    assert receipt['automation_error_code'] == '-1743'
    assert receipt['observations'] == []
    assert 'excel' not in receipt


def test_timeout_does_not_send_cleanup_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(oracle.platform, 'system', lambda: 'Darwin')
    calls = []
    def timeout(*args, **kwargs):
        calls.append(args)
        raise oracle.subprocess.TimeoutExpired(args[0], kwargs['timeout'])
    monkeypatch.setattr(oracle.subprocess, 'run', timeout)
    _, receipt = oracle.run_observations(oracle.load_cases(), tmp_path, run_excel=True)
    assert receipt['status'] == 'blocked'
    assert receipt['observations'] == []
    assert receipt['owned_workbook_may_remain_open'] is True
    assert len(calls) == 1


def test_live_scenario_retains_observations_after_cleanup_failure(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import workbook_forge.toolkit as toolkit

    monkeypatch.setattr(toolkit, 'operating_scenario', lambda: SimpleNamespace(to_dict=lambda: {'sheets': [{'name': 'Assumptions', 'cells': {}}, {'name': 'Forecast', 'cells': {}}]}))
    monkeypatch.setattr(oracle.platform, 'system', lambda: 'Darwin')
    raw = 'excel_version=16.113.2\nOBSERVED=baseline.total_revenue|real|7400.0\nexecution error: AppleEvent timed out. (-1712)'
    monkeypatch.setattr(oracle.subprocess, 'run', lambda *args, **kwargs: oracle.subprocess.CompletedProcess(args[0], 1, '', raw))
    _, receipt = oracle.run_live_scenario(tmp_path)
    assert receipt['status'] == 'observed_partial'
    assert receipt['observations'][0]['observed'] == {'type': 'number', 'value': 7400.0}
    assert receipt['observations'][0]['matches_expectation'] is True
    assert receipt['save_attempted'] is False
    assert receipt['owned_workbook_closed'] is False
    assert receipt['automation_error_code'] == '-1712'


def test_live_scenario_does_not_coerce_text_to_numeric_evidence(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import workbook_forge.toolkit as toolkit

    monkeypatch.setattr(toolkit, 'operating_scenario', lambda: SimpleNamespace(to_dict=lambda: {'sheets': []}))
    monkeypatch.setattr(oracle.platform, 'system', lambda: 'Darwin')
    raw = 'OBSERVED=baseline.total_revenue|text|7400.0'
    monkeypatch.setattr(oracle.subprocess, 'run', lambda *args, **kwargs: oracle.subprocess.CompletedProcess(args[0], 1, '', raw))
    _, receipt = oracle.run_live_scenario(tmp_path)
    assert receipt['observations'][0]['matches_expectation'] is False


def _scenario_contract():
    return oracle.validate_roundtrip_contract({
        'schema_version': 1,
        'id': 'test-scenario',
        'required_fixture_classes': ['scenario'],
        'edits': [{'sheet': 'Main', 'address': 'A1', 'value': 5}],
        'checks': [{
            'id': 'output', 'class': 'scenario', 'sheet': 'Main', 'address': 'B1',
            'expected_formula': '=A1*2',
            'expected_result': {'type': 'number', 'value': 10, 'tolerance': {'absolute': 0, 'relative': 0}},
        }],
    })


def _array_spill_contract():
    return oracle.validate_roundtrip_contract({
        'schema_version': 1,
        'id': 'test-array-spill',
        'required_fixture_classes': ['array_spill'],
        'checks': [{
            'id': 'sequence-spill', 'class': 'array_spill', 'sheet': 'Main', 'address': 'B7',
            'expected_formula': '=SEQUENCE(3)',
            'expected_result': {'type': 'array', 'shape': [3, 1]},
            'spill_cells': ['B7', 'B8', 'B9'],
        }],
    })


def _sdk_export(path):
    from workbook_forge.toolkit import WorkbookModel
    from workbook_forge.xlsx import export_xlsx

    model = WorkbookModel(sheets=['Main'])
    model.set_value('Main', 'A1', 4)
    model.set_formula('Main', 'B1', '=A1*2')
    export_xlsx(model, path)
    return path


def _add_excel_cache(path, *, input_value=5, output_value=10, formula='A1*2'):
    with zipfile.ZipFile(path) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    root = ET.fromstring(parts['xl/worksheets/sheet1.xml'])
    for item in root.iter(oracle._q('c')):
        address = item.get('r')
        if address == 'A1':
            value = item.find(oracle._q('v'))
            if value is None:
                value = ET.SubElement(item, oracle._q('v'))
            value.text = str(input_value)
        elif address == 'B1':
            expression = item.find(oracle._q('f'))
            if expression is not None:
                expression.text = formula
            value = item.find(oracle._q('v'))
            if value is None:
                value = ET.SubElement(item, oracle._q('v'))
            value.text = str(output_value)
    parts['xl/worksheets/sheet1.xml'] = ET.tostring(root)
    with zipfile.ZipFile(path, 'w') as target:
        for name, content in parts.items():
            target.writestr(name, content)


def _excel_metadata():
    return '''excel_version=16.113.2
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
owned_workbook_closed=true'''


def test_roundtrip_contract_fixture_covers_red_flags_and_explicit_policies():
    contract = oracle.load_roundtrip_contract(ROOT / 'fixtures' / 'excel-roundtrip-red-flags.contract.json')
    assert {check['class'] for check in contract['checks']} >= {
        'volatile', 'iteration', 'array', 'array_spill', 'excel_quirk'
    }
    assert all(
        ('shape' in check['expected_result'] and check.get('spill_cells'))
        if check['class'] == 'array_spill'
        else ('tolerance' in check['expected_result'] or 'predicate' in check['expected_result'])
        for check in contract['checks']
    )
    assert all(item.get('reason') for item in contract['allowlist'])


def test_array_spill_contract_requires_complete_rectangular_cell_map():
    contract = _array_spill_contract()
    assert contract['checks'][0]['spill_cells'] == ['B7', 'B8', 'B9']
    assert oracle.roundtrip_result_matches(
        {'type': 'array', 'shape': [3, 1]}, contract['checks'][0]['expected_result']
    )
    incomplete = copy.deepcopy(contract)
    incomplete['checks'][0]['spill_cells'] = ['B7', 'B8']
    with pytest.raises(ValueError, match='complete rectangular spill_cells'):
        oracle.validate_roundtrip_contract(incomplete)
    wrong_class = copy.deepcopy(contract)
    wrong_class['checks'][0]['class'] = 'array'
    with pytest.raises(ValueError, match='only for class array_spill'):
        oracle.validate_roundtrip_contract(wrong_class)


def test_json_schema_accepts_the_machine_readable_array_spill_contract():
    import jsonschema

    schema = json.loads((ROOT / 'schemas' / 'excel-roundtrip-contract.schema.json').read_text())
    jsonschema.validate(json.loads(json.dumps(_array_spill_contract())), schema)


def test_sdk_export_currently_refuses_dynamic_array_spill_placement(tmp_path):
    from workbook_forge.toolkit import WorkbookModel
    from workbook_forge.workbook import UnsupportedWorkbook
    from workbook_forge.xlsx import export_xlsx

    model = WorkbookModel(sheets=['Main'])
    model.set_formula('Main', 'B7', '=SEQUENCE(3)')
    with pytest.raises(UnsupportedWorkbook, match='worksheet array spill caches are unsupported'):
        export_xlsx(model, tmp_path / 'sequence-spill.xlsx')


def test_roundtrip_receipt_blocks_spill_coverage_without_calling_excel(tmp_path, monkeypatch):
    source = _sdk_export(tmp_path / 'sdk-generated.xlsx')
    contract = oracle.load_roundtrip_contract(ROOT / 'fixtures' / 'excel-roundtrip-red-flags.contract.json')
    monkeypatch.setattr(oracle.platform, 'system', lambda: 'Darwin')

    def unexpected_excel(*args, **kwargs):
        raise AssertionError('unsupported spill coverage must be refused before Excel opens')

    monkeypatch.setattr(oracle.subprocess, 'run', unexpected_excel)
    path, receipt = oracle.run_roundtrip(source, contract, tmp_path / 'receipts', run_excel=True)
    coverage = {item['class']: item['status'] for item in receipt['fixture_coverage']}
    assert receipt['status'] == 'blocked'
    assert receipt['blocker_kind'] == 'unsupported_capability'
    assert receipt['preflight_skipped'] is True
    assert receipt['capability_blockers'][0]['check_id'] == 'array-spill-placement'
    assert receipt['capability_blockers'][0]['spill_cells'] == ['B7', 'B8', 'B9']
    assert coverage['array_spill'] == 'blocked'
    assert coverage['array'] == 'not_observed'
    assert 'scalar reduction' in receipt['capability_blockers'][0]['reason']
    assert json.loads(path.read_text())['source_unchanged'] is True


def test_roundtrip_contract_rejects_implicit_volatile_or_numeric_tolerance():
    data = json.loads((ROOT / 'fixtures' / 'excel-roundtrip-red-flags.contract.json').read_text())
    data['allowlist'] = [item for item in data['allowlist'] if item['check_id'] != 'volatile-rand']
    with pytest.raises(ValueError, match='explicit result allowlist'):
        oracle.validate_roundtrip_contract(data)
    data = json.loads((ROOT / 'fixtures' / 'excel-roundtrip-red-flags.contract.json').read_text())
    del data['checks'][-1]['expected_result']['tolerance']
    with pytest.raises(ValueError, match='explicit absolute and relative tolerances'):
        oracle.validate_roundtrip_contract(data)


def test_volatile_predicate_checks_type_and_domain_not_identical_value():
    expected = {'type': 'number', 'predicate': {
        'minimum': 0, 'maximum': 1, 'minimum_inclusive': True, 'maximum_inclusive': False,
    }}
    assert oracle.roundtrip_result_matches({'type': 'number', 'value': 0.421}, expected)
    assert not oracle.roundtrip_result_matches({'type': 'number', 'value': 1}, expected)
    assert not oracle.roundtrip_result_matches({'type': 'text', 'value': '0.421'}, expected)
    assert not oracle.roundtrip_result_matches({'type': 'number', 'value': float('nan')}, expected)


def test_full_rebuild_script_scopes_to_owned_workbook_and_writes_no_global_settings():
    script = oracle.build_roundtrip_applescript(_scenario_contract())
    assert 'calculate full rebuild' in script
    assert 'count of workbooks' in script
    assert 'save targetBook' in script
    assert 'close targetBook saving no' in script
    assert not re.search(r'(?i)set\s+(calculation|iteration|max iterations|max change)\s+to\b', script)
    assert 'close every' not in script


def test_iteration_script_refuses_unobservable_settings_without_writing_them():
    contract = oracle.load_roundtrip_contract(ROOT / 'fixtures' / 'excel-roundtrip-red-flags.contract.json')
    script = oracle.build_roundtrip_applescript(contract)
    assert 'iterationBefore is "missing value"' in script
    assert 'maxIterationsBefore is "missing value"' in script
    assert 'maxChangeBefore is "missing value"' in script
    assert not re.search(r'(?i)set\s+(calculation|iteration|max iterations|max change)\s+to\b', script)


def test_roundtrip_preparation_consumes_and_reimports_an_sdk_export(tmp_path):
    source = _sdk_export(tmp_path / 'sdk-generated.xlsx')
    before = oracle._hash(source)
    path, receipt = oracle.run_roundtrip(source, _scenario_contract(), tmp_path / 'receipts')
    assert receipt['status'] == 'prepared'
    assert receipt['formula_count_before'] == 1
    assert receipt['observations'] == []
    assert receipt['source_unchanged'] is True
    assert oracle._hash(source) == before
    assert json.loads(path.read_text())['safety']['source_overwritten'] is False


def test_roundtrip_full_excel_cycle_reimports_formula_and_behavior_diff(tmp_path, monkeypatch):
    source = _sdk_export(tmp_path / 'sdk-generated.xlsx')
    monkeypatch.setattr(oracle.platform, 'system', lambda: 'Darwin')

    def save_as_excel(args, **kwargs):
        _add_excel_cache(Path(args[2]))
        return oracle.subprocess.CompletedProcess(args[0], 0, _excel_metadata(), '')

    monkeypatch.setattr(oracle.subprocess, 'run', save_as_excel)
    path, receipt = oracle.run_roundtrip(source, _scenario_contract(), tmp_path / 'receipts', run_excel=True)
    assert receipt['status'] == 'mismatch'
    assert receipt['observations'][0]['result_matches'] is False
    assert receipt['mismatches']
    assert receipt['full_rebuild_invoked'] is True
    assert receipt['observations'][0]['before_recalc']['formula'] == '=A1*2'
    assert receipt['observations'][0]['after_recalc_and_reimport']['result'] == {'type': 'number', 'value': 10.0}
    assert receipt['formula_diffs'] == []
    assert receipt['source_unchanged'] is True
    assert json.loads(path.read_text())['excel']['owned_workbook_closed'] == 'true'


def test_roundtrip_accepts_only_an_exact_allowlisted_formula_rewrite(tmp_path, monkeypatch):
    source = _sdk_export(tmp_path / 'sdk-generated.xlsx')
    monkeypatch.setattr(oracle.platform, 'system', lambda: 'Darwin')
    contract = _scenario_contract()
    contract['allowlist'] = [{
        'check_id': 'output', 'field': 'formula', 'accepted_values': ['=(A1*2)'],
        'reason': 'This exact parenthesized form is accepted for the test.',
    }]

    def save_with_exact_rewrite(args, **kwargs):
        _add_excel_cache(Path(args[2]), formula='(A1*2)')
        return oracle.subprocess.CompletedProcess(args[0], 0, _excel_metadata(), '')

    monkeypatch.setattr(oracle.subprocess, 'run', save_with_exact_rewrite)
    _, receipt = oracle.run_roundtrip(source, contract, tmp_path / 'receipts', run_excel=True)
    assert receipt['status'] == 'observed'
    assert receipt['formula_diffs'] == [{
        'cell': 'Main!B1', 'before': '=A1*2', 'after': '=(A1*2)', 'allowlisted': True,
    }]
    assert receipt['observations'][0]['result_matches'] is True


def test_roundtrip_matching_cache_cannot_hide_a_changed_formula(tmp_path, monkeypatch):
    source = _sdk_export(tmp_path / 'sdk-generated.xlsx')
    monkeypatch.setattr(oracle.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(
        oracle.subprocess, 'run',
        lambda args, **kwargs: (
            _add_excel_cache(Path(args[2]), formula='1+9')
            or oracle.subprocess.CompletedProcess(args[0], 0, _excel_metadata(), '')
        ),
    )
    _, receipt = oracle.run_roundtrip(source, _scenario_contract(), tmp_path / 'receipts', run_excel=True)
    assert receipt['status'] == 'mismatch'
    assert receipt['observations'][0]['after_recalc_and_reimport']['result'] == {'type': 'number', 'value': 10.0}
    assert receipt['observations'][0]['formula_matches'] is False
    assert receipt['formula_diffs'][0]['allowlisted'] is False


def test_roundtrip_automation_blocker_is_not_reported_as_parity(tmp_path, monkeypatch):
    source = _sdk_export(tmp_path / 'sdk-generated.xlsx')
    monkeypatch.setattr(oracle.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(oracle.subprocess, 'run', lambda *args, **kwargs: oracle.subprocess.CompletedProcess(
        args[0], 1, '', 'execution error: Full rebuild requires Excel to have no other open workbooks (-50)'
    ))
    _, receipt = oracle.run_roundtrip(source, _scenario_contract(), tmp_path / 'receipts', run_excel=True)
    assert receipt['status'] == 'blocked'
    assert 'Full rebuild would affect all open workbooks' in receipt['reason']
    assert receipt['observations'] == []
