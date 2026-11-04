//! Volatile functions in sessions: one context per pass, dirty on every pass,
//! logged clock and seed, deterministic replay, and undo/redo that do not
//! re-roll. Expected serials are independent arithmetic: 2026-01-15 is
//! 1900-system serial 46037 (2026-01-01 = 46023), and 12:00 is .5.
use serde_json::{Value as Json, json};
use workbook_forge::browser_contract::CommandSession;
use workbook_forge::event_log::{Baseline, replay};
use workbook_forge::toolkit::{CalculatedValue, Cell, CellValue, Edit, Session, Sheet};
use workbook_forge::{CalcContext, WorkbookModel};

const NOON: i64 = 1_768_478_400_000; // 2026-01-15T12:00:00Z

fn number(value: Option<&CalculatedValue>) -> f64 {
    match value {
        Some(CalculatedValue::Scalar(CellValue::Number(n))) => *n,
        other => panic!("expected a number, got {other:?}"),
    }
}

fn session(cells: &[(&str, &str)]) -> Session {
    let mut sheet = Sheet::new("Sheet1");
    for (address, formula) in cells {
        sheet
            .cells
            .insert((*address).into(), Cell::formula(*formula));
    }
    let mut model = WorkbookModel::default();
    model.sheets.push(sheet);
    Session::new(model).unwrap()
}

#[test]
fn every_now_in_one_pass_reads_the_same_instant() {
    let s = session(&[
        ("A1", "=NOW()"),
        ("B1", "=NOW()"),
        ("C1", "=A1-B1"),
        ("D1", "=TODAY()"),
    ]);
    let context = CalcContext::new(NOON, 0, 1);
    let report = s.calculate_with(1, &context);
    assert_eq!(report.calc, Some(context));
    assert_eq!(number(report.values.get("Sheet1!A1")), 46_037.5);
    assert_eq!(number(report.values.get("Sheet1!B1")), 46_037.5);
    assert_eq!(number(report.values.get("Sheet1!C1")), 0.0);
    assert_eq!(number(report.values.get("Sheet1!D1")), 46_037.0);
}

#[test]
fn the_session_offset_moves_today_across_local_midnight() {
    let s = session(&[("A1", "=TODAY()")]);
    s.set_utc_offset_minutes(-300).unwrap();
    assert_eq!(s.utc_offset_minutes(), -300);
    assert!(s.set_utc_offset_minutes(841).is_err());
    // 2026-11-04T04:59:59.999Z is 23:59:59.999 on Nov 3 at UTC-05:00.
    let before = 1_793_768_399_999;
    let report = s.calculate_with(1, &CalcContext::new(before, -300, 0));
    assert_eq!(number(report.values.get("Sheet1!A1")), 46_329.0);
    let report = s.calculate_with(1, &CalcContext::new(before + 1, -300, 0));
    assert_eq!(number(report.values.get("Sheet1!A1")), 46_330.0);
}

#[test]
fn a_volatile_formula_and_its_dependents_recalculate_on_every_pass() {
    let s = session(&[("A1", "=NOW()"), ("B1", "=A1+1"), ("C1", "=1+1")]);
    let first = s.calculate_with(1, &CalcContext::new(NOON, 0, 1));
    assert_eq!(first.evaluated_cells.len(), 3);
    s.apply(
        vec![Edit::value("Sheet1", "D1", CellValue::Number(5.0))],
        None,
    )
    .unwrap();
    let later = CalcContext::new(NOON + 43_200_000, 0, 1);
    let second = s.calculate_with(1, &later);
    // C1 is reused; the volatile A1 and its dependent B1 are recalculated.
    assert_eq!(second.evaluated_cells, vec!["Sheet1!A1", "Sheet1!B1"]);
    assert_eq!(number(second.values.get("Sheet1!A1")), 46_038.0);
    assert_eq!(number(second.values.get("Sheet1!B1")), 46_039.0);
    // Without an edit a pass still recalculates the volatile cells.
    let third = s.calculate_with(1, &CalcContext::new(NOON, 0, 1));
    assert_eq!(third.evaluated_cells, vec!["Sheet1!A1", "Sheet1!B1"]);
    assert_eq!(number(third.values.get("Sheet1!A1")), 46_037.5);
}

#[test]
fn a_report_without_volatile_cells_carries_no_context() {
    let s = session(&[("A1", "=1+1")]);
    let report = s.calculate(1);
    assert_eq!(report.calc, None);
    assert!(!serde_json::to_string(&report).unwrap().contains("calc\""));
}

fn envelope(id: &str, base: &str, op: Json) -> String {
    json!({
        "v": 1,
        "command_id": id,
        "client_id": "client-ui",
        "client_seq": "1",
        "idempotency_key": null,
        "actor": {"kind": "human", "id": "ui", "on_behalf_of": null},
        "base_revision": base,
        "mode": "strict",
        "preconditions": [],
        "op": op,
    })
    .to_string()
}

fn set(address: &str, formula: &str) -> Json {
    json!({"type": "set_cells", "edits": [{"sheet": "Sheet1", "address": address, "formula": formula}]})
}

fn pause() {
    // The clock has millisecond resolution; make each capture distinct.
    std::thread::sleep(std::time::Duration::from_millis(3));
}

