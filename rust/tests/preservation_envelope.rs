//! Spec 10 Design 1: the in-memory `PreservationEnvelope` that `import_xlsx`
//! assembles from what the importer already holds. Python builds the same
//! envelope in `python/tests/test_preservation_envelope.py`; the
//! `envelope_describe` example feeds the cross-language agreement test.

use serde_json::Value as Json;
use std::collections::BTreeMap;
use std::path::PathBuf;
use workbook_forge::envelope::PreservationEnvelope;
use workbook_forge::model::content_fingerprint;
use workbook_forge::xlsx::{EXCEL_MAX_COLUMNS, EXCEL_MAX_ROWS, import_xlsx, import_xlsx_bytes};

fn corpus(name: &str) -> Vec<u8> {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../fixtures/corpus")
        .join(name);
    std::fs::read(path).expect("corpus file")
}

/// A private folder under the system temp dir whose name must never leak
/// into the envelope's projection. Removed on drop.
struct PrivateFolder(PathBuf);
impl PrivateFolder {
    fn new(tag: &str) -> Self {
        let folder = std::env::temp_dir()
            .join(format!("wf-envelope-{}-{tag}", std::process::id()))
            .join("private-dir-xyz");
        std::fs::create_dir_all(&folder).expect("temp folder");
        Self(folder)
    }
    fn place(&self, name: &str, bytes: &[u8]) -> PathBuf {
        let path = self.0.join(name);
        std::fs::write(&path, bytes).expect("write copy");
        path
    }
}
impl Drop for PrivateFolder {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(self.0.parent().unwrap());
    }
}

fn import_from_bytes(bytes: &[u8]) -> PreservationEnvelope {
    import_xlsx_bytes(bytes, BTreeMap::new(), BTreeMap::new())
        .expect("import")
        .envelope()
}

#[test]
fn envelope_is_assembled_from_import_and_holds_no_path() {
    let bytes = corpus("op_merged_cells.xlsx");
    let folder = PrivateFolder::new("assembled");
    let path = folder.place("merged.xlsx", &bytes);
    let imported = import_xlsx(&path, BTreeMap::new(), BTreeMap::new()).expect("import");
    let envelope = imported.envelope();

    let describe = envelope.describe();
    let text = serde_json::to_string(&describe).unwrap();
    assert!(
        !text.contains("private-dir-xyz"),
        "describe() leaks the folder"
    );
    assert!(
        envelope.source.handle.is_some(),
        "a path import keeps its handle"
    );

    assert_eq!(envelope.source.fingerprint, content_fingerprint(&bytes));
    assert_eq!(
        envelope.source.parts,
        envelope.baseline.parts.keys().cloned().collect::<Vec<_>>()
    );
    assert!(envelope.source.parts.windows(2).all(|w| w[0] < w[1]));
    assert_eq!(envelope.baseline.document, imported.snapshot());
    assert!(envelope.baseline.container.is_none());
    assert_eq!(envelope.source.workbook_part, "xl/workbook.xml");
    assert_eq!(
        envelope.source.sheet_entries,
        envelope
            .baseline
            .document
            .sheets
            .iter()
            .map(|s| s.name.clone())
            .collect::<Vec<_>>()
    );
    for name in &envelope.source.sheet_entries {
        assert!(
            envelope.source.sheet_parts.contains_key(name),
            "{name} part"
        );
    }
    assert!(envelope.source.chartsheets.is_empty());
    assert!(
        envelope.merged.values().any(|ranges| !ranges.is_empty()),
        "the fixture merges at least one range"
    );
    assert_eq!(envelope.date_system, "1900");
    assert_eq!(envelope.compatibility_version, 1);
    assert_eq!(
        envelope.insertion_bound,
        (EXCEL_MAX_ROWS, EXCEL_MAX_COLUMNS)
    );

    assert_eq!(
        describe["source"]["fingerprint"],
        Json::from(envelope.source.fingerprint.clone())
    );
    assert_eq!(
        describe["baseline"]["parts"]["xl/workbook.xml"],
        Json::from(content_fingerprint(
            &envelope.baseline.parts["xl/workbook.xml"]
        ))
    );
    assert_eq!(describe["baseline"]["container"], Json::Null);
    assert_eq!(
        describe["baseline"]["document"],
        serde_json::to_value(&envelope.baseline.document).unwrap()
    );
    assert_eq!(
        describe["insertion_bound"],
        serde_json::json!([EXCEL_MAX_ROWS, EXCEL_MAX_COLUMNS])
    );
    let merged = describe["merged"].as_object().unwrap();
    assert!(merged.values().any(|ranges| {
        ranges.as_array().is_some_and(|r| {
            r.iter()
                .any(|range| range.as_array().is_some_and(|q| q.len() == 4))
        })
    }));
}

