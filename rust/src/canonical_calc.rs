//! Calculation sessions over the canonical workbook model.
//!
//! The session owns a snapshot of the native `model::Workbook`. It does not
//! translate the workbook into the older toolkit session DTO, and formula
//! caches remain observations rather than calculation inputs.

use crate::model::{BindingDirection, CellValue, DataType, Workbook};
use crate::toolkit::{SUPPORTED_FUNCTIONS, analyze_formula};
use crate::{FormulaError, FormulaResult, Value, evaluate_result};
use serde::Serialize;
use serde_json::{Number, Value as JsonValue, json};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fmt;
use std::sync::Mutex;

const MAX_DEPENDENCIES: usize = 100_000;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CalculationError {
    pub code: String,
    pub message: String,
}

impl CalculationError {
    fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }
}

impl fmt::Display for CalculationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for CalculationError {}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Diagnostic {
    pub code: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sheet: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub address: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct CalculationReport {
    pub backend: &'static str,
    pub schema_version: u32,
    pub model_version: u32,
    pub revision: u64,
    pub outputs: BTreeMap<String, JsonValue>,
    pub values: BTreeMap<String, JsonValue>,
    pub diagnostics: Vec<Diagnostic>,
}

fn cell_key(sheet: &str, address: &str) -> String {
    format!("{sheet}!{address}")
}

fn value_type_matches(value_type: &DataType, value: &CellValue) -> bool {
    match value_type {
        DataType::Blank => matches!(value, CellValue::Blank),
        DataType::Number => matches!(value, CellValue::Number(number) if number.is_finite()),
        DataType::Boolean => matches!(value, CellValue::Boolean(_)),
        DataType::Text => matches!(value, CellValue::Text(_)),
        DataType::Error => matches!(value, CellValue::Error { .. }),
        DataType::Unknown => true,
    }
}

fn cell_to_engine(value: &CellValue) -> Value {
    match value {
        CellValue::Number(number) => Value::Number(*number),
        CellValue::Text(text) => Value::Text(text.clone()),
        CellValue::Boolean(boolean) => Value::Bool(*boolean),
        CellValue::Blank => Value::Blank,
        CellValue::Error { error, .. } => Value::Error(match error.as_str() {
            "#DIV/0!" => FormulaError::Div0,
            "#REF!" => FormulaError::Ref,
            "#NAME?" => FormulaError::Name,
            "#NUM!" => FormulaError::Num,
            "#N/A" | "#N/A!" => FormulaError::NA,
            "#CALC!" => FormulaError::Calc,
            _ => FormulaError::Value,
        }),
    }
}

fn engine_to_json(value: &Value) -> JsonValue {
    match value {
        Value::Number(number) => Number::from_f64(*number)
            .map(JsonValue::Number)
            .unwrap_or(JsonValue::Null),
        Value::Text(text) => JsonValue::String(text.clone()),
        Value::Bool(boolean) => JsonValue::Bool(*boolean),
        Value::Blank => JsonValue::Null,
        Value::Error(error) => json!({"error": error.to_string()}),
    }
}

fn function_names(formula: &str) -> Result<BTreeSet<String>, CalculationError> {
    let analysis = analyze_formula(formula)
        .map_err(|error| CalculationError::new(error.code, error.message))?;
    let functions = analysis
        .get("functions")
        .and_then(JsonValue::as_array)
        .ok_or_else(|| {
            CalculationError::new("parse_error", "formula analysis omitted functions")
        })?;
    let mut names = BTreeSet::new();
    for function in functions {
        let name = function.as_str().ok_or_else(|| {
            CalculationError::new(
                "parse_error",
                "formula analysis returned an invalid function",
            )
        })?;
        let canonical = name.to_ascii_uppercase();
        if !SUPPORTED_FUNCTIONS.contains(&canonical.as_str()) {
            return Err(CalculationError::new(
                "unsupported_formula",
                format!("function {canonical} is not evaluated by Rust"),
            ));
        }
        names.insert(canonical);
    }
    Ok(names)
}

fn coordinate_to_address(row: u64, column: u64) -> Result<String, CalculationError> {
    if row == 0 || column == 0 || row > 1_048_576 || column > 16_384 {
        return Err(CalculationError::new(
            "invalid_reference",
            "formula reference is outside the Excel worksheet grid",
        ));
    }
    let mut column = column;
    let mut letters = String::new();
    while column > 0 {
        let remainder = ((column - 1) % 26) as u8;
        letters.push(char::from(b'A' + remainder));
        column = (column - 1) / 26;
    }
    Ok(format!(
        "{}{}",
        letters.chars().rev().collect::<String>(),
        row
    ))
}

fn reference_cells(
    formula: &crate::model::Formula,
    current_sheet: &str,
    workbook: &Workbook,
) -> Result<Vec<String>, CalculationError> {
    function_names(&formula.expression)?;
    let analysis = analyze_formula(&formula.expression)
        .map_err(|error| CalculationError::new(error.code, error.message))?;
    let references = analysis
        .get("references")
        .and_then(JsonValue::as_array)
        .ok_or_else(|| {
            CalculationError::new("parse_error", "formula analysis omitted references")
        })?;
    let sheet_names: BTreeMap<String, String> = workbook
        .sheets
        .iter()
        .map(|sheet| (sheet.name.to_ascii_lowercase(), sheet.name.clone()))
        .collect();
    let mut dependencies = BTreeSet::new();
    for reference in references {
        let start = reference
            .get("start")
            .ok_or_else(|| CalculationError::new("parse_error", "reference omitted start"))?;
        let end = reference
            .get("end")
            .filter(|item| !item.is_null())
            .unwrap_or(start);
        let start_sheet = start
            .get("sheet")
            .and_then(JsonValue::as_str)
            .unwrap_or(current_sheet);
        let end_sheet = end
            .get("sheet")
            .and_then(JsonValue::as_str)
            .unwrap_or(start_sheet);
        if !start_sheet.eq_ignore_ascii_case(end_sheet) {
            return Err(CalculationError::new(
                "unsupported_formula",
                "ranges spanning multiple worksheets are not supported",
            ));
        }
        let canonical_sheet = sheet_names
            .get(&start_sheet.to_ascii_lowercase())
            .ok_or_else(|| {
                CalculationError::new(
                    "invalid_reference",
                    format!("unknown worksheet {start_sheet}"),
                )
            })?;
        let start_row = start
            .get("row")
            .and_then(JsonValue::as_u64)
            .ok_or_else(|| {
                CalculationError::new("parse_error", "reference start row is invalid")
            })?;
        let start_column = start
            .get("column")
            .and_then(JsonValue::as_u64)
            .ok_or_else(|| {
                CalculationError::new("parse_error", "reference start column is invalid")
            })?;
        let end_row = end
            .get("row")
            .and_then(JsonValue::as_u64)
            .unwrap_or(start_row);
        let end_column = end
            .get("column")
            .and_then(JsonValue::as_u64)
            .unwrap_or(start_column);
        let (first_row, last_row) = (start_row.min(end_row), start_row.max(end_row));
        let (first_column, last_column) =
            (start_column.min(end_column), start_column.max(end_column));
        let count = (last_row - first_row + 1).saturating_mul(last_column - first_column + 1);
        if count > (MAX_DEPENDENCIES - dependencies.len()) as u64 {
            return Err(CalculationError::new(
                "resource_limit",
                "formula dependency expansion exceeds 100000 cells",
            ));
        }
        for row in first_row..=last_row {
            for column in first_column..=last_column {
                dependencies.insert(cell_key(
                    canonical_sheet,
                    &coordinate_to_address(row, column)?,
                ));
            }
        }
    }
    let declared: BTreeSet<_> = formula.dependencies.iter().cloned().collect();
    if declared != dependencies {
        return Err(CalculationError::new(
            "invalid_dependencies",
            "declared formula dependencies do not match references in the formula",
        ));
    }
    Ok(dependencies.into_iter().collect())
}

#[derive(Default)]
struct Calculator {
    states: BTreeMap<String, &'static str>,
    values: BTreeMap<String, Value>,
    array_values: BTreeMap<String, JsonValue>,
    diagnostics: BTreeMap<String, Diagnostic>,
}

impl Calculator {
    fn fail_cell(&mut self, key: &str, code: &str, message: impl Into<String>) {
        let (sheet, address) = key.rsplit_once('!').unwrap_or((key, ""));
        self.diagnostics
            .entry(key.to_owned())
            .or_insert_with(|| Diagnostic {
                code: code.to_owned(),
                message: message.into(),
                sheet: Some(sheet.to_owned()),
                address: Some(address.to_owned()),
            });
        self.states.insert(key.to_owned(), "failed");
    }

