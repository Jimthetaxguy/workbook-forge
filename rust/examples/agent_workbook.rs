//! One host-selected workbook, exposed through bounded JSON-lines operations.
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::Deserialize;
use workbook_forge::agent::{AgentWorkbook, serve_json_lines};
use workbook_forge::{CellAddress, InputBinding, WorkbookModel, operating_scenario, xlsx};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Bindings {
    #[serde(default)]
    inputs: BTreeMap<String, InputBinding>,
    #[serde(default)]
    outputs: BTreeMap<String, CellAddress>,
}
fn read_bounded(path: &Path, limit: u64) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    use std::io::Read;
    let mut bytes = Vec::new();
    std::fs::File::open(path)?
        .take(limit + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > limit {
        return Err("startup file exceeds its size limit".into());
    }
    Ok(bytes)
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut source = None;
    let mut bindings = None;
    let mut output_dir = None;
    let mut scenario = false;
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--scenario" if !scenario => scenario = true,
            "--output-dir" if output_dir.is_none() => output_dir = Some(PathBuf::from(args.next().ok_or("--output-dir requires a directory")?)),
            "--bindings" if bindings.is_none() => bindings = Some(PathBuf::from(args.next().ok_or("--bindings requires a JSON file")?)),
            _ if !arg.starts_with('-') && source.is_none() => source = Some(PathBuf::from(arg)),
            _ => return Err("usage: agent_workbook (--scenario | SOURCE [--bindings BINDINGS]) --output-dir DIRECTORY".into()),
        }
    }
    let output_dir = output_dir.ok_or("--output-dir is required")?;
    if scenario == source.is_some() || (scenario && bindings.is_some()) {
        return Err("choose --scenario or a source workbook".into());
    }
    let bindings = bindings
        .map(|path| -> Result<Bindings, Box<dyn std::error::Error>> {
            Ok(serde_json::from_slice(&read_bounded(&path, 1024 * 1024)?)?)
        })
        .transpose()?;
    let agent = if scenario {
        AgentWorkbook::new(operating_scenario(), output_dir)?
    } else {
        let source = source.expect("source checked above");
        if source
            .extension()
            .is_some_and(|extension| extension.eq_ignore_ascii_case("xlsx"))
        {
            let bindings = bindings.ok_or("XLSX source requires explicit --bindings")?;
            AgentWorkbook::from_imported(
                xlsx::import_xlsx(source, bindings.inputs, bindings.outputs)?,
                output_dir,
            )?
        } else {
            let mut model: WorkbookModel =
                serde_json::from_slice(&read_bounded(&source, 128 * 1024 * 1024)?)?;
            if let Some(bindings) = bindings {
                model.inputs = bindings.inputs;
                model.outputs = bindings.outputs;
            }
            AgentWorkbook::new(model, output_dir)?
        }
    };
    serve_json_lines(&agent, std::io::stdin().lock(), std::io::stdout().lock())?;
    Ok(())
}
