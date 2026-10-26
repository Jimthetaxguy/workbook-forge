"""Versioned multi-cell source compiler with independent generated runtimes.

Only the bounded constructor dialect is importable. Formula source lives in
executable expressions, never in an opaque workbook/manifest restoration field.
"""
from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
import heapq
import io
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from . import ErrorValue
from .expressions import CellReference, parse_expression
from .source_contract import MAX_ARTIFACT_BYTES, SourceDiagnostic, manifest_bytes
from .source_package import MAIN, check_fidelity
from .source_process import run_validated_source
from .toolkit import WorkbookModel
from .xlsx import export_xlsx, import_xlsx
from .workbook import WorkbookError

FUNCTIONS_BY_VERSION = {
    2: ("SUM",),
    3: ("SUM", "IF", "IFERROR"),
    4: ("SUM", "IF", "IFERROR"),
    5: ("SUM", "IF", "IFERROR", "AVERAGE"),
    6: ("SUM", "IF", "IFERROR", "AVERAGE", "XLOOKUP"),
}
SOURCE_FUNCTIONS = FUNCTIONS_BY_VERSION[6]
MAX_SOURCE_BYTES = MAX_ARTIFACT_BYTES
MAX_EDGES = 100000
ERRORS = {"#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#CALC!", "#NULL!", "#SPILL!"}
LEGACY_ERRORS = ERRORS - {"#NULL!", "#SPILL!"}
COMPARISONS = {"=":"equal", "<>":"not_equal", "<":"less", "<=":"less_equal", ">":"greater", ">=":"greater_equal"}
OPS = {"+": "add", "-": "sub", "*": "mul", "/": "div"}


@dataclass(frozen=True)
class Node:
    kind: str
    value: object = None
    args: tuple[Node, ...] = ()


@dataclass(frozen=True)
class Program:
    sheet: str
    defaults: dict[str, Node]
    formulas: dict[str, Node]
    sheets: tuple[str, ...] = ()
    version: int = 2


def fail(code, message):
    raise SourceDiagnostic(code, message)


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(float(value))
    except OverflowError:
        return False


def scalar(value):
    if value is None:
        return Node("blank")
    if type(value) is bool:
        return Node("boolean", value)
    if finite(value):
        return Node("num", float(value))
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeError as error:
            raise SourceDiagnostic("invalid_input", "text input must be valid UTF-8") from error
        return Node("text_hex", value)
    if isinstance(value, dict) and isinstance(value.get("error"), str) and value["error"] in ERRORS and set(value) <= {"error", "message"}:
        return Node("error", value["error"])
    fail("invalid_input", "expected a finite scalar number, text, Boolean, blank or typed Excel error")


def scalar_value(node):
    if node.kind == "blank":
        return None
    if node.kind == "error":
        return {"error": node.value}
    return node.value


def sheet_prefix(name):
    return "'" + name.replace("'", "''") + "'!"


def split_reference(value, sheet, sheets=()):
    name, address = sheet, value
    if "!" in value:
        prefix, address = value.rsplit("!", 1)
        name = prefix[1:-1].replace("''", "'") if prefix.startswith("'") and prefix.endswith("'") else prefix
    if sheets:
        names = {item.lower():item for item in sheets}
        if name.lower() not in names:
            fail("unsupported_reference", "reference sheet is absent; external/3D bindings are not enabled")
        name = names[name.lower()]
    elif name != sheet:
        fail("unsupported_reference", "cross-sheet references require source profile v3")
    try:
        return name, CellReference.parse(address)
    except ValueError as error:
        raise SourceDiagnostic("invalid_reference", str(error)) from error


def key(value, sheet, sheets=()):
    name, ref = split_reference(value, sheet, sheets)
    return (sheet_prefix(name) if sheets else "") + CellReference(ref.row, ref.column).render()


def range_keys(value, sheet, sheets=()):
    try:
        first, last = value.split(":")
    except ValueError as error:
        raise SourceDiagnostic("invalid_reference", "range needs exactly two endpoints") from error
    name, a = split_reference(first, sheet, sheets)
    end_name, b = split_reference(last, name, sheets)
    if name != end_name:
        fail("unsupported_reference", "3D range endpoints are not enabled")
    size = (abs(a.row-b.row)+1) * (abs(a.column-b.column)+1)
    if size > MAX_EDGES:
        fail("resource_limit", "range exceeds the existing 100000-cell budget")
    return [(sheet_prefix(name) if sheets else "") + CellReference(row, column).render()
            for row in range(min(a.row, b.row), max(a.row, b.row)+1)
            for column in range(min(a.column, b.column), max(a.column, b.column)+1)]


def cell_sheet(address, program):
    return split_reference(address, program.sheet, program.sheets)[0]


