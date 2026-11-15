"""Module layering in both languages, checked from the source text.

Each package has an order from the modules everything rests on (the catalog,
the formula evaluator) up to the entry points (the agent adapters, the command
line, the browser session). A module may use the modules listed before it.
The few places where the code goes the other way today are listed by name, so
the list can shrink and cannot grow without someone deciding it should.

Two further rules protect what makes the evaluators reusable: they do no file
or process work of their own, and the generated source runtimes stand alone.

The check reads imports and ``crate::`` paths as text. It sees direct uses
only, and it does not load any module, so it runs the same with or without
the native bridge.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
PYTHON_PACKAGE = ROOT / "python" / "workbook_forge"
RUST_SOURCE = ROOT / "rust" / "src"

# Lowest layer first. ``__init__`` is the formula evaluator, which lives in
# the package root. A name with no file yet (a module another branch adds) is
# ignored until the file exists.
PYTHON_ORDER = [
    "evidence",
    "catalog",
    "volatile",
    "source_contract",
    "xml_patterns",
    "__init__",
    "expressions",
    "primitives",
    "python_engine",
    "workbook",
    "xlsx_features",
    "toolkit",
    "model",
    "envelope",
    "xlsx",
    "convert",
    "session",
    "views",
    "canonical_xlsx",
    "canonical_roundtrip",
    "intake",
    "delimited",
    "extraction",
    "source_package",
    "source_process",
    "source_graph",
    "agent",
    "source_cli",
    "cli",
]

# (module, the later module it uses). Each is a real cycle to remove.
PYTHON_UPWARD = {
    # The envelope is assembled from what the importer holds, and the
    # importer hands back an envelope.
    ("envelope", "xlsx"),
}

# Emitted beside generated source and run without the package installed.
PYTHON_STANDALONE_PREFIX = "source_runtime_v"
PYTHON_STANDALONE_IMPORTS = {"__future__", "math", "re", "sys"}

# The evaluator and the calculation session do arithmetic on values they are
# handed. Reading packages, files and processes belongs to the modules above.
PYTHON_EVALUATOR = ["__init__", "expressions", "primitives", "volatile", "python_engine"]
PYTHON_FILE_WORK = {
    "argparse", "pathlib", "shutil", "socket", "subprocess", "tempfile", "urllib", "xml", "zipfile",
}

# ``lib`` is the crate root, which holds the formula evaluator.
RUST_ORDER = [
    "evidence",
    "worksheet_functions",
    "volatile",
    "lib",
    "names",
    "primitives",
    "toolkit",
    "model",
    "xml_patterns",
    "xlsx_features",
    "envelope",
    "xlsx",
    "convert",
    "session",
    "extraction",
    "delimited",
    "agent",
    "event_log",
    "browser_contract",
    "wasm",
]

RUST_UPWARD = {
    # The evaluator resolves a defined name through a scope that parses the
    # name's formula with the evaluator's own parser.
    ("lib", "names"),
    # The evaluator reads the function list of the extraction pattern catalog.
    ("lib", "extraction"),
    # The volatile context takes its clock from the event log.
    ("volatile", "event_log"),
    # The XML reader reports failures as the package reader's error type.
    ("xml_patterns", "xlsx"),
    # As in Python: the envelope is assembled from the importer's state.
    ("envelope", "xlsx"),
    # The event log replays through the command session that writes to it.
    ("event_log", "browser_contract"),
}

RUST_EVALUATOR = ["lib", "worksheet_functions", "names"]
RUST_FILE_WORK = ["std::fs", "std::process", "std::net", "zip::", "quick_xml::"]


def _python_modules() -> dict[str, Path]:
    return {path.stem: path for path in sorted(PYTHON_PACKAGE.glob("*.py"))}


def _python_imports(path: Path, modules: set[str]) -> tuple[set[str], set[str]]:
    """Package modules and top-level standard-library names a file imports."""
    internal: set[str] = set()
    external: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            parts = (node.module or "").split(".")
            if node.level == 1:
                if node.module:
                    internal.add(parts[0])
                else:
                    internal.update(
                        alias.name if alias.name in modules else "__init__"
                        for alias in node.names
                    )
            elif node.level == 0 and parts[0] == "workbook_forge":
                internal.add(parts[1] if len(parts) > 1 else "__init__")
            elif node.level == 0:
                external.add(parts[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root == "workbook_forge":
                    rest = alias.name.split(".")
                    internal.add(rest[1] if len(rest) > 1 else "__init__")
                else:
                    external.add(root)
    # A name imported from the package root that is a module's own name
    # resolves to that module; anything else is the evaluator.
    internal = {name if name in modules else "__init__" for name in internal}
    internal.discard(path.stem)
    return internal, external


def _rust_modules() -> dict[str, Path]:
    return {path.stem: path for path in sorted(RUST_SOURCE.glob("*.rs"))}


def _rust_code(path: Path) -> str:
    """The file without its in-crate tests and without line comments."""
    text = path.read_text(encoding="utf-8")
    cut = text.find("#[cfg(test)]\nmod tests")
    if cut >= 0:
        text = text[:cut]
    return re.sub(r"//[^\n]*", "", text)


def _rust_uses(name: str, code: str, modules: set[str]) -> set[str]:
    used: set[str] = set()
    for match in re.finditer(r"\bcrate::(\{[^}]*\}|\w+)", code):
        token = match.group(1)
        heads = (
            [part.strip().split("::")[0].split(" ")[0] for part in token[1:-1].split(",")]
            if token.startswith("{")
            else [token]
        )
        for head in heads:
            if head:
                # An item named at the crate root lives in lib.rs.
                used.add(head if head in modules else "lib")
    if name == "lib":
        # The root names its own modules without the ``crate::`` prefix. A
        # ``pub use`` line republishes a module's items and uses none of them.
        code = re.sub(r"\bpub use [^;]*;", "", code)
        for module in modules - {"lib"}:
            if re.search(rf"(?<![\w:]){module}::", code):
                used.add(module)
    used.discard(name)
    return used


def _upward(order: list[str], uses: dict[str, set[str]]) -> set[tuple[str, str]]:
    rank = {name: index for index, name in enumerate(order)}
    return {
        (name, dependency)
        for name, dependencies in uses.items()
        for dependency in dependencies
        if name in rank and dependency in rank and rank[dependency] > rank[name]
    }


def _explain(language: str, order_name: str, upward_name: str, unlisted, extra, gone) -> str:
    lines: list[str] = []
    if unlisted:
        lines.append(
            f"{language} modules with no place in {order_name}: {sorted(unlisted)}.\n"
            f"  Add each to {order_name} in python/tests/test_architecture.py, after every "
            "module it uses and before every module that uses it."
        )
    for name, dependency in sorted(extra):
        lines.append(
            f"{language} module {name!r} uses {dependency!r}, which is a later layer.\n"
            f"  Move the shared piece down into {name!r} or a module below both, or pass it "
            f"in as an argument. If {name!r} now belongs above {dependency!r}, move it in "
            f"{order_name}. Add the pair to {upward_name} only as a recorded decision."
        )
    for name, dependency in sorted(gone):
        lines.append(
            f"{language} module {name!r} no longer uses {dependency!r}.\n"
            f"  Remove ({name!r}, {dependency!r}) from {upward_name}."
        )
    return "\n".join(lines)


def test_python_modules_use_only_the_layers_below_them():
    modules = _python_modules()
    layered = {name for name in modules if not name.startswith(PYTHON_STANDALONE_PREFIX)}
    uses = {name: _python_imports(modules[name], set(modules))[0] for name in layered}
    upward = _upward(PYTHON_ORDER, uses)
    message = _explain(
        "Python", "PYTHON_ORDER", "PYTHON_UPWARD",
        layered - set(PYTHON_ORDER), upward - PYTHON_UPWARD, PYTHON_UPWARD - upward,
    )
    assert not message, "\n" + message


def test_rust_modules_use_only_the_layers_below_them():
    modules = _rust_modules()
    uses = {
        name: _rust_uses(name, _rust_code(path), set(modules)) for name, path in modules.items()
    }
    upward = _upward(RUST_ORDER, uses)
    message = _explain(
        "Rust", "RUST_ORDER", "RUST_UPWARD",
        set(modules) - set(RUST_ORDER), upward - RUST_UPWARD, RUST_UPWARD - upward,
    )
    assert not message, "\n" + message


@pytest.mark.parametrize("name", PYTHON_EVALUATOR)
def test_python_evaluator_modules_do_no_file_or_process_work(name):
    _, external = _python_imports(_python_modules()[name], set(_python_modules()))
    found = sorted(external & PYTHON_FILE_WORK)
    assert not found, (
        f"\npython/workbook_forge/{name}.py imports {found}.\n"
        "  The evaluator works on values it is handed. Do the reading or writing in the "
        "caller (workbook.py, xlsx.py, the command line) and pass the result in."
    )


@pytest.mark.parametrize("name", RUST_EVALUATOR)
def test_rust_evaluator_modules_do_no_file_or_process_work(name):
    code = _rust_code(_rust_modules()[name])
    found = [item for item in RUST_FILE_WORK if re.search(rf"(?<![\w:]){re.escape(item)}", code)]
    assert not found, (
        f"\nrust/src/{name}.rs uses {found}.\n"
        "  The evaluator works on values it is handed. Do the reading or writing in "
        "xlsx.rs, agent.rs or an example, and pass the result in."
    )


def test_generated_source_runtimes_stand_alone():
    modules = _python_modules()
    for name, path in modules.items():
        if not name.startswith(PYTHON_STANDALONE_PREFIX):
            continue
        internal, external = _python_imports(path, set(modules))
        assert not internal, (
            f"\npython/workbook_forge/{name}.py imports {sorted(internal)} from the package.\n"
            "  A source runtime is copied beside generated source and runs without "
            "workbook_forge installed. Put what it needs in the runtime file itself."
        )
        extra = sorted(external - PYTHON_STANDALONE_IMPORTS)
        assert not extra, (
            f"\npython/workbook_forge/{name}.py imports {extra}.\n"
            f"  Source runtimes import only {sorted(PYTHON_STANDALONE_IMPORTS)}. A new import "
            "widens what generated source needs at run time; decide that in a new runtime "
            "version and add the name to PYTHON_STANDALONE_IMPORTS."
        )


def test_the_check_notices_an_upward_use():
    order = ["low", "high"]
    assert _upward(order, {"low": {"high"}, "high": {"low"}}) == {("low", "high")}
    assert _rust_uses("names", "use crate::{Expr, toolkit::Session};", {"names", "toolkit", "lib"}) == {
        "lib", "toolkit",
    }
