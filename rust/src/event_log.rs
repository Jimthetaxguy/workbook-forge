//! Append-only session event log (schema v1) and log-based undo/redo.
//!
//! The log is owned by the browser command session (`CommandSession` in
//! `browser_contract.rs`) and is appended under the same lock that assigns
//! revisions, so log order = revision order = patch order. Intent events are
//! the source of truth; calc events are derived from them and are re-derived
//! and checked by [`replay`]. Rejected intents are logged with their error
//! code, never dropped. Revisions are decimal strings.
//!
//! Event kinds (`kind`):
//!
//! * `session` with `op: "opened"` (seq 0: baseline name, sha256, bytes and
//!   engine info) or `op: "exported"` (revision and the exported XLSX sha256).
//! * `intent`: one user or agent command (`edits`, `undo` or `redo`) with its
//!   actor, base, status (`applied` | `rejected`), revision, per-cell
//!   `before`/`after` inputs and error.
//! * `calc`: the recalculation an applied intent caused: `caused_by`,
//!   `revision`, `evaluated`, `changed`, `calc_ms`, `deltas` as
//!   `[key, before, after]` triples (formula cells whose value changed) and
//!   diagnostics.
//!
//! Undo and redo are intents too. `undo` targets the actor's own latest
//! applied, not-undone intent (or an explicit event id) and applies its
//! `after -> before`, only if every target cell's current input still equals
//! the event's `after`; otherwise it is rejected with `undo_conflict` naming
//! the cells and the event that overwrote them. Revisions only move forward.
//!
//! Storage is memory only. Calc-event compaction (dropping re-derivable
//! deltas past a size cap) is not implemented yet.

use crate::browser_contract::{
    CommandTarget, ContractError, apply_calculate_diff, apply_parsed, edit_from_input, input_json,
    parse_revision,
};
use crate::toolkit::{Cell, Edit, Session, Sheet, WorkbookModel};
use serde::Serialize;
use serde_json::{Map, Value as Json, json};
use std::collections::{BTreeMap, HashMap, HashSet};

/// Schema version of every event.
pub const LOG_VERSION: u64 = 1;

/// What a session was opened from, recorded in `session.opened`.
#[derive(Clone, Debug, Default, PartialEq, Serialize)]
pub struct Baseline {
    /// `"xlsx"` (imported bytes) or `"blank"` (authored workbook).
    pub kind: String,
    pub name: String,
    /// Lowercase hex sha256 of the opened bytes; `None` for a blank workbook.
    pub sha256: Option<String>,
    pub bytes: usize,
    /// The first worksheet's name, for blank workbooks.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sheet: Option<String>,
}

impl Baseline {
    pub fn xlsx(name: impl Into<String>, bytes: &[u8]) -> Self {
        Self {
            kind: "xlsx".into(),
            name: name.into(),
            sha256: Some(sha256_hex(bytes)),
            bytes: bytes.len(),
            sheet: None,
        }
    }

    pub fn blank(sheet: impl Into<String>) -> Self {
        let sheet = sheet.into();
        Self {
            kind: "blank".into(),
            name: sheet.clone(),
            sha256: None,
            bytes: 0,
            sheet: Some(sheet),
        }
    }
}

/// Which history operation a plan is for.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HistoryKind {
    Undo,
    Redo,
}

/// A validated undo/redo: the edits to apply and the event they act on.
#[derive(Clone, Debug)]
pub struct HistoryPlan {
    pub kind: HistoryKind,
    /// `undo`: the intent being undone. `redo`: the undo event being reversed.
    pub source_seq: usize,
    pub source_id: String,
    /// The original edit intent, reported as `command.target`.
    pub target_id: String,
    pub edits: Vec<Edit>,
}

/// The in-memory log plus per-actor undo/redo stacks.
#[derive(Default)]
pub struct EventLog {
    events: Vec<Json>,
    session: String,
    baseline: Option<Baseline>,
    name: Option<String>,
    /// Actor id -> seqs of applied intents that can be undone, oldest first.
    undo: HashMap<String, Vec<usize>>,
    /// Actor id -> seqs of applied undo events that can be redone.
    redo: HashMap<String, Vec<usize>>,
    /// Intents undone (and undo events redone), so they are not reused.
    consumed: HashSet<usize>,
}

