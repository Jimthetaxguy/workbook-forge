//! Spec 09 step 0: the two conversion edges between the canonical and the
//! toolkit workbook shapes, pinned by `fixtures/shape-conversion-cases.v1.json`.
//! Python runs the same fixture in `python/tests/test_shape_conversion.py`.

use serde_json::{Value as Json, json};
use std::collections::{BTreeMap, BTreeSet};
use workbook_forge::convert::{Conversion, ConversionError, to_canonical, to_toolkit};
use workbook_forge::model;
use workbook_forge::toolkit::{
    Cell, CellReference, CellValue, Edit, Session, Sheet, WorkbookModel,
};
use workbook_forge::xlsx::import_xlsx_bytes;

fn corpus(name: &str) -> Vec<u8> {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../fixtures/corpus")
        .join(name);
    std::fs::read(path).expect("corpus file")
}

fn fixture() -> Json {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../fixtures/shape-conversion-cases.v1.json"
    );
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn operating_scenario() -> Json {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../fixtures/operating-scenario.workbook.json"
    );
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn cases(fixture: &Json, group: &str) -> Vec<Json> {
    fixture[group].as_array().unwrap().clone()
}

fn hydrate(document: &Json) -> model::Workbook {
    model::hydrate(&serde_json::to_vec(document).unwrap()).unwrap()
}

fn toolkit_model(document: &Json) -> WorkbookModel {
    serde_json::from_value(document.clone()).unwrap()
}

fn canonical_json(workbook: &model::Workbook) -> Json {
    serde_json::from_str(&workbook.to_json().unwrap()).unwrap()
}

/// The normalised toolkit form: what the toolkit reads back from its own
/// JSON (an in-memory blank cache is written as null and read as no cache).
fn normalised_toolkit_json(model: &WorkbookModel) -> Json {
    serde_json::to_value(toolkit_model(&serde_json::to_value(model).unwrap())).unwrap()
}

fn input_kinds(case: &Json) -> BTreeMap<String, String> {
    match case["input_kinds"].as_object() {
        Some(kinds) => kinds
            .iter()
            .map(|(name, kind)| (name.clone(), kind.as_str().unwrap().to_string()))
            .collect(),
        None => BTreeMap::new(),
    }
}

/// Every input of a toolkit document typed as the document types it.
fn kinds_of(model: &WorkbookModel) -> BTreeMap<String, String> {
    model
        .inputs
        .iter()
        .map(|(name, input)| (name.clone(), input.kind.clone()))
        .collect()
}

fn all_number(workbook: &model::Workbook) -> BTreeMap<String, String> {
    workbook
        .bindings
        .iter()
        .flat_map(|b| b.inputs.keys())
        .map(|name| (name.clone(), "number".to_string()))
        .collect()
}

enum Segment {
    Key(String),
    Index(usize),
}

/// Split a conversion path into its steps. Keys that may hold a dot
/// (`metadata.<key>`, `bindings.inputs.<name>`) are taken whole.
fn segments(path: &str) -> Vec<Segment> {
    if let Some(key) = path.strip_prefix("metadata.") {
        return vec![Segment::Key("metadata".into()), Segment::Key(key.into())];
    }
    if let Some(rest) = path.strip_prefix("bindings.inputs.") {
        let mut out = vec![
            Segment::Key("bindings".into()),
            Segment::Key("inputs".into()),
        ];
        let suffixes = [".kind", ".required", ".min", ".max", ".choices"];
        match suffixes
            .iter()
            .find_map(|s| rest.strip_suffix(s).map(|n| (n, &s[1..])))
        {
            Some((name, field)) => {
                out.push(Segment::Key(name.into()));
                out.push(Segment::Key(field.into()));
            }
            None => out.push(Segment::Key(rest.into())),
        }
        return out;
    }
    let mut out = Vec::new();
    for piece in path.split('.') {
        match piece.split_once('[') {
            Some((key, index)) => {
                out.push(Segment::Key(key.into()));
                let index = index.strip_suffix(']').unwrap().parse().unwrap();
                out.push(Segment::Index(index));
            }
            None => out.push(Segment::Key(piece.into())),
        }
    }
    out
}

