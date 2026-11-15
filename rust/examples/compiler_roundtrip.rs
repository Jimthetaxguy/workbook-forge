//! Independent Rust OOXML / toolkit-IR benchmark adapter. No Python execution.
use std::collections::BTreeMap;
use workbook_forge::{CalculationReport, Session, WorkbookModel, toolkit, xlsx};

/// The model and the legacy `revision` a v1 document may still carry (0 when
/// absent); the model itself holds none.
fn read_ir(path: &str) -> Result<(WorkbookModel, u64), Box<dyn std::error::Error>> {
    use std::io::Read;
    let mut bytes = Vec::new();
    std::fs::File::open(path)?
        .take(16 * 1024 * 1024 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() > 16 * 1024 * 1024 {
        return Err("IR exceeds benchmark adapter's 16 MiB input limit".into());
    }
    let (model, revision) = toolkit::read_document(&bytes)?;
    model.validate()?;
    Ok((model, revision))
}

/// A stateless pass that reports the revision the document carried.
fn calculate(
    model: &WorkbookModel,
    revision: u64,
) -> Result<CalculationReport, Box<dyn std::error::Error>> {
    Ok(Session::with_names_at(model.clone(), None, revision)?.calculate(1))
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("decode") if args.len() == 3 => {
            let imported = xlsx::import_xlsx(&args[2], BTreeMap::new(), BTreeMap::new())?;
            println!("{}", serde_json::to_string(&imported.snapshot())?);
        }
        Some("calculate") if args.len() == 3 => {
            let (model, revision) = read_ir(&args[2])?;
            println!("{}", serde_json::to_string(&calculate(&model, revision)?)?);
        }
        Some("emit-fresh") if args.len() == 4 => {
            let (model, revision) = read_ir(&args[2])?;
            xlsx::export_xlsx(&model, &args[3])?;
            println!("{}", serde_json::to_string(&calculate(&model, revision)?)?);
        }
        Some("emit") if args.len() == 5 => {
            let (mut model, revision) = read_ir(&args[3])?;
            let report = calculate(&model, revision)?;
            let imported = xlsx::import_xlsx(&args[2], BTreeMap::new(), BTreeMap::new())?;
            // Existing readers assign different runtime-local sheet IDs. Cell
            // references use sheet names; bind IDs only after matching names/order.
            let baseline = imported.snapshot();
            if model.sheets.len() != baseline.sheets.len() {
                return Err("sheet count differs".into());
            }
            for (sheet, original) in model.sheets.iter_mut().zip(&baseline.sheets) {
                if sheet.name != original.name {
                    return Err("sheet identity differs".into());
                }
                sheet.id = original.id.clone();
            }
            imported.export_snapshot(model, &args[4])?;
            println!("{}", serde_json::to_string(&report)?);
        }
        Some("analyze") if args.len() == 3 => {
            let expression = workbook_forge::toolkit::Expression::parse(&args[2])?;
            println!(
                "{}",
                serde_json::json!({"functions": expression.functions()})
            );
        }
        _ => return Err(
            "usage: compiler_roundtrip decode INPUT | calculate IR | emit BASELINE IR OUTPUT | analyze FORMULA"
                .into(),
        ),
    }
    Ok(())
}
