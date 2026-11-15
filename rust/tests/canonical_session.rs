//! The calculation session contract (spec 05; spec 09 step 1) on the Rust
//! engine, replayed from `fixtures/session-input-change.v1.json`.
//!
//! The fixture is hand-authored: every expected value in it is arithmetic,
//! an Excel fact or a stated contract rule. `python/tests/test_canonical_session.py`
//! reads the same file, so the two engines agree by each matching one
//! independent expectation, not by matching each other.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{Value as Json, json};
use workbook_forge::convert::{Conversion, to_toolkit};
use workbook_forge::session::CalculationSession;
use workbook_forge::toolkit::{self, Cell, InputBinding, Sheet};
use workbook_forge::xlsx::import_xlsx_bytes;
use workbook_forge::{
    CalcContext, CellAddress, CellValue, Edit, Session, WorkbookModel, model, operating_scenario,
};

fn fixture_path(name: &str) -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../fixtures")
        .join(name)
}

fn fixture() -> Json {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../fixtures/session-input-change.v1.json"
    );
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

/// The pinned clock and seed every replayed pass uses.
fn context(fixture: &Json) -> CalcContext {
    serde_json::from_value(fixture["calc"].clone()).expect("fixture calc")
}

fn string_map(value: &Json) -> BTreeMap<String, String> {
    value.as_object().map_or_else(BTreeMap::new, |object| {
        object
            .iter()
            .map(|(name, kind)| (name.clone(), kind.as_str().unwrap().to_string()))
            .collect()
    })
}

fn input_values(value: &Json) -> BTreeMap<String, CellValue> {
    serde_json::from_value(value.clone()).expect("step values")
}

fn root_keys(value: &Json) -> BTreeSet<String> {
    value
        .as_object()
        .unwrap_or_else(|| panic!("expected an object, got {value}"))
        .keys()
        .cloned()
        .collect()
}

/// Open one of the fixture's documents by its `entry` and check its `open`
/// block.
fn open(fixture: &Json, doc_id: &str) -> CalculationSession {
    let document = &fixture["documents"][doc_id];
    let session = match document["entry"].as_str() {
        Some("model") => {
            let model: WorkbookModel =
                serde_json::from_value(document["document"].clone()).expect("owner model");
            CalculationSession::new(model, None).expect("session from a model")
        }
        Some("open") => {
            // The exact bytes the fixture pins a SHA-256 for: a file, or the
            // document written out as one line of text.
            let bytes = match document["document_fixture"].as_str() {
                Some(name) => std::fs::read(fixture_path(name)).expect("document fixture"),
                None => {
                    let text = document["document_text"].as_str().expect("document_text");
                    assert_eq!(
                        serde_json::from_str::<Json>(text).unwrap(),
                        document["document"],
                        "{doc_id}: document_text is not the document"
                    );
                    text.as_bytes().to_vec()
                }
            };
            CalculationSession::open(&bytes, &string_map(&document["input_kinds"]), None)
                .expect("open")
        }
        other => panic!("{doc_id}: unknown entry {other:?}"),
    };
    let expected = &document["open"];
    assert_eq!(
        Json::from(session.revision()),
        expected["revision"],
        "{doc_id}: revision"
    );
    assert_eq!(
        json!(session.model_version()),
        expected["model_version"],
        "{doc_id}: model_version"
    );
    match &expected["conversion"] {
        Json::Null => assert_eq!(session.conversion(), None, "{doc_id}: conversion"),
        Json::String(word) if word == "present" => {
            assert!(session.conversion().is_some(), "{doc_id}: conversion")
        }
        ledger => assert_eq!(
            &serde_json::to_value(session.conversion().expect("conversion")).unwrap(),
            ledger,
            "{doc_id}: conversion"
        ),
    }
    // Null, or a literal digest of the bytes above that the fixture took
    // with a separate tool: never a value read back from a session.
    assert_eq!(
        json!(session.source_fingerprint()),
        expected["source_fingerprint"],
        "{doc_id}: fingerprint"
    );
    session
}

/// The fixture's comparison rule set, applied below the report's root. A
/// report's `source_fingerprint` is compared with the fixture's literal like
/// any other text.
struct Comparison {
    tolerance: f64,
}

impl Comparison {
    fn value(&self, path: &str, actual: &Json, expected: &Json) {
        match (actual, expected) {
            (Json::Number(got), Json::Number(want)) => {
                // Two integers are compared exactly; the tolerance is for
                // calculated numbers.
                let exact = (got.is_u64() || got.is_i64()) && (want.is_u64() || want.is_i64());
                if exact {
                    assert_eq!(got, want, "{path}");
                } else {
                    let (got, want) = (got.as_f64().unwrap(), want.as_f64().unwrap());
                    assert!(
                        (got - want).abs() <= self.tolerance * want.abs().max(1.0),
                        "{path}: expected {want}, got {got}"
                    );
                }
            }
            (_, Json::Object(want)) if want.get("error").is_some_and(Json::is_string) => {
                // An error value: the code is the contract, the message is not.
                assert_eq!(
                    actual.get("error"),
                    want.get("error"),
                    "{path}: expected {expected}, got {actual}"
                );
                let extra: Vec<_> = root_keys(actual)
                    .into_iter()
                    .filter(|key| key != "error" && key != "message")
                    .collect();
                assert!(extra.is_empty(), "{path}: unexpected keys {extra:?}");
            }
            (Json::Object(got), Json::Object(want)) => {
                assert_eq!(
                    got.keys().collect::<Vec<_>>(),
                    want.keys().collect::<Vec<_>>(),
                    "{path}: keys"
                );
                for (key, want) in want {
                    self.value(&format!("{path}.{key}"), &got[key], want);
                }
            }
            (Json::Array(got), Json::Array(want)) => {
                assert_eq!(
                    got.len(),
                    want.len(),
                    "{path}: expected {expected}, got {actual}"
                );
                for (index, (got, want)) in got.iter().zip(want).enumerate() {
                    self.value(&format!("{path}[{index}]"), got, want);
                }
            }
            _ => assert_eq!(actual, expected, "{path}"),
        }
    }