fn remove_empty_objects(value: &mut Json) {
    match value {
        Json::Object(map) => {
            for item in map.values_mut() {
                remove_empty_objects(item);
            }
            map.retain(|_, item| !item.as_object().is_some_and(|o| o.is_empty()));
        }
        Json::Array(items) => items.iter_mut().for_each(remove_empty_objects),
        _ => {}
    }
}

/// Strip one leading `=` from every formula string (toolkit `formula`,
/// canonical `formula.expression`): the canonical shape spells a formula
/// with it and the importers' raw OOXML text without, one formula either way.
fn unprefix_formulas(value: &mut Json) {
    match value {
        Json::Object(map) => {
            for key in ["formula", "expression"] {
                if let Some(Json::String(text)) = map.get_mut(key)
                    && let Some(rest) = text.strip_prefix('=')
                {
                    *text = rest.to_string();
                }
            }
            map.values_mut().for_each(unprefix_formulas);
        }
        Json::Array(items) => items.iter_mut().for_each(unprefix_formulas),
        _ => {}
    }
}

/// Delete each listed path from a JSON document: a `diagnostics[i]` path
/// removes `diagnostics`; a missing path is ignored. The grammar nests
/// bindings as the canonical shape does, so on a toolkit document
/// `bindings.inputs.<name>` means its top-level `inputs.<name>`. Objects a
/// deletion leaves empty are removed on both sides afterwards, because the
/// normalised target form omits an empty optional object (`style`) where the
/// source wrote one, and both readers refuse a document missing a required one.
/// Formula spellings with and without a leading `=` are read as one.
fn without(json: &Json, paths: &BTreeSet<String>) -> Json {
    let mut json = json.clone();
    unprefix_formulas(&mut json);
    let toolkit_shape = json.get("bindings").is_none() && json.get("inputs").is_some();
    for path in paths {
        let mut segments = if path.starts_with("diagnostics[") {
            vec![Segment::Key("diagnostics".into())]
        } else {
            segments(path)
        };
        if toolkit_shape && path.starts_with("bindings.") {
            segments.remove(0);
        }
        let (last, parents) = segments.split_last().unwrap();
        let mut cursor = Some(&mut json);
        for segment in parents {
            cursor = match (cursor, segment) {
                (Some(Json::Object(map)), Segment::Key(key)) => map.get_mut(key),
                (Some(Json::Array(items)), Segment::Index(index)) => items.get_mut(*index),
                _ => None,
            };
        }
        if let (Some(Json::Object(map)), Segment::Key(key)) = (cursor, last) {
            map.remove(key);
        }
    }
    remove_empty_objects(&mut json);
    json
}

fn is_index(text: &str) -> bool {
    !text.is_empty() && text.bytes().all(|b| b.is_ascii_digit())
}

fn is_column(text: &str) -> bool {
    !text.is_empty() && text.bytes().all(|b| b.is_ascii_uppercase())
}

/// A canonical A1 address: uppercase letters then digits, no anchors.
fn is_address(text: &str) -> bool {
    text.bytes()
        .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit())
        && CellReference::parse(None, text).is_ok()
}

