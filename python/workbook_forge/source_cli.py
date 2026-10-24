"""Installed source compiler CLI for numeric-v1 and graph-v2 through graph-v5.

Generated code has no Forge runtime dependency. Source import accepts only the
declared versioned scaffold/dialect, never arbitrary programs. Graph-v2 adds
dependencies, typed Excel errors and SUM/ranges; numeric-v1 stays compatible.
"""
from __future__ import annotations

import argparse
import ast
import copy
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from workbook_forge.source_contract import MAX_ARTIFACT_BYTES, SourceDiagnostic, manifest_bytes
from workbook_forge.source_package import check_fidelity
from workbook_forge.source_process import run_validated_source
from workbook_forge import source_graph
from workbook_forge.toolkit import WorkbookModel
from workbook_forge.expressions import Expression
from workbook_forge.xlsx import export_xlsx, import_xlsx
from workbook_forge.workbook import WorkbookError

VERSION = 1
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
SCAFFOLDS = {
    "python": ("# Workbook Forge numeric source subset v1\nimport sys\ndef calculate(a1):\n    return ",
               '\nif __name__ == "__main__":\n    print(calculate(float(sys.argv[1])))\n'),
    "rust": ("// Workbook Forge numeric source subset v1\npub fn calculate(a1: f64) -> f64 { ",
             ' }\nfn main() {\n    let a1: f64 = std::env::args().nth(1).expect("input").parse().expect("number");\n    println!("{}", calculate(a1));\n}\n'),
}



def numeric_domain(value) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and abs(value) <= 1e15
    except OverflowError:
        return False


