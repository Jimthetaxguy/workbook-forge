//! The shared defined-name fixture (`fixtures/defined-names.v1.json`) in the
//! Rust toolkit session and the Rust canonical model. Python runs the same
//! fixture in `python/tests/test_defined_names.py`.

use serde_json::{Map, Value as Json, json};
use std::collections::BTreeMap;
use workbook_forge::model::{
    self, Cell as CanonicalCell, Formula, Scalar, Sheet as CanonicalSheet,
};
use workbook_forge::toolkit::{Edit, WorkbookModel};
use workbook_forge::{DefinedName, DefinedNames, Session};

fn fixture() -> Json {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../fixtures/defined-names.v1.json"
    );
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn column(mut index: usize) -> String {
    let mut letters = String::new();
    while index > 0 {
        let digit = (index - 1) % 26;
        letters.insert(0, (b'A' + digit as u8) as char);
        index = (index - 1) / 26;
    }
    letters
}

/// case id -> (sheet, address): case n (from 1) is column n of row 10.
fn case_cells(fixture: &Json) -> BTreeMap<String, (String, String)> {
    fixture["cases"]
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
        .map(|(index, case)| {
            (
                case["id"].as_str().unwrap().to_string(),
                (
                    case["sheet"].as_str().unwrap().to_string(),
                    format!("{}10", column(index + 1)),
                ),
            )
        })
        .collect()
}

fn names(fixture: &Json) -> DefinedNames {
    let entries: Vec<DefinedName> = serde_json::from_value(fixture["names"].clone()).unwrap();
    DefinedNames::new(entries)
}

fn same(actual: &Json, expected: &Json) -> bool {
    match (actual.as_f64(), expected.as_f64()) {
        (Some(a), Some(e)) => (a - e).abs() <= 1e-12 * e.abs().max(1.0),
        _ => actual == expected,
    }
}

fn toolkit_model(fixture: &Json, cells: &BTreeMap<String, (String, String)>) -> WorkbookModel {
    let mut sheets = Map::new();
    for name in fixture["sheets"].as_array().unwrap() {
        let name = name.as_str().unwrap();
        sheets.insert(
            name.into(),
            json!({"id": name, "name": name, "cells": {}, "column_widths": {}}),
        );
    }
    for (key, value) in fixture["cells"].as_object().unwrap() {
        let (sheet, address) = key.split_once('!').unwrap();
        sheets[sheet]["cells"][address] = json!({"value": value});
    }
    for case in fixture["cases"].as_array().unwrap() {
        let (sheet, address) = &cells[case["id"].as_str().unwrap()];
        sheets[sheet.as_str()]["cells"][address.as_str()] = json!({"formula": case["formula"]});
    }
    serde_json::from_value(json!({
        "schema_version": 1, "revision": 0,
        "sheets": sheets.values().cloned().collect::<Vec<_>>(),
        "inputs": {}, "outputs": {}
    }))
    .unwrap()
}

fn check_toolkit(
    report: &Json,
    cases: &[Json],
    cells: &BTreeMap<String, (String, String)>,
) -> Vec<String> {
    let mut problems = Vec::new();
    for case in cases {
        let (sheet, address) = &cells[case["id"].as_str().unwrap()];
        let expected = &case["expected"];
        let key = format!("{sheet}!{address}");
        let got = report["diagnostics"]
            .as_array()
            .unwrap()
            .iter()
            .find(|item| {
                item["sheet"].as_str() == Some(sheet) && item["address"].as_str() == Some(address)
            });
        let ok = if let Some(code) = expected.get("code") {
            got.is_some_and(|got| {
                &got["code"] == code
                    && expected
                        .get("message")
                        .is_none_or(|message| &got["message"] == message)
            })
        } else if let Some(error) = expected.get("error") {
            report["values"][&key] == json!({"error": error})
        } else {
            same(&report["values"][&key], &expected["value"])
        };
        if !ok {
            problems.push(format!(
                "{}: {:?} / {:?} != {expected}",
                case["id"],
                report["values"].get(&key),
                got
            ));
        }
    }
    problems
}