/// The path grammar of the plan's shared definitions, one arm per row.
fn matches_grammar(path: &str) -> bool {
    if matches!(path, "model_version" | "provenance" | "revision") {
        return true;
    }
    if let Some(key) = path.strip_prefix("metadata.") {
        return !key.is_empty();
    }
    if let Some(index) = path.strip_prefix("diagnostics[") {
        return index.strip_suffix(']').is_some_and(is_index);
    }
    if let Some(rest) = path.strip_prefix("bindings.inputs.") {
        let fields = ["kind", "required", "min", "max", "choices"];
        return match rest.rsplit_once('.') {
            Some((name, field)) if fields.contains(&field) => !name.is_empty(),
            _ => !rest.is_empty(),
        };
    }
    let Some(rest) = path.strip_prefix("sheets[") else {
        return false;
    };
    let Some((index, field)) = rest.split_once("].") else {
        return false;
    };
    if !is_index(index) {
        return false;
    }
    if matches!(field, "id" | "dimensions") {
        return true;
    }
    if let Some(column) = field.strip_prefix("column_widths.") {
        return is_column(column);
    }
    let Some(cell) = field.strip_prefix("cells.") else {
        return false;
    };
    let Some((address, field)) = cell.split_once('.') else {
        return false;
    };
    if !is_address(address) {
        return false;
    }
    match field {
        "data_type" | "provenance" | "cached_value" | "blocked_reason" => true,
        "formula.dependencies" | "formula.result" => true,
        other => other.strip_prefix("style.").is_some_and(|key| {
            matches!(
                key,
                "number_format" | "bold" | "font_color" | "fill_color" | "horizontal"
            )
        }),
    }
}

fn union(a: &Conversion, b: &Conversion) -> BTreeSet<String> {
    [&a.dropped, &a.added, &b.dropped, &b.added]
        .into_iter()
        .flatten()
        .cloned()
        .collect()
}

#[test]
fn formula_spelling_gains_a_leading_equals_and_is_not_listed() {
    // The importers keep the raw OOXML text; the canonical shape spells every
    // expression with a leading `=`. A representation change, like `#N/A!`.
    let model = toolkit_model(&json!({
        "schema_version": 1,
        "sheets": [{"id": "s", "name": "S", "cells": {
            "A2": {"value": 1.0},
            "A3": {"value": 2.0},
            "B1": {"value": null, "formula": "A2+A3"},
            "B2": {"value": null, "formula": "=A2+A3"}
        }}]
    }));
    let (workbook, conversion) = to_canonical(&model, None).unwrap();
    let cells = &workbook.sheets[0].cells;
    for address in ["B1", "B2"] {
        let formula = cells[address].formula.as_ref().unwrap();
        assert_eq!(formula.expression, "=A2+A3", "{address}");
        assert_eq!(formula.dependencies, ["S!A2", "S!A3"], "{address}");
        let listed: Vec<_> = union(&conversion, &Conversion::default())
            .into_iter()
            .filter(|p| p.contains(&format!(".{address}.")))
            .collect();
        assert_eq!(
            listed,
            [
                format!("sheets[0].cells.{address}.data_type"),
                format!("sheets[0].cells.{address}.formula.dependencies"),
                format!("sheets[0].cells.{address}.formula.result"),
            ],
            "{address}"
        );
    }
    // The way back keeps the expression text as the canonical document gives it.
    let (back, _) = to_toolkit(&workbook, &BTreeMap::new()).unwrap();
    assert_eq!(
        back.sheets[0].cells["B1"].formula.as_deref(),
        Some("=A2+A3")
    );
}

/// `#N/A!` and `#N/A` are one error to the toolkit reader; the canonical
/// shape spells it `#N/A`, so a round trip comes back with that spelling.
fn spell_na(value: &mut Json) {
    match value {
        Json::Object(map) => {
            if map.get("error") == Some(&Json::from("#N/A!")) {
                map.insert("error".into(), Json::from("#N/A"));
            }
            map.values_mut().for_each(spell_na);
        }
        Json::Array(items) => items.iter_mut().for_each(spell_na),
        _ => {}
    }
}

#[test]
fn to_toolkit_matches_fixture_cases() {
    let fixture = fixture();
    for case in cases(&fixture, "canonical_cases") {
        let name = case["name"].as_str().unwrap();
        let workbook = hydrate(&case["canonical"]);
        let (model, conversion) = to_toolkit(&workbook, &input_kinds(&case))
            .unwrap_or_else(|error| panic!("{name}: {error}"));
        assert_eq!(
            serde_json::to_value(&model).unwrap(),
            case["toolkit"],
            "{name}: toolkit document"
        );
        assert_eq!(
            serde_json::to_value(&conversion).unwrap(),
            case["conversion"],
            "{name}: conversion record"
        );
    }
}

