//! Event log v1 (realtime design section 4): sequencing, rejected intents,
//! log-based per-actor undo/redo with the `undo_conflict` rule, and replay.
//!
//! Fixture: fixtures/realtime-chain.xlsx (examples/univer_spike_fixture.rs
//! --realtime): A1 = 10, B1 = 0 (literal), C1 = B1+5, D1 = C1*3, E1 = D1-B1,
//! F1 = SUM(A1:E1). Expected values are independent arithmetic:
//!   start:            B1..F1 = 0, 5, 15, 15, 45
//!   B1 = A1*2 (A1=10): 20, 25, 75, 55, 185
//!   A1 = 20:           40, 45, 135, 95, 335
use serde_json::{Value as Json, json};
use std::collections::BTreeMap;
use workbook_forge::browser_contract::CommandSession;
use workbook_forge::event_log::{Baseline, json_eq, replay, replay_json, sha256_hex};
use workbook_forge::xlsx::{ImportedWorkbook, import_xlsx_bytes};

fn fixture_bytes() -> Vec<u8> {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../fixtures/realtime-chain.xlsx"
    );
    std::fs::read(path).expect("fixtures/realtime-chain.xlsx")
}

fn open() -> (Vec<u8>, ImportedWorkbook, CommandSession) {
    let bytes = fixture_bytes();
    let workbook = import_xlsx_bytes(&bytes, BTreeMap::new(), BTreeMap::new()).expect("import");
    let session = CommandSession::new();
    session.set_baseline(Baseline::xlsx("realtime-chain.xlsx", &bytes));
    session.start_log(None, json!({"wasm_sha256": null}), 0);
    (bytes, workbook, session)
}

fn envelope(id: &str, actor: (&str, &str), base: &str, op: Json) -> Json {
    json!({
        "v": 1,
        "command_id": id,
        "client_id": format!("client-{}", actor.1),
        "client_seq": "1",
        "idempotency_key": null,
        "actor": {"kind": actor.0, "id": actor.1, "on_behalf_of": null},
        "base_revision": base,
        "mode": "strict",
        "preconditions": [],
        "op": op,
    })
}

const HUMAN: (&str, &str) = ("human", "ui");
const AGENT: (&str, &str) = ("agent", "mcp:claude");

fn set(address: &str, input: Json) -> Json {
    let mut edit = json!({"sheet": "Sheet1", "address": address});
    edit.as_object_mut()
        .unwrap()
        .extend(input.as_object().unwrap().clone());
    json!({"type": "set_cells", "edits": [edit]})
}

fn submit(session: &CommandSession, workbook: &ImportedWorkbook, envelope: Json) -> Json {
    let request = json!({
        "envelope": envelope,
        "command": {"source": "editor", "univer": "sheet.command.set-range-values"},
    });
    serde_json::from_str(&session.submit(workbook, &request.to_string())).unwrap()
}

fn kinds(events: &[Json]) -> Vec<String> {
    events
        .iter()
        .map(|e| {
            format!(
                "{}:{}",
                e["kind"].as_str().unwrap(),
                e["status"].as_str().unwrap_or("-")
            )
        })
        .collect()
}

fn value(workbook: &ImportedWorkbook, address: &str) -> f64 {
    let values = workbook.calculate(1).values;
    serde_json::to_value(&values[&format!("Sheet1!{address}")])
        .unwrap()
        .as_f64()
        .unwrap()
}

fn assert_json(actual: &Json, expected: &Json) {
    assert!(
        json_eq(actual, expected),
        "\n  actual: {actual}\nexpected: {expected}"
    );
}

/// The two acceptance edits: B1 = A1*2 at r0, A1 = 20 at r1.
fn two_edits(session: &CommandSession, workbook: &ImportedWorkbook) {
    let first = submit(
        session,
        workbook,
        envelope("c1", HUMAN, "0", set("B1", json!({"formula": "A1*2"}))),
    );
    assert_eq!(first["status"], "applied", "{first}");
    let second = submit(
        session,
        workbook,
        envelope("c2", HUMAN, "1", set("A1", json!({"value": 20}))),
    );
    assert_eq!(second["status"], "applied", "{second}");
}