fn recalculated_cases(fixture: &Json, step: &Json) -> Vec<Json> {
    step["expected"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(id, value)| {
            let case = fixture["cases"]
                .as_array()
                .unwrap()
                .iter()
                .find(|case| case["id"] == json!(id))
                .unwrap();
            json!({"id": id, "sheet": case["sheet"], "expected": {"value": value}})
        })
        .collect()
}

#[test]
fn shared_fixture_in_the_toolkit_session() {
    let fixture = fixture();
    let cells = case_cells(&fixture);
    let session =
        Session::with_names(toolkit_model(&fixture, &cells), Some(names(&fixture))).unwrap();
    let report = serde_json::to_value(session.calculate(1)).unwrap();
    let cases = fixture["cases"].as_array().unwrap();
    assert_eq!(check_toolkit(&report, cases, &cells), Vec::<String>::new());
    for step in fixture["recalculations"].as_array().unwrap() {
        let edit: Edit = serde_json::from_value(step["edit"].clone()).unwrap();
        session.apply(vec![edit], None).unwrap();
        let report = serde_json::to_value(session.calculate(1)).unwrap();
        let changed = recalculated_cases(&fixture, step);
        assert_eq!(
            check_toolkit(&report, &changed, &cells),
            Vec::<String>::new()
        );
    }
}

#[test]
fn a_session_without_a_table_keeps_the_unsupported_name_refusal() {
    let fixture = fixture();
    let cells = case_cells(&fixture);
    let session = Session::new(toolkit_model(&fixture, &cells)).unwrap();
    let report = serde_json::to_value(session.calculate(1)).unwrap();
    let (sheet, address) = &cells["cell_name"];
    let got = report["diagnostics"]
        .as_array()
        .unwrap()
        .iter()
        .find(|item| item["sheet"] == json!(sheet) && item["address"] == json!(address))
        .unwrap();
    assert_eq!(got["message"], json!("unsupported name 'Rate'"));
}

fn canonical_number(value: &Json) -> Scalar {
    Scalar::Number(serde_json::Number::from_f64(value.as_f64().unwrap()).unwrap())
}

fn canonical_workbook(
    fixture: &Json,
    cells: &BTreeMap<String, (String, String)>,
    edits: &[(String, String, Json)],
) -> model::Workbook {
    let mut sheets: BTreeMap<String, BTreeMap<String, CanonicalCell>> = BTreeMap::new();
    let cell =
        |address: &str, value: Scalar, data_type: &str, formula: Option<Formula>| CanonicalCell {
            address: address.into(),
            value,
            data_type: data_type.into(),
            number_format: None,
            formula,
            provenance: None,
        };
    for (key, value) in fixture["cells"].as_object().unwrap() {
        let (sheet, address) = key.split_once('!').unwrap();
        sheets.entry(sheet.into()).or_default().insert(
            address.into(),
            cell(address, canonical_number(value), "number", None),
        );
    }
    for (sheet, address, value) in edits {
        sheets.entry(sheet.clone()).or_default().insert(
            address.clone(),
            cell(address, canonical_number(value), "number", None),
        );
    }
    for case in fixture["cases"].as_array().unwrap() {
        let (sheet, address) = &cells[case["id"].as_str().unwrap()];
        let formula = Formula {
            expression: case["formula"].as_str().unwrap().into(),
            dependencies: Vec::new(),
            result: Scalar::Blank,
        };
        sheets.entry(sheet.clone()).or_default().insert(
            address.clone(),
            cell(address, Scalar::Blank, "blank", Some(formula)),
        );
    }
    let mut metadata = Map::new();
    metadata.insert("defined_names".into(), fixture["names"].clone());
    model::Workbook {
        schema_version: 1,
        model_version: 1,
        metadata,
        sheets: fixture["sheets"]
            .as_array()
            .unwrap()
            .iter()
            .map(|name| {
                let name = name.as_str().unwrap();
                CanonicalSheet {
                    name: name.into(),
                    cells: sheets.remove(name).unwrap_or_default(),
                    dimensions: None,
                }
            })
            .collect(),
        source_path: None,
        provenance: None,
        bindings: None,
        diagnostics: Vec::new(),
    }
}