#[test]
fn to_toolkit_refusals() {
    let fixture = fixture();
    let mut seen = 0;
    for case in cases(&fixture, "refusals") {
        if case["direction"] != "to_toolkit" {
            continue;
        }
        seen += 1;
        let name = case["name"].as_str().unwrap();
        let workbook = hydrate(&case["document"]);
        let error: ConversionError = to_toolkit(&workbook, &input_kinds(&case))
            .err()
            .unwrap_or_else(|| panic!("{name}: accepted"));
        assert_eq!(error.code, case["code"], "{name}");
        assert_eq!(
            error.to_string(),
            format!("{}: {}", error.code, error.message)
        );
        if name == "input-kinds-must-name-a-bound-input" {
            // The same sentence the Python conversion and the toolkit reader use.
            assert_eq!(error.message, "unknown input b");
        }
    }
    assert!(seen >= 2, "fixture holds {seen} to_toolkit refusals");
}

#[test]
fn to_toolkit_refuses_unsupported_versions() {
    // A hand-built workbook never passed through `hydrate`; the model's own
    // version rule refuses it with the model's own code.
    let fixture = fixture();
    let case = &cases(&fixture, "canonical_cases")[0];
    let mut workbook = hydrate(&case["canonical"]);
    workbook.model_version = 2;
    let error = to_toolkit(&workbook, &input_kinds(case)).expect_err("refused");
    assert_eq!(error.code, "unsupported_model_version");
    let mut workbook = hydrate(&case["canonical"]);
    workbook.schema_version = 2;
    let error = to_toolkit(&workbook, &input_kinds(case)).expect_err("refused");
    assert_eq!(error.code, "unsupported_schema_version");
}

#[test]
fn to_toolkit_output_calculates_in_the_toolkit_session() {
    let fixture = fixture();
    let scenario = hydrate(&operating_scenario());
    let mut documents: Vec<(String, model::Workbook, BTreeMap<String, String>)> = vec![(
        "operating-scenario".into(),
        scenario.clone(),
        all_number(&scenario),
    )];
    for case in cases(&fixture, "canonical_cases") {
        let name = case["name"].as_str().unwrap().to_string();
        documents.push((name, hydrate(&case["canonical"]), input_kinds(&case)));
    }
    for (name, workbook, kinds) in documents {
        let (model, _) = to_toolkit(&workbook, &kinds).unwrap();
        let session = Session::new(model).unwrap_or_else(|error| panic!("{name}: {error}"));
        let report = session.calculate(1);
        assert!(
            report.diagnostics.iter().all(|d| d.code != "invalid_model"),
            "{name}: {:?}",
            report.diagnostics
        );
    }
}

#[test]
fn to_canonical_matches_fixture_cases() {
    let fixture = fixture();
    for case in cases(&fixture, "toolkit_cases") {
        let name = case["name"].as_str().unwrap();
        let model = toolkit_model(&case["toolkit"]);
        let (workbook, conversion) =
            to_canonical(&model, None).unwrap_or_else(|error| panic!("{name}: {error}"));
        assert_eq!(
            canonical_json(&workbook),
            case["canonical"],
            "{name}: canonical document"
        );
        assert_eq!(
            serde_json::to_value(&conversion).unwrap(),
            case["conversion"],
            "{name}: conversion record"
        );
    }
}

#[test]
fn to_canonical_refusals() {
    let fixture = fixture();
    let mut seen = 0;
    for case in cases(&fixture, "refusals") {
        if case["direction"] != "to_canonical" {
            continue;
        }
        seen += 1;
        let name = case["name"].as_str().unwrap();
        let model = toolkit_model(&case["document"]);
        let error = to_canonical(&model, None)
            .err()
            .unwrap_or_else(|| panic!("{name}: accepted"));
        assert_eq!(error.code, case["code"], "{name}");
    }
    assert!(seen >= 2, "fixture holds {seen} to_canonical refusals");
}

