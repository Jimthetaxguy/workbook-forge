"""Harness contract tests; these do not claim to simulate or verify Excel."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
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


def test_applescript_scope_uses_identity_checked_owned_workbook():
    script = oracle.build_applescript(oracle.load_cases())
    assert 'calculate full' not in script
    assert 'active workbook' not in script
    assert 'close every' not in script
    assert 'set calculation' not in script
    assert 'set iteration' not in script
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
