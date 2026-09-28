//! Reproducible correctness and work measurements, no machine-specific paths.
//! Run release mode; compare elapsed time only on the same machine and build.
use std::time::Instant;
use workbook_forge::toolkit::{Cell, CellAddress, CellValue, Sheet, WorkbookModel, calculate};
fn run(name: &str, model: WorkbookModel) {
    let start = Instant::now();
    let serial = calculate(&model, 1);
    let serial_elapsed = start.elapsed();
    let start = Instant::now();
    let parallel = calculate(&model, 4);
    let parallel_elapsed = start.elapsed();
    assert_eq!(serial.outputs, parallel.outputs);
    assert_eq!(serial.values, parallel.values);
    assert_eq!(serial.diagnostics, parallel.diagnostics);
    assert!(serial.diagnostics.is_empty());
    println!(
        "{}",
        serde_json::json!({"workload":name,"populated_cells":model.sheets.iter().map(|sheet|sheet.cells.len()).sum::<usize>(),"formula_evaluations":serial.evaluated_cells.len(),"serial_us":serial_elapsed.as_micros(),"parallel_us":parallel_elapsed.as_micros(),"workers":4,"serialized_model_bytes":serde_json::to_vec(&model).unwrap().len(),"serialized_result_bytes":serde_json::to_vec(&serial).unwrap().len(),"memory_note":"serialized sizes are payload measurements, not resident memory","equal":true})
    );
}
fn main() {
    for workload in ["sparse", "dense", "chain", "branching", "parallel-heavy"] {
        let mut sheet = Sheet::new("Benchmark");
        let count = if workload == "parallel-heavy" {
            64
        } else {
            1000
        };
        for row in 1..=count {
            let address = format!("A{row}");
            let cell = match workload {
                "sparse" => Cell::value(CellValue::Number(row as f64)),
                "dense" => Cell::formula(format!("={row}*3+4")),
                "chain" if row == 1 => Cell::value(CellValue::Number(1.0)),
                "chain" => Cell::formula(format!("=A{}+1", row - 1)),
                "branching" if row == 1 => Cell::value(CellValue::Number(1.0)),
                "branching" => Cell::formula(format!("=A{}+1", row / 2)),
                "parallel-heavy" => Cell::formula("=SUM(SORT(SEQUENCE(10000,1,10000,-1)))"),
                _ => unreachable!(),
            };
            sheet.cells.insert(address, cell);
        }
        let mut model = WorkbookModel {
            sheets: vec![sheet],
            ..WorkbookModel::default()
        };
        if workload == "chain" {
            model.outputs.insert(
                "last".into(),
                CellAddress::new("Benchmark", format!("A{count}")),
            );
        }
        run(workload, model);
    }
}
