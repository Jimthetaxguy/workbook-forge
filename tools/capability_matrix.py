"""Join the catalogs into one feature x route x operation capability matrix.

Usage: python tools/capability_matrix.py [--write | --check] [--out catalog/capability-matrix.v0.json]

Workbook Forge states what it supports in several files that nothing joins:
the function catalog, the corpus report and its preserved-only tier, the source
compiler's function table, the agent operation catalog, the browser protocol
and the primitive table. This tool reads only those tracked files (the
TypeScript protocol is parsed with a regular expression, never run) and writes
``catalog/capability-matrix.v0.json`` so a caller can ask "does feature X work
for operation Y on route Z, and with what evidence" instead of reading a
function count. It decides nothing: every status is taken from an input or is
``untested``. ``--check`` exits 1 when the committed file differs from a fresh
generation; python/tests/test_capability_matrix.py runs the same comparison.

The document has ``schema_version`` 0, ``generated_from`` (each input's path
and sha256), the ``routes`` table (the operations each route offers), and
``rows``. There is one row for every feature, route and operation, so a lookup
needs no default. A row is ``kind``, ``feature``, ``route``, ``operation``,
``status``, ``evidence_grade`` and ``source`` (the input that established the
status; ``tools/capability_matrix.py`` when a rule in this file did).
``evidence_grade`` is the function catalog's grade only on an evaluate row
the catalog established, and ``none`` on every other row.

Features are of three kinds:

- ``function``: every entry of ``catalog/formulas.json``. Its catalog
  ``evidence_grade`` is recorded on the evaluate rows for python-toolkit,
  python-canonical and rust when that route's status is ``supported``.
- ``category``: every category in the committed corpus report's Matrix table.
- ``primitive``: the eleven primitives of ``catalog/workbook-capabilities.json``.

Statuses: ``supported``, ``preserved`` (kept through the route, not editable or
calculated), ``refused:<code>`` (a typed refusal; several codes in one category
are sorted and comma-separated), ``untested`` (no input establishes it; never a
guess) and ``not-applicable`` (the route offers no such operation, or the kind
of feature or the input does not speak to it).

Rules, in the order the code applies them:

1. A route offers the operations in ``ROUTE_OPERATIONS``; any other operation
   on that route is ``not-applicable``. The engine routes offer the corpus
   stages (inspect, preserve, evaluate, edit, reimport, export); the canonical
   model writes a fresh package by design and so has no preserve operation;
   the source routes offer emit-source, evaluate, reimport and export; the
   agent route offers inspect, edit and export, derived from the operation
   catalog's ``effect`` fields; the browser route offers inspect, evaluate,
   edit and export, derived from the protocol command names. Both derived
   sets are checked against the table. The browser ``calculate`` command is
   evaluate. No catalog grades that command, so every browser-protocol
   evaluate cell is ``untested``.
2. Functions answer evaluate and emit-source. ``evaluate`` is ``supported`` on
   python-toolkit, python-canonical and rust when the catalog status for that
   language is ``conformance-tested`` (the two Python routes share one engine),
   and ``untested`` on wasm-browser and browser-protocol, which the catalog
   does not grade. The catalog ``evidence_grade`` is recorded on those
   ``supported`` evaluate rows and is ``none`` on ``untested``,
   ``not-applicable`` and emit-source rows.
   ``emit-source`` on the source routes is ``supported`` for
   ``FUNCTIONS_BY_VERSION[6]`` and otherwise the refusal source_graph.py raises
   for any other call; ``evaluate`` there is ``untested`` for those five and
   ``not-applicable`` for the rest. Package operations are claims about corpus
   categories, so they are ``not-applicable`` for a function.
3. Categories take each engine's outcome from the report: ``py_toolkit`` is
   python-toolkit, ``py_canonical`` is python-canonical and ``rust`` is both
   rust and wasm-browser (the Rust lane is also the WASM engine). A category
   with a refused file is ``refused:<codes>`` for every operation the route
   offers. A category in the preserved-only tier is inspected, preserved,
   exported and reimported (``supported``) but ``preserved`` for edit and
   evaluate; the canonical model does not carry that content, so on
   python-canonical only inspect is ``supported`` and the rest is
   ``not-applicable``. Any other category is ``supported`` for the corpus
   stages and ``untested`` for evaluate, because the report counts calculation
   diagnostics per file, not per category. The source, agent and browser
   routes are ``untested`` for every category. The report is checked against
   ``tools/corpus/expectations.json`` first; a mismatch or a defect outcome
   stops generation.
4. Primitives map their booleans onto python-toolkit and rust, the two
   independent implementations of the toolkit profile: read is inspect,
   calculate is evaluate, transform is edit, export is export; ``construct``
   has no operation in this matrix. A false boolean is a declared boundary,
   recorded as ``not-applicable`` with the table as its source.

Rows are sorted by kind, feature, then route and operation in the order above.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "corpus"))
import capabilities  # noqa: E402

SCHEMA_VERSION = 0
SELF = "tools/capability_matrix.py"
DEFAULT_OUT = ROOT / "catalog" / "capability-matrix.v0.json"

INPUTS = {
    "formulas": "catalog/formulas.json",
    "primitives": "catalog/workbook-capabilities.json",
    "report": "docs/corpus-report.md",
    "expectations": "tools/corpus/expectations.json",
    "tiers": "tools/corpus/capabilities.py",
    "source_graph": "python/workbook_forge/source_graph.py",
    "agent": "catalog/agent-operations.json",
    "protocol": "web/forge-engine/src/protocol.ts",
}

KINDS = ("category", "function", "primitive")
ROUTES = (
    "python-toolkit", "python-canonical", "rust", "wasm-browser",
    "source-python", "source-rust", "agent-protocol", "browser-protocol",
)
OPERATIONS = ("inspect", "preserve", "evaluate", "edit", "emit-source", "reimport", "export")
STATUSES = ("supported", "preserved", "untested", "not-applicable")  # plus refused:<code>

CORPUS_STAGES = ("inspect", "preserve", "evaluate", "edit", "reimport", "export")
SOURCE_OPERATIONS = ("emit-source", "evaluate", "reimport", "export")
PROTOCOL_OPERATIONS = ("inspect", "edit", "export")
# `calculate` is evaluate. No catalog grades that command, so those cells are untested.
BROWSER_OPERATIONS = ("inspect", "evaluate", "edit", "export")
ROUTE_OPERATIONS = {
    "python-toolkit": CORPUS_STAGES,
    "python-canonical": tuple(op for op in CORPUS_STAGES if op != "preserve"),
    "rust": CORPUS_STAGES,
    "wasm-browser": CORPUS_STAGES,
    "source-python": SOURCE_OPERATIONS,
    "source-rust": SOURCE_OPERATIONS,
    "agent-protocol": PROTOCOL_OPERATIONS,
    "browser-protocol": BROWSER_OPERATIONS,
}

# Corpus engine column -> routes it establishes. The Rust lane runs the same
# reader the WASM engine uses (tools/corpus_check.py).
ENGINE_ROUTES = {
    "py_toolkit": ("python-toolkit",),
    "py_canonical": ("python-canonical",),
    "rust": ("rust", "wasm-browser"),
}
FUNCTION_STATUS_LANGUAGE = {"python-toolkit": "python", "python-canonical": "python", "rust": "rust"}
SOURCE_ROUTES = ("source-python", "source-rust")
SOURCE_PROFILE = 6
# Agent operation `effect` -> matrix operation (catalog/agent-operations.json).
AGENT_EFFECTS = {"read": "inspect", "preview": "edit", "edit": "edit", "write_file": "export"}
# Browser command -> matrix operation. None marks session lifecycle commands
# (open a workbook, create one, subscribe to events, replay a log) that are
# not one of the seven operations, and `copyFormulas`, which rewrites formula
# text for a move and neither reads nor changes a workbook. `calculate` is
# evaluate; no catalog grades it.
BROWSER_COMMANDS = {
    "open": None, "newWorkbook": None, "snapshot": "inspect", "applyEdits": "edit",
    "calculate": "evaluate", "export": "export", "applyAndCalculate": "edit", "log": "inspect",
    "subscribe": None, "replay": None, "copyFormulas": None,
}
# Primitive boolean -> matrix operation; `construct` has no counterpart.
PRIMITIVE_FLAGS = {"read": "inspect", "calculate": "evaluate", "transform": "edit", "export": "export"}
PRIMITIVE_ROUTES = ("python-toolkit", "rust")
DEFECTS = {"crash", "corrupt", "lossy", "untyped"}


def read_text(key: str) -> str:
    return (ROOT / INPUTS[key]).read_text(encoding="utf-8")


def read_json(key: str) -> dict:
    return json.loads(read_text(key))


def read_functions_by_version() -> dict[int, tuple[str, ...]]:
    """FUNCTIONS_BY_VERSION as the module text states it, without importing the module."""
    for node in ast.parse(read_text("source_graph")).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "FUNCTIONS_BY_VERSION" for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise ValueError(f"{INPUTS['source_graph']} does not assign FUNCTIONS_BY_VERSION")


def read_source_refusal_code() -> str:
    """The code source_graph.py raises for a call outside the profile's function table."""
    match = re.search(r'fail\("([a-z0-9_]+)",\s*f"source profile v\{version\} cannot lower', read_text("source_graph"))
    if match is None:
        raise ValueError(f"{INPUTS['source_graph']} no longer refuses unknown calls with a typed code")
    return match.group(1)