#[test]
fn na_bang_is_spelled_na() {
    // Review focus 2 and 5: the toolkit accepts `#N/A!` and an error with no
    // message; the canonical shape spells `#N/A` and writes `message: null`.
    let model = toolkit_model(&json!({
        "schema_version": 1,
        "sheets": [{"id": "s", "name": "S", "cells": {
            "A1": {"value": {"error": "#N/A!"}},
            "B1": {"value": null, "formula": "=NA()", "cached_value": {"error": "#N/A!"}}
        }}]
    }));
    let (workbook, conversion) = to_canonical(&model, None).unwrap();
    let document = canonical_json(&workbook);
    let error = json!({"error": "#N/A", "message": null});
    for address in ["A1", "B1"] {
        let cell = &document["sheets"][0]["cells"][address];
        assert_eq!(cell["value"], error, "{address}");
        assert_eq!(cell["data_type"], "error", "{address}");
    }
    assert_eq!(
        document["sheets"][0]["cells"]["B1"]["formula"]["result"],
        Json::Null
    );
    assert_eq!(
        document["sheets"][0]["cells"]["B1"]["provenance"],
        json!({"origin": "imported"})
    );
    let listed: Vec<_> = union(&conversion, &Conversion::default())
        .into_iter()
        .filter(|p| p.contains(".A1."))
        .collect();
    assert_eq!(listed, ["sheets[0].cells.A1.data_type"]);
}

#[test]
fn integer_rule_is_inclusive_at_two_to_the_53_and_keeps_negative_zero_a_float() {
    let model = toolkit_model(&json!({
        "schema_version": 1,
        "sheets": [{"id": "s", "name": "S", "cells": {
            "A1": {"value": -0.0},
            "A2": {"value": 9007199254740992.0},
            "A3": {"value": 9007199254740994.0},
            "A4": {"value": -3.0}
        }}]
    }));
    let (workbook, _) = to_canonical(&model, None).unwrap();
    let number = |address: &str| match &workbook.sheets[0].cells[address].value {
        model::Scalar::Number(number) => number.clone(),
        other => panic!("{address}: {other:?}"),
    };
    assert!(number("A1").is_f64(), "negative zero stays a float");
    assert!(number("A1").as_f64().unwrap().is_sign_negative());
    assert_eq!(
        number("A2"),
        serde_json::Number::from(9_007_199_254_740_992_i64)
    );
    assert!(number("A3").is_f64(), "beyond 2^53 stays a float");
    assert_eq!(number("A4"), serde_json::Number::from(-3_i64));
    assert!(workbook.to_json().unwrap().contains("\"value\":-0.0"));

    let (back, _) = to_toolkit(&workbook, &BTreeMap::new()).unwrap();
    let json = serde_json::to_value(&back).unwrap();
    let a1 = json["sheets"][0]["cells"]["A1"]["value"].as_f64().unwrap();
    assert!(a1 == 0.0 && a1.is_sign_negative());
}

#[test]
fn unnormalised_toolkit_document_lists_required() {
    // Review focus 1: the drop list is relative to the normalised toolkit
    // form, so `required` and `revision` are listed although never written.
    let fixture = fixture();
    let case = cases(&fixture, "toolkit_cases")
        .into_iter()
        .find(|c| c["name"] == "unnormalised-input")
        .unwrap();
    assert!(case["toolkit"].get("revision").is_none());
    assert!(case["toolkit"]["inputs"]["x"].get("required").is_none());
    let (workbook, conversion) = to_canonical(&toolkit_model(&case["toolkit"]), None).unwrap();
    assert!(
        conversion
            .dropped
            .contains(&"bindings.inputs.x.required".to_string())
    );
    assert!(conversion.dropped.contains(&"revision".to_string()));
    let addresses: Vec<_> = workbook.sheets[0].cells.keys().cloned().collect();
    assert_eq!(addresses, ["A1", "B1"]);
}

