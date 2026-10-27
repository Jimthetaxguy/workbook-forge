//! The command path's incremental delta (a target reporting its own changes)
//! against the full diff (a copy of every value, diffed after the edit).
//! Random command sequences, including cycles, errors, stale bases and
//! interleaved legacy applies, must produce identical responses and event
//! logs through both, and `changed` must be the diff of full calculations.
use serde_json::{Value as Json, json};
use std::collections::BTreeMap;
use workbook_forge::browser_contract::{
    CommandSession, CommandTarget, ContractError, apply_edits_envelope, revision_string,
};
use workbook_forge::toolkit::{
    CalculationReport, Cell, CellValue, Edit, Sheet, WorkbookModel, calculate,
};
use workbook_forge::xlsx::{ImportedWorkbook, export_xlsx_bytes, import_xlsx_bytes};

/// An imported workbook seen only through the required `CommandTarget`
/// methods, so the command path falls back to the full diff.
struct FullDiff(ImportedWorkbook);

impl CommandTarget for FullDiff {
    fn revision(&self) -> u64 {
        self.0.revision()
    }
    fn resolve_key(&self, sheet: &str, address: &str) -> Option<String> {
        CommandTarget::resolve_key(&self.0, sheet, address)
    }
    fn read_cells(&self, keys: &[String]) -> Vec<Option<Cell>> {
        self.0.read_cells(keys)
    }
    fn apply_edits(&self, edits: Vec<Edit>, expected: u64) -> Result<u64, ContractError> {
        CommandTarget::apply_edits(&self.0, edits, expected)
    }
    fn calculate_report(&self) -> CalculationReport {
        self.0.calculate(1)
    }
}

const ROWS: usize = 6;

/// A literals, B and C formulas over A and B, so edits can form chains,
/// ranges, cycles and errors without inserting cells (which imports refuse).
fn workbook() -> ImportedWorkbook {
    let mut sheet = Sheet::new("Sheet1");
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
            .insert(format!("C{row}"), Cell::formula(format!("=SUM(B1:B{row})")));
    }
    let model = WorkbookModel {
        sheets: vec![sheet],
        ..WorkbookModel::default()
    };
    let bytes = export_xlsx_bytes(&model).unwrap();
    import_xlsx_bytes(&bytes, BTreeMap::new(), BTreeMap::new()).unwrap()
}

struct Lcg(u64);
impl Lcg {
    fn below(&mut self, n: usize) -> usize {
        self.0 = self
            .0
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        ((self.0 >> 33) as usize) % n
    }
    fn cell(&mut self) -> String {
        format!("{}{}", ["A", "B", "C"][self.below(3)], 1 + self.below(ROWS))
    }
}

fn random_edits(rng: &mut Lcg) -> Json {
    let edits: Vec<Json> = (0..1 + rng.below(2))
        .map(|_| {
            let address = rng.cell();
            let (x, y) = (rng.cell(), rng.cell());
            match rng.below(7) {
                0 | 1 => json!({"sheet": "Sheet1", "address": address, "value": rng.below(10) as f64}),
                2 => json!({"sheet": "Sheet1", "address": address, "formula": format!("={x}+1")}),
                3 => json!({"sheet": "Sheet1", "address": address, "formula": format!("=SUM({x}:{y})")}),
                4 => json!({"sheet": "Sheet1", "address": address, "formula": "=1/0"}),
                5 => json!({"sheet": "Sheet1", "address": address, "formula": format!("=NOPE({x})")}),
                _ => json!({"sheet": "Sheet1", "address": address, "formula": format!("=IF({x}>3,{y},0)")}),
            }
        })
        .collect();
    json!(edits)
}

fn envelope(command_id: &str, base: &str, edits: Json) -> String {
    json!({
        "v": 1, "command_id": command_id, "client_id": "tab-1", "client_seq": "1",
        "idempotency_key": null,
        "actor": {"kind": "human", "id": "user-1", "on_behalf_of": null},
        "base_revision": base, "mode": "strict", "preconditions": [],
        "op": {"type": "set_cells", "edits": edits},
    })
    .to_string()
}

