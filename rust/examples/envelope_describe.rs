//! Print each imported workbook's preservation envelope projection, one JSON
//! line per path argument, for the cross-language agreement test.
//!
//! `envelope_describe PATH...` prints `{"index": i, "describe": {...}}` for a
//! workbook the importer accepts and `{"index": i, "error": "<code>"}` for one
//! it refuses, then exits 0 once every argument was processed. The envelope's
//! handle is never printed.

use std::collections::BTreeMap;
use std::env;
use std::io::Write;
use std::process::ExitCode;
use workbook_forge::xlsx::import_xlsx;

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{error}");
            ExitCode::from(1)
        }
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let paths: Vec<String> = env::args().skip(1).collect();
    if paths.is_empty() {
        return Err("usage: envelope_describe PATH...".into());
    }
    let stdout = std::io::stdout();
    let mut out = stdout.lock();
    for (index, path) in paths.iter().enumerate() {
        let line = match import_xlsx(path, BTreeMap::new(), BTreeMap::new()) {
            Ok(imported) => {
                serde_json::json!({ "index": index, "describe": imported.envelope().describe() })
            }
            Err(error) => serde_json::json!({ "index": index, "error": error.code() }),
        };
        writeln!(out, "{}", serde_json::to_string(&line)?)?;
    }
    Ok(())
}
