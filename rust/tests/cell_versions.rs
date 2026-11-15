//! Per-cell versions and per-cell preconditions on the command envelope
//! (docs/cell-versions.md).
//!
//! A cell's version is the revision of the last applied edit that wrote its
//! input (set_cells, undo, redo, the legacy apply). Recalculation never bumps
//! it. `mode: "cells"` applies a command whose base revision is behind the
//! workbook unless a precondition cell was written after its `base_version`;
//! `mode: "strict"` stays whole-workbook compare-and-swap.
//!
//! Fixture: fixtures/realtime-chain.xlsx: A1 = 10, B1 = 0 (literals),
//! C1 = B1+5, D1 = C1*3, E1 = D1-B1, F1 = SUM(A1:E1).
use serde_json::{Value as Json, json};
use std::collections::BTreeMap;
use workbook_forge::browser_contract::{CommandSession, CommandTarget, cell_revisions_json};
use workbook_forge::event_log::{Baseline, replay};
use workbook_forge::toolkit::{Cell, CellValue, Session, Sheet, WorkbookModel};
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

const ALICE: &str = "alice";
const AGENT: &str = "agent-1";

fn envelope(id: &str, actor: &str, mode: &str, base: &str, preconditions: Json, op: Json) -> Json {
    let kind = if actor.starts_with("agent") {
        "agent"
    } else {
        "human"
    };
    json!({
        "v": 1,
        "command_id": id,
        "client_id": format!("client-{actor}"),
        "client_seq": "1",
        "idempotency_key": null,
        "actor": {"kind": kind, "id": actor, "on_behalf_of": null},
        "base_revision": base,
        "mode": mode,
        "preconditions": preconditions,
        "op": op,
    })
}

fn set(address: &str, value: f64) -> Json {
    json!({"type": "set_cells", "edits": [{"sheet": "Sheet1", "address": address, "value": value}]})
}

fn pre(address: &str, base_version: &str) -> Json {
    json!({"sheet": "Sheet1", "address": address, "base_version": base_version})
}

fn run<W: CommandTarget + ?Sized>(session: &CommandSession, workbook: &W, envelope: Json) -> Json {
    serde_json::from_str(&session.apply_and_calculate(workbook, &envelope.to_string())).unwrap()
}

fn version<W: CommandTarget + ?Sized>(workbook: &W, address: &str) -> u64 {
    workbook.cell_revisions(&[format!("Sheet1!{address}")]).1[0]
}

fn changed_version(response: &Json, address: &str) -> Json {
    response["changed"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["address"] == address)
        .unwrap_or_else(|| panic!("{address} not in changed: {response:#}"))["cell_revision"]
        .clone()
}

fn number(workbook: &ImportedWorkbook, address: &str) -> f64 {
    let key = format!("Sheet1!{address}");
    match &workbook.read_cells(&[key])[0] {
        Some(cell) => match cell.value {
            CellValue::Number(n) => n,
            ref other => panic!("{address} is {other:?}"),
        },
        None => panic!("{address} absent"),
    }
}

#[test]
fn every_cell_starts_at_the_opening_revision_and_an_edit_bumps_only_its_targets() {
    let (_, workbook, session) = open();
    assert_eq!(version(&workbook, "A1"), 0);
    assert_eq!(
        version(&workbook, "Z99"),
        0,
        "an absent cell has a version too"
    );
    assert_eq!(cell_revisions_json(&workbook), json!({}));
    let r = run(
        &session,
        &workbook,
        envelope("c1", ALICE, "strict", "0", json!([]), set("B1", 1.0)),
    );
    assert_eq!(r["status"], "applied", "{r:#}");
    assert_eq!(r["revision"], "1");
    assert_eq!(changed_version(&r, "B1"), "1");
    // C1..F1 recalculated, but nothing wrote them.
    for address in ["C1", "D1", "E1", "F1"] {
        assert_eq!(changed_version(&r, address), "0", "{address}");
        assert_eq!(version(&workbook, address), 0, "{address}");
    }
    assert_eq!(version(&workbook, "A1"), 0);
    assert_eq!(
        cell_revisions_json(&workbook),
        json!({"Sheet1": {"B1": "1"}})
    );
}

