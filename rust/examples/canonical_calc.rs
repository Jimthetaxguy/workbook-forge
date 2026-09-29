use serde::Deserialize;
use serde_json::{Value, json};
use std::collections::BTreeMap;
use workbook_forge::canonical_calc::CanonicalCalculationSession;
use workbook_forge::model::{CellValue, Workbook};

const MODEL_BYTES: &[u8] =
    include_bytes!("../../tests/fixtures/canonical/operating-scenario-v1.json");
const CASE_BYTES: &[u8] =
    include_bytes!("../../tests/fixtures/canonical/operating-scenario-cases.json");

#[derive(Deserialize)]
struct Cases {
    numeric_tolerance: f64,
    cases: Vec<Case>,
}

#[derive(Deserialize)]
struct Case {
    name: String,
    inputs: BTreeMap<String, CellValue>,
    expected: BTreeMap<String, Value>,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let workbook = Workbook::from_bytes(MODEL_BYTES)?;
    let cases: Cases = serde_json::from_slice(CASE_BYTES)?;
    let mut reports = Vec::new();
    for case in cases.cases {
        let session = CanonicalCalculationSession::new(workbook.clone())?;
        session.set_inputs(&case.inputs, Some(0))?;
        let report = session.calculate()?;
        reports.push(json!({
            "name": case.name,
            "inputs": case.inputs,
            "expected": case.expected,
            "outputs": report.outputs,
            "values": report.values,
            "diagnostics": report.diagnostics,
            "revision": report.revision,
        }));
    }
    println!(
        "{}",
        serde_json::to_string(&json!({
            "backend": "rust",
            "schema_version": workbook.schema_version,
            "model_version": workbook.model_version,
            "workbook": serde_json::from_slice::<Value>(&workbook.to_bytes()?)?,
            "numeric_tolerance": cases.numeric_tolerance,
            "cases": reports,
        }))?
    );
    Ok(())
}