fn event_id(seq: usize) -> String {
    format!("ev_{seq:04}")
}

impl EventLog {
    pub fn set_baseline(&mut self, baseline: Baseline) {
        self.baseline = Some(baseline);
    }

    pub fn events(&self) -> &[Json] {
        &self.events
    }

    pub fn len(&self) -> usize {
        self.events.len()
    }

    pub fn is_empty(&self) -> bool {
        self.events.is_empty()
    }

    /// Write `session.opened` once. Later calls are no-ops; every other
    /// record call starts the log implicitly with default engine info.
    pub fn start(&mut self, name: Option<&str>, engine: Json, revision: u64) {
        if !self.events.is_empty() {
            return;
        }
        if let Some(name) = name {
            self.name = Some(name.to_string());
        }
        let mut baseline = self
            .baseline
            .clone()
            .unwrap_or_else(|| Baseline::blank("Sheet1"));
        if let Some(name) = &self.name {
            baseline.name = name.clone();
        }
        let wall = clock::wall_ms();
        let seed = format!(
            "{}|{wall}|{}",
            baseline.sha256.as_deref().unwrap_or(&baseline.name),
            clock::mono_ms()
        );
        self.session = format!("s_{}", &sha256_hex(seed.as_bytes())[..8]);
        let mut engine_info = default_engine();
        if let Json::Object(extra) = engine {
            for (key, value) in extra {
                engine_info.insert(key, value);
            }
        }
        let mut event = self.header(0, "session", Some(wall));
        event.insert("op".into(), json!("opened"));
        event.insert("revision".into(), json!(revision.to_string()));
        event.insert("baseline".into(), json!(baseline));
        event.insert("engine".into(), Json::Object(engine_info));
        self.events.push(Json::Object(event));
    }

    fn ensure_started(&mut self, revision: u64) {
        if self.events.is_empty() {
            self.start(None, Json::Null, revision);
        }
    }

    fn header(&self, seq: usize, kind: &str, wall: Option<f64>) -> Map<String, Json> {
        let mut event = Map::new();
        event.insert("v".into(), json!(LOG_VERSION));
        event.insert("seq".into(), json!(seq));
        event.insert("id".into(), json!(event_id(seq)));
        event.insert("kind".into(), json!(kind));
        event.insert("session".into(), json!(self.session));
        if let Some(wall) = wall {
            event.insert("ts".into(), json!(clock::iso_utc(wall)));
        }
        event
    }

    /// Append an intent event and return its `(seq, id)`.
    #[allow(clippy::too_many_arguments)]
    pub fn record_intent(
        &mut self,
        actor: Json,
        command: Json,
        base: Json,
        applied: bool,
        revision: u64,
        inputs: Vec<Json>,
        error: Option<&ContractError>,
    ) -> (usize, String) {
        self.ensure_started(revision);
        let seq = self.events.len();
        let mut event = self.header(seq, "intent", Some(clock::wall_ms()));
        event.insert("mono_ms".into(), json!(round3(clock::mono_ms())));
        event.insert("actor".into(), actor);
        event.insert("command".into(), command);
        event.insert("base".into(), base);
        event.insert(
            "status".into(),
            json!(if applied { "applied" } else { "rejected" }),
        );
        event.insert("revision".into(), json!(revision.to_string()));
        event.insert("inputs".into(), Json::Array(inputs));
        event.insert(
            "error".into(),
            error.map_or(Json::Null, |error| json!(error)),
        );
        self.events.push(Json::Object(event));
        (seq, event_id(seq))
    }

    /// Append the calc event an applied intent caused.
    pub fn record_calc(
        &mut self,
        caused_by: &str,
        revision: u64,
        evaluated: usize,
        calc_ms: f64,
        deltas: Vec<Json>,
        diagnostics: Json,
    ) -> String {
        self.ensure_started(revision);
        let seq = self.events.len();
        let mut event = self.header(seq, "calc", None);
        event.insert("caused_by".into(), json!([caused_by]));
        event.insert("revision".into(), json!(revision.to_string()));
        event.insert("reason".into(), json!("inputs"));
        event.insert("evaluated".into(), json!(evaluated));
        event.insert("changed".into(), json!(deltas.len()));
        event.insert("calc_ms".into(), json!(round3(calc_ms)));
        event.insert("deltas".into(), Json::Array(deltas));
        event.insert("diagnostics".into(), diagnostics);
        self.events.push(Json::Object(event));
        event_id(seq)
    }