def xlookup_vector_length(node, context, program):
    """Return the size of a scalar cell reference or one-dimensional range."""
    if node.kind == "reference":
        split_reference(node.value, context, program.sheets)
        return 1
    if node.kind != "range_ref":
        fail("unsupported_shape", "XLOOKUP arrays must be direct cell references or ranges")
    try:
        first, last = node.value.split(":")
    except ValueError as error:
        raise SourceDiagnostic("invalid_reference", "XLOOKUP range needs two endpoints") from error
    first_sheet, start = split_reference(first, context, program.sheets)
    last_sheet, end = split_reference(last, first_sheet, program.sheets)
    if first_sheet != last_sheet:
        fail("unsupported_reference", "XLOOKUP arrays cannot span multiple worksheets")
    if start.row != end.row and start.column != end.column:
        fail("unsupported_shape", "XLOOKUP arrays must be one-dimensional; 2D spill is unsupported")
    return abs(start.row - end.row) + abs(start.column - end.column) + 1


def validate(program):
    if type(program.version) is not int or program.version not in {2,3,4,5,6}:
        fail("unsupported_version", "unknown source profile contract")
    identities=list(program.sheets) if isinstance(program.sheets,(list,tuple)) else []
    if any(isinstance(name,str) and any(char in name for char in ("\u0085","\u2028","\u2029")) for name in [program.sheet,*identities]):
        fail("unsupported_reference", "sheet line-boundary characters need a later source dialect")
    if not isinstance(program.sheet, str) or not program.sheet or len(program.sheet.encode("utf-16-le"))//2 > 31:
        fail("invalid_reference", "invalid worksheet name")
    try:
        WorkbookModel(sheets=list(program.sheets) if program.sheets else [program.sheet])
    except ValueError as error:
        raise SourceDiagnostic("invalid_reference", "invalid worksheet name") from error
    if program.version == 2 and program.sheets:
        fail("unsupported_version", "unknown source profile/sheet contract")
    if program.version >= 3 and (not program.sheets or program.sheet != program.sheets[0]):
        fail("invalid_reference", "profile v3 requires ordered worksheet identities")
    if not isinstance(program.sheets,tuple) or any(not isinstance(name,str) for name in program.sheets):
        fail("invalid_source", "ordered worksheet identities must be a tuple of strings")
    if any(any(ord(c) < 32 for c in name) for name in program.sheets):
        fail("unsupported_reference", "control characters in sheet identities are not enabled")
    if any(node.kind not in {"num", "text_hex", "boolean", "blank", "error"} for node in program.defaults.values()):
        fail("invalid_source", "input defaults must be scalar literals")
    for node in program.defaults.values():
        if program.version == 2 and node.kind == "error" and node.value not in LEGACY_ERRORS:
            fail("unsupported_error", "remaining error values require source profile v3")
        if scalar(scalar_value(node)) != node:
            fail("invalid_source", "invalid default scalar literal")
    if not program.formulas or set(program.defaults) & set(program.formulas):
        fail("invalid_source", "need distinct input and formula cells")
    if len(program.defaults) + len(program.formulas) > MAX_EDGES:
        fail("resource_limit", "workbook exceeds the existing 100000-cell budget")
    for address in program.defaults.keys() | program.formulas.keys():
        if key(address, program.sheet, program.sheets) != address:
            fail("invalid_reference", "cell declarations require canonical A1 addresses")
    dependencies, all_dependencies = {}, {}
    edges = 0
    expanded = 0
    # Addresses only: one validation's sheet identities/reference text are fixed.
    # Never retain values or this cache across calls. Every occurrence below
    # still counts toward expansion limits, including cached occurrences.
    range_expansions = {}
    for address, root in program.formulas.items():
        context = cell_sheet(address, program)
        pending, deps, nodes = [(root, 1, None, None)], set(), 0
        while pending:
            node, depth, parent, parent_index = pending.pop()
            nodes += 1
            if nodes > 256 or depth > 32:
                fail("resource_limit", "source expression exceeds 256 nodes or 32 levels")
            arity = {"num": 0, "text_hex": 0, "boolean": 0, "blank": 0, "error": 0,
                     "reference": 0, "range_ref": 0, "add": 2, "sub": 2, "mul": 2, "div": 2,
                     "neg": 1, "positive": 1, "if_value": 3, "if_error": 2, "xlookup": 6,
                     "missing": 0, **{name:2 for name in COMPARISONS.values()}}
            if node.kind not in {"sum_values","average_values"} and (node.kind not in arity or len(node.args) != arity[node.kind]):
                fail("invalid_source", "invalid expression constructor/arity")
            if program.version < 5 and node.kind == "average_values":
                fail("unsupported_formula", "AVERAGE requires source profile v5")
            if program.version < 6 and node.kind == "xlookup":
                fail("unsupported_formula", "XLOOKUP requires source profile v6")
            if node.kind == "missing" and (parent != "xlookup" or parent_index not in {0,3,4,5}):
                fail("unsupported_formula", "omitted arguments are supported only in XLOOKUP optional positions")
            if node.kind == "xlookup":
                args = node.args
                if args[1].kind not in {"reference", "range_ref"} or args[2].kind not in {"reference", "range_ref"}:
                    fail("unsupported_shape", "XLOOKUP lookup_array and return_array must be direct cell references or ranges")
                if any(args[index].kind == "range_ref" for index in (0, 3, 4, 5)):
                    fail("unsupported_shape", "XLOOKUP lookup_value, fallback and modes must be scalar")
                lookup_size = xlookup_vector_length(args[1], context, program)
                return_size = xlookup_vector_length(args[2], context, program)
                if lookup_size != return_size:
                    fail("unsupported_shape", "XLOOKUP lookup_array and return_array must have equal size")
            if node.kind == "average_values" and not node.args:
                fail("unsupported_formula", "AVERAGE requires at least one argument")
            if program.version < 4 and node.kind in COMPARISONS.values():
                fail("unsupported_formula", "comparison constructors require source profile v4")
            if program.version == 2 and node.kind in {"if_value", "if_error"}:
                fail("unsupported_formula", "lazy selections require source profile v3")
            if program.version == 2 and node.kind == "error" and node.value in {"#NULL!", "#SPILL!"}:
                fail("unsupported_error", "remaining error literals require source profile v3")
            if node.kind == "num" and not finite(node.value) or node.kind == "boolean" and type(node.value) is not bool:
                fail("invalid_source", "invalid numeric/Boolean literal")
            if node.kind == "error" and (not isinstance(node.value, str) or node.value not in ERRORS):
                fail("invalid_source", "unknown Excel error code")
            if node.kind == "blank":
                fail("unsupported_formula", "blank defaults/references are supported; an executable blank literal has no reversible formula syntax")
            if node.kind == "text_hex":
                if not isinstance(node.value, str):
                    fail("invalid_source", "text literal required")
                try:
                    node.value.encode("utf-8")
                except UnicodeError as error:
                    raise SourceDiagnostic("invalid_source", "invalid UTF-8 text") from error
            if node.kind == "reference":
                deps.add(key(node.value, context, program.sheets))
            if node.kind == "range_ref":
                if parent not in {"sum_values", "average_values"} and not (parent == "xlookup" and parent_index in {1,2}):
                    fail("unsupported_shape", "range expressions are supported as SUM/AVERAGE or XLOOKUP array arguments")
                cache_key = (context, node.value) if isinstance(node.value, str) else None
                if cache_key is not None and cache_key in range_expansions:
                    members = range_expansions[cache_key]
                else:
                    members = tuple(range_keys(node.value, context, program.sheets))
                    if cache_key is not None:
                        range_expansions[cache_key] = members
                expanded += len(members)
                if expanded > MAX_EDGES:
                    fail("resource_limit", "repeated range expansion exceeds the existing 100000-cell budget")
                deps.update(members)
            if node.kind in {"sum_values","average_values"} and len(node.args) > 255:
                fail("resource_limit", "aggregate exceeds 255 arguments")
            pending.extend((child, depth+1, node.kind, index) for index, child in enumerate(node.args))
        edges += len(deps)
        if edges > MAX_EDGES:
            fail("resource_limit", "source graph exceeds the existing 100000-edge budget")
        all_dependencies[address] = deps
        dependencies[address] = deps & program.formulas.keys()
    remaining = {cell: len(deps) for cell, deps in dependencies.items()}
    reverse = {cell: [] for cell in dependencies}
    for cell, deps in dependencies.items():
        for dep in deps:
            reverse[dep].append(cell)
    ready = [cell for cell, count in remaining.items() if not count]
    heapq.heapify(ready)
    order, depths = [], {}
    while ready:
        cell = heapq.heappop(ready)
        order.append(cell)
        depths[cell] = 1 + max((depths[dep] for dep in dependencies[cell]), default=0)
        if depths[cell] > 96:
            fail("resource_limit", "source dependency depth exceeds 96")
        for dependent in reverse[cell]:
            remaining[dependent] -= 1
            if not remaining[dependent]:
                heapq.heappush(ready, dependent)
    if len(order) != len(dependencies):
        fail("cycle", "circular references require an explicit iterative-calculation contract")
    return {cell: sorted(deps) for cell, deps in all_dependencies.items()}


