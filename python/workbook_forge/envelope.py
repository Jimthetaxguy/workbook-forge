"""The preservation envelope an imported workbook carries beside its document.

Spec 10 Design 1, assembled in memory by ``xlsx.import_xlsx`` from what the
importer already holds: the package identity and bytes, the imported document
the writers diff against, and the rules (merged ranges, date system,
compatibility version, feature guards, text layout) that a later write must
honour. Every field but ``source.handle`` and ``insertion_bound`` is a function
of the imported bytes and the import bindings.

``describe()`` is the path-free JSON projection the Rust side produces too.
The ``handle`` is session memory and never appears in it.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
import zipfile

from . import xlsx_features as features
from .model import content_fingerprint

if TYPE_CHECKING:
    from .xlsx import TextLayout
    from .xlsx_features import FeatureGuard


@dataclass(frozen=True)
class SourceIdentity:
    """Which package the envelope describes and how its sheets map to parts."""

    fingerprint: str
    parts: tuple[str, ...]
    workbook_part: str
    sheet_parts: dict[str, str]
    sheet_entries: tuple[str, ...]
    chartsheets: dict[str, str]
    # Where the bytes came from, for the overwrite refusal only. Never serialised.
    handle: Path | None


@dataclass(frozen=True)
class ContainerMetadata:
    """Zip entry metadata and the archive comment, kept for a byte-faithful write."""

    infos: tuple[zipfile.ZipInfo, ...]
    comment: bytes


@dataclass(frozen=True)
class Baseline:
    """The imported bytes and the document as imported: the "before" of every patch."""

    parts: dict[str, bytes]
    container: ContainerMetadata | None
    document: dict[str, Any]


@dataclass(frozen=True)
class TextPreservation:
    """Imported Text (@) cells and the row/column styles an inserted cell inherits."""

    # (casefolded sheet, address).
    cells: frozenset[tuple[str, str]]
    # Casefolded sheet name -> layout.
    layouts: dict[str, TextLayout]


@dataclass(frozen=True)
class PreservationEnvelope:
    source: SourceIdentity
    baseline: Baseline
    tables: tuple[dict[str, Any], ...]
    # Sheet name as written -> merged ranges (column, row, column, row).
    merged: dict[str, tuple[tuple[int, int, int, int], ...]]
    date_system: str
    compatibility_version: int
    guards: FeatureGuard
    text: TextPreservation
    # Rows, columns: the largest absent cell a session may insert at.
    insertion_bound: tuple[int, int]

    def describe(self) -> dict[str, Any]:
        """The path-free JSON projection; identical in shape to the Rust side's."""
        container = self.baseline.container
        written_names = [sheet["name"] for sheet in self.baseline.document["sheets"]]
        return {
            "source": {
                "fingerprint": self.source.fingerprint,
                "parts": list(self.source.parts),
                "workbook_part": self.source.workbook_part,
                "sheet_parts": dict(self.source.sheet_parts),
                "sheet_entries": list(self.source.sheet_entries),
                "chartsheets": dict(self.source.chartsheets),
            },
            "baseline": {
                "parts": {
                    name: content_fingerprint(data) for name, data in self.baseline.parts.items()
                },
                "container": None
                if container is None
                else {"entries": len(container.infos), "comment_bytes": len(container.comment)},
                "document": copy.deepcopy(self.baseline.document),
            },
            "tables": copy.deepcopy(list(self.tables)),
            "merged": {
                sheet: [list(bounds) for bounds in ranges] for sheet, ranges in self.merged.items()
            },
            "date_system": self.date_system,
            "compatibility_version": self.compatibility_version,
            "guards": {
                "data_validations": features.public(self.guards.validations),
                "pivots": features.public(self.guards.pivots),
                "charts": features.public(self.guards.charts),
            },
            "text": {
                "cells": sorted([sheet, address] for sheet, address in self.text.cells),
                "layouts": {
                    name: self.text.layouts[name.casefold()].public()
                    for name in written_names
                    if name.casefold() in self.text.layouts
                },
            },
            "insertion_bound": list(self.insertion_bound),
        }