#[test]
fn the_log_records_each_pass_context_and_replay_reuses_it() {
    let mut model = WorkbookModel::default();
    model.sheets.push(Sheet::new("Sheet1"));
    let s = Session::new(model).unwrap();
    s.set_utc_offset_minutes(-300).unwrap();
    let commands = CommandSession::new();
    commands.set_baseline(Baseline::blank("Sheet1"));
    commands.start_log(None, json!({}), 0);
    let applied = |id: &str, base: &str, op: Json| {
        pause();
        let response: Json =
            serde_json::from_str(&commands.apply_and_calculate(&s, &envelope(id, base, op)))
                .unwrap();
        assert_eq!(response["status"], "applied", "{response}");
        response
    };
    applied("c1", "0", set("A1", "NOW()"));
    applied("c2", "1", set("B1", "A1*2"));
    applied("c3", "2", set("C1", "A1+1"));
    let events = commands.events();
    let calcs: Vec<&Json> = events.iter().filter(|e| e["kind"] == "calc").collect();
    assert_eq!(calcs.len(), 3);
    for calc in &calcs {
        let context: CalcContext = serde_json::from_value(calc["calc_context"].clone()).unwrap();
        assert_eq!(context.utc_offset_minutes, -300);
        assert!(calc["calc_context"]["seed"].is_string());
    }
    // The first command calculated its own baseline, so that is logged too.
    assert!(calcs[0]["base_calc_context"].is_object());
    assert!(calcs[1].get("base_calc_context").is_none());
    // Each edit captured a new clock: A1 changed on every pass.
    assert_ne!(calcs[1]["calc_context"], calcs[2]["calc_context"]);
    let ndjson = commands.export_log_ndjson(s.revision(), None);
    let first = replay(&[], &ndjson).expect("replay");
    let second = replay(&[], &ndjson).expect("replay again");
    assert_eq!(first["ok"], true);
    assert_eq!(first["calcs_checked"], 3);
    assert_eq!(first["calc_events"], second["calc_events"]);
}

#[test]
fn undo_and_redo_restore_volatile_values_instead_of_rerolling() {
    let mut model = WorkbookModel::default();
    model.sheets.push(Sheet::new("Sheet1"));
    let s = Session::new(model).unwrap();
    let commands = CommandSession::new();
    commands.set_baseline(Baseline::blank("Sheet1"));
    commands.start_log(None, json!({}), 0);
    let run = |id: &str, base: &str, op: Json| -> Json {
        pause();
        serde_json::from_str(&commands.apply_and_calculate(&s, &envelope(id, base, op))).unwrap()
    };
    run("c1", "0", set("A1", "NOW()+RAND()"));
    let calc_value = |events: &[Json], key: &str| -> f64 {
        let calc = events.iter().rev().find(|e| e["kind"] == "calc").unwrap();
        let context: CalcContext = serde_json::from_value(calc["calc_context"].clone()).unwrap();
        number(s.calculate_with(1, &context).values.get(key))
    };
    let after_first = calc_value(&commands.events(), "Sheet1!A1");
    run("c2", "1", set("B1", "1"));
    let after_second = calc_value(&commands.events(), "Sheet1!A1");
    assert_ne!(after_first, after_second);
    let undo = run("u1", "2", json!({"type": "undo"}));
    assert_eq!(undo["status"], "applied", "{undo}");
    // Undoing c2 returns to the state after c1, with c1's clock and seed.
    assert_eq!(calc_value(&commands.events(), "Sheet1!A1"), after_first);
    let redo = run("r1", "3", json!({"type": "redo"}));
    assert_eq!(redo["status"], "applied", "{redo}");
    assert_eq!(calc_value(&commands.events(), "Sheet1!A1"), after_second);
    let ndjson = commands.export_log_ndjson(s.revision(), None);
    assert_eq!(replay(&[], &ndjson).expect("replay")["ok"], true);
}

#[test]
fn random_draws_repeat_for_a_seed_and_reroll_for_another() {
    let s = session(&[("A1", "=RAND()"), ("B1", "=A1*2"), ("C1", "=RANDBETWEEN(1,6)")]);
    let first = s.calculate_with(1, &CalcContext::new(NOON, 0, 1));
    let again = s.calculate_with(1, &CalcContext::new(NOON, 0, 1));
    let other = s.calculate_with(1, &CalcContext::new(NOON, 0, 2));
    assert_eq!(first.values, again.values);
    let a1 = number(first.values.get("Sheet1!A1"));
    assert!((0.0..1.0).contains(&a1));
    assert_eq!(a1, CalcContext::new(NOON, 0, 1).uniform("Sheet1!A1", 0, 0));
    assert_eq!(number(first.values.get("Sheet1!B1")), a1 * 2.0);
    assert_ne!(number(other.values.get("Sheet1!A1")), a1);
    let die = number(first.values.get("Sheet1!C1"));
    assert!((1.0..=6.0).contains(&die) && die.fract() == 0.0);
}

#[test]
fn worker_count_does_not_change_random_draws() {
    let cells: Vec<(String, String)> = (1..=40)
        .map(|row| (format!("A{row}"), "=RAND()+RANDBETWEEN(1,100)".to_string()))
        .collect();
    let refs: Vec<(&str, &str)> = cells.iter().map(|(a, f)| (a.as_str(), f.as_str())).collect();
    let context = CalcContext::new(NOON, 0, 99);
    let one = session(&refs).calculate_with(1, &context);
    let many = session(&refs).calculate_with(4, &context);
    assert_eq!(one.values, many.values);
}