#[test]
fn strict_mode_is_unchanged_a_disjoint_edit_is_still_a_whole_workbook_conflict() {
    let (_, workbook, session) = open();
    run(
        &session,
        &workbook,
        envelope("b", AGENT, "strict", "0", json!([]), set("B1", 1.0)),
    );
    let stale = run(
        &session,
        &workbook,
        envelope("a", ALICE, "strict", "0", json!([]), set("A1", 5.0)),
    );
    assert_eq!(stale["status"], "rejected");
    assert_eq!(stale["error"]["code"], "revision_conflict");
    assert_eq!(stale["error"]["expected_revision"], "0");
    assert_eq!(stale["error"]["current_revision"], "1");
    assert!(stale["error"].get("stale_cells").is_none());
    assert_eq!(number(&workbook, "A1"), 10.0);
}

#[test]
fn cells_mode_accepts_a_disjoint_edit_after_the_workbook_moved() {
    let (_, workbook, session) = open();
    // Alice read the workbook at r0. The agent then writes B1 (r1).
    run(
        &session,
        &workbook,
        envelope("b", AGENT, "strict", "0", json!([]), set("B1", 1.0)),
    );
    let r = run(
        &session,
        &workbook,
        envelope(
            "a",
            ALICE,
            "cells",
            "0",
            json!([pre("A1", "0")]),
            set("A1", 5.0),
        ),
    );
    assert_eq!(r["status"], "applied", "{r:#}");
    assert_eq!(r["previous_revision"], "1");
    assert_eq!(r["revision"], "2");
    assert_eq!(changed_version(&r, "A1"), "2");
    assert_eq!(number(&workbook, "A1"), 5.0);
    assert_eq!(number(&workbook, "B1"), 1.0, "the agent's write survives");
    // F1 = SUM(A1:E1) with A1 = 5, B1 = 1, C1 = 6, D1 = 18, E1 = 17.
    let f1 = r["changed"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["address"] == "F1")
        .unwrap();
    assert_eq!(f1["value"].as_f64(), Some(47.0));
    // The log records the mode and the preconditions.
    let events = session.events();
    let intent = events.iter().rfind(|e| e["kind"] == "intent").unwrap();
    assert_eq!(intent["base"]["mode"], "cells");
    assert_eq!(intent["base"]["baseRevision"], "0");
    assert_eq!(intent["base"]["preconditions"], json!([pre("A1", "0")]));
    // A blind write (no preconditions) is last-write-wins by client policy.
    let blind = run(
        &session,
        &workbook,
        envelope("c", AGENT, "cells", "0", json!([]), set("A1", 6.0)),
    );
    assert_eq!(blind["status"], "applied", "{blind:#}");
    assert_eq!(number(&workbook, "A1"), 6.0);
}

