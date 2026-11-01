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

#[test]
fn rows_and_cells_without_references_refuse_with_the_shared_message() {
    for name in [
        "hb_no_cell_references.xlsx",
        "hb_rows_without_references.xlsx",
    ] {
        let error = import_xlsx_bytes(&corpus(name), BTreeMap::new(), BTreeMap::new())
            .err()
            .expect("refused");
        assert_eq!(error.code(), "positional_cells", "{name}");
        assert_eq!(
            error.to_string(),
            workbook_forge::xlsx::POSITIONAL_CELLS_MESSAGE,
            "{name}"
        );
    }
}

#[test]
fn three_d_references_are_typed_as_not_calculated() {
    let imported = import_xlsx_bytes(
        &corpus("hb_3d_references.xlsx"),
        BTreeMap::new(),
        BTreeMap::new(),
    )
    .expect("import");
    let report = imported.calculate(1);
    for (address, span) in [
        ("A1", "Jan:Mar"),
        ("B1", "Q2 Apr:Q2 May"),
        ("C1", "Jan:Mar"),
        ("E1", "Jan:Feb"),
    ] {
        let diagnostic = report
            .diagnostics
            .iter()
            .find(|d| {
                d.sheet.as_deref() == Some("Summary") && d.address.as_deref() == Some(address)
            })
            .unwrap_or_else(|| {
                panic!(
                    "no diagnostic for Summary!{address}: {:?}",
                    report.diagnostics
                )
            });
        assert_eq!(diagnostic.code, "unsupported_formula", "{address}");
        assert!(
            diagnostic.message.contains(&format!(
                "3D reference {span}: a reference across a range of sheets is not calculated"
            )),
            "{address}: {}",
            diagnostic.message
        );
    }
}

// Excel's lists, arrays in cells and nested arrays (Excel Beta, September
// 2026). The corpus files are modeled, not captured from Excel; see
// tools/corpus/in_cell_arrays.py. Python holds the same files to the same
// outcomes in python/tests/test_in_cell_arrays.py.

fn zip_parts(bytes: &[u8]) -> BTreeMap<String, Vec<u8>> {
    use std::io::Read;
    let mut archive = zip::ZipArchive::new(std::io::Cursor::new(bytes)).expect("zip");
    let mut parts = BTreeMap::new();
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index).expect("entry");
        let mut data = Vec::new();
        entry.read_to_end(&mut data).expect("read entry");
        parts.insert(entry.name().to_string(), data);
    }
    parts
}

fn cell_xml(sheet: &[u8], address: &str) -> Vec<u8> {
    let text = String::from_utf8_lossy(sheet);
    let start = text
        .find(&format!("<c r=\"{address}\""))
        .unwrap_or_else(|| panic!("no cell {address}"));
    let rest = &text[start..];
    let end = rest.find("</c>").map(|i| i + 4).expect("cell end");
    rest.as_bytes()[..end].to_vec()
}

#[test]
fn a_cell_holding_an_array_is_refused_with_one_typed_code() {
    for (name, sheet, address) in [
        ("hb_in_cell_list.xlsx", "Projects", "B2"),
        ("hb_in_cell_array_formula.xlsx", "Splits", "A1"),
        ("hb_nested_array.xlsx", "Nested", "A1"),
    ] {
        let error = match import_xlsx_bytes(&corpus(name), BTreeMap::new(), BTreeMap::new()) {
            Ok(_) => panic!("{name} imported"),
            Err(error) => error,
        };
        assert_eq!(error.code(), "in_cell_array", "{name}");
        assert_eq!(
            error.to_string(),
            format!(
                "in_cell_array: {}",
                workbook_forge::xlsx::in_cell_array_message(sheet, address)
            )
        );
        // The browser reports the same code with the prefix split off.
        assert_eq!(
            workbook_forge::browser_contract::typed_open_error(&error),
            error.to_string()
        );
    }
}

#[test]
fn in_cell_array_syntax_matches_the_shared_cases() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../tools/corpus/in-cell-array-cases.json");
    let cases: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(path).expect("cases")).expect("json");
    let cases = cases["formulas"].as_array().expect("formulas");
    assert!(cases.len() >= 20);
    for case in cases {
        let formula = case["formula"].as_str().expect("formula");
        let expected = case["message"].as_str().map(str::to_string);
        assert_eq!(
            workbook_forge::in_cell_array_syntax(formula),
            expected,
            "{formula}"
        );
    }
}

