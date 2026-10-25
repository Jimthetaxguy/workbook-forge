//! Host-agnostic result envelopes for the browser command API.
//!
//! The WASM facade in `wasm.rs` forwards to these functions so the contract can
//! be tested natively. Every result is a JSON object with `ok`; revisions cross
//! the boundary as decimal strings so JavaScript never needs `BigInt`, and
//! failures keep a machine-readable `code` instead of a flattened string.

use crate::toolkit::{CalculatedValue, CalculationReport, CellValue, Edit, Session};
use crate::xlsx::ImportedWorkbook;
use serde::{Deserialize, Serialize};
use serde_json::{Value as Json, json};
use std::collections::{BTreeMap, BTreeSet, HashMap, VecDeque};
use std::sync::{Mutex, MutexGuard};

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

/// The current revision of an imported workbook as a decimal string. Reads
/// the revision under the session lock; the model is not cloned.
pub fn revision_string(workbook: &ImportedWorkbook) -> String {
    workbook.revision().to_string()
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
    apply_parsed(workbook, edits, expected)
}

/// What the command envelope needs from a workbook session. Implemented for
/// imported workbooks and for authored (blank) toolkit sessions so both
/// browser workbook kinds share one apply + calculate + delta path.
pub trait CommandTarget {
    /// The current revision, without cloning the model.
    fn revision(&self) -> u64;
    /// Canonical `Sheet!A1` key, or `None` when the target is unresolvable.
    fn resolve_key(&self, sheet: &str, address: &str) -> Option<String>;
    /// Copy only the named cells (canonical keys).
    fn read_cells(&self, keys: &[String]) -> Vec<Option<crate::toolkit::Cell>>;
    /// Apply edits at an expected revision, mapping failures to contract codes.
    fn apply_edits(&self, edits: Vec<Edit>, expected: u64) -> Result<u64, ContractError>;
    /// Calculate the current revision.
    fn calculate_report(&self) -> CalculationReport;
}

impl CommandTarget for ImportedWorkbook {
    fn revision(&self) -> u64 {
        ImportedWorkbook::revision(self)
    }
    fn resolve_key(&self, sheet: &str, address: &str) -> Option<String> {
        ImportedWorkbook::resolve_key(self, sheet, address).ok()
    }
    fn read_cells(&self, keys: &[String]) -> Vec<Option<crate::toolkit::Cell>> {
        ImportedWorkbook::read_cells(self, keys)
    }
    fn apply_edits(&self, edits: Vec<Edit>, expected: u64) -> Result<u64, ContractError> {
        self.apply(edits, Some(expected))
            .map_err(|e| classify_edit_error(&e.to_string()))
    }
    fn calculate_report(&self) -> CalculationReport {
        self.calculate(1)
    }
}

impl CommandTarget for Session {
    fn revision(&self) -> u64 {
        Session::revision(self)
    }
    fn resolve_key(&self, sheet: &str, address: &str) -> Option<String> {
        Session::resolve_key(self, sheet, address).ok()
    }
    fn read_cells(&self, keys: &[String]) -> Vec<Option<crate::toolkit::Cell>> {
        Session::read_cells(self, keys)
    }
    fn apply_edits(&self, edits: Vec<Edit>, expected: u64) -> Result<u64, ContractError> {
        self.apply(edits, Some(expected))
            .map_err(|e| ContractError::new(e.code, e.message))
    }
    fn calculate_report(&self) -> CalculationReport {
        self.calculate(1)
    }
}

