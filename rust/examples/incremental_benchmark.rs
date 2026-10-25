//! Edit-to-result latency for a session on ~10K-cell workbooks.
//! Run in release mode; compare timings only on the same machine and build.
use std::time::{Duration, Instant};
use workbook_forge::toolkit::{Cell, CellValue, Edit, Session, Sheet, WorkbookModel};

const ROWS: usize = 2500;
const EDITS: usize = 25;

fn grid(total: bool) -> WorkbookModel {
    // Four columns per row, optionally one total: ~10K cells, ~12.5-15K edges.
    let mut sheet = Sheet::new("Bench");
    for row in 1..=ROWS {
        sheet.cells.insert(
            format!("A{row}"),
            Cell::value(CellValue::Number(row as f64)),
        );
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
    if total {
        sheet
            .cells
            .insert("E1".into(), Cell::formula(format!("=SUM(D1:D{ROWS})")));
    }
    WorkbookModel {
        sheets: vec![sheet],
        ..WorkbookModel::default()
    }
}

fn chain() -> WorkbookModel {
    let mut sheet = Sheet::new("Bench");
    sheet
        .cells
        .insert("A1".into(), Cell::value(CellValue::Number(1.0)));
    for row in 2..=ROWS * 4 {
        sheet
            .cells
            .insert(format!("A{row}"), Cell::formula(format!("=A{}+1", row - 1)));
    }
    WorkbookModel {
        sheets: vec![sheet],
        ..WorkbookModel::default()
    }
}

fn median(mut samples: Vec<Duration>) -> u128 {
    samples.sort();
    samples[samples.len() / 2].as_micros()
}

fn measure(workload: &str, kind: &str, model: WorkbookModel, edit: impl Fn(usize) -> Edit) {
    let cells = model.sheets.iter().map(|s| s.cells.len()).sum::<usize>();
    let session = Session::new(model).unwrap();
    let start = Instant::now();
    let first = session.calculate(1);
    let initial = start.elapsed();
    assert!(first.diagnostics.is_empty(), "{:?}", first.diagnostics);
    let (mut apply, mut calc, mut evaluated) = (Vec::new(), Vec::new(), 0);
    for i in 0..EDITS {
        let start = Instant::now();
        session.apply(vec![edit(i)], None).unwrap();
        apply.push(start.elapsed());
        let start = Instant::now();
        let report = session.calculate(1);
        calc.push(start.elapsed());
        assert!(report.diagnostics.is_empty() && !report.stale);
        evaluated = report.evaluated_cells.len();
    }
    let (apply_us, calc_us) = (median(apply), median(calc));
    println!(
        "{}",
        serde_json::json!({"workload":workload,"edit":kind,"cells":cells,
            "initial_calculate_us":initial.as_micros(),"median_apply_us":apply_us,
            "median_calculate_us":calc_us,"median_edit_to_result_us":apply_us+calc_us,
            "evaluated_per_edit":evaluated})
    );
}

fn main() {
    let value = |i: usize| {
        Edit::value(
            "Bench",
            format!("A{}", 1 + i * 97),
            CellValue::Number(i as f64),
        )
    };
    measure("grid", "value", grid(false), value);
    measure("grid+total", "value", grid(true), value);
    measure("grid", "formula", grid(false), |i| {
        Edit::formula(
            "Bench",
            format!("B{}", 1 + i * 97),
            format!("=A{}*3", 1 + i * 97),
        )
    });
    measure("chain", "value-head", chain(), |i| {
        Edit::value("Bench", "A1", CellValue::Number(i as f64))
    });
    measure("chain", "value-tail", chain(), |i| {
        Edit::value(
            "Bench",
            format!("A{}", ROWS * 4 - 1),
            CellValue::Number(i as f64),
        )
    });
}
