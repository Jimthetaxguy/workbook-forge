//! Write the Univer spike fixture: one input and five dependent formulas.
//!
//! Usage: univer_spike_fixture OUTPUT.xlsx
//! Expected values (independent arithmetic, A1 = 10):
//! B1 = A1*2 = 20, C1 = B1+5 = 25, D1 = C1*3 = 75, E1 = D1-B1 = 55,
//! F1 = SUM(A1:E1) = 185.
use workbook_forge::toolkit::Cell;
use workbook_forge::{CellValue, Sheet, WorkbookModel, xlsx};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().collect();
    let [_, output] = args.as_slice() else {
        return Err("usage: univer_spike_fixture OUTPUT.xlsx".into());
    };
    let mut sheet = Sheet::new("Sheet1");
    sheet
        .cells
        .insert("A1".into(), Cell::value(CellValue::Number(10.0)));
    for (address, formula) in [
        ("B1", "A1*2"),
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
