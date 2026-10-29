//! CSV and TSV import into a fresh workbook model (docs/csv-import.md).
//!
//! The same rules as `python/workbook_forge/delimited.py`; both engines run
//! the shared case table in `tools/corpus/csv-cases.json`. Decoding failures
//! refuse with `csv_encoding`, syntax errors with `csv_malformed`.
use crate::toolkit::{Cell, CellValue, Sheet, WorkbookModel};
use crate::xlsx::XlsxError;
use std::path::Path;

pub const MAX_INPUT_BYTES: usize = 128 * 1024 * 1024;
pub const MAX_ROWS: usize = 1_048_576;
pub const MAX_COLUMNS: usize = 16_384;
pub const DELIMITERS: [char; 4] = [',', '\t', ';', '|'];

type Result<T> = std::result::Result<T, XlsxError>;

fn refuse(code: &str, message: impl AsRef<str>) -> XlsxError {
    XlsxError(format!("{code}: {}", message.as_ref()))
}

/// Options for [`import_delimited`]. `delimiter: None` means a comma.
#[derive(Clone, Debug)]
pub struct DelimitedOptions {
    pub delimiter: Option<char>,
    pub encoding: Option<String>,
    pub sheet_name: String,
    pub as_text: bool,
}
impl Default for DelimitedOptions {
    fn default() -> Self {
        Self {
            delimiter: None,
            encoding: None,
            sheet_name: "Sheet1".into(),
            as_text: false,
        }
    }
}

fn xml_storable(c: char) -> bool {
    match c as u32 {
        0x09 | 0x0A | 0x0D => true,
        p if p < 0x20 => false,
        0xFFFE | 0xFFFF => false,
        _ => true,
    }
}

fn decode_utf16(data: &[u8], little: bool, name: &str) -> Result<String> {
    if data.len() % 2 == 1 {
        return Err(refuse(
            "csv_encoding",
            format!(
                "bytes do not decode as {name} at byte offset {}",
                data.len() - 1
            ),
        ));
    }
    let (pairs, _) = data.as_chunks::<2>();
    let units = pairs.iter().map(|&pair| {
        if little {
            u16::from_le_bytes(pair)
        } else {
            u16::from_be_bytes(pair)
        }
    });
    char::decode_utf16(units)
        .collect::<std::result::Result<String, _>>()
        .map_err(|_| {
            refuse(
                "csv_encoding",
                format!("bytes do not decode as {name} (unpaired surrogate)"),
            )
        })
}

/// Decode delimited text by the module's encoding rules.
pub fn decode(data: &[u8], encoding: Option<&str>) -> Result<String> {
    if data.len() > MAX_INPUT_BYTES {
        return Err(refuse(
            "package_limit",
            "delimited text exceeds the 128 MiB input limit",
        ));
    }
    let (name, body): (&str, &[u8]) = match encoding {
        None => {
            if let Some(rest) = data.strip_prefix(b"\xef\xbb\xbf") {
                ("utf-8", rest)
            } else if let Some(rest) = data.strip_prefix(b"\xff\xfe") {
                ("utf-16-le", rest)
            } else if let Some(rest) = data.strip_prefix(b"\xfe\xff") {
                ("utf-16-be", rest)
            } else {
                ("utf-8", data)
            }
        }
        Some(requested) => {
            let key = requested.trim().to_lowercase().replace('_', "-");
            let name = match key.as_str() {
                "utf-8" | "utf8" => "utf-8",
                "utf-16" | "utf16" => "utf-16",
                "utf-16-le" | "utf-16le" => "utf-16-le",
                "utf-16-be" | "utf-16be" => "utf-16-be",
                "latin-1" | "latin1" | "iso-8859-1" => "latin-1",
                _ => {
                    return Err(refuse(
                        "csv_encoding",
                        format!("unsupported encoding {requested:?}"),
                    ));
                }
            };
            match name {
                "utf-8" => ("utf-8", data.strip_prefix(b"\xef\xbb\xbf").unwrap_or(data)),
                "utf-16" => {
                    if let Some(rest) = data.strip_prefix(b"\xff\xfe") {
                        ("utf-16-le", rest)
                    } else if let Some(rest) = data.strip_prefix(b"\xfe\xff") {
                        ("utf-16-be", rest)
                    } else {
                        return Err(refuse(
                            "csv_encoding",
                            "utf-16 text needs a byte-order mark",
                        ));
                    }
                }
                "utf-16-le" => ("utf-16-le", data.strip_prefix(b"\xff\xfe").unwrap_or(data)),
                "utf-16-be" => ("utf-16-be", data.strip_prefix(b"\xfe\xff").unwrap_or(data)),
                _ => ("latin-1", data),
            }
        }
    };
    match name {
        "utf-8" => std::str::from_utf8(body).map(str::to_owned).map_err(|e| {
            refuse(
                "csv_encoding",
                format!(
                    "bytes do not decode as utf-8 at byte offset {}",
                    e.valid_up_to()
                ),
            )
        }),
        "utf-16-le" => decode_utf16(body, true, name),
        "utf-16-be" => decode_utf16(body, false, name),
        _ => Ok(body.iter().map(|&b| char::from(b)).collect()),
    }
}

