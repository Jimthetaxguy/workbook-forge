//! Independently generate, inspect, or edit a real XLSX using Rust alone.
use std::collections::BTreeMap;
use workbook_forge::{CellValue, operating_scenario, xlsx};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().collect();
    let scenario = operating_scenario();
    match args.get(1).map(String::as_str) {
        Some("generate") if args.len() == 3 => {
            xlsx::export_xlsx(&scenario, &args[2])?;
            println!(
                "{}",
                serde_json::to_string(&workbook_forge::calculate(&scenario, 1))?
            );
        }
        Some("inspect") if args.len() == 3 => {
            let imported = xlsx::import_xlsx(&args[2], scenario.inputs, scenario.outputs)?;
            println!(
                "{}",
                serde_json::json!({"model":imported.snapshot(),"report":imported.calculate(1),"capabilities":imported.capabilities()})
            );
        }
        Some("edit") if (4..=5).contains(&args.len()) => {
            let imported = xlsx::import_xlsx(&args[2], scenario.inputs, scenario.outputs)?;
            let price = args
                .get(4)
                .map(|s| s.parse::<f64>())
                .transpose()?
                .unwrap_or(25.0);
            imported.set_inputs(
                &BTreeMap::from([("unit_price".into(), CellValue::Number(price))]),
                None,
            )?;
            imported.export(&args[3])?;
            println!("{}", serde_json::to_string(&imported.calculate(1))?);
        }
        _ => return Err(
            "usage: xlsx_scenario generate OUTPUT | inspect INPUT | edit INPUT OUTPUT [UNIT_PRICE]"
                .into(),
        ),
    }
    Ok(())
}
