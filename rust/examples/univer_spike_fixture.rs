//! Write the Univer spike fixtures: one input and five dependent formulas.
//!
//! Usage: univer_spike_fixture [--realtime] OUTPUT.xlsx
//!
//! Default (fixtures/univer-spike-chain.xlsx), expected values by independent
//! arithmetic with A1 = 10:
//! B1 = A1*2 = 20, C1 = B1+5 = 25, D1 = C1*3 = 75, E1 = D1-B1 = 55,
//! F1 = SUM(A1:E1) = 185.
//!
//! `--realtime` (fixtures/realtime-chain.xlsx, the event-log acceptance
//! fixture): B1 holds the literal placeholder 0 instead of `A1*2`, so typing
//! `=A1*2` into B1 is a real change that Univer's editor commits:
//! A1 = 10, B1 = 0, C1 = B1+5 = 5, D1 = C1*3 = 15, E1 = D1-B1 = 15,
//! F1 = SUM(A1:E1) = 45.
use workbook_forge::toolkit::Cell;
use workbook_forge::{CellValue, Sheet, WorkbookModel, xlsx};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().collect();
    let (realtime, output) = match args.as_slice() {
        [_, output] => (false, output),
        [_, flag, output] if flag == "--realtime" => (true, output),
        _ => return Err("usage: univer_spike_fixture [--realtime] OUTPUT.xlsx".into()),
    };
    let mut sheet = Sheet::new("Sheet1");
    sheet
        .cells
        .insert("A1".into(), Cell::value(CellValue::Number(10.0)));
    if realtime {
        sheet
            .cells
            .insert("B1".into(), Cell::value(CellValue::Number(0.0)));
    } else {
        sheet.cells.insert("B1".into(), Cell::formula("A1*2"));
    }
    for (address, formula) in [
        ("C1", "B1+5"),
        ("D1", "C1*3"),
        ("E1", "D1-B1"),
        ("F1", "SUM(A1:E1)"),
    ] {
        sheet.cells.insert(address.into(), Cell::formula(formula));
    }
    let model = WorkbookModel {
        sheets: vec![sheet],
        ..WorkbookModel::default()
    };
    xlsx::export_xlsx(&model, output)?;
    Ok(())
}