#[test]
fn cells_mode_refuses_an_overlapping_edit_and_names_the_stale_cells() {
    let (_, workbook, session) = open();
    let agent = run(
        &session,
        &workbook,
        envelope("b", AGENT, "strict", "0", json!([]), set("A1", 1.0)),
    );
    let writer = agent["event_id"].clone();
    let r = run(
        &session,
        &workbook,
        envelope(
            "a",
            ALICE,
            "cells",
            "0",
            json!([pre("A1", "0"), pre("B1", "0")]),
            json!({"type": "set_cells", "edits": [
                {"sheet": "Sheet1", "address": "A1", "value": 5.0},
                {"sheet": "Sheet1", "address": "B1", "value": 2.0},
            ]}),
        ),
    );
    assert_eq!(r["status"], "rejected", "{r:#}");
    assert_eq!(r["error"]["code"], "revision_conflict");
    assert_eq!(r["error"]["expected_revision"], "0");
    assert_eq!(r["error"]["current_revision"], "1");
    assert_eq!(
        r["error"]["stale_cells"],
        json!([{"sheet": "Sheet1", "address": "A1", "base_version": "0",
                "cell_revision": "1", "overwritten_by": writer}])
    );
    // Nothing applied: not even the fresh B1.
    assert_eq!(r["revision"], "1");
    assert_eq!(number(&workbook, "A1"), 1.0);
    assert_eq!(number(&workbook, "B1"), 0.0);
    // The client rebases on the cell's current version and succeeds.
    let retry = run(
        &session,
        &workbook,
        envelope(
            "a2",
            ALICE,
            "cells",
            "1",
            json!([pre("A1", "1")]),
            set("A1", 5.0),
        ),
    );
    assert_eq!(retry["status"], "applied", "{retry:#}");
    assert_eq!(number(&workbook, "A1"), 5.0);
}

#[test]
fn strict_mode_checks_preconditions_too() {
    let (_, workbook, session) = open();
    run(
        &session,
        &workbook,
        envelope("b", AGENT, "strict", "0", json!([]), set("A1", 1.0)),
    );
    // Alice holds A1 at version 0 from an older read but a newer snapshot
    // revision: the cell moved in between.
    let r = run(
        &session,
        &workbook,
        envelope(
            "a",
            ALICE,
            "strict",
            "1",
            json!([pre("A1", "0")]),
            set("A1", 5.0),
        ),
    );
    assert_eq!(r["error"]["code"], "revision_conflict", "{r:#}");
    assert_eq!(r["error"]["stale_cells"][0]["address"], "A1");
    let ok = run(
        &session,
        &workbook,
        envelope(
            "a2",
            ALICE,
            "strict",
            "1",
            json!([pre("A1", "1")]),
            set("A1", 5.0),
        ),
    );
    assert_eq!(ok["status"], "applied", "{ok:#}");
}

#[test]
fn undo_and_redo_bump_the_version_of_the_cells_they_write() {
    let (_, workbook, session) = open();
    let edit = run(
        &session,
        &workbook,
        envelope("e", ALICE, "strict", "0", json!([]), set("A1", 5.0)),
    );
    assert_eq!(edit["status"], "applied");
    assert_eq!(version(&workbook, "A1"), 1);
    let undo = run(
        &session,
        &workbook,
        envelope(
            "u",
            ALICE,
            "strict",
            "1",
            json!([]),
            json!({"type": "undo"}),
        ),
    );
    assert_eq!(undo["status"], "applied", "{undo:#}");
    assert_eq!(number(&workbook, "A1"), 10.0);
    assert_eq!(version(&workbook, "A1"), 2);
    assert_eq!(changed_version(&undo, "A1"), "2");
    // A client holding A1 at version 1 now conflicts.
    let stale = run(
        &session,
        &workbook,
        envelope(
            "x",
            AGENT,
            "cells",
            "1",
            json!([pre("A1", "1")]),
            set("A1", 7.0),
        ),
    );
    assert_eq!(stale["error"]["code"], "revision_conflict");
    assert_eq!(stale["error"]["stale_cells"][0]["cell_revision"], "2");
    assert_eq!(
        stale["error"]["stale_cells"][0]["overwritten_by"],
        undo["event_id"]
    );
    let redo = run(
        &session,
        &workbook,
        envelope(
            "r",
            ALICE,
            "strict",
            "2",
            json!([]),
            json!({"type": "redo"}),
        ),
    );
    assert_eq!(redo["status"], "applied", "{redo:#}");
    assert_eq!(number(&workbook, "A1"), 5.0);
    assert_eq!(version(&workbook, "A1"), 3);
    assert_eq!(changed_version(&redo, "A1"), "3");
    // Untouched cells keep the opening revision.
    assert_eq!(version(&workbook, "B1"), 0);
}