#[test]
fn canonical_round_trip_restores_everything_not_listed() {
    let fixture = fixture();
    let scenario = operating_scenario();
    let mut documents = vec![(
        "operating-scenario".to_string(),
        scenario.clone(),
        all_number(&hydrate(&scenario)),
    )];
    for case in cases(&fixture, "canonical_cases") {
        let name = case["name"].as_str().unwrap().to_string();
        documents.push((name, case["canonical"].clone(), input_kinds(&case)));
    }
    for (name, original, kinds) in documents {
        let (model, forward) = to_toolkit(&hydrate(&original), &kinds).unwrap();
        let (workbook, back) = to_canonical(&model, None).unwrap();
        let listed = union(&forward, &back);
        assert_eq!(
            without(&original, &listed),
            without(&canonical_json(&workbook), &listed),
            "{name}"
        );
    }
}

#[test]
fn every_importable_corpus_workbook_converts_and_round_trips() {
    let folder = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../fixtures/corpus");
    // Workbooks the toolkit imports but the canonical reader refuses: a
    // blocked formula cell (a data table, a shared-formula follower) carries
    // an empty expression, which the canonical shape cannot hold. The
    // conversion reports the reader's own code rather than inventing a
    // representation; Python refuses the same files the same way (spec 09
    // row 0 gap, recorded in the run report).
    let known_canonical_refusals: BTreeMap<String, &str> = [
        ("hb_data_table_formula.xlsx".to_string(), "invalid_model"),
        ("hb_shared_formulas.xlsx".to_string(), "invalid_model"),
    ]
    .into();
    let mut converted = 0;
    let mut import_refused = 0;
    let mut canonical_refused = BTreeMap::new();
    let mut with_diagnostics: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut entries: Vec<_> = std::fs::read_dir(&folder)
        .expect("corpus folder")
        .map(|entry| entry.expect("entry").path())
        .filter(|path| path.extension().and_then(|s| s.to_str()) == Some("xlsx"))
        .collect();
    entries.sort();
    for path in entries {
        let name = path.file_name().unwrap().to_string_lossy().to_string();
        let bytes = std::fs::read(&path).expect("read");
        let Ok(imported) = import_xlsx_bytes(&bytes, BTreeMap::new(), BTreeMap::new()) else {
            import_refused += 1;
            continue;
        };
        let envelope = imported.envelope();
        let snapshot = imported.snapshot();
        let (workbook, forward) = match to_canonical(&snapshot, Some(&envelope)) {
            Ok(converted) => converted,
            Err(error) => {
                canonical_refused.insert(name.clone(), error.code.clone());
                continue;
            }
        };
        converted += 1;
        // Every analyzer refusal is a diagnostic listed as added, in index
        // order, and nothing past the last one is listed.
        for index in 0..workbook.diagnostics.len() {
            let path = format!("diagnostics[{index}]");
            assert!(forward.added.contains(&path), "{name}: {path} not listed");
        }
        let past_end = format!("diagnostics[{}]", workbook.diagnostics.len());
        assert!(
            !forward.added.contains(&past_end),
            "{name}: {past_end} listed"
        );
        if !workbook.diagnostics.is_empty() {
            let codes = workbook
                .diagnostics
                .iter()
                .map(|d| d.code.clone())
                .collect();
            with_diagnostics.insert(name.clone(), codes);
        }
        let text = workbook.to_json().unwrap();
        model::hydrate(text.as_bytes()).unwrap_or_else(|error| panic!("{name}: {error}"));
        let document = canonical_json(&workbook);
        assert_eq!(
            document["metadata"]["source"]["fingerprint"],
            Json::from(envelope.source.fingerprint.clone()),
            "{name}"
        );
        assert_eq!(
            document["provenance"],
            json!({"origin": "imported", "source": envelope.source.workbook_part}),
            "{name}"
        );
        let (back, backward) = to_toolkit(&workbook, &BTreeMap::new())
            .unwrap_or_else(|error| panic!("{name}: to_toolkit {error}"));
        let listed = union(&forward, &backward);
        for path in &listed {
            assert!(
                matches_grammar(path),
                "{name}: path {path} is outside the grammar"
            );
        }
        let mut original = normalised_toolkit_json(&snapshot);
        spell_na(&mut original);
        assert_eq!(
            without(&original, &listed),
            without(&serde_json::to_value(&back).unwrap(), &listed),
            "{name}"
        );
    }
    assert!(
        !with_diagnostics.is_empty(),
        "no corpus workbook exercised the diagnostic path"
    );
    assert_eq!(
        canonical_refused,
        known_canonical_refusals
            .iter()
            .map(|(name, code)| (name.clone(), code.to_string()))
            .collect(),
        "canonical-reader refusals differ from the known set"
    );
    assert!(
        converted >= 40,
        "corpus: {converted} workbooks converted, {import_refused} refused by the importer"
    );
}

