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

# Registry packages are reviewed by exact version and content checksum, including
# the Python bridge's transitive dependencies. Evidence lives with the catalog.
RUST_LICENSE_REVIEW = ROOT / "catalog" / "dependency-licenses.json"


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
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = configuration["project"]
    declared = [*project.get("dependencies", []), *project.get("optional-dependencies", {}).get("test", []), *configuration["build-system"]["requires"]]
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
    evidence = json.loads(RUST_LICENSE_REVIEW.read_text())["packages"]
    reviewed = {(entry["name"], entry["version"]): entry for entry in evidence}
    assert len(reviewed) == len(evidence), "duplicate dependency review records"
    problems = []
    for manifest in ("rust", "native"):
        lock = tomllib.loads((ROOT / manifest / "Cargo.lock").read_text())
        for package in lock["package"]:
            if "source" not in package:
                assert package["name"] in {"workbook_forge", "workbook_forge_python"}
                continue
            record = reviewed.get((package["name"], package["version"]))
            if record is None:
                problems.append(f"unreviewed: {package['name']} {package['version']}")
                continue
            assert package["source"] == "registry+https://github.com/rust-lang/crates.io-index"
            assert record["registry_checksum"] == package["checksum"]
            assert _spdx_is_permissive(record["license"]), record
            assert record["license_files"], record
    assert not problems, "\n".join(problems)