#[test]
fn bytes_import_has_no_handle_and_the_same_fingerprint() {
    let bytes = corpus("op_merged_cells.xlsx");
    let from_bytes = import_from_bytes(&bytes);
    assert!(from_bytes.source.handle.is_none());
    assert_eq!(from_bytes.source.fingerprint, content_fingerprint(&bytes));

    let folder = PrivateFolder::new("bytes");
    let path = folder.place("merged.xlsx", &bytes);
    let from_path = import_xlsx(&path, BTreeMap::new(), BTreeMap::new())
        .expect("import")
        .envelope();
    assert_eq!(from_path.source.fingerprint, from_bytes.source.fingerprint);
    assert_eq!(from_path.describe(), from_bytes.describe());
}

#[test]
fn compatibility_version_is_kept_as_an_integer_not_a_flag() {
    let envelope = import_from_bytes(&corpus("hb_compat_version_3.xlsx"));
    assert_eq!(envelope.compatibility_version, 3);
    assert_eq!(envelope.describe()["compatibility_version"], Json::from(3));
    let plain = import_from_bytes(&corpus("op_merged_cells.xlsx"));
    assert_eq!(plain.compatibility_version, 1);
}

#[test]
fn describe_is_sorted_and_serialisable() {
    let envelope = import_from_bytes(&corpus("op_merged_cells.xlsx"));
    let describe = envelope.describe();
    let text = serde_json::to_string(&describe).unwrap();
    let reparsed: Json = serde_json::from_str(&text).unwrap();
    assert_eq!(reparsed, describe);

    let keys: Vec<_> = describe.as_object().unwrap().keys().cloned().collect();
    assert_eq!(
        keys,
        [
            "baseline",
            "compatibility_version",
            "date_system",
            "guards",
            "insertion_bound",
            "merged",
            "source",
            "tables",
            "text",
        ]
    );
    let source: Vec<_> = describe["source"]
        .as_object()
        .unwrap()
        .keys()
        .cloned()
        .collect();
    assert_eq!(
        source,
        [
            "chartsheets",
            "fingerprint",
            "parts",
            "sheet_entries",
            "sheet_parts",
            "workbook_part",
        ]
    );
    let guards: Vec<_> = describe["guards"]
        .as_object()
        .unwrap()
        .keys()
        .cloned()
        .collect();
    assert_eq!(guards, ["charts", "data_validations", "pivots"]);
    assert!(describe["tables"].is_array());
    assert!(describe["text"]["layouts"].is_object());

    let cells = describe["text"]["cells"].as_array().unwrap();
    let pairs: Vec<(String, String)> = cells
        .iter()
        .map(|pair| {
            let pair = pair.as_array().unwrap();
            assert_eq!(pair.len(), 2);
            (
                pair[0].as_str().unwrap().to_string(),
                pair[1].as_str().unwrap().to_string(),
            )
        })
        .collect();
    assert!(
        pairs.windows(2).all(|w| w[0] < w[1]),
        "text.cells is sorted"
    );

    for (part, digest) in describe["baseline"]["parts"].as_object().unwrap() {
        let digest = digest.as_str().unwrap_or_else(|| panic!("{part} digest"));
        assert_eq!(digest.len(), 7 + 64, "{part}");
        assert!(digest.starts_with("sha256:"), "{part}");
        assert!(digest[7..].bytes().all(|b| b.is_ascii_hexdigit()), "{part}");
    }
    assert_eq!(
        describe["guards"]["data_validations"],
        envelope.guards()["data_validations"]
    );
    assert_eq!(describe["text"]["layouts"], envelope.layouts());
}