def from_excel(node, sheet, depth=1, version=2):
    if depth > 32:
        fail("resource_limit", "source expression exceeds 32 levels")
    def children():
        return tuple(from_excel(child, sheet, depth+1, version) for child in node.children)
    if node.kind == "missing":
        return Node("missing")
    if node.kind == "number":
        value = float(node.value)
        if not math.isfinite(value):
            fail("unsupported_numeric_domain", "nonfinite literal")
        return Node("num", value)
    if node.kind == "literal":
        return scalar({"error": node.value.code} if isinstance(node.value, ErrorValue) else node.value)
    if node.kind in {"cell", "range"}:
        *addresses, named_sheet = node.value
        if version == 2 and named_sheet is not None and named_sheet != sheet:
            fail("unsupported_reference", "cross-sheet references need a later source profile")
        prefix = sheet_prefix(named_sheet) if named_sheet is not None else ""
        return Node("reference" if node.kind == "cell" else "range_ref", prefix + ":".join(addresses))
    if node.kind == "binary" and node.value in OPS:
        return Node(OPS[node.value], args=children())
    if version >= 4 and node.kind == "binary" and node.value in COMPARISONS:
        return Node(COMPARISONS[node.value],args=children())
    if node.kind == "unary" and node.value in {"+", "-"}:
        return Node("positive" if node.value == "+" else "neg", args=children())
    if node.kind == "call" and node.value.upper() == "AVERAGE" and version < 5:
        fail("unsupported_formula", "AVERAGE requires source profile v5")
    if node.kind == "call" and node.value.upper() == "XLOOKUP" and version < 6:
        fail("unsupported_formula", "XLOOKUP requires source profile v6")
    if node.kind == "call" and node.value.upper() in FUNCTIONS_BY_VERSION.get(version, ()):
        name = node.value.upper()
        if name == "SUM":
            return Node("sum_values", args=children())
        if name == "AVERAGE":
            return Node("average_values", args=children())
        if name == "XLOOKUP":
            args = children()
            if not 3 <= len(args) <= 6:
                fail("unsupported_formula", "XLOOKUP requires three through six positional arguments")
            return Node("xlookup", args=(*args, *(Node("missing") for _ in range(6-len(args)))))
        if version >= 3:
            args = children()
            if name == "IF" and len(args) in {2,3}:
                return Node("if_value", args=args if len(args)==3 else (*args, Node("boolean",False)))
            if name == "IFERROR" and len(args)==2:
                return Node("if_error", args=args)
            fail("unsupported_formula", "invalid lazy selection arity")
    fail("unsupported_formula", f"source profile v{version} cannot lower {node.kind}:{node.value}")


