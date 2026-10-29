//! Thin JavaScript boundary for browser-local workbook bytes.
//!
//! Workbook parsing, edits, calculation, and OOXML generation remain in the
//! shared Rust implementation. The browser owns file selection and saving.

use crate::event_log::Baseline;
use crate::toolkit::{CalculationReport, Session, Sheet, WorkbookModel};
use crate::xlsx::{self, ImportedWorkbook};
use std::collections::BTreeMap;
use wasm_bindgen::prelude::*;

enum WorkbookSession {
    Imported(ImportedWorkbook),
    Authored(Session),
}

impl WorkbookSession {
    fn snapshot(&self) -> WorkbookModel {
        match self {
            Self::Imported(workbook) => workbook.snapshot(),
            Self::Authored(session) => session.snapshot(),
        }
    }

    fn revision(&self) -> u64 {
        match self {
            Self::Imported(workbook) => workbook.revision(),
            Self::Authored(session) => session.revision(),
        }
    }

    fn calculate(&self) -> CalculationReport {
        match self {
            Self::Imported(workbook) => workbook.calculate(1),
            Self::Authored(session) => session.calculate(1),
        }
    }

    fn export_xlsx(&self) -> Result<Vec<u8>, String> {
        match self {
            Self::Imported(workbook) => workbook.export_bytes().map_err(|error| error.to_string()),
            Self::Authored(session) => {
                xlsx::export_xlsx_bytes(&session.snapshot()).map_err(|error| error.to_string())
            }
        }
    }
}

fn js_error(error: impl std::fmt::Display) -> JsValue {
    JsValue::from_str(&error.to_string())
}

/// An imported workbook whose package and edits remain in this WASM instance.
#[wasm_bindgen]
pub struct BrowserWorkbook {
    session: WorkbookSession,
    commands: crate::browser_contract::CommandSession,
}

impl BrowserWorkbook {
    fn revision(&self) -> u64 {
        self.session.revision()
    }
}

#[wasm_bindgen]
impl BrowserWorkbook {
    /// Return the current versioned workbook model as JSON for the browser UI.
    #[wasm_bindgen(js_name = snapshotJson)]
    pub fn snapshot_json(&self) -> Result<String, JsValue> {
        serde_json::to_string(&self.session.snapshot()).map_err(js_error)
    }

    /// Apply a bounded JSON edit array at an expected revision. Both imported
    /// and authored workbooks may add cells; imported files refuse insertions
    /// outside Excel's grid (`out_of_bounds`) and merged non-anchor cells.
    /// Logged as an `edits` intent from source `api`.
    #[wasm_bindgen(js_name = applyEditsJson)]
    pub fn apply_edits_json(
        &self,
        edits_json: &str,
        expected_revision: u64,
    ) -> Result<u64, JsValue> {
        let expected = expected_revision.to_string();
        let result = match &self.session {
            WorkbookSession::Imported(workbook) => self
                .commands
                .apply_edits_logged_result(workbook, edits_json, &expected),
            WorkbookSession::Authored(session) => self
                .commands
                .apply_edits_logged_result(session, edits_json, &expected),
        };
        result.map_err(|error| JsValue::from_str(&format!("{}: {}", error.code, error.message)))
    }

    /// The current revision as a decimal string, so hosts never need `BigInt`.
    #[wasm_bindgen(js_name = revisionString)]
    pub fn revision_string(&self) -> String {
        self.revision().to_string()
    }

    /// Apply edits at a decimal-string revision and return a JSON envelope:
    /// `{"ok":true,"revision":"N"}` or
    /// `{"ok":false,"error":{"code","message","expected_revision","current_revision"}}`.
    /// This never throws for a rejected edit. Logged as an `api` intent.
    #[wasm_bindgen(js_name = applyEditsResultJson)]
    pub fn apply_edits_result_json(&self, edits_json: &str, expected_revision: &str) -> String {
        match &self.session {
            WorkbookSession::Imported(workbook) => {
                self.commands
                    .apply_edits_logged(workbook, edits_json, expected_revision)
            }
            WorkbookSession::Authored(session) => {
                self.commands
                    .apply_edits_logged(session, edits_json, expected_revision)
            }
        }
    }