    /// Append `session.exported` and return the whole log as NDJSON.
    pub fn export_ndjson(&mut self, revision: u64, xlsx_sha256: Option<&str>) -> String {
        self.ensure_started(revision);
        let seq = self.events.len();
        let mut event = self.header(seq, "session", Some(clock::wall_ms()));
        event.insert("op".into(), json!("exported"));
        event.insert("revision".into(), json!(revision.to_string()));
        event.insert("xlsx_sha256".into(), json!(xlsx_sha256));
        self.events.push(Json::Object(event));
        let mut out = String::new();
        for event in &self.events {
            out.push_str(&event.to_string());
            out.push('\n');
        }
        out
    }

    /// `{"events":[...],"next_seq":N}` from `from_seq`, at most `limit` events.
    pub fn page(&self, from_seq: usize, limit: usize) -> Json {
        let start = from_seq.min(self.events.len());
        let end = start.saturating_add(limit).min(self.events.len());
        json!({"events": &self.events[start..end], "next_seq": end, "total": self.events.len()})
    }

    /// A new applied edit intent: it can be undone, and it clears the actor's
    /// redo stack.
    pub fn commit_edit(&mut self, actor_id: &str, seq: usize) {
        self.undo.entry(actor_id.to_string()).or_default().push(seq);
        self.redo.remove(actor_id);
    }

    /// An applied undo or redo intent at `seq`.
    pub fn commit_history(&mut self, actor_id: &str, plan: &HistoryPlan, seq: usize) {
        self.consumed.insert(plan.source_seq);
        let stacks = match plan.kind {
            HistoryKind::Undo => &mut self.undo,
            HistoryKind::Redo => &mut self.redo,
        };
        for stack in stacks.values_mut() {
            stack.retain(|&s| s != plan.source_seq);
        }
        let next = match plan.kind {
            HistoryKind::Undo => &mut self.redo,
            HistoryKind::Redo => &mut self.undo,
        };
        next.entry(actor_id.to_string()).or_default().push(seq);
    }

    fn seq_of(&self, id: &str) -> Option<usize> {
        let seq: usize = id.strip_prefix("ev_")?.parse().ok()?;
        (self.events.get(seq)?["id"] == id).then_some(seq)
    }

