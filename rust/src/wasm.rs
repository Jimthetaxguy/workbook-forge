//! Thin JavaScript boundary for browser-local workbook bytes.
//!
//! Workbook parsing, edits, calculation, and OOXML generation remain in the
//! shared Rust implementation. The browser owns file selection and saving.

use crate::toolkit::{CalculationReport, Edit, Session, Sheet, WorkbookModel};
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

    fn calculate(&self) -> CalculationReport {
        match self {
            Self::Imported(workbook) => workbook.calculate(1),
            Self::Authored(session) => session.calculate(1),
        }
    }

    fn apply(&self, edits: Vec<Edit>, expected_revision: u64) -> Result<u64, String> {
        match self {
            Self::Imported(workbook) => workbook
                .apply(edits, Some(expected_revision))
                .map_err(|error| error.to_string()),
            Self::Authored(session) => session
                .apply(edits, Some(expected_revision))
                .map_err(|error| error.to_string()),
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
}

#[wasm_bindgen]
impl BrowserWorkbook {
    /// Return the current versioned workbook model as JSON for the browser UI.
    #[wasm_bindgen(js_name = snapshotJson)]
    pub fn snapshot_json(&self) -> Result<String, JsValue> {
        serde_json::to_string(&self.session.snapshot()).map_err(js_error)
    }

    /// Apply a bounded JSON edit array at an expected revision. Imported files
    /// keep their source-cell guard; authored workbooks may add new cells.
    #[wasm_bindgen(js_name = applyEditsJson)]
    pub fn apply_edits_json(
        &self,
        edits_json: &str,
        expected_revision: u64,
    ) -> Result<u64, JsValue> {
        if edits_json.len() > crate::agent::MAX_REQUEST_BYTES {
            return Err(JsValue::from_str("edit payload exceeds 1 MiB"));
        }
        crate::model::screen_json_text(edits_json).map_err(js_error)?;
        let edits: Vec<Edit> = serde_json::from_str(edits_json).map_err(js_error)?;
        self.session
            .apply(edits, expected_revision)
            .map_err(js_error)
    }

    /// The current revision as a decimal string, so hosts never need `BigInt`.
    #[wasm_bindgen(js_name = revisionString)]
    pub fn revision_string(&self) -> String {
        self.session.snapshot().revision.to_string()
    }

    /// Apply edits at a decimal-string revision and return a JSON envelope:
    /// `{"ok":true,"revision":"N"}` or
    /// `{"ok":false,"error":{"code","message","expected_revision","current_revision"}}`.
    /// This never throws for a rejected edit.
    #[wasm_bindgen(js_name = applyEditsResultJson)]
    pub fn apply_edits_result_json(&self, edits_json: &str, expected_revision: &str) -> String {
        match &self.session {
            WorkbookSession::Imported(workbook) => crate::browser_contract::apply_edits_envelope(
                workbook,
                edits_json,
                expected_revision,
            ),
            WorkbookSession::Authored(session) => {
                apply_authored_edits_envelope(session, edits_json, expected_revision)
            }
        }
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

fn apply_authored_edits_envelope(
    session: &Session,
    edits_json: &str,
    expected_revision: &str,
) -> String {
    use crate::browser_contract::{ContractError, error_json, parse_revision};

    let failure = |error: ContractError| error_json(&error);
    if edits_json.len() > crate::agent::MAX_REQUEST_BYTES {
        return failure(ContractError::new(
            "invalid_request",
            "edit payload exceeds 1 MiB",
        ));
    }
    let expected = match parse_revision(expected_revision) {
        Ok(revision) => revision,
        Err(error) => return failure(error),
    };
    if let Err(error) = crate::model::screen_json_text(edits_json) {
        return failure(ContractError::new("invalid_request", error.to_string()));
    }
    let edits: Vec<Edit> = match serde_json::from_str(edits_json) {
        Ok(edits) => edits,
        Err(error) => return failure(ContractError::new("invalid_request", error.to_string())),
    };
    let current = session.snapshot().revision;
    if current != expected {
        let mut error = ContractError::new("revision_conflict", "expected revision does not match");
        error.expected_revision = Some(expected.to_string());
        error.current_revision = Some(current.to_string());
        return failure(error);
    }
    match session.apply(edits, Some(expected)) {
        Ok(revision) => {
            serde_json::json!({"ok": true, "revision": revision.to_string()}).to_string()
        }
        Err(error) => {
            let current = session.snapshot().revision;
            let mut contract_error = ContractError::new(error.code, error.message);
            contract_error.expected_revision = Some(expected.to_string());
            contract_error.current_revision = Some(current.to_string());
            failure(contract_error)
        }
    }
}

/// Open caller-selected `.xlsx` bytes without uploading or retaining a path.
#[wasm_bindgen(js_name = openXlsx)]
pub fn open_xlsx(bytes: &[u8]) -> Result<BrowserWorkbook, JsValue> {
    let imported =
        xlsx::import_xlsx_bytes(bytes, BTreeMap::new(), BTreeMap::new()).map_err(js_error)?;
    Ok(BrowserWorkbook {
        session: WorkbookSession::Imported(imported),
    })
}

/// Create a blank workbook for browser-local authoring.
#[wasm_bindgen(js_name = createBlankWorkbook)]
pub fn create_blank_workbook(sheet_name: &str) -> Result<BrowserWorkbook, JsValue> {
    let mut model = WorkbookModel::default();
    model.sheets.push(Sheet::new(sheet_name));
    let session = Session::new(model).map_err(js_error)?;
    Ok(BrowserWorkbook {
        session: WorkbookSession::Authored(session),
    })
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