#[test]
fn realtime_fixture_holds_a_literal_b1_and_four_dependent_formulas() {
    let (_, workbook, _) = open();
    let cells = &workbook.snapshot().sheets[0].cells;
    assert!(cells["B1"].formula.is_none());
    for (address, formula) in [
        ("C1", "B1+5"),
        ("D1", "C1*3"),
        ("E1", "D1-B1"),
        ("F1", "SUM(A1:E1)"),
    ] {
        assert_eq!(
            cells[address]
                .formula
                .as_deref()
                .unwrap()
                .trim_start_matches('='),
            formula
        );
    }
    let values: Vec<f64> = ["A1", "B1", "C1", "D1", "E1", "F1"]
        .iter()
        .map(|a| value(&workbook, a))
        .collect();
    assert_eq!(values, [10.0, 0.0, 5.0, 15.0, 15.0, 45.0]);
}

#[test]
fn log_is_sequenced_in_revision_order_with_inputs_and_calc_deltas() {
    let (bytes, workbook, session) = open();
    two_edits(&session, &workbook);
    let events = session.events();
    assert_eq!(
        kinds(&events),
        [
            "session:-",
            "intent:applied",
            "calc:-",
            "intent:applied",
            "calc:-"
        ]
    );
    for (seq, event) in events.iter().enumerate() {
        assert_eq!(event["v"], 1);
        assert_eq!(event["seq"], seq);
        assert_eq!(event["id"], format!("ev_{seq:04}"));
        assert_eq!(event["session"], events[0]["session"]);
    }
    let opened = &events[0];
    assert_eq!(opened["op"], "opened");
    assert_eq!(opened["revision"], "0");
    assert_eq!(opened["baseline"]["name"], "realtime-chain.xlsx");
    assert_eq!(opened["baseline"]["sha256"], sha256_hex(&bytes));
    assert_eq!(opened["baseline"]["bytes"], bytes.len());
    assert_eq!(opened["engine"]["forge"], env!("CARGO_PKG_VERSION"));

    let b1 = &events[1];
    assert_eq!(b1["actor"], json!({"type": "human", "id": "ui"}));
    assert_eq!(b1["command"]["name"], "edits");
    assert_eq!(b1["command"]["source"], "editor");
    assert_eq!(b1["command"]["univer"], "sheet.command.set-range-values");
    assert_eq!(
        b1["base"],
        json!({"mode": "strict", "expectedRevision": "0"})
    );
    assert_eq!(b1["revision"], "1");
    assert_eq!(b1["error"], Json::Null);
    assert!(b1["ts"].as_str().unwrap().ends_with('Z'));
    assert!(b1["mono_ms"].is_number());
    assert_json(
        &b1["inputs"],
        &json!([{"sheet": "Sheet1", "address": "B1", "before": {"value": 0}, "after": {"formula": "A1*2"}}]),
    );
    assert_eq!(events[2]["caused_by"], json!(["ev_0001"]));
    assert_eq!(events[2]["revision"], "1");
    assert_json(
        &events[2]["deltas"],
        &json!([
            ["Sheet1!B1", 0, 20],
            ["Sheet1!C1", 5, 25],
            ["Sheet1!D1", 15, 75],
            ["Sheet1!E1", 15, 55],
            ["Sheet1!F1", 45, 185]
        ]),
    );
    assert_eq!(events[2]["changed"], 5);
    assert!(events[2]["calc_ms"].as_f64().unwrap() >= 0.0);

    assert_eq!(events[3]["revision"], "2");
    assert_json(
        &events[3]["inputs"],
        &json!([{"sheet": "Sheet1", "address": "A1", "before": {"value": 10}, "after": {"value": 20}}]),
    );
    // A1 is a literal input: it is in the intent, not in the calc deltas.
    assert_json(
        &events[4]["deltas"],
        &json!([
            ["Sheet1!B1", 20, 40],
            ["Sheet1!C1", 25, 45],
            ["Sheet1!D1", 75, 135],
            ["Sheet1!E1", 55, 95],
            ["Sheet1!F1", 185, 335]
        ]),
    );

    let page = session.log_page(3, 10);
    assert_eq!(page["events"].as_array().unwrap().len(), 2);
    assert_eq!(page["next_seq"], 5);
}

