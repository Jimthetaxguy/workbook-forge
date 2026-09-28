"""Refresh the extraction projection after authoritative formula catalogs change."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def projected_catalog() -> dict:
    catalog = json.loads((ROOT / 'catalog/extraction-patterns.json').read_text())
    inventory = json.loads((ROOT / 'catalog/function_inventory.json').read_text())
    support = json.loads((ROOT / 'catalog/formulas.json').read_text())
    implemented = {item['name']: item for item in support['functions']}
    catalog['source_catalogs'] = {
        'inventory_version': inventory['inventory_version'],
        'support_version': support['catalog_version'],
    }
    catalog['functions'] = {
        item['name']: {
            'name': item['name'], 'known': True, 'category': item['category'],
            **{language: implemented.get(item['name'], {}).get('status', {}).get(language, 'catalogued')
               for language in ('python', 'rust')},
        }
        for item in inventory['functions']
    }
    return catalog


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='refresh the projection and embedded Rust copy')
    args = parser.parse_args()
    catalog = ROOT / 'catalog/extraction-patterns.json'
    embedded = ROOT / 'rust/src/extraction_patterns.json'
    projected = projected_catalog()
    if args.write:
        content = json.dumps(projected, indent=2, ensure_ascii=False) + '\n'
        catalog.write_text(content)
        embedded.write_text(content)
    elif json.loads(catalog.read_text()) != projected or embedded.read_bytes() != catalog.read_bytes():
        raise SystemExit('Extraction mappings are stale; run tools/sync_extraction_catalog.py --write')
    print('Extraction formula mappings and embedded catalog match their sources.')


if __name__ == '__main__':
    main()
