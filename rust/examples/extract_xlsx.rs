//! Standalone formula-aware extraction; no Python interpreter is involved.
use workbook_forge::extraction::{ExtractionOptions, extract_xlsx, pattern_catalog};
fn run() -> Result<serde_json::Value, String> {
    let mut args = std::env::args().skip(1);
    let mut path = None;
    let mut options = ExtractionOptions::default();
    let mut catalog = false;
    let mut offset = false;
    let mut limit = false;
    while let Some(argument) = args.next() {
        match argument.as_str() {
            "--catalog" if !catalog=>catalog=true,
            "--pattern"=>options.patterns.get_or_insert_with(Vec::new).push(args.next().ok_or("--pattern requires an ID")?),
            "--sheet" if options.sheet.is_none()=>options.sheet=Some(args.next().ok_or("--sheet requires a name")?),
            "--offset" if !offset=>{offset=true;options.offset=args.next().ok_or("--offset requires an integer")?.parse().map_err(|_|"invalid offset")?;},
            "--limit" if !limit=>{limit=true;options.limit=args.next().ok_or("--limit requires an integer")?.parse().map_err(|_|"invalid limit")?;},
            _ if !argument.starts_with('-')&&path.is_none()=>path=Some(argument),
            _=>return Err("usage: extract_xlsx PATH [--pattern ID] [--sheet NAME] [--offset N] [--limit N] | --catalog".into()),
        }
    }
    if catalog {
        if path.is_some()
            || options.patterns.is_some()
            || options.sheet.is_some()
            || offset
            || limit
        {
            return Err("--catalog cannot be combined with extraction options".into());
        }
        return Ok(pattern_catalog());
    }
    extract_xlsx(path.ok_or("input XLSX is required")?, options).map_err(|error| error.to_string())
}
fn main() {
    match run() {
        Ok(report) => println!("{report}"),
        Err(message) => {
            eprintln!(
                "{}",
                serde_json::json!({"error":{"code":"extraction_error","message":message}})
            );
            std::process::exit(1);
        }
    }
}