def quote(value):
    return json.dumps(value, ensure_ascii=False)


def render(node, language):
    if node.kind == "num":
        return f"num({repr(float(node.value))})"
    if node.kind == "text_hex":
        return f'text_hex("{node.value.encode("utf-8").hex()}")'
    if node.kind == "boolean":
        value = str(node.value) if language == "python" else str(node.value).lower()
        return f"boolean({value})"
    if node.kind == "blank":
        return "blank()"
    if node.kind == "error":
        return f"error({quote(node.value)})"
    if node.kind in {"reference", "range_ref"}:
        return f"{node.kind}(e, {quote(node.value)})"
    if node.kind in {"if_value", "if_error"}:
        head = render(node.args[0], language)
        branches = [render(child, language) for child in node.args[1:]]
        closures = [f"lambda e: {branch}" if language == "python" else f"|e| {branch}" for branch in branches]
        return f"{node.kind}({head}, e, " + ", ".join(closures) + ")"
    if node.kind == "xlookup":
        lookup, lookup_array, return_array, fallback, match_mode, search_mode = node.args
        fallback_body = render(fallback, language)
        fallback_closure = (
            f"lambda e: {fallback_body}" if language == "python" else f"|e| {fallback_body}"
        )
        arguments = [lookup, lookup_array, return_array, match_mode, search_mode]
        rendered = [render(argument, language) for argument in arguments]
        return (
            f"xlookup({rendered[0]}, {rendered[1]}, {rendered[2]}, e, "
            f"{fallback_closure}, {rendered[3]}, {rendered[4]})"
        )
    arguments = ", ".join(render(child, language) for child in node.args)
    if node.kind in {"sum_values","average_values"}:
        return f"{node.kind}({'vec!' if language == 'rust' else ''}[{arguments}])"
    return f"{node.kind}({arguments})"


def template(language, version=2):
    if language not in {"python", "rust"}:
        fail("unsupported_language", "source profile requires Rust or Python")
    if type(version) is not int or version not in {2,3,4,5,6}:
        fail("unsupported_version", "unknown source runtime profile")
    return Path(__file__).with_name(f"source_runtime_v{version}.py" if language == "python" else f"source_runtime_v{version}.rs").read_text()


def generate(program, language):
    validate(program)
    marker = "#" if language == "python" else "//"
    version = program.version
    identity = program.sheet if version == 2 else json.dumps(program.sheets,ensure_ascii=False)
    header = template(language,version) + f"\n{marker} WFG{version} SHEET {identity.encode().hex()}\n{marker} WFG{version} DEFAULTS\n"
    if language == "python":
        defaults = "DEFAULTS = {\n" + "".join(f"    {quote(k)}: {render(v, language)},\n" for k,v in sorted(program.defaults.items())) + "}\n"
        formulas = "FORMULAS = {\n" + "".join(f"    {quote(k)}: lambda e: {render(v, language)},\n" for k,v in sorted(program.formulas.items())) + "}\n"
        tail = 'if __name__ == "__main__":\n    run(DEFAULTS, FORMULAS)\n'
    else:
        defaults = "fn default_values() -> BTreeMap<String,V> {\n    BTreeMap::from([\n" + "".join(f"        ({quote(k)}.to_string(), {render(v, language)}),\n" for k,v in sorted(program.defaults.items())) + "    ])\n}\n"
        formulas = "fn formula(key: &str, e: &mut Engine) -> V {\n    match key {\n" + "".join(f"        {quote(k)} => {render(v, language)},\n" for k,v in sorted(program.formulas.items())) + '        _ => panic!("unknown formula"),\n    }\n}\n'
        formulas += "fn formula_keys() -> &'static [&'static str] { &[" + ", ".join(quote(k) for k in sorted(program.formulas)) + "] }\nfn is_formula(key: &str) -> bool { formula_keys().contains(&key) }\n"
        tail = "fn main() { run(default_values()); }\n"
    if version >= 3:
        tail = 'if __name__ == "__main__":\n    run(DEFAULTS, FORMULAS, ' + json.dumps(program.sheets,ensure_ascii=False) + ')\n' if language == "python" else "fn main() { run(default_values(), &[" + ", ".join(quote(name) for name in program.sheets) + "]); }\n"
    source = header + defaults + f"{marker} WFG{version} FORMULAS\n" + formulas + f"{marker} WFG{version} MAIN\n" + tail
    if len(source.encode()) > MAX_SOURCE_BYTES:
        fail("resource_limit", "generated source exceeds 1 MiB")
    return source


