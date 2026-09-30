//! Print the canonical workbook JSON for a fixture.
//!
//! `canonical_calc FIXTURE hydrate` hydrates only.
//! `canonical_calc FIXTURE calculate` hydrates and calculates.

use std::env;
use std::fs;
use std::process::ExitCode;
use workbook_forge::model::{calculate, hydrate};

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
    let mut args = env::args().skip(1);
    let path = args
        .next()
        .ok_or("usage: canonical_calc FIXTURE [hydrate|calculate]")?;
    let mode = args.next().unwrap_or_else(|| "calculate".to_string());
    let workbook = hydrate(&fs::read(path)?)?;
    let workbook = match mode.as_str() {
        "hydrate" => workbook,
        "calculate" => calculate(&workbook)?,
        _ => return Err(format!("unknown mode {mode}").into()),
    };
    let document: serde_json::Value = serde_json::from_str(&workbook.to_json()?)?;
    println!(
        "{}",
        serde_json::to_string(&serde_json::json!({ "workbook": document }))?
    );
    Ok(())
}