    /// Validate an undo or redo and return the edits to apply.
    ///
    /// `target` is an event id, or `None` for the actor's own latest. `read`
    /// copies the named cells' current inputs.
    pub fn plan(
        &self,
        kind: HistoryKind,
        actor_id: &str,
        target: Option<&str>,
        read: impl Fn(&[String]) -> Vec<Option<Cell>>,
    ) -> Result<HistoryPlan, ContractError> {
        let (word, stacks) = match kind {
            HistoryKind::Undo => ("undo", &self.undo),
            HistoryKind::Redo => ("redo", &self.redo),
        };
        let seq = match target {
            None => *stacks
                .get(actor_id)
                .and_then(|stack| stack.last())
                .ok_or_else(|| {
                    ContractError::new(
                        format!("nothing_to_{word}"),
                        format!("this actor has no applied intent to {word}"),
                    )
                })?,
            Some(id) => {
                let seq = self.seq_of(id).ok_or_else(|| {
                    ContractError::new("invalid_target", format!("no event {id} in this log"))
                })?;
                let event = &self.events[seq];
                let name = event["command"]["name"].as_str().unwrap_or("");
                let eligible = event["kind"] == "intent"
                    && event["status"] == "applied"
                    && match kind {
                        HistoryKind::Undo => name == "edits" || name == "redo",
                        HistoryKind::Redo => name == "undo",
                    };
                if !eligible {
                    let wanted = match kind {
                        HistoryKind::Undo => "an applied edits or redo intent",
                        HistoryKind::Redo => "an applied undo intent",
                    };
                    return Err(ContractError::new(
                        "invalid_target",
                        format!("{id} is not {wanted}"),
                    ));
                }
                if self.consumed.contains(&seq) {
                    let done = match kind {
                        HistoryKind::Undo => "undone",
                        HistoryKind::Redo => "redone",
                    };
                    return Err(ContractError::new(
                        "invalid_target",
                        format!("{id} was already {done}"),
                    ));
                }
                seq
            }
        };
        let event = &self.events[seq];
        let source_id = event_id(seq);
        let target_id = match kind {
            HistoryKind::Undo => source_id.clone(),
            HistoryKind::Redo => event["command"]["target"]
                .as_str()
                .unwrap_or(&source_id)
                .to_string(),
        };
        let inputs = event["inputs"].as_array().cloned().unwrap_or_default();
        let keys: Vec<String> = inputs.iter().map(input_key).collect();
        let current = read(&keys);
        let mut cells = Vec::new();
        let mut overwritten: Vec<String> = Vec::new();
        for ((input, key), cell) in inputs.iter().zip(&keys).zip(&current) {
            let now = input_json(cell);
            if json_eq(&now, &input["after"]) {
                continue;
            }
            let writer = self.last_writer(key, seq);
            if let Some(writer) = &writer
                && !overwritten.contains(writer)
            {
                overwritten.push(writer.clone());
            }
            cells.push(json!({
                "sheet": input["sheet"],
                "address": input["address"],
                "expected": input["after"],
                "current": now,
                "overwritten_by": writer,
            }));
        }
        if !cells.is_empty() {
            let names: Vec<String> = cells
                .iter()
                .map(|c| {
                    format!(
                        "{}!{}",
                        c["sheet"].as_str().unwrap_or(""),
                        c["address"].as_str().unwrap_or("")
                    )
                })
                .collect();
            let by = if overwritten.is_empty() {
                "a change outside the log".to_string()
            } else {
                overwritten.join(", ")
            };
            let mut error = ContractError::new(
                format!("{word}_conflict"),
                format!(
                    "cannot {word} {source_id}: {} changed since, by {by}",
                    names.join(", ")
                ),
            );
            error.details.insert("target".into(), json!(source_id));
            error.details.insert("cells".into(), Json::Array(cells));
            error
                .details
                .insert("overwritten_by".into(), json!(overwritten));
            return Err(error);
        }
        let edits = inputs
            .iter()
            .map(|input| {
                edit_from_input(
                    input["sheet"].as_str().unwrap_or(""),
                    input["address"].as_str().unwrap_or(""),
                    &input["before"],
                )
            })
            .collect::<Result<Vec<_>, _>>()?;
        Ok(HistoryPlan {
            kind,
            source_seq: seq,
            source_id,
            target_id,
            edits,
        })
    }

    /// The latest applied intent after `after_seq` that wrote `key`.
    fn last_writer(&self, key: &str, after_seq: usize) -> Option<String> {
        self.events[after_seq + 1..]
            .iter()
            .rev()
            .find(|event| {
                event["kind"] == "intent"
                    && event["status"] == "applied"
                    && event["inputs"]
                        .as_array()
                        .is_some_and(|inputs| inputs.iter().any(|i| input_key(i) == key))
            })
            .and_then(|event| event["id"].as_str().map(String::from))
    }
}

fn input_key(input: &Json) -> String {
    format!(
        "{}!{}",
        input["sheet"].as_str().unwrap_or(""),
        input["address"].as_str().unwrap_or("")
    )
}

fn default_engine() -> Map<String, Json> {
    let mut engine = Map::new();
    engine.insert("forge".into(), json!(env!("CARGO_PKG_VERSION")));
    engine.insert("commit".into(), json!(option_env!("FORGE_COMMIT")));
    engine.insert("wasm_sha256".into(), Json::Null);
    engine
}

fn round3(value: f64) -> f64 {
    (value * 1000.0).round() / 1000.0
}