    fn evaluate_cell(&mut self, key: &str, workbook: &Workbook) -> Option<Value> {
        match self.states.get(key).copied() {
            Some("done") => return self.values.get(key).cloned(),
            Some("failed") => return None,
            Some("visiting") => {
                self.fail_cell(key, "dependency_cycle", "formula dependency cycle");
                return None;
            }
            _ => {}
        }
        let Some((sheet_name, address)) = key.rsplit_once('!') else {
            self.fail_cell(
                key,
                "invalid_reference",
                "cell address must include a worksheet",
            );
            return None;
        };
        let Some(sheet) = workbook
            .sheets
            .iter()
            .find(|sheet| sheet.name.eq_ignore_ascii_case(sheet_name))
        else {
            self.fail_cell(
                key,
                "invalid_reference",
                format!("unknown worksheet {sheet_name}"),
            );
            return None;
        };
        let Some(cell) = sheet.cells.get(address) else {
            self.values.insert(key.to_owned(), Value::Blank);
            self.states.insert(key.to_owned(), "done");
            return Some(Value::Blank);
        };
        let Some(formula) = cell.formula.as_ref() else {
            let value = cell_to_engine(&cell.value);
            self.values.insert(key.to_owned(), value.clone());
            self.states.insert(key.to_owned(), "done");
            return Some(value);
        };
        self.states.insert(key.to_owned(), "visiting");
        let dependencies = match reference_cells(formula, &sheet.name, workbook) {
            Ok(dependencies) => dependencies,
            Err(error) => {
                self.fail_cell(key, &error.code, error.message);
                return None;
            }
        };
        for dependency in &dependencies {
            let value = self.evaluate_cell(dependency, workbook);
            if self.array_values.contains_key(dependency) {
                self.fail_cell(
                    key,
                    "unsupported_array_reference",
                    format!("dependency {dependency} is an array"),
                );
                return None;
            }
            if value.is_none() && self.states.get(dependency).copied() == Some("failed") {
                self.fail_cell(
                    key,
                    "unsupported_dependency",
                    format!("dependency {dependency} could not be calculated"),
                );
                return None;
            }
        }
        let mut environment = HashMap::new();
        for dependency in &dependencies {
            if let Some(value) = self.values.get(dependency) {
                environment.insert(dependency.clone(), value.clone());
            }
        }
        let result = match evaluate_result(&formula.expression, &environment, &sheet.name) {
            Ok(FormulaResult::Scalar(value)) => value,
            Ok(FormulaResult::Array(array)) => {
                self.states.insert(key.to_owned(), "done");
                let rows = array
                    .rows()
                    .iter()
                    .map(|row| row.iter().map(engine_to_json).collect::<Vec<_>>())
                    .collect::<Vec<_>>();
                self.array_values
                    .insert(key.to_owned(), json!({"rows": rows}));
                return Some(Value::Blank);
            }
            Err(error) if error.excel_code().is_some() => Value::Error(error),
            Err(error) => {
                let code = match error {
                    FormulaError::Parse(_) => "parse_error",
                    FormulaError::Unsupported(_) => "unsupported_formula",
                    _ => "unsupported_formula",
                };
                self.fail_cell(key, code, error.to_string());
                return None;
            }
        };
        self.values.insert(key.to_owned(), result.clone());
        self.states.insert(key.to_owned(), "done");
        Some(result)
    }