def expression(text: str) -> ast.expr:
    if len(text.encode()) > 8192:
        raise SourceDiagnostic("resource_limit", "expression exceeds 8192 bytes")
    if not re.fullmatch(r"[A-Za-z0-9_\s.+*()\-]+", text):
        raise SourceDiagnostic("unsupported_source", "characters outside numeric expression subset")
    try:
        root = ast.parse(text, mode="eval").body
    except (SyntaxError, RecursionError) as error:
        raise SourceDiagnostic("unsupported_source", "not a numeric subset expression") from error
    pending = [(root, 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if depth > 32 or count > 256:
            raise SourceDiagnostic("resource_limit", "expression exceeds 32 levels or 256 nodes")
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
            pending.extend([(node.left, depth + 1), (node.right, depth + 1)])
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            pending.append((node.operand, depth + 1))
        elif isinstance(node, ast.Name) and node.id == "a1":
            pass
        elif isinstance(node, ast.Constant) and type(node.value) in (int, float):
            try:
                valid = math.isfinite(float(node.value)) and abs(float(node.value)) <= 1e15
            except OverflowError:
                valid = False
            if not valid:
                raise SourceDiagnostic("unsupported_numeric_domain", "literal outside finite magnitude profile")
        else:
            raise SourceDiagnostic("unsupported_source", "only a1, finite literals, unary signs and + - * are importable")
    return root


def render(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return "a1"
    if isinstance(node, ast.Constant):
        return repr(float(node.value))
    if isinstance(node, ast.UnaryOp):
        return f"(-{render(node.operand)})" if isinstance(node.op, ast.USub) else render(node.operand)
    operator = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*"}[type(node.op)]
    return f"({render(node.left)} {operator} {render(node.right)})"


def generate(node: ast.expr, language: str) -> str:
    before, after = SCAFFOLDS[language]
    return before + render(node) + after


def import_source(source: str, language: str) -> ast.expr:
    if len(source.encode()) > 16384:
        raise SourceDiagnostic("resource_limit", "source exceeds 16 KiB")
    before, after = SCAFFOLDS[language]
    if not source.startswith(before) or not source.endswith(after):
        raise SourceDiagnostic("unsupported_source", "program is outside the versioned generated scaffold")
    return expression(source[len(before):-len(after)])


def translate(source: str, origin: str, destination: str) -> str:
    if any(source.startswith(source_graph.template(origin,version)) for version in (2,3,4,5)):
        return source_graph.translate(source, origin, destination)
    return generate(import_source(source, origin), destination)



def compile_workbook(source: Path, language: str) -> tuple[str, dict]:
    try:
        payload=source_graph.bounded_package(source)
    except zipfile.BadZipFile as error:
        raise SourceDiagnostic("invalid_package","malformed source package") from error
    with tempfile.TemporaryDirectory(prefix="forge-numeric-input-") as directory:
        pinned=Path(directory)/"input.xlsx"
        pinned.write_bytes(payload)
        return _compile_numeric_package(pinned,language)


def _compile_numeric_package(source: Path, language: str) -> tuple[str, dict]:
    model = import_xlsx(source).to_dict()  # Existing bounded package/XML reader first.
    with zipfile.ZipFile(source) as archive:
        allowed = {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels",
                   "xl/styles.xml", "xl/worksheets/sheet1.xml"}
        if set(archive.namelist()) != allowed:
            raise SourceDiagnostic("unsupported_fidelity", "source subset does not yet carry additional package parts")
        check_fidelity(archive)
        for part, tags in [("xl/workbook.xml", {"workbookPr", "sheets", "calcPr"}),
                           ("xl/worksheets/sheet1.xml", {"dimension", "cols", "sheetData"})]:
            root = ET.fromstring(archive.read(part))
            if any(child.tag.removeprefix(f"{{{MAIN}}}") not in tags for child in root):
                raise SourceDiagnostic("unsupported_fidelity", "unrepresented workbook or worksheet metadata")
    if len(model["sheets"]) != 1 or set(model["sheets"][0]["cells"]) != {"A1", "B1"}:
        raise SourceDiagnostic("unsupported_workbook_shape", "source subset v1 requires A1 input and B1 formula on one sheet")
    sheet = model["sheets"][0]
    a1, b1 = sheet["cells"]["A1"], sheet["cells"]["B1"]
    if any(cell.get("blocked_reason") for cell in (a1, b1)):
        raise SourceDiagnostic("unsupported_fidelity", "preserved-only cells need a richer source bundle")
    if type(a1.get("value")) not in (int, float) or a1.get("formula") or not b1.get("formula"):
        raise SourceDiagnostic("unsupported_type", "A1 must be numeric and B1 must be a formula")
    if not numeric_domain(a1["value"]):
        raise SourceDiagnostic("unsupported_numeric_domain", "input outside finite magnitude profile")
    formula = b1["formula"].removeprefix("=")
    Expression.parse(formula)  # Establish Excel syntax before importing the numeric subset.
    # No strings, names, calls, cross-sheet or other references are accepted.
    lowered = re.sub(r"(?<![A-Za-z0-9_])\$?A\$?1(?![A-Za-z0-9_])", "a1", formula, flags=re.I)
    root = expression(lowered)
    manifest = {"source_subset_version": VERSION, "model": copy.deepcopy(model),
                "input": {"sheet": sheet["name"], "address": "A1"},
                "output": {"sheet": sheet["name"], "address": "B1"},
                "numeric_profile": "finite binary64 scalar arithmetic; magnitudes <= 1e15; Excel observation unverified"}
    for cell in manifest["model"]["sheets"][0]["cells"].values():
        cell.pop("formula", None)
        cell.pop("cached_value", None)
    manifest_bytes(manifest)
    return generate(root, language), manifest


def execute(program: Path, language: str, value: float, output: Path) -> float:
    source=read_source(program)
    import_source(source, language)  # Reject arbitrary source before executing.
    if not numeric_domain(value):
        raise SourceDiagnostic("unsupported_numeric_domain", "input outside finite magnitude profile")
    result = run_validated_source(source,language,[str(value)],output)
    number = float(result.stdout)
    if not math.isfinite(number) or abs(number) > 1e15:
        raise SourceDiagnostic("unsupported_numeric_domain", "result outside finite magnitude profile")
    return number


def lower(source: str, language: str, manifest: dict, value: float) -> dict:
    if not isinstance(manifest, dict) or type(manifest.get("source_subset_version")) is not int or manifest["source_subset_version"] != VERSION:
        raise SourceDiagnostic("unsupported_version", "unknown source subset version")
    node = import_source(source, language)
    if not numeric_domain(value):
        raise SourceDiagnostic("unsupported_numeric_domain", "input outside finite magnitude profile")
    if not isinstance(manifest.get("model"), dict):
        raise SourceDiagnostic("unsupported_manifest", "manifest needs a typed workbook model")
    document = WorkbookModel(document=manifest["model"]).to_dict()
    if len(document["sheets"]) != 1 or set(document["sheets"][0]["cells"]) != {"A1", "B1"}:
        raise SourceDiagnostic("unsupported_manifest", "manifest is outside numeric source subset")
    if any(cell.get("formula") or cell.get("cached_value") is not None for cell in document["sheets"][0]["cells"].values()):
        raise SourceDiagnostic("unsupported_manifest", "source bundle cannot restore formulas or caches from its manifest")
    sheet = document["sheets"][0]
    if manifest.get("input") != {"sheet": sheet["name"], "address": "A1"} or manifest.get("output") != {"sheet": sheet["name"], "address": "B1"}:
        raise SourceDiagnostic("unsupported_manifest", "manifest bindings differ from numeric source subset")
    sheet["cells"]["A1"]["value"] = value
    sheet["cells"]["B1"]["formula"] = "=" + render(node).replace("a1", "A1")
    return document


def read_source(path: Path) -> str:
    with path.open("rb") as stream:
        content = stream.read(16385)
    if len(content) > 16384:
        raise SourceDiagnostic("resource_limit", "source exceeds 16 KiB")
    return content.decode("utf-8")


def main(argv=None, *, default_profile="graph-v5"):
    parser = argparse.ArgumentParser(description=__doc__,epilog="Graph-v5 AVERAGE direct coercion is provisional: Microsoft sources conflict. Excel observation is unavailable. Rust emission needs an explicit writer adapter.")
    commands = parser.add_subparsers(dest="command", required=True)
    compile_ = commands.add_parser("compile")
    compile_.add_argument("input", type=Path)
    compile_.add_argument("directory", type=Path)
    compile_.add_argument("--language", choices=SCAFFOLDS, required=True)
    compile_.add_argument("--profile", choices=["numeric-v1", "graph-v2", "graph-v3", "graph-v4", "graph-v5"], default=default_profile, help="versioned source dialect (default: %(default)s)")
    transfer = commands.add_parser("translate")
    transfer.add_argument("input", type=Path)
    transfer.add_argument("output", type=Path)
    transfer.add_argument("--origin", choices=SCAFFOLDS, required=True)
    transfer.add_argument("--destination", choices=SCAFFOLDS, required=True)
    emit = commands.add_parser("emit")
    emit.add_argument("source", type=Path)
    emit.add_argument("manifest", type=Path)
    emit.add_argument("output", type=Path)
    emit.add_argument("--language", choices=SCAFFOLDS, required=True)
    emit.add_argument("--input", type=float)
    emit.add_argument("--inputs", help="JSON object of typed cell overrides for graph-v2")
    emit.add_argument("--rust-binary", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "compile":
            if args.profile.startswith("graph-"):
                code, manifest = source_graph.compile_workbook(args.input, args.language,version=int(args.profile[-1]))
            else:
                code, manifest = compile_workbook(args.input,args.language)
            encoded_manifest = manifest_bytes(manifest)
            args.directory.mkdir(parents=True, exist_ok=False)
            (args.directory / ("program.rs" if args.language == "rust" else "program.py")).write_text(code)
            (args.directory / "manifest.json").write_bytes(encoded_manifest)
        elif args.command == "translate":
            translated = translate(source_graph.read_source(args.input), args.origin, args.destination)
            with args.output.open("x") as target:
                target.write(translated)
        else:
            with args.manifest.open("rb") as stream:
                manifest_payload = stream.read(MAX_ARTIFACT_BYTES + 1)
            if len(manifest_payload) > MAX_ARTIFACT_BYTES:
                raise SourceDiagnostic("resource_limit", "manifest exceeds 1 MiB")
            manifest = json.loads(manifest_payload)
            if isinstance(manifest, dict) and manifest.get("source_subset_version") in {2,3,4,5}:
                if args.input is not None:
                    raise SourceDiagnostic("invalid_input", "graph profiles require --inputs; --input is numeric-v1 only")
                source_graph.emit(args.source, args.language, manifest, json.loads(args.inputs or "{}"), args.output, args.rust_binary)
                return 0
            if args.inputs is not None:
                raise SourceDiagnostic("invalid_input", "numeric-v1 requires --input; --inputs is for graph profiles")
            if args.input is None:
                raise SourceDiagnostic("invalid_input", "numeric-v1 emit requires --input")
            source=read_source(args.source)
            document = lower(source, args.language, manifest, args.input)
            with tempfile.TemporaryDirectory(prefix="forge-source-execute-") as directory:
                pinned=Path(directory)/("snapshot.rs" if args.language=="rust" else "snapshot.py")
                pinned.write_text(source,encoding="utf-8")
                actual = execute(pinned, args.language, args.input, Path(directory) / "program")
            report = WorkbookModel(document=document).calculate()
            expected = report["values"].get(document["sheets"][0]["name"] + "!B1")
            if report["diagnostics"] or actual != expected:
                raise SourceDiagnostic("execution_mismatch", "generated program and lowered workbook disagree")
            with tempfile.TemporaryDirectory(prefix="forge-v1-emit-") as directory:
                candidate=Path(directory)/"candidate.xlsx"
                if args.language == "rust":
                    if args.rust_binary is None:
                        raise SourceDiagnostic("missing_backend", "Rust export requires compiler_roundtrip executable")
                    ir = Path(directory)/"model.json"
                    ir.write_text(json.dumps(document))
                    subprocess.run([str(args.rust_binary), "emit-fresh", str(ir), str(candidate)], check=True, timeout=60)
                else:
                    export_xlsx(WorkbookModel(document=document), candidate)
                sheet=document["sheets"][0]["name"]
                program=source_graph.Program(sheet,{"A1":source_graph.scalar(args.input)},
                    {"B1":source_graph.from_excel(source_graph.parse_expression(document["sheets"][0]["cells"]["B1"]["formula"]),sheet)})
                payload=source_graph.verified_export(candidate,document,program,{"A1":args.input,"B1":actual},directory)
                with args.output.open("xb") as target:
                    target.write(payload)
    except (ValueError, WorkbookError, OSError, subprocess.SubprocessError, RecursionError) as error:
        print(json.dumps({"ok": False, "code": getattr(error, "code", "invalid_input"), "message": str(error)}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