    /// In order, on (code, sheet, address); the message is free text but
    /// must be there.
    fn diagnostics(&self, path: &str, actual: &Json, expected: &Json) {
        let (got, want) = (actual.as_array().unwrap(), expected.as_array().unwrap());
        assert_eq!(
            got.len(),
            want.len(),
            "{path}: expected {expected}, got {actual}"
        );
        for (index, (got, want)) in got.iter().zip(want).enumerate() {
            for key in ["code", "sheet", "address"] {
                assert_eq!(got.get(key), want.get(key), "{path}[{index}].{key}: {got}");
            }
            assert!(
                got["message"].as_str().is_some_and(|text| !text.is_empty()),
                "{path}[{index}]: message must be a non-empty string: {got}"
            );
            let allowed = ["code", "message", "sheet", "address"];
            assert!(
                root_keys(got)
                    .iter()
                    .all(|key| allowed.contains(&key.as_str())),
                "{path}[{index}]: unexpected keys in {got}"
            );
        }
    }

    /// One `calculation-report.v2` against its expectation: every field but
    /// `engine`, which the fixture omits and this engine must set to `rust`.
    fn report(&self, path: &str, actual: &Json, expected: &Json) {
        let mut keys = root_keys(expected);
        keys.insert("engine".into());
        assert_eq!(root_keys(actual), keys, "{path}: report keys");
        assert_eq!(actual["engine"], "rust", "{path}.engine");
        for (key, want) in expected.as_object().unwrap() {
            let at = format!("{path}.{key}");
            if key == "diagnostics" {
                self.diagnostics(&at, &actual[key], want);
            } else {
                self.value(&at, &actual[key], want);
            }
        }
    }
}

fn scenario<'a>(fixture: &'a Json, id: &str) -> &'a Json {
    fixture["scenarios"]
        .as_array()
        .unwrap()
        .iter()
        .find(|scenario| scenario["id"] == id)
        .unwrap_or_else(|| panic!("no scenario {id}"))
}

/// Replay a scenario's steps on one of its documents, up to and including
/// step `through` (every step when `None`). Returns the session as the last
/// replayed step left it.
fn replay(
    fixture: &Json,
    scenario: &Json,
    doc_id: &str,
    through: Option<&str>,
) -> CalculationSession {
    let context = context(fixture);
    let session = open(fixture, doc_id);
    let comparison = Comparison {
        tolerance: fixture["numeric_tolerance"].as_f64().unwrap(),
    };
    let steps = scenario["steps"].as_array().unwrap();
    if let Some(last) = through {
        assert!(
            steps.iter().any(|step| step["id"] == last),
            "no step {last}"
        );
    }
    for step in steps {
        let id = step["id"].as_str().unwrap();
        let at = format!("{}/{doc_id}/{id}", scenario["id"].as_str().unwrap());
        match step["op"].as_str().unwrap() {
            "calculate" => {
                let report = serde_json::to_value(session.calculate_with(1, &context)).unwrap();
                comparison.report(&at, &report, &step["expect"][doc_id]);
            }
            "set_inputs" => {
                let before = session.snapshot();
                let result = session.set_inputs(
                    &input_values(&step["values"]),
                    step["expected_revision"].as_u64().unwrap(),
                );
                match step["error"].as_str() {
                    Some(code) => {
                        assert_eq!(result.expect_err(&at).code, code, "{at}: code");
                        assert_eq!(
                            Json::from(session.revision()),
                            step["revision_after"],
                            "{at}: revision after a refusal"
                        );
                        // A refused batch is atomic: none of it was applied.
                        assert_eq!(session.snapshot(), before, "{at}: model after a refusal");
                    }
                    None => {
                        let revision = result.unwrap_or_else(|error| panic!("{at}: {error:?}"));
                        assert_eq!(Json::from(revision), step["returns"], "{at}: returns");
                        assert_eq!(session.revision(), revision, "{at}: revision");
                    }
                }
            }
            "preview_inputs" => {
                let (revision, before) = (session.revision(), session.snapshot());
                let result = session.preview_inputs(
                    &input_values(&step["values"]),
                    step["expected_revision"].as_u64().unwrap(),
                    &context,
                );
                match step["error"].as_str() {
                    Some(code) => assert_eq!(result.expect_err(&at).code, code, "{at}: code"),
                    None => {
                        let preview = result.unwrap_or_else(|error| panic!("{at}: {error:?}"));
                        let preview = serde_json::to_value(preview).unwrap();
                        let expected = &step["expect"][doc_id];
                        assert_eq!(root_keys(&preview), root_keys(expected), "{at}: keys");
                        assert_eq!(preview["applied"], false, "{at}: applied");
                        for key in ["applied", "base_revision", "proposed_revision", "changes"] {
                            comparison.value(&format!("{at}.{key}"), &preview[key], &expected[key]);
                        }
                        for key in ["before", "after"] {
                            comparison.report(
                                &format!("{at}.{key}"),
                                &preview[key],
                                &expected[key],
                            );
                        }
                    }
                }
                // Detached either way: the session did not move.
                assert_eq!(session.revision(), revision, "{at}: revision after preview");
                assert_eq!(
                    Json::from(session.revision()),
                    step["revision_after"],
                    "{at}: revision_after"
                );
                assert_eq!(session.snapshot(), before, "{at}: model after preview");
            }
            other => panic!("{at}: unknown op {other}"),
        }
        if through == Some(id) {
            break;
        }
    }
    session
}

fn step<'a>(scenario: &'a Json, id: &str) -> &'a Json {
    scenario["steps"]
        .as_array()
        .unwrap()
        .iter()
        .find(|step| step["id"] == id)
        .unwrap_or_else(|| panic!("no step {id}"))
}

