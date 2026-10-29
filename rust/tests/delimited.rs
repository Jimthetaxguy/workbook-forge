//! CSV/TSV import (docs/csv-import.md). The same case table as
//! python/tests/test_delimited.py.
use serde_json::Value as Json;
use workbook_forge::delimited::{DelimitedOptions, import_delimited, import_delimited_path};
use workbook_forge::toolkit::CellValue;
use workbook_forge::xlsx::{export_xlsx_bytes, import_xlsx_bytes};

fn root() -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}
fn cases() -> Vec<Json> {
    let table: Json =
        serde_json::from_slice(&std::fs::read(root().join("tools/corpus/csv-cases.json")).unwrap())
            .unwrap();
    table["cases"].as_array().unwrap().clone()
}
fn hex(text: &str) -> Vec<u8> {
    (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap())
        .collect()
}
fn input(case: &Json) -> Vec<u8> {
    match case.get("hex") {
        Some(h) => hex(h.as_str().unwrap()),
        None => case["text"].as_str().unwrap().as_bytes().to_vec(),
    }
}
fn options(case: &Json) -> DelimitedOptions {
    let mut options = DelimitedOptions::default();
    if let Some(o) = case.get("options") {
        if let Some(d) = o.get("delimiter").and_then(Json::as_str) {
            options.delimiter = d.chars().next();
        }
        if let Some(e) = o.get("encoding").and_then(Json::as_str) {
            options.encoding = Some(e.to_owned());
        }
        if let Some(t) = o.get("as_text").and_then(Json::as_bool) {
            options.as_text = t;
        }
    }
    options
}
fn json_value(value: &CellValue) -> Json {
    match value {
        CellValue::Number(n) => serde_json::json!(n),
        CellValue::Text(t) => Json::String(t.clone()),
        other => panic!("unexpected cell value {other:?}"),
    }
}
fn same(expected: &Json, got: &Json) -> bool {
    match (expected, got) {
        (Json::Number(a), Json::Number(b)) => a.as_f64() == b.as_f64(),
        _ => expected == got,
    }
}

#[test]
fn shared_cases_match_the_table() {
    let mut failures = Vec::new();
    for case in cases() {
        let name = case["name"].as_str().unwrap().to_owned();
        let result = import_delimited(&input(&case), &options(&case));
        match (case.get("refuse").and_then(Json::as_str), result) {
            (Some(code), Err(error)) => {
                if error.code() != code {
                    failures.push(format!(
                        "{name}: refused {} ({error}), want {code}",
                        error.code()
                    ));
                }
            }
            (Some(code), Ok(_)) => failures.push(format!("{name}: imported, want refusal {code}")),
            (None, Err(error)) => failures.push(format!("{name}: refused {error}")),
            (None, Ok(model)) => {
                let cells = &model.sheets[0].cells;
                let expected = case["cells"].as_object().unwrap();
                if cells.len() != expected.len() {
                    failures.push(format!(
                        "{name}: {} cells, want {}",
                        cells.len(),
                        expected.len()
                    ));
                }
                for (address, want) in expected {
                    match cells.get(address) {
                        Some(cell) if same(want, &json_value(&cell.value)) => {}
                        other => failures.push(format!(
                            "{name}: {address} = {:?}, want {want}",
                            other.map(|c| &c.value)
                        )),
                    }
                }
            }
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

#[test]
fn tsv_suffix_picks_a_tab() {
    let dir = std::env::temp_dir().join(format!("wf-delimited-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let tsv = dir.join("parts.tsv");
    std::fs::write(&tsv, b"a,b\tc\n").unwrap();
    let model = import_delimited_path(&tsv, &DelimitedOptions::default()).unwrap();
    assert_eq!(
        model.sheets[0].cells["A1"].value,
        CellValue::Text("a,b".into())
    );
    let csv = dir.join("parts.csv");
    std::fs::write(&csv, b"a,b\tc\n").unwrap();
    let model = import_delimited_path(&csv, &DelimitedOptions::default()).unwrap();
    assert_eq!(
        model.sheets[0].cells["B1"].value,
        CellValue::Text("b\tc".into())
    );
    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn export_round_trip_keeps_text_text() {
    let options = DelimitedOptions {
        sheet_name: "Data".into(),
        ..DelimitedOptions::default()
    };
    let model = import_delimited(b"id,amount\n00123,1.5\n=1+1,TRUE\n", &options).unwrap();
    let bytes = export_xlsx_bytes(&model).unwrap();
    let reopened = import_xlsx_bytes(&bytes, Default::default(), Default::default())
        .unwrap()
        .snapshot();
    let sheet = &reopened.sheets[0];
    assert_eq!(sheet.name, "Data");
    assert_eq!(sheet.cells["A2"].value, CellValue::Text("00123".into()));
    assert_eq!(sheet.cells["B2"].value, CellValue::Number(1.5));
    assert_eq!(sheet.cells["A3"].value, CellValue::Text("=1+1".into()));
    assert_eq!(sheet.cells["B3"].value, CellValue::Text("TRUE".into()));
    assert!(sheet.cells.values().all(|c| c.formula.is_none()));
}

#[test]
fn grid_limits_refuse_out_of_bounds() {
    let wide = format!("{}\n", ",".repeat(16_384));
    let error = import_delimited(wide.as_bytes(), &DelimitedOptions::default()).unwrap_err();
    assert_eq!(error.code(), "out_of_bounds");
}
