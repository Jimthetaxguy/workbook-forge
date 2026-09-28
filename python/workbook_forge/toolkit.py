"""Workbook authoring with an independent Python engine by default.

The optional Rust bridge selects the separately implemented Rust engine.
Both engines use the same interchange contract and own their editing sessions.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Iterable, Mapping
from typing import Any


class NativeUnavailableError(ImportError):
    """The native workbook engine was not included in this installation."""


def _native():
    try:
        return importlib.import_module("workbook_forge._native")
    except ImportError as exc:
        raise NativeUnavailableError(
            "The explicit Rust backend requires workbook_forge._native. "
            "Install a native Workbook Forge wheel or build with "
            "WORKBOOK_FORGE_BUILD_NATIVE=1 and --no-build-isolation. "
            "The default Python backend works independently without Rust."
        ) from exc


def _engine(backend: str):
    if backend == "python":
        return importlib.import_module("workbook_forge.python_engine")
    if backend == "rust":
        return _native()
    raise ValueError("backend must be 'python' or 'rust'")


def _digest(document: Any) -> str:
    return hashlib.sha256(json.dumps(document, sort_keys=True, allow_nan=False, ensure_ascii=False).encode()).hexdigest()


def _encode(value: Any) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"))


class WorkbookModel:
    """Author, inspect, and calculate one independent workbook session.

    ``document`` accepts the versioned workbook JSON contract. Otherwise
    ``sheets`` is a sequence of names (default: ``Sheet1``). Input and output
    names are application bindings, not Excel defined names. Bindings are
    fixed when a session is created; create another model from ``to_dict()``
    to define another tool over the same cells.
    """

    def __init__(
        self,
        sheets: Iterable[str] | None = None,
        *,
        document: Mapping[str, Any] | None = None,
        inputs: Mapping[str, Any] | None = None,
        outputs: Mapping[str, Any] | None = None,
        backend: str = "python",
    ):
        if document is not None and sheets is not None:
            raise ValueError("specify sheets or document, not both")
        if isinstance(sheets, str):
            raise TypeError("sheets must be a sequence of worksheet names")
        if document is None:
            data = {
                "schema_version": 1,
                "revision": 0,
                "sheets": [
                    {"id": f"sheet-{index}", "name": name, "cells": {}, "column_widths": {}}
                    for index, name in enumerate(sheets if sheets is not None else ["Sheet1"], 1)
                ],
                "inputs": {},
                "outputs": {},
            }
        else:
            data = json.loads(_encode(document))
        if inputs is not None:
            data["inputs"] = dict(inputs)
        if outputs is not None:
            data["outputs"] = dict(outputs)
        self._engine = _engine(backend)
        self._backend = backend
        self._session = self._engine.Session(_encode(data))
        self._source_baseline = None
        self._import_metadata = None

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def revision(self) -> int:
        return self.to_dict()["revision"]

    def to_dict(self) -> dict[str, Any]:
        """Return a detached snapshot; modifying it does not edit this session."""
        return json.loads(self._session.snapshot())

    def apply(self, edits: Iterable[Mapping[str, Any]], *, expected_revision: int | None = None) -> int:
        """Commit a bounded edit batch atomically or leave the session unchanged."""
        batch = list(edits)
        if (
            self._source_baseline is not None
            and self._source_baseline.uses_1904_date_system
            and any(edit.get("formula") is not None for edit in batch)
        ):
            from .workbook import UnsupportedWorkbook

            raise UnsupportedWorkbook(
                "formula edits in imported 1904 date-system workbooks are unsupported"
            )
        return self._session.apply(_encode(batch), expected_revision)

    def set_value(self, sheet: str, address: str, value: Any) -> int:
        return self.apply([{"sheet": sheet, "address": address, "value": value}])

    def set_formula(self, sheet: str, address: str, formula: str) -> int:
        return self.apply([{"sheet": sheet, "address": address, "formula": formula}])

    def set_style(self, sheet: str, address: str, **style: Any) -> int:
        """Replace a cell's supported style; empty arguments reset that style."""
        return self.apply([{"sheet": sheet, "address": address, "style": style}])

    def set_inputs(self, values: Mapping[str, Any], *, expected_revision: int | None = None) -> int:
        """Validate application inputs and apply them in a single atomic commit."""
        return self._session.set_inputs(_encode(dict(values)), expected_revision)

    def calculate(self, *, workers: int = 1) -> dict[str, Any]:
        """Calculate outputs and their dependencies from a coherent snapshot."""
        snapshot = self.to_dict()
        report = json.loads(self._session.calculate(workers))
        report["backend"] = self.backend
        if report["revision"] == snapshot["revision"]:
            report["model_digest"] = _digest(snapshot)
        return report

    def inspect(self) -> dict[str, Any]:
        report = json.loads(self._engine.inspect(_encode(self.to_dict())))
        report["backend"] = self.backend
        if self._import_metadata is not None:
            report["xlsx"] = json.loads(_encode(self._import_metadata))
        return report

    def copy_formula(
        self, sheet: str, source: str, target: str, *, target_sheet: str | None = None
    ) -> int:
        """Copy a formula with mixed references adjusted by its cell displacement."""
        from .workbook import _cell_position, _normal_address

        snapshot = self.to_dict()
        source = _normal_address(source)
        source_sheet = next((item for item in snapshot["sheets"] if item["name"] == sheet), None)
        if source_sheet is None:
            raise KeyError(sheet)
        formula = source_sheet["cells"].get(source, {}).get("formula")
        if formula is None:
            raise ValueError(f"source is not a formula cell: {sheet}!{source}")
        source_column, source_row = _cell_position(source)
        target_column, target_row = _cell_position(target)
        copied = self._engine.copy_formula(formula, target_row - source_row, target_column - source_column)
        return self.apply(
            [{"sheet": target_sheet or sheet, "address": target, "formula": copied}],
            expected_revision=snapshot["revision"],
        )


def operating_scenario(*, backend: str = "python") -> WorkbookModel:
    """Return the synthetic two-sheet operating-scenario tool."""
    return WorkbookModel(document=json.loads(_engine(backend).scenario()), backend=backend)