#[test]
fn preview_is_detached() {
    let fixture = fixture();
    let scenario = scenario(&fixture, "input-changes");
    // s13 previews unit_price 30 at revision 3. The replay checks that the
    // preview reports revision 4 over base 3 while the session and its model
    // stay where they were, and that the live pass after it (s14) still
    // reuses the session's own published pass and evaluates nothing.
    assert_eq!(step(scenario, "s13")["op"], "preview_inputs");
    assert_eq!(step(scenario, "s14")["op"], "calculate");
    let session = replay(&fixture, scenario, "planner", Some("s14"));
    assert_eq!(session.revision(), 3);
    assert_eq!(
        session.snapshot().sheets[0].cells["B1"].value,
        CellValue::Number(25.0),
        "the previewed price of 30 must not be in the session"
    );
}

#[test]
fn stale_revision_is_revision_conflict() {
    let fixture = fixture();
    let scenario = scenario(&fixture, "input-changes");
    // s07 commits and s12 previews at a revision the session has left.
    for id in ["s07", "s12"] {
        assert_eq!(step(scenario, id)["error"], "revision_conflict", "{id}");
        assert!(
            step(scenario, id)["expected_revision"].as_u64().unwrap()
                < step(scenario, id)["revision_after"].as_u64().unwrap(),
            "{id} must be stale"
        );
    }
    let session = replay(&fixture, scenario, "planner", Some("s12"));
    assert_eq!(session.revision(), 3);
}

#[test]
fn report_matches_python_on_operating_scenario_after_set_inputs() {
    // The Python test reads this same file, so agreement between the engines
    // is each matching one hand-derived expectation, not each other.
    let fixture = fixture();
    let scenario = scenario(&fixture, "input-changes");
    let session = replay(&fixture, scenario, "canonical", None);
    assert_eq!(session.revision(), 4);
    assert_eq!(session.model_version(), Some(1));
}

#[test]
fn shared_input_change_fixture_replays_every_scenario() {
    let fixture = fixture();
    let mut replayed = BTreeSet::new();
    for scenario in fixture["scenarios"].as_array().unwrap() {
        let documents = scenario["documents"].as_array().unwrap();
        assert!(!documents.is_empty(), "{}", scenario["id"]);
        for doc_id in documents {
            let doc_id = doc_id.as_str().unwrap();
            replay(&fixture, scenario, doc_id, None);
            replayed.insert(doc_id.to_string());
        }
    }
    // No document sits in the fixture unreplayed.
    assert_eq!(replayed, root_keys(&fixture["documents"]));
}

fn revision_dropped() -> Conversion {
    Conversion {
        dropped: vec!["revision".into()],
        added: Vec::new(),
    }
}

/// Every Rust reader of a v1 toolkit document, on one text: the session
/// contract's `open`, the plain model reader and the paired reader.
fn readers_accept(text: &str) -> [bool; 3] {
    [
        CalculationSession::open(text.as_bytes(), &BTreeMap::new(), None).is_ok(),
        serde_json::from_str::<WorkbookModel>(text).is_ok(),
        toolkit::read_document(text.as_bytes()).is_ok(),
    ]
}

#[test]
fn revision_key_is_validated_then_dropped() {
    let fixture = fixture();
    let table = &fixture["revision_key"];
    let template = table["template"].as_str().unwrap();
    let opens_at = table["opens_at"].as_u64().unwrap();
    let texts = |list: &str| -> Vec<String> {
        table[list]
            .as_array()
            .unwrap()
            .iter()
            .map(|entry| template.replace('@', entry.as_str().unwrap()))
            .collect()
    };
    let accepted = texts("accept");
    assert!(!accepted.is_empty());
    let absent = table["absent"].as_str().unwrap().to_string();
    for (text, carried) in accepted
        .iter()
        .map(|text| (text, true))
        .chain([(&absent, false)])
    {
        let session = CalculationSession::open(text.as_bytes(), &BTreeMap::new(), None)
            .unwrap_or_else(|error| panic!("{text}: {error:?}"));
        assert_eq!(session.revision(), opens_at, "{text}");
        let dropped = revision_dropped();
        assert_eq!(
            session.conversion(),
            carried.then_some(&dropped),
            "{text}: conversion"
        );
        let model = serde_json::from_str::<WorkbookModel>(text)
            .unwrap_or_else(|error| panic!("{text}: {error}"));
        // Both readers read the same model, and neither keeps the key.
        assert_eq!(session.snapshot(), model, "{text}");
        assert!(!root_keys(&serde_json::to_value(&model).unwrap()).contains("revision"));
        assert_eq!(readers_accept(text), [true; 3], "{text}");
    }
    let refused = texts("refuse");
    assert!(!refused.is_empty());
    for text in &refused {
        assert_eq!(readers_accept(text), [false; 3], "{text}");
        let error = CalculationSession::open(text.as_bytes(), &BTreeMap::new(), None)
            .err()
            .unwrap();
        assert_eq!(error.code, "invalid_model", "{text}");
    }
}

#[test]
fn revision_key_reader_differences_are_the_listed_ones() {
    // Spellings the Python and Rust JSON readers do not treat alike. Both
    // are general JSON-reader differences (-0 is a float here; a derived
    // reader refuses a duplicate field), not revision logic.
    let fixture = fixture();
    let differences = fixture["revision_key"]["reader_differences"]
        .as_array()
        .unwrap();
    let names: Vec<_> = differences
        .iter()
        .map(|entry| entry["name"].as_str().unwrap())
        .collect();
    assert_eq!(names, ["negative-zero", "duplicate-key"]);
    for entry in differences {
        let text = entry["document"].as_str().unwrap();
        let accept = match entry["rust"].as_str().unwrap() {
            "accept" => true,
            "refuse" => false,
            other => panic!("{}: unknown expectation {other}", entry["name"]),
        };
        assert_eq!(readers_accept(text), [accept; 3], "{}", entry["name"]);
        assert_ne!(entry["rust"], entry["python"], "{}", entry["name"]);
    }
}