def read_agent_operations() -> dict[str, str]:
    """{operation name: matrix operation} from the agent catalog's effects."""
    mapped = {}
    for operation in read_json("agent")["operations"]:
        effect = operation["effect"]
        if effect not in AGENT_EFFECTS:
            raise ValueError(f"agent operation {operation['name']} has an unmapped effect {effect!r}")
        mapped[operation["name"]] = AGENT_EFFECTS[effect]
    offered = tuple(op for op in OPERATIONS if op in set(mapped.values()))
    if offered != ROUTE_OPERATIONS["agent-protocol"]:
        raise ValueError(f"agent catalog offers {offered}, ROUTE_OPERATIONS says {ROUTE_OPERATIONS['agent-protocol']}")
    return mapped


def read_browser_commands() -> list[str]:
    """Command names of the `Commands` interface in protocol.ts, parsed as text."""
    text = read_text("protocol")
    match = re.search(r"export interface Commands \{(.*?)\n\}", text, re.S)
    if match is None:
        raise ValueError(f"{INPUTS['protocol']} has no Commands interface")
    names = re.findall(r"^\s+(\w+): \{ args:", match.group(1), re.M)
    if set(names) != set(BROWSER_COMMANDS):
        raise ValueError(f"protocol commands {sorted(names)} differ from BROWSER_COMMANDS {sorted(BROWSER_COMMANDS)}")
    offered = tuple(op for op in OPERATIONS if op in {v for v in BROWSER_COMMANDS.values() if v})
    if offered != ROUTE_OPERATIONS["browser-protocol"]:
        raise ValueError(f"protocol offers {offered}, ROUTE_OPERATIONS says {ROUTE_OPERATIONS['browser-protocol']}")
    return names


