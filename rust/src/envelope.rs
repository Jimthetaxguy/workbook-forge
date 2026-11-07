//! The in-memory preservation envelope (spec 10 Design 1), assembled by
//! `xlsx::import_xlsx` from what the importer already holds.
//!
//! Every field is a pure function of the imported package bytes except
//! `source.handle`, `insertion_bound` and the bindings inside
//! `baseline.document`, which come from the import call. `describe()` is the
//! path-free JSON projection both languages agree on; `handle` never appears
//! in it.

use crate::model::content_fingerprint;
use crate::toolkit::WorkbookModel;
use crate::xlsx::{FeatureGuard, TextLayout};
use serde_json::{Map, Value as Json, json};
use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;

/// Which bytes the envelope belongs to and how the package names its sheets.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SourceIdentity {
    /// `sha256:` + 64 hex over the imported bytes; the envelope's identity.
    pub fingerprint: String,
    /// Every part name, sorted: the key set of `baseline.parts`.
    pub parts: Vec<String>,
    pub workbook_part: String,
    /// Worksheet name -> part. Chartsheets are listed separately.
    pub sheet_parts: BTreeMap<String, String>,
    /// Every `<sheet>` entry in workbook order, the order `localSheetId` counts.
    pub sheet_entries: Vec<String>,
    /// Chartsheet name -> part.
    pub chartsheets: BTreeMap<String, String>,
    /// Where the bytes came from; session memory, never serialised.
    pub handle: Option<PathBuf>,
}

/// Zip entry metadata and archive comment. The Rust package keeps neither
/// today, so `baseline.container` is always `None`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ContainerMetadata {
    pub entries: usize,
    pub comment_bytes: usize,
}

/// The imported bytes and the document as imported: the "before" every
/// patch diffs against.
#[derive(Clone, Debug, PartialEq)]
pub struct Baseline {
    pub parts: BTreeMap<String, Vec<u8>>,
    pub container: Option<ContainerMetadata>,
    pub document: WorkbookModel,
}

/// Imported Text (`@`) cells and the per-sheet layout an inserted cell
/// inherits its number format from.
#[derive(Clone, Debug)]
pub struct TextPreservation {
    /// `(lowercased sheet, address)`.
    pub cells: BTreeSet<(String, String)>,
    /// Lowercased sheet name -> layout; `layouts()` projects it.
    pub(crate) layouts: BTreeMap<String, TextLayout>,
}

/// Everything an imported workbook needs beyond its document to patch the
/// package it came from.
#[derive(Clone)]
pub struct PreservationEnvelope {
    pub source: SourceIdentity,
    pub baseline: Baseline,
    pub tables: Vec<Json>,
    /// Sheet name -> merged ranges `(c1, r1, c2, r2)`.
    pub merged: BTreeMap<String, Vec<(u32, u32, u32, u32)>>,
    /// `"1900"` or `"1904"`.
    pub date_system: String,
    /// The integer, not a flag; 3 or later blocks formula edits.
    pub compatibility_version: u32,
    pub(crate) guards: FeatureGuard,
    /// Pivot and chart records as the importer inspected them; `guards()`
    /// projects them beside the validation rules.
    pub(crate) pivot_records: Vec<Json>,
    pub(crate) chart_records: Vec<Json>,
    pub text: TextPreservation,
    /// `(rows, columns)` an absent cell may be inserted within.
    pub insertion_bound: (u32, u32),
}

impl PreservationEnvelope {
    /// Validation, pivot and chart rules as inspection records.
    pub fn guards(&self) -> Json {
        json!({
            "data_validations": self.guards.records(),
            "pivots": self.pivot_records,
            "charts": self.chart_records,
        })
    }

    /// Text layouts keyed by the sheet's name as written in the workbook.
    pub fn layouts(&self) -> Json {
        Json::Object(
            self.text
                .layouts
                .values()
                .map(|layout| (layout.name.clone(), layout.public()))
                .collect::<Map<_, _>>(),
        )
    }

    /// The path-free projection. `baseline.parts` carries a fingerprint per
    /// part, not the bytes.
    pub fn describe(&self) -> Json {
        let part_fingerprints: Map<_, _> = self
            .baseline
            .parts
            .iter()
            .map(|(part, bytes)| (part.clone(), Json::from(content_fingerprint(bytes))))
            .collect();
        // A derived Serialize over strings, numbers and maps keyed by strings
        // cannot fail.
        let document =
            serde_json::to_value(&self.baseline.document).expect("toolkit document serialises");
        json!({
            "source": {
                "fingerprint": self.source.fingerprint,
                "parts": self.source.parts,
                "workbook_part": self.source.workbook_part,
                "sheet_parts": self.source.sheet_parts,
                "sheet_entries": self.source.sheet_entries,
                "chartsheets": self.source.chartsheets,
            },
            "baseline": {
                "parts": part_fingerprints,
                "container": self.baseline.container.as_ref().map(|container| json!({
                    "entries": container.entries,
                    "comment_bytes": container.comment_bytes,
                })),
                "document": document,
            },
            "tables": self.tables,
            "merged": self.merged,
            "date_system": self.date_system,
            "compatibility_version": self.compatibility_version,
            "guards": self.guards(),
            "text": {
                "cells": self.text.cells,
                "layouts": self.layouts(),
            },
            "insertion_bound": [self.insertion_bound.0, self.insertion_bound.1],
        })
    }
}