def source_node(tree, depth=1):
    if depth > 32 or not isinstance(tree, ast.Call) or not isinstance(tree.func, ast.Name) or tree.keywords:
        fail("unsupported_source", "source must use the bounded constructor dialect")
    name, args = tree.func.id, tree.args
    def literal(index):
        try:
            return ast.literal_eval(args[index])
        except (ValueError, TypeError, IndexError, SyntaxError) as error:
            raise SourceDiagnostic("unsupported_source", "constructor literal required") from error
    if name == "num" and len(args) == 1 and finite(literal(0)):
        return Node(name, float(literal(0)))
    if name == "boolean" and len(args) == 1 and type(literal(0)) is bool:
        return Node(name, literal(0))
    if name == "blank" and not args:
        return Node(name)
    if name == "error" and len(args) == 1 and isinstance(literal(0), str) and literal(0) in ERRORS:
        return Node(name, literal(0))
    if name == "text_hex" and len(args) == 1 and isinstance(literal(0), str):
        try:
            return Node(name, bytes.fromhex(literal(0)).decode("utf-8"))
        except (ValueError, UnicodeError) as error:
            raise SourceDiagnostic("unsupported_source", "valid UTF-8 hex text required") from error
    if name in {"reference", "range_ref"} and len(args) == 2 and isinstance(args[0], ast.Name) and args[0].id == "e" and isinstance(literal(1), str):
        return Node(name, literal(1))
    if name in {"add", "sub", "mul", "div", *COMPARISONS.values()} and len(args) == 2 or name in {"neg", "positive"} and len(args) == 1:
        return Node(name, args=tuple(source_node(child, depth+1) for child in args))
    if name in {"if_value", "if_error"} and len(args) == (4 if name == "if_value" else 3):
        if not isinstance(args[1],ast.Name) or args[1].id != "e":
            fail("unsupported_source", "selection runtime context required")
        branches = []
        for branch in args[2:]:
            if not isinstance(branch,ast.Lambda) or ast.dump(branch.args) != ast.dump(ast.parse("lambda e: 0",mode="eval").body.args):
                fail("unsupported_source", "selection branch requires a single lazy runtime context")
            branches.append(source_node(branch.body,depth+1))
        return Node(name,args=(source_node(args[0],depth+1),*branches))
    if name == "missing" and not args:
        return Node(name)
    if name in {"sum_values","average_values"} and len(args) == 1 and isinstance(args[0], ast.List):
        if len(args[0].elts) > 255:
            fail("resource_limit", "aggregate exceeds 255 arguments")
        return Node(name, args=tuple(source_node(child, depth+1) for child in args[0].elts))
    if name == "xlookup" and len(args) == 7:
        if not isinstance(args[3], ast.Name) or args[3].id != "e":
            fail("unsupported_source", "XLOOKUP runtime context required")
        fallback = args[4]
        if not isinstance(fallback, ast.Lambda) or ast.dump(fallback.args) != ast.dump(ast.parse("lambda e: 0",mode="eval").body.args):
            fail("unsupported_source", "XLOOKUP fallback requires a lazy runtime context")
        return Node("xlookup", args=(
            source_node(args[0],depth+1), source_node(args[1],depth+1),
            source_node(args[2],depth+1), source_node(fallback.body,depth+1),
            source_node(args[5],depth+1), source_node(args[6],depth+1),
        ))
    fail("unsupported_source", f"unsupported source constructor: {name}")


def parse_constructor(text):
    try:
        tokens = r'"(?:[^"\\]|\\.)*"|\btrue\b|\bfalse\b|vec!\[|\|e\| '
        replacements = {"true":"True", "false":"False", "vec![":"[", "|e| ":"lambda e: "}
        text = re.sub(tokens,lambda match:replacements.get(match[0],match[0]),text)
        return source_node(ast.parse(text, mode="eval").body)
    except (ValueError, TypeError, OverflowError, SyntaxError, RecursionError) as error:
        if isinstance(error, SourceDiagnostic):
            raise
        raise SourceDiagnostic("unsupported_source", "invalid source expression") from error