def read_corpus() -> tuple[dict, dict]:
    """(results, manifest) from the report, checked against expectations.json."""
    results, manifest = capabilities.parse_report(read_text("report"))
    expectations = read_json("expectations")
    for name, row in results.items():
        for engine in capabilities.ENGINES:
            record = row.get(engine, {})
            outcome = record.get("outcome")
            if outcome in DEFECTS:
                raise ValueError(f"the corpus report records {outcome} for {name} in {engine}; fix it before generating")
            expected = expectations.get(name, {}).get(engine)
            actual = f"refuse:{record.get('code')}" if outcome == "refuse" else None
            if expected != actual:
                raise ValueError(f"{name} {engine}: report says {actual}, expectations.json says {expected}")
    return results, manifest


def sha256(key: str) -> str:
    return hashlib.sha256((ROOT / INPUTS[key]).read_bytes()).hexdigest()


def row_key(row: dict) -> tuple:
    return (KINDS.index(row["kind"]), row["feature"], ROUTES.index(row["route"]), OPERATIONS.index(row["operation"]))


def evidence_grade(kind: str, route: str, operation: str, status: str, grade: str) -> str:
    """The catalog grade only on an evaluate row the catalog established."""
    if (
        kind == "function"
        and operation == "evaluate"
        and route in FUNCTION_STATUS_LANGUAGE
        and status == "supported"
    ):
        return grade
    return "none"


class Rows:
    """Collects one row per feature, route and operation; unset cells become not-applicable."""

    def __init__(self) -> None:
        self.cells: dict[tuple[str, str, str, str], tuple[str, str]] = {}
        self.grades: dict[tuple[str, str], str] = {}

    def feature(self, kind: str, name: str, grade: str = "none") -> None:
        self.grades[(kind, name)] = grade

    def set(self, kind: str, name: str, route: str, operation: str, status: str, source: str) -> None:
        if operation not in ROUTE_OPERATIONS[route]:
            raise ValueError(f"{route} does not offer {operation}")
        self.cells[(kind, name, route, operation)] = (status, source)

    def rows(self) -> list[dict]:
        out = []
        for (kind, name), grade in self.grades.items():
            for route in ROUTES:
                for operation in OPERATIONS:
                    status, source = self.cells.get((kind, name, route, operation), ("not-applicable", SELF))
                    out.append({
                        "kind": kind, "feature": name, "route": route, "operation": operation,
                        "status": status,
                        "evidence_grade": evidence_grade(kind, route, operation, status, grade),
                        "source": source,
                    })
        return sorted(out, key=row_key)


def add_functions(rows: Rows) -> None:
    source_functions = set(read_functions_by_version()[SOURCE_PROFILE])
    refusal = f"refused:{read_source_refusal_code()}"
    for entry in read_json("formulas")["functions"]:
        name = entry["name"]
        rows.feature("function", name, entry["evidence_grade"])
        for route, language in FUNCTION_STATUS_LANGUAGE.items():
            tested = entry["status"].get(language) == "conformance-tested"
            rows.set("function", name, route, "evaluate", "supported" if tested else "untested",
                     INPUTS["formulas"] if tested else SELF)
        rows.set("function", name, "wasm-browser", "evaluate", "untested", SELF)
        for route in SOURCE_ROUTES:
            if name in source_functions:
                rows.set("function", name, route, "emit-source", "supported", INPUTS["source_graph"])
                rows.set("function", name, route, "evaluate", "untested", SELF)
            else:
                rows.set("function", name, route, "emit-source", refusal, INPUTS["source_graph"])