fn unstorable(c: char, line: usize) -> XlsxError {
    refuse(
        "csv_malformed",
        format!(
            "character U+{:04X} on line {line} cannot be stored in a workbook",
            c as u32
        ),
    )
}

/// Split decoded text into records of fields by the module's syntax rules.
pub fn parse(text: &str, delimiter: char) -> Result<Vec<Vec<String>>> {
    if !DELIMITERS.contains(&delimiter) {
        return Err(XlsxError(format!(
            "delimiter must be one of {DELIMITERS:?}"
        )));
    }
    let chars: Vec<char> = text.chars().collect();
    let length = chars.len();
    let mut records: Vec<Vec<String>> = Vec::new();
    let mut record: Vec<String> = Vec::new();
    let mut field = String::new();
    let mut line = 1usize;
    let mut index = 0usize;
    let mut at_field_start = true;
    while index < length {
        let c = chars[index];
        if at_field_start && c == '"' {
            let start_line = line;
            index += 1;
            loop {
                if index >= length {
                    return Err(refuse(
                        "csv_malformed",
                        format!("unterminated quoted field starting on line {start_line}"),
                    ));
                }
                let c = chars[index];
                if c == '"' {
                    if chars.get(index + 1) == Some(&'"') {
                        field.push('"');
                        index += 2;
                        continue;
                    }
                    index += 1;
                    break;
                }
                if c == '\n' || (c == '\r' && chars.get(index + 1) != Some(&'\n')) {
                    line += 1;
                }
                if !xml_storable(c) {
                    return Err(unstorable(c, line));
                }
                field.push(c);
                index += 1;
            }
            if let Some(&next) = chars.get(index)
                && next != delimiter
                && next != '\r'
                && next != '\n'
            {
                return Err(refuse(
                    "csv_malformed",
                    format!("unexpected text after a closing quote on line {line}"),
                ));
            }
            at_field_start = false;
            continue;
        }
        if c == delimiter {
            record.push(std::mem::take(&mut field));
            at_field_start = true;
            index += 1;
            continue;
        }
        if c == '\r' || c == '\n' {
            record.push(std::mem::take(&mut field));
            records.push(std::mem::take(&mut record));
            at_field_start = true;
            index += if c == '\r' && chars.get(index + 1) == Some(&'\n') {
                2
            } else {
                1
            };
            line += 1;
            continue;
        }
        if !xml_storable(c) {
            return Err(unstorable(c, line));
        }
        field.push(c);
        at_field_start = false;
        index += 1;
    }
    if !field.is_empty() || !record.is_empty() || !at_field_start {
        record.push(field);
        records.push(record);
    }
    Ok(records)
}

fn plain_number(text: &str) -> bool {
    let b = text.as_bytes();
    let mut i = 0;
    if b.get(i) == Some(&b'-') {
        i += 1;
    }
    match b.get(i) {
        Some(b'0') => i += 1,
        Some(b'1'..=b'9') => {
            while matches!(b.get(i), Some(b'0'..=b'9')) {
                i += 1;
            }
        }
        _ => return false,
    }
    if b.get(i) == Some(&b'.') {
        i += 1;
        let start = i;
        while matches!(b.get(i), Some(b'0'..=b'9')) {
            i += 1;
        }
        if i == start {
            return false;
        }
    }
    if matches!(b.get(i), Some(b'e' | b'E')) {
        i += 1;
        if matches!(b.get(i), Some(b'+' | b'-')) {
            i += 1;
        }
        let start = i;
        while matches!(b.get(i), Some(b'0'..=b'9')) {
            i += 1;
        }
        if i == start {
            return false;
        }
    }
    i == b.len()
}