    fn calculate(mut self, workbook: &Workbook, revision: u64) -> CalculationReport {
        let mut outputs = BTreeMap::new();
        let mut output_bindings: Vec<_> = workbook
            .bindings
            .iter()
            .filter(|binding| binding.direction == BindingDirection::Output)
            .collect();
        output_bindings.sort_by(|left, right| left.name.cmp(&right.name));
        for binding in &output_bindings {
            let Some(sheet) = workbook
                .sheets
                .iter()
                .find(|sheet| sheet.name.eq_ignore_ascii_case(&binding.sheet))
            else {
                self.diagnostics.insert(
                    format!("output:{}", binding.name),
                    Diagnostic {
                        code: "invalid_binding".into(),
                        message: format!("unknown worksheet {}", binding.sheet),
                        sheet: None,
                        address: None,
                    },
                );
                continue;
            };
            if !sheet.cells.contains_key(&binding.address) {
                self.diagnostics.insert(
                    format!("output:{}", binding.name),
                    Diagnostic {
                        code: "invalid_binding".into(),
                        message: format!("output {} points to an empty cell", binding.name),
                        sheet: Some(sheet.name.clone()),
                        address: Some(binding.address.clone()),
                    },
                );
                continue;
            }
            let key = cell_key(&sheet.name, &binding.address);
            self.evaluate_cell(&key, workbook);
            if self.states.get(&key).copied() == Some("done") {
                let value = self
                    .array_values
                    .get(&key)
                    .cloned()
                    .or_else(|| self.values.get(&key).map(engine_to_json));
                if let Some(value) = value {
                    outputs.insert(binding.name.clone(), value);
                }
            }
        }
        let mut values = self
            .values
            .iter()
            .map(|(key, value)| (key.clone(), engine_to_json(value)))
            .collect::<BTreeMap<_, _>>();
        for (key, value) in self.array_values {
            values.insert(key, value);
        }
        CalculationReport {
            backend: "rust",
            schema_version: workbook.schema_version,
            model_version: workbook.model_version,
            revision,
            outputs,
            values,
            diagnostics: self.diagnostics.into_values().collect(),
        }
    }
}

/// A calculation session operating directly on the canonical Rust Workbook.
pub struct CanonicalCalculationSession {
    workbook: Mutex<Workbook>,
    revision: Mutex<u64>,
}

impl CanonicalCalculationSession {
    pub fn new(workbook: Workbook) -> Result<Self, CalculationError> {
        workbook
            .to_bytes()
            .map_err(|error| CalculationError::new("invalid_model", error.to_string()))?;
        Self::validate_bindings(&workbook)?;
        let session = Self {
            workbook: Mutex::new(workbook),
            revision: Mutex::new(0),
        };
        session.set_inputs(&BTreeMap::new(), Some(0))?;
        Ok(session)
    }

