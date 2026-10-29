//! Corpus robustness driver for `tools/corpus_check.py`.
//!
//! `corpus_roundtrip SOURCE.xlsx OUTPUT.xlsx` runs open (from bytes, the path
//! the WASM engine uses), inspect, calculate, one value edit, one absent-cell
//! insert, export and reimport, and prints one JSON line. A refusal prints
//! `"outcome":"refused"` with the error's code and exits 3. A panic is caught
//! and reported as `"outcome":"crash"`. Package-level checks (byte
//! preservation, openpyxl reopen) are made by the Python harness.
use serde_json::{Value as Json, json};
use std::collections::BTreeMap;
use std::panic::{AssertUnwindSafe, catch_unwind};
use workbook_forge::toolkit::{CellAddress, CellValue, Edit, WorkbookModel};
use workbook_forge::xlsx::{XlsxError, import_xlsx_bytes};

fn refused(stage: &str, error: &XlsxError) -> Json {
    json!({"outcome": "refused", "stage": stage, "code": error.code(), "error": error.to_string()})
}

fn column_name(mut column: u32) -> String {
    let mut name = String::new();
    while column > 0 {
        let rem = (column - 1) % 26;
        name.insert(0, (b'A' + rem as u8) as char);
        column = (column - 1) / 26;
    }
    name
}

fn pick_edit(model: &WorkbookModel) -> Option<(String, String, CellValue)> {
    for sheet in &model.sheets {
        for (address, cell) in &sheet.cells {
            if cell.formula.is_some() || cell.blocked_reason.is_some() {
                continue;
            }
            match &cell.value {
                CellValue::Number(n) => {
                    return Some((
                        sheet.name.clone(),
                        address.clone(),
                        CellValue::Number(n + 1.0),
                    ));
                }
                CellValue::Text(t) if t.len() < 1000 => {
                    return Some((
                        sheet.name.clone(),
                        address.clone(),
                        CellValue::Text(format!("{t} (edited)")),
                    ));
                }
                _ => {}
            }
        }
    }
    None
}

fn pick_insert(
    model: &WorkbookModel,
    merged: &BTreeMap<String, Vec<(u32, u32, u32, u32)>>,
) -> Option<(String, String)> {
    for sheet in &model.sheets {
        let ranges = merged.get(&sheet.name).cloned().unwrap_or_default();
        for row in 1..60u32 {
            for column in 1..12u32 {
                let address = format!("{}{row}", column_name(column));
                if sheet.cells.contains_key(&address) {
                    continue;
                }
                if ranges
                    .iter()
                    .any(|&(c1, r1, c2, r2)| c1 <= column && column <= c2 && r1 <= row && row <= r2)
                {
                    continue;
                }
                return Some((sheet.name.clone(), address));
            }
        }
    }
    None
}

fn same(a: &CellValue, b: &CellValue) -> bool {
    match (a, b) {
        (CellValue::Number(x), CellValue::Number(y)) => (x - y).abs() <= 1e-12 * x.abs().max(1.0),
        _ => a == b,
    }
}

fn run(source: &str, output: &str) -> Json {
    let bytes = match std::fs::read(source) {
        Ok(bytes) => bytes,
        Err(error) => {
            return json!({"outcome": "crash", "stage": "read", "error": error.to_string()});
        }
    };
    let imported = match import_xlsx_bytes(&bytes, BTreeMap::new(), BTreeMap::new()) {
        Ok(imported) => imported,
        Err(error) => return refused("open", &error),
    };
    let _ = imported.capabilities();
    let report = imported.calculate(1);
    if report.stale {
        return json!({"outcome": "crash", "stage": "calculate", "error": "stale report"});
    }
    let full_diagnostics = report.diagnostics.len();
    let before = imported.snapshot();
    let merged = imported.merged_ranges();
    let edit = pick_edit(&before);
    // Bind the edited cell as the session output (the documented export flow
    // for workbooks holding formulas the engine does not calculate).
    let imported = match &edit {
        Some((sheet, address, _)) => {
            let outputs = BTreeMap::from([(
                "edited".to_string(),
                CellAddress::new(sheet.clone(), address.clone()),
            )]);
            match import_xlsx_bytes(&bytes, BTreeMap::new(), outputs) {
                Ok(imported) => imported,
                Err(error) => return refused("rebind", &error),
            }
        }
        None => imported,
    };
    let mut edits = Vec::new();
    if let Some((sheet, address, value)) = edit {
        if let Err(error) = imported.apply(vec![Edit::value(&sheet, &address, value.clone())], None)
        {
            let mut record = refused("edit", &error);
            record["full_calc_diagnostics"] = json!(full_diagnostics);
            return record;
        }
        edits.push((sheet, address, value));
    }
    if let Some((sheet, address)) = pick_insert(&before, &merged) {
        let value = CellValue::Number(4242.0);
        if let Err(error) = imported.apply(vec![Edit::value(&sheet, &address, value.clone())], None)
        {
            return refused("insert", &error);
        }
        edits.push((sheet, address, value));
    }
    let exported = match imported.export_bytes() {
        Ok(exported) => exported,
        Err(error) => return refused("export", &error),
    };
    if let Err(error) = std::fs::write(output, &exported) {
        return json!({"outcome": "crash", "stage": "write", "error": error.to_string()});
    }
    let again = match import_xlsx_bytes(&exported, BTreeMap::new(), BTreeMap::new()) {
        Ok(again) => again,
        Err(error) => {
            return json!({"outcome": "corrupt", "stage": "reimport", "code": error.code(), "error": error.to_string()});
        }
    };
    let after = again.snapshot();
    let mut lossy = Vec::new();
    for sheet in &before.sheets {
        let Some(other) = after.sheets.iter().find(|s| s.name == sheet.name) else {
            lossy.push(format!("sheet {} missing", sheet.name));
            continue;
        };
        let mut expected = sheet.cells.clone();
        for (name, address, value) in &edits {
            if name == &sheet.name {
                expected.insert(
                    address.clone(),
                    workbook_forge::toolkit::Cell::value(value.clone()),
                );
            }
        }
        for (address, cell) in &expected {
            match other.cells.get(address) {
                None => lossy.push(format!("{}!{address} missing after reimport", sheet.name)),
                Some(got) => {
                    if got.formula != cell.formula
                        || (cell.formula.is_none() && !same(&got.value, &cell.value))
                    {
                        lossy.push(format!(
                            "{}!{address}: {:?} became {:?}",
                            sheet.name, cell.value, got.value
                        ));
                    }
                }
            }
        }
    }
    let edited: Vec<&String> = edits.iter().map(|(sheet, _, _)| sheet).collect();
    json!({"outcome": "exported", "edited_sheets": edited, "lossy": lossy, "full_calc_diagnostics": full_diagnostics})
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() != 2 {
        eprintln!("usage: corpus_roundtrip SOURCE.xlsx OUTPUT.xlsx");
        std::process::exit(2);
    }
    std::panic::set_hook(Box::new(|_| {}));
    let result = catch_unwind(AssertUnwindSafe(|| run(&args[0], &args[1])));
    let record = match result {
        Ok(record) => record,
        Err(payload) => {
            let message = payload
                .downcast_ref::<String>()
                .cloned()
                .or_else(|| payload.downcast_ref::<&str>().map(|s| s.to_string()))
                .unwrap_or_else(|| "panic".into());
            json!({"outcome": "crash", "stage": "panic", "error": message})
        }
    };
    println!("{record}");
    if record["outcome"] == "refused" {
        std::process::exit(3);
    }
}