#[test]
fn a_legacy_revision_seeds_an_engine_session_but_not_a_calculation_session() {
    // The fixture's legacy document carries "revision": 7.
    let fixture = fixture();
    let document = &fixture["documents"]["legacy_revision"]["document"];
    assert_eq!(document["revision"], 7);
    let bytes = serde_json::to_vec(document).unwrap();
    let (model, legacy) = toolkit::read_document(&bytes).unwrap();
    assert_eq!(legacy, 7);
    let engine = Session::with_names_at(model, None, legacy).unwrap();
    assert_eq!(engine.revision(), 7);
    assert!(!root_keys(&serde_json::to_value(engine.snapshot()).unwrap()).contains("revision"));
    // 7 is the engine session's revision, so a batch at 7 commits and gives 8.
    let ten = BTreeMap::from([("a".to_string(), CellValue::Number(10.0))]);
    assert_eq!(engine.set_inputs(&ten, Some(7)).unwrap(), 8);
    // The session contract starts at 0 whatever the document carried.
    let session = CalculationSession::open(&bytes, &BTreeMap::new(), None).unwrap();
    assert_eq!(session.revision(), 0);
    assert_eq!(session.conversion(), Some(&revision_dropped()));
}

fn assert_no_revision_key(what: &str, document: &Json) {
    let keys = root_keys(document);
    assert!(
        keys.contains("schema_version") && keys.contains("sheets"),
        "{what}: not a workbook document: {keys:?}"
    );
    assert!(!keys.contains("revision"), "{what} writes a revision key");
}

#[test]
fn no_writer_emits_a_revision_key() {
    let to_json = |model: &WorkbookModel| serde_json::to_value(model).unwrap();

    assert_no_revision_key("operating_scenario()", &to_json(&operating_scenario()));
    assert_no_revision_key(
        "WorkbookModel::default()",
        &to_json(&WorkbookModel::default()),
    );

    let session = Session::new(operating_scenario()).unwrap();
    assert_no_revision_key("Session::snapshot", &to_json(&session.snapshot()));
    let edit = Edit::value("Assumptions", "B1", CellValue::Number(25.0));
    assert_eq!(session.apply(vec![edit], Some(0)).unwrap(), 1);
    assert_no_revision_key(
        "Session::snapshot after an edit",
        &to_json(&session.snapshot()),
    );
    let (paired, revision) = session.snapshot_with_revision();
    assert_eq!(revision, 1);
    assert_no_revision_key("Session::snapshot_with_revision", &to_json(&paired));
    assert_no_revision_key("Session::fork", &to_json(&session.fork().snapshot()));

    let canonical = std::fs::read(fixture_path("operating-scenario.workbook.json")).unwrap();
    let kinds: BTreeMap<String, String> = ["unit_price", "unit_cost", "fixed_cost"]
        .into_iter()
        .map(|name| (name.to_string(), "number".to_string()))
        .collect();
    let (converted, conversion) = to_toolkit(&model::hydrate(&canonical).unwrap(), &kinds).unwrap();
    assert_no_revision_key("to_toolkit", &to_json(&converted));
    assert!(!conversion.added.iter().any(|path| path == "revision"));

    let opened = CalculationSession::open(&canonical, &kinds, None).unwrap();
    assert_no_revision_key("CalculationSession::snapshot", &to_json(&opened.snapshot()));
    let price = BTreeMap::from([("unit_price".to_string(), CellValue::Number(25.0))]);
    assert_eq!(opened.set_inputs(&price, 0).unwrap(), 1);
    assert_no_revision_key(
        "CalculationSession::snapshot after set_inputs",
        &to_json(&opened.snapshot()),
    );

    let workbook = std::fs::read(fixture_path("corpus/op_merged_cells.xlsx")).unwrap();
    let imported = import_xlsx_bytes(&workbook, BTreeMap::new(), BTreeMap::new()).unwrap();
    assert_no_revision_key("ImportedWorkbook::snapshot", &to_json(&imported.snapshot()));
    assert_no_revision_key(
        "the envelope's baseline document",
        &imported.envelope().describe()["baseline"]["document"],
    );
}

fn planner(fixture: &Json) -> CalculationSession {
    open(fixture, "planner")
}

#[test]
fn unknown_binding_is_invalid_input() {
    let fixture = fixture();
    let session = planner(&fixture);
    let before = session.snapshot();
    let unknown = BTreeMap::from([("nope".to_string(), CellValue::Number(1.0))]);
    assert_eq!(
        session.set_inputs(&unknown, 0).unwrap_err().code,
        "invalid_input"
    );
    assert_eq!(
        session
            .preview_inputs(&unknown, 0, &context(&fixture))
            .unwrap_err()
            .code,
        "invalid_input"
    );
    // A known name beside the unknown one is not applied either.
    let mixed = BTreeMap::from([
        ("unit_price".to_string(), CellValue::Number(30.0)),
        ("nope".to_string(), CellValue::Number(1.0)),
    ]);
    assert_eq!(
        session.set_inputs(&mixed, 0).unwrap_err().code,
        "invalid_input"
    );
    assert_eq!(session.revision(), 0);
    assert_eq!(session.snapshot(), before);
}

#[test]
fn input_kinds_with_a_toolkit_document_is_invalid_request() {
    let fixture = fixture();
    let toolkit_document =
        serde_json::to_vec(&fixture["documents"]["planner"]["document"]).unwrap();
    let kinds = BTreeMap::from([("unit_price".to_string(), "number".to_string())]);
    let error = CalculationSession::open(&toolkit_document, &kinds, None)
        .err()
        .expect("input_kinds does not apply to a toolkit document");
    assert_eq!(error.code, "invalid_request");
    // Without them the same document opens.
    assert!(CalculationSession::open(&toolkit_document, &BTreeMap::new(), None).is_ok());
}

#[test]
fn input_kinds_naming_an_unbound_input_is_unknown_input() {
    let canonical = std::fs::read(fixture_path("operating-scenario.workbook.json")).unwrap();
    let kinds = BTreeMap::from([("nope".to_string(), "number".to_string())]);
    let error = CalculationSession::open(&canonical, &kinds, None)
        .err()
        .expect("the workbook binds no input named nope");
    assert_eq!(error.code, "unknown_input");
}