/// JSON equality that compares numbers by value, so `20` (re-serialized by a
/// JavaScript host) equals `20.0` (serialized by Rust).
pub fn json_eq(a: &Json, b: &Json) -> bool {
    match (a, b) {
        (Json::Number(x), Json::Number(y)) => x.as_f64() == y.as_f64(),
        (Json::Array(x), Json::Array(y)) => {
            x.len() == y.len() && x.iter().zip(y).all(|(a, b)| json_eq(a, b))
        }
        (Json::Object(x), Json::Object(y)) => {
            x.len() == y.len()
                && x.iter()
                    .all(|(key, value)| y.get(key).is_some_and(|other| json_eq(value, other)))
        }
        _ => a == b,
    }
}

// ---- Replay ---------------------------------------------------------------

fn replay_error(code: &str, message: impl Into<String>) -> ContractError {
    ContractError::new(code, message)
}

/// Parse a log given as NDJSON or as a JSON array of events.
pub fn parse_events(text: &str) -> Result<Vec<Json>, ContractError> {
    if text.len() > 64 * crate::agent::MAX_REQUEST_BYTES {
        return Err(replay_error("invalid_request", "log exceeds 64 MiB"));
    }
    let trimmed = text.trim_start();
    if trimmed.starts_with('[') {
        return serde_json::from_str(trimmed)
            .map_err(|e| replay_error("invalid_log", e.to_string()));
    }
    text.lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| {
            serde_json::from_str(line).map_err(|e| replay_error("invalid_log", e.to_string()))
        })
        .collect()
}

/// `replay(baselineBytes, events)` as a JSON result envelope:
/// `{"ok":true,"revision","intents_applied","calcs_checked","calc_events",
/// "snapshot","values"}` or `{"ok":false,"error":{code,...}}`.
pub fn replay_json(bytes: &[u8], events_text: &str) -> String {
    match replay(bytes, events_text) {
        Ok(value) => value.to_string(),
        Err(error) => json!({"ok": false, "error": error}).to_string(),
    }
}

/// Open the baseline, reapply every applied intent in `seq` order at
/// `revision - 1`, recompute each calc event and compare it with the recorded
/// deltas. An xlsx baseline must match sha256 and length. A blank baseline
/// must have `bytes.len() == baseline.bytes` (0 for a blank open) and a known
/// sheet name. A byte or delta mismatch is `replay_mismatch`. A missing or
/// unknown blank sheet is `invalid_log`.
pub fn replay(bytes: &[u8], events_text: &str) -> Result<Json, ContractError> {
    let events = parse_events(events_text)?;
    let opened = events
        .first()
        .filter(|e| e["kind"] == "session" && e["op"] == "opened")
        .ok_or_else(|| replay_error("invalid_log", "the log must start with session.opened"))?;
    let baseline = &opened["baseline"];
    match baseline["kind"].as_str() {
        Some("xlsx") => {
            let actual = sha256_hex(bytes);
            let expected = baseline["sha256"].as_str().unwrap_or("");
            if actual != expected || baseline["bytes"].as_u64() != Some(bytes.len() as u64) {
                let mut error = replay_error(
                    "baseline_mismatch",
                    "baseline bytes do not match session.opened sha256/bytes",
                );
                error
                    .details
                    .insert("expected_sha256".into(), json!(expected));
                error.details.insert("actual_sha256".into(), json!(actual));
                error
                    .details
                    .insert("expected_bytes".into(), baseline["bytes"].clone());
                error
                    .details
                    .insert("actual_bytes".into(), json!(bytes.len()));
                return Err(error);
            }
            let workbook = crate::xlsx::import_xlsx_bytes(bytes, BTreeMap::new(), BTreeMap::new())
                .map_err(|e| replay_error("open_failed", e.to_string()))?;
            let mut summary = replay_on(&workbook, &events)?;
            summary["snapshot"] = json!(workbook.snapshot());
            summary["values"] = json!(workbook.calculate(1).values);
            Ok(summary)
        }
        Some("blank") => {
            if baseline["bytes"].as_u64() != Some(bytes.len() as u64) {
                let mut error = replay_error(
                    "replay_mismatch",
                    "baseline bytes do not match session.opened bytes",
                );
                error.details.insert("seq".into(), opened["seq"].clone());
                error.details.insert("event".into(), opened["id"].clone());
                error
                    .details
                    .insert("expected_bytes".into(), baseline["bytes"].clone());
                error
                    .details
                    .insert("actual_bytes".into(), json!(bytes.len()));
                return Err(error);
            }
            // A missing name used to be invented as "Sheet1".
            let Some(sheet) = baseline["sheet"].as_str() else {
                return Err(replay_error(
                    "invalid_log",
                    "blank baseline sheet is missing or unknown",
                ));
            };
            let mut model = WorkbookModel::default();
            model.sheets.push(Sheet::new(sheet));
            let session = Session::new(model).map_err(|error| {
                if error.code == "invalid_sheet" {
                    replay_error("invalid_log", "blank baseline sheet is missing or unknown")
                } else {
                    replay_error("open_failed", error.to_string())
                }
            })?;
            let mut summary = replay_on(&session, &events)?;
            summary["snapshot"] = json!(session.snapshot());
            summary["values"] = json!(session.calculate(1).values);
            Ok(summary)
        }
        _ => Err(replay_error("invalid_log", "unknown baseline kind")),
    }
}