    /// Apply one command envelope (`{"v":1,"command_id",...,"op":{"type":
    /// "set_cells","edits":[...]}}`, or `{"type":"undo"|"redo","target"?}`),
    /// calculate, log, and return the delta response
    /// `{"v":1,"command_id","status","revision","previous_revision","changed",
    /// "evaluated_cells","diagnostics","duplicate","event_id"[,"error"]}`.
    /// Never throws.
    #[wasm_bindgen(js_name = applyAndCalculateJson)]
    pub fn apply_and_calculate_json(&self, envelope_json: &str) -> String {
        match &self.session {
            WorkbookSession::Imported(workbook) => {
                self.commands.apply_and_calculate(workbook, envelope_json)
            }
            WorkbookSession::Authored(session) => {
                self.commands.apply_and_calculate(session, envelope_json)
            }
        }
    }

    /// As `applyAndCalculateJson`, for `{"envelope":{...},"command":{"source",
    /// "univer"}}` so the log records where the intent came from.
    #[wasm_bindgen(js_name = submitJson)]
    pub fn submit_json(&self, request_json: &str) -> String {
        match &self.session {
            WorkbookSession::Imported(workbook) => self.commands.submit(workbook, request_json),
            WorkbookSession::Authored(session) => self.commands.submit(session, request_json),
        }
    }

    /// Write the log's `session.opened` event (once per workbook). `engine_json`
    /// is merged into the engine info, e.g. `{"wasm_sha256":"..."}`.
    #[wasm_bindgen(js_name = startLog)]
    pub fn start_log(&self, name: &str, engine_json: &str) {
        let engine = serde_json::from_str(engine_json).unwrap_or(serde_json::Value::Null);
        let name = (!name.is_empty()).then_some(name);
        self.commands.start_log(name, engine, self.revision());
    }

    /// `{"events":[...],"next_seq":N,"total":T}`: events from `from_seq`.
    #[wasm_bindgen(js_name = logJson)]
    pub fn log_json(&self, from_seq: u32, limit: u32) -> String {
        self.commands
            .log_page(from_seq as usize, limit as usize)
            .to_string()
    }

    /// Append `session.exported` (with the exported bytes' sha256) and return
    /// the whole log as NDJSON.
    #[wasm_bindgen(js_name = exportLogNdjson)]
    pub fn export_log_ndjson(&self, xlsx_sha256: &str) -> String {
        let sha = (!xlsx_sha256.is_empty()).then_some(xlsx_sha256);
        self.commands.export_log_ndjson(self.revision(), sha)
    }

    /// Calculate the current workbook and return the existing report contract.
    #[wasm_bindgen(js_name = calculateJson)]
    pub fn calculate_json(&self) -> Result<String, JsValue> {
        serde_json::to_string(&self.session.calculate()).map_err(js_error)
    }

    /// Export the current workbook to bytes without choosing a local path.
    #[wasm_bindgen(js_name = exportXlsx)]
    pub fn export_xlsx(&self) -> Result<Vec<u8>, JsValue> {
        self.session.export_xlsx().map_err(js_error)
    }
}

/// Open caller-selected `.xlsx` bytes without uploading or retaining a path.
#[wasm_bindgen(js_name = openXlsx)]
pub fn open_xlsx(bytes: &[u8]) -> Result<BrowserWorkbook, JsValue> {
    let imported =
        xlsx::import_xlsx_bytes(bytes, BTreeMap::new(), BTreeMap::new()).map_err(js_error)?;
    let commands = crate::browser_contract::CommandSession::new();
    commands.set_baseline(Baseline::xlsx("workbook.xlsx", bytes));
    Ok(BrowserWorkbook {
        session: WorkbookSession::Imported(imported),
        commands,
    })
}

/// Create a blank workbook for browser-local authoring.
#[wasm_bindgen(js_name = createBlankWorkbook)]
pub fn create_blank_workbook(sheet_name: &str) -> Result<BrowserWorkbook, JsValue> {
    let mut model = WorkbookModel::default();
    model.sheets.push(Sheet::new(sheet_name));
    let session = Session::new(model).map_err(js_error)?;
    let commands = crate::browser_contract::CommandSession::new();
    commands.set_baseline(Baseline::blank(sheet_name));
    Ok(BrowserWorkbook {
        session: WorkbookSession::Authored(session),
        commands,
    })
}

/// Lowercase hex SHA-256 of `bytes` (baseline and export hashes).
#[wasm_bindgen(js_name = sha256Hex)]
pub fn sha256_hex(bytes: &[u8]) -> String {
    crate::event_log::sha256_hex(bytes)
}

