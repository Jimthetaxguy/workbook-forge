"""The committed Excel evidence package against the current exporter.

``receipts/canonical-operating-scenario/operating-scenario.exported.xlsx`` is
the package the committed preflight receipt pins by hash. ``evidence.check_observation``
verifies hashes and consistency but never re-runs an engine, so once the writer
changes a single byte a byte-pinned receipt can never re-match, and nothing says
whether the change was a regression or a documented behaviour change.

This module is the regression link. It exports the fixture again with the
current writer, reads both packages with the project's own reader and compares
them semantically: sheet names, cell sets, every formula string, every stored
value, every style and every non-volatile cached value must be equal. A cell
whose formula calls a volatile function (``workbook_forge.volatile.VOLATILE_FUNCTIONS``)
is compared by the rule the Excel round-trip contract declares for that function
(``fixtures/excel-roundtrip-red-flags.contract.json``, allowlist policy
``volatile_predicate``): the cache must be present, of the declared type and
inside the declared range, not equal, because Excel's own value changes on every
recalculation too. The rule is data read from the contract; a volatile function
without a contract rule is reported, never waved through.

Finding (2026-11-06): with every cached value compared strictly, the only
difference between the committed package and today's export is ``Forecast!J1``
``=NOW()``. The committed package stores no cache for it; the current export
stores ``t="n"`` and a date serial, because volatile functions calculate since
PR #84. ``test_strict_comparison_reports_only_the_volatile_cache`` records that
finding and should be deleted when the committed package is regenerated.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest

from workbook_forge import analyze_formula
from workbook_forge.canonical_roundtrip import (
    DEFAULT_FIXTURE,
    RED_FLAG_CONTRACT,
    build_canonical_receipt,
)
from workbook_forge.volatile import VOLATILE_FUNCTIONS
from workbook_forge.xlsx import import_xlsx

ROOT = Path(__file__).resolve().parents[2]
COMMITTED_PACKAGE = ROOT / "receipts/canonical-operating-scenario/operating-scenario.exported.xlsx"
# The fixture's one volatile formula, =NOW().
VOLATILE_CELL = ("Forecast", "J1")
_COMPATIBILITY_PREFIXES = ("_XLFN._XLWS.", "_XLFN.", "_XLWS.", "_XLUDF.")
# Fields of a stored cell that must be equal in both packages.
EXACT_FIELDS = ("formula", "value", "style")

# sheet name -> address -> the stored cell as import_xlsx reads it
Cells = dict[str, dict[str, dict[str, Any]]]


@dataclass(frozen=True)
class Difference:
    """One place where the committed package and the fresh export disagree."""

    sheet: str
    address: str | None
    field: str
    committed: Any
    fresh: Any

    def __str__(self) -> str:
        where = self.sheet if self.address is None else f"{self.sheet}!{self.address}"
        return f"{where} {self.field}: committed {self.committed!r}, fresh {self.fresh!r}"


def read_cells(path: Path) -> Cells:
    """Every stored cell of a package, by sheet and address, through import_xlsx."""
    model = import_xlsx(path).to_dict()
    return {sheet["name"]: sheet["cells"] for sheet in model["sheets"]}


def _function_name(name: str) -> str:
    upper = name.upper()
    for prefix in _COMPATIBILITY_PREFIXES:
        if upper.startswith(prefix):
            return upper[len(prefix):]
    return upper


def volatile_functions(formula: str) -> frozenset[str]:
    """The volatile functions a formula calls; empty when Forge cannot parse it.

    An unparseable formula calculates nothing and is exported as text without a
    cache, so there is no volatile value to compare.
    """
    try:
        names = analyze_formula(formula).functions
    except (TypeError, ValueError, RecursionError):
        return frozenset()
    return frozenset(_function_name(name) for name in names) & VOLATILE_FUNCTIONS


def volatile_rules(contract: Path) -> dict[str, dict[str, Any]]:
    """Volatile function -> expected_result rule, from a round-trip contract.

    A rule is the ``expected_result`` of a check whose allowlist entry carries
    ``policy: volatile_predicate``. The function it governs is the volatile
    function the check's ``expected_formula`` calls.
    """
    document = json.loads(contract.read_text(encoding="utf-8"))
    checks = {check["id"]: check for check in document["checks"]}
    rules: dict[str, dict[str, Any]] = {}
    for entry in document.get("allowlist", []):
        if entry.get("policy") != "volatile_predicate":
            continue
        check = checks[entry["check_id"]]
        for function in volatile_functions(check["expected_formula"]):
            rules[function] = check["expected_result"]
    return rules


def _rule_violation(value: Any, rule: Mapping[str, Any], *, apply_predicate: bool) -> str | None:
    kind = rule["type"]
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{value!r} is not a number"
    elif kind == "boolean":
        if not isinstance(value, bool):
            return f"{value!r} is not a boolean"
    elif kind == "text":
        if not isinstance(value, str):
            return f"{value!r} is not text"
    else:
        return f"contract type {kind!r} is not comparable here"
    predicate = rule.get("predicate")
    if predicate is None or not apply_predicate:
        return None
    minimum, maximum = predicate.get("minimum"), predicate.get("maximum")
    if minimum is not None:
        if value < minimum or (value == minimum and not predicate.get("minimum_inclusive", True)):
            return f"{value!r} is below the contract minimum {minimum}"
    if maximum is not None:
        if value > maximum or (value == maximum and not predicate.get("maximum_inclusive", True)):
            return f"{value!r} is above the contract maximum {maximum}"
    return None


def semantic_differences(
    committed: Cells,
    fresh: Cells,
    *,
    volatile_rules: Mapping[str, Mapping[str, Any]] | None,
) -> list[Difference]:
    """Compare two packages cell by cell.

    With ``volatile_rules`` ``None`` every cached value is compared strictly.
    With rules, a cell that calls a volatile function is checked by rule: the
    fresh export must store a cache of the contract's type, and the contract's
    range is applied when the cell calls exactly one volatile function (a range
    describes that function's own result, not an expression over several). A
    committed cache, when present, is held to the same rule; an absent committed
    cache is accepted because the pinned package predates volatile calculation.
    """
    differences: list[Difference] = []
    if list(committed) != list(fresh):
        differences.append(Difference("workbook", None, "sheet_names", list(committed), list(fresh)))
    for sheet in committed:
        if sheet not in fresh:
            continue
        before, after = committed[sheet], fresh[sheet]
        if set(before) != set(after):
            differences.append(Difference(
                sheet, None, "addresses", sorted(set(before) - set(after)), sorted(set(after) - set(before)),
            ))
        for address in sorted(set(before) & set(after)):
            x, y = before[address], after[address]
            for field in EXACT_FIELDS:
                if x.get(field) != y.get(field):
                    differences.append(Difference(sheet, address, field, x.get(field), y.get(field)))
            formula = y.get("formula") if y.get("formula") is not None else x.get("formula")
            volatile = volatile_functions(formula) if formula is not None else frozenset()
            cached_x, cached_y = x.get("cached_value"), y.get("cached_value")
            if not volatile or volatile_rules is None:
                if cached_x != cached_y:
                    differences.append(Difference(sheet, address, "cached_value", cached_x, cached_y))
                continue
            unruled = sorted(volatile - set(volatile_rules))
            if unruled:
                differences.append(Difference(
                    sheet, address, "volatile_rule", None,
                    f"the contract declares no volatile_predicate rule for {', '.join(unruled)}",
                ))
                continue
            apply_predicate = len(volatile) == 1
            rules = [volatile_rules[function] for function in sorted(volatile)]
            if cached_y is None:
                differences.append(Difference(
                    sheet, address, "volatile_cache", cached_x, "no cached value for a volatile formula",
                ))
            else:
                for rule in rules:
                    reason = _rule_violation(cached_y, rule, apply_predicate=apply_predicate)
                    if reason:
                        differences.append(Difference(sheet, address, "volatile_cache", cached_x, reason))
            if cached_x is not None:
                for rule in rules:
                    reason = _rule_violation(cached_x, rule, apply_predicate=apply_predicate)
                    if reason:
                        differences.append(Difference(sheet, address, "volatile_cache", reason, cached_y))
    return differences


@pytest.fixture(scope="module")
def fresh_package(tmp_path_factory) -> Path:
    """Today's export of the fixture, through the same path as the committed receipt."""
    output = tmp_path_factory.mktemp("export-regression") / "fresh"
    build_canonical_receipt(DEFAULT_FIXTURE, output, run_excel=False, rust=False)
    return output / "operating-scenario.exported.xlsx"


@pytest.fixture(scope="module")
def packages(fresh_package) -> tuple[Cells, Cells]:
    return read_cells(COMMITTED_PACKAGE), read_cells(fresh_package)


@pytest.fixture(scope="module")
def rules() -> dict[str, dict[str, Any]]:
    return volatile_rules(RED_FLAG_CONTRACT)


def test_contract_declares_the_rule_for_the_fixture_volatile_cell(packages, rules):
    committed, _fresh = packages
    sheet, address = VOLATILE_CELL
    called = volatile_functions(committed[sheet][address]["formula"])
    assert called == {"NOW"}
    assert rules["NOW"]["type"] == "number"
    assert set(rules["NOW"]["predicate"]) >= {"minimum", "maximum"}


def test_canonical_export_matches_committed_package_semantically(packages, rules):
    committed, fresh = packages
    differences = semantic_differences(committed, fresh, volatile_rules=rules)
    assert not differences, "\n".join(str(item) for item in differences)


def test_strict_comparison_reports_only_the_volatile_cache(packages):
    """The 2026-11-06 finding: the committed package predates volatile calculation."""
    committed, fresh = packages
    differences = semantic_differences(committed, fresh, volatile_rules=None)
    assert [(item.sheet, item.address, item.field) for item in differences] == [
        (*VOLATILE_CELL, "cached_value"),
    ]
    (only,) = differences
    assert only.committed is None
    assert isinstance(only.fresh, float) and not isinstance(only.fresh, bool)


def _rename_sheet(cells: Cells) -> tuple[str, str | None]:
    cells["Renamed"] = cells.pop("Assumptions")
    return ("workbook", None)


def _drop_cell(cells: Cells) -> tuple[str, str | None]:
    del cells["Forecast"]["F2"]
    return ("Forecast", None)


def _change_formula(cells: Cells) -> tuple[str, str | None]:
    cells["Forecast"]["F2"]["formula"] = "SUM(C2:C5)"
    return ("Forecast", "F2")


def _change_cache(cells: Cells) -> tuple[str, str | None]:
    cells["Forecast"]["F2"]["cached_value"] = 7401
    return ("Forecast", "F2")


def _change_literal(cells: Cells) -> tuple[str, str | None]:
    cells["Assumptions"]["B1"]["value"] = 21
    return ("Assumptions", "B1")


def _change_style(cells: Cells) -> tuple[str, str | None]:
    cells["Forecast"]["F4"]["style"] = {"number_format": "0.000"}
    return ("Forecast", "F4")


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (_rename_sheet, "sheet_names"),
        (_drop_cell, "addresses"),
        (_change_formula, "formula"),
        (_change_cache, "cached_value"),
        (_change_literal, "value"),
        (_change_style, "style"),
    ],
    ids=["sheet-name", "missing-cell", "formula-string", "cached-value", "literal-value", "style"],
)
def test_comparison_fails_when_a_copy_is_mutated(
    packages, rules, mutate: Callable[[Cells], tuple[str, str | None]], field: str,
):
    committed, fresh = packages
    changed = copy.deepcopy(committed)
    sheet, address = mutate(changed)
    differences = semantic_differences(changed, fresh, volatile_rules=rules)
    assert (sheet, address, field) in {(item.sheet, item.address, item.field) for item in differences}, (
        "\n".join(str(item) for item in differences)
    )