#[test]
fn canonical_open_without_input_kinds_lists_the_dropped_bindings() {
    // The canonical v1 shape gives an input binding no kind, so without
    // `input_kinds` no input survives conversion (spec 09 step 2 changes
    // that). The file binds unit_price, unit_cost and fixed_cost.
    let canonical = std::fs::read(fixture_path("operating-scenario.workbook.json")).unwrap();
    let session = CalculationSession::open(&canonical, &BTreeMap::new(), None).unwrap();
    assert_eq!(session.revision(), 0);
    assert_eq!(session.model_version(), Some(1));
    let conversion = session.conversion().expect("a canonical open converts");
    let bindings: Vec<_> = conversion
        .dropped
        .iter()
        .filter(|path| path.starts_with("bindings.inputs"))
        .collect();
    assert_eq!(
        bindings,
        [
            "bindings.inputs.fixed_cost",
            "bindings.inputs.unit_cost",
            "bindings.inputs.unit_price"
        ]
    );
    assert!(
        !conversion
            .added
            .iter()
            .any(|path| path.starts_with("bindings.inputs")),
        "{:?}",
        conversion.added
    );
    let snapshot = session.snapshot();
    assert!(snapshot.inputs.is_empty());
    assert_eq!(snapshot.outputs.len(), 6);
    // With no input bound, the name is unknown to the session.
    let price = BTreeMap::from([("unit_price".to_string(), CellValue::Number(25.0))]);
    assert_eq!(
        session.set_inputs(&price, 0).unwrap_err().code,
        "invalid_input"
    );
    let report = serde_json::to_value(session.calculate_with(1, &context(&fixture()))).unwrap();
    assert_eq!(report["inputs"], json!([]));
    assert_eq!(report["revision"], 0);
}

/// `Data!A1` is the bound input `n` (starting at 1); `B1 = A1 + 1`, and `C1`
/// and `D1` are each `SEQUENCE(A1)`. Two arrays of 60000 cells are 120000
/// materialized result cells, over the engine's documented budget of 100000
/// (`resource_limit`), so a pass at n = 60000 stops part-way.
fn budget_workbook() -> WorkbookModel {
    let mut sheet = Sheet::new("Data");
    sheet
        .cells
        .insert("A1".into(), Cell::value(CellValue::Number(1.0)));
    for (address, formula) in [
        ("B1", "=A1+1"),
        ("C1", "=SEQUENCE(A1)"),
        ("D1", "=SEQUENCE(A1)"),
    ] {
        sheet.cells.insert(address.into(), Cell::formula(formula));
    }
    WorkbookModel {
        sheets: vec![sheet],
        inputs: BTreeMap::from([(
            "n".to_string(),
            InputBinding {
                sheet: "Data".into(),
                address: "A1".into(),
                kind: "number".into(),
                required: true,
                min: None,
                max: None,
                choices: None,
            },
        )]),
        outputs: BTreeMap::from([
            ("next".to_string(), CellAddress::new("Data", "B1")),
            ("first".to_string(), CellAddress::new("Data", "C1")),
            ("second".to_string(), CellAddress::new("Data", "D1")),
        ]),
        ..WorkbookModel::default()
    }
}

fn n(value: f64) -> BTreeMap<String, CellValue> {
    BTreeMap::from([("n".to_string(), CellValue::Number(value))])
}

const OVER_BUDGET: f64 = 60_000.0;

fn has_resource_limit(report: &Json) -> bool {
    report["diagnostics"]
        .as_array()
        .unwrap()
        .iter()
        .any(|diagnostic| diagnostic["code"] == "resource_limit")
}

#[test]
fn failed_calculation_publishes_no_partial_result() {
    let context = context(&fixture());
    let calculate = |session: &CalculationSession| {
        serde_json::to_value(session.calculate_with(1, &context)).unwrap()
    };
    let every_formula = json!(["Data!B1", "Data!C1", "Data!D1"]);

    // A completed pass at revision 0 is published.
    let session = CalculationSession::new(budget_workbook(), None).unwrap();
    let first = calculate(&session);
    assert_eq!(first["diagnostics"], json!([]));
    assert_eq!(first["base_revision"], Json::Null);
    assert_eq!(first["evaluated_cells"], every_formula);
    assert_eq!(first["values"]["Data!B1"], 2.0);

    // The pass at revision 1 ends early. It is returned, not published.
    assert_eq!(session.set_inputs(&n(OVER_BUDGET), 0).unwrap(), 1);
    let failed = calculate(&session);
    assert!(has_resource_limit(&failed), "{}", failed["diagnostics"]);
    assert_eq!(failed["revision"], 1);
    assert_eq!(failed["stale"], false);
    assert_eq!(failed["base_revision"], 0);

    // Simply recalculating starts from the same place: the base is still the
    // completed pass at revision 0, never the failed pass at revision 1.
    let again = calculate(&session);
    assert!(has_resource_limit(&again), "{}", again["diagnostics"]);
    assert_eq!(again["revision"], 1);
    assert_eq!(again["base_revision"], 0);

    // With the cause fixed, the next pass reuses nothing from the failed
    // ones: its base is revision 0, and every cell downstream of the input
    // changed since then runs again. B1 = 2 + 1; SEQUENCE(2) is 1, 2 down
    // one column.
    assert_eq!(session.set_inputs(&n(2.0), 1).unwrap(), 2);
    let fixed = calculate(&session);
    assert_eq!(fixed["diagnostics"], json!([]));
    assert_eq!(fixed["revision"], 2);
    assert_eq!(fixed["base_revision"], 0);
    assert_eq!(fixed["evaluated_cells"], every_formula);
    assert_eq!(fixed["values"]["Data!B1"], 3.0);
    for key in ["Data!C1", "Data!D1"] {
        assert_eq!(
            fixed["values"][key],
            json!({"rows": [[1.0], [2.0]]}),
            "{key}"
        );
    }
    assert_eq!(fixed["calculated_formulas"], 3);
    assert_eq!(fixed["unresolved_formulas"], 0);
    // That pass completed, so it is the base of the one after it.
    let settled = calculate(&session);
    assert_eq!(settled["base_revision"], 2);
    assert_eq!(settled["evaluated_cells"], json!([]));

    // With no completed pass before it, a pass that ends early leaves no
    // base at all: the next completed pass is a full one.
    let mut model = budget_workbook();
    model.sheets[0]
        .cells
        .insert("A1".into(), Cell::value(CellValue::Number(OVER_BUDGET)));
    let session = CalculationSession::new(model, None).unwrap();
    let failed = calculate(&session);
    assert!(has_resource_limit(&failed), "{}", failed["diagnostics"]);
    assert_eq!(failed["base_revision"], Json::Null);
    assert!(has_resource_limit(&calculate(&session)));
    assert_eq!(session.set_inputs(&n(2.0), 0).unwrap(), 1);
    let fixed = calculate(&session);
    assert_eq!(fixed["diagnostics"], json!([]));
    assert_eq!(fixed["base_revision"], Json::Null);
    assert_eq!(fixed["evaluated_cells"], every_formula);
    assert_eq!(fixed["values"]["Data!B1"], 3.0);
}

