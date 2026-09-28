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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect, execute, and export typed workbook models")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities", help="show primitive and backend support")
    inspect = commands.add_parser("inspect", help="inspect an XLSX or a model interchange document")
    inspect.add_argument("source", type=Path)
    inspect.add_argument("--bindings", type=Path)
    scenario = commands.add_parser("scenario", help="run the operating-scenario example")
    scenario.add_argument("--model", type=Path, help="write a new model interchange document")
    run = commands.add_parser("run", help="calculate a model or supported XLSX output closure")
    run.add_argument("source", type=Path)
    run.add_argument("--bindings", type=Path, help="JSON containing explicit input/output bindings")
    for command in (inspect, scenario, run):
        command.add_argument("--backend", choices=("python", "rust"), default="python")
    for command in (scenario, run):
        command.add_argument("--inputs", default="{}", help="JSON object of named scenario inputs")
        command.add_argument("--workers", type=int, default=1)
        command.add_argument("--xlsx", type=Path, help="write a new Excel workbook")
    args = parser.parse_args(argv)
    try:
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
