//! The command-envelope entry (`CommandSession::apply_and_calculate`): strict
//! compare-and-swap, dedupe, delta == diff of full calculations, fail-closed
//! modes, and envelope validation. Expected values are independent arithmetic
//! on a chain workbook (A1 = 1, An = A(n-1) + 1), never engine output.
use serde_json::{Value as Json, json};
use std::collections::BTreeMap;
use workbook_forge::browser_contract::{
    CommandSession, DEDUPE_CAPACITY, apply_edits_envelope, revision_string,
};
use workbook_forge::toolkit::{Cell, CellValue, Sheet, WorkbookModel, calculate};
use workbook_forge::xlsx::{ImportedWorkbook, export_xlsx_bytes, import_xlsx_bytes};

const CHAIN: usize = 30;

/// A1 = 1, A2 = A1+1, ..., A30 = A29+1, plus B1 = 7 (an unrelated literal).
fn chain() -> ImportedWorkbook {
    let mut sheet = Sheet::new("Sheet1");
    sheet
        .cells
        .insert("A1".into(), Cell::value(CellValue::Number(1.0)));
    for row in 2..=CHAIN {
        sheet
            .cells
            .insert(format!("A{row}"), Cell::formula(format!("=A{}+1", row - 1)));
    }
    sheet
        .cells
        .insert("B1".into(), Cell::value(CellValue::Number(7.0)));
    let model = WorkbookModel {
        sheets: vec![sheet],
        ..WorkbookModel::default()
    };
    let bytes = export_xlsx_bytes(&model).expect("export");
    import_xlsx_bytes(&bytes, BTreeMap::new(), BTreeMap::new()).expect("import")
}

fn envelope(command_id: &str, base: &str, edits: Json) -> Json {
    json!({
        "v": 1,
        "command_id": command_id,
        "client_id": "tab-1",
        "client_seq": "1",
        "idempotency_key": null,
        "actor": {"kind": "human", "id": "user-1", "on_behalf_of": null},
        "base_revision": base,
        "mode": "strict",
        "preconditions": [],
        "op": {"type": "set_cells", "edits": edits},
    })
}

fn set(address: &str, value: f64) -> Json {
    json!([{"sheet": "Sheet1", "address": address, "value": value}])
}

fn run(session: &CommandSession, workbook: &ImportedWorkbook, request: &Json) -> Json {
    serde_json::from_str(&session.apply_and_calculate(workbook, &request.to_string())).unwrap()
}

