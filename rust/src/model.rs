//! Native Rust types for the canonical serialized workbook model.
//!
//! `schemas/workbook-model-v1.schema.json` is the cross-language contract.
//! These types are hydrated directly from its bytes and are the model surface
//! for future calculation binding; this module is not a conversion DTO for the
//! legacy toolkit session model.

pub use crate::toolkit::CellValue;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value as JsonValue};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

pub const SCHEMA_VERSION: u32 = 1;
pub const MODEL_VERSION: u32 = 1;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum DataType {
    Blank,
    Number,
    Boolean,
    Text,
    Error,
    Unknown,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Formula {
    pub expression: String,
    pub dependencies: Vec<String>,
    /// Value imported from a workbook formula cache, not a fresh calculation.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cached_value: Option<CellValue>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Cell {
    pub address: String,
    pub value: CellValue,
    pub data_type: DataType,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub number_format: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub formula: Option<Formula>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Sheet {
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dimensions: Option<[u32; 4]>,
    pub cells: BTreeMap<String, Cell>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum BindingDirection {
    Input,
    Output,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BindingConstraints {
    #[serde(rename = "min", skip_serializing_if = "Option::is_none")]
    pub minimum: Option<f64>,
    #[serde(rename = "max", skip_serializing_if = "Option::is_none")]
    pub maximum: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub choices: Option<Vec<CellValue>>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Binding {
    pub direction: BindingDirection,
    pub name: String,
    pub sheet: String,
    pub address: String,
    pub value_type: DataType,
    pub required: bool,
    pub constraints: BindingConstraints,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Workbook {
    pub schema_version: u32,
    pub model_version: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source_path: Option<String>,
    pub metadata: BTreeMap<String, JsonValue>,
    pub bindings: Vec<Binding>,
    pub sheets: Vec<Sheet>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ModelError(String);
impl fmt::Display for ModelError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for ModelError {}
impl From<serde_json::Error> for ModelError {
    fn from(error: serde_json::Error) -> Self {
        Self(format!("invalid workbook model JSON: {error}"))
    }
}
type Result<T> = std::result::Result<T, ModelError>;

fn fail<T>(message: impl Into<String>) -> Result<T> {
    Err(ModelError(message.into()))
}

fn object_fields<'a>(
    value: &'a JsonValue,
    path: &str,
    required: &[&str],
    optional: &[&str],
) -> Result<&'a Map<String, JsonValue>> {
    let Some(object) = value.as_object() else {
        return fail(format!("{path} must be a JSON object"));
    };
    for field in required {
        if !object.contains_key(*field) {
            return fail(format!("{path} is missing required field {field}"));
        }
    }
    for field in object.keys() {
        if !required.contains(&field.as_str()) && !optional.contains(&field.as_str()) {
            return fail(format!("{path} has unsupported field {field}"));
        }
    }
    Ok(object)
}

fn check_version(root: &Map<String, JsonValue>, field: &str, supported: u32) -> Result<()> {
    let Some(value) = root.get(field) else {
        return fail(format!("workbook.{field} must be an integer"));
    };
    if let Some(version) = value.as_u64() {
        if version == u64::from(supported) {
            return Ok(());
        }
        return fail(format!(
            "unsupported {field} {version}; this reader supports {supported}"
        ));
    }
    if let Some(version) = value.as_i64() {
        return fail(format!(
            "unsupported {field} {version}; this reader supports {supported}"
        ));
    }
    fail(format!("workbook.{field} must be an integer"))
}

fn parse_address(address: &str, path: &str) -> Result<(u32, u32)> {
    let split = address.bytes().take_while(u8::is_ascii_uppercase).count();
    if !(1..=3).contains(&split) || address.len() <= split {
        return fail(format!("{path} must be a canonical A1 address"));
    }
    let (letters, digits) = address.split_at(split);
    if !digits.bytes().all(|byte| byte.is_ascii_digit()) || digits.starts_with('0') {
        return fail(format!("{path} must be a canonical A1 address"));
    }
    let mut column = 0u32;
    for byte in letters.bytes() {
        column = column * 26 + u32::from(byte - b'A' + 1);
    }
    let row = digits
        .parse::<u32>()
        .map_err(|_| ModelError(format!("{path} must be a canonical A1 address")))?;
    if column > 16_384 || row == 0 || row > 1_048_576 {
        return fail(format!("{path} is outside the Excel worksheet grid"));
    }
    Ok((row, column))
}

fn check_value(value: &JsonValue, path: &str) -> Result<()> {
    match value {
        JsonValue::Null | JsonValue::Bool(_) | JsonValue::Number(_) | JsonValue::String(_) => {
            Ok(())
        }
        JsonValue::Object(_) => {
            let error = object_fields(value, path, &["error"], &["message"])?;
            if error
                .get("error")
                .and_then(JsonValue::as_str)
                .is_none_or(str::is_empty)
            {
                return fail(format!("{path}.error must be a non-empty string"));
            }
            if error
                .get("message")
                .is_some_and(|message| !message.is_string())
            {
                return fail(format!("{path}.message must be a string"));
            }
            Ok(())
        }
        JsonValue::Array(_) => fail(format!(
            "{path} must be a JSON scalar or spreadsheet error object"
        )),
    }
}

fn check_sheet(sheet: &JsonValue, index: usize) -> Result<()> {
    let path = format!("workbook.sheets[{index}]");
    let sheet = object_fields(sheet, &path, &["name", "cells"], &["dimensions"])?;
    if sheet
        .get("name")
        .and_then(JsonValue::as_str)
        .is_none_or(str::is_empty)
    {
        return fail(format!("{path}.name must be a non-empty string"));
    }
    if let Some(dimensions) = sheet.get("dimensions") {
        let Some(coords) = dimensions.as_array() else {
            return fail(format!("{path}.dimensions must be a four-item array"));
        };
        if coords.len() != 4 {
            return fail(format!("{path}.dimensions must be a four-item array"));
        }
        let mut parsed = [0u32; 4];
        for (coord_index, coord) in coords.iter().enumerate() {
            let Some(value) = coord.as_u64().and_then(|value| u32::try_from(value).ok()) else {
                return fail(format!(
                    "{path}.dimensions[{coord_index}] must be a positive integer"
                ));
            };
            let limit = if coord_index < 2 { 1_048_576 } else { 16_384 };
            if value == 0 || value > limit {
                return fail(format!(
                    "{path}.dimensions[{coord_index}] is outside the Excel grid"
                ));
            }
            parsed[coord_index] = value;
        }
        if parsed[0] > parsed[1] || parsed[2] > parsed[3] {
            return fail(format!("{path}.dimensions minimum must not exceed maximum"));
        }
    }

    let Some(cells) = sheet.get("cells").and_then(JsonValue::as_object) else {
        return fail(format!(
            "{path}.cells must be an object keyed by A1 address"
        ));
    };
    for (address, cell) in cells {
        let cell_path = format!("{path}.cells[{address:?}]");
        parse_address(address, &format!("{cell_path} key"))?;
        let cell = object_fields(
            cell,
            &cell_path,
            &["address", "value", "data_type"],
            &["number_format", "formula"],
        )?;
        let Some(cell_address) = cell.get("address").and_then(JsonValue::as_str) else {
            return fail(format!("{cell_path}.address must be a string"));
        };
        parse_address(cell_address, &format!("{cell_path}.address"))?;
        if cell_address != address {
            return fail(format!("{cell_path}.address must match its cell-map key"));
        }
        check_value(&cell["value"], &format!("{cell_path}.value"))?;
        if !["blank", "number", "boolean", "text", "error", "unknown"].contains(
            &cell
                .get("data_type")
                .and_then(JsonValue::as_str)
                .unwrap_or(""),
        ) {
            return fail(format!(
                "{cell_path}.data_type is not a supported data type"
            ));
        }
        if cell
            .get("number_format")
            .is_some_and(|number_format| !number_format.is_string())
        {
            return fail(format!("{cell_path}.number_format must be a string"));
        }
        if let Some(formula) = cell.get("formula") {
            let formula_path = format!("{cell_path}.formula");
            let formula = object_fields(
                formula,
                &formula_path,
                &["expression", "dependencies"],
                &["cached_value"],
            )?;
            if !cell["value"].is_null() {
                return fail(format!(
                    "{cell_path}.value must be null when formula is present"
                ));
            }
            if formula
                .get("expression")
                .and_then(JsonValue::as_str)
                .is_none_or(|expression| !expression.starts_with('='))
            {
                return fail(format!("{formula_path}.expression must begin with '='"));
            }
            let Some(dependencies) = formula.get("dependencies").and_then(JsonValue::as_array)
            else {
                return fail(format!("{formula_path}.dependencies must be an array"));
            };
            let mut seen = BTreeSet::new();
            for dependency in dependencies {
                let Some(reference) = dependency.as_str() else {
                    return fail(format!(
                        "{formula_path}.dependencies entries must be strings"
                    ));
                };
                let Some((sheet_name, address)) = reference.split_once('!') else {
                    return fail(format!(
                        "{formula_path}.dependencies must use Sheet!A1 addresses"
                    ));
                };
                if sheet_name.is_empty() || reference.matches('!').count() != 1 {
                    return fail(format!(
                        "{formula_path}.dependencies must use Sheet!A1 addresses"
                    ));
                }
                parse_address(address, &format!("{formula_path}.dependencies entry"))?;
                if !seen.insert(reference) {
                    return fail(format!(
                        "{formula_path}.dependencies must not contain duplicates"
                    ));
                }
            }
            if let Some(cached_value) = formula.get("cached_value") {
                if cached_value.is_null() {
                    return fail(format!(
                        "{formula_path}.cached_value must be omitted when blank"
                    ));
                }
                check_value(cached_value, &format!("{formula_path}.cached_value"))?;
            }
        }
    }
    Ok(())
}

fn check_binding(binding: &JsonValue, index: usize, sheet_names: &BTreeSet<String>) -> Result<()> {
    let path = format!("workbook.bindings[{index}]");
    let binding = object_fields(
        binding,
        &path,
        &[
            "direction",
            "name",
            "sheet",
            "address",
            "value_type",
            "required",
            "constraints",
        ],
        &[],
    )?;
    let Some(direction) = binding.get("direction").and_then(JsonValue::as_str) else {
        return fail(format!("{path}.direction must be 'input' or 'output'"));
    };
    if !["input", "output"].contains(&direction) {
        return fail(format!("{path}.direction must be 'input' or 'output'"));
    }
    for field in ["name", "sheet"] {
        if binding
            .get(field)
            .and_then(JsonValue::as_str)
            .is_none_or(str::is_empty)
        {
            return fail(format!("{path}.{field} must be a non-empty string"));
        }
    }
    let sheet = binding["sheet"].as_str().expect("checked above");
    if !sheet_names.contains(sheet) {
        return fail(format!("{path}.sheet must name a sheet in this workbook"));
    }
    let Some(address) = binding.get("address").and_then(JsonValue::as_str) else {
        return fail(format!("{path}.address must be a canonical A1 address"));
    };
    parse_address(address, &format!("{path}.address"))?;
    if !["blank", "number", "boolean", "text", "error", "unknown"]
        .contains(&binding["value_type"].as_str().unwrap_or(""))
    {
        return fail(format!("{path}.value_type is not a supported data type"));
    }
    if !binding.get("required").is_some_and(JsonValue::is_boolean) {
        return fail(format!("{path}.required must be a boolean"));
    }
    let constraints = object_fields(
        &binding["constraints"],
        &format!("{path}.constraints"),
        &[],
        &["min", "max", "choices"],
    )?;
    let mut min = None;
    let mut max = None;
    for (field, slot) in [("min", &mut min), ("max", &mut max)] {
        if let Some(value) = constraints.get(field) {
            let Some(number) = value.as_f64().filter(|number| number.is_finite()) else {
                return fail(format!(
                    "{path}.constraints.{field} must be a finite number"
                ));
            };
            *slot = Some(number);
        }
    }
    if min
        .zip(max)
        .is_some_and(|(minimum, maximum)| minimum > maximum)
    {
        return fail(format!("{path}.constraints.min must not exceed max"));
    }
    if let Some(choices) = constraints.get("choices") {
        let Some(choices) = choices.as_array() else {
            return fail(format!("{path}.constraints.choices must be an array"));
        };
        for choice in choices {
            check_value(choice, &format!("{path}.constraints.choices[]"))?;
        }
    }
    Ok(())
}

fn validate_document(value: &JsonValue) -> Result<()> {
    let root = object_fields(
        value,
        "workbook",
        &[
            "schema_version",
            "model_version",
            "metadata",
            "bindings",
            "sheets",
        ],
        &["source_path"],
    )?;
    check_version(root, "schema_version", SCHEMA_VERSION)?;
    check_version(root, "model_version", MODEL_VERSION)?;
    if root
        .get("source_path")
        .is_some_and(|path| !path.is_string())
    {
        return fail("workbook.source_path must be a string when present");
    }
    if !root.get("metadata").is_some_and(JsonValue::is_object) {
        return fail("workbook.metadata must be a JSON object");
    }
    let Some(sheets) = root.get("sheets").and_then(JsonValue::as_array) else {
        return fail("workbook.sheets must be an array");
    };
    let mut sheet_names = BTreeSet::new();
    for (index, sheet) in sheets.iter().enumerate() {
        check_sheet(sheet, index)?;
        let name = sheet["name"].as_str().expect("validated sheet name");
        if !sheet_names.insert(name.to_owned()) {
            return fail(format!("duplicate worksheet name: {name}"));
        }
    }
    let Some(bindings) = root.get("bindings").and_then(JsonValue::as_array) else {
        return fail("workbook.bindings must be an array");
    };
    let mut binding_names = BTreeSet::new();
    for (index, binding) in bindings.iter().enumerate() {
        check_binding(binding, index, &sheet_names)?;
        let direction = binding["direction"].as_str().expect("validated direction");
        let name = binding["name"].as_str().expect("validated name");
        if !binding_names.insert((direction.to_owned(), name.to_owned())) {
            return fail(format!("duplicate {direction} binding name: {name}"));
        }
    }
    Ok(())
}

impl Workbook {
    /// Parse canonical JSON bytes into native Rust model types.
    pub fn from_bytes(bytes: &[u8]) -> Result<Self> {
        let value: JsonValue = serde_json::from_slice(bytes)?;
        validate_document(&value)?;
        // Parse the original bytes too: typed map visitors reject duplicate
        // fields and keys that a generic JSON object could otherwise collapse.
        serde_json::from_slice(bytes).map_err(ModelError::from)
    }

    /// Validate and serialize the native model to deterministic compact JSON.
    pub fn to_bytes(&self) -> Result<Vec<u8>> {
        let value = serde_json::to_value(self).map_err(ModelError::from)?;
        validate_document(&value)?;
        serde_json::to_vec(&value).map_err(ModelError::from)
    }
}

#[cfg(test)]
mod tests {
    use super::{CellValue, ModelError, Workbook};
    use serde_json::Value as JsonValue;

    const FIXTURE: &[u8] = include_bytes!("../../tests/fixtures/canonical/workbook-v1.json");

    #[test]
    fn shared_fixture_hydrates_and_serializes_stably() {
        let workbook = Workbook::from_bytes(FIXTURE).expect("canonical fixture must hydrate");
        assert_eq!(workbook.schema_version, 1);
        assert_eq!(workbook.model_version, 1);
        assert_eq!(workbook.bindings.len(), 2);
        assert_eq!(workbook.bindings[0].name, "amount");
        assert_eq!(workbook.bindings[0].constraints.minimum, Some(0.0));
        assert_eq!(workbook.bindings[0].constraints.maximum, Some(1000.0));
        assert_eq!(
            workbook.sheets[1].cells["A1"]
                .formula
                .as_ref()
                .unwrap()
                .dependencies,
            ["Inputs!A1"]
        );
        assert_eq!(workbook.sheets[1].cells["A1"].value, CellValue::Blank);
        assert_eq!(
            workbook.sheets[1].cells["A1"]
                .formula
                .as_ref()
                .unwrap()
                .cached_value,
            Some(CellValue::Number(20.0))
        );
        assert_eq!(
            workbook.sheets[1].cells["B1"]
                .formula
                .as_ref()
                .unwrap()
                .cached_value,
            None
        );

        let first_bytes = workbook.to_bytes().expect("model must serialize");
        let rehydrated = Workbook::from_bytes(&first_bytes).expect("serialized model must hydrate");
        let second_bytes = rehydrated
            .to_bytes()
            .expect("rehydrated model must serialize");
        assert_eq!(first_bytes, second_bytes);
        assert_eq!(
            serde_json::from_slice::<JsonValue>(&first_bytes).unwrap(),
            serde_json::from_slice::<JsonValue>(FIXTURE).unwrap()
        );
    }

    #[test]
    fn unsupported_versions_are_explicit() {
        for field in ["schema_version", "model_version"] {
            for version in [2, -1] {
                let mut document: JsonValue = serde_json::from_slice(FIXTURE).unwrap();
                document[field] = JsonValue::from(version);
                let error =
                    Workbook::from_bytes(&serde_json::to_vec(&document).unwrap()).unwrap_err();
                assert!(
                    error
                        .to_string()
                        .contains(&format!("unsupported {field} {version}"))
                );
            }
        }
    }

    #[test]
    fn rejects_unknown_fields_and_cell_map_address_mismatch() {
        let mut document: JsonValue = serde_json::from_slice(FIXTURE).unwrap();
        document["sheets"][0]["cells"]["A1"]["surprise"] = JsonValue::Bool(true);
        let error = Workbook::from_bytes(&serde_json::to_vec(&document).unwrap()).unwrap_err();
        assert!(error.to_string().contains("unsupported field surprise"));

        let mut document: JsonValue = serde_json::from_slice(FIXTURE).unwrap();
        document["sheets"][0]["cells"]["A1"]["address"] = JsonValue::String("B1".into());
        let error = Workbook::from_bytes(&serde_json::to_vec(&document).unwrap()).unwrap_err();
        assert!(error.to_string().contains("must match its cell-map key"));
    }

    #[test]
    fn rejects_unsupported_error_object_fields() {
        let mut document: JsonValue = serde_json::from_slice(FIXTURE).unwrap();
        document["sheets"][0]["cells"]["A1"]["value"] = serde_json::json!({
            "error": "#VALUE!",
            "unexpected": "silent loss"
        });
        let error = Workbook::from_bytes(&serde_json::to_vec(&document).unwrap()).unwrap_err();
        assert!(error.to_string().contains("unsupported field unexpected"));
    }

    #[test]
    fn rejects_invalid_binding_constraints() {
        let mut document: JsonValue = serde_json::from_slice(FIXTURE).unwrap();
        document["bindings"][0]["constraints"] = serde_json::json!({"min": 10, "max": 2});
        let error = Workbook::from_bytes(&serde_json::to_vec(&document).unwrap()).unwrap_err();
        assert!(
            error
                .to_string()
                .contains("constraints.min must not exceed max")
        );
    }

    #[test]
    fn missing_version_is_not_treated_as_v1() {
        let mut document: JsonValue = serde_json::from_slice(FIXTURE).unwrap();
        document.as_object_mut().unwrap().remove("model_version");
        let error: ModelError =
            Workbook::from_bytes(&serde_json::to_vec(&document).unwrap()).unwrap_err();
        assert!(
            error
                .to_string()
                .contains("missing required field model_version")
        );
    }

    #[test]
    fn rejects_duplicate_version_keys() {
        let fixture = std::str::from_utf8(FIXTURE).unwrap();
        let duplicate = fixture.replacen(
            "\"model_version\": 1,",
            "\"model_version\": 1, \"model_version\": 1,",
            1,
        );
        let error = Workbook::from_bytes(duplicate.as_bytes()).unwrap_err();
        assert!(
            error
                .to_string()
                .contains("duplicate field `model_version`")
        );
    }
}
