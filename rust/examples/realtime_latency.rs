//! Per-keystroke latency bench for the legacy apply-then-calculate path:
//! apply_edits_envelope (applyEditsResultJson) -> ImportedWorkbook::calculate(1)
//! (calculateJson) -> serde_json::to_string(report). Release build only.
use std::collections::BTreeMap;
use std::time::{Duration, Instant};
use workbook_forge::browser_contract::apply_edits_envelope;
use workbook_forge::toolkit::{Cell, CellValue, Sheet, WorkbookModel, calculate};
use workbook_forge::xlsx::{export_xlsx_bytes, import_xlsx_bytes};

fn ms(d: Duration) -> f64 {
    d.as_secs_f64() * 1000.0
}
fn pct(v: &mut [f64], p: f64) -> f64 {
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let i = ((v.len() as f64 - 1.0) * p).round() as usize;
    v[i]
}
fn num(n: f64) -> Cell {
    Cell::value(CellValue::Number(n))
}

fn chain(n: usize) -> WorkbookModel {
    let mut s = Sheet::new("S");
    s.cells.insert("A1".into(), num(1.0));
    for r in 2..=n {
        s.cells
            .insert(format!("A{r}"), Cell::formula(format!("=A{}+1", r - 1)));
    }
    WorkbookModel {
        sheets: vec![s],
        ..WorkbookModel::default()
    }
}
fn fanout(sums: usize, range: usize) -> WorkbookModel {
    let mut s = Sheet::new("S");
    for r in 1..=range {
        s.cells.insert(format!("A{r}"), num(r as f64));
    }
    for r in 1..=sums {
        s.cells.insert(
            format!("B{r}"),
            Cell::formula(format!("=SUM($A$1:$A${range})")),
        );
    }
    WorkbookModel {
        sheets: vec![s],
        ..WorkbookModel::default()
    }
}
/// Financial mix: Assumptions (growth, tax, threshold, 10-row rate table) + n rows of
/// A=key, B=revenue, C=B*(1+growth), D=IF(C>thr,C*tax,0), E=VLOOKUP(A,rates,2,FALSE),
/// F=C-D-C*E, G=running SUM($F$1:F r) on every 50th row; total = SUM(F1:Fn).
fn financial(n: usize) -> WorkbookModel {
    let mut a = Sheet::new("Assumptions");
    a.cells.insert("B1".into(), num(0.05));
    a.cells.insert("B2".into(), num(0.21));
    a.cells.insert("B3".into(), num(500.0));
    for k in 1..=10 {
        a.cells.insert(format!("D{k}"), num(k as f64));
        a.cells.insert(format!("E{k}"), num(0.01 * k as f64));
    }
    let mut m = Sheet::new("Model");
    for r in 1..=n {
        m.cells
            .insert(format!("A{r}"), num(((r - 1) % 10 + 1) as f64));
        m.cells.insert(format!("B{r}"), num(100.0 + r as f64));
        m.cells.insert(
            format!("C{r}"),
            Cell::formula(format!("=B{r}*(1+Assumptions!$B$1)")),
        );
        m.cells.insert(
            format!("D{r}"),
            Cell::formula(format!(
                "=IF(C{r}>Assumptions!$B$3,C{r}*Assumptions!$B$2,0)"
            )),
        );
        m.cells.insert(
            format!("E{r}"),
            Cell::formula(format!("=VLOOKUP(A{r},Assumptions!$D$1:$E$10,2,FALSE)")),
        );
        m.cells.insert(
            format!("F{r}"),
            Cell::formula(format!("=C{r}-D{r}-C{r}*E{r}")),
        );
    }
    m.cells
        .insert("H1".into(), Cell::formula(format!("=SUM(F1:F{n})")));
    WorkbookModel {
        sheets: vec![a, m],
        ..WorkbookModel::default()
    }
}

struct Case {
    name: String,
    model: WorkbookModel,
    sheet: &'static str,
    cell: String,
}