/// Every cell's value from a fresh, non-incremental full calculation of a
/// model snapshot, plus literal inputs the report may omit.
fn full_values(workbook: &ImportedWorkbook) -> BTreeMap<String, Json> {
    let model = workbook.snapshot();
    let report = serde_json::to_value(calculate(&model, 1)).unwrap();
    let mut out: BTreeMap<String, Json> = report["values"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    for sheet in &model.sheets {
        for (address, cell) in &sheet.cells {
            out.entry(format!("{}!{address}", sheet.name))
                .or_insert_with(|| serde_json::to_value(&cell.value).unwrap());
        }
    }
    out
}

fn diff(before: &BTreeMap<String, Json>, after: &BTreeMap<String, Json>) -> BTreeMap<String, Json> {
    after
        .iter()
        .filter(|(k, v)| before.get(*k) != Some(*v))
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect()
}

fn changed_map(response: &Json) -> BTreeMap<String, Json> {
    response["changed"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| {
            let key = format!(
                "{}!{}",
                c["sheet"].as_str().unwrap(),
                c["address"].as_str().unwrap()
            );
            (key, c["value"].clone())
        })
        .collect()
}

#[test]
fn strict_mode_rejects_a_stale_base_and_applies_nothing() {
    let workbook = chain();
    let session = CommandSession::new();
    let ok = run(&session, &workbook, &envelope("c1", "0", set("A1", 5.0)));
    assert_eq!(ok["status"], "applied");
    assert_eq!(ok["revision"], "1");
    assert_eq!(ok["previous_revision"], "0");
    let stale = run(&session, &workbook, &envelope("c2", "0", set("A1", 9.0)));
    assert_eq!(stale["status"], "rejected");
    assert_eq!(stale["command_id"], "c2");
    assert_eq!(stale["error"]["code"], "revision_conflict");
    assert_eq!(stale["error"]["expected_revision"], "0");
    assert_eq!(stale["error"]["current_revision"], "1");
    assert_eq!(stale["revision"], "1");
    assert_eq!(stale["changed"], json!([]));
    assert_eq!(revision_string(&workbook), "1");
    assert_eq!(full_values(&workbook)["Sheet1!A1"], json!(5.0));
    assert_eq!(full_values(&workbook)["Sheet1!A30"], json!(34.0));
}

#[test]
fn duplicate_command_id_returns_the_original_response_and_applies_nothing() {
    let workbook = chain();
    let session = CommandSession::new();
    let request = envelope("same-id", "0", set("A1", 5.0));
    let first = run(&session, &workbook, &request);
    assert_eq!(first["status"], "applied");
    assert_eq!(first["duplicate"], false);
    let again = run(&session, &workbook, &request);
    assert_eq!(again["duplicate"], true);
    let mut stripped = again.clone();
    stripped["duplicate"] = json!(false);
    assert_eq!(stripped, first);
    assert_eq!(revision_string(&workbook), "1");
    // The same id with another payload at the now-current base is still a replay.
    let reused = run(
        &session,
        &workbook,
        &envelope("same-id", "1", set("A1", 99.0)),
    );
    assert_eq!(reused["duplicate"], true);
    assert_eq!(reused["revision"], "1");
    assert_eq!(revision_string(&workbook), "1");
    assert_eq!(full_values(&workbook)["Sheet1!A1"], json!(5.0));

    // An agent retry under a new command_id with the same idempotency key.
    let mut agent = envelope("agent-a", "1", set("B1", 8.0));
    agent["actor"] = json!({"kind": "agent", "id": "agent-7", "on_behalf_of": "user-1"});
    agent["idempotency_key"] = json!("run-42/step-3");
    assert_eq!(run(&session, &workbook, &agent)["status"], "applied");
    agent["command_id"] = json!("agent-b");
    let retry = run(&session, &workbook, &agent);
    assert_eq!(retry["duplicate"], true);
    assert_eq!(retry["command_id"], "agent-a");
    assert_eq!(revision_string(&workbook), "2");
    // Same key, different actor: a different scope, so it is processed.
    agent["command_id"] = json!("agent-c");
    agent["actor"]["id"] = json!("agent-8");
    agent["base_revision"] = json!("2");
    assert_eq!(run(&session, &workbook, &agent)["duplicate"], false);
}

#[test]
fn dedupe_memory_is_bounded() {
    let workbook = chain();
    let session = CommandSession::new();
    // Rejected commands are consumed and remembered too; use them to fill.
    for i in 0..=DEDUPE_CAPACITY {
        let stale = run(
            &session,
            &workbook,
            &envelope(&format!("r{i}"), "9", json!([])),
        );
        assert_eq!(stale["error"]["code"], "revision_conflict");
    }
    // r0 was evicted, so replaying it is processed afresh.
    let replay = run(&session, &workbook, &envelope("r0", "0", set("A1", 2.0)));
    assert_eq!(replay["duplicate"], false);
    assert_eq!(replay["status"], "applied");
    let recent = run(
        &session,
        &workbook,
        &envelope(&format!("r{DEDUPE_CAPACITY}"), "1", json!([])),
    );
    assert_eq!(recent["duplicate"], true);
}

#[test]
fn changed_is_exactly_the_diff_of_full_calculations_on_a_chain() {
    let workbook = chain();
    let session = CommandSession::new();
    let steps: Vec<(Json, usize)> = vec![
        // Head edit: A1 and all 29 dependents change.
        (set("A1", 10.0), CHAIN),
        // Tail formula edit: only A30 changes.
        (
            json!([{"sheet": "Sheet1", "address": "A30", "formula": "=A29+100"}]),
            1,
        ),
        // Same value, new formula text: an input-only change, one cell.
        (
            json!([{"sheet": "Sheet1", "address": "A16", "formula": "=A15+1+0"}]),
            1,
        ),
        // Same literal again: nothing changes.
        (set("B1", 7.0), 0),
        // Unrelated literal: one cell.
        (set("B1", 8.0), 1),
        // Mid-chain literal over a formula: A20 and its 10 dependents.
        (set("A20", 0.0), 11),
    ];
    for (i, (edits, expected_len)) in steps.into_iter().enumerate() {
        let before = full_values(&workbook);
        let base = revision_string(&workbook);
        let response = run(
            &session,
            &workbook,
            &envelope(&format!("c{i}"), &base, edits),
        );
        assert_eq!(response["status"], "applied", "step {i}: {response}");
        let after = full_values(&workbook);
        let mut expected = diff(&before, &after);
        if i == 2 {
            expected.insert("Sheet1!A16".into(), after["Sheet1!A16"].clone());
        }
        let changed = changed_map(&response);
        assert_eq!(changed, expected, "step {i}");
        assert_eq!(changed.len(), expected_len, "step {i}");
    }
    let last = full_values(&workbook);
    // A1=10 .. A19=28, A20=0, A29=9, A30=A29+100=109.
    assert_eq!(last["Sheet1!A19"], json!(28.0));
    assert_eq!(last["Sheet1!A29"], json!(9.0));
    assert_eq!(last["Sheet1!A30"], json!(109.0));
    // Formulas cross the boundary without the leading "=".
    let base = revision_string(&workbook);
    let response = run(
        &session,
        &workbook,
        &envelope(
            "f",
            &base,
            json!([{"sheet": "Sheet1", "address": "A2", "formula": "A1+2"}]),
        ),
    );
    let cells = response["changed"].as_array().unwrap();
    let a2 = cells.iter().find(|c| c["address"] == "A2").unwrap();
    assert_eq!(a2["formula"], "A1+2");
    assert_eq!(a2["value"], json!(12.0));
    let a1_untouched = cells.iter().all(|c| c["address"] != "A1");
    assert!(a1_untouched);
}

#[test]
fn changed_stays_correct_after_an_interleaved_legacy_apply() {
    let workbook = chain();
    let session = CommandSession::new();
    run(&session, &workbook, &envelope("a", "0", set("A1", 2.0)));
    // The legacy entry moves the revision outside the session.
    apply_edits_envelope(
        &workbook,
        r#"[{"sheet":"Sheet1","address":"A1","value":3}]"#,
        "1",
    );
    let before = full_values(&workbook);
    let response = run(&session, &workbook, &envelope("b", "2", set("A29", 0.0)));
    assert_eq!(
        changed_map(&response),
        diff(&before, &full_values(&workbook))
    );
    assert_eq!(changed_map(&response).len(), 2); // A29 and A30
}

#[test]
fn rebase_mode_and_preconditions_fail_closed() {
    let workbook = chain();
    let session = CommandSession::new();
    let mut rebase = envelope("rb", "0", set("A1", 5.0));
    rebase["mode"] = json!("rebase");
    let response = run(&session, &workbook, &rebase);
    assert_eq!(response["status"], "rejected");
    assert_eq!(response["error"]["code"], "unsupported_mode");
    let mut pre = envelope("pc", "0", set("A1", 5.0));
    pre["preconditions"] = json!([{"cell": "Sheet1!A1", "unchanged_since": "0"}]);
    let response = run(&session, &workbook, &pre);
    assert_eq!(response["status"], "rejected");
    assert_eq!(response["error"]["code"], "unsupported_precondition");
    assert_eq!(revision_string(&workbook), "0");
    assert_eq!(full_values(&workbook)["Sheet1!A1"], json!(1.0));
}

type Mutation = Box<dyn Fn(&mut Json)>;

#[test]
fn invalid_envelopes_are_rejected_with_invalid_request() {
    let workbook = chain();
    let session = CommandSession::new();
    let good = envelope("x", "0", set("A1", 5.0));
    let mutations: Vec<(&str, Mutation)> = vec![
        ("version", Box::new(|e| e["v"] = json!(2))),
        ("unknown field", Box::new(|e| e["extra"] = json!(1))),
        (
            "empty command_id",
            Box::new(|e| e["command_id"] = json!("")),
        ),
        (
            "numeric client_seq",
            Box::new(|e| e["client_seq"] = json!(1)),
        ),
        (
            "bad client_seq",
            Box::new(|e| e["client_seq"] = json!("-1")),
        ),
        (
            "actor kind",
            Box::new(|e| e["actor"]["kind"] = json!("robot")),
        ),
        (
            "actor unknown field",
            Box::new(|e| e["actor"]["role"] = json!("x")),
        ),
        (
            "missing actor",
            Box::new(|e| drop(e.as_object_mut().unwrap().remove("actor"))),
        ),
        (
            "missing client_id",
            Box::new(|e| drop(e.as_object_mut().unwrap().remove("client_id"))),
        ),
        ("mode", Box::new(|e| e["mode"] = json!("loose"))),
        (
            "op type",
            Box::new(|e| e["op"]["type"] = json!("insert_rows")),
        ),
        (
            "edits not array",
            Box::new(|e| e["op"]["edits"] = json!({})),
        ),
        (
            "empty idempotency_key",
            Box::new(|e| e["idempotency_key"] = json!("")),
        ),
    ];
    for (label, mutate) in mutations {
        let mut request = good.clone();
        mutate(&mut request);
        let response = run(&session, &workbook, &request);
        assert_eq!(response["status"], "rejected", "{label}");
        assert_eq!(
            response["error"]["code"], "invalid_request",
            "{label}: {response}"
        );
    }
    let text: Json =
        serde_json::from_str(&session.apply_and_calculate(&workbook, "not json")).unwrap();
    assert_eq!(text["error"]["code"], "invalid_request");
    assert_eq!(text["command_id"], Json::Null);
    let mut revision = good.clone();
    revision["base_revision"] = json!("r0");
    assert_eq!(
        run(&session, &workbook, &revision)["error"]["code"],
        "invalid_revision"
    );
    assert_eq!(revision_string(&workbook), "0");
    // Invalid envelopes are not consumed: the same command_id applies later.
    // (The invalid_revision one was consumed under id "x", so use a new id.)
    let response = run(&session, &workbook, &envelope("y", "0", set("A1", 5.0)));
    assert_eq!(response["status"], "applied");
}

#[test]
fn edit_errors_keep_their_codes_inside_the_envelope() {
    let workbook = chain();
    let session = CommandSession::new();
    let outside = run(&session, &workbook, &envelope("a", "0", set("XFE99", 1.0)));
    assert_eq!(outside["error"]["code"], "out_of_bounds");
    let sheet = run(
        &session,
        &workbook,
        &envelope(
            "b",
            "0",
            json!([{"sheet": "Nope", "address": "A1", "value": 1}]),
        ),
    );
    assert_eq!(sheet["error"]["code"], "unknown_sheet");
    assert_eq!(revision_string(&workbook), "0");
}