    fn validate_bindings(workbook: &Workbook) -> Result<(), CalculationError> {
        let mut input_cells = BTreeSet::new();
        for binding in &workbook.bindings {
            let Some(sheet) = workbook
                .sheets
                .iter()
                .find(|sheet| sheet.name.eq_ignore_ascii_case(&binding.sheet))
            else {
                return Err(CalculationError::new(
                    "invalid_binding",
                    format!("unknown worksheet {}", binding.sheet),
                ));
            };
            let cell = sheet.cells.get(&binding.address);
            match binding.direction {
                BindingDirection::Input => {
                    let key = cell_key(&sheet.name, &binding.address);
                    if !input_cells.insert(key) {
                        return Err(CalculationError::new(
                            "invalid_input",
                            "multiple inputs bind the same cell",
                        ));
                    }
                    if cell.is_none_or(|cell| cell.formula.is_some()) {
                        return Err(CalculationError::new(
                            "invalid_input",
                            "input must bind an existing literal cell",
                        ));
                    }
                }
                BindingDirection::Output if cell.is_none() => {
                    return Err(CalculationError::new(
                        "invalid_binding",
                        "output must bind an existing cell",
                    ));
                }
                BindingDirection::Output => {}
            }
        }
        Ok(())
    }

    pub fn revision(&self) -> Result<u64, CalculationError> {
        self.revision.lock().map(|revision| *revision).map_err(|_| {
            CalculationError::new("session_poisoned", "session revision lock poisoned")
        })
    }

    pub fn workbook(&self) -> Result<Workbook, CalculationError> {
        self.workbook
            .lock()
            .map(|workbook| workbook.clone())
            .map_err(|_| {
                CalculationError::new("session_poisoned", "session workbook lock poisoned")
            })
    }