#[test]
fn rejected_intents_are_logged_with_their_error_code_and_apply_nothing() {
    let (_, workbook, session) = open();
    two_edits(&session, &workbook);
    let stale = submit(
        &session,
        &workbook,
        envelope("c3", AGENT, "1", set("C1", json!({"value": 1}))),
    );
    assert_eq!(stale["status"], "rejected");
    let absent = submit(
        &session,
        &workbook,
        envelope("c4", HUMAN, "2", set("Z99", json!({"value": 1}))),
    );
    assert_eq!(absent["error"]["code"], "absent_cell");
    let mut bad = envelope("c5", HUMAN, "2", set("A1", json!({"value": 1})));
    bad["v"] = json!(2);
    let invalid = submit(&session, &workbook, bad);
    assert_eq!(invalid["error"]["code"], "invalid_request");
    assert_eq!(workbook.revision(), 2);

    let events = session.events();
    assert_eq!(
        &kinds(&events)[5..],
        ["intent:rejected", "intent:rejected", "intent:rejected"]
    );
    let conflict = &events[5];
    assert_eq!(stale["event_id"], "ev_0005");
    assert_eq!(
        conflict["actor"],
        json!({"type": "agent", "id": "mcp:claude"})
    );
    assert_eq!(conflict["revision"], "2");
    assert_json(
        &conflict["inputs"],
        &json!([{"sheet": "Sheet1", "address": "C1", "after": {"value": 1}}]),
    );
    assert_eq!(
        conflict["error"],
        json!({"code": "revision_conflict", "message": "expected revision does not match",
               "expected_revision": "1", "current_revision": "2"})
    );
    assert_eq!(events[6]["error"]["code"], "absent_cell");
    assert_eq!(events[7]["error"]["code"], "invalid_request");
    // A duplicate replays the stored response and is not logged again.
    let dup = submit(
        &session,
        &workbook,
        envelope("c2", HUMAN, "1", set("A1", json!({"value": 20}))),
    );
    assert_eq!(dup["duplicate"], true);
    assert_eq!(session.events().len(), 8);
}

#[test]
fn undo_and_redo_are_forward_intents_on_the_actors_own_history() {
    let (_, workbook, session) = open();
    two_edits(&session, &workbook);
    let undo = submit(
        &session,
        &workbook,
        envelope("u1", HUMAN, "2", json!({"type": "undo"})),
    );
    assert_eq!(undo["status"], "applied", "{undo}");
    assert_eq!(undo["revision"], "3");
    assert_eq!(value(&workbook, "A1"), 10.0);
    assert_eq!(value(&workbook, "B1"), 20.0);
    let events = session.events();
    let intent = &events[5];
    assert_eq!(intent["command"]["name"], "undo");
    assert_eq!(intent["command"]["target"], "ev_0003");
    assert_eq!(
        intent["base"],
        json!({"mode": "cells", "baseRevision": "2"})
    );
    assert_json(
        &intent["inputs"],
        &json!([{"sheet": "Sheet1", "address": "A1", "before": {"value": 20}, "after": {"value": 10}}]),
    );
    assert_json(
        &events[6]["deltas"],
        &json!([
            ["Sheet1!B1", 40, 20],
            ["Sheet1!C1", 45, 25],
            ["Sheet1!D1", 135, 75],
            ["Sheet1!E1", 95, 55],
            ["Sheet1!F1", 335, 185]
        ]),
    );

    let redo = submit(
        &session,
        &workbook,
        envelope("r1", HUMAN, "3", json!({"type": "redo"})),
    );
    assert_eq!(redo["revision"], "4");
    assert_eq!(value(&workbook, "B1"), 40.0);
    let events = session.events();
    assert_eq!(events[7]["command"]["name"], "redo");
    assert_eq!(events[7]["command"]["target"], "ev_0003");
    assert_eq!(events[7]["command"]["undo_event"], "ev_0005");

    // Undo again undoes the redo, then the B1 formula edit, then nothing.
    let again = submit(
        &session,
        &workbook,
        envelope("u2", HUMAN, "4", json!({"type": "undo"})),
    );
    assert_eq!(session.events()[9]["command"]["target"], "ev_0007");
    assert_eq!(again["revision"], "5");
    let formula = submit(
        &session,
        &workbook,
        envelope("u3", HUMAN, "5", json!({"type": "undo"})),
    );
    assert_eq!(formula["revision"], "6");
    assert_eq!(value(&workbook, "B1"), 0.0);
    assert!(workbook.snapshot().sheets[0].cells["B1"].formula.is_none());
    let none = submit(
        &session,
        &workbook,
        envelope("u4", HUMAN, "6", json!({"type": "undo"})),
    );
    assert_eq!(none["error"]["code"], "nothing_to_undo");
    assert_eq!(session.events().last().unwrap()["status"], "rejected");

    // A new edit clears the actor's redo stack.
    submit(
        &session,
        &workbook,
        envelope("c9", HUMAN, "6", set("A1", json!({"value": 7}))),
    );
    let cleared = submit(
        &session,
        &workbook,
        envelope("r2", HUMAN, "7", json!({"type": "redo"})),
    );
    assert_eq!(cleared["error"]["code"], "nothing_to_redo");
    assert_eq!(workbook.revision(), 7);
}