fn run(c: Case) {
    let cells: usize = c.model.sheets.iter().map(|s| s.cells.len()).sum();
    let t = Instant::now();
    let cold_full = calculate(&c.model, 1);
    let cold_ms = ms(t.elapsed());
    if !cold_full.diagnostics.is_empty() {
        println!(
            "| {} | {} | REJECTED: {} {} |",
            c.name, cells, cold_full.diagnostics[0].code, cold_full.diagnostics[0].message
        );
        return;
    }
    let bytes = export_xlsx_bytes(&c.model).unwrap();
    let wb = import_xlsx_bytes(&bytes, BTreeMap::new(), BTreeMap::new()).unwrap();
    let first = wb.calculate(1); // prime published report (session cache)
    assert!(
        first.diagnostics.is_empty(),
        "{:?}",
        &first.diagnostics[..1]
    );
    // overhead floor: recalc with nothing dirty (graph rebuild + clone of cached values)
    let t = Instant::now();
    let _ = wb.calculate(1);
    let noop_ms = ms(t.elapsed());
    let t = Instant::now();
    let snap = serde_json::to_string(&wb.snapshot()).unwrap();
    let snap_ms = ms(t.elapsed());

    let (mut ap, mut ca, mut se, mut tot) = (vec![], vec![], vec![], vec![]);
    let (mut out_bytes, mut evaluated) = (0usize, 0usize);
    let budget = Instant::now();
    let mut i = 0u64;
    while i < 30 && (i < 5 || budget.elapsed() < Duration::from_secs(20)) {
        let rev = wb.revision_string_for_bench();
        let edit = format!(
            r#"[{{"sheet":"{}","address":"{}","value":{}}}]"#,
            c.sheet,
            c.cell,
            2.0 + i as f64
        );
        let t0 = Instant::now();
        let env = apply_edits_envelope(&wb, &edit, &rev);
        let t1 = Instant::now();
        assert!(env.contains("\"ok\":true"), "{env}");
        let report = wb.calculate(1);
        let t2 = Instant::now();
        let json = serde_json::to_string(&report).unwrap();
        let t3 = Instant::now();
        out_bytes = json.len();
        evaluated = report.evaluated_cells.len();
        ap.push(ms(t1 - t0));
        ca.push(ms(t2 - t1));
        se.push(ms(t3 - t2));
        tot.push(ms(t3 - t0));
        i += 1;
    }
    println!(
        "| {} | {} | {} | {:.2}/{:.2} | {:.2}/{:.2} | {:.2}/{:.2} | **{:.1}/{:.1}** | {:.1} | {:.1} | {} KB | {} KB |",
        c.name,
        cells,
        evaluated,
        pct(&mut ap, 0.5),
        pct(&mut ap, 0.95),
        pct(&mut ca, 0.5),
        pct(&mut ca, 0.95),
        pct(&mut se, 0.5),
        pct(&mut se, 0.95),
        pct(&mut tot.clone(), 0.5),
        pct(&mut tot.clone(), 0.95),
        noop_ms,
        cold_ms,
        out_bytes / 1024,
        snap.len() / 1024,
    );
    eprintln!("{} snapshotJson {:.1} ms, iters {}", c.name, snap_ms, i);
}

trait Rev {
    fn revision_string_for_bench(&self) -> String;
}
impl Rev for workbook_forge::xlsx::ImportedWorkbook {
    fn revision_string_for_bench(&self) -> String {
        workbook_forge::browser_contract::revision_string(self)
    }
}

fn main() {
    let only: Option<String> = std::env::args().nth(1);
    println!(
        "| workload | cells | evaluated/edit | apply p50/p95 ms | recalc p50/p95 | serialize p50/p95 | total p50/p95 | no-op recalc ms | cold full calc ms | response | snapshotJson |"
    );
    println!("|---|---|---|---|---|---|---|---|---|---|---|");
    let mut cases = vec![];
    for n in [100, 1_000, 2_500, 5_000, 10_000, 50_000] {
        cases.push(Case {
            name: format!("chain {n}, edit head A1"),
            model: chain(n),
            sheet: "S",
            cell: "A1".into(),
        });
    }
    cases.push(Case {
        name: "fan-out 10k x SUM($A$1:$A$1000)".into(),
        model: fanout(10_000, 1000),
        sheet: "S",
        cell: "A500".into(),
    });
    cases.push(Case {
        name: "chain 10000, edit tail A9990".into(),
        model: chain(10_000),
        sheet: "S",
        cell: "A9990".into(),
    });
    cases.push(Case {
        name: "fan-out 90 x SUM($A$1:$A$1000)".into(),
        model: fanout(90, 1000),
        sheet: "S",
        cell: "A500".into(),
    });
    cases.push(Case {
        name: "fan-out 10k x SUM($A$1:$A$9)".into(),
        model: fanout(10_000, 9),
        sheet: "S",
        cell: "A5".into(),
    });
    for n in [100, 250, 500, 1_000, 3_000, 5_000] {
        cases.push(Case {
            name: format!("financial {n} rows, edit growth"),
            model: financial(n),
            sheet: "Assumptions",
            cell: "B1".into(),
        });
        cases.push(Case {
            name: format!("financial {n} rows, edit one row"),
            model: financial(n),
            sheet: "Model",
            cell: format!("B{}", n / 2),
        });
    }
    for c in cases {
        if only.as_deref().is_some_and(|o| !c.name.contains(o)) {
            continue;
        }
        run(c);
    }
}