    pub fn set_inputs(
        &self,
        values: &BTreeMap<String, CellValue>,
        expected_revision: Option<u64>,
    ) -> Result<u64, CalculationError> {
        let mut workbook = self.workbook.lock().map_err(|_| {
            CalculationError::new("session_poisoned", "session workbook lock poisoned")
        })?;
        let mut revision = self.revision.lock().map_err(|_| {
            CalculationError::new("session_poisoned", "session revision lock poisoned")
        })?;
        if expected_revision.is_some_and(|expected| expected != *revision) {
            return Err(CalculationError::new(
                "revision_conflict",
                "model revision conflict",
            ));
        }
        let input_bindings: BTreeMap<_, _> = workbook
            .bindings
            .iter()
            .filter(|binding| binding.direction == BindingDirection::Input)
            .map(|binding| (binding.name.clone(), binding.clone()))
            .collect();
        if let Some(unknown) = values
            .keys()
            .find(|name| !input_bindings.contains_key(*name))
        {
            return Err(CalculationError::new(
                "unknown_input",
                format!("unknown input {unknown}"),
            ));
        }
        let mut updates = Vec::new();
        for (name, binding) in &input_bindings {
            let sheet = workbook
                .sheets
                .iter()
                .find(|sheet| sheet.name.eq_ignore_ascii_case(&binding.sheet))
                .expect("bindings validated at session creation");
            let cell = sheet
                .cells
                .get(&binding.address)
                .expect("input cells validated at session creation");
            let supplied = values.get(name);
            let value = supplied.unwrap_or(&cell.value);
            if matches!(value, CellValue::Blank) && !binding.required {
                if supplied.is_some() {
                    updates.push((
                        binding.sheet.clone(),
                        binding.address.clone(),
                        value.clone(),
                    ));
                }
                continue;
            }
            if !value_type_matches(&binding.value_type, value) {
                return Err(CalculationError::new(
                    "invalid_input",
                    format!("{name}: input expects {:?}", binding.value_type),
                ));
            }
            if let CellValue::Number(number) = value {
                if binding
                    .constraints
                    .minimum
                    .is_some_and(|minimum| *number < minimum)
                    || binding
                        .constraints
                        .maximum
                        .is_some_and(|maximum| *number > maximum)
                {
                    return Err(CalculationError::new(
                        "invalid_input",
                        format!("{name}: input is outside its numeric bounds"),
                    ));
                }
            }
            if binding
                .constraints
                .choices
                .as_ref()
                .is_some_and(|choices| !choices.iter().any(|choice| choice == value))
            {
                return Err(CalculationError::new(
                    "invalid_input",
                    format!("{name}: input is not an allowed choice"),
                ));
            }
            // Validate the existing cell model before a candidate update is
            // applied; all updates remain staged until every input passes.
            let _ = cell;
            if supplied.is_some() {
                updates.push((
                    binding.sheet.clone(),
                    binding.address.clone(),
                    value.clone(),
                ));
            }
        }
        if values.is_empty() {
            return Ok(*revision);
        }
        let mut candidate = workbook.clone();
        for (sheet_name, address, value) in updates {
            let sheet = candidate
                .sheets
                .iter_mut()
                .find(|sheet| sheet.name.eq_ignore_ascii_case(&sheet_name))
                .expect("input sheet validated");
            let cell = sheet.cells.get_mut(&address).expect("input cell validated");
            cell.value = value;
            cell.data_type = input_bindings
                .values()
                .find(|binding| {
                    binding.sheet.eq_ignore_ascii_case(&sheet_name) && binding.address == address
                })
                .expect("input binding exists")
                .value_type
                .clone();
        }
        candidate
            .to_bytes()
            .map_err(|error| CalculationError::new("invalid_model", error.to_string()))?;
        *workbook = candidate;
        *revision += 1;
        Ok(*revision)
    }

    pub fn calculate(&self) -> Result<CalculationReport, CalculationError> {
        let (workbook, revision) = {
            let workbook = self.workbook.lock().map_err(|_| {
                CalculationError::new("session_poisoned", "session workbook lock poisoned")
            })?;
            let revision = self.revision.lock().map_err(|_| {
                CalculationError::new("session_poisoned", "session revision lock poisoned")
            })?;
            (workbook.clone(), *revision)
        };
        Ok(Calculator::default().calculate(&workbook, revision))
    }
}

#[cfg(test)]
mod tests {
    use super::{CalculationReport, CanonicalCalculationSession};
    use crate::model::{CellValue, Workbook};
    use serde::Deserialize;
    use serde_json::Value as JsonValue;
    use std::collections::BTreeMap;

