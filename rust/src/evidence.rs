//! Evidence grades for catalog functions, implemented independently of Python.
//!
//! Four grades exist and only the last is evidence about Excel:
//! `profile` (Workbook Forge declares the rule), `dual_engine` (Python and Rust
//! agree on shared fixtures), `expected_value` (matches a Microsoft example or
//! independent arithmetic) and `excel_observed` (Excel Desktop produced it and a
//! receipt pins the observed workbook's sha256, the Excel version and the time
//! Excel ran).
//!
//! This module validates the catalog records. Hashing receipts to detect a
//! stale observation is done by the Python harness that writes them
//! (`tools/excel_oracle.py`, `workbook_forge.evidence`), so the Rust crate does
//! not take a hashing dependency for it.

use serde_json::Value as JsonValue;
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum EvidenceGrade {
    Profile,
    DualEngine,
    ExpectedValue,
    ExcelObserved,
}

impl EvidenceGrade {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "profile" => Some(Self::Profile),
            "dual_engine" => Some(Self::DualEngine),
            "expected_value" => Some(Self::ExpectedValue),
            "excel_observed" => Some(Self::ExcelObserved),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Profile => "profile",
            Self::DualEngine => "dual_engine",
            Self::ExpectedValue => "expected_value",
            Self::ExcelObserved => "excel_observed",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProfileClass {
    Cosmetic,
    Semantic,
    Unclassified,
}

impl ProfileClass {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "cosmetic" => Some(Self::Cosmetic),
            "semantic" => Some(Self::Semantic),
            "unclassified" => Some(Self::Unclassified),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExcelObservation {
    pub receipt: String,
    pub receipt_sha256: String,
    pub workbook: String,
    pub workbook_sha256: String,
    pub excel_version: String,
    pub event_time: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FunctionEvidence {
    pub name: String,
    pub grade: EvidenceGrade,
    pub profile_class: ProfileClass,
    pub excel_observation: Option<ExcelObservation>,
}

impl FunctionEvidence {
    /// Only an `excel_observed` grade with a receipt can be Excel-compatible;
    /// whether that receipt is still current is checked where it is hashed.
    pub fn may_claim_excel_compatibility(&self) -> bool {
        self.grade == EvidenceGrade::ExcelObserved && self.excel_observation.is_some()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EvidenceError {
    pub code: &'static str,
    pub message: String,
}

impl fmt::Display for EvidenceError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for EvidenceError {}

fn error(code: &'static str, message: String) -> EvidenceError {
    EvidenceError { code, message }
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn is_relative_path(value: &str) -> bool {
    !value.is_empty()
        && !value.starts_with('/')
        && !value.starts_with('~')
        && !value.contains('\\')
        && !value.split('/').any(|part| part == "..")
}

fn has_zoned_time(value: &str) -> bool {
    // ISO 8601 date-time with an explicit zone: `...T...Z` or `...T...+hh:mm`.
    let Some((date, time)) = value.split_once('T') else {
        return false;
    };
    date.len() == 10
        && (time.ends_with('Z') || time.rfind(['+', '-']).is_some_and(|index| index >= 5))
}

const OBSERVATION_FIELDS: [&str; 6] = [
    "receipt",
    "receipt_sha256",
    "workbook",
    "workbook_sha256",
    "excel_version",
    "event_time",
];

fn observation(record: &JsonValue, name: &str) -> Result<ExcelObservation, EvidenceError> {
    let object = record.as_object().ok_or_else(|| {
        error(
            "invalid_observation",
            format!("function {name} excel_observation must be an object"),
        )
    })?;
    if let Some(unknown) = object
        .keys()
        .find(|key| !OBSERVATION_FIELDS.contains(&key.as_str()))
    {
        return Err(error(
            "invalid_observation",
            format!("function {name} excel_observation has unknown field {unknown}"),
        ));
    }
    let field = |key: &str| -> Result<String, EvidenceError> {
        object
            .get(key)
            .and_then(JsonValue::as_str)
            .map(str::to_owned)
            .ok_or_else(|| {
                error(
                    "invalid_observation",
                    format!("function {name} excel_observation is missing {key}"),
                )
            })
    };
    let parsed = ExcelObservation {
        receipt: field("receipt")?,
        receipt_sha256: field("receipt_sha256")?,
        workbook: field("workbook")?,
        workbook_sha256: field("workbook_sha256")?,
        excel_version: field("excel_version")?,
        event_time: field("event_time")?,
    };
    if !is_sha256(&parsed.receipt_sha256) || !is_sha256(&parsed.workbook_sha256) {
        return Err(error(
            "invalid_observation",
            format!("function {name} excel_observation hashes must be lowercase sha256"),
        ));
    }
    if !is_relative_path(&parsed.receipt) || !is_relative_path(&parsed.workbook) {
        return Err(error(
            "invalid_observation",
            format!("function {name} excel_observation paths must be repository-relative"),
        ));
    }
    if parsed.excel_version.trim().is_empty() || !has_zoned_time(&parsed.event_time) {
        return Err(error(
            "invalid_observation",
            format!(
                "function {name} excel_observation needs an Excel version and a zoned event_time"
            ),
        ));
    }
    Ok(parsed)
}

/// Validate one `catalog/formulas.json` function entry's evidence fields.
pub fn validate_function_evidence(entry: &JsonValue) -> Result<FunctionEvidence, EvidenceError> {
    let name = entry["name"].as_str().unwrap_or("?").to_ascii_uppercase();
    let grade = entry["evidence_grade"]
        .as_str()
        .and_then(EvidenceGrade::parse)
        .ok_or_else(|| {
            error(
                "invalid_evidence_grade",
                format!("function {name} has no valid evidence_grade"),
            )
        })?;
    let profile_class = match entry.get("profile_class") {
        None => ProfileClass::Unclassified,
        Some(value) => value
            .as_str()
            .and_then(ProfileClass::parse)
            .ok_or_else(|| {
                error(
                    "invalid_profile_class",
                    format!("function {name} has an invalid profile_class"),
                )
            })?,
    };
    let excel_observation = match (grade, entry.get("excel_observation")) {
        (EvidenceGrade::ExcelObserved, Some(record)) => Some(observation(record, &name)?),
        (EvidenceGrade::ExcelObserved, None) => {
            return Err(error(
                "missing_receipt",
                format!("function {name} is excel_observed without a receipt"),
            ));
        }
        (_, Some(_)) => {
            return Err(error(
                "grade_mismatch",
                format!(
                    "function {name} carries an Excel observation but is graded {}",
                    grade.as_str()
                ),
            ));
        }
        (_, None) => None,
    };
    let evidence = FunctionEvidence {
        name: name.clone(),
        grade,
        profile_class,
        excel_observation,
    };
    match entry.get("excel_compatible") {
        None | Some(JsonValue::Bool(false)) => {}
        Some(JsonValue::Bool(true)) if evidence.may_claim_excel_compatibility() => {}
        Some(JsonValue::Bool(true)) => {
            return Err(error(
                "excel_compatibility_without_observation",
                format!(
                    "function {name} is graded {}; only excel_observed with a receipt can be Excel-compatible",
                    grade.as_str()
                ),
            ));
        }
        Some(_) => {
            return Err(error(
                "invalid_excel_compatible",
                format!("function {name} excel_compatible must be boolean"),
            ));
        }
    }
    Ok(evidence)
}

/// Validate every function entry in a parsed `formulas.json` document.
pub fn validate_catalog_evidence(
    catalog: &JsonValue,
) -> Result<Vec<FunctionEvidence>, EvidenceError> {
    catalog["functions"]
        .as_array()
        .ok_or_else(|| error("invalid_catalog", "formulas.json must list functions".into()))?
        .iter()
        .map(validate_function_evidence)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn catalog() -> JsonValue {
        serde_json::from_str(
            &std::fs::read_to_string(concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/../catalog/formulas.json"
            ))
            .unwrap(),
        )
        .unwrap()
    }

    fn observation_record() -> JsonValue {
        json!({
            "receipt": "receipts/sum-excel/receipt.json",
            "receipt_sha256": "a".repeat(64),
            "workbook": "receipts/sum-excel/roundtrip.xlsx",
            "workbook_sha256": "b".repeat(64),
            "excel_version": "16.113.4",
            "event_time": "2026-10-14T18:00:03Z",
        })
    }

    #[test]
    fn checked_in_catalog_grades_every_function_and_claims_no_excel_observation() {
        let graded = validate_catalog_evidence(&catalog()).unwrap();
        assert!(!graded.is_empty());
        assert!(
            graded
                .iter()
                .all(|item| item.grade != EvidenceGrade::ExcelObserved)
        );
        assert!(
            graded
                .iter()
                .all(|item| item.profile_class == ProfileClass::Unclassified)
        );
        assert!(
            graded
                .iter()
                .all(|item| !item.may_claim_excel_compatibility())
        );
    }

    #[test]
    fn dual_engine_only_cannot_be_marked_excel_compatible() {
        for grade in ["profile", "dual_engine", "expected_value"] {
            let entry = json!({"name": "SUM", "evidence_grade": grade, "excel_compatible": true});
            assert_eq!(
                validate_function_evidence(&entry).unwrap_err().code,
                "excel_compatibility_without_observation"
            );
        }
        let entry = json!({
            "name": "SUM", "evidence_grade": "dual_engine",
            "excel_observation": observation_record(),
        });
        assert_eq!(
            validate_function_evidence(&entry).unwrap_err().code,
            "grade_mismatch"
        );
    }

    #[test]
    fn excel_observed_requires_a_well_formed_receipt() {
        let missing = json!({"name": "SUM", "evidence_grade": "excel_observed"});
        assert_eq!(
            validate_function_evidence(&missing).unwrap_err().code,
            "missing_receipt"
        );
        for (field, bad) in [
            ("receipt_sha256", "short"),
            ("workbook", "/Users/someone/roundtrip.xlsx"),
            ("receipt", "../receipt.json"),
            ("event_time", "2026-10-14 18:00"),
            ("excel_version", " "),
        ] {
            let mut record = observation_record();
            record[field] = json!(bad);
            let entry = json!({
                "name": "SUM", "evidence_grade": "excel_observed", "excel_observation": record,
            });
            assert_eq!(
                validate_function_evidence(&entry).unwrap_err().code,
                "invalid_observation",
                "{field}"
            );
        }
        let entry = json!({
            "name": "SUM", "evidence_grade": "excel_observed", "profile_class": "semantic",
            "excel_observation": observation_record(), "excel_compatible": true,
        });
        let evidence = validate_function_evidence(&entry).unwrap();
        assert!(evidence.may_claim_excel_compatibility());
        assert_eq!(evidence.profile_class, ProfileClass::Semantic);
    }

    #[test]
    fn profile_class_defaults_to_unclassified() {
        let entry = json!({"name": "SUM", "evidence_grade": "dual_engine"});
        assert_eq!(
            validate_function_evidence(&entry).unwrap().profile_class,
            ProfileClass::Unclassified
        );
        let entry =
            json!({"name": "SUM", "evidence_grade": "dual_engine", "profile_class": "maybe"});
        assert_eq!(
            validate_function_evidence(&entry).unwrap_err().code,
            "invalid_profile_class"
        );
    }
}
