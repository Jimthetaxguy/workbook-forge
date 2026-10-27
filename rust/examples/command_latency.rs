//! Per-command latency of the browser edit loop: one `set_cells` envelope
//! through `CommandSession::apply_and_calculate` on an imported workbook
//! (apply, calculate, diff against the baseline, log, respond). Run in release
//! mode; compare timings only on the same machine and build.
use serde_json::{Value as Json, json};
use std::collections::BTreeMap;
use std::time::{Duration, Instant};
use workbook_forge::browser_contract::CommandSession;
use workbook_forge::toolkit::{Cell, CellValue, Sheet, WorkbookModel, calculate};
use workbook_forge::xlsx::{export_xlsx_bytes, import_xlsx_bytes};

const COMMANDS: usize = 25;

type Build = fn() -> WorkbookModel;

fn num(n: f64) -> Cell {
    Cell::value(CellValue::Number(n))
}

fn model(sheets: Vec<Sheet>) -> WorkbookModel {
    WorkbookModel {
        sheets,
        ..WorkbookModel::default()
    }
}

fn chain(n: usize) -> WorkbookModel {
    let mut sheet = Sheet::new("S");
    sheet.cells.insert("A1".into(), num(1.0));
    for row in 2..=n {
        sheet
            .cells
            .insert(format!("A{row}"), Cell::formula(format!("=A{}+1", row - 1)));
    }
    model(vec![sheet])
}

/// Four columns per row: A literal, B=A*2, C=B+A, D=SUM(B:C).
fn grid(rows: usize) -> WorkbookModel {
    let mut sheet = Sheet::new("S");
    for row in 1..=rows {
        sheet.cells.insert(format!("A{row}"), num(row as f64));
        sheet
            .cells
            .insert(format!("B{row}"), Cell::formula(format!("=A{row}*2")));
        sheet
            .cells
            .insert(format!("C{row}"), Cell::formula(format!("=B{row}+A{row}")));
        sheet.cells.insert(
            format!("D{row}"),
            Cell::formula(format!("=SUM(B{row}:C{row})")),
        );
    }
    model(vec![sheet])
}

/// The financial mix from `realtime_latency`, without its total row.
fn financial(rows: usize) -> WorkbookModel {
    let mut assumptions = Sheet::new("Assumptions");
    assumptions.cells.insert("B1".into(), num(0.05));
    assumptions.cells.insert("B2".into(), num(0.21));
    assumptions.cells.insert("B3".into(), num(500.0));
    for k in 1..=10 {
        assumptions.cells.insert(format!("D{k}"), num(k as f64));
        assumptions
            .cells
            .insert(format!("E{k}"), num(0.01 * k as f64));
    }
    let mut sheet = Sheet::new("Model");
    for r in 1..=rows {
        sheet
            .cells
            .insert(format!("A{r}"), num(((r - 1) % 10 + 1) as f64));
        sheet.cells.insert(format!("B{r}"), num(100.0 + r as f64));
        let formulas = [
            ("C", format!("=B{r}*(1+Assumptions!$B$1)")),
            (
                "D",
                format!("=IF(C{r}>Assumptions!$B$3,C{r}*Assumptions!$B$2,0)"),
            ),
            (
                "E",
                format!("=VLOOKUP(A{r},Assumptions!$D$1:$E$10,2,FALSE)"),
            ),
            ("F", format!("=C{r}-D{r}-C{r}*E{r}")),
        ];
        for (column, formula) in formulas {
            sheet
                .cells
                .insert(format!("{column}{r}"), Cell::formula(formula));
        }
    }
    model(vec![assumptions, sheet])
}

fn envelope(id: usize, base: &str, sheet: &str, address: &str, value: f64) -> String {
    json!({
        "v": 1, "command_id": format!("c{id}"), "client_id": "bench",
        "client_seq": id.to_string(), "idempotency_key": null,
        "actor": {"kind": "human", "id": "bench", "on_behalf_of": null},
        "base_revision": base, "mode": "strict", "preconditions": [],
        "op": {"type": "set_cells", "edits": [{"sheet": sheet, "address": address, "value": value}]},
    })
    .to_string()
}

fn percentile(samples: &mut [Duration], p: f64) -> f64 {
    samples.sort();
    let index = ((samples.len() - 1) as f64 * p).round() as usize;
    samples[index].as_secs_f64() * 1000.0
}

fn run(name: &str, model: WorkbookModel, sheet: &str, address: &str) {
    let cells: usize = model.sheets.iter().map(|s| s.cells.len()).sum();
    let bytes = export_xlsx_bytes(&model).unwrap();
    let workbook = import_xlsx_bytes(&bytes, BTreeMap::new(), BTreeMap::new()).unwrap();
    let session = CommandSession::new();
    let mut revision = workbook.revision().to_string();
    let (mut samples, mut changed, mut evaluated, mut response_bytes) = (Vec::new(), 0, 0, 0);
    // Command 0 primes the baseline with a full calculation; it is not timed.
    for id in 0..=COMMANDS {
        let request = envelope(id, &revision, sheet, address, 2.0 + id as f64);
        let start = Instant::now();
        let response = session.apply_and_calculate(&workbook, &request);
        let elapsed = start.elapsed();
        let parsed: Json = serde_json::from_str(&response).unwrap();
        assert_eq!(parsed["status"], "applied", "{response}");
        revision = parsed["revision"].as_str().unwrap().to_string();
        if id > 0 {
            samples.push(elapsed);
            changed = parsed["changed"].as_array().map_or(0, Vec::len);
            evaluated = parsed["evaluated_cells"].as_u64().unwrap_or(0) as usize;
            response_bytes = response.len();
        }
    }
    // The timed path must agree with a fresh full calculation.
    assert_eq!(
        workbook.calculate(1).values,
        calculate(&workbook.snapshot(), 1).values,
        "{name}"
    );
    let p50 = percentile(&mut samples, 0.5);
    let p95 = percentile(&mut samples, 0.95);
    println!(
        "{}",
        json!({"workload": name, "cells": cells, "evaluated": evaluated, "changed": changed,
            "p50_ms": (p50 * 100.0).round() / 100.0, "p95_ms": (p95 * 100.0).round() / 100.0,
            "response_bytes": response_bytes})
    );
}

fn main() {
    let only = std::env::args().nth(1);
    let cases: Vec<(&str, Build, &str, &str)> = vec![
        ("chain 10k, edit tail", || chain(10_000), "S", "A9999"),
        ("chain 10k, edit head", || chain(10_000), "S", "A1"),
        ("grid 10k, edit one row", || grid(2_500), "S", "A1250"),
        (
            "financial 2.5k rows (15k cells), edit one row",
            || financial(2_500),
            "Model",
            "B1250",
        ),
        (
            "financial 2.5k rows (15k cells), edit growth",
            || financial(2_500),
            "Assumptions",
            "B1",
        ),
        ("grid 50k, edit one row", || grid(12_500), "S", "A6250"),
        ("chain 50k, edit tail", || chain(50_000), "S", "A49999"),
    ];
    for (name, build, sheet, address) in cases {
        if only.as_deref().is_some_and(|o| !name.contains(o)) {
            continue;
        }
        run(name, build(), sheet, address);
    }
}
