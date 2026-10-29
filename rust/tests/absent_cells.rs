//! Absent-cell authoring on imported workbooks: an edit to a cell the XLSX
//! does not hold inserts it (within the session's grid bound), the calc
//! graph picks it up, the exported package stays valid OOXML, and the event
//! log records the cell's `before` as absent so undo removes it again.
//!
//! Fixture (built here from fixtures/realtime-chain.xlsx's package parts with
//! a richer sheet): A1 = 1, D1 = SUM(A1:C1), A2 = 10, A4 = SUM(A1:A3), and a
//! merged range F1:G2. Rows 1, 2 and 4 carry `spans`; there is no row 3.
//! Expected values are independent arithmetic, never engine output:
//!   start:        D1 = 1, A4 = 11
//!   A3 = 100:     A4 = 1 + 10 + 100 = 111
//!   B1 = 2:       D1 = 1 + 2 = 3, A4 unchanged
use serde_json::{Value as Json, json};
use std::collections::BTreeMap;
use std::io::{Cursor, Read, Write};
use workbook_forge::browser_contract::CommandSession;
use workbook_forge::event_log::{Baseline, json_eq, replay};
use workbook_forge::toolkit::{CellValue, Edit};
use workbook_forge::xlsx::{
    EXCEL_MAX_COLUMNS, EXCEL_MAX_ROWS, ImportedWorkbook, import_xlsx_bytes,
};

const SHEET: &str = "xl/worksheets/sheet1.xml";
const MAIN_NS: &str = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";

fn parts(bytes: &[u8]) -> BTreeMap<String, Vec<u8>> {
    let mut archive = zip::ZipArchive::new(Cursor::new(bytes)).unwrap();
    let mut out = BTreeMap::new();
    for index in 0..archive.len() {
        let mut file = archive.by_index(index).unwrap();
        let mut data = Vec::new();
        file.read_to_end(&mut data).unwrap();
        out.insert(file.name().to_string(), data);
    }
    out
}

fn package(parts: &BTreeMap<String, Vec<u8>>) -> Vec<u8> {
    let mut writer = zip::ZipWriter::new(Cursor::new(Vec::new()));
    for (name, data) in parts {
        writer
            .start_file(name.as_str(), zip::write::SimpleFileOptions::default())
            .unwrap();
        writer.write_all(data).unwrap();
    }
    writer.finish().unwrap().into_inner()
}

fn with_sheet(sheet_xml: &str) -> Vec<u8> {
    let base = std::fs::read(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../fixtures/realtime-chain.xlsx"
    ))
    .unwrap();
    let mut parts = parts(&base);
    parts.insert(SHEET.into(), sheet_xml.as_bytes().to_vec());
    package(&parts)
}

fn sparse_bytes() -> Vec<u8> {
    with_sheet(&format!(
        r#"<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{MAIN_NS}"><dimension ref="A1:D4"/><cols><col min="3" max="3" width="12" style="0" customWidth="1"/></cols><sheetData><row r="1" spans="1:4"><c r="A1"><v>1</v></c><c r="D1"><f>SUM(A1:C1)</f><v>1</v></c></row><row r="2" spans="1:4"><c r="A2"><v>10</v></c></row><row r="4" spans="1:4"><c r="A4"><f>SUM(A1:A3)</f><v>11</v></c></row></sheetData><mergeCells count="1"><mergeCell ref="F1:G2"/></mergeCells></worksheet>"#
    ))
}

fn open(bytes: &[u8]) -> ImportedWorkbook {
    import_xlsx_bytes(bytes, BTreeMap::new(), BTreeMap::new()).expect("import")
}

fn number(workbook: &ImportedWorkbook, address: &str) -> f64 {
    let values = workbook.calculate(1).values;
    serde_json::to_value(&values[&format!("Sheet1!{address}")])
        .unwrap()
        .as_f64()
        .unwrap()
}

fn sheet_xml(bytes: &[u8]) -> String {
    String::from_utf8(parts(bytes)[SHEET].clone()).unwrap()
}

fn has_cell(workbook: &ImportedWorkbook, address: &str) -> bool {
    workbook.snapshot().sheets[0].cells.contains_key(address)
}

fn set(address: &str, input: Json) -> Json {
    let mut edit = json!({"sheet": "Sheet1", "address": address});
    edit.as_object_mut()
        .unwrap()
        .extend(input.as_object().unwrap().clone());
    json!({"type": "set_cells", "edits": [edit]})
}