/// Every cell's value from a fresh full calculation, plus literal inputs.
fn full_values(workbook: &ImportedWorkbook) -> BTreeMap<String, Json> {
    let model = workbook.snapshot();
    let report = serde_json::to_value(calculate(&model, 1)).unwrap();
    let mut values: BTreeMap<String, Json> = report["values"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect();
    for sheet in &model.sheets {
        for (address, cell) in &sheet.cells {
            values
                .entry(format!("{}!{address}", sheet.name))
                .or_insert_with(|| serde_json::to_value(&cell.value).unwrap());
        }
    }
    values
}

/// Timing and the random session id are the only fields that may differ.
fn without_timing(mut event: Json) -> Json {
    if let Some(object) = event.as_object_mut() {
        object.remove("calc_ms");
        object.remove("ts");
        object.remove("mono_ms");
        object.remove("session");
    }
    event
}

#[test]
fn incremental_delta_matches_the_full_diff_and_full_calculations() {
    let (mut applied, mut rejected, mut legacy) = (0, 0, 0);
    for seed in 0..20u64 {
        let mut rng = Lcg(seed);
        let fast = workbook();
        let slow = FullDiff(workbook());
        let (fast_session, slow_session) = (CommandSession::new(), CommandSession::new());
        for step in 0..40 {
            let current = revision_string(&fast);
            if rng.below(8) == 0 {
                // Moves the revision outside the command sessions.
                let edits = random_edits(&mut rng).to_string();
                let a = apply_edits_envelope(&fast, &edits, &current);
                let b = apply_edits_envelope(&slow.0, &edits, &current);
                assert_eq!(a, b, "seed {seed} step {step}");
                legacy += 1;
                continue;
            }
            // Occasionally a stale base, which strict mode rejects.
            let base = if rng.below(10) == 0 && current != "0" {
                "0".to_string()
            } else {
                current
            };
            let edits = random_edits(&mut rng);
            let targets: Vec<String> = edits
                .as_array()
                .unwrap()
                .iter()
                .map(|edit| format!("Sheet1!{}", edit["address"].as_str().unwrap()))
                .collect();
            let request = envelope(&format!("c{step}"), &base, edits);
            let before = full_values(&fast);
            let a: Json =
                serde_json::from_str(&fast_session.apply_and_calculate(&fast, &request)).unwrap();
            let b: Json =
                serde_json::from_str(&slow_session.apply_and_calculate(&slow, &request)).unwrap();
            assert_eq!(a, b, "seed {seed} step {step}");
            if a["status"] == "applied" {
                applied += 1;
                let after = full_values(&fast);
                let changed: BTreeMap<String, Json> = a["changed"]
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
                    .collect();
                // Every changed value is listed with its new value; anything
                // else listed is an edited cell (an input-only change).
                for (key, value) in &after {
                    if before.get(key) != Some(value) {
                        assert_eq!(
                            changed.get(key),
                            Some(value),
                            "seed {seed} step {step} {key}"
                        );
                    }
                }
                for (key, value) in &changed {
                    if before.get(key) == after.get(key) {
                        assert!(
                            targets.contains(key),
                            "seed {seed} step {step}: {key} listed without changing"
                        );
                    }
                    if let Some(after) = after.get(key) {
                        assert_eq!(value, after, "seed {seed} step {step} {key}");
                    }
                }
            } else {
                rejected += 1;
            }
        }
        let events = |session: &CommandSession| -> Vec<Json> {
            session.events().into_iter().map(without_timing).collect()
        };
        assert_eq!(events(&fast_session), events(&slow_session), "seed {seed}");
    }
    assert!(
        applied > 400 && rejected > 20 && legacy > 50,
        "{applied} applied, {rejected} rejected, {legacy} legacy"
    );
}