/// `replay(baselineBytes, events)`: reapply every applied intent in seq order
/// and check every calc event's deltas. An xlsx baseline is checked by
/// SHA-256 and length (`baseline_mismatch`). A blank baseline has no hash and
/// is checked by length (`replay_mismatch`). `events` is NDJSON or a JSON
/// array. Returns `{"ok":true,"revision","snapshot","values","calc_events",...}`
/// or `{"ok":false,"error":{...}}`; never throws.
#[wasm_bindgen(js_name = replayJson)]
pub fn replay_json(bytes: &[u8], events: &str) -> String {
    crate::event_log::replay_json(bytes, events)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value as Json;
    use wasm_bindgen_test::*;

    wasm_bindgen_test_configure!(run_in_browser);

    #[wasm_bindgen_test]
    fn browser_bytes_open_edit_recalculate_and_export_without_paths() {
        let model = crate::toolkit::operating_scenario();
        let source = xlsx::export_xlsx_bytes(&model).unwrap();
        let workbook = open_xlsx(&source).unwrap();

        let initial: Json = serde_json::from_str(&workbook.calculate_json().unwrap()).unwrap();
        assert_eq!(initial["values"]["Forecast!C2"], 2000.0);

        let revision = workbook
            .apply_edits_json(r#"[{"sheet":"Assumptions","address":"B1","value":25}]"#, 0)
            .unwrap();
        assert_eq!(revision, 1);
        let updated: Json = serde_json::from_str(&workbook.calculate_json().unwrap()).unwrap();
        assert_eq!(updated["values"]["Forecast!C2"], 2500.0);

        let output = workbook.export_xlsx().unwrap();
        let reopened = open_xlsx(&output).unwrap();
        let roundtrip: Json = serde_json::from_str(&reopened.calculate_json().unwrap()).unwrap();
        assert_eq!(roundtrip["values"]["Forecast!C2"], 2500.0);
    }

    #[wasm_bindgen_test]
    fn browser_envelope_reports_revision_strings_and_structured_conflicts() {
        let model = crate::toolkit::operating_scenario();
        let workbook = open_xlsx(&xlsx::export_xlsx_bytes(&model).unwrap()).unwrap();
        assert_eq!(workbook.revision_string(), "0");
        let ok: Json = serde_json::from_str(&workbook.apply_edits_result_json(
            r#"[{"sheet":"Assumptions","address":"B1","value":25}]"#,
            "0",
        ))
        .unwrap();
        assert_eq!(ok, serde_json::json!({"ok": true, "revision": "1"}));
        let stale: Json = serde_json::from_str(&workbook.apply_edits_result_json(
            r#"[{"sheet":"Assumptions","address":"B1","value":30}]"#,
            "0",
        ))
        .unwrap();
        assert_eq!(stale["ok"], false);
        assert_eq!(stale["error"]["code"], "revision_conflict");
        assert_eq!(stale["error"]["current_revision"], "1");
    }

    #[wasm_bindgen_test]
    fn browser_blank_workbook_can_author_new_cells_and_export_round_trip() {
        let workbook = create_blank_workbook("Formula Lab").unwrap();
        let initial: Json = serde_json::from_str(&workbook.snapshot_json().unwrap()).unwrap();
        assert_eq!(initial["revision"], 0);
        assert_eq!(initial["sheets"][0]["name"], "Formula Lab");
        assert_eq!(initial["sheets"][0]["cells"], serde_json::json!({}));

        let edit = workbook.apply_edits_result_json(
            r#"[{"sheet":"Formula Lab","address":"A1","value":2},{"sheet":"Formula Lab","address":"A2","value":3},{"sheet":"Formula Lab","address":"A3","formula":"SUM(A1:A2)"}]"#,
            "0",
        );
        let applied: Json = serde_json::from_str(&edit).unwrap();
        assert_eq!(applied, serde_json::json!({"ok": true, "revision": "1"}));

        let calculated: Json = serde_json::from_str(&workbook.calculate_json().unwrap()).unwrap();
        assert_eq!(calculated["values"]["Formula Lab!A3"], 5.0);

        let bytes = workbook.export_xlsx().unwrap();
        let reopened = open_xlsx(&bytes).unwrap();
        let reopened_snapshot: Json =
            serde_json::from_str(&reopened.snapshot_json().unwrap()).unwrap();
        assert_eq!(
            reopened_snapshot["sheets"][0]["cells"]["A3"]["formula"],
            "SUM(A1:A2)"
        );
        let reopened_calculated: Json =
            serde_json::from_str(&reopened.calculate_json().unwrap()).unwrap();
        assert_eq!(reopened_calculated["values"]["Formula Lab!A3"], 5.0);
    }
}
