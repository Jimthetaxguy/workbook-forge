//! Thin JavaScript boundary for browser-local workbook bytes.
//!
//! Workbook parsing, edits, calculation, and OOXML generation remain in the
//! shared Rust implementation. The browser owns file selection and saving.

use crate::toolkit::Edit;
use crate::xlsx::{self, ImportedWorkbook};
use std::collections::BTreeMap;
use wasm_bindgen::prelude::*;

fn js_error(error: impl std::fmt::Display) -> JsValue {
    JsValue::from_str(&error.to_string())
}

/// An imported workbook whose package and edits remain in this WASM instance.
#[wasm_bindgen]
pub struct BrowserWorkbook {
    imported: ImportedWorkbook,
}

#[wasm_bindgen]
impl BrowserWorkbook {
    /// Return the current versioned workbook model as JSON for the browser UI.
    #[wasm_bindgen(js_name = snapshotJson)]
    pub fn snapshot_json(&self) -> Result<String, JsValue> {
        serde_json::to_string(&self.imported.snapshot()).map_err(js_error)
    }

    /// Apply a bounded JSON array of existing-cell edits at an expected revision.
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
        self.imported
            .apply(edits, Some(expected_revision))
            .map_err(js_error)
    }

    /// Calculate the current workbook and return the existing report contract.
    #[wasm_bindgen(js_name = calculateJson)]
    pub fn calculate_json(&self) -> Result<String, JsValue> {
        serde_json::to_string(&self.imported.calculate(1)).map_err(js_error)
    }

    /// Export the current workbook to bytes without choosing a local path.
    #[wasm_bindgen(js_name = exportXlsx)]
    pub fn export_xlsx(&self) -> Result<Vec<u8>, JsValue> {
        self.imported.export_bytes().map_err(js_error)
    }
}

/// Open caller-selected `.xlsx` bytes without uploading or retaining a path.
#[wasm_bindgen(js_name = openXlsx)]
pub fn open_xlsx(bytes: &[u8]) -> Result<BrowserWorkbook, JsValue> {
    let imported =
        xlsx::import_xlsx_bytes(bytes, BTreeMap::new(), BTreeMap::new()).map_err(js_error)?;
    Ok(BrowserWorkbook { imported })
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
}