    const MODEL_BYTES: &[u8] =
        include_bytes!("../../tests/fixtures/canonical/operating-scenario-v1.json");
    const CASE_BYTES: &[u8] =
        include_bytes!("../../tests/fixtures/canonical/operating-scenario-cases.json");

    #[derive(Deserialize)]
    struct Cases {
        numeric_tolerance: f64,
        cases: Vec<Case>,
    }

    #[derive(Deserialize)]
    struct Case {
        name: String,
        inputs: BTreeMap<String, CellValue>,
        expected: BTreeMap<String, JsonValue>,
    }

    fn shared_cases() -> Cases {
        serde_json::from_slice(CASE_BYTES).expect("shared golden cases must be valid JSON")
    }

    fn assert_json_close(actual: &JsonValue, expected: &JsonValue, tolerance: f64, at: &str) {
        match (actual.as_f64(), expected.as_f64()) {
            (Some(actual), Some(expected)) => assert!(
                (actual - expected).abs() <= tolerance,
                "{at}: expected {expected}, got {actual}"
            ),
            _ => assert_eq!(actual, expected, "{at}"),
        }
    }

    fn assert_json_semantic(actual: &JsonValue, expected: &JsonValue, at: &str) {
        match (actual, expected) {
            (JsonValue::Number(actual), JsonValue::Number(expected)) => assert_eq!(
                actual.as_f64(),
                expected.as_f64(),
                "{at}: JSON number meaning differs"
            ),
            (JsonValue::Object(actual), JsonValue::Object(expected)) => {
                assert_eq!(
                    actual.keys().collect::<Vec<_>>(),
                    expected.keys().collect::<Vec<_>>(),
                    "{at}"
                );
                for key in actual.keys() {
                    assert_json_semantic(&actual[key], &expected[key], &format!("{at}.{key}"));
                }
            }
            (JsonValue::Array(actual), JsonValue::Array(expected)) => {
                assert_eq!(actual.len(), expected.len(), "{at}");
                for (index, (actual, expected)) in actual.iter().zip(expected).enumerate() {
                    assert_json_semantic(actual, expected, &format!("{at}[{index}]"));
                }
            }
            _ => assert_eq!(actual, expected, "{at}"),
        }
    }

    fn run_case(case: &Case) -> CalculationReport {
        let workbook = Workbook::from_bytes(MODEL_BYTES).expect("canonical fixture hydrates");
        let session = CanonicalCalculationSession::new(workbook).expect("valid session");
        assert_eq!(session.set_inputs(&case.inputs, Some(0)).unwrap(), 1);
        session.calculate().unwrap()
    }

    #[test]
    fn shared_canonical_cases_calculate_with_typed_errors() {
        let cases = shared_cases();
        assert_eq!(cases.cases.len(), 3);
        for case in &cases.cases {
            let report = run_case(case);
            assert_eq!(report.backend, "rust", "{}", case.name);
            assert_eq!(report.schema_version, 1, "{}", case.name);
            assert_eq!(report.model_version, 1, "{}", case.name);
            for (name, expected) in &case.expected {
                let actual = report
                    .outputs
                    .get(name)
                    .expect("every named output is calculated");
                assert_json_close(actual, expected, cases.numeric_tolerance, name);
            }
            assert!(
                report.diagnostics.is_empty(),
                "{}: {:?}",
                case.name,
                report.diagnostics
            );
        }
        let boundary = run_case(&cases.cases[2]);
        assert_eq!(
            boundary.outputs["break_even_units"],
            serde_json::json!({"error":"#N/A"})
        );
    }