fn envelope(id: &str, base: &str, op: Json) -> String {
    json!({
        "envelope": {
            "v": 1, "command_id": id, "client_id": "tab", "client_seq": "1",
            "idempotency_key": null,
            "actor": {"kind": "human", "id": "ui", "on_behalf_of": null},
            "base_revision": base, "mode": "strict", "preconditions": [], "op": op,
        },
        "command": {"source": "editor"},
    })
    .to_string()
}

fn submit(session: &CommandSession, workbook: &ImportedWorkbook, id: &str, op: Json) -> Json {
    let base = workbook.revision().to_string();
    serde_json::from_str(&session.submit(workbook, &envelope(id, &base, op))).unwrap()
}

fn assert_json(actual: &Json, expected: &Json) {
    assert!(json_eq(actual, expected), "{actual:#} != {expected:#}");
}

#[test]
fn inserts_in_row_middle_row_end_and_new_rows_in_order() {
    let bytes = sparse_bytes();
    let workbook = open(&bytes);
    workbook
        .apply(
            vec![
                Edit::value("Sheet1", "B1", CellValue::Number(2.0)),
                Edit::value("Sheet1", "E1", CellValue::Number(5.0)),
                Edit::value("Sheet1", "A3", CellValue::Number(100.0)),
                Edit::value("Sheet1", "A6", CellValue::Text("note & <x>".into())),
            ],
            Some(0),
        )
        .unwrap();
    assert_eq!(number(&workbook, "D1"), 3.0);
    assert_eq!(number(&workbook, "A4"), 111.0);

    let exported = workbook.export_bytes().unwrap();
    let xml = sheet_xml(&exported);
    // Row 1: B1 between A1 and D1, E1 after D1; spans widened to column 5.
    let row1 = &xml[xml.find(r#"<row r="1""#).unwrap()..xml.find(r#"<row r="2""#).unwrap()];
    assert!(row1.contains(r#"spans="1:5""#), "{row1}");
    let order: Vec<usize> = ["\"A1\"", "\"B1\"", "\"D1\"", "\"E1\""]
        .iter()
        .map(|r| row1.find(r).unwrap())
        .collect();
    assert!(order.windows(2).all(|w| w[0] < w[1]), "{row1}");
    // Row 3 is created between rows 2 and 4; row 6 after row 4.
    let rows: Vec<usize> = [
        "<row r=\"2\"",
        "<row r=\"3\"",
        "<row r=\"4\"",
        "<row r=\"6\"",
    ]
    .iter()
    .map(|r| xml.find(r).unwrap_or_else(|| panic!("{r} in {xml}")))
    .collect();
    assert!(rows.windows(2).all(|w| w[0] < w[1]), "{xml}");
    assert!(
        xml.contains(r#"<row r="3" spans="1:1"><c r="A3"><v>100</v></c></row>"#),
        "{xml}"
    );
    // Text uses the writer's inline-string convention, escaped.
    assert!(xml.contains("note &amp; &lt;x&gt;"), "{xml}");
    assert!(xml.contains(r#"t="inlineStr""#), "{xml}");
    // The dimension grows to the new extent; the merge is untouched.
    assert!(xml.contains(r#"<dimension ref="A1:E6"/>"#), "{xml}");
    assert!(xml.contains(r#"<mergeCell ref="F1:G2"/>"#), "{xml}");

    let reopened = open(&exported);
    let cells = &reopened.snapshot().sheets[0].cells;
    assert_eq!(cells["B1"].value, CellValue::Number(2.0));
    assert_eq!(cells["E1"].value, CellValue::Number(5.0));
    assert_eq!(cells["A3"].value, CellValue::Number(100.0));
    assert_eq!(cells["A6"].value, CellValue::Text("note & <x>".into()));
    assert_eq!(number(&reopened, "D1"), 3.0);
    assert_eq!(number(&reopened, "A4"), 111.0);
}

#[test]
fn new_row_just_before_a_rewritten_row_is_placed_before_it() {
    // A3 creates row 3 at the start offset of row 4, and B4 rewrites row 4.
    // Both patches start at the same byte; the new row must come first.
    let bytes = sparse_bytes();
    let workbook = open(&bytes);
    workbook
        .apply(
            vec![
                Edit::value("Sheet1", "A3", CellValue::Number(100.0)),
                Edit::value("Sheet1", "B4", CellValue::Number(7.0)),
            ],
            Some(0),
        )
        .unwrap();
    let exported = workbook.export_bytes().unwrap();
    let xml = sheet_xml(&exported);
    let row3 = xml.find(r#"<row r="3""#).unwrap_or_else(|| panic!("{xml}"));
    let row4 = xml.find(r#"<row r="4""#).unwrap_or_else(|| panic!("{xml}"));
    assert!(row3 < row4, "{xml}");
    assert!(
        xml.contains(r#"<row r="3" spans="1:1"><c r="A3"><v>100</v></c></row>"#),
        "{xml}"
    );
    let reopened = open(&exported);
    let cells = &reopened.snapshot().sheets[0].cells;
    assert_eq!(cells["A3"].value, CellValue::Number(100.0));
    assert_eq!(cells["B4"].value, CellValue::Number(7.0));
    assert_eq!(number(&reopened, "A4"), 111.0);
}

#[test]
fn formula_into_an_absent_cell_round_trips_with_its_value() {
    let workbook = open(&sparse_bytes());
    workbook
        .apply(vec![Edit::formula("Sheet1", "C2", "=A2*3")], Some(0))
        .unwrap();
    assert_eq!(number(&workbook, "C2"), 30.0);
    let exported = workbook.export_bytes().unwrap();
    let xml = sheet_xml(&exported);
    // Column C declares a style, so the new cell takes it as Excel would.
    assert!(
        xml.contains(r#"<c r="C2" s="0"><f>A2*3</f><v>30</v></c>"#),
        "{xml}"
    );
    let reopened = open(&exported);
    let c2 = &reopened.snapshot().sheets[0].cells["C2"];
    assert_eq!(c2.formula.as_deref(), Some("A2*3"));
    assert_eq!(c2.cached_value, Some(CellValue::Number(30.0)));
    assert_eq!(number(&reopened, "C2"), 30.0);
    // A formula reading a range with the new formula cell in it recalcs too.
    workbook
        .apply(vec![Edit::formula("Sheet1", "D2", "=SUM(A2:C2)")], Some(1))
        .unwrap();
    assert_eq!(number(&workbook, "D2"), 40.0);
}

#[test]
fn sum_range_dependents_recalculate_and_report_deltas() {
    let workbook = open(&sparse_bytes());
    let session = CommandSession::new();
    let response = submit(&session, &workbook, "c1", set("A3", json!({"value": 100})));
    assert_eq!(response["status"], "applied", "{response:#}");
    let changed: BTreeMap<String, Json> = response["changed"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| {
            (
                c["address"].as_str().unwrap().to_string(),
                c["value"].clone(),
            )
        })
        .collect();
    assert_json(&changed["A3"], &json!(100));
    assert_json(&changed["A4"], &json!(111));
    let calc = &session.events()[2];
    assert_eq!(calc["kind"], "calc");
    assert_json(&calc["deltas"], &json!([["Sheet1!A4", 11, 111]]));
}

#[test]
fn out_of_bounds_and_merged_insertions_are_refused_with_codes() {
    assert_eq!((EXCEL_MAX_ROWS, EXCEL_MAX_COLUMNS), (1_048_576, 16_384));
    let workbook = open(&sparse_bytes());
    let session = CommandSession::new();
    // The far corner of Excel's grid is inside the default bound.
    let corner = submit(
        &session,
        &workbook,
        "a",
        set("XFD1048576", json!({"value": 1})),
    );
    assert_eq!(corner["status"], "applied", "{corner:#}");
    for (id, address) in [("b", "XFE1"), ("c", "A1048577"), ("d", "AAAAAA1")] {
        let response = submit(&session, &workbook, id, set(address, json!({"value": 1})));
        assert_eq!(response["error"]["code"], "out_of_bounds", "{address}");
    }
    // F1 anchors the merge F1:G2; its other cells cannot hold content.
    let anchor = submit(&session, &workbook, "e", set("F1", json!({"value": 1})));
    assert_eq!(anchor["status"], "applied");
    let covered = submit(&session, &workbook, "f", set("G2", json!({"value": 1})));
    assert_eq!(covered["error"]["code"], "unsupported_edit");
    assert_eq!(workbook.revision(), 2);

    // A smaller declared bound: insertions stop at C3, existing cells stay
    // editable wherever they are.
    let bounded = open(&sparse_bytes()).with_insertion_bound(3, 3).unwrap();
    let session = CommandSession::new();
    let inside = submit(&session, &bounded, "g", set("C3", json!({"value": 1})));
    assert_eq!(inside["status"], "applied");
    let below = submit(&session, &bounded, "h", set("A5", json!({"value": 1})));
    assert_eq!(below["error"]["code"], "out_of_bounds");
    assert!(
        below["error"]["message"]
            .as_str()
            .unwrap()
            .contains("3 rows x 3 columns")
    );
    let right = submit(&session, &bounded, "i", set("D2", json!({"value": 1})));
    assert_eq!(right["error"]["code"], "out_of_bounds");
    let existing = submit(
        &session,
        &bounded,
        "j",
        set("D1", json!({"formula": "A1*2"})),
    );
    assert_eq!(existing["status"], "applied", "{existing:#}");
    assert!(open(&sparse_bytes()).with_insertion_bound(0, 3).is_err());
    assert!(
        open(&sparse_bytes())
            .with_insertion_bound(3, 16_385)
            .is_err()
    );
}

#[test]
fn undo_restores_absence_and_redo_reinserts() {
    let bytes = sparse_bytes();
    let workbook = open(&bytes);
    let session = CommandSession::new();
    session.set_baseline(Baseline::xlsx("sparse.xlsx", &bytes));
    session.start_log(None, json!({}), 0);
    let applied = submit(&session, &workbook, "c1", set("A3", json!({"value": 100})));
    assert_eq!(applied["status"], "applied");
    let intent = &session.events()[1];
    assert_json(
        &intent["inputs"],
        &json!([{"sheet": "Sheet1", "address": "A3",
                 "before": {"absent": true}, "after": {"value": 100}}]),
    );

    let undo = submit(&session, &workbook, "u1", json!({"type": "undo"}));
    assert_eq!(undo["status"], "applied", "{undo:#}");
    assert!(!has_cell(&workbook, "A3"), "undo removes the inserted cell");
    assert_eq!(number(&workbook, "A4"), 11.0);
    let a3 = undo["changed"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["address"] == "A3")
        .expect("A3 repainted");
    assert_eq!(a3["value"], Json::Null);
    assert_eq!(a3["formula"], Json::Null);
    let undo_event = &session.events()[3];
    assert_json(
        &undo_event["inputs"],
        &json!([{"sheet": "Sheet1", "address": "A3",
                 "before": {"value": 100}, "after": {"absent": true}}]),
    );
    // Absent again on disk too: no row 3, the dimension is the source's.
    let xml = sheet_xml(&workbook.export_bytes().unwrap());
    assert!(!xml.contains("<row r=\"3\""), "{xml}");
    assert!(xml.contains(r#"<dimension ref="A1:D4"/>"#), "{xml}");

    let redo = submit(&session, &workbook, "r1", json!({"type": "redo"}));
    assert_eq!(redo["status"], "applied", "{redo:#}");
    assert_eq!(number(&workbook, "A4"), 111.0);
    assert!(has_cell(&workbook, "A3"));
}

#[test]
fn replay_reproduces_insertion_undo_and_redo() {
    let bytes = sparse_bytes();
    let workbook = open(&bytes);
    let session = CommandSession::new();
    session.set_baseline(Baseline::xlsx("sparse.xlsx", &bytes));
    session.start_log(None, json!({}), 0);
    for (id, op) in [
        ("c1", set("A3", json!({"value": 100}))),
        ("c2", set("B1", json!({"formula": "A3/50"}))),
        ("u1", json!({"type": "undo"})),
        ("u2", json!({"type": "undo"})),
        ("r1", json!({"type": "redo"})),
        ("c3", set("C3", json!({"value": null}))),
        ("u3", json!({"type": "undo"})),
    ] {
        let response = submit(&session, &workbook, id, op);
        assert_eq!(response["status"], "applied", "{id}: {response:#}");
    }
    assert!(has_cell(&workbook, "A3"));
    assert!(!has_cell(&workbook, "B1"));
    assert!(!has_cell(&workbook, "C3"));
    let log = session.export_log_ndjson(workbook.revision(), None);
    let replayed = replay(&bytes, &log).expect("replay");
    assert_eq!(replayed["revision"], workbook.revision().to_string());
    assert_eq!(replayed["snapshot"], json!(workbook.snapshot()));
    assert_eq!(replayed["values"], json!(workbook.calculate(1).values));
}

#[test]
fn absent_blank_and_cleared_stay_distinct() {
    let bytes = sparse_bytes();
    let workbook = open(&bytes);
    let session = CommandSession::new();
    // Clearing an absent cell: the session holds a blank cell, the export
    // writes nothing for it, and its log `before` is absent.
    let clear_absent = submit(&session, &workbook, "a", set("C3", json!({"value": null})));
    assert_eq!(clear_absent["status"], "applied");
    assert!(has_cell(&workbook, "C3"));
    assert_json(
        &session.events()[1]["inputs"],
        &json!([{"sheet": "Sheet1", "address": "C3",
                 "before": {"absent": true}, "after": {"value": null}}]),
    );
    // Clearing a source cell: it stays in the model and in the XML as an
    // empty `<c>`, and A4 drops by its old value.
    let clear_source = submit(&session, &workbook, "b", set("A2", json!({"value": null})));
    assert_eq!(clear_source["status"], "applied");
    assert_eq!(number(&workbook, "A4"), 1.0);
    let xml = sheet_xml(&workbook.export_bytes().unwrap());
    assert!(!xml.contains("C3"), "{xml}");
    assert!(xml.contains(r#"<c r="A2"></c>"#), "{xml}");
    // Undoing the source clear restores the value; the cell never left.
    submit(&session, &workbook, "u1", json!({"type": "undo"}));
    assert_eq!(
        workbook.snapshot().sheets[0].cells["A2"].value,
        CellValue::Number(10.0)
    );
    // Undoing the absent clear removes the blank cell.
    submit(&session, &workbook, "u2", json!({"type": "undo"}));
    assert!(!has_cell(&workbook, "C3"));
    // A source cell can never be made absent.
    let error = workbook
        .apply(vec![Edit::absent("Sheet1", "A1")], None)
        .unwrap_err();
    assert!(
        error.to_string().starts_with("unsupported_edit:"),
        "{error}"
    );
}

#[test]
fn edits_validate_against_the_session_model_not_the_import() {
    let workbook = open(&sparse_bytes());
    // B3 is inserted, then edited again: it is an existing cell now.
    workbook
        .apply(
            vec![Edit::value("Sheet1", "B3", CellValue::Number(1.0))],
            Some(0),
        )
        .unwrap();
    workbook
        .apply(vec![Edit::formula("Sheet1", "B3", "=A2+1")], Some(1))
        .unwrap();
    assert_eq!(number(&workbook, "B3"), 11.0);
    // Under a declared bound, a cell inserted earlier stays editable even if
    // the batch also touches new in-bound cells, and the whole batch is
    // atomic when one insertion is out of bounds.
    let bounded = open(&sparse_bytes()).with_insertion_bound(4, 4).unwrap();
    bounded
        .apply(
            vec![Edit::value("Sheet1", "D4", CellValue::Number(4.0))],
            Some(0),
        )
        .unwrap();
    let before = bounded.snapshot();
    let error = bounded
        .apply(
            vec![
                Edit::value("Sheet1", "D4", CellValue::Number(5.0)),
                Edit::value("Sheet1", "E4", CellValue::Number(6.0)),
            ],
            Some(1),
        )
        .unwrap_err();
    assert!(error.to_string().starts_with("out_of_bounds:"), "{error}");
    assert_eq!(bounded.snapshot(), before);
}

#[test]
fn empty_sheet_data_and_rows_without_spans_take_insertions() {
    let bytes = with_sheet(&format!(
        r#"<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{MAIN_NS}"><dimension ref="A1"/><sheetData/></worksheet>"#
    ));
    let workbook = open(&bytes);
    workbook
        .apply(
            vec![
                Edit::value("Sheet1", "B2", CellValue::Number(2.0)),
                Edit::formula("Sheet1", "B3", "=B2*B2"),
                Edit::value("Sheet1", "A2", CellValue::Boolean(true)),
            ],
            Some(0),
        )
        .unwrap();
    let exported = workbook.export_bytes().unwrap();
    let xml = sheet_xml(&exported);
    assert!(
        xml.contains(r#"<sheetData><row r="2"><c r="A2" t="b"><v>1</v></c><c r="B2"><v>2</v></c></row><row r="3"><c r="B3"><f>B2*B2</f><v>4</v></c></row></sheetData>"#),
        "{xml}"
    );
    assert!(xml.contains(r#"<dimension ref="A1:B3"/>"#), "{xml}");
    let reopened = open(&exported);
    assert_eq!(number(&reopened, "B3"), 4.0);

    // A self-closing row also takes a cell.
    let bytes = with_sheet(&format!(
        r#"<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{MAIN_NS}"><sheetData><row r="1" ht="30" customHeight="1"/><row r="2"><c r="A2"><v>3</v></c></row></sheetData></worksheet>"#
    ));
    let workbook = open(&bytes);
    workbook
        .apply(vec![Edit::formula("Sheet1", "C1", "=A2+1")], Some(0))
        .unwrap();
    let xml = sheet_xml(&workbook.export_bytes().unwrap());
    assert!(
        xml.contains(
            r#"<row r="1" ht="30" customHeight="1"><c r="C1"><f>A2+1</f><v>4</v></c></row>"#
        ),
        "{xml}"
    );
}