fn apply_parsed<W: CommandTarget + ?Sized>(
    workbook: &W,
    edits: Vec<Edit>,
    expected: u64,
) -> Result<u64, ContractError> {
    let current = workbook.revision();
    if current != expected {
        return Err(ContractError {
            expected_revision: Some(expected.to_string()),
            current_revision: Some(current.to_string()),
            ..ContractError::new("revision_conflict", "expected revision does not match")
        });
    }
    workbook.apply_edits(edits, expected).map_err(|mut error| {
        error.expected_revision = Some(expected.to_string());
        error.current_revision = Some(workbook.revision().to_string());
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

// ---- Command envelope: apply + calculate + delta -------------------------
//
// One call per user or agent intent. The request is the multi-user command
// envelope (realtime design, multi-user contract section 6.1). Only
// `mode: "strict"` (compare-and-swap on `base_revision`, today's semantics)
// with an empty `preconditions` list is implemented. Everything else fails
// closed with a rejected response that applies nothing:
//
// * `mode: "rebase"` -> `unsupported_mode`
// * non-empty `preconditions` -> `unsupported_precondition`
// * a malformed envelope (unknown field, missing field, `v` != 1, bad id,
//   bad actor kind, bad `client_seq`, unknown `op.type`, > 1 MiB)
//   -> `invalid_request`
// * a malformed `base_revision` -> `invalid_revision` (the code used today)
//
// Every response has the same shape, applied or rejected:
// `{"v":1,"command_id","status","revision","previous_revision","changed",
//   "evaluated_cells","diagnostics","duplicate"[,"error"]}`.
//
// Dedupe: a validated envelope is consumed whether it applies or is
// rejected (every replica must agree an id was used). Replaying its
// `command_id`, or the same `(actor.id, idempotency_key)`, returns the
// original response with `"duplicate": true` and applies nothing, even if the
// replayed payload differs. The last `DEDUPE_CAPACITY` outcomes are kept. An
// envelope that fails validation is not consumed.

/// Supported envelope version.
pub const ENVELOPE_VERSION: u64 = 1;
/// How many recent command outcomes a session remembers for dedupe.
pub const DEDUPE_CAPACITY: usize = 1024;
const MAX_ID_BYTES: usize = 256;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CommandEnvelope {
    v: u64,
    command_id: String,
    client_id: String,
    client_seq: String,
    #[serde(default)]
    idempotency_key: Option<String>,
    actor: Actor,
    base_revision: String,
    mode: String,
    #[serde(default)]
    preconditions: Vec<Json>,
    op: Operation,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Actor {
    kind: String,
    id: String,
    #[serde(default)]
    on_behalf_of: Option<String>,
}

#[derive(Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum Operation {
    SetCells { edits: Vec<Edit> },
}

/// One cell in a delta: its calculated value (the `calculateJson` value
/// encoding, which for scalars is the snapshot's cell value encoding) and its
/// current formula without the leading `=`, or `null` for a literal.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ChangedCell {
    pub sheet: String,
    pub address: String,
    pub value: CalculatedValue,
    pub formula: Option<String>,
}

#[derive(Default)]
struct SessionInner {
    /// Oldest first: (command_id, idempotency scope), for eviction.
    order: VecDeque<(String, Option<String>)>,
    by_command: HashMap<String, Json>,
    by_key: HashMap<String, Json>,
    /// Calculated values at a revision: the base of the next diff.
    baseline: Option<(u64, BTreeMap<String, CalculatedValue>)>,
}

/// Per-workbook command state kept beside an `ImportedWorkbook`: bounded
/// dedupe memory and the last calculated values used to compute deltas.
#[derive(Default)]
pub struct CommandSession {
    inner: Mutex<SessionInner>,
}

fn valid_id(text: &str) -> bool {
    !text.is_empty()
        && text.len() <= MAX_ID_BYTES
        && text.bytes().all(|b| (0x21..0x7f).contains(&b))
}

fn invalid(message: impl Into<String>) -> ContractError {
    ContractError::new("invalid_request", message)
}

fn parse_command(text: &str) -> Result<CommandEnvelope, ContractError> {
    if text.len() > crate::agent::MAX_REQUEST_BYTES {
        return Err(invalid("command payload exceeds 1 MiB"));
    }
    crate::model::screen_json_text(text).map_err(|e| invalid(e.to_string()))?;
    let envelope: CommandEnvelope =
        serde_json::from_str(text).map_err(|e| invalid(e.to_string()))?;
    if envelope.v != ENVELOPE_VERSION {
        return Err(invalid("unsupported envelope version; expected v = 1"));
    }
    let ids = [
        ("command_id", Some(&envelope.command_id)),
        ("client_id", Some(&envelope.client_id)),
        ("idempotency_key", envelope.idempotency_key.as_ref()),
        ("actor.id", Some(&envelope.actor.id)),
        ("actor.on_behalf_of", envelope.actor.on_behalf_of.as_ref()),
    ];
    for (name, value) in ids {
        if let Some(value) = value
            && !valid_id(value)
        {
            return Err(invalid(format!(
                "{name} must be 1-256 printable ASCII characters"
            )));
        }
    }
    if parse_revision(&envelope.client_seq).is_err() {
        return Err(invalid("client_seq must be a decimal string"));
    }
    if envelope.actor.kind != "human" && envelope.actor.kind != "agent" {
        return Err(invalid("actor.kind must be \"human\" or \"agent\""));
    }
    if envelope.mode != "strict" && envelope.mode != "rebase" {
        return Err(invalid("mode must be \"strict\" or \"rebase\""));
    }
    Ok(envelope)
}

fn input_of(cell: &Option<crate::toolkit::Cell>) -> (CellValue, Option<String>) {
    match cell {
        Some(cell) => (cell.value.clone(), cell.formula.clone()),
        None => (CellValue::Blank, None),
    }
}

fn rejected(command_id: Option<&str>, revision: &str, error: ContractError) -> Json {
    json!({
        "v": ENVELOPE_VERSION,
        "command_id": command_id,
        "status": "rejected",
        "revision": revision,
        "previous_revision": revision,
        "changed": [],
        "evaluated_cells": 0,
        "diagnostics": [],
        "duplicate": false,
        "error": error,
    })
}

impl CommandSession {
    pub fn new() -> Self {
        Self::default()
    }

    fn lock(&self) -> MutexGuard<'_, SessionInner> {
        self.inner
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    /// Apply one command envelope, calculate, and return the delta response
    /// as JSON. Rejections are responses, never panics or JS exceptions.
    pub fn apply_and_calculate<W: CommandTarget + ?Sized>(
        &self,
        workbook: &W,
        envelope_json: &str,
    ) -> String {
        self.apply_and_calculate_value(workbook, envelope_json)
            .to_string()
    }

    /// As [`Self::apply_and_calculate`], returning the response value.
    pub fn apply_and_calculate_value<W: CommandTarget + ?Sized>(
        &self,
        workbook: &W,
        envelope_json: &str,
    ) -> Json {
        let envelope = match parse_command(envelope_json) {
            Ok(envelope) => envelope,
            Err(error) => {
                let command_id = serde_json::from_str::<Json>(envelope_json)
                    .ok()
                    .and_then(|v| v.get("command_id")?.as_str().map(String::from));
                let revision = workbook.revision().to_string();
                return rejected(command_id.as_deref(), &revision, error);
            }
        };
        let mut inner = self.lock();
        let scope = envelope
            .idempotency_key
            .as_deref()
            .map(|key| format!("{}\u{0}{key}", envelope.actor.id));
        let cached = inner
            .by_command
            .get(&envelope.command_id)
            .or_else(|| scope.as_ref().and_then(|s| inner.by_key.get(s)));
        if let Some(original) = cached {
            let mut duplicate = original.clone();
            duplicate["duplicate"] = Json::Bool(true);
            return duplicate;
        }
        let response = execute(&mut inner, workbook, &envelope);
        remember(&mut inner, &envelope.command_id, scope, &response);
        response
    }
}

fn execute<W: CommandTarget + ?Sized>(
    inner: &mut SessionInner,
    workbook: &W,
    envelope: &CommandEnvelope,
) -> Json {
    let id = Some(envelope.command_id.as_str());
    let current = workbook.revision();
    let base = match parse_revision(&envelope.base_revision) {
        Ok(base) => base,
        Err(error) => return rejected(id, &current.to_string(), error),
    };
    if envelope.mode == "rebase" {
        let error = ContractError::new(
            "unsupported_mode",
            "mode \"rebase\" is not implemented yet; submit with mode \"strict\"",
        );
        return rejected(id, &current.to_string(), error);
    }
    if !envelope.preconditions.is_empty() {
        let error = ContractError::new(
            "unsupported_precondition",
            "preconditions are not implemented yet; send an empty list",
        );
        return rejected(id, &current.to_string(), error);
    }
    let Operation::SetCells { edits } = &envelope.op;
    // Calculated values at the current revision. Reused from the previous
    // command unless another path (applyEditsJson) moved the revision.
    let baseline = match inner.baseline.take() {
        Some((revision, values)) if revision == current => values,
        _ => workbook.calculate_report().values,
    };
    // Edited cells, for input-only changes (formula text with the same value).
    // An unresolvable target leaves this empty; apply then reports its code.
    let targets: Vec<String> = edits
        .iter()
        .map(|edit| workbook.resolve_key(&edit.sheet, &edit.address))
        .collect::<Option<_>>()
        .unwrap_or_default();
    let before = workbook.read_cells(&targets);
    let revision = match apply_parsed(workbook, edits.clone(), base) {
        Ok(revision) => revision,
        Err(error) => {
            inner.baseline = Some((current, baseline));
            return rejected(id, &current.to_string(), error);
        }
    };
    let report = workbook.calculate_report();
    let mut keys: BTreeSet<String> = report
        .values
        .iter()
        .filter(|(key, value)| baseline.get(*key) != Some(*value))
        .map(|(key, _)| key.clone())
        .collect();
    keys.extend(
        baseline
            .keys()
            .filter(|key| !report.values.contains_key(*key))
            .cloned(),
    );
    let after = workbook.read_cells(&targets);
    for ((key, old), new) in targets.iter().zip(&before).zip(&after) {
        if input_of(old) != input_of(new) {
            keys.insert(key.clone());
        }
    }
    let keys: Vec<String> = keys.into_iter().collect();
    let cells = workbook.read_cells(&keys);
    let changed: Vec<ChangedCell> = keys
        .iter()
        .zip(cells)
        .map(|(key, cell)| {
            let (sheet, address) = key.rsplit_once('!').unwrap_or(("", key));
            let (input, formula) = input_of(&cell);
            let value = report.values.get(key).cloned().unwrap_or_else(|| {
                CalculatedValue::Scalar(if formula.is_some() {
                    CellValue::Blank
                } else {
                    input
                })
            });
            ChangedCell {
                sheet: sheet.to_string(),
                address: address.to_string(),
                value,
                formula: formula.map(|f| f.strip_prefix('=').unwrap_or(&f).to_string()),
            }
        })
        .collect();
    let response = json!({
        "v": ENVELOPE_VERSION,
        "command_id": envelope.command_id,
        "status": "applied",
        "revision": revision.to_string(),
        "previous_revision": base.to_string(),
        "changed": changed,
        "evaluated_cells": report.evaluated_cells.len(),
        "diagnostics": report.diagnostics,
        "duplicate": false,
    });
    if report.revision == revision && !report.stale {
        inner.baseline = Some((revision, report.values));
    }
    response
}

fn remember(inner: &mut SessionInner, command_id: &str, scope: Option<String>, response: &Json) {
    if inner.order.len() >= DEDUPE_CAPACITY
        && let Some((old_id, old_scope)) = inner.order.pop_front()
    {
        inner.by_command.remove(&old_id);
        if let Some(old_scope) = old_scope {
            inner.by_key.remove(&old_scope);
        }
    }
    inner
        .by_command
        .insert(command_id.to_string(), response.clone());
    if let Some(scope) = &scope {
        inner.by_key.insert(scope.clone(), response.clone());
    }
    inner.order.push_back((command_id.to_string(), scope));
}