#[test]
fn new_functions_are_typed_not_calculated_and_preserved() {
    use workbook_forge::toolkit::{CellAddress, CellValue, Edit};
    let source = corpus("hb_flatten_has_functions.xlsx");
    let report = import_xlsx_bytes(&source, BTreeMap::new(), BTreeMap::new())
        .expect("import")
        .calculate(1);
    for (address, name) in [
        ("A3", "HAS"),
        ("B3", "HASANY"),
        ("C3", "HASALL"),
        ("D3", "FLATTEN"),
    ] {
        let diagnostic = report
            .diagnostics
            .iter()
            .find(|d| d.address.as_deref() == Some(address))
            .unwrap_or_else(|| panic!("no diagnostic for {address}"));
        assert_eq!(diagnostic.code, "unsupported_formula", "{address}");
        assert!(
            diagnostic.message.ends_with(&format!(
                "in-cell array function {name}: FLATTEN, HAS, HASANY and HASALL work on Excel arrays in cells and are not calculated"
            )),
            "{address}: {}",
            diagnostic.message
        );
    }
    assert!(
        !report
            .diagnostics
            .iter()
            .any(|d| d.address.as_deref() == Some("E3"))
    );

    let outputs = BTreeMap::from([("edited".to_string(), CellAddress::new("Tags", "E3"))]);
    let imported = import_xlsx_bytes(&source, BTreeMap::new(), outputs).expect("rebind");
    imported
        .apply(
            vec![Edit::value("Tags", "A2", CellValue::Number(10.0))],
            None,
        )
        .expect("edit");
    let exported = imported.export_bytes().expect("export");
    let (before, after) = (zip_parts(&source), zip_parts(&exported));
    assert_eq!(
        before.keys().collect::<Vec<_>>(),
        after.keys().collect::<Vec<_>>()
    );
    for (name, data) in &before {
        if !matches!(
            name.as_str(),
            "xl/worksheets/sheet1.xml" | "xl/workbook.xml" | "[Content_Types].xml"
        ) {
            assert_eq!(&after[name], data, "{name}");
        }
    }
    for address in ["A3", "B3", "C3", "D3"] {
        assert_eq!(
            cell_xml(&after["xl/worksheets/sheet1.xml"], address),
            cell_xml(&before["xl/worksheets/sheet1.xml"], address),
            "{address}"
        );
    }
    // A new formula using one is refused, not calculated.
    let error = imported
        .apply(
            vec![Edit::formula("Tags", "F3", "=HAS(A1:C1,\"drama\")")],
            None,
        )
        .expect_err("formula edit");
    assert!(
        error.to_string().contains("in-cell array function HAS"),
        "{error}"
    );
}

#[test]
fn compatibility_version_3_preserves_formulas_and_refuses_formula_edits() {
    use workbook_forge::toolkit::{CellAddress, CellValue, Edit};
    use workbook_forge::xlsx::{COMPAT_V3_CALCULATION, COMPAT_V3_EDIT};
    let source = corpus("hb_compat_version_3.xlsx");
    let imported = import_xlsx_bytes(&source, BTreeMap::new(), BTreeMap::new()).expect("import");
    let snapshot = imported.snapshot();
    for address in ["B1", "B2"] {
        assert_eq!(
            snapshot.sheets[0].cells[address].blocked_reason.as_deref(),
            Some(COMPAT_V3_CALCULATION)
        );
    }
    let report = imported.calculate(1);
    assert_eq!(report.diagnostics.len(), 2, "{:?}", report.diagnostics);
    assert!(
        report
            .diagnostics
            .iter()
            .all(|d| d.message == COMPAT_V3_CALCULATION)
    );
    let error = imported
        .apply(vec![Edit::formula("Calc", "C1", "=A2+1")], None)
        .expect_err("formula edit");
    assert_eq!(error.code(), "compat_version_3");
    assert_eq!(
        error.to_string(),
        format!("compat_version_3: {COMPAT_V3_EDIT}")
    );
    let browser = workbook_forge::browser_contract::classify_edit_error(&error.to_string());
    assert_eq!(browser.code, "compat_version_3");

    let outputs = BTreeMap::from([("edited".to_string(), CellAddress::new("Calc", "A2"))]);
    let imported = import_xlsx_bytes(&source, BTreeMap::new(), outputs).expect("rebind");
    imported
        .apply(
            vec![Edit::value("Calc", "A2", CellValue::Number(5.0))],
            None,
        )
        .expect("value edit");
    let after = zip_parts(&imported.export_bytes().expect("export"));
    let before = zip_parts(&source);
    let version = "<xlwcv:version setVersion=\"3\" warnBelowVersion=\"3\"/>";
    assert!(String::from_utf8_lossy(&after["xl/workbook.xml"]).contains(version));
    assert_eq!(
        before.keys().collect::<Vec<_>>(),
        after.keys().collect::<Vec<_>>()
    );
    for (name, data) in &before {
        if !matches!(
            name.as_str(),
            "xl/worksheets/sheet1.xml" | "xl/workbook.xml" | "[Content_Types].xml"
        ) {
            assert_eq!(&after[name], data, "{name}");
        }
    }
    for address in ["B1", "B2"] {
        assert_eq!(
            cell_xml(&after["xl/worksheets/sheet1.xml"], address),
            cell_xml(&before["xl/worksheets/sheet1.xml"], address)
        );
    }
}
