"""Command-line access to independent Python and optional Rust engines."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .catalog import workbook_capabilities
from .workbook import WorkbookError


def _object(source: str) -> dict:
    value = json.loads(source)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def _read(path: Path) -> dict:
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("model document exceeds 128 MiB")
    return _object(path.read_text(encoding="utf-8"))


def _model(path: Path, bindings: Path | None, backend: str):
    from .toolkit import WorkbookModel
    from .xlsx import import_xlsx

    selected = _read(bindings) if bindings else {}
    if set(selected) - {"inputs", "outputs"}:
        raise ValueError("bindings accept only inputs and outputs")
    if path.suffix.lower() == ".xlsx":
        return import_xlsx(path, backend=backend, **selected)
    document = _read(path)
    document.update(selected)
    return WorkbookModel(document=document, backend=backend)


def _agent_loop(adapter, source, destination) -> int:
    """Serve bounded JSON lines; a bad request never becomes multiple requests."""
    maximum = 1024 * 1024

    def reject(code: str, message: str) -> dict:
        return {
            "schema_version": 1, "id": None, "operation": None, "ok": False,
            "revision": adapter.revision, "error": {"code": code, "message": message},
        }

    def reject_constant(value: str):
        raise ValueError(f"nonfinite JSON constant: {value}")

    while raw := source.readline(maximum + 2):
        payload = raw.removesuffix(b"\n")
        if len(payload) > maximum:
            # Discard only the rest of this oversized line, using bounded reads.
            while not raw.endswith(b"\n"):
                raw = source.readline(maximum + 2)
                if not raw:
                    break
            response = reject("resource_limit", "request exceeds 1 MiB UTF-8")
        else:
            try:
                request = json.loads(payload.decode("utf-8"), parse_constant=reject_constant)
            except (ValueError, UnicodeError, RecursionError):
                response = reject("invalid_request", "request must be one valid UTF-8 JSON object")
            else:
                response = adapter.handle(request)
        destination.write(json.dumps(response, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
        destination.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect, execute, and export typed workbook models")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities", help="show primitive and backend support")
    inspect = commands.add_parser("inspect", help="inspect an XLSX or a model interchange document")
    inspect.add_argument("source", type=Path)
    inspect.add_argument("--bindings", type=Path)
    intake = commands.add_parser("intake", help="read an XLSX into the canonical workbook model")
    intake.add_argument("source", type=Path)
    intake.add_argument("--summary", action="store_true", help="print versions and counts instead of the model")
    extract = commands.add_parser("extract", help="extract namespace-aware XLSX patterns and formula mappings")
    extract.add_argument("source", type=Path)
    extract.add_argument("--pattern", dest="patterns", action="append", help="pattern ID; repeat to select multiple patterns")
    extract.add_argument("--sheet", help="limit extraction to one worksheet")
    extract.add_argument("--offset", type=int, default=0)
    extract.add_argument("--limit", type=int, default=100)
    scenario = commands.add_parser("scenario", help="run the operating-scenario example")
    scenario.add_argument("--model", type=Path, help="write a new model interchange document")
    run = commands.add_parser("run", help="calculate a model or supported XLSX output closure")
    run.add_argument("source", type=Path)
    run.add_argument("--bindings", type=Path, help="JSON containing explicit input/output bindings")
    agent = commands.add_parser("agent", help="serve bounded SDK operations over JSON lines")
    agent.add_argument("source", type=Path, nargs="?")
    agent.add_argument("--scenario", action="store_true", help="start with the synthetic operating planner")
    agent.add_argument("--bindings", type=Path, help="explicit input/output bindings for a source")
    agent.add_argument("--output-dir", type=Path, required=True, help="host-selected directory for new XLSX exports")
    for command in (inspect, scenario, run, agent):
        command.add_argument("--backend", choices=("python", "rust"), default="python")
    for command in (scenario, run):
        command.add_argument("--inputs", default="{}", help="JSON object of named scenario inputs")
        command.add_argument("--workers", type=int, default=1)
        command.add_argument("--xlsx", type=Path, help="write a new Excel workbook")
    args = parser.parse_args(argv)
    try:
        if args.command == "intake":
            from .canonical_xlsx import CanonicalPackageError
            from .intake import intake_workbook, intake_workbook_model, summarize

            try:
                if args.summary:
                    print(json.dumps(summarize(intake_workbook_model(args.source)), ensure_ascii=False, allow_nan=False))
                else:
                    sys.stdout.write(intake_workbook(args.source).decode("utf-8") + "\n")
            except CanonicalPackageError as error:
                raise ValueError(str(error)) from error
            return 0
        if args.command == "extract":
            from .extraction import extract_xlsx

            result = extract_xlsx(args.source, patterns=args.patterns, sheet=args.sheet,
                                  offset=args.offset, limit=args.limit)
            # Keep the wire encoding within the extractor's checked byte budget.
            print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
            return 0
        if args.command == "agent":
            from .agent import AgentWorkbook
            from .toolkit import operating_scenario

            if bool(args.source) == args.scenario:
                raise ValueError("agent requires exactly one source or --scenario")
            if args.scenario and args.bindings:
                raise ValueError("--bindings requires a source workbook")
            model = operating_scenario(backend=args.backend) if args.scenario else _model(args.source, args.bindings, args.backend)
            adapter = AgentWorkbook(model, output_dir=args.output_dir)
            return _agent_loop(adapter, sys.stdin.buffer, sys.stdout)
        if args.command == "capabilities":
            result = workbook_capabilities()
        elif args.command == "inspect":
            result = _model(args.source, args.bindings, args.backend).inspect()
        else:
            from .toolkit import operating_scenario
            from .xlsx import export_xlsx

            model = operating_scenario(backend=args.backend) if args.command == "scenario" else _model(args.source, args.bindings, args.backend)
            inputs = _object(args.inputs)
            if inputs:
                model.set_inputs(inputs)
            result = model.calculate(workers=args.workers)
            if result["diagnostics"] or result["stale"]:
                print(json.dumps(result, ensure_ascii=False, allow_nan=False))
                return 1
            if args.xlsx:
                export_xlsx(model, args.xlsx, report=result)
            if args.command == "scenario" and args.model:
                # This is a portable interchange artifact, not a live state store.
                with args.model.open("x", encoding="utf-8") as stream:
                    json.dump(model.to_dict(), stream, indent=2, ensure_ascii=False, allow_nan=False)
                    stream.write("\n")
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, ImportError, WorkbookError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
