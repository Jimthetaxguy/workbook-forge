"""Read an Excel workbook into the canonical workbook model.

This is the entry point for turning an `.xlsx` file into the versioned JSON
document that both engines read. It adds no reader of its own: the package is
read by `canonical_xlsx.import_canonical`.

A value Excel stored for a formula cell is kept as `Cell.value` with
provenance `imported`. `Formula.result` and `Formula.dependencies` stay empty
until `model.calculate` fills them, so an imported cache is never mistaken
for a value Workbook Forge calculated.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .canonical_xlsx import import_canonical
from .model import Workbook
from .workbook import WorkbookError


def intake_workbook_model(path: str | Path) -> Workbook:
    """Read an `.xlsx` file and return the canonical workbook."""
    source = Path(path)
    if source.suffix.lower() != ".xlsx":
        raise WorkbookError(f"intake reads .xlsx files, not {source.suffix or 'a file with no suffix'}")
    if not source.is_file():
        raise WorkbookError(f"workbook not found: {source}")
    return import_canonical(source)


def intake_workbook(path: str | Path) -> bytes:
    """Read an `.xlsx` file and return the canonical JSON document."""
    return intake_workbook_model(path).to_json().encode("utf-8")


def summarize(workbook: Workbook) -> dict[str, Any]:
    """Counts and versions for a person to read. Not the model itself."""
    sheets = [
        {
            "name": sheet.name,
            "cells": len(sheet.cells),
            "formulas": sum(1 for cell in sheet.cells.values() if cell.formula is not None),
            "dimensions": list(sheet.dimensions) if sheet.dimensions else None,
        }
        for sheet in workbook.sheets
    ]
    return {
        "schema_version": workbook.schema_version,
        "model_version": workbook.model_version,
        "source_path": workbook.source_path,
        "sheets": sheets,
        "cell_count": sum(sheet["cells"] for sheet in sheets),
        "formula_count": sum(sheet["formulas"] for sheet in sheets),
    }
