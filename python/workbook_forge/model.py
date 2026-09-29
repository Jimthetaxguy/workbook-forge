"""Canonical structured workbook model for Workbook Forge.

These types are the single source of truth for intake, calc-binding, export,
and agent-headless slices. Later stages import them directly — no adapters.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

DataType = Literal["blank", "number", "boolean", "text", "error", "unknown"]


@dataclass(frozen=True, slots=True)
class Formula:
    """A formula expression with resolved dependencies and cached result."""

    expression: str
    dependencies: tuple[str, ...] = ()
    result: Any = None


@dataclass(frozen=True, slots=True)
class Cell:
    """One cell: address, stored value, optional formula, and data type."""

    address: str
    value: Any = None
    formula: Formula | None = None
    data_type: DataType = "unknown"
    number_format: str | None = None


@dataclass(frozen=True, slots=True)
class Sheet:
    """One worksheet: name, cells keyed by A1 address, and used dimensions."""

    name: str
    cells: dict[str, Cell] = field(default_factory=dict)
    dimensions: tuple[int, int, int, int] | None = None  # min_row, max_row, min_col, max_col


@dataclass(frozen=True, slots=True)
class Workbook:
    """A workbook: sheets, source path, and light metadata."""

    sheets: tuple[Sheet, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    source_path: str | None = None

    def sheet(self, name: str) -> Sheet | None:
        for item in self.sheets:
            if item.name == name:
                return item
        return None

    def to_summary_dict(self) -> dict[str, Any]:
        """JSON-friendly intake summary: counts and dependency graph size."""
        dep_edges: set[tuple[str, str]] = set()
        formula_count = 0
        cell_count = 0
        sheet_summaries = []
        for sheet in self.sheets:
            formulas = 0
            for cell in sheet.cells.values():
                cell_count += 1
                if cell.formula is not None:
                    formulas += 1
                    formula_count += 1
                    src = f"{sheet.name}!{cell.address}"
                    for dep in cell.formula.dependencies:
                        dep_edges.add((src, dep))
            sheet_summaries.append(
                {
                    "name": sheet.name,
                    "cell_count": len(sheet.cells),
                    "formula_count": formulas,
                    "dimensions": list(sheet.dimensions) if sheet.dimensions else None,
                }
            )
        return {
            "source_path": self.source_path,
            "sheet_count": len(self.sheets),
            "cell_count": cell_count,
            "formula_count": formula_count,
            "dependency_graph_size": len(dep_edges),
            "sheets": sheet_summaries,
            "metadata": self.metadata,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