def add_categories(rows: Rows) -> None:
    results, manifest = read_corpus()
    files_by_category: dict[str, list[str]] = {}
    for name, info in sorted(manifest.items()):
        for category in info["categories"]:
            files_by_category.setdefault(category, []).append(name)
    for category, files in sorted(files_by_category.items()):
        rows.feature("category", category)
        for engine, routes in ENGINE_ROUTES.items():
            codes = sorted({results[f][engine]["code"] for f in files if results[f][engine]["outcome"] == "refuse"})
            for route in routes:
                for operation in ROUTE_OPERATIONS[route]:
                    if codes:
                        rows.set("category", category, route, operation, "refused:" + ",".join(codes), INPUTS["report"])
                    elif category in capabilities.PRESERVED_ONLY:
                        if route == "python-canonical":
                            if operation == "inspect":
                                rows.set("category", category, route, operation, "supported", INPUTS["report"])
                            else:
                                rows.set("category", category, route, operation, "not-applicable", INPUTS["tiers"])
                        elif operation in ("edit", "evaluate"):
                            rows.set("category", category, route, operation, "preserved", INPUTS["tiers"])
                        else:
                            rows.set("category", category, route, operation, "supported", INPUTS["report"])
                    elif operation == "evaluate":
                        rows.set("category", category, route, operation, "untested", SELF)
                    else:
                        rows.set("category", category, route, operation, "supported", INPUTS["report"])
        for route in (*SOURCE_ROUTES, "agent-protocol", "browser-protocol"):
            for operation in ROUTE_OPERATIONS[route]:
                rows.set("category", category, route, operation, "untested", SELF)


def add_primitives(rows: Rows) -> None:
    for entry in read_json("primitives")["primitives"]:
        rows.feature("primitive", entry["id"])
        for route in PRIMITIVE_ROUTES:
            for flag, operation in PRIMITIVE_FLAGS.items():
                status = "supported" if entry[flag] is True else "not-applicable"
                rows.set("primitive", entry["id"], route, operation, status, INPUTS["primitives"])


def fill_browser_evaluate(rows: Rows) -> None:
    """protocol.ts ``calculate`` is evaluate. No input grades that command."""
    for kind, name in rows.grades:
        if (kind, name, "browser-protocol", "evaluate") not in rows.cells:
            rows.set(kind, name, "browser-protocol", "evaluate", "untested", SELF)


def build() -> dict:
    read_agent_operations()
    read_browser_commands()
    rows = Rows()
    add_categories(rows)
    add_functions(rows)
    add_primitives(rows)
    fill_browser_evaluate(rows)
    table = rows.rows()
    features = {kind: 0 for kind in KINDS}
    for kind, _name in rows.grades:
        features[kind] += 1
    statuses: dict[str, int] = {}
    for row in table:
        status = "refused" if row["status"].startswith("refused:") else row["status"]
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": f"python {SELF} --write",
        "generated_from": [{"path": INPUTS[key], "sha256": sha256(key)} for key in sorted(INPUTS, key=INPUTS.get)],
        "routes": {route: list(ops) for route, ops in ROUTE_OPERATIONS.items()},
        "operations": list(OPERATIONS),
        "summary": {
            "features": features,
            "rows": len(table),
            "statuses": {status: statuses[status] for status in sorted(statuses)},
        },
        "rows": table,
    }


def render(document: dict | None = None) -> str:
    """The document as text: an indented header and one compact row per line."""
    document = build() if document is None else document
    header = json.dumps({key: value for key, value in document.items() if key != "rows"}, indent=2, ensure_ascii=False)
    assert header.endswith("\n}")
    lines = [header[:-2] + ",", '  "rows": [']
    lines.append(",\n".join("    " + json.dumps(row, separators=(",", ":"), ensure_ascii=False) for row in document["rows"]))
    lines += ["  ]", "}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="write the matrix to --out")
    mode.add_argument("--check", action="store_true", help="exit 1 when --out differs from a fresh generation")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="the committed matrix (default: %(default)s)")
    args = parser.parse_args(argv)
    out = Path(args.out)
    text = render()
    if args.write:
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
        return 0
    if args.check:
        committed = out.read_text(encoding="utf-8") if out.exists() else None
        if committed == text:
            print(f"{out} is current")
            return 0
        print(f"{out} differs from a fresh generation; run python {SELF} --write")
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