#[test]
fn a_refused_batch_and_a_preview_leave_the_published_pass_in_place() {
    // The other half of "a failure publishes nothing": a refused commit and
    // a preview change neither the revision nor the published pass, so the
    // next live pass reuses it whole.
    let fixture = fixture();
    let context = context(&fixture);
    let session = planner(&fixture);
    let first = session.calculate_with(1, &context);
    assert_eq!((first.revision, first.base_revision), (0, None));
    assert_eq!(first.evaluated_cells.len(), 12);

    let price = BTreeMap::from([("unit_price".to_string(), CellValue::Number(30.0))]);
    assert_eq!(
        session.set_inputs(&price, 1).unwrap_err().code,
        "revision_conflict"
    );
    let text = BTreeMap::from([("unit_price".to_string(), CellValue::Text("thirty".into()))]);
    assert_eq!(
        session.set_inputs(&text, 0).unwrap_err().code,
        "invalid_input"
    );
    let preview = session.preview_inputs(&price, 0, &context).unwrap();
    assert_eq!(preview.proposed_revision, 1);

    let next = session.calculate_with(1, &context);
    assert_eq!((next.revision, next.base_revision), (0, Some(0)));
    assert!(next.evaluated_cells.is_empty());
    assert_eq!(next.values, first.values);
}

#[test]
fn the_comparison_refuses_a_report_that_differs() {
    // The replay is only as strict as its comparison, so show the comparison
    // failing: one real report against expectations each changed in one place.
    let fixture = fixture();
    let scenario = scenario(&fixture, "input-changes");
    let expected = &step(scenario, "s01")["expect"]["planner"];
    let session = planner(&fixture);
    let report = serde_json::to_value(session.calculate_with(1, &context(&fixture))).unwrap();
    let comparison = Comparison {
        tolerance: fixture["numeric_tolerance"].as_f64().unwrap(),
    };
    comparison.report("s01", &report, expected);
    let differs = |change: &dyn Fn(&mut Json)| {
        let mut expected = expected.clone();
        change(&mut expected);
        std::panic::catch_unwind(|| comparison.report("s01", &report, &expected)).is_err()
    };
    assert!(differs(&|e| e["values"]["Forecast!F2"] = json!(7400.001)));
    assert!(differs(&|e| e["revision"] = json!(1)));
    assert!(differs(&|e| e["base_revision"] = json!(0)));
    assert!(differs(&|e| e["inputs"][0]["origin"] = json!("authored")));
    assert!(differs(
        &|e| e["outputs"][0]["result"] = json!({"error": "#N/A"})
    ));
    assert!(differs(&|e| {
        e["evaluated_cells"].as_array_mut().unwrap().pop();
    }));
    assert!(differs(
        &|e| e["diagnostics"] = json!([{"code": "parse_error"}])
    ));
    assert!(differs(&|e| {
        e.as_object_mut().unwrap().remove("stale");
    }));
    assert!(differs(&|e| e["calc"]["seed"] = json!("2")));
    // One millisecond off is a different pinned clock: integers are exact.
    assert!(differs(
        &|e| e["calc"]["now_ms"] = json!(1_767_225_600_001_i64)
    ));
    assert!(differs(&|e| e["source_fingerprint"] = json!(
        "sha256:6c54429516c1a74e864c1d44b17115b228046e402b1db0fddbfb4a2364137d79"
    )));
}

#[test]
fn the_legacy_document_fingerprint_is_the_pinned_digest_of_its_text() {
    // The digest in the fixture was taken with `shasum -a 256` over the
    // document_text bytes; `open` must report that and nothing derived from
    // a re-serialised document.
    let fixture = fixture();
    let document = &fixture["documents"]["legacy_revision"];
    let pinned = document["open"]["source_fingerprint"].as_str().unwrap();
    assert_eq!(
        pinned,
        "sha256:02c7ed20265b603ea530aeb2356533d02b31eb3d3dc035cb6bdae9638f891c01"
    );
    let session = open(&fixture, "legacy_revision");
    assert_eq!(session.source_fingerprint(), Some(pinned));
    // Other bytes for the same document have another fingerprint.
    let pretty = serde_json::to_vec_pretty(&document["document"]).unwrap();
    let other = CalculationSession::open(&pretty, &BTreeMap::new(), None).unwrap();
    assert_ne!(other.source_fingerprint(), Some(pinned));
    let report = serde_json::to_value(session.calculate_with(1, &context(&fixture))).unwrap();
    assert_eq!(report["source_fingerprint"], pinned);
}

fn refusal_code(document: &[u8], kinds: &BTreeMap<String, String>) -> String {
    CalculationSession::open(document, kinds, None)
        .err()
        .unwrap_or_else(|| panic!("opened {:?}", String::from_utf8_lossy(document)))
        .code
}

