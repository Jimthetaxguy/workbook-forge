//! Built-in number formats are named when a workbook is read.
//!
//! Excel writes a built-in format as a bare id with no format text, so the
//! reader has to know the text. The ids and texts below are from ECMA-376
//! part 1, 18.8.30, not from the reader.
use std::collections::BTreeMap;
use std::fs;
use std::io::{Read, Write};
use std::path::PathBuf;

use workbook_forge::{operating_scenario, xlsx};

/// Export the scenario, then point every cell style at one built-in format.
fn import_with_builtin_format(id: u32) -> Vec<Option<String>> {
    let folder: PathBuf = std::env::temp_dir().join(format!(
        "workbook-forge-formats-{}-{id}",
        std::process::id()
    ));
    fs::create_dir_all(&folder).unwrap();
    let exported = xlsx::export_xlsx(&operating_scenario(), folder.join("exported.xlsx")).unwrap();
    let mut archive = zip::ZipArchive::new(fs::File::open(&exported).unwrap()).unwrap();
    let rewritten = folder.join("rewritten.xlsx");
    let mut writer = zip::ZipWriter::new(fs::File::create(&rewritten).unwrap());
    let options =
        zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
    let mut styled = 0;
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index).unwrap();
        let name = entry.name().to_owned();
        let mut bytes = Vec::new();
        entry.read_to_end(&mut bytes).unwrap();
        if name == "xl/styles.xml" {
            let text = String::from_utf8(bytes).unwrap();
            let (before, cell_styles) = text.split_once("<cellXfs").unwrap();
            let (cell_styles, after) = cell_styles.split_once("</cellXfs>").unwrap();
            let mut changed = String::new();
            for (position, piece) in cell_styles.split("<xf numFmtId=\"").enumerate() {
                if position == 0 {
                    changed.push_str(piece);
                    continue;
                }
                let (_, rest) = piece.split_once('"').unwrap();
                changed.push_str(&format!("<xf numFmtId=\"{id}\"{rest}"));
                styled += 1;
            }
            bytes = format!("{before}<cellXfs{changed}</cellXfs>{after}").into_bytes();
        }
        writer.start_file(name, options).unwrap();
        writer.write_all(&bytes).unwrap();
    }
    writer.finish().unwrap();
    assert!(styled > 0, "the export should hold cell styles");

    let imported = xlsx::import_xlsx(&rewritten, BTreeMap::new(), BTreeMap::new()).unwrap();
    let formats = imported
        .snapshot()
        .sheets
        .iter()
        .flat_map(|sheet| sheet.cells.values())
        .filter_map(|cell| cell.style.as_ref())
        .map(|style| style.number_format.clone())
        .collect();
    let _ = fs::remove_dir_all(&folder);
    formats
}

#[test]
fn a_built_in_format_that_is_the_same_everywhere_is_named() {
    for (id, text) in [
        (3, "#,##0"),
        (4, "#,##0.00"),
        (11, "0.00E+00"),
        (15, "d-mmm-yy"),
        (18, "h:mm AM/PM"),
        (22, "m/d/yy h:mm"),
        (38, "#,##0 ;[Red](#,##0)"),
        (46, "[h]:mm:ss"),
        (49, "@"),
    ] {
        let formats = import_with_builtin_format(id);
        assert!(!formats.is_empty(), "format {id}");
        assert!(
            formats.iter().all(|format| format.as_deref() == Some(text)),
            "format {id}: {formats:?}"
        );
    }
}

#[test]
fn a_built_in_format_that_depends_on_the_locale_is_not_guessed() {
    for id in [5, 7, 27, 41, 44] {
        let formats = import_with_builtin_format(id);
        assert!(
            formats.iter().all(Option::is_none),
            "format {id}: {formats:?}"
        );
    }
}
