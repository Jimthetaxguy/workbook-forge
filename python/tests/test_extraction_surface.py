"""Installed-resource and CLI boundaries for the independent extraction APIs."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_extraction_catalog_projection_and_embedded_copy():
    subprocess.run([sys.executable, str(ROOT / 'tools/sync_extraction_catalog.py')], check=True)
    catalog = json.loads((ROOT / 'catalog/extraction-patterns.json').read_text())
    assert catalog['functions']['SUM']['python'] == 'conformance-tested'
    assert catalog['functions']['SUM']['rust'] == 'conformance-tested'
    assert catalog['functions']['LET']['python'] == 'catalogued'
    assert len({pattern['id'] for pattern in catalog['patterns']}) == len(catalog['patterns'])
    schema = json.loads((ROOT / 'catalog/extraction-report.schema.json').read_text())
    jsonschema.Draft202012Validator.check_schema(schema)


def test_extraction_cli_returns_schema_valid_filtered_page(tmp_path):
    from workbook_forge.toolkit import operating_scenario
    from workbook_forge.xlsx import export_xlsx

    source = export_xlsx(operating_scenario(), tmp_path / 'source.xlsx')
    before = source.read_bytes()
    result = subprocess.run(
        [sys.executable, '-m', 'workbook_forge.cli', 'extract', str(source),
         '--pattern', 'formula', '--sheet', 'forecast', '--limit', '2'],
        check=True, capture_output=True, text=True,
    )
    report = json.loads(result.stdout)
    jsonschema.validate(report, json.loads((ROOT / 'catalog/extraction-report.schema.json').read_text()))
    assert report['truncated'] and report['next_offset'] == 2
    assert len(report['records']) == 2
    assert all(item['pattern'] == 'formula' and item['sheet'] == 'Forecast' for item in report['records'])
    assert source.read_bytes() == before


@pytest.mark.parametrize('arguments', [['--limit', '101'], ['--limit', '0'], ['--offset', '-1'], ['--pattern', 'not_a_pattern'], ['--sheet', 'absent']])
def test_extraction_cli_failure_is_structured_and_nonmutating(tmp_path, arguments):
    from workbook_forge.toolkit import operating_scenario
    from workbook_forge.xlsx import export_xlsx

    source = export_xlsx(operating_scenario(), tmp_path / 'source.xlsx')
    before = source.read_bytes()
    result = subprocess.run([sys.executable, '-m', 'workbook_forge.cli', 'extract', str(source), *arguments], capture_output=True, text=True)
    assert result.returncode == 1 and not result.stdout
    assert isinstance(json.loads(result.stderr)['error'], str)
    assert source.read_bytes() == before