def import_source(source, language):
    if len(source.encode()) > MAX_SOURCE_BYTES:
        fail("resource_limit", "source exceeds 1 MiB")
    version = next((version for version in (6,5,4,3,2) if source.startswith(template(language,version))),2)
    prefix = template(language,version)
    if not source.startswith(prefix):
        fail("unsupported_source", "runtime scaffold was changed or has an unknown version")
    marker = "#" if language == "python" else "//"
    try:
        header, rest = source[len(prefix):].split(f"{marker} WFG{version} DEFAULTS\n", 1)
        sheet = bytes.fromhex(header.strip().removeprefix(f"{marker} WFG{version} SHEET ")).decode()
        identities = None if version == 2 else json.loads(sheet)
        if version >= 3 and not isinstance(identities,list):
            fail("invalid_source", "ordered worksheet identity list required")
        sheets = () if version == 2 else tuple(identities)
        if version >= 3:
            if not sheets or not all(isinstance(name,str) for name in sheets):
                fail("invalid_source", "ordered worksheet identities required")
            sheet = sheets[0]
        defaults_block, rest = rest.split(f"{marker} WFG{version} FORMULAS\n", 1)
        formulas_block, _ = rest.split(f"{marker} WFG{version} MAIN\n", 1)
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error,SourceDiagnostic):
            raise
        raise SourceDiagnostic("unsupported_source", "source sections are incomplete") from error
    defaults, formulas = {}, {}
    for block, target, pattern in [
        (defaults_block, defaults, r'    ("(?:[^"\\]|\\.)*"): (.+),' if language == "python" else r'        \(("(?:[^"\\]|\\.)*")\.to_string\(\), (.+)\),'),
        (formulas_block, formulas, r'    ("(?:[^"\\]|\\.)*"): lambda e: (.+),' if language == "python" else r'        ("(?:[^"\\]|\\.)*") => (.+),'),
    ]:
        for line in block.splitlines():
            match = re.fullmatch(pattern, line)
            if match:
                address = json.loads(match[1])
                if address in target:
                    fail("invalid_source", "duplicate cell declaration")
                target[address] = parse_constructor(match[2])
    if any(node.kind not in {"num", "text_hex", "boolean", "blank", "error"} for node in defaults.values()):
        fail("unsupported_source", "input defaults must be typed scalar literals")
    program = Program(sheet, defaults, formulas, sheets, version)
    if generate(program, language) != source:
        fail("unsupported_source", "noncanonical edits outside source expressions/default values")
    return program


def translate(source, origin, destination):
    return generate(import_source(source, origin), destination)


def bounded_package(path):
    """Read a source-profile package before any decompression/import work."""
    with Path(path).open("rb") as stream:
        packed=stream.read(MAX_SOURCE_BYTES+1)
    if len(packed)>MAX_SOURCE_BYTES:
        fail("resource_limit", "source package exceeds the existing 1 MiB budget")
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        entries=archive.infolist()
        if len({entry.filename for entry in entries})!=len(entries):
            fail("unsupported_fidelity", "duplicate OOXML package parts are ambiguous")
        if sum(entry.file_size for entry in entries)>MAX_SOURCE_BYTES:
            fail("resource_limit", "expanded source package exceeds the existing 1 MiB budget")
    return packed


def compile_workbook(path, language, version=2):
    try:
        packed = bounded_package(path)
        with tempfile.TemporaryDirectory(prefix="forge-package-pinned-") as directory:
            pinned=Path(directory)/"input.xlsx"
            pinned.write_bytes(packed)
            return _compile_package(pinned,language,version)
    except SourceDiagnostic:
        raise
    except (zipfile.BadZipFile, ET.ParseError, UnicodeError, KeyError, ValueError) as error:
        raise SourceDiagnostic("invalid_package", "malformed or unrepresentable source package") from error