#[test]
fn envelope_marks_only_baseline_cells_imported() {
    // F1: the baseline document says which cells the package held; a cell
    // added or changed since import, or on a sheet the import never saw, is
    // authored with no source. The workbook itself stays imported.
    let imported = import_xlsx_bytes(
        &corpus("op_merged_cells.xlsx"),
        BTreeMap::new(),
        BTreeMap::new(),
    )
    .unwrap();
    let edit = |address: &str, value: Option<CellValue>, formula: Option<&str>| Edit {
        sheet: "Report".into(),
        address: address.into(),
        value,
        formula: formula.map(String::from),
        style: None,
        restore_absent: false,
    };
    imported
        .apply(
            vec![
                edit("Z99", Some(CellValue::Number(1.0)), None),
                edit("Z100", None, Some("Z99*2")),
                edit("B3", Some(CellValue::Number(99.0)), None),
            ],
            None,
        )
        .unwrap();
    let mut model = imported.snapshot();
    let mut later = Sheet::new("Later");
    later
        .cells
        .insert("A1".into(), Cell::value(CellValue::Text("late".into())));
    model.sheets.push(later);

    let envelope = imported.envelope();
    let (workbook, conversion) = to_canonical(&model, Some(&envelope)).unwrap();
    let document = canonical_json(&workbook);
    assert_eq!(
        document["provenance"],
        json!({"origin": "imported", "source": "xl/workbook.xml"})
    );
    let imported_cell = json!({"origin": "imported", "source": "xl/worksheets/sheet1.xml"});
    let authored = json!({"origin": "authored"});
    let report = &document["sheets"][0]["cells"];
    for address in ["A1", "A2", "B2", "C3"] {
        assert_eq!(report[address]["provenance"], imported_cell, "{address}");
    }
    for address in ["Z99", "Z100", "B3"] {
        assert_eq!(report[address]["provenance"], authored, "{address}");
    }
    assert_eq!(document["sheets"][1]["name"], "Later");
    assert_eq!(document["sheets"][1]["cells"]["A1"]["provenance"], authored);
    for path in [
        "sheets[0].cells.A1.provenance",
        "sheets[0].cells.B3.provenance",
        "sheets[0].cells.Z99.provenance",
        "sheets[0].cells.Z100.provenance",
        "sheets[1].cells.A1.provenance",
    ] {
        assert!(conversion.added.contains(&path.to_string()), "{path}");
    }
}

