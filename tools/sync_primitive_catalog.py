"""Project the native primitive discovery catalog from the formula support source."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FUNCTIONS = {
    'SUM': 'sum', 'AVERAGE': 'average', 'MIN': 'min', 'MAX': 'max',
    'COUNT': 'count', 'IF': 'if_', 'IFERROR': 'iferror', 'ROUND': 'round', 'ABS': 'abs',
}
OPERATORS = {
    '+': 'add', '-': 'subtract', '*': 'multiply', '/': 'divide', '=': 'equal',
    '<>': 'not_equal', '<': 'less_than', '<=': 'less_equal', '>': 'greater_than', '>=': 'greater_equal',
}


def projected_catalog() -> dict:
    source = json.loads((ROOT / 'catalog/formulas.json').read_text())
    supported = {item['name']: item for item in source['functions']}
    functions = []
    for name, api in FUNCTIONS.items():
        item = supported[name]
        limits = ['Behavior inherits the existing independent evaluator profile; function identity does not claim complete Excel equivalence.']
        if name in {'SUM', 'AVERAGE', 'MIN', 'MAX', 'COUNT'}:
            limits.append('The current Forge aggregate profile ignores Boolean and text values in scalar arguments as well as ranges. Direct scalar Excel coercion is not established by this mapping.')
        if name in {'IF', 'IFERROR'}:
            limits.append('Composed expressions evaluate branches lazily; host-language arguments to direct functions are evaluated before the function is called. All declared named inputs are required even in untaken branches.')
        if name == 'ROUND':
            limits.append('Fractional precision is truncated and precision is bounded by the existing Forge evaluator profile.')
        entry = {
            'id': f'excel.{name}', 'name': name, 'excel_name': name,
            'category': item['category'], 'arity': item['arity'],
            'python_api': f'workbook_forge.primitives.{api}',
            'rust_api': f'workbook_forge::primitives::{api}',
            'implementation_status': item['status'],
            'supported_error_values': ['#VALUE!', '#DIV/0!', '#REF!', '#NAME?', '#NUM!', '#N/A', '#CALC!'],
            'compatibility': 'bounded-forge-profile',
            'evidence': 'Existing shared formula fixtures plus dedicated native primitive contract tests; this descriptor is not an Excel observation.',
            'limitations': limits,
        }
        if name in source.get('semantic_specs', {}):
            entry['reference_semantic_spec'] = source['semantic_specs'][name]
            entry['reference_scope'] = 'Inherited formula documentation; native input/error/resource restrictions in this descriptor take precedence.'
        if name == 'SUM':
            entry['documented_difference'] = {
                'source': 'https://learn.microsoft.com/en-us/office/vba/api/excel.worksheetfunction.sum',
                'basis': 'documented, not independently observed in Excel',
                'behavior': 'Microsoft documents counting direct logical/numeric-text arguments while ignoring those values in arrays/references. Forge currently ignores them in both contexts.',
            }
        functions.append(entry)
    return {
        'schema_version': 1, 'profile': 'spreadsheet-primitives-v1',
        'source_catalog_version': source['catalog_version'],
        'purpose': 'Discover and compose spreadsheet operations without a workbook, cell addresses, formula parsing, or another language runtime.',
        'functions': functions,
        'operators': [
            {'id': f'excel.operator.{name}', 'symbol': symbol, 'arity': {'min': 2, 'max': 2},
             'compatibility': 'bounded-forge-profile',
             'limitations': ['Uses the existing binary64, blank/error and comparison profiles; ordinary Python or Rust arithmetic is not substituted.']}
            for symbol, name in OPERATORS.items()
        ],
        'value_kinds': ['number', 'text', 'boolean', 'blank', 'error', 'range', 'array'],
        'omitted_argument': 'Distinct from blank; supported only as an immediate function-call argument.',
        'input_contract': 'Case-sensitive ASCII names; every declared input is required and unknown inputs are refused.',
        'limits': {'max_nodes': 1024, 'max_depth': 64, 'max_inputs': 256,
                   'max_text_utf16_units': 32767, 'max_value_elements': 100000,
                   'max_interchange_bytes': 1048576},
        'expression_schema': 'primitive-expression.schema.json',
        'expression_schema_api': {
            'python': 'workbook_forge.primitives.primitive_expression_schema',
            'rust': 'workbook_forge::primitives::primitive_expression_schema',
        },
        'evaluation_payload': 'The 1 MiB limit also applies to compact UTF-8 JSON containing both the expression envelope and encoded inputs. Repeated bound values count at every expression use.',
        'non_goals': ['Automatic equivalence proof for arbitrary scripts', 'Worksheet spill placement', 'A spreadsheet UI'],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    content = json.dumps(projected_catalog(), indent=2, ensure_ascii=False) + '\n'
    paths = [ROOT / 'catalog/primitive-catalog.json', ROOT / 'rust/src/primitive_catalog.json']
    schema = (ROOT / 'catalog/primitive-expression.schema.json').read_text()
    schema_copy = ROOT / 'rust/src/primitive_expression.schema.json'
    if args.write:
        schema_copy.write_text(schema)
        for path in paths:
            path.write_text(content)
    elif not schema_copy.is_file() or schema_copy.read_text() != schema or any(not path.is_file() or path.read_text() != content for path in paths):
        raise SystemExit('Primitive catalog is stale; run tools/sync_primitive_catalog.py --write')
    print('Native primitive catalog and Rust copy match the formula support source.')


if __name__ == '__main__':
    main()
