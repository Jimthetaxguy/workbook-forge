//! Run with `cargo run --example operating_scenario -- [input-overrides.json]`.
use std::collections::BTreeMap;
use workbook_forge::{CellValue, Session, operating_scenario, validate_inputs};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let model = operating_scenario();
    let overrides: BTreeMap<String, CellValue> = match std::env::args().nth(1) {
        Some(path) => serde_json::from_str(&std::fs::read_to_string(path)?)?,
        None => BTreeMap::new(),
    };
    let edits = validate_inputs(&model, &overrides)?;
    let session = Session::new(model)?;
    session.apply(edits, Some(0))?;
    let report = session.calculate(1);
    println!("{}", serde_json::to_string_pretty(&report)?);
    if !report.diagnostics.is_empty() {
        std::process::exit(1);
    }
    Ok(())
}