/// Replay `events` onto a freshly opened target.
pub fn replay_on<W: CommandTarget + ?Sized>(
    workbook: &W,
    events: &[Json],
) -> Result<Json, ContractError> {
    let mismatch = |event: &Json, message: String| {
        let mut error = replay_error("replay_mismatch", message);
        error.details.insert("seq".into(), event["seq"].clone());
        error.details.insert("event".into(), event["id"].clone());
        error
    };
    let mut cache: Option<(u64, BTreeMap<String, crate::toolkit::CalculatedValue>)> = None;
    let mut computed: HashMap<String, (u64, Vec<Json>)> = HashMap::new();
    let mut calc_events = Vec::new();
    let mut intents = 0usize;
    for (index, event) in events.iter().enumerate() {
        if event["v"].as_u64() != Some(LOG_VERSION) {
            return Err(replay_error(
                "invalid_log",
                format!("event {index}: v must be 1"),
            ));
        }
        if event["seq"].as_u64() != Some(index as u64) {
            return Err(replay_error(
                "invalid_log",
                format!("event {index}: seq must equal its position"),
            ));
        }
        match event["kind"].as_str() {
            Some("session") => match event["op"].as_str() {
                Some("opened") if index == 0 => {}
                Some("exported") => {
                    let current = workbook.revision().to_string();
                    if event["revision"].as_str() != Some(current.as_str()) {
                        return Err(mismatch(
                            event,
                            format!(
                                "exported at r{} but replay is at r{current}",
                                event["revision"]
                            ),
                        ));
                    }
                }
                _ => {
                    return Err(replay_error(
                        "invalid_log",
                        format!("event {index}: unexpected session op"),
                    ));
                }
            },
            Some("intent") => {
                if event["status"] != "applied" {
                    continue;
                }
                let revision = parse_revision(event["revision"].as_str().unwrap_or(""))?;
                let expected = revision.saturating_sub(1);
                if revision == 0 || workbook.revision() != expected {
                    return Err(mismatch(
                        event,
                        format!(
                            "intent at r{revision} but replay is at r{}",
                            workbook.revision()
                        ),
                    ));
                }
                let edits = event["inputs"]
                    .as_array()
                    .map(|inputs| {
                        inputs
                            .iter()
                            .map(|input| {
                                edit_from_input(
                                    input["sheet"].as_str().unwrap_or(""),
                                    input["address"].as_str().unwrap_or(""),
                                    &input["after"],
                                )
                            })
                            .collect::<Result<Vec<_>, _>>()
                    })
                    .transpose()?
                    .unwrap_or_default();
                let id = event["id"].as_str().unwrap_or("").to_string();
                let has_calc = events[index + 1..]
                    .iter()
                    .take_while(|e| e["kind"] != "intent")
                    .any(|e| {
                        e["kind"] == "calc"
                            && e["caused_by"]
                                .as_array()
                                .is_some_and(|c| c.iter().any(|x| x == id.as_str()))
                    });
                if has_calc {
                    let base = match cache.take() {
                        Some((at, values)) if at == expected => values,
                        _ => workbook.calculate_report().values,
                    };
                    let applied = apply_calculate_diff(workbook, base, edits, expected).map_err(
                        |(error, _)| {
                            mismatch(
                                event,
                                format!("reapply failed: {}: {}", error.code, error.message),
                            )
                        },
                    )?;
                    if applied.revision != revision {
                        return Err(mismatch(
                            event,
                            format!(
                                "reapply reached r{} instead of r{revision}",
                                applied.revision
                            ),
                        ));
                    }
                    computed.insert(id, (revision, applied.deltas));
                    if applied.report.revision == revision && !applied.report.stale {
                        cache = Some((revision, applied.report.values));
                    }
                } else {
                    let reached = apply_parsed(workbook, edits, expected).map_err(|error| {
                        mismatch(
                            event,
                            format!("reapply failed: {}: {}", error.code, error.message),
                        )
                    })?;
                    if reached != revision {
                        return Err(mismatch(
                            event,
                            format!("reapply reached r{reached} instead of r{revision}"),
                        ));
                    }
                }
                intents += 1;
            }
            Some("calc") => {
                let cause = event["caused_by"][0].as_str().unwrap_or("");
                let Some((revision, deltas)) = computed.remove(cause) else {
                    return Err(mismatch(
                        event,
                        format!("calc event caused by {cause}, which was not reapplied"),
                    ));
                };
                if event["revision"].as_str() != Some(revision.to_string().as_str()) {
                    return Err(mismatch(event, "calc revision differs".into()));
                }
                let recorded = &event["deltas"];
                let actual = Json::Array(deltas);
                if !json_eq(recorded, &actual) {
                    let mut error = mismatch(event, "recomputed deltas differ from the log".into());
                    error.details.insert("expected".into(), recorded.clone());
                    error.details.insert("actual".into(), actual);
                    return Err(error);
                }
                calc_events.push(json!({
                    "caused_by": event["caused_by"],
                    "revision": revision.to_string(),
                    "deltas": actual,
                }));
            }
            _ => {
                return Err(replay_error(
                    "invalid_log",
                    format!("event {index}: unknown kind"),
                ));
            }
        }
    }
    Ok(json!({
        "ok": true,
        "revision": workbook.revision().to_string(),
        "intents_applied": intents,
        "calcs_checked": calc_events.len(),
        "calc_events": calc_events,
    }))
}