def source_model(path, version=2):
    if type(version) is not int or version not in {2,3,4,5,6}:
        fail("unsupported_version", "unknown source profile")
    model = import_xlsx(path).to_dict()
    if version == 2 and len(model["sheets"]) != 1:
        fail("unsupported_workbook_shape", "source profile v2 supports one worksheet")
    names = tuple(sheet["name"] for sheet in model["sheets"])
    with zipfile.ZipFile(path) as archive:
        worksheets = {f"xl/worksheets/sheet{i+1}.xml" for i in range(len(names))}
        if set(archive.namelist()) != {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml"} | worksheets:
            fail("unsupported_fidelity", "package parts need a richer source bundle")
        check_fidelity(archive, allow_strings=True, worksheets=sorted(worksheets))
        for part, tags in [("xl/workbook.xml", {"workbookPr", "sheets", "calcPr"}), *[(part, {"dimension", "cols", "sheetData"}) for part in worksheets]]:
            if any(child.tag.removeprefix(f"{{{MAIN}}}") not in tags for child in ET.fromstring(archive.read(part))):
                fail("unsupported_fidelity", "unrepresented worksheet/workbook metadata")
    return model


def _compile_package(path, language, version=2):
    model=source_model(path,version)
    names=tuple(sheet["name"] for sheet in model["sheets"])
    defaults, formulas = {}, {}
    for sheet in model["sheets"]:
        for address, cell in sheet["cells"].items():
            address = sheet_prefix(sheet["name"]) + address if version >= 3 else address
            if cell.get("blocked_reason"):
                fail("unsupported_fidelity", "blocked cell in source program")
            if cell.get("formula") is not None:
                formulas[address] = from_excel(parse_expression(cell["formula"]), sheet["name"],version=version)
            else:
                defaults[address] = scalar(cell.get("value"))
    program = Program(names[0], defaults, formulas, names if version >= 3 else (),version)
    code = generate(program, language)
    manifest = {"source_subset_version": version, "model": copy.deepcopy(model), "formula_addresses": sorted(formulas),
                "profile": "independent scalar runtime; versioned source dialect; Excel evidence separate"}
    if version >= 5:
        manifest["semantic_profile"]={
            "functions":list(FUNCTIONS_BY_VERSION[version]),
            "average_direct_arguments":"provisional counted booleans/decimal text; conflicting Microsoft worksheet/method documentation",
            "comparison_evidence":"72 operator/arithmetic, 168 MATCH-order inference, 246 local-profile expectations",
            "excel_observed":False,
            "driver":"Python intake/translation/lowering/checks; independently generated Rust/Python evaluators",
            "export":"fresh generation, staged writer-result verification, exclusive publication"}
    for sheet in manifest["model"]["sheets"]:
        for cell in sheet["cells"].values():
            cell.pop("formula", None)
            cell.pop("cached_value", None)
    manifest_bytes(manifest)  # A returned bundle must be consumable under the same budget.
    return code, manifest


def formula_text(node):
    if node.kind == "num":
        return repr(node.value)
    if node.kind == "text_hex":
        return '"' + node.value.replace('"', '""') + '"'
    if node.kind == "boolean":
        return str(node.value).upper()
    if node.kind == "error":
        return node.value
    if node.kind in {"reference", "range_ref"}:
        return node.value
    if node.kind in {"sum_values", "average_values", "if_value", "if_error", "xlookup"}:
        return {"sum_values":"SUM", "average_values":"AVERAGE", "if_value":"IF", "if_error":"IFERROR", "xlookup":"XLOOKUP"}[node.kind] + "(" + ",".join("" if child.kind == "missing" else formula_text(child) for child in node.args) + ")"
    if node.kind in {"positive", "neg"}:
        return "(" + ("+" if node.kind == "positive" else "-") + formula_text(node.args[0]) + ")"
    operator = {v:k for k,v in (OPS | COMPARISONS).items()}[node.kind]
    return "(" + formula_text(node.args[0]) + operator + formula_text(node.args[1]) + ")"


def canonical_sheet_references(node, sheet, sheets):
    if node.kind in {"reference", "range_ref"} and "!" in node.value:
        parts=node.value.split(":")
        name, first=split_reference(parts[0],sheet,sheets)
        value=sheet_prefix(name)+first.render()
        if len(parts)==2:
            _,last=split_reference(parts[1],name,sheets)
            value+=":"+last.render()
        return Node(node.kind,value)
    return Node(node.kind,node.value,tuple(canonical_sheet_references(child,sheet,sheets) for child in node.args))


def lower(source, language, manifest, inputs):
    if not isinstance(manifest, dict) or type(manifest.get("source_subset_version")) is not int or manifest["source_subset_version"] not in {2,3,4,5,6}:
        fail("unsupported_version", "unknown source graph manifest version")
    manifest_bytes(manifest)
    program = import_source(source, language)
    if not isinstance(manifest.get("model"), dict):
        fail("invalid_manifest", "typed model required")
    model = WorkbookModel(document=manifest["model"]).to_dict()
    names = tuple(sheet["name"] for sheet in model["sheets"])
    if names != (program.sheets or (program.sheet,)) or program.version != manifest["source_subset_version"]:
        fail("invalid_manifest", "source sheet identity/version differs from manifest")
    cells = {(sheet_prefix(sheet["name"]) if program.sheets else "")+address:cell
             for sheet in model["sheets"] for address,cell in sheet["cells"].items()}
    if set(cells) != program.defaults.keys() | program.formulas.keys() or manifest.get("formula_addresses") != sorted(program.formulas):
        fail("invalid_manifest", "source cell roles/addresses differ from manifest")
    if any(cell.get("formula") is not None or cell.get("cached_value") is not None for cell in cells.values()):
        fail("invalid_manifest", "original formulas and caches cannot be restored from a manifest")
    if not isinstance(inputs, dict) or inputs.keys() - program.defaults.keys():
        fail("invalid_input", "overrides must address declared nonformula cells")
    for address, node in program.defaults.items():
        cells[address]["value"] = scalar_value(scalar(inputs[address]) if address in inputs else node)
    for address, node in program.formulas.items():
        expression = canonical_sheet_references(node,cell_sheet(address,program),program.sheets) if program.version >= 3 else node
        cells[address]["formula"] = "=" + formula_text(expression)
        cells[address]["value"] = None
    return model


def encode(value):
    node = scalar(value)
    if node.kind == "num":
        return "n:" + repr(node.value)
    if node.kind == "text_hex":
        return "t:" + node.value.encode().hex()
    if node.kind == "boolean":
        return "b:" + str(int(node.value))
    if node.kind == "blank":
        return "z:"
    return "e:" + node.value


def decode(tag, payload):
    if tag == "n":
        value = float(payload)
        if not math.isfinite(value):
            fail("invalid_result", "runtime returned nonfinite scalar")
        return value
    if tag == "t":
        return bytes.fromhex(payload).decode()
    if tag == "b" and payload in {"0", "1"}:
        return payload == "1"
    if tag == "z" and not payload:
        return None
    if tag == "e" and payload in ERRORS:
        return {"error": payload}
    fail("invalid_result", "invalid typed runtime output")


def execute(path, language, inputs, output):
    source = read_source(path)
    program = import_source(source, language)
    if not isinstance(inputs, dict) or inputs.keys() - program.defaults.keys():
        fail("invalid_input", "overrides must address declared nonformula cells")
    if program.version == 2 and any(isinstance(value,dict) and value.get("error") in {"#NULL!","#SPILL!"} for value in inputs.values()):
        fail("unsupported_error", "remaining error values require source profile v3")
    arguments = [f"{address.encode().hex() if program.version >= 3 else address}={encode(value)}" for address,value in sorted(inputs.items())]
    payload = "\n".join(arguments).encode()
    if len(payload) > MAX_SOURCE_BYTES:
        fail("resource_limit", "typed overrides exceed 1 MiB")
    result = run_validated_source(source,language,["--stdin"],output,stdin=payload.decode())
    values = {}
    for line in result.stdout.splitlines():
        address, tag, payload = line.split("\t", 2)
        if program.version >= 3:
            address = bytes.fromhex(address).decode()
        if address in values:
            fail("invalid_result", "duplicate runtime output")
        values[address] = decode(tag, payload)
    if values.keys() != program.defaults.keys() | program.formulas.keys():
        fail("invalid_result", "runtime output addresses differ from program")
    return values


def read_source(path):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_SOURCE_BYTES+1)
    if len(data) > MAX_SOURCE_BYTES:
        fail("resource_limit", "source exceeds 1 MiB")
    return data.decode()


