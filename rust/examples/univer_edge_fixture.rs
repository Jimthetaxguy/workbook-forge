//! Write a Univer recovery fixture with an allowed `!` in the sheet name and a Boolean cell.
//!
//! Usage: univer_edge_fixture OUTPUT.xlsx
use workbook_forge::toolkit::Cell;
use workbook_forge::{CellValue, Sheet, WorkbookModel, xlsx};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().collect();
    let [_, output] = args.as_slice() else {
        return Err("usage: univer_edge_fixture OUTPUT.xlsx".into());
    };
    let mut sheet = Sheet::new("Sales!2026");
    sheet
        .cells
        .insert("A1".into(), Cell::value(CellValue::Number(7.0)));
    sheet
        .cells
        .insert("B1".into(), Cell::value(CellValue::Boolean(true)));
    let model = WorkbookModel {
        sheets: vec![sheet],
        ..WorkbookModel::default()
    };
    xlsx::export_xlsx(&model, output)?;
    Ok(())
}
