"""Excel → structured model intake.

Uses the project's bounded OOXML reader (stdlib zip/XML; openpyxl-equivalent
for this SDK) so agents get a typed Workbook without a third-party Excel dep.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from .model import Cell, Formula, Sheet, Workbook
from .workbook import WorkbookError, _cell_position
from .xlsx import import_xlsx

_CELL_REF_RE = re.compile(
    r"(?:(?P<sheet>(?:\'[\w .]+\'|[A-Za-z_][\w.]*))!)?"
    r"\$?(?P<col>[A-Za-z]{1,3})\$?(?P<row>\d{1,7})"
)


def _classify_value(value: Any) -> str:
    if value is None:
        return "blank"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    if isinstance(value, dict) and "error" in value:
        return "error"
    if isinstance(value, str):
        if value in {
            "#DIV/0!", "#N/A", "#NAME?", "#NULL!", "#NUM!", "#REF!", "#VALUE!",
        }:
            return "error"
        return "text"
    return "unknown"


def _normalize_dependency(sheet_name: str, match: re.Match[str]) -> str:
    raw_sheet = match.group("sheet")
    col = match.group("col").upper()
    row = match.group("row")
    address = f"{col}{int(row)}"
    if raw_sheet:
        return f"{raw_sheet.strip(chr(39))}!{address}"
    return f"{sheet_name}!{address}"


def extract_dependencies(expression: str, sheet_name: str) -> tuple[str, ...]:
    text = expression[1:] if expression.startswith("=") else expression
    seen: list[str] = []
    for match in _CELL_REF_RE.finditer(text):
        dep = _normalize_dependency(sheet_name, match)
        if dep not in seen:
            seen.append(dep)
    return tuple(seen)


def _dimensions(cells: dict[str, Cell]) -> tuple[int, int, int, int] | None:
    if not cells:
        return None
    rows: list[int] = []
    cols: list[int] = []
    for address in cells:
        col, row = _cell_position(address)
        cols.append(col)
        rows.append(row)
    return (min(rows), max(rows), min(cols), max(cols))


def intake_workbook(path: str | Path) -> Workbook:
    """Open an .xlsx and return the canonical structured Workbook model."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"workbook not found: {source}")
    if source.suffix.lower() != ".xlsx":
        raise WorkbookError(f"intake expects an .xlsx path, got {source.suffix!r}")

    imported = import_xlsx(source, backend="python")
    snapshot = imported.to_dict()
    sheets: list[Sheet] = []
    for sheet_doc in snapshot.get("sheets", []):
        name = str(sheet_doc.get("name") or sheet_doc.get("id") or "")
        raw_cells = sheet_doc.get("cells") or {}
        cells: dict[str, Cell] = {}
        for address, payload in raw_cells.items():
            if not isinstance(payload, dict):
                continue
            formula_text = payload.get("formula")
            value = payload.get("value")
            if value is None and "cached_value" in payload:
                value = payload.get("cached_value")
            style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
            number_format = style.get("number_format") if style else None
            formula_obj = None
            if isinstance(formula_text, str) and formula_text:
                expression = formula_text if formula_text.startswith("=") else f"={formula_text}"
                formula_obj = Formula(
                    expression=expression,
                    dependencies=extract_dependencies(expression, name),
                    result=value,
                )
            cells[address] = Cell(
                address=address,
                value=value,
                formula=formula_obj,
                data_type=_classify_value(value),  # type: ignore[arg-type]
                number_format=number_format,
            )
        sheets.append(Sheet(name=name, cells=cells, dimensions=_dimensions(cells)))

    inspect_report = imported.inspect()
    metadata = {
        "schema_version": snapshot.get("schema_version"),
        "backend": imported.backend,
        "package": inspect_report.get("xlsx") or {},
    }
    return Workbook(sheets=tuple(sheets), metadata=metadata, source_path=str(source))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print("usage: python -m workbook_forge.intake <file.xlsx>", file=sys.stderr)
        return 2
    workbook = intake_workbook(args[0])
    print(json.dumps(workbook.to_summary_dict(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