@pytest.mark.parametrize(
    ("cache", "reason"),
    [
        (None, "no cached value"),
        ("12:00", "is not a number"),
        (True, "is not a number"),
        (5, "below the contract minimum"),
        (10**9, "above the contract maximum"),
    ],
    ids=["absent", "text", "boolean", "below-range", "above-range"],
)
def test_volatile_rule_requires_a_typed_cache_inside_the_contract_range(packages, rules, cache, reason):
    committed, fresh = packages
    sheet, address = VOLATILE_CELL
    changed = copy.deepcopy(fresh)
    if cache is None:
        changed[sheet][address].pop("cached_value", None)
    else:
        changed[sheet][address]["cached_value"] = cache
    differences = semantic_differences(committed, changed, volatile_rules=rules)
    assert len(differences) == 1
    assert (differences[0].sheet, differences[0].address, differences[0].field) == (sheet, address, "volatile_cache")
    assert reason in str(differences[0])


def test_a_volatile_function_without_a_contract_rule_is_reported(packages, rules):
    committed, fresh = packages
    sheet, address = VOLATILE_CELL
    without_now = {name: rule for name, rule in rules.items() if name != "NOW"}
    differences = semantic_differences(committed, fresh, volatile_rules=without_now)
    assert [(item.sheet, item.address, item.field) for item in differences] == [(sheet, address, "volatile_rule")]
    assert "NOW" in str(differences[0])