    #[test]
    fn canonical_serialization_preserves_bindings_and_separates_formula_cache() {
        let workbook = Workbook::from_bytes(MODEL_BYTES).expect("fixture hydrates");
        let serialized = workbook.to_bytes().expect("native model serializes");
        let first: JsonValue = serde_json::from_slice(MODEL_BYTES).unwrap();
        let second: JsonValue = serde_json::from_slice(&serialized).unwrap();
        assert_json_semantic(&first, &second, "canonical workbook");
        let round_trip = Workbook::from_bytes(&serialized).unwrap();
        assert_eq!(round_trip.to_bytes().unwrap(), serialized);
        assert!(round_trip.bindings.iter().any(|binding| {
            binding.name == "unit_price"
                && binding.sheet == "Assumptions"
                && binding.address == "B1"
        }));
        let formula = round_trip.sheets[1].cells["F2"].formula.as_ref().unwrap();
        assert!(matches!(
            round_trip.sheets[1].cells["F2"].value,
            CellValue::Blank
        ));
        assert_eq!(formula.cached_value, Some(CellValue::Number(7400.0)));
        let report = run_case(&shared_cases().cases[1]);
        assert_eq!(report.outputs["revenue"], 9250.0);
        assert_ne!(
            report.outputs["revenue"],
            formula
                .cached_value
                .as_ref()
                .map(|value| match value {
                    CellValue::Number(number) => JsonValue::from(*number),
                    _ => JsonValue::Null,
                })
                .unwrap()
        );
    }

    #[test]
    fn input_refusals_are_explicit_and_batch_updates_are_atomic() {
        let workbook = Workbook::from_bytes(MODEL_BYTES).unwrap();
        let session = CanonicalCalculationSession::new(workbook).unwrap();
        let initial = session.workbook().unwrap().to_bytes().unwrap();
        let mut cases = Vec::new();
        cases.push((
            "unknown_input",
            [("missing".to_owned(), CellValue::Number(1.0))].into(),
        ));
        cases.push((
            "invalid_input",
            [("unit_price".to_owned(), CellValue::Text("20".into()))].into(),
        ));
        cases.push((
            "invalid_input",
            [("unit_price".to_owned(), CellValue::Blank)].into(),
        ));
        cases.push((
            "invalid_input",
            [("unit_price".to_owned(), CellValue::Number(-1.0))].into(),
        ));
        let batch = [
            ("unit_price".to_owned(), CellValue::Number(25.0)),
            ("unit_cost".to_owned(), CellValue::Text("bad".into())),
        ]
        .into();
        cases.push(("invalid_input", batch));
        for (expected_code, inputs) in cases {
            let error = session.set_inputs(&inputs, Some(0)).unwrap_err();
            assert_eq!(error.code, expected_code);
            assert_eq!(session.revision().unwrap(), 0);
            assert_eq!(session.workbook().unwrap().to_bytes().unwrap(), initial);
        }
        assert_eq!(
            session
                .set_inputs(&BTreeMap::new(), Some(1))
                .unwrap_err()
                .code,
            "revision_conflict"
        );
    }

    #[test]
    fn unsupported_formula_and_cycles_are_reported_without_using_caches() {
        let mut workbook = Workbook::from_bytes(MODEL_BYTES).unwrap();
        let forecast = &mut workbook.sheets[1];
        let total_revenue = forecast.cells.get_mut("F2").unwrap();
        let formula = total_revenue.formula.as_mut().unwrap();
        formula.expression = "=MYSTERY(1)".into();
        formula.dependencies.clear();
        let session = CanonicalCalculationSession::new(workbook.clone()).unwrap();
        let report = session.calculate().unwrap();
        assert!(
            report
                .diagnostics
                .iter()
                .any(|item| item.code == "unsupported_formula")
        );
        assert!(!report.outputs.contains_key("revenue"));

        let forecast = &mut workbook.sheets[1];
        for (address, expression, dependency) in [
            ("C2", "=D2+1", "Forecast!D2"),
            ("D2", "=C2+1", "Forecast!C2"),
        ] {
            let cell = forecast.cells.get_mut(address).unwrap();
            let formula = cell.formula.as_mut().unwrap();
            formula.expression = expression.into();
            formula.dependencies = vec![dependency.into()];
            formula.cached_value = Some(CellValue::Number(999.0));
        }
        let session = CanonicalCalculationSession::new(workbook).unwrap();
        let report = session.calculate().unwrap();
        assert!(
            report
                .diagnostics
                .iter()
                .any(|item| item.code == "dependency_cycle")
        );
        assert!(!report.outputs.contains_key("revenue"));
    }
}