// ---- Clock ----------------------------------------------------------------

/// Wall clock (UTC ms) and a monotonic clock (ms). In WASM these are
/// `Date.now()` and `performance.now()` of the worker (or Node).
pub mod clock {
    #[cfg(target_arch = "wasm32")]
    mod imp {
        use wasm_bindgen::prelude::*;
        #[wasm_bindgen]
        extern "C" {
            #[wasm_bindgen(js_namespace = Date, js_name = now)]
            fn date_now() -> f64;
            #[wasm_bindgen(js_namespace = performance, js_name = now)]
            fn performance_now() -> f64;
        }
        pub fn wall_ms() -> f64 {
            date_now()
        }
        pub fn mono_ms() -> f64 {
            performance_now()
        }
    }

    #[cfg(not(target_arch = "wasm32"))]
    mod imp {
        use std::sync::OnceLock;
        use std::time::{Instant, SystemTime, UNIX_EPOCH};
        static START: OnceLock<Instant> = OnceLock::new();
        pub fn wall_ms() -> f64 {
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_secs_f64() * 1000.0)
                .unwrap_or(0.0)
        }
        pub fn mono_ms() -> f64 {
            START.get_or_init(Instant::now).elapsed().as_secs_f64() * 1000.0
        }
    }

    pub use imp::{mono_ms, wall_ms};

    /// `YYYY-MM-DDTHH:MM:SS.mmmZ` for a UTC epoch-millisecond time.
    pub fn iso_utc(ms: f64) -> String {
        let total = if ms.is_finite() && ms > 0.0 {
            ms as i64
        } else {
            0
        };
        let (secs, millis) = (total.div_euclid(1000), total.rem_euclid(1000));
        let (days, of_day) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
        // Civil-from-days (H. Hinnant), proleptic Gregorian calendar.
        let z = days + 719_468;
        let era = z.div_euclid(146_097);
        let doe = z - era * 146_097;
        let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        let mp = (5 * doy + 2) / 153;
        let day = doy - (153 * mp + 2) / 5 + 1;
        let month = if mp < 10 { mp + 3 } else { mp - 9 };
        let year = yoe + era * 400 + i64::from(month <= 2);
        format!(
            "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}.{millis:03}Z",
            of_day / 3600,
            of_day % 3600 / 60,
            of_day % 60
        )
    }
}

