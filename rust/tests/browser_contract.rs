//! The browser command envelope, tested natively against the Univer spike
//! fixture. Expected values are independent arithmetic (see
//! examples/univer_spike_fixture.rs), never engine output.
use std::collections::BTreeMap;
use workbook_forge::browser_contract::{
    apply_edits_envelope, classify_edit_error, parse_envelope, parse_revision, revision_string,
};
use workbook_forge::xlsx::{ImportedWorkbook, import_xlsx_bytes};

fn fixture() -> ImportedWorkbook {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../fixtures/univer-spike-chain.xlsx"
    );
    let bytes = std::fs::read(path).expect("fixture");
    import_xlsx_bytes(&bytes, BTreeMap::new(), BTreeMap::new()).expect("import")
}

fn number(workbook: &ImportedWorkbook, address: &str) -> f64 {
    let report = serde_json::to_value(workbook.calculate(1)).unwrap();
    report["values"][format!("Sheet1!{address}")]
        .as_f64()
        .unwrap_or_else(|| panic!("{address} not numeric in {}", report["values"]))
}

#[test]
fn fixture_holds_five_prepopulated_dependent_formulas() {
    let workbook = fixture();
    let snapshot = workbook.snapshot();
    let cells = &snapshot.sheets[0].cells;
    for (address, formula) in [
        ("B1", "A1*2"),
        ("C1", "B1+5"),
        ("D1", "C1*3"),
        ("E1", "D1-B1"),
        ("F1", "SUM(A1:E1)"),
    ] {
        let stored = cells[address].formula.as_deref().unwrap();
        assert_eq!(stored.trim_start_matches('='), formula, "{address}");
    }
    for (address, expected) in [
        ("B1", 20.0),
        ("C1", 25.0),
        ("D1", 75.0),
        ("E1", 55.0),
        ("F1", 185.0),
    ] {
        assert_eq!(number(&workbook, address), expected, "{address}");
    }
}

#[test]
fn envelope_applies_with_string_revisions_and_recalculates() {
    let workbook = fixture();
    assert_eq!(revision_string(&workbook), "0");
    let ok = parse_envelope(&apply_edits_envelope(
        &workbook,
        r#"[{"sheet":"Sheet1","address":"A1","value":20}]"#,
        "0",
    ));
    assert_eq!(ok, serde_json::json!({"ok": true, "revision": "1"}));
    // A1=20: B1=40, C1=45, D1=135, E1=95, F1=335.
    assert_eq!(number(&workbook, "F1"), 335.0);
    let ok = parse_envelope(&apply_edits_envelope(
        &workbook,
        r#"[{"sheet":"Sheet1","address":"C1","formula":"=B1+100"}]"#,
        "1",
    ));
    assert_eq!(ok["revision"], "2");
    // C1=140, D1=420, E1=380, F1=20+40+140+420+380=1000.
    assert_eq!(number(&workbook, "F1"), 1000.0);
}

#[test]
fn envelope_reports_structured_conflicts_without_applying() {
    let workbook = fixture();
    let stale = parse_envelope(&apply_edits_envelope(
        &workbook,
        r#"[{"sheet":"Sheet1","address":"A1","value":20}]"#,
        "7",
    ));
    assert_eq!(stale["ok"], false);
    assert_eq!(stale["error"]["code"], "revision_conflict");
    assert_eq!(stale["error"]["expected_revision"], "7");
    assert_eq!(stale["error"]["current_revision"], "0");
    assert_eq!(revision_string(&workbook), "0");
    assert_eq!(number(&workbook, "A1"), 10.0);
}

#[test]
fn envelope_rejects_out_of_grid_cells_and_bad_input_with_codes() {
    let workbook = fixture();
    // Absent cells inside Excel's grid are insertions now; past it (column
    // XFE = 16,385) the edit is refused with a structured code.
    let outside = parse_envelope(&apply_edits_envelope(
        &workbook,
        r#"[{"sheet":"Sheet1","address":"XFE9","value":1}]"#,
        "0",
    ));
    assert_eq!(outside["error"]["code"], "out_of_bounds");
    assert_eq!(outside["error"]["current_revision"], "0");
    let rows = parse_envelope(&apply_edits_envelope(
        &workbook,
        r#"[{"sheet":"Sheet1","address":"A1048577","value":1}]"#,
        "0",
    ));
    assert_eq!(rows["error"]["code"], "out_of_bounds");
    let sheet = parse_envelope(&apply_edits_envelope(
        &workbook,
        r#"[{"sheet":"Nope","address":"A1","value":1}]"#,
        "0",
    ));
    assert_eq!(sheet["error"]["code"], "unknown_sheet");
    let json = parse_envelope(&apply_edits_envelope(&workbook, "not json", "0"));
    assert_eq!(json["error"]["code"], "invalid_request");
    let revision = parse_envelope(&apply_edits_envelope(&workbook, "[]", "-1"));
    assert_eq!(revision["error"]["code"], "invalid_revision");
    assert_eq!(revision_string(&workbook), "0");
}

#[test]
fn revision_strings_and_error_codes_are_stable() {
    assert_eq!(parse_revision("18446744073709551615").unwrap(), u64::MAX);
    assert!(parse_revision("18446744073709551616").is_err());
    assert!(parse_revision("").is_err());
    assert!(parse_revision("1e3").is_err());
    assert_eq!(
        classify_edit_error("parse_error: bad token").code,
        "parse_error"
    );
    assert_eq!(
        classify_edit_error("parse_error: bad token").message,
        "bad token"
    );
    assert_eq!(classify_edit_error("something else").code, "edit_rejected");
}
