//! Every corpus workbook opens from bytes (the WASM entry point) without a
//! panic: it either imports or refuses with an error. The full round trip is
//! checked by python/tests/test_corpus.py through examples/corpus_roundtrip.
use std::collections::BTreeMap;
use std::panic::{AssertUnwindSafe, catch_unwind};
use workbook_forge::xlsx::import_xlsx_bytes;

#[test]
fn corpus_imports_or_refuses_without_panicking() {
    let folder = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../fixtures/corpus");
    let mut seen = 0;
    for entry in std::fs::read_dir(&folder).expect("corpus folder") {
        let path = entry.expect("entry").path();
        let extension = path.extension().and_then(|s| s.to_str()).unwrap_or("");
        if !matches!(extension, "xlsx" | "xlsm") {
            continue;
        }
        seen += 1;
        let bytes = std::fs::read(&path).expect("read");
        let outcome = catch_unwind(AssertUnwindSafe(|| {
            match import_xlsx_bytes(&bytes, BTreeMap::new(), BTreeMap::new()) {
                Ok(imported) => {
                    let _ = imported.calculate(1);
                    let _ = imported.export_bytes();
                }
                Err(error) => assert!(!error.code().is_empty()),
            }
        }));
        assert!(outcome.is_ok(), "{} panicked", path.display());
    }
    assert!(seen >= 50, "corpus has {seen} workbooks");
}

fn corpus(name: &str) -> Vec<u8> {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../fixtures/corpus")
        .join(name);
    std::fs::read(path).expect("corpus file")
}

#[test]
fn chartsheets_are_listed_and_preserved_byte_for_byte() {
    let bytes = corpus("xw_chartsheet.xlsx");
    let imported = import_xlsx_bytes(&bytes, BTreeMap::new(), BTreeMap::new()).expect("import");
    let chartsheets = imported.capabilities()["chartsheets"]
        .as_object()
        .expect("chartsheets listed");
    assert_eq!(chartsheets.len(), 1);
    let part = chartsheets
        .values()
        .next()
        .unwrap()
        .as_str()
        .unwrap()
        .to_string();
    let exported = imported.export_bytes().expect("export");
    let read = |data: &[u8]| {
        let mut archive = zip::ZipArchive::new(std::io::Cursor::new(data.to_vec())).unwrap();
        let mut file = archive.by_name(&part).unwrap();
        let mut out = Vec::new();
        std::io::Read::read_to_end(&mut file, &mut out).unwrap();
        out
    };
    assert_eq!(read(&bytes), read(&exported));
}

#[test]
fn newer_error_values_refuse_with_their_code() {
    let error = import_xlsx_bytes(
        &corpus("hb_errors_newer_values.xlsx"),
        BTreeMap::new(),
        BTreeMap::new(),
    )
    .err()
    .expect("refused");
    assert_eq!(error.code(), "unsupported_error_value");
    import_xlsx_bytes(
        &corpus("hb_errors_as_values.xlsx"),
        BTreeMap::new(),
        BTreeMap::new(),
    )
    .expect("the v1 error set imports");
}

#[test]
fn byte_order_marked_parts_export_valid_xml() {
    let imported = import_xlsx_bytes(
        &corpus("hb_bom_part.xlsx"),
        BTreeMap::new(),
        BTreeMap::new(),
    )
    .expect("import");
    let exported = imported.export_bytes().expect("export");
    import_xlsx_bytes(&exported, BTreeMap::new(), BTreeMap::new()).expect("reimport");
}