/// The cell value of one field: `None` (absent), a number or the text itself.
pub fn field_value(text: &str, as_text: bool) -> Option<CellValue> {
    if text.is_empty() {
        return None;
    }
    if as_text || !plain_number(text) {
        return Some(CellValue::Text(text.to_owned()));
    }
    let mantissa = text.split(['e', 'E']).next().unwrap_or(text);
    let digits = mantissa
        .trim_start_matches('-')
        .replace('.', "")
        .trim_start_matches('0')
        .len();
    if digits > 15 {
        return Some(CellValue::Text(text.to_owned()));
    }
    match text.parse::<f64>() {
        Ok(n) if n.is_finite() => Some(CellValue::Number(if n == 0.0 { 0.0 } else { n })),
        _ => Some(CellValue::Text(text.to_owned())),
    }
}

fn column_name(mut column: usize) -> String {
    let mut name = Vec::new();
    while column > 0 {
        let remainder = (column - 1) % 26;
        name.push(b'A' + remainder as u8);
        column = (column - 1) / 26;
    }
    name.reverse();
    String::from_utf8(name).unwrap_or_default()
}

/// The version 1 workbook model for parsed records.
pub fn to_model(records: &[Vec<String>], sheet_name: &str, as_text: bool) -> Result<WorkbookModel> {
    if records.len() > MAX_ROWS {
        return Err(refuse(
            "out_of_bounds",
            format!("{} rows exceed the {MAX_ROWS}-row grid", records.len()),
        ));
    }
    let mut sheet = Sheet::new(sheet_name);
    sheet.id = "sheet-1".into();
    for (row, record) in records.iter().enumerate() {
        let row = row + 1;
        if record.len() > MAX_COLUMNS {
            return Err(refuse(
                "out_of_bounds",
                format!(
                    "line {row} has {} fields, more than the {MAX_COLUMNS}-column grid",
                    record.len()
                ),
            ));
        }
        for (column, text) in record.iter().enumerate() {
            if let Some(value) = field_value(text, as_text) {
                sheet.cells.insert(
                    format!("{}{row}", column_name(column + 1)),
                    Cell::value(value),
                );
            }
        }
    }
    let model = WorkbookModel {
        sheets: vec![sheet],
        ..WorkbookModel::default()
    };
    model.validate().map_err(XlsxError::from)?;
    Ok(model)
}

/// Read CSV or TSV bytes into a new one-sheet workbook model. Export it with
/// [`crate::xlsx::export_xlsx`] or [`crate::xlsx::export_xlsx_bytes`].
pub fn import_delimited(data: &[u8], options: &DelimitedOptions) -> Result<WorkbookModel> {
    let text = decode(data, options.encoding.as_deref())?;
    let records = parse(&text, options.delimiter.unwrap_or(','))?;
    to_model(&records, &options.sheet_name, options.as_text)
}

/// [`import_delimited`] for a file. With no delimiter, `.tsv` and `.tab`
/// files use a tab and anything else a comma.
pub fn import_delimited_path(
    path: impl AsRef<Path>,
    options: &DelimitedOptions,
) -> Result<WorkbookModel> {
    let path = path.as_ref();
    if std::fs::metadata(path)?.len() > MAX_INPUT_BYTES as u64 {
        return Err(refuse(
            "package_limit",
            "delimited text exceeds the 128 MiB input limit",
        ));
    }
    let data = std::fs::read(path)?;
    let mut options = options.clone();
    if options.delimiter.is_none() {
        let tab = path
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| matches!(e.to_ascii_lowercase().as_str(), "tsv" | "tab"))
            .unwrap_or(false);
        options.delimiter = Some(if tab { '\t' } else { ',' });
    }
    import_delimited(&data, &options)
}