fn check_canonical(
    workbook: &model::Workbook,
    cases: &[Json],
    cells: &BTreeMap<String, (String, String)>,
) -> Vec<String> {
    let mut problems = Vec::new();
    for case in cases {
        let (sheet, address) = &cells[case["id"].as_str().unwrap()];
        let expected = &case["expected"];
        let result = &workbook.sheet(sheet).unwrap().cells[address]
            .formula
            .as_ref()
            .unwrap()
            .result;
        let got = workbook.diagnostics.iter().find(|item| {
            item.sheet.as_deref() == Some(sheet) && item.address.as_deref() == Some(address)
        });
        let ok = if let Some(code) = expected.get("code") {
            let (code, classification) = match code.as_str().unwrap() {
                "name_cycle" => ("cycle", "cycle"),
                "resource_limit" => ("resource_limit", "resource_limit"),
                "parse_error" => ("parse_error", "parse_error"),
                "invalid_reference" => ("invalid_reference", "invalid_reference"),
                _ => ("unsupported_formula", "unsupported"),
            };
            got.is_some_and(|got| {
                got.code == code
                    && got.classification == classification
                    && expected
                        .get("message")
                        .is_none_or(|message| message.as_str() == Some(got.message.as_str()))
            })
        } else if let Some(error) = expected.get("error") {
            matches!(result, Scalar::Error { code, .. } if Some(code.as_str()) == error.as_str())
        } else {
            match (result, &expected["value"]) {
                (Scalar::Number(number), expected) => {
                    same(&json!(number.as_f64().unwrap()), expected)
                }
                (Scalar::Text(text), expected) => expected.as_str() == Some(text),
                _ => false,
            }
        };
        if !ok {
            problems.push(format!(
                "{}: {result:?} / {got:?} != {expected}",
                case["id"]
            ));
        }
    }
    problems
}

#[test]
fn shared_fixture_in_the_canonical_model() {
    let fixture = fixture();
    let cells = case_cells(&fixture);
    let calculated = model::calculate(&canonical_workbook(&fixture, &cells, &[])).unwrap();
    let cases = fixture["cases"].as_array().unwrap();
    assert_eq!(
        check_canonical(&calculated, cases, &cells),
        Vec::<String>::new()
    );
    let mut edits = Vec::new();
    for step in fixture["recalculations"].as_array().unwrap() {
        let edit = &step["edit"];
        edits.push((
            edit["sheet"].as_str().unwrap().to_string(),
            edit["address"].as_str().unwrap().to_string(),
            edit["value"].clone(),
        ));
        let calculated = model::calculate(&canonical_workbook(&fixture, &cells, &edits)).unwrap();
        let changed = recalculated_cases(&fixture, step);
        assert_eq!(
            check_canonical(&calculated, &changed, &cells),
            Vec::<String>::new()
        );
    }
}

#[test]
fn imported_names_calculate_and_export_keeps_workbook_xml() {
    use workbook_forge::toolkit::CellValue;
    use workbook_forge::xlsx::import_xlsx_bytes;
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../fixtures/corpus/op_defined_names.xlsx"
    );
    let bytes = std::fs::read(path).unwrap();
    let imported = import_xlsx_bytes(&bytes, BTreeMap::new(), BTreeMap::new()).unwrap();
    let report = serde_json::to_value(imported.calculate(1)).unwrap();
    assert!(same(&report["values"]["Calc!A1"], &json!(120.0)));
    assert!(same(&report["values"]["Calc!A2"], &json!(6.0)));
    imported
        .apply(
            vec![Edit::value("Inputs", "A1", CellValue::Number(0.1))],
            None,
        )
        .unwrap();
    let report = serde_json::to_value(imported.calculate(1)).unwrap();
    assert!(same(&report["values"]["Calc!A1"], &json!(150.0)));
    let exported = imported.export_bytes().unwrap();
    let workbook_xml = |bytes: &[u8]| {
        use std::io::Read;
        let mut archive = zip::ZipArchive::new(std::io::Cursor::new(bytes)).unwrap();
        let mut text = String::new();
        archive
            .by_name("xl/workbook.xml")
            .unwrap()
            .read_to_string(&mut text)
            .unwrap();
        let start = text.find("<definedNames").unwrap();
        let end = text.find("</definedNames>").unwrap();
        text[start..end].to_string()
    };
    assert_eq!(workbook_xml(&exported), workbook_xml(&bytes));
}