#[test]
fn open_refusals_report_the_listed_code() {
    let fixture = fixture();
    let refusals = &fixture["open_refusals"];
    let cases = refusals["cases"].as_array().unwrap();
    assert!(!cases.is_empty());
    let mut differ = Vec::new();
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let document = case["document"].as_str().unwrap().as_bytes();
        assert_eq!(
            refusal_code(document, &BTreeMap::new()),
            case["rust"].as_str().unwrap(),
            "{name}"
        );
        if case["rust"] != case["python"] {
            differ.push(json!(name));
        }
    }
    // The two engines' readers disagree on a code only where the contract
    // lists it.
    assert_eq!(Json::Array(differ), refusals["reader_differences"]);
    // A document that is not a JSON object is refused before input_kinds is
    // looked at; a JSON object that is not canonical refuses input_kinds.
    let kinds = BTreeMap::from([("unit_price".to_string(), "number".to_string())]);
    for name in ["not-json", "byte-order-mark", "nan-constant", "array-root"] {
        let case = cases.iter().find(|case| case["name"] == name).unwrap();
        let document = case["document"].as_str().unwrap().as_bytes();
        assert_eq!(refusal_code(document, &kinds), "invalid_model", "{name}");
    }
    assert_eq!(refusal_code(b"{}", &kinds), "invalid_request");
    assert_eq!(refusal_code(b"\xff\xfe", &BTreeMap::new()), "invalid_model");
}

/// One sheet `S`. Text starting with `=` is a formula; anything else here is
/// a number.
fn sheet_model(
    cells: &[(&str, &str)],
    input: Option<(&str, &str)>,
    outputs: &[(&str, &str)],
) -> WorkbookModel {
    let mut sheet = Sheet::new("S");
    for (address, content) in cells {
        let cell = match content.strip_prefix('=') {
            Some(_) => Cell::formula(*content),
            None => Cell::value(CellValue::Number(content.parse().expect("a number"))),
        };
        sheet.cells.insert((*address).into(), cell);
    }
    WorkbookModel {
        sheets: vec![sheet],
        inputs: input
            .into_iter()
            .map(|(name, address)| {
                (
                    name.to_string(),
                    InputBinding {
                        sheet: "S".into(),
                        address: address.into(),
                        kind: "number".into(),
                        required: true,
                        min: None,
                        max: None,
                        choices: None,
                    },
                )
            })
            .collect(),
        outputs: outputs
            .iter()
            .map(|(name, address)| (name.to_string(), CellAddress::new("S", *address)))
            .collect(),
        ..WorkbookModel::default()
    }
}

fn located_codes(report: &Json) -> Vec<(String, Json, Json)> {
    report["diagnostics"]
        .as_array()
        .unwrap()
        .iter()
        .map(|item| {
            (
                item["code"].as_str().unwrap().to_string(),
                item.get("sheet").cloned().unwrap_or(Json::Null),
                item.get("address").cloned().unwrap_or(Json::Null),
            )
        })
        .collect()
}

#[test]
fn graph_refused_pass_counts_every_formula_unresolved_and_publishes_nothing() {
    // C1 names two ranges of 60000 cells. The engine expands at most 100000
    // referenced cells per workbook (resource_limit) and 120000 is over, so
    // the graph is refused before any cell runs. Such a pass has no closure:
    // it reports no value and counts every formula cell of the document (A3,
    // B1, B2, C1: four) as unresolved.
    let model = sheet_model(
        &[
            ("A1", "1"),
            ("A2", "2"),
            ("A3", "=A1+A2"),
            ("B1", "=A1*10"),
            ("B2", "=1+"),
            ("C1", "=SUM(A1:A60000)+SUM(D1:D60000)"),
        ],
        None,
        &[],
    );
    let session = CalculationSession::new(model, None).unwrap();
    let context = context(&fixture());
    for _ in 0..2 {
        // The second pass has the same null base: the first published nothing.
        let report = serde_json::to_value(session.calculate_with(1, &context)).unwrap();
        assert_eq!(
            located_codes(&report),
            [("resource_limit".to_string(), Json::Null, Json::Null)]
        );
        assert_eq!(report["values"], json!({}));
        assert_eq!(report["evaluated_cells"], json!([]));
        assert_eq!(report["calculated_formulas"], 0);
        assert_eq!(report["unresolved_formulas"], 4);
        assert_eq!(report["revision"], 0);
        assert_eq!(report["base_revision"], Json::Null);
        assert_eq!(report["stale"], false);
    }
}

#[test]
fn early_ended_pass_lists_its_evaluated_cells_sorted() {
    // Depths: A1 and M1 are literals; Y1, Z1 and M2 read them; B2 reads Z1
    // and M3 reads M2; M4 reads M3 (3 * 33000 = 99000 rows). A pass covers
    // the closure depth by depth. The first pass materialises A1, M1, Z1,
    // M2, B2 and M3 (1 cell each), Y1 (10 rows) and M4 (99000 rows): 99016
    // result cells, inside the budget of 100000.
    let model = sheet_model(
        &[
            ("A1", "10"),
            ("Y1", "=SEQUENCE(A1)"),
            ("Z1", "=A1+1"),
            ("B2", "=Z1+1"),
            ("M1", "1"),
            ("M2", "=M1+1"),
            ("M3", "=M2+1"),
            ("M4", "=SEQUENCE(M3*33000)"),
        ],
        Some(("x", "A1")),
        &[],
    );
    let session = CalculationSession::new(model, None).unwrap();
    let context = context(&fixture());
    let first = serde_json::to_value(session.calculate_with(1, &context)).unwrap();
    assert_eq!(first["diagnostics"], json!([]));
    assert_eq!(
        first["evaluated_cells"],
        json!(["S!B2", "S!M2", "S!M3", "S!M4", "S!Y1", "S!Z1"])
    );
    // With x = 1000 the second pass runs the cells downstream of the input,
    // Y1 (1000 rows) and Z1 and then B2, and reuses the M column from its
    // base. Reaching M4 it holds 1 + 1 + 1000 + 1 + 1 + 1 + 1 = 1006 cells,
    // and 99000 more is over the budget, so it ends early. Y1 and Z1 ran
    // before B2; the list is sorted all the same.
    let x = BTreeMap::from([("x".to_string(), CellValue::Number(1000.0))]);
    assert_eq!(session.set_inputs(&x, 0).unwrap(), 1);
    for _ in 0..2 {
        let ended = serde_json::to_value(session.calculate_with(1, &context)).unwrap();
        assert_eq!(
            located_codes(&ended),
            [("resource_limit".to_string(), json!("S"), json!("M4"))]
        );
        assert_eq!(ended["evaluated_cells"], json!(["S!B2", "S!Y1", "S!Z1"]));
        assert_eq!(ended["revision"], 1);
        assert_eq!(ended["base_revision"], 0);
        // Z1 = 1000 + 1 and B2 = Z1 + 1.
        assert_eq!(ended["values"]["S!Z1"], 1001.0);
        assert_eq!(ended["values"]["S!B2"], 1002.0);
        // Of the six formula cells only M4 has no value in this pass.
        assert!(ended["values"].get("S!M4").is_none());
        assert_eq!(ended["calculated_formulas"], 5);
        assert_eq!(ended["unresolved_formulas"], 1);
    }
}

