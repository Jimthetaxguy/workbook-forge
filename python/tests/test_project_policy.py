"""Project policy checks: catalog schemas and the third-party license rule.

The license rule (README, "License and source policy") allows only permissive
open-source licenses with no copyleft terms. It applies to runtime and
development dependencies alike, including transitive ones.
"""

import json
import re
import tomllib
from importlib import metadata
from pathlib import Path

import jsonschema
import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from workbook_forge.catalog import PERMISSIVE_LICENSES

ROOT = Path(__file__).parents[2]

# Trove license classifiers that map to a permissive SPDX identifier.
PERMISSIVE_CLASSIFIERS = {
    "Apache Software License": "Apache-2.0",
    "BSD License": "BSD-3-Clause",
    "Boost Software License 1.0 (BSL-1.0)": "BSL-1.0",
    "ISC License (ISCL)": "ISC",
    "MIT License": "MIT",
    "MIT No Attribution License (MIT-0)": "MIT-0",
    "Python Software Foundation License": "PSF-2.0",
    "The Unlicense (Unlicense)": "Unlicense",
    "zlib/libpng License": "Zlib",
}
COPYLEFT_TEXT = re.compile(r"GPL|General Public|Mozilla|\bMPL\b|\bEPL\b|Eclipse Public|EUPL|CDDL|SSPL|BUSL")
PERMISSIVE_TEXT = re.compile(r"\b(MIT|BSD|Apache|ISC|PSF|Python Software Foundation|Unlicense|Zlib)\b")

# Rust crates that have passed a license review. Add a crate only after checking
# that its license, and its dependencies' licenses, satisfy the rule above.
REVIEWED_RUST_CRATES: frozenset[str] = frozenset()


@pytest.mark.parametrize(
    ("data_name", "schema_name"),
    [
        ("formulas.json", "formula-support.schema.json"),
        ("function_inventory.json", "function-inventory.schema.json"),
        ("open_source_patterns.json", "open-source-patterns.schema.json"),
    ],
)
def test_catalogs_validate_against_their_schemas(data_name, schema_name):
    schema = json.loads((ROOT / "catalog" / schema_name).read_text(encoding="utf-8"))
    data = json.loads((ROOT / "catalog" / data_name).read_text(encoding="utf-8"))
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    validator = validator_class(schema, format_checker=jsonschema.FormatChecker())
    errors = sorted(validator.iter_errors(data), key=lambda error: list(error.absolute_path))
    assert not errors, "\n".join(f"{list(e.absolute_path)}: {e.message}" for e in errors[:10])


def _spdx_is_permissive(expression: str) -> bool:
    """Evaluate an SPDX expression: OR needs one permissive option, AND needs all."""
    tokens = re.findall(r"\(|\)|[^\s()]+", expression)
    position = 0

    def parse_or() -> bool:
        nonlocal position
        result = parse_and()
        while position < len(tokens) and tokens[position].upper() == "OR":
            position += 1
            result = parse_and() or result
        return result

    def parse_and() -> bool:
        nonlocal position
        result = parse_term()
        while position < len(tokens) and tokens[position].upper() == "AND":
            position += 1
            result = parse_term() and result
        return result

    def parse_term() -> bool:
        nonlocal position
        token = tokens[position]
        position += 1
        if token == "(":
            result = parse_or()
            position += 1  # closing parenthesis
            return result
        if position < len(tokens) and tokens[position].upper() == "WITH":
            position += 2  # a license exception does not change the base license
        return token.removesuffix("+") in PERMISSIVE_LICENSES

    return parse_or()


def _license_verdict(distribution: metadata.Distribution) -> tuple[str, bool]:
    fields = distribution.metadata
    expression = fields.get("License-Expression")
    if expression:
        return expression, _spdx_is_permissive(expression)
    classifiers = [
        classifier.split(" :: ")[-1]
        for classifier in fields.get_all("Classifier") or []
        if classifier.startswith("License ::")
    ]
    permissive = [PERMISSIVE_CLASSIFIERS[name] for name in classifiers if name in PERMISSIVE_CLASSIFIERS]
    if permissive:
        return " OR ".join(permissive), True
    text = fields.get("License") or ""
    if text and PERMISSIVE_TEXT.search(text) and not COPYLEFT_TEXT.search(text):
        return text.splitlines()[0][:60], True
    return ("; ".join(classifiers) or text[:60] or "no license metadata"), False


def test_python_dependencies_use_permissive_licenses():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = [*project.get("dependencies", []), *project.get("optional-dependencies", {}).get("test", [])]
    assert declared, "pyproject.toml should declare the test dependencies the suite needs"
    pending = [Requirement(requirement) for requirement in declared]
    checked: dict[str, str] = {}
    problems = []
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        if name in checked:
            continue
        try:
            distribution = metadata.distribution(requirement.name)
        except metadata.PackageNotFoundError:
            problems.append(f"{requirement.name} is required but not installed, so its license cannot be checked")
            continue
        license_text, permissive = _license_verdict(distribution)
        checked[name] = license_text
        if not permissive:
            problems.append(f"{name} {distribution.version}: {license_text!r} is not a permissive, copyleft-free license")
        extras = requirement.extras or {""}
        for raw in distribution.requires or []:
            child = Requirement(raw)
            if child.marker is None or any(child.marker.evaluate({"extra": extra}) for extra in extras):
                pending.append(child)
    assert not problems, "\n".join(problems)


def test_rust_crate_has_no_unreviewed_third_party_dependencies():
    lock = (ROOT / "rust" / "Cargo.lock").read_text(encoding="utf-8")
    crates = set(re.findall(r'^name = "([^"]+)"', lock, flags=re.MULTILINE))
    unreviewed = sorted(crates - {"workbook_forge"} - REVIEWED_RUST_CRATES)
    assert not unreviewed, (
        f"Rust crates need a license review (permissive, no copyleft) before use: {unreviewed}. "
        "After the review, add them to REVIEWED_RUST_CRATES."
    )