#[test]
fn undo_and_redo_refuse_preconditions() {
    let (_, workbook, session) = open();
    run(
        &session,
        &workbook,
        envelope("e", ALICE, "strict", "0", json!([]), set("A1", 5.0)),
    );
    let r = run(
        &session,
        &workbook,
        envelope(
            "u",
            ALICE,
            "strict",
            "1",
            json!([pre("A1", "1")]),
            json!({"type": "undo"}),
        ),
    );
    assert_eq!(r["error"]["code"], "unsupported_precondition", "{r:#}");
    assert_eq!(number(&workbook, "A1"), 5.0);
}

#[test]
fn the_legacy_apply_bumps_versions_as_well() {
    let (_, workbook, session) = open();
    let ok = session.apply_edits_logged(
        &workbook,
        &json!([{"sheet": "Sheet1", "address": "B1", "value": 3.0}]).to_string(),
        "0",
    );
    assert_eq!(serde_json::from_str::<Json>(&ok).unwrap()["ok"], true);
    assert_eq!(version(&workbook, "B1"), 1);
    let r = run(
        &session,
        &workbook,
        envelope(
            "a",
            ALICE,
            "cells",
            "0",
            json!([pre("B1", "0")]),
            set("B1", 4.0),
        ),
    );
    assert_eq!(r["error"]["code"], "revision_conflict");
    assert_eq!(r["error"]["stale_cells"][0]["overwritten_by"], "ev_0001");
}

/// A1 = RAND(), A2 = TODAY(), A3 = C1*2, C1 = 1 in an authored session.
fn volatile_session() -> Session {
    let mut sheet = Sheet::new("Sheet1");
    sheet.cells.insert("A1".into(), Cell::formula("=RAND()"));
    sheet.cells.insert("A2".into(), Cell::formula("=TODAY()"));
    sheet.cells.insert("A3".into(), Cell::formula("=C1*2"));
    sheet
        .cells
        .insert("C1".into(), Cell::value(CellValue::Number(1.0)));
    Session::new(WorkbookModel {
        sheets: vec![sheet],
        ..WorkbookModel::default()
    })
    .expect("session")
}

#[test]
fn recalculated_values_never_bump_a_version() {
    let workbook = volatile_session();
    let session = CommandSession::new();
    let first = workbook.calculate(1);
    let r = run(
        &session,
        &workbook,
        envelope("c", ALICE, "strict", "0", json!([]), set("C1", 2.0)),
    );
    assert_eq!(r["status"], "applied", "{r:#}");
    // RAND re-rolled and A3 doubled, but only C1 was written.
    let second = workbook.calculate(1);
    assert_ne!(first.values["Sheet1!A1"], second.values["Sheet1!A1"]);
    assert_eq!(changed_version(&r, "A3"), "0");
    for address in ["A1", "A2", "A3"] {
        assert_eq!(version(&workbook, address), 0, "{address}");
    }
    assert_eq!(version(&workbook, "C1"), 1);
    // Several more recalculations change nothing either.
    for _ in 0..3 {
        workbook.calculate(1);
    }
    assert_eq!(
        cell_revisions_json(&workbook),
        json!({"Sheet1": {"C1": "1"}})
    );
    // So a client holding the volatile cells at version 0 is not refused.
    let ok = run(
        &session,
        &workbook,
        envelope(
            "d",
            AGENT,
            "cells",
            "0",
            json!([pre("A1", "0"), pre("A2", "0"), pre("A3", "0")]),
            json!({"type": "set_cells", "edits": [{"sheet": "Sheet1", "address": "A3", "formula": "C1*3"}]}),
        ),
    );
    assert_eq!(ok["status"], "applied", "{ok:#}");
    assert_eq!(version(&workbook, "A3"), 2);
}

