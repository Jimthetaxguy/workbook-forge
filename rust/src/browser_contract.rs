//! Host-agnostic result envelopes for the browser command API.
//!
//! The WASM facade in `wasm.rs` forwards to these functions so the contract can
//! be tested natively. Every result is a JSON object with `ok`; revisions cross
//! the boundary as decimal strings so JavaScript never needs `BigInt`, and
//! failures keep a machine-readable `code` instead of a flattened string.

use crate::toolkit::Edit;
use crate::xlsx::ImportedWorkbook;
use serde::Serialize;
use serde_json::{Value as Json, json};

/// A structured failure returned inside `{"ok": false, "error": ...}`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ContractError {
    pub code: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub expected_revision: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub current_revision: Option<String>,
}

impl ContractError {
    pub fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
            expected_revision: None,
            current_revision: None,
        }
    }
}

/// Serialize a failure envelope.
pub fn error_json(error: &ContractError) -> String {
    json!({"ok": false, "error": error}).to_string()
}

/// Parse a decimal revision string such as `"0"` or `"42"`.
pub fn parse_revision(text: &str) -> Result<u64, ContractError> {
    if text.is_empty() || text.len() > 20 || !text.bytes().all(|b| b.is_ascii_digit()) {
        return Err(ContractError::new(
            "invalid_revision",
            "expected revision must be a decimal string",
        ));
    }
    text.parse::<u64>()
        .map_err(|_| ContractError::new("invalid_revision", "expected revision is out of range"))
}

/// Map an import-adapter edit failure to a stable code.
///
/// Toolkit errors arrive as `code: message`; the import adapter's own checks
/// have fixed messages that are mapped here.
pub fn classify_edit_error(message: &str) -> ContractError {
    if let Some((code, rest)) = message.split_once(": ")
        && !code.is_empty()
        && code.bytes().all(|b| b.is_ascii_lowercase() || b == b'_')
    {
        return ContractError::new(code, rest);
    }
    let code = match message {
        "new-cell insertion on import is unsupported" => "absent_cell",
        "unknown worksheet" => "unknown_sheet",
        "imported style edits are unsupported" | "1904 formula edits are unsupported" => {
            "unsupported_edit"
        }
        _ => "edit_rejected",
    };
    ContractError::new(code, message)
}

/// The current revision of an imported workbook as a decimal string.
pub fn revision_string(workbook: &ImportedWorkbook) -> String {
    workbook.snapshot().revision.to_string()
}

fn apply(
    workbook: &ImportedWorkbook,
    edits_json: &str,
    expected_revision: &str,
) -> Result<u64, ContractError> {
    if edits_json.len() > crate::agent::MAX_REQUEST_BYTES {
        return Err(ContractError::new(
            "invalid_request",
            "edit payload exceeds 1 MiB",
        ));
    }
    let expected = parse_revision(expected_revision)?;
    crate::model::screen_json_text(edits_json)
        .map_err(|e| ContractError::new("invalid_request", e.to_string()))?;
    let edits: Vec<Edit> = serde_json::from_str(edits_json)
        .map_err(|e| ContractError::new("invalid_request", e.to_string()))?;
    let current = workbook.snapshot().revision;
    if current != expected {
        return Err(ContractError {
            expected_revision: Some(expected.to_string()),
            current_revision: Some(current.to_string()),
            ..ContractError::new("revision_conflict", "expected revision does not match")
        });
    }
    workbook.apply(edits, Some(expected)).map_err(|e| {
        let mut error = classify_edit_error(&e.to_string());
        error.expected_revision = Some(expected.to_string());
        error.current_revision = Some(workbook.snapshot().revision.to_string());
        error
    })
}

/// Apply a JSON array of edits and return `{"ok":true,"revision":"N"}` or a
/// structured failure. Nothing is applied when any edit is rejected.
pub fn apply_edits_envelope(
    workbook: &ImportedWorkbook,
    edits_json: &str,
    expected_revision: &str,
) -> String {
    match apply(workbook, edits_json, expected_revision) {
        Ok(revision) => json!({"ok": true, "revision": revision.to_string()}).to_string(),
        Err(error) => error_json(&error),
    }
}

/// Parse an envelope produced by this module (test and host helper).
pub fn parse_envelope(text: &str) -> Json {
    serde_json::from_str(text).unwrap_or(Json::Null)
}
