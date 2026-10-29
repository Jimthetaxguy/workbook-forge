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