#[test]
fn undo_conflict_names_the_cells_and_the_overwriting_event() {
    let (_, workbook, session) = open();
    submit(
        &session,
        &workbook,
        envelope("h1", HUMAN, "0", set("A1", json!({"value": 20}))),
    ); // ev_0001
    submit(
        &session,
        &workbook,
        envelope("a1", AGENT, "1", set("A1", json!({"value": 30}))),
    ); // ev_0003
    let conflict = submit(
        &session,
        &workbook,
        envelope("hu", HUMAN, "2", json!({"type": "undo"})),
    );
    assert_eq!(conflict["status"], "rejected");
    assert_eq!(workbook.revision(), 2);
    assert_eq!(value(&workbook, "A1"), 30.0);
    let error = &conflict["error"];
    assert_eq!(error["code"], "undo_conflict");
    assert_eq!(error["target"], "ev_0001");
    assert_eq!(error["overwritten_by"], json!(["ev_0003"]));
    assert_json(
        &error["cells"],
        &json!([{"sheet": "Sheet1", "address": "A1", "expected": {"value": 20},
                 "current": {"value": 30}, "overwritten_by": "ev_0003"}]),
    );
    let logged = session.events().last().unwrap().clone();
    assert_eq!(logged["status"], "rejected");
    assert_eq!(logged["command"]["target"], "ev_0001");
    assert_eq!(logged["error"]["code"], "undo_conflict");

    // Per-actor: the agent's undo targets the agent's own edit; after that the
    // human's edit is current again and its undo applies.
    let agent_undo = submit(
        &session,
        &workbook,
        envelope("au", AGENT, "2", json!({"type": "undo"})),
    );
    assert_eq!(agent_undo["status"], "applied");
    assert_eq!(
        session.events()[agent_undo["event_id"].as_str().unwrap()[3..]
            .parse::<usize>()
            .unwrap()]["command"]["target"],
        "ev_0003"
    );
    assert_eq!(value(&workbook, "A1"), 20.0);
    let human_undo = submit(
        &session,
        &workbook,
        envelope("hu2", HUMAN, "3", json!({"type": "undo"})),
    );
    assert_eq!(human_undo["status"], "applied", "{human_undo}");
    assert_eq!(value(&workbook, "A1"), 10.0);
    assert_eq!(workbook.revision(), 4);

    // An explicit target that is not an applied edit is refused.
    let bad = submit(
        &session,
        &workbook,
        envelope(
            "x",
            HUMAN,
            "4",
            json!({"type": "undo", "target": "ev_0002"}),
        ),
    );
    assert_eq!(bad["error"]["code"], "invalid_target");
}

