//! Each documented limit, tested at the limit and one step past it.
//!
//! Deliberately changing any of these comparisons by one used to leave every
//! test passing. The last worksheet cell is XFD1048576: column 16384, row
//! 1048576.
use std::collections::BTreeMap;
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};

use serde_json::{Value, json};
use workbook_forge::agent::{AgentWorkbook, MAX_REQUEST_BYTES};
use workbook_forge::toolkit::CellReference;
use workbook_forge::{operating_scenario, xlsx};

static NEXT: AtomicUsize = AtomicUsize::new(0);

struct Scratch(PathBuf);

impl Scratch {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "workbook-forge-limits-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&path).unwrap();
        Self(path)
    }

    fn path(&self, name: &str) -> PathBuf {
        self.0.join(name)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn a_reference_to_the_last_cell_is_accepted() {
    for address in ["A1", "XFD1", "A1048576", "XFD1048576"] {
        let reference = CellReference::parse(None, address).unwrap();
        assert!(reference.column <= 16_384 && reference.row <= 1_048_576);
    }
    let last = CellReference::parse(None, "XFD1048576").unwrap();
    assert_eq!((last.column, last.row), (16_384, 1_048_576));
}

#[test]
fn a_reference_past_the_last_cell_is_refused() {
    for address in ["XFE1", "A1048577", "XFE1048577"] {
        let error = CellReference::parse(None, address).unwrap_err();
        assert_eq!(error.code, "invalid_reference", "{address}");
    }
}

/// A request whose serialized form is exactly `length` bytes.
fn request_of(length: usize) -> Value {
    let empty = json!({"schema_version": 1, "id": "limit", "operation": "inspect", "arguments": {"padding": ""}});
    let base = serde_json::to_vec(&empty).unwrap().len();
    let request = json!({
        "schema_version": 1,
        "id": "limit",
        "operation": "inspect",
        "arguments": {"padding": "x".repeat(length - base)},
    });
    assert_eq!(serde_json::to_vec(&request).unwrap().len(), length);
    request
}

fn error_of(response: &Value) -> (String, String) {
    (
        response["error"]["code"].as_str().unwrap_or("").to_owned(),
        response["error"]["message"]
            .as_str()
            .unwrap_or("")
            .to_owned(),
    )
}

#[test]
fn a_request_of_exactly_the_limit_is_read() {
    let scratch = Scratch::new();
    let agent = AgentWorkbook::new(operating_scenario(), scratch.path("out")).unwrap();
    let (_, message) = error_of(&agent.handle(request_of(MAX_REQUEST_BYTES)));
    assert_ne!(message, "request exceeds 1 MiB");
}

#[test]
fn a_request_one_byte_over_the_limit_is_refused() {
    let scratch = Scratch::new();
    let agent = AgentWorkbook::new(operating_scenario(), scratch.path("out")).unwrap();
    let response = agent.handle(request_of(MAX_REQUEST_BYTES + 1));
    assert_eq!(response["ok"], json!(false));
    assert_eq!(
        error_of(&response),
        (
            "resource_limit".to_owned(),
            "request exceeds 1 MiB".to_owned()
        )
    );
}

/// Export the scenario, then rewrite its first worksheet to declare one
/// column range ending at `last_column`.
fn workbook_with_column_range(scratch: &Scratch, last_column: u32) -> PathBuf {
    let exported = xlsx::export_xlsx(&operating_scenario(), scratch.path("exported.xlsx")).unwrap();
    let mut archive = zip::ZipArchive::new(fs::File::open(&exported).unwrap()).unwrap();
    let rewritten = scratch.path(&format!("columns-{last_column}.xlsx"));
    let mut writer = zip::ZipWriter::new(fs::File::create(&rewritten).unwrap());
    let options =
        zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
    let mut changed = 0;
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index).unwrap();
        let name = entry.name().to_owned();
        let mut bytes = Vec::new();
        entry.read_to_end(&mut bytes).unwrap();
        if name == "xl/worksheets/sheet1.xml" {
            let text = String::from_utf8(bytes).unwrap();
            let columns = format!(
                "<cols><col min=\"{last_column}\" max=\"{last_column}\" width=\"9\" customWidth=\"1\"/></cols>"
            );
            let text = match (text.find("<cols>"), text.find("</cols>")) {
                (Some(start), Some(end)) => {
                    format!(
                        "{}{columns}{}",
                        &text[..start],
                        &text[end + "</cols>".len()..]
                    )
                }
                _ => text.replacen("<sheetData>", &format!("{columns}<sheetData>"), 1),
            };
            assert!(text.contains(&columns));
            bytes = text.into_bytes();
            changed += 1;
        }
        writer.start_file(name, options).unwrap();
        writer.write_all(&bytes).unwrap();
    }
    writer.finish().unwrap();
    assert_eq!(
        changed, 1,
        "the export should hold xl/worksheets/sheet1.xml"
    );
    rewritten
}

fn import(path: &Path) -> Result<xlsx::ImportedWorkbook, xlsx::XlsxError> {
    xlsx::import_xlsx(path, BTreeMap::new(), BTreeMap::new())
}

#[test]
fn a_column_range_ending_at_the_last_column_is_imported() {
    let scratch = Scratch::new();
    assert!(import(&workbook_with_column_range(&scratch, 16_384)).is_ok());
}

#[test]
fn a_column_range_ending_past_the_last_column_is_refused() {
    let scratch = Scratch::new();
    let error = import(&workbook_with_column_range(&scratch, 16_385))
        .err()
        .expect("column 16385 is outside the sheet");
    assert!(
        error.to_string().contains("invalid column range"),
        "{error}"
    );
}