#[test]
fn invalid_preconditions_fail_closed() {
    let (_, workbook, session) = open();
    run(
        &session,
        &workbook,
        envelope("b", AGENT, "strict", "0", json!([]), set("B1", 1.0)),
    );
    let cases: Vec<(&str, &str, Json, &str)> = vec![
        // base_version ahead of the workbook (r1)
        (
            "ahead",
            "cells",
            json!([pre("A1", "5")]),
            "invalid_revision",
        ),
        ("bad", "cells", json!([pre("A1", "x")]), "invalid_revision"),
        (
            "dup",
            "cells",
            json!([pre("A1", "0"), pre("a1", "0")]),
            "invalid_request",
        ),
        (
            "sheet",
            "cells",
            json!([{"sheet": "Nope", "address": "A1", "base_version": "0"}]),
            "invalid_request",
        ),
        // The pre-implementation draft shape is not accepted.
        (
            "draft",
            "cells",
            json!([{"cell": "Sheet1!A1", "unchanged_since": "0"}]),
            "invalid_request",
        ),
        ("mode", "loose", json!([]), "invalid_request"),
    ];
    for (id, mode, preconditions, code) in cases {
        let r = run(
            &session,
            &workbook,
            envelope(id, ALICE, mode, "1", preconditions, set("A1", 9.0)),
        );
        assert_eq!(r["status"], "rejected", "{id}: {r:#}");
        assert_eq!(r["error"]["code"], code, "{id}: {r:#}");
    }
    // A base revision from the future is a conflict, as in strict mode.
    let future = run(
        &session,
        &workbook,
        envelope("f", ALICE, "cells", "9", json!([]), set("A1", 9.0)),
    );
    assert_eq!(future["error"]["code"], "revision_conflict");
    assert_eq!(number(&workbook, "A1"), 10.0);
    assert_eq!(workbook.revision(), 1);
}

#[test]
fn replay_rebuilds_identical_cell_versions() {
    let (bytes, workbook, session) = open();
    let steps = [
        envelope("1", AGENT, "strict", "0", json!([]), set("B1", 1.0)),
        envelope(
            "2",
            ALICE,
            "cells",
            "0",
            json!([pre("A1", "0")]),
            set("A1", 5.0),
        ),
        envelope(
            "3",
            ALICE,
            "cells",
            "0",
            json!([pre("A1", "0")]),
            set("A1", 6.0),
        ), // refused
        envelope(
            "4",
            ALICE,
            "strict",
            "2",
            json!([]),
            json!({"type": "undo"}),
        ),
        envelope(
            "5",
            AGENT,
            "cells",
            "1",
            json!([pre("C1", "0")]),
            json!({"type": "set_cells", "edits": [
                {"sheet": "Sheet1", "address": "C1", "formula": "B1+6"},
                {"sheet": "Sheet1", "address": "H5", "value": 3.0},
            ]}),
        ),
        envelope(
            "6",
            ALICE,
            "strict",
            "4",
            json!([]),
            json!({"type": "redo"}),
        ),
    ];
    let statuses: Vec<Json> = steps
        .into_iter()
        .map(|step| run(&session, &workbook, step)["status"].clone())
        .collect();
    assert_eq!(
        statuses,
        vec![
            "applied", "applied", "rejected", "applied", "applied", "applied"
        ]
    );
    let live = cell_revisions_json(&workbook);
    assert_eq!(
        live,
        json!({"Sheet1": {"A1": "5", "B1": "1", "C1": "4", "H5": "4"}})
    );
    let ndjson = session.export_log_ndjson(workbook.revision(), None);
    let first = replay(&bytes, &ndjson).expect("replay");
    assert_eq!(first["cell_revisions"], live);
    let second = replay(&bytes, &ndjson).expect("replay again");
    assert_eq!(second["cell_revisions"], live);
    assert_eq!(second["snapshot"], first["snapshot"]);
}