/// Lowercase hex SHA-256 (FIPS 180-4). Used for baseline and export hashes.
/// Formats [`crate::model::sha256`]; no crate is added for it.
pub fn sha256_hex(data: &[u8]) -> String {
    let mut hex = String::with_capacity(64);
    for byte in crate::model::sha256(data) {
        hex.push_str(&format!("{byte:02x}"));
    }
    hex
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sha256_matches_fips_vectors() {
        assert_eq!(
            sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(
            sha256_hex(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"
        );
        // Same digest `content_fingerprint` publishes, plus its prefix.
        assert_eq!(
            crate::model::content_fingerprint(b"abc"),
            format!("sha256:{}", sha256_hex(b"abc"))
        );
    }

    fn blank_opened(sheet: Option<Json>, bytes: u64) -> String {
        let mut baseline = Map::new();
        baseline.insert("kind".into(), json!("blank"));
        baseline.insert("name".into(), json!("Blank"));
        baseline.insert("sha256".into(), Json::Null);
        baseline.insert("bytes".into(), json!(bytes));
        if let Some(sheet) = sheet {
            baseline.insert("sheet".into(), sheet);
        }
        json!([{
            "v": 1,
            "seq": 0,
            "id": "ev_0000",
            "kind": "session",
            "op": "opened",
            "revision": "0",
            "baseline": baseline,
        }])
        .to_string()
    }

    #[test]
    fn blank_replay_accepts_empty_bytes_for_a_recorded_sheet() {
        let log = blank_opened(Some(json!("Ledger")), 0);
        let result = replay(b"", &log).expect("blank replay");
        assert_eq!(result["ok"], true);
        assert_eq!(result["revision"], "0");
        assert_eq!(result["intents_applied"], 0);
        assert_eq!(result["snapshot"]["sheets"][0]["name"], "Ledger");
        // The check is equality with the recorded count, not a hard-coded zero.
        let recorded = blank_opened(Some(json!("Ledger")), 4);
        let matched = replay(b"abcd", &recorded).expect("matching length");
        assert_eq!(matched["ok"], true);
        assert_eq!(matched["snapshot"]["sheets"][0]["name"], "Ledger");
    }

    #[test]
    fn blank_replay_rejects_a_different_length_and_a_missing_or_unknown_sheet() {
        let log = blank_opened(Some(json!("Sheet1")), 0);
        let error = replay(b"not-empty", &log).unwrap_err();
        assert_eq!(error.code, "replay_mismatch");
        assert_eq!(error.details["seq"], json!(0));
        assert_eq!(error.details["event"], json!("ev_0000"));
        assert_eq!(error.details["expected_bytes"], json!(0));
        assert_eq!(error.details["actual_bytes"], json!(9));

        let missing = blank_opened(None, 0);
        assert_eq!(replay(b"", &missing).unwrap_err().code, "invalid_log");
        let unknown = blank_opened(Some(json!("Bad:Name")), 0);
        assert_eq!(replay(b"", &unknown).unwrap_err().code, "invalid_log");
        let not_a_string = blank_opened(Some(Json::Null), 0);
        assert_eq!(replay(b"", &not_a_string).unwrap_err().code, "invalid_log");
    }

    #[test]
    fn iso_utc_formats_epoch_and_leap_day() {
        assert_eq!(clock::iso_utc(0.0), "1970-01-01T00:00:00.000Z");
        assert_eq!(
            clock::iso_utc(951_782_400_123.0),
            "2000-02-29T00:00:00.123Z"
        );
        assert_eq!(
            clock::iso_utc(1_761_409_200_000.0),
            "2025-10-25T16:20:00.000Z"
        );
    }

    #[test]
    fn json_eq_compares_numbers_by_value() {
        assert!(json_eq(
            &json!([["A", 20, 40.0]]),
            &json!([["A", 20.0, 40]])
        ));
        assert!(!json_eq(&json!({"value": 1}), &json!({"value": 2})));
    }
}
