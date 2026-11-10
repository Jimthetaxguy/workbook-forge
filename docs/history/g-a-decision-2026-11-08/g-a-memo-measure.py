"""Aggregate the Conversion records of spec 09 step 0 over the tree's fixtures.

Usage: measure_conversion.py ROOT
Runs from a checkout that holds python/workbook_forge/convert.py (PR #119).
Prints a JSON summary: for each direction, every dropped and added path kind
with the number of workbooks and the number of paths carrying it.
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(sys.argv[1])
sys.path.insert(0, str(ROOT / "python"))

from workbook_forge.workbook import WorkbookError  # noqa: E402
from workbook_forge.convert import ConversionError, to_canonical, to_toolkit  # noqa: E402
from workbook_forge.model import hydrate  # noqa: E402
from workbook_forge.xlsx import import_xlsx  # noqa: E402

KINDS = [
    ("model_version", r"^model_version$"),
    ("revision", r"^revision$"),
    ("workbook provenance", r"^provenance$"),
    ("metadata.source", r"^metadata\.source$"),
    ("metadata (other keys)", r"^metadata\..+$"),
    ("diagnostic", r"^diagnostics\[\d+\]$"),
    ("input binding (whole)", r"^bindings\.inputs\.[^.]+$"),
    ("input kind", r"^bindings\.inputs\.[^.]+\.kind$"),
    ("input required", r"^bindings\.inputs\.[^.]+\.required$"),
    ("input min/max/choices", r"^bindings\.inputs\.[^.]+\.(?:min|max|choices)$"),
    ("sheet id", r"^sheets\[\d+\]\.id$"),
    ("sheet dimensions", r"^sheets\[\d+\]\.dimensions$"),
    ("column width", r"^sheets\[\d+\]\.column_widths\.[A-Z]+$"),
    ("cell data_type", r"^sheets\[\d+\]\.cells\.[A-Z]+\d+\.data_type$"),
    ("cell provenance", r"^sheets\[\d+\]\.cells\.[A-Z]+\d+\.provenance$"),
    ("cell cached_value", r"^sheets\[\d+\]\.cells\.[A-Z]+\d+\.cached_value$"),
    ("cell blocked_reason", r"^sheets\[\d+\]\.cells\.[A-Z]+\d+\.blocked_reason$"),
    ("cell style.number_format (General)", r"^sheets\[\d+\]\.cells\.[A-Z]+\d+\.style\.number_format$"),
    ("cell style (bold, colours, alignment)", r"^sheets\[\d+\]\.cells\.[A-Z]+\d+\.style\.[a-z_]+$"),
    ("formula.dependencies", r"^sheets\[\d+\]\.cells\.[A-Z]+\d+\.formula\.dependencies$"),
    ("formula.result", r"^sheets\[\d+\]\.cells\.[A-Z]+\d+\.formula\.result$"),
]
KIND_RES = [(label, re.compile(pattern)) for label, pattern in KINDS]


def kind_of(path: str) -> str:
    for label, pattern in KIND_RES:
        if pattern.match(path):
            return label
    return "UNCLASSIFIED " + path


def tally(target: dict, name: str, paths: list[str]) -> None:
    kinds = Counter(kind_of(p) for p in paths)
    for kind, count in kinds.items():
        target[kind]["paths"] += count
        target[kind]["workbooks"].add(name)


def fresh():
    return defaultdict(lambda: {"paths": 0, "workbooks": set()})


def finish(table):
    return {
        kind: {"paths": entry["paths"], "workbooks": len(entry["workbooks"])}
        for kind, entry in sorted(table.items(), key=lambda item: (-item[1]["paths"], item[0]))
    }


summary = {"corpus": {}, "canonical_fixture": {}, "fixture_cases": {}}

# --- corpus: toolkit documents from import_xlsx --------------------------------
to_can_dropped, to_can_added = fresh(), fresh()
to_tk_dropped, to_tk_added = fresh(), fresh()
imported = converted = 0
refused_import = []
refused_convert = []
cells_total = 0
sheets_total = 0
workbooks_with_styles = set()
workbooks_with_widths = set()
workbooks_with_blocked = set()
diag_codes = Counter()
workbooks_with_diag = set()
for path in sorted((ROOT / "fixtures" / "corpus").glob("*.xlsx")):
    try:
        model = import_xlsx(path)
    except WorkbookError as error:
        refused_import.append((path.name, type(error).__name__))
        continue
    imported += 1
    document = model.to_dict()
    try:
        workbook, forward = to_canonical(document, model.envelope)
    except ConversionError as error:
        refused_convert.append((path.name, error.code))
        continue
    converted += 1
    sheets_total += len(workbook.sheets)
    cells_total += sum(len(s.cells) for s in workbook.sheets)
    tally(to_can_dropped, path.name, forward.dropped)
    tally(to_can_added, path.name, forward.added)
    for p in forward.dropped:
        k = kind_of(p)
        if k.startswith("cell style"):
            workbooks_with_styles.add(path.name)
        elif k == "column width":
            workbooks_with_widths.add(path.name)
        elif k == "cell blocked_reason":
            workbooks_with_blocked.add(path.name)
    for d in workbook.diagnostics:
        diag_codes[d.code] += 1
        workbooks_with_diag.add(path.name)
    back, backward = to_toolkit(workbook, input_kinds={})
    tally(to_tk_dropped, path.name, backward.dropped)
    tally(to_tk_added, path.name, backward.added)

summary["corpus"] = {
    "files": len(list((ROOT / "fixtures" / "corpus").glob("*.xlsx"))),
    "imported": imported,
    "refused_by_importer": refused_import,
    "converted": converted,
    "refused_by_canonical_reader": refused_convert,
    "sheets": sheets_total,
    "cells": cells_total,
    "workbooks_with_style_beyond_number_format_or_General": len(workbooks_with_styles),
    "workbooks_with_column_widths": len(workbooks_with_widths),
    "workbooks_with_blocked_reason": len(workbooks_with_blocked),
    "workbooks_with_analyzer_diagnostics": len(workbooks_with_diag),
    "diagnostic_codes": dict(diag_codes),
    "to_canonical": {"dropped": finish(to_can_dropped), "added": finish(to_can_added)},
    "to_toolkit": {"dropped": finish(to_tk_dropped), "added": finish(to_tk_added)},
}

# --- the canonical fixture: operating scenario --------------------------------
scenario = hydrate((ROOT / "fixtures" / "operating-scenario.workbook.json").read_bytes())
for label, kinds in (
    ("input_kinds={}", {}),
    ("input_kinds=all number", {name: "number" for name in scenario.bindings.inputs}),
):
    document, forward = to_toolkit(scenario, input_kinds=kinds)
    back, backward = to_canonical(document, None)
    fd, fa, bd, ba = fresh(), fresh(), fresh(), fresh()
    tally(fd, "scenario", forward.dropped)
    tally(fa, "scenario", forward.added)
    tally(bd, "scenario", backward.dropped)
    tally(ba, "scenario", backward.added)
    summary["canonical_fixture"][label] = {
        "to_toolkit": {"dropped": finish(fd), "added": finish(fa)},
        "back_to_canonical": {"dropped": finish(bd), "added": finish(ba)},
        "cell_provenance_after_round_trip": Counter(
            (c.provenance.origin if c.provenance else "none") for s in back.sheets for c in s.cells.values()
        ),
        "workbook_provenance_after_round_trip": back.provenance.to_dict() if back.provenance else None,
    }
scenario_cells = sum(len(s.cells) for s in scenario.sheets)
scenario_formula_cells = sum(1 for s in scenario.sheets for c in s.cells.values() if c.formula)
summary["canonical_fixture"]["cells"] = scenario_cells
summary["canonical_fixture"]["formula_cells"] = scenario_formula_cells
summary["canonical_fixture"]["bindings"] = {
    "inputs": {
        name: scenario.sheets[[s.name for s in scenario.sheets].index(t.sheet)].cells[t.address].data_type
        for name, t in scenario.bindings.inputs.items()
    },
    "outputs": len(scenario.bindings.outputs),
}

# --- the hand-authored fixture cases ------------------------------------------
cases = json.loads((ROOT / "fixtures" / "shape-conversion-cases.v1.json").read_text())
for group in ("canonical_cases", "toolkit_cases"):
    for case in cases[group]:
        conv = case["conversion"]
        summary["fixture_cases"][f"{group}/{case['name']}"] = {
            "dropped": dict(Counter(kind_of(p) for p in conv["dropped"])),
            "added": dict(Counter(kind_of(p) for p in conv["added"])),
        }

print(json.dumps(summary, indent=1, default=lambda o: sorted(o) if isinstance(o, set) else dict(o)))