def typed(value):
    return encode(value)


def verified_export(candidate, expected, program, actual, directory):
    """A writer's exit status is not evidence its formulas/caches match."""
    try:
        payload=bounded_package(candidate)
        pinned=Path(directory)/"verified.xlsx"
        pinned.write_bytes(payload)
        observed=source_model(pinned,program.version)
        if [sheet["name"] for sheet in observed["sheets"]]!=[sheet["name"] for sheet in expected["sheets"]]:
            fail("backend_mismatch", "writer changed worksheet identities/order")
        for wanted,got in zip(expected["sheets"],observed["sheets"]):
            if set(wanted["cells"])!=set(got["cells"]):
                fail("backend_mismatch", "writer changed cell declarations")
            for address,wanted_cell in wanted["cells"].items():
                cell=got["cells"][address]
                key=(sheet_prefix(wanted["name"]) if program.sheets else "")+address
                formula=wanted_cell.get("formula")
                if formula is not None:
                    if cell.get("formula") is None or from_excel(parse_expression(cell["formula"]),wanted["name"],version=program.version)!=from_excel(parse_expression(formula),wanted["name"],version=program.version):
                        fail("backend_mismatch", "writer changed a formula expression")
                    value=cell.get("cached_value")
                else:
                    if cell.get("formula") is not None:
                        fail("backend_mismatch", "writer changed a scalar cell into a formula")
                    value=cell.get("value")
                if typed(value)!=typed(actual[key]):
                    fail("backend_mismatch", "writer result differs from generated execution")
        return payload
    except SourceDiagnostic as error:
        if error.code in {"backend_mismatch","resource_limit"}:
            raise
        raise SourceDiagnostic("backend_mismatch", "writer package is outside the source contract") from error
    except (ValueError,KeyError,zipfile.BadZipFile,ET.ParseError,WorkbookError) as error:
        raise SourceDiagnostic("backend_mismatch", "writer package cannot be verified") from error


def emit(path, language, manifest, inputs, output, rust_binary=None):
    source = read_source(path)
    model = lower(source, language, manifest, inputs)
    with tempfile.TemporaryDirectory(prefix="forge-graph-v2-") as directory:
        pinned=Path(directory)/("snapshot.rs" if language=="rust" else "snapshot.py")
        pinned.write_text(source,encoding="utf-8")
        actual = execute(pinned, language, inputs, Path(directory)/"program")
        report = WorkbookModel(document=model).calculate()
        program = import_source(source,language)
        def result_key(address):
            sheet, ref = split_reference(address,program.sheet,program.sheets)
            return sheet+"!"+ref.render()
        if report["diagnostics"] or any(typed(value) != typed(report["values"][result_key(address)]) for address,value in actual.items()):
            fail("execution_mismatch", "generated execution and lowered workbook disagree")
        candidate=Path(directory)/"candidate.xlsx"
        if language == "rust":
            if rust_binary is None:
                fail("missing_backend", "Rust export needs the independent compiler_roundtrip adapter")
            ir = Path(directory)/"model.json"
            ir.write_text(json.dumps(model, allow_nan=False))
            subprocess.run([str(rust_binary), "emit-fresh", str(ir), str(candidate)], check=True, capture_output=True, timeout=60)
        else:
            export_xlsx(WorkbookModel(document=model), candidate)
        payload=verified_export(candidate,model,program,actual,directory)
        with Path(output).open("xb") as destination:
            destination.write(payload)
    return actual
