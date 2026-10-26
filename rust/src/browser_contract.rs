//! Host-agnostic result envelopes for the browser command API.
//!
//! The WASM facade in `wasm.rs` forwards to these functions so the contract can
//! be tested natively. Every result is a JSON object with `ok`; revisions cross
//! the boundary as decimal strings so JavaScript never needs `BigInt`, and
//! failures keep a machine-readable `code` instead of a flattened string.

use crate::event_log::{Baseline, EventLog, HistoryKind, HistoryPlan, clock};
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
    /// Code-specific structured fields (for example `undo_conflict`'s
    /// `target`, `cells` and `overwritten_by`), flattened into the error.
    #[serde(flatten)]
    pub details: serde_json::Map<String, Json>,
}

impl ContractError {
    pub fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
            expected_revision: None,
            current_revision: None,
            details: serde_json::Map::new(),
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

/// Compare-and-swap apply through a [`CommandTarget`], with revisions on
/// every failure.
pub fn apply_parsed<W: CommandTarget + ?Sized>(
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
    SetCells {
        edits: Vec<Edit>,
    },
    /// Undo the actor's latest applied, not-undone intent, or `target`.
    Undo {
        #[serde(default)]
        target: Option<String>,
    },
    /// Redo the actor's latest undo, or the undo event `target`.
    Redo {
        #[serde(default)]
        target: Option<String>,
    },
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
    /// The session's append-only event log, sequenced under this lock.
    log: EventLog,
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

/// A cell's input as logged: `{"formula": "A1*2"}` (no leading `=`) or
/// `{"value": v}` (`null` for blank or absent).
pub fn input_json(cell: &Option<crate::toolkit::Cell>) -> Json {
    match input_of(cell) {
        (_, Some(formula)) => {
            json!({"formula": formula.strip_prefix('=').unwrap_or(&formula)})
        }
        (value, None) => json!({"value": value}),
    }
}

/// The edit that sets a cell to a logged input.
pub fn edit_from_input(sheet: &str, address: &str, input: &Json) -> Result<Edit, ContractError> {
    let bad = || ContractError::new("invalid_log", format!("bad input for {sheet}!{address}"));
    let (value, formula) = if let Some(formula) = input.get("formula").and_then(Json::as_str) {
        (
            None,
            Some(format!("={}", formula.strip_prefix('=').unwrap_or(formula))),
        )
    } else {
        let value = input.get("value").ok_or_else(bad)?;
        let value: CellValue = serde_json::from_value(value.clone()).map_err(|_| bad())?;
        (Some(value), None)
    };
    Ok(Edit {
        sheet: sheet.to_string(),
        address: address.to_string(),
        value,
        formula,
        style: None,
    })
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

/// Host-supplied description of where an intent came from, logged as
/// `command.source` / `command.univer` (`submitJson`'s `command` field).
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CommandMeta {
    #[serde(default)]
    pub source: Option<String>,
    #[serde(default)]
    pub univer: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SubmitRequest {
    envelope: Json,
    #[serde(default)]
    command: Option<CommandMeta>,
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

    /// Record what the session was opened from (before `start_log`).
    pub fn set_baseline(&self, baseline: Baseline) {
        self.lock().log.set_baseline(baseline);
    }

    /// Write `session.opened` (once). `engine` adds to the default engine
    /// info, e.g. `{"wasm_sha256": "..."}`.
    pub fn start_log(&self, name: Option<&str>, engine: Json, revision: u64) {
        self.lock().log.start(name, engine, revision);
    }

    /// A copy of every event so far.
    pub fn events(&self) -> Vec<Json> {
        self.lock().log.events().to_vec()
    }

    /// `{"events":[...],"next_seq":N,"total":T}` from `from_seq`.
    pub fn log_page(&self, from_seq: usize, limit: usize) -> Json {
        self.lock().log.page(from_seq, limit)
    }

    /// Append `session.exported` and return the log as NDJSON.
    pub fn export_log_ndjson(&self, revision: u64, xlsx_sha256: Option<&str>) -> String {
        self.lock().log.export_ndjson(revision, xlsx_sha256)
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
        self.submit_value(workbook, envelope_json, &CommandMeta::default())
    }

    /// `{"envelope": <command envelope>, "command": {"source","univer"}}`:
    /// apply the envelope with logged command metadata.
    pub fn submit<W: CommandTarget + ?Sized>(&self, workbook: &W, request_json: &str) -> String {
        let parsed = if request_json.len() > crate::agent::MAX_REQUEST_BYTES {
            Err(invalid("submit payload exceeds 1 MiB"))
        } else {
            crate::model::screen_json_text(request_json)
                .map_err(|e| invalid(e.to_string()))
                .and_then(|()| {
                    serde_json::from_str::<SubmitRequest>(request_json)
                        .map_err(|e| invalid(e.to_string()))
                })
        };
        match parsed {
            Ok(request) => self
                .submit_value(
                    workbook,
                    &request.envelope.to_string(),
                    &request.command.unwrap_or_default(),
                )
                .to_string(),
            Err(error) => {
                let mut inner = self.lock();
                let revision = workbook.revision();
                let (_, event) = inner.log.record_intent(
                    Json::Null,
                    json!({"name": "edits", "source": "api"}),
                    Json::Null,
                    false,
                    revision,
                    Vec::new(),
                    Some(&error),
                );
                let mut response = rejected(None, &revision.to_string(), error);
                response["event_id"] = json!(event);
                response.to_string()
            }
        }
    }

    fn submit_value<W: CommandTarget + ?Sized>(
        &self,
        workbook: &W,
        envelope_json: &str,
        meta: &CommandMeta,
    ) -> Json {
        let envelope = match parse_command(envelope_json) {
            Ok(envelope) => envelope,
            Err(error) => {
                let raw = serde_json::from_str::<Json>(envelope_json).unwrap_or(Json::Null);
                let command_id = raw
                    .get("command_id")
                    .and_then(Json::as_str)
                    .map(String::from);
                let revision = workbook.revision();
                let actor = match (raw["actor"]["kind"].as_str(), raw["actor"]["id"].as_str()) {
                    (Some(kind), Some(id)) => json!({"type": kind, "id": id}),
                    _ => Json::Null,
                };
                let name = raw["op"]["type"]
                    .as_str()
                    .map(|op| if op == "set_cells" { "edits" } else { op })
                    .unwrap_or("edits");
                let mut inner = self.lock();
                let (_, event) = inner.log.record_intent(
                    actor,
                    command_json(name, meta, "human", command_id.as_deref(), None),
                    Json::Null,
                    false,
                    revision,
                    Vec::new(),
                    Some(&error),
                );
                let mut response = rejected(command_id.as_deref(), &revision.to_string(), error);
                response["event_id"] = json!(event);
                return response;
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
        let response = execute(&mut inner, workbook, &envelope, meta);
        remember(&mut inner, &envelope.command_id, scope, &response);
        response
    }

    /// The legacy `{"ok":true,"revision":"N"}` apply (no calculation), logged
    /// as an `edits` intent from source `api` so replay stays complete.
    pub fn apply_edits_logged<W: CommandTarget + ?Sized>(
        &self,
        workbook: &W,
        edits_json: &str,
        expected_revision: &str,
    ) -> String {
        match self.apply_edits_logged_result(workbook, edits_json, expected_revision) {
            Ok(revision) => json!({"ok": true, "revision": revision.to_string()}).to_string(),
            Err(error) => error_json(&error),
        }
    }

    /// As [`Self::apply_edits_logged`], returning the revision or the error.
    pub fn apply_edits_logged_result<W: CommandTarget + ?Sized>(
        &self,
        workbook: &W,
        edits_json: &str,
        expected_revision: &str,
    ) -> Result<u64, ContractError> {
        let mut inner = self.lock();
        let actor = json!({"type": "human", "id": "api"});
        let command = json!({"name": "edits", "source": "api"});
        let base = json!({"mode": "strict", "expectedRevision": expected_revision});
        let current = workbook.revision();
        let parsed = (|| {
            if edits_json.len() > crate::agent::MAX_REQUEST_BYTES {
                return Err(invalid("edit payload exceeds 1 MiB"));
            }
            let expected = parse_revision(expected_revision)?;
            crate::model::screen_json_text(edits_json).map_err(|e| invalid(e.to_string()))?;
            let edits: Vec<Edit> =
                serde_json::from_str(edits_json).map_err(|e| invalid(e.to_string()))?;
            Ok((normalized(edits), expected))
        })();
        let (edits, expected) = match parsed {
            Ok(parsed) => parsed,
            Err(error) => {
                inner.log.record_intent(
                    actor,
                    command,
                    base,
                    false,
                    current,
                    Vec::new(),
                    Some(&error),
                );
                return Err(error);
            }
        };
        let requested = requested_inputs(&edits);
        let targets = resolve_targets(workbook, &edits);
        let before = workbook.read_cells(&targets);
        match apply_parsed(workbook, edits, expected) {
            Ok(revision) => {
                let after = workbook.read_cells(&targets);
                let inputs = logged_inputs(&targets, &before, &after);
                let (seq, _) = inner
                    .log
                    .record_intent(actor, command, base, true, revision, inputs, None);
                inner.log.commit_edit("api", seq);
                Ok(revision)
            }
            Err(error) => {
                inner.log.record_intent(
                    actor,
                    command,
                    base,
                    false,
                    current,
                    requested,
                    Some(&error),
                );
                Err(error)
            }
        }
    }
}

fn actor_json(actor: &Actor) -> Json {
    let mut out = json!({"type": actor.kind, "id": actor.id});
    if let Some(on_behalf_of) = &actor.on_behalf_of {
        out["on_behalf_of"] = json!(on_behalf_of);
    }
    out
}

fn command_json(
    name: &str,
    meta: &CommandMeta,
    actor_kind: &str,
    command_id: Option<&str>,
    target: Option<&str>,
) -> Json {
    let default_source = if actor_kind == "agent" {
        "agent"
    } else {
        "api"
    };
    let mut out = json!({
        "name": name,
        "source": meta.source.as_deref().unwrap_or(default_source),
    });
    if let Some(univer) = &meta.univer {
        out["univer"] = json!(univer);
    }
    if let Some(target) = target {
        out["target"] = json!(target);
    }
    if let Some(command_id) = command_id {
        out["command_id"] = json!(command_id);
    }
    out
}

/// Canonical keys of the edit targets, or none when any is unresolvable
/// (apply then reports that target's code).
fn resolve_targets<W: CommandTarget + ?Sized>(workbook: &W, edits: &[Edit]) -> Vec<String> {
    edits
        .iter()
        .map(|edit| workbook.resolve_key(&edit.sheet, &edit.address))
        .collect::<Option<_>>()
        .unwrap_or_default()
}

fn split_key(key: &str) -> (&str, &str) {
    key.rsplit_once('!').unwrap_or(("", key))
}

/// Store every edited formula with its leading `=` (the form the browser
/// engine always sends), so a logged input, which omits it, replays to the
/// identical stored text.
fn normalized(mut edits: Vec<Edit>) -> Vec<Edit> {
    for edit in &mut edits {
        if let Some(formula) = &mut edit.formula
            && !formula.starts_with('=')
        {
            formula.insert(0, '=');
        }
    }
    edits
}

/// What a rejected intent asked for: `after` only.
fn requested_inputs(edits: &[Edit]) -> Vec<Json> {
    edits
        .iter()
        .map(|edit| {
            let after = match (&edit.formula, &edit.value) {
                (Some(formula), _) => {
                    json!({"formula": formula.strip_prefix('=').unwrap_or(formula)})
                }
                (None, Some(value)) => json!({"value": value}),
                (None, None) => json!({"value": null}),
            };
            json!({"sheet": edit.sheet, "address": edit.address, "after": after})
        })
        .collect()
}

fn logged_inputs(
    targets: &[String],
    before: &[Option<crate::toolkit::Cell>],
    after: &[Option<crate::toolkit::Cell>],
) -> Vec<Json> {
    targets
        .iter()
        .zip(before)
        .zip(after)
        .map(|((key, old), new)| {
            let (sheet, address) = split_key(key);
            json!({
                "sheet": sheet,
                "address": address,
                "before": input_json(old),
                "after": input_json(new),
            })
        })
        .collect()
}

/// One applied edit batch: the new revision, its calculation, the delta for
/// patches (`changed`), the calc-event deltas and the logged inputs.
pub struct Applied {
    pub revision: u64,
    pub report: CalculationReport,
    pub changed: Vec<ChangedCell>,
    /// `[key, before, after]` for every formula cell whose value changed.
    pub deltas: Vec<Json>,
    pub inputs: Vec<Json>,
    pub calc_ms: f64,
}

/// Apply `edits` at `expected`, calculate, and diff against `baseline` (the
/// calculated values at `expected`). On failure nothing is applied and the
/// baseline is handed back. Shared by the live command path and replay, so
/// a replayed calc event is computed exactly as it was recorded.
// The error hands the (large) baseline map back so the caller can keep
// reusing it; boxing it would cost an allocation on every rejection instead.
#[allow(clippy::result_large_err)]
pub fn apply_calculate_diff<W: CommandTarget + ?Sized>(
    workbook: &W,
    baseline: BTreeMap<String, CalculatedValue>,
    edits: Vec<Edit>,
    expected: u64,
) -> Result<Applied, (ContractError, BTreeMap<String, CalculatedValue>)> {
    // Edited cells, for input-only changes (formula text with the same value).
    let targets = resolve_targets(workbook, &edits);
    let before = workbook.read_cells(&targets);
    let revision = match apply_parsed(workbook, edits, expected) {
        Ok(revision) => revision,
        Err(error) => return Err((error, baseline)),
    };
    let started = clock::mono_ms();
    let report = workbook.calculate_report();
    let calc_ms = (clock::mono_ms() - started).max(0.0);
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
    let mut deltas = Vec::new();
    let changed: Vec<ChangedCell> = keys
        .iter()
        .zip(cells)
        .map(|(key, cell)| {
            let (sheet, address) = split_key(key);
            let (input, formula) = input_of(&cell);
            let had_formula = targets
                .iter()
                .position(|target| target == key)
                .map(|i| input_of(&before[i]).1.is_some())
                .unwrap_or(false);
            let old = baseline.get(key);
            let new = report.values.get(key);
            if (formula.is_some() || had_formula) && old != new {
                let encode = |value: Option<&CalculatedValue>| {
                    value.map_or(Json::Null, |value| json!(value))
                };
                deltas.push(json!([key, encode(old), encode(new)]));
            }
            let value = new.cloned().unwrap_or_else(|| {
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
    Ok(Applied {
        revision,
        report,
        changed,
        deltas,
        inputs: logged_inputs(&targets, &before, &after),
        calc_ms,
    })
}

fn log_rejection(
    inner: &mut SessionInner,
    envelope: &CommandEnvelope,
    command: Json,
    base: Json,
    revision: u64,
    inputs: Vec<Json>,
    error: ContractError,
) -> Json {
    let (_, event) = inner.log.record_intent(
        actor_json(&envelope.actor),
        command,
        base,
        false,
        revision,
        inputs,
        Some(&error),
    );
    let mut response = rejected(Some(&envelope.command_id), &revision.to_string(), error);
    response["event_id"] = json!(event);
    response
}

fn execute<W: CommandTarget + ?Sized>(
    inner: &mut SessionInner,
    workbook: &W,
    envelope: &CommandEnvelope,
    meta: &CommandMeta,
) -> Json {
    let current = workbook.revision();
    let actor_id = envelope.actor.id.as_str();
    let (name, history, target) = match &envelope.op {
        Operation::SetCells { .. } => ("edits", None, None),
        Operation::Undo { target } => ("undo", Some(HistoryKind::Undo), target.as_deref()),
        Operation::Redo { target } => ("redo", Some(HistoryKind::Redo), target.as_deref()),
    };
    let command_id = Some(envelope.command_id.as_str());
    let kind = envelope.actor.kind.as_str();
    let mut command = command_json(name, meta, kind, command_id, target);
    // UI history runs under the cell-conflict rule rather than strict CAS.
    let base = match (history, envelope.mode.as_str()) {
        (None, "strict") => json!({"mode": "strict", "expectedRevision": envelope.base_revision}),
        (_, "rebase") => {
            json!({"mode": "rebase", "baseRevision": envelope.base_revision})
        }
        _ => json!({"mode": "cells", "baseRevision": envelope.base_revision}),
    };
    let base_revision = match parse_revision(&envelope.base_revision) {
        Ok(base) => base,
        Err(error) => {
            return log_rejection(inner, envelope, command, base, current, Vec::new(), error);
        }
    };
    if envelope.mode == "rebase" {
        let error = ContractError::new(
            "unsupported_mode",
            "mode \"rebase\" is not implemented yet; submit with mode \"strict\"",
        );
        return log_rejection(inner, envelope, command, base, current, Vec::new(), error);
    }
    if !envelope.preconditions.is_empty() {
        let error = ContractError::new(
            "unsupported_precondition",
            "preconditions are not implemented yet; send an empty list",
        );
        return log_rejection(inner, envelope, command, base, current, Vec::new(), error);
    }
    let (edits, expected, plan): (Vec<Edit>, u64, Option<HistoryPlan>) = match &envelope.op {
        Operation::SetCells { edits } => (normalized(edits.clone()), base_revision, None),
        Operation::Undo { .. } | Operation::Redo { .. } => {
            let kind = history.unwrap_or(HistoryKind::Undo);
            match inner
                .log
                .plan(kind, actor_id, target, |keys| workbook.read_cells(keys))
            {
                Ok(plan) => {
                    command["target"] = json!(plan.target_id);
                    if kind == HistoryKind::Redo {
                        command["undo_event"] = json!(plan.source_id);
                    }
                    (plan.edits.clone(), current, Some(plan))
                }
                Err(error) => {
                    if let Some(target) = error.details.get("target") {
                        command["target"] = target.clone();
                    }
                    return log_rejection(
                        inner,
                        envelope,
                        command,
                        base,
                        current,
                        Vec::new(),
                        error,
                    );
                }
            }
        }
    };
    let requested = requested_inputs(&edits);
    // Calculated values at the current revision. Reused from the previous
    // command unless another path (applyEditsJson) moved the revision.
    let baseline = match inner.baseline.take() {
        Some((revision, values)) if revision == current => values,
        _ => workbook.calculate_report().values,
    };
    let applied = match apply_calculate_diff(workbook, baseline, edits, expected) {
        Ok(applied) => applied,
        Err((error, baseline)) => {
            inner.baseline = Some((current, baseline));
            return log_rejection(inner, envelope, command, base, current, requested, error);
        }
    };
    let revision = applied.revision;
    let (seq, event_id) = inner.log.record_intent(
        actor_json(&envelope.actor),
        command,
        base,
        true,
        revision,
        applied.inputs,
        None,
    );
    match &plan {
        Some(plan) => inner.log.commit_history(actor_id, plan, seq),
        None => inner.log.commit_edit(actor_id, seq),
    }
    let calc_event = inner.log.record_calc(
        &event_id,
        revision,
        applied.report.evaluated_cells.len(),
        applied.calc_ms,
        applied.deltas,
        json!(applied.report.diagnostics),
    );
    let response = json!({
        "v": ENVELOPE_VERSION,
        "command_id": envelope.command_id,
        "status": "applied",
        "revision": revision.to_string(),
        "previous_revision": current.to_string(),
        "changed": applied.changed,
        "evaluated_cells": applied.report.evaluated_cells.len(),
        "diagnostics": applied.report.diagnostics,
        "duplicate": false,
        "event_id": event_id,
        "calc_event_id": calc_event,
    });
    if applied.report.revision == revision && !applied.report.stale {
        inner.baseline = Some((revision, applied.report.values));
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