#[test]
fn envelope_marks_a_signed_zero_change_authored() {
    // `CellValue`'s `PartialEq` treats -0.0 and 0.0 as equal. The baseline
    // comparison keeps the sign, so a cell whose value or cache flips only
    // that sign is authored. The same sign on both sides stays imported.
    let imported = import_xlsx_bytes(
        &corpus("op_merged_cells.xlsx"),
        BTreeMap::new(),
        BTreeMap::new(),
    )
    .unwrap();
    let mut envelope = imported.envelope();
    let mut model = imported.snapshot();
    let number = |value: f64| Cell::value(CellValue::Number(value));
    let cached = |value: f64| Cell {
        formula: Some("1+1".to_string()),
        cached_value: Some(CellValue::Number(value)),
        ..Cell::default()
    };
    let baseline = envelope
        .baseline
        .document
        .sheets
        .iter_mut()
        .find(|sheet| sheet.name == "Report")
        .unwrap();
    baseline.cells.insert("Z1".into(), number(0.0));
    baseline.cells.insert("Z2".into(), number(-0.0));
    baseline.cells.insert("Z3".into(), number(0.0));
    baseline.cells.insert("Z4".into(), cached(0.0));
    baseline.cells.insert("Z5".into(), cached(-0.0));
    let current = model
        .sheets
        .iter_mut()
        .find(|sheet| sheet.name == "Report")
        .unwrap();
    current.cells.insert("Z1".into(), number(-0.0));
    current.cells.insert("Z2".into(), number(-0.0));
    current.cells.insert("Z3".into(), number(0.0));
    current.cells.insert("Z4".into(), cached(-0.0));
    current.cells.insert("Z5".into(), cached(-0.0));

    let (workbook, _) = to_canonical(&model, Some(&envelope)).unwrap();
    let document = canonical_json(&workbook);
    assert_eq!(document["sheets"][0]["name"], "Report");
    let report = &document["sheets"][0]["cells"];
    let imported_cell = json!({"origin": "imported", "source": "xl/worksheets/sheet1.xml"});
    let authored = json!({"origin": "authored"});
    assert_eq!(report["A1"]["provenance"], imported_cell);
    assert_eq!(report["Z1"]["provenance"], authored, "value sign");
    assert_eq!(
        report["Z2"]["provenance"], imported_cell,
        "negative zero held"
    );
    assert_eq!(
        report["Z3"]["provenance"], imported_cell,
        "positive zero held"
    );
    assert_eq!(report["Z4"]["provenance"], authored, "cache sign");
    assert_eq!(
        report["Z5"]["provenance"], imported_cell,
        "negative zero cache held"
    );
}

#[test]
fn analyzer_refusals_travel_as_diagnostics() {
    // F2: a formula the canonical analyzer refuses keeps empty dependencies
    // and its refusal becomes a canonical diagnostic, listed as added.
    let model = toolkit_model(&json!({
        "schema_version": 1,
        "sheets": [{"id": "s", "name": "S", "cells": {
            "A1": {"value": null, "formula": "=SUM(B1:B2)"},
            "A2": {"value": null, "formula": "=NOSUCHFUNCTION(("},
            "B1": {"value": 1.0},
            "B2": {"value": 2.0}
        }}]
    }));
    let (workbook, conversion) = to_canonical(&model, None).unwrap();
    let formula = |address: &str| workbook.sheets[0].cells[address].formula.clone().unwrap();
    assert_eq!(formula("A1").dependencies, ["S!B1", "S!B2"]);
    assert!(formula("A2").dependencies.is_empty());
    assert_eq!(workbook.diagnostics.len(), 1, "{:?}", workbook.diagnostics);
    let diagnostic = &workbook.diagnostics[0];
    assert_eq!(diagnostic.code, "parse_error");
    assert_eq!(diagnostic.sheet.as_deref(), Some("S"));
    assert_eq!(diagnostic.address.as_deref(), Some("A2"));
    assert!(conversion.added.contains(&"diagnostics[0]".to_string()));
    assert!(matches_grammar("diagnostics[0]"));
    let document = canonical_json(&workbook);
    assert_eq!(document["diagnostics"].as_array().unwrap().len(), 1);
    let stripped = without(&document, &BTreeSet::from(["diagnostics[0]".to_string()]));
    assert!(stripped.get("diagnostics").is_none());
}

#[test]
fn toolkit_round_trip_restores_everything_not_listed() {
    let fixture = fixture();
    for case in cases(&fixture, "toolkit_cases") {
        let name = case["name"].as_str().unwrap();
        let model = toolkit_model(&case["toolkit"]);
        let (workbook, forward) = to_canonical(&model, None).unwrap();
        let (back_model, back) = to_toolkit(&workbook, &kinds_of(&model)).unwrap();
        let listed = union(&forward, &back);
        let mut original = serde_json::to_value(&model).unwrap();
        spell_na(&mut original);
        assert_eq!(
            without(&original, &listed),
            without(&serde_json::to_value(&back_model).unwrap(), &listed),
            "{name}"
        );
    }
}
