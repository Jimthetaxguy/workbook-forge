//! Drop-downs, pivots and charts on imported workbooks (docs/xlsx-features.md).
//! The same case table as python/tests/test_xlsx_features.py.
use serde_json::Value as Json;
use std::collections::BTreeMap;
use workbook_forge::toolkit::{CellValue, Edit};
use workbook_forge::xlsx::import_xlsx_bytes;

fn root() -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}
fn cases() -> Json {
    serde_json::from_slice(&std::fs::read(root().join("tools/corpus/feature-cases.json")).unwrap())
        .unwrap()
}
fn corpus(name: &str) -> Vec<u8> {
    std::fs::read(root().join("fixtures/corpus").join(name)).unwrap()
}
fn value(json: &Json) -> CellValue {
    match json {
        Json::Null => CellValue::Blank,
        Json::Bool(b) => CellValue::Boolean(*b),
        Json::Number(n) => CellValue::Number(n.as_f64().unwrap()),
        Json::String(s) => CellValue::Text(s.clone()),
        other => panic!("unsupported case value {other}"),
    }
}

#[test]
fn write_cases_match_the_shared_table() {
    let mut failures = Vec::new();
    for case in cases()["writes"].as_array().unwrap() {
        let file = case["file"].as_str().unwrap();
        let sheet = case["sheet"].as_str().unwrap();
        let address = case["address"].as_str().unwrap();
        let imported = import_xlsx_bytes(&corpus(file), BTreeMap::new(), BTreeMap::new()).unwrap();
        let result = imported.apply(
            vec![Edit::value(sheet, address, value(&case["value"]))],
            None,
        );
        let got = match &result {
            Ok(_) => "ok".to_string(),
            Err(error) => error.code().to_string(),
        };
        if got != case["expect"].as_str().unwrap() {
            failures.push(format!(
                "{file} {sheet}!{address}={}: expected {}, got {got} ({result:?})",
                case["value"], case["expect"]
            ));
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

#[test]
fn formula_write_cases_match_the_shared_table() {
    let mut failures = Vec::new();
    for case in cases()["formula_writes"].as_array().unwrap() {
        let file = case["file"].as_str().unwrap();
        let sheet = case["sheet"].as_str().unwrap();
        let address = case["address"].as_str().unwrap();
        let formula = case["formula"].as_str().unwrap();
        let imported = import_xlsx_bytes(&corpus(file), BTreeMap::new(), BTreeMap::new()).unwrap();
        let before = imported.snapshot();
        let result = imported.apply(vec![Edit::formula(sheet, address, formula)], None);
        let got = match &result {
            Ok(_) => "ok".to_string(),
            Err(error) => error.code().to_string(),
        };
        if got != case["expect"].as_str().unwrap() {
            failures.push(format!(
                "{file} {sheet}!{address}=({formula}): expected {}, got {got} ({result:?})",
                case["expect"]
            ));
        } else if result.is_err() && imported.snapshot() != before {
            failures.push(format!("{file} {sheet}!{address}: refused edit changed the model"));
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

#[test]
fn formula_results_are_checked_with_the_whole_batch_and_at_export() {
    let imported =
        import_xlsx_bytes(&corpus("xw_dropdowns.xlsx"), BTreeMap::new(), BTreeMap::new()).unwrap();
    imported
        .apply(
            vec![
                Edit::value("Orders", "G3", CellValue::Text("Blue".into())),
                Edit::formula("Orders", "C3", "G3"),
            ],
            None,
        )
        .unwrap();
    let refused = imported
        .apply(
            vec![
                Edit::value("Orders", "G4", CellValue::Text("Pink".into())),
                Edit::formula("Orders", "C4", "G4"),
            ],
            None,
        )
        .unwrap_err();
    assert_eq!(refused.code(), "validation_failed");
    let unknown = imported
        .apply(vec![Edit::formula("Orders", "C5", "NOSUCHFUNCTION(1)")], None)
        .unwrap_err();
    assert_eq!(unknown.code(), "validation_failed");
    assert!(unknown.to_string().contains("could not be calculated"), "{unknown}");
    imported
        .apply(vec![Edit::formula("Orders", "A5", "NOSUCHFUNCTION(1)")], None)
        .unwrap();

    let fresh =
        import_xlsx_bytes(&corpus("xw_dropdowns.xlsx"), BTreeMap::new(), BTreeMap::new()).unwrap();
    let dir = std::env::temp_dir().join(format!("wf-dv-formula-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let mut model = fresh.snapshot();
    let orders = model.sheets.iter_mut().find(|s| s.name == "Orders").unwrap();
    let cell = orders.cells.get_mut("C2").unwrap();
    cell.formula = Some("\"Pur\"&\"ple\"".into());
    let refused = fresh.export_snapshot(model.clone(), dir.join("bad.xlsx")).unwrap_err();
    assert_eq!(refused.code(), "validation_failed", "{refused}");
    let orders = model.sheets.iter_mut().find(|s| s.name == "Orders").unwrap();
    orders.cells.get_mut("C2").unwrap().formula = Some("\"Bl\"&\"ue\"".into());
    fresh.export_snapshot(model, dir.join("ok.xlsx")).unwrap();
    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn inspect_cases_match_the_shared_table() {
    let cases = cases();
    for (name, want) in cases["inspect"].as_object().unwrap() {
        let imported = import_xlsx_bytes(&corpus(name), BTreeMap::new(), BTreeMap::new()).unwrap();
        let got = imported.capabilities();
        if let Some(count) = want.get("data_validations") {
            let rules = got["data_validations"].as_array().unwrap();
            assert_eq!(rules.len() as u64, count.as_u64().unwrap(), "{name}");
            let enforced = rules.iter().filter(|r| r["enforced"] == true).count() as u64;
            assert_eq!(enforced, want["enforced"].as_u64().unwrap(), "{name}");
            for (sqref, values) in want
                .get("allowed_values")
                .and_then(Json::as_object)
                .into_iter()
                .flatten()
            {
                let rule = rules
                    .iter()
                    .find(|r| {
                        r["sqref"]
                            .as_array()
                            .unwrap()
                            .iter()
                            .map(|s| s.as_str().unwrap())
                            .collect::<Vec<_>>()
                            .join(" ")
                            == *sqref
                    })
                    .unwrap();
                let got: Vec<String> = rule["allowed_values"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|v| v.to_string().trim_end_matches(".0").to_string())
                    .collect();
                let want: Vec<String> = values
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|v| v.to_string())
                    .collect();
                assert_eq!(got, want, "{name} {sqref}");
            }
        }
        if let Some(pivots) = want.get("pivots") {
            let got: Vec<Json> = got["pivots"]
                .as_array()
                .unwrap()
                .iter()
                .map(|p| {
                    assert!(p["refresh"].as_str().unwrap().starts_with("unsupported"));
                    serde_json::json!({
                        "sheet": p["sheet"], "location": p["location"],
                        "source_sheet": p["source"]["sheet"], "source_ref": p["source"]["ref"],
                        "row_fields": p["row_fields"], "column_fields": p["column_fields"],
                        "page_fields": p["page_fields"],
                        "data_fields": p["data_fields"].as_array().unwrap().iter().map(|d| d["field"].clone()).collect::<Vec<_>>(),
                    })
                })
                .collect();
            assert_eq!(&Json::Array(got), pivots, "{name}");
        }
        if let Some(charts) = want.get("charts") {
            let got = got["charts"].as_array().unwrap();
            let charts = charts.as_array().unwrap();
            assert_eq!(got.len(), charts.len(), "{name}");
            for (chart, expected) in got.iter().zip(charts) {
                assert_eq!(chart["types"], expected["types"], "{name}");
                assert_eq!(
                    chart["series"].as_array().unwrap().len() as u64,
                    expected["series"].as_u64().unwrap()
                );
                if let Some(anchor) = expected.get("anchor").filter(|a| !a.is_null()) {
                    assert_eq!(&chart["anchor"], anchor, "{name}");
                }
            }
        }
    }
}

#[test]
fn a_value_edit_keeps_feature_parts_byte_for_byte() {
    for name in [
        "hb_pivot_cache.xlsx",
        "hb_pivot_fields.xlsx",
        "op_charts.xlsx",
        "xw_chart_image.xlsx",
        "xw_chartsheet.xlsx",
    ] {
        let bytes = corpus(name);
        let imported = import_xlsx_bytes(&bytes, BTreeMap::new(), BTreeMap::new()).unwrap();
        let sheet = imported.snapshot().sheets[0].name.clone();
        imported
            .apply(
                vec![Edit::value(&sheet, "Z99", CellValue::Number(1.0))],
                None,
            )
            .unwrap();
        let exported = imported.export_bytes().unwrap();
        let mut before = zip::ZipArchive::new(std::io::Cursor::new(bytes.clone())).unwrap();
        let mut after = zip::ZipArchive::new(std::io::Cursor::new(exported.clone())).unwrap();
        for index in 0..before.len() {
            let mut entry = before.by_index(index).unwrap();
            let part = entry.name().to_string();
            if !(part.contains("pivot") || part.contains("chart") || part.contains("drawing")) {
                continue;
            }
            let mut a = Vec::new();
            std::io::Read::read_to_end(&mut entry, &mut a).unwrap();
            let mut b = Vec::new();
            std::io::Read::read_to_end(&mut after.by_name(&part).unwrap(), &mut b).unwrap();
            assert_eq!(a, b, "{name} {part}");
        }
        let again = import_xlsx_bytes(&exported, BTreeMap::new(), BTreeMap::new()).unwrap();
        assert_eq!(
            again.capabilities()["pivots"],
            imported.capabilities()["pivots"]
        );
        assert_eq!(
            again.capabilities()["charts"],
            imported.capabilities()["charts"]
        );
    }
}

#[test]
fn renaming_a_charted_sheet_refuses() {
    let imported =
        import_xlsx_bytes(&corpus("op_charts.xlsx"), BTreeMap::new(), BTreeMap::new()).unwrap();
    let mut model = imported.snapshot();
    model.sheets[0].name = "Renamed".into();
    let folder = std::env::temp_dir().join(format!("wf-chart-{}", std::process::id()));
    std::fs::create_dir_all(&folder).unwrap();
    let error = imported
        .export_snapshot(model, folder.join("out.xlsx"))
        .expect_err("refused");
    assert_eq!(error.code(), "chart_reference_protected");
    let _ = std::fs::remove_dir_all(folder);
}

#[test]
fn text_writes_store_the_expected_type() {
    for case in cases()["text_writes"].as_array().unwrap() {
        let sheet = case["sheet"].as_str().unwrap();
        let address = case["address"].as_str().unwrap();
        let imported = import_xlsx_bytes(
            &corpus(case["file"].as_str().unwrap()),
            BTreeMap::new(),
            BTreeMap::new(),
        )
        .unwrap();
        imported
            .apply(
                vec![Edit::value(sheet, address, value(&case["value"]))],
                None,
            )
            .unwrap();
        let expected = value(&case["stored"]);
        let check = |model: &workbook_forge::toolkit::WorkbookModel| {
            let cell = &model.sheets.iter().find(|s| s.name == sheet).unwrap().cells[address];
            assert_eq!(cell.value, expected, "{address} <- {}", case["value"]);
        };
        check(&imported.snapshot());
        let exported = imported.export_bytes().unwrap();
        check(
            &import_xlsx_bytes(&exported, BTreeMap::new(), BTreeMap::new())
                .unwrap()
                .snapshot(),
        );
    }
}

#[test]
fn text_round_trips_exactly() {
    for name in [
        "hb_text_edge_cases.xlsx",
        "op_text_cells.xlsx",
        "xw_text_formats.xlsx",
    ] {
        let first = import_xlsx_bytes(&corpus(name), BTreeMap::new(), BTreeMap::new())
            .unwrap()
            .snapshot()
            .sheets[0]
            .name
            .clone();
        // Bind one cell as the output so formulas the engine does not
        // calculate are preserved rather than blocking export.
        let outputs = BTreeMap::from([(
            "edited".to_string(),
            workbook_forge::toolkit::CellAddress::new(first.clone(), "A1".to_string()),
        )]);
        let imported = import_xlsx_bytes(&corpus(name), BTreeMap::new(), outputs).unwrap();
        let before = imported.snapshot();
        imported
            .apply(
                vec![Edit::value(&first, "Z99", CellValue::Text("edited".into()))],
                None,
            )
            .unwrap();
        let after = import_xlsx_bytes(
            &imported.export_bytes().unwrap(),
            BTreeMap::new(),
            BTreeMap::new(),
        )
        .unwrap()
        .snapshot();
        for (old, new) in before.sheets.iter().zip(&after.sheets) {
            for (address, cell) in &old.cells {
                assert_eq!(
                    new.cells[address].value, cell.value,
                    "{name} {}!{address}",
                    old.name
                );
            }
        }
    }
}
