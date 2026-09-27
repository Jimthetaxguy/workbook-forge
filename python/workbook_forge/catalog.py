"""Access the source-linked Excel 365 function inventory and support states."""

from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import sysconfig
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]

# Permissive SPDX licenses with no copyleft terms. The same list appears in the
# open-source pattern schema and the README license policy; tests keep them aligned.
PERMISSIVE_LICENSES = frozenset({
    "0BSD", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "BSL-1.0", "CC0-1.0", "ISC",
    "MIT", "MIT-0", "PSF-2.0", "Unicode-3.0", "Unicode-DFS-2016", "Unlicense", "Zlib",
})


def _catalog_file(name: str) -> Path:
    """Find canonical source-tree data or its installed share-directory copy."""
    candidates = (
        _ROOT / "catalog" / name,
        Path(sysconfig.get_path("data")) / "share" / "workbook_forge" / "catalog" / name,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Workbook Forge catalog file is missing: {name}")


class CatalogError(ValueError):
    """The checked-in catalog is malformed or internally inconsistent."""


@lru_cache(maxsize=1)
def _load() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    inventory = json.loads(_catalog_file("function_inventory.json").read_text(encoding="utf-8"))
    support = json.loads(_catalog_file("formulas.json").read_text(encoding="utf-8"))
    schema_name = support.get("catalog_schema")
    if not isinstance(schema_name, str):
        raise CatalogError("support catalog must name its schema")
    try:
        json.loads(_catalog_file(schema_name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"support catalog schema is missing or invalid: {schema_name}") from exc
    functions = inventory.get("functions")
    if not isinstance(functions, list) or inventory.get("function_count") != len(functions):
        raise CatalogError("function inventory count does not match its records")
    by_name: dict[str, dict[str, Any]] = {}
    for item in functions:
        if not isinstance(item.get("name"), str) or not isinstance(item.get("category"), str):
            raise CatalogError("function inventory entries require name and category")
        key = item["name"].upper()
        if key in by_name:
            raise CatalogError(f"duplicate function name: {key}")
        by_name[key] = item
    supported: dict[str, dict[str, Any]] = {}
    for item in support.get("functions", []):
        if not isinstance(item.get("name"), str):
            raise CatalogError("support entries require a function name")
        key = item["name"].upper()
        if key in supported or key not in by_name:
            raise CatalogError(f"duplicate or unknown supported function: {key}")
        arity = item.get("arity", {})
        if not isinstance(arity.get("min"), int) or not isinstance(arity.get("max"), int):
            raise CatalogError(f"function {key} requires integer minimum and maximum arity")
        if arity["min"] < 0 or arity["max"] < arity["min"]:
            raise CatalogError(f"function {key} has an invalid arity interval")
        status = item.get("status", {})
        if any(value not in {"catalogued", "parsed", "evaluated", "conformance-tested"} for value in status.values()):
            raise CatalogError(f"function {key} has an unknown support status")
        supported[key] = item

    semantic_specs = support.get("semantic_specs", {})
    if not isinstance(semantic_specs, dict):
        raise CatalogError("semantic_specs must be an object keyed by function name")
    source_ids = {source.get("id") for source in support.get("sources", []) if isinstance(source, dict)}
    for name, spec in semantic_specs.items():
        key = name.upper()
        if key not in supported or not isinstance(spec, dict):
            raise CatalogError(f"semantic spec has no supported function: {key}")
        if not isinstance(spec.get("arguments"), list) or not isinstance(spec.get("returns"), dict):
            raise CatalogError(f"semantic spec for {key} requires arguments and returns")
        if not isinstance(spec.get("semantics"), dict):
            raise CatalogError(f"semantic spec for {key} requires a semantics object")
        references = spec.get("source_refs")
        if not isinstance(references, list) or any(reference not in source_ids for reference in references):
            raise CatalogError(f"semantic spec for {key} has missing or unknown source references")
        if any(not isinstance(argument, dict) or not isinstance(argument.get("name"), str) for argument in spec["arguments"]):
            raise CatalogError(f"semantic spec for {key} has an invalid argument record")

    return inventory, {
        name: {
            **item,
            "implementation": supported.get(name),
            "semantic_spec": semantic_specs.get(name),
        }
        for name, item in by_name.items()
    }


@lru_cache(maxsize=1)
def _load_open_source_patterns() -> dict[str, Any]:
    patterns = json.loads(_catalog_file("open_source_patterns.json").read_text(encoding="utf-8"))
    schema_name = patterns.get("catalog_schema")
    if not isinstance(schema_name, str):
        raise CatalogError("open-source pattern catalog must name its schema")
    try:
        json.loads(_catalog_file(schema_name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"open-source pattern schema is missing or invalid: {schema_name}") from exc

    policy = patterns.get("eligibility_policy", {})
    allowed_licenses = set(policy.get("allowed_licenses", []))
    if (
        not allowed_licenses
        or not allowed_licenses <= PERMISSIVE_LICENSES
        or policy.get("code_copied") is not False
    ):
        raise CatalogError("open-source pattern catalog violates its license or code-copy policy")
    sources = patterns.get("sources", [])
    source_ids = {source.get("id") for source in sources if isinstance(source, dict)}
    if len(source_ids) != len(sources):
        raise CatalogError("open-source pattern sources require unique ids")
    for source in sources:
        licenses = source.get("licenses", [])
        if not licenses or not set(licenses) <= allowed_licenses:
            raise CatalogError(f"open-source pattern source {source.get('id')} has a disallowed license")
    pattern_ids: set[str] = set()
    for pattern in patterns.get("patterns", []):
        pattern_id = pattern.get("id")
        if not isinstance(pattern_id, str) or pattern_id in pattern_ids:
            raise CatalogError("open-source patterns require unique string ids")
        pattern_ids.add(pattern_id)
        if not set(pattern.get("source_refs", [])) <= source_ids:
            raise CatalogError(f"open-source pattern {pattern_id} has an unknown source reference")
        if pattern.get("code_copied") is not False:
            raise CatalogError(f"open-source pattern {pattern_id} records copied code")
    return patterns


def inventory_metadata() -> dict[str, Any]:
    """Return a shallow copy of version, profile, source, and refresh metadata."""
    inventory, _ = _load()
    return {key: value for key, value in inventory.items() if key != "functions"}


def list_functions(category: str | None = None) -> tuple[dict[str, Any], ...]:
    """List catalog entries; optional category matches Microsoft's label case-insensitively."""
    _, functions = _load()
    records = functions.values()
    if category is not None:
        records = (item for item in records if item["category"].casefold() == category.casefold())
    return tuple(dict(item) for item in records)


def lookup_function(name: str) -> dict[str, Any] | None:
    """Find a function by its canonical listed name, case-insensitively."""
    _, functions = _load()
    found = functions.get(name.upper())
    return dict(found) if found is not None else None


def function_status(name: str, language: str) -> str:
    """Return `catalogued`, `parsed`, `evaluated`, or `conformance-tested`.

    The checked-in function inventory is the broader catalog. `formulas.json`
    carries implementation claims, which are deliberately reviewed separately.
    """
    if language not in {"python", "rust"}:
        raise ValueError("language must be 'python' or 'rust'")
    entry = lookup_function(name)
    if entry is None:
        raise KeyError(name)
    implementation = entry["implementation"]
    if implementation is None:
        return "catalogued"
    return implementation["status"].get(language, "catalogued")


def implementation_patterns() -> dict[str, Any]:
    """Return the provenance-tracked, permissively licensed implementation-pattern catalog."""
    return deepcopy(_load_open_source_patterns())