#[test]
fn bound_outputs_stop_at_the_output_result_budget() {
    // A1 is one array of 60000 cells; B1 is a scalar. Both fit the pass's
    // budget of 100000 result cells (60001). Bound outputs are copied in
    // name order under a second budget of the same size: `first` takes
    // 60000, `second` would make 120000 and is refused with resource_limit,
    // and the copy stops there, so `third` is not copied either. The legacy
    // report leaves both out of its outputs; calculation-report.v2 follows
    // the same rule and must not materialise the array once per binding.
    let model = sheet_model(
        &[("A1", "=SEQUENCE(60000)"), ("B1", "=1+1")],
        None,
        &[("first", "A1"), ("second", "A1"), ("third", "B1")],
    );
    let session = CalculationSession::new(model, None).unwrap();
    let context = context(&fixture());
    let typed = session.calculate_with(1, &context);
    let rows = |index: usize| match &typed.outputs[index].result {
        Some(toolkit::CalculatedValue::Array { rows }) => Some(rows.len()),
        _ => None,
    };
    // SEQUENCE(n) is 1 to n down one column.
    assert_eq!(rows(0), Some(60_000));
    let report = serde_json::to_value(&typed).unwrap();
    let outputs = report["outputs"].as_array().unwrap();
    let names: Vec<_> = outputs.iter().map(|row| row["name"].clone()).collect();
    assert_eq!(names, ["first", "second", "third"]);
    assert_eq!(outputs[0]["origin"], "calculated");
    assert_eq!(outputs[0]["result"]["rows"][0], json!([1.0]));
    assert_eq!(outputs[0]["result"]["rows"][59_999], json!([60000.0]));
    for index in [1, 2] {
        assert_eq!(outputs[index]["origin"], "not_calculated", "{index}");
        assert_eq!(outputs[index]["result"], Json::Null, "{index}");
    }
    assert_eq!(
        located_codes(&report),
        [("resource_limit".to_string(), json!("S"), json!("A1"))]
    );
    // The cells themselves were calculated: `values` holds them once each.
    assert_eq!(
        report["values"]["S!A1"]["rows"].as_array().unwrap().len(),
        60_000
    );
    assert_eq!(report["values"]["S!B1"], 2.0);
    assert_eq!(report["calculated_formulas"], 2);
    assert_eq!(report["unresolved_formulas"], 0);
    // The pass covered its closure, so it is a completed pass and the next
    // one builds on it.
    let again = session.calculate_with(1, &context);
    assert_eq!(again.base_revision, Some(0));
    assert!(again.evaluated_cells.is_empty());
    let origins: Vec<_> = again
        .outputs
        .iter()
        .map(|row| row.origin.as_deref())
        .collect();
    assert_eq!(
        origins,
        [
            Some("calculated"),
            Some("not_calculated"),
            Some("not_calculated")
        ]
    );
    // One array fits: with a single binding nothing is refused.
    let single = sheet_model(&[("A1", "=SEQUENCE(60000)")], None, &[("only", "A1")]);
    let single = CalculationSession::new(single, None)
        .unwrap()
        .calculate_with(1, &context);
    assert!(single.diagnostics.is_empty());
    assert_eq!(single.outputs[0].origin.as_deref(), Some("calculated"));
}

#[test]
fn a_value_that_is_not_a_cell_value_is_invalid_input_whatever_the_revision() {
    // The typed map leaves one value that is not a cell value: a number that
    // is not finite. It is refused before the revision is compared, as the
    // Python layer refuses a malformed value.
    let fixture = fixture();
    let session = planner(&fixture);
    let before = session.snapshot();
    for number in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        let values = BTreeMap::from([("unit_price".to_string(), CellValue::Number(number))]);
        for revision in [0, 5] {
            assert_eq!(
                session.set_inputs(&values, revision).unwrap_err().code,
                "invalid_input",
                "{number} at {revision}"
            );
            assert_eq!(
                session
                    .preview_inputs(&values, revision, &context(&fixture))
                    .unwrap_err()
                    .code,
                "invalid_input",
                "{number} at {revision}"
            );
        }
    }
    // A cell value the binding refuses is the binding's code, after the
    // revision check.
    let text = BTreeMap::from([("unit_price".to_string(), CellValue::Text("thirty".into()))]);
    assert_eq!(
        session.set_inputs(&text, 0).unwrap_err().code,
        "invalid_input"
    );
    assert_eq!(
        session.set_inputs(&text, 5).unwrap_err().code,
        "revision_conflict"
    );
    assert_eq!(session.revision(), 0);
    assert_eq!(session.snapshot(), before);
}