/// A session with edits, a rejected intent, a legacy api apply, undo and redo.
fn mixed_session() -> (Vec<u8>, ImportedWorkbook, CommandSession) {
    let (bytes, workbook, session) = open();
    two_edits(&session, &workbook);
    submit(
        &session,
        &workbook,
        envelope("s", AGENT, "1", set("C1", json!({"value": 1}))),
    );
    let legacy = session.apply_edits_logged(
        &workbook,
        r#"[{"sheet":"Sheet1","address":"C1","formula":"=B1+100"}]"#,
        "2",
    );
    assert_eq!(legacy, r#"{"ok":true,"revision":"3"}"#);
    submit(
        &session,
        &workbook,
        envelope("a", AGENT, "3", set("A1", json!({"value": 5}))),
    );
    submit(
        &session,
        &workbook,
        envelope("u", HUMAN, "4", json!({"type": "undo"})),
    ); // conflict
    submit(
        &session,
        &workbook,
        envelope("au", AGENT, "4", json!({"type": "undo"})),
    );
    submit(
        &session,
        &workbook,
        envelope("ar", AGENT, "5", json!({"type": "redo"})),
    );
    (bytes, workbook, session)
}

#[test]
fn replay_reproduces_the_final_state_and_every_recorded_delta() {
    let (bytes, workbook, session) = mixed_session();
    assert_eq!(workbook.revision(), 6);
    let ndjson = session.export_log_ndjson(workbook.revision(), Some("abc"));
    let events: Vec<Json> = ndjson
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    assert_eq!(events.last().unwrap()["op"], "exported");
    assert_eq!(events.last().unwrap()["revision"], "6");
    let recorded: Vec<&Json> = events.iter().filter(|e| e["kind"] == "calc").collect();

    let result = replay(&bytes, &ndjson).expect("replay");
    assert_eq!(result["ok"], true);
    assert_eq!(result["revision"], "6");
    assert_eq!(result["intents_applied"], 6);
    assert_eq!(result["calcs_checked"], recorded.len());
    for (replayed, logged) in result["calc_events"]
        .as_array()
        .unwrap()
        .iter()
        .zip(&recorded)
    {
        assert_json(&replayed["deltas"], &logged["deltas"]);
    }
    assert_eq!(result["snapshot"], json!(workbook.snapshot()));
    assert_eq!(result["values"], json!(workbook.calculate(1).values));
    // Deterministic: a second replay (and a JSON-array log that a JavaScript
    // host re-serialized, turning 20.0 into 20) gives the same answer.
    let array = serde_json::to_string(&events)
        .unwrap()
        .replace(".0,", ",")
        .replace(".0]", "]");
    let again = replay(&bytes, &array).expect("replay array");
    assert_eq!(again["calc_events"], result["calc_events"]);
    assert_eq!(again["snapshot"], result["snapshot"]);
}

#[test]
fn replay_rejects_a_changed_baseline_and_a_tampered_delta() {
    let (bytes, workbook, session) = mixed_session();
    let ndjson = session.export_log_ndjson(workbook.revision(), None);

    let mut other = bytes.clone();
    other.push(0);
    let wrong: Json = serde_json::from_str(&replay_json(&other, &ndjson)).unwrap();
    assert_eq!(wrong["ok"], false);
    assert_eq!(wrong["error"]["code"], "baseline_mismatch");

    let mut events: Vec<Json> = ndjson
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    let calc = events.iter_mut().find(|e| e["kind"] == "calc").unwrap();
    calc["deltas"][4][2] = json!(186);
    let tampered = events
        .iter()
        .map(Json::to_string)
        .collect::<Vec<_>>()
        .join("\n");
    let error = replay(&bytes, &tampered).unwrap_err();
    assert_eq!(error.code, "replay_mismatch");
    assert_eq!(error.details["event"], "ev_0002");

    let mut events: Vec<Json> = ndjson
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    events.remove(1);
    let gap = events
        .iter()
        .map(Json::to_string)
        .collect::<Vec<_>>()
        .join("\n");
    assert_eq!(replay(&bytes, &gap).unwrap_err().code, "invalid_log");
}
