//! Canonical workbook model.
//!
//! `schemas/workbook-model.v1.schema.json` is the source of truth. These types
//! are hydrated from those bytes. `schema_version` is the wire shape.
//! `model_version` is the semantic interpretation.

use crate::toolkit::{Expression, SUPPORTED_FUNCTIONS};
use crate::{FormulaError, FormulaResult, Value, evaluate_result};
use serde_json::{Map, Number, Value as JsonValue, json};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fmt;

const MAX_ROW: u64 = 1_048_576;
const MAX_COLUMN: u64 = 16_384;
const MAX_RANGE_CELLS: u64 = 100_000;
const ERROR_CODES: &[&str] = &[
    "#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#SPILL!", "#CALC!",
];

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ModelError {
    pub code: String,
    pub message: String,
}

impl ModelError {
    fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }
}

impl fmt::Display for ModelError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for ModelError {}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Provenance {
    pub origin: String,
    pub source: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CellId {
    pub sheet: String,
    pub address: String,
}

impl CellId {
    pub fn key(&self) -> String {
        format!("{}!{}", self.sheet, self.address)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Bindings {
    pub inputs: BTreeMap<String, CellId>,
    pub outputs: BTreeMap<String, CellId>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Diagnostic {
    pub code: String,
    pub classification: String,
    pub message: String,
    pub sheet: Option<String>,
    pub address: Option<String>,
    pub function: Option<String>,
}

impl Diagnostic {
    fn cell(
        code: &str,
        classification: &str,
        message: impl Into<String>,
        sheet: &str,
        address: &str,
        function: Option<String>,
    ) -> Self {
        Self {
            code: code.to_string(),
            classification: classification.to_string(),
            message: message.into(),
            sheet: Some(sheet.to_string()),
            address: Some(address.to_string()),
            function,
        }
    }

    fn sort_key(&self) -> (String, String, String, String, String) {
        (
            self.sheet.clone().unwrap_or_default(),
            self.address.clone().unwrap_or_default(),
            self.code.clone(),
            self.function.clone().unwrap_or_default(),
            self.message.clone(),
        )
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum Scalar {
    Blank,
    Number(Number),
    Text(String),
    Boolean(bool),
    Error {
        code: String,
        message: Option<String>,
    },
}

#[derive(Clone, Debug, PartialEq)]
pub struct Formula {
    pub expression: String,
    pub dependencies: Vec<String>,
    pub result: Scalar,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Cell {
    pub address: String,
    pub value: Scalar,
    pub data_type: String,
    pub number_format: Option<String>,
    pub formula: Option<Formula>,
    pub provenance: Option<Provenance>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Sheet {
    pub name: String,
    pub cells: BTreeMap<String, Cell>,
    pub dimensions: Option<[u64; 4]>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Workbook {
    pub schema_version: i64,
    pub model_version: i64,
    pub metadata: Map<String, JsonValue>,
    pub sheets: Vec<Sheet>,
    pub source_path: Option<String>,
    pub provenance: Option<Provenance>,
    pub bindings: Option<Bindings>,
    pub diagnostics: Vec<Diagnostic>,
}

impl Workbook {
    pub fn sheet(&self, name: &str) -> Option<&Sheet> {
        self.sheets.iter().find(|sheet| sheet.name == name)
    }

    pub fn to_json(&self) -> String {
        write_canonical(&workbook_json(self))
    }
}

enum Outcome {
    Value(Scalar),
    Diagnostic(Diagnostic),
}

/// Hydrate native types from canonical JSON bytes. Does not calculate.
pub fn hydrate(bytes: &[u8]) -> Result<Workbook, ModelError> {
    let text = std::str::from_utf8(bytes)
        .map_err(|_| ModelError::new("invalid_model", "document is not valid UTF-8"))?;
    let parsed: JsonValue = serde_json::from_str(text)
        .map_err(|_| ModelError::new("invalid_model", "document is not valid JSON"))?;
    let object = parsed
        .as_object()
        .ok_or_else(|| ModelError::new("invalid_model", "document must be an object"))?;
    parse_workbook(object)
}

/// Calculate formula results on a hydrated workbook.
///
/// Bindings already name canonical cells. Unsupported formulas are classified
/// and are not evaluated. Imported cell values are not used as formula results.
pub fn calculate(workbook: &Workbook) -> Result<Workbook, ModelError> {
    require_bindings(workbook)?;
    let mut cells: BTreeMap<String, Cell> = BTreeMap::new();
    let mut sheet_of: BTreeMap<String, String> = BTreeMap::new();
    for sheet in &workbook.sheets {
        for (address, cell) in &sheet.cells {
            let key = format!("{}!{address}", sheet.name);
            cells.insert(key.clone(), cell.clone());
            sheet_of.insert(key, sheet.name.clone());
        }
    }

    let mut analyzed: BTreeMap<String, (Vec<String>, Option<Diagnostic>)> = BTreeMap::new();
    for sheet in &workbook.sheets {
        for (address, cell) in &sheet.cells {
            let Some(formula) = &cell.formula else {
                continue;
            };
            let key = format!("{}!{address}", sheet.name);
            analyzed.insert(
                key,
                analyze(workbook, &sheet.name, address, &formula.expression),
            );
        }
    }

    let formula_keys: BTreeSet<String> = analyzed.keys().cloned().collect();
    let mut pending: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for (key, (deps, _)) in &analyzed {
        pending.insert(
            key.clone(),
            deps.iter()
                .filter(|dep| formula_keys.contains(*dep))
                .cloned()
                .collect(),
        );
    }
    let mut ready: BTreeSet<String> = pending
        .iter()
        .filter(|(_, deps)| deps.is_empty())
        .map(|(key, _)| key.clone())
        .collect();
    let mut order = Vec::new();
    while let Some(current) = ready.pop_first() {
        order.push(current.clone());
        for (key, deps) in &mut pending {
            if deps.remove(&current) && deps.is_empty() {
                ready.insert(key.clone());
            }
        }
    }

    let mut diagnostics: BTreeMap<String, Diagnostic> = BTreeMap::new();
    for (key, (_, diagnostic)) in &analyzed {
        if let Some(diagnostic) = diagnostic {
            diagnostics.insert(key.clone(), diagnostic.clone());
        }
    }
    for key in formula_keys.difference(&order.iter().cloned().collect()) {
        let (sheet, address) = split_key(key);
        diagnostics.entry(key.clone()).or_insert_with(|| {
            Diagnostic::cell(
                "cycle",
                "cycle",
                "circular formula reference",
                sheet,
                address,
                None,
            )
        });
    }

    let mut results: BTreeMap<String, Scalar> = BTreeMap::new();
    for key in &order {
        if diagnostics.contains_key(key) {
            continue;
        }
        let deps = &analyzed[key].0;
        let mut blocked: Vec<&String> = deps
            .iter()
            .filter(|dep| {
                formula_keys.contains(*dep)
                    && (diagnostics.contains_key(*dep) || !results.contains_key(*dep))
            })
            .collect();
        blocked.sort();
        if let Some(first) = blocked.first() {
            let classification = diagnostics
                .get(*first)
                .map(|item| item.classification.clone())
                .unwrap_or_else(|| "unsupported".to_string());
            let (sheet, address) = split_key(key);
            diagnostics.insert(
                key.clone(),
                Diagnostic::cell(
                    "blocked_dependency",
                    &classification,
                    format!("dependency {first} was not calculated"),
                    sheet,
                    address,
                    None,
                ),
            );
            continue;
        }
        let (sheet, address) = split_key(key);
        let expression = cells[key]
            .formula
            .as_ref()
            .map(|formula| formula.expression.as_str())
            .unwrap_or("");
        match evaluate(workbook, sheet, address, expression, &results) {
            Outcome::Diagnostic(diagnostic) => {
                diagnostics.insert(key.clone(), diagnostic);
            }
            Outcome::Value(value) => {
                results.insert(key.clone(), value);
            }
        }
    }

    Ok(with_calculation(workbook, &analyzed, &results, diagnostics))
}

fn with_calculation(
    workbook: &Workbook,
    analyzed: &BTreeMap<String, (Vec<String>, Option<Diagnostic>)>,
    results: &BTreeMap<String, Scalar>,
    diagnostics: BTreeMap<String, Diagnostic>,
) -> Workbook {
    let mut sheets = Vec::new();
    for sheet in &workbook.sheets {
        let mut cells = BTreeMap::new();
        for (address, cell) in &sheet.cells {
            let key = format!("{}!{address}", sheet.name);
            let formula = cell.formula.as_ref().map(|formula| {
                if let Some((deps, _)) = analyzed.get(&key) {
                    Formula {
                        expression: formula.expression.clone(),
                        dependencies: deps.clone(),
                        result: results.get(&key).cloned().unwrap_or(Scalar::Blank),
                    }
                } else {
                    formula.clone()
                }
            });
            cells.insert(
                address.clone(),
                Cell {
                    address: cell.address.clone(),
                    value: cell.value.clone(),
                    data_type: cell.data_type.clone(),
                    number_format: cell.number_format.clone(),
                    formula,
                    provenance: cell.provenance.clone(),
                },
            );
        }
        sheets.push(Sheet {
            name: sheet.name.clone(),
            cells,
            dimensions: sheet.dimensions,
        });
    }
    let mut ordered: Vec<Diagnostic> = diagnostics.into_values().collect();
    ordered.sort_by_key(|item| item.sort_key());
    Workbook {
        schema_version: workbook.schema_version,
        model_version: workbook.model_version,
        metadata: workbook.metadata.clone(),
        sheets,
        source_path: workbook.source_path.clone(),
        provenance: workbook.provenance.clone(),
        bindings: workbook.bindings.clone(),
        diagnostics: ordered,
    }
}

fn require_bindings(workbook: &Workbook) -> Result<(), ModelError> {
    let Some(bindings) = &workbook.bindings else {
        return Ok(());
    };
    for (kind, mapping) in [("input", &bindings.inputs), ("output", &bindings.outputs)] {
        for (name, target) in mapping {
            let cell = workbook
                .sheet(&target.sheet)
                .and_then(|sheet| sheet.cells.get(&target.address));
            let Some(cell) = cell else {
                return Err(ModelError::new(
                    "invalid_model",
                    format!("{kind} binding '{name}' does not identify a cell"),
                ));
            };
            if kind == "input" && cell.formula.is_some() {
                return Err(ModelError::new(
                    "invalid_model",
                    format!("input binding '{name}' points at a formula cell"),
                ));
            }
        }
    }
    Ok(())
}

fn analyze(
    workbook: &Workbook,
    sheet_name: &str,
    address: &str,
    expression: &str,
) -> (Vec<String>, Option<Diagnostic>) {
    let expression_parsed = match Expression::parse(expression) {
        Ok(expression) => expression,
        Err(_) => {
            return (
                Vec::new(),
                Some(Diagnostic::cell(
                    "parse_error",
                    "parse_error",
                    "formula could not be parsed",
                    sheet_name,
                    address,
                    None,
                )),
            );
        }
    };
    let references = match expression_parsed.references() {
        Ok(references) => references,
        Err(_) => {
            return (
                Vec::new(),
                Some(Diagnostic::cell(
                    "invalid_reference",
                    "invalid_reference",
                    format!("invalid reference in {expression}"),
                    sheet_name,
                    address,
                    None,
                )),
            );
        }
    };
    let mut dependencies = BTreeSet::new();
    let mut unknown_sheets = Vec::new();
    let mut oversized = false;
    for reference in references {
        let end = reference.end.as_ref().unwrap_or(&reference.start);
        let row_lo = u64::from(reference.start.row.min(end.row));
        let row_hi = u64::from(reference.start.row.max(end.row));
        let col_lo = u64::from(reference.start.column.min(end.column));
        let col_hi = u64::from(reference.start.column.max(end.column));
        let count = (row_hi - row_lo + 1) * (col_hi - col_lo + 1);
        if count > MAX_RANGE_CELLS {
            oversized = true;
            continue;
        }
        let raw_sheet = reference.start.sheet.as_deref();
        let Some(resolved) = resolve_sheet(workbook, raw_sheet, sheet_name) else {
            unknown_sheets.push(raw_sheet.unwrap_or(sheet_name).to_string());
            continue;
        };
        for row in row_lo..=row_hi {
            for col in col_lo..=col_hi {
                dependencies.insert(format!(
                    "{resolved}!{}",
                    crate::encode_ref(col as usize, row as usize)
                ));
            }
        }
    }
    if oversized {
        return (
            Vec::new(),
            Some(Diagnostic::cell(
                "resource_limit",
                "resource_limit",
                "reference range exceeds 100000 cells",
                sheet_name,
                address,
                None,
            )),
        );
    }
    if !unknown_sheets.is_empty() {
        unknown_sheets.sort();
        return (
            Vec::new(),
            Some(Diagnostic::cell(
                "invalid_reference",
                "invalid_reference",
                format!("unknown worksheet {}", unknown_sheets[0]),
                sheet_name,
                address,
                None,
            )),
        );
    }
    let unsupported = expression_parsed
        .functions()
        .into_iter()
        .find(|name| !SUPPORTED_FUNCTIONS.contains(&name.as_str()));
    let diagnostic = unsupported.map(|name| {
        Diagnostic::cell(
            "unsupported_formula",
            "unsupported",
            format!("unsupported function {name}"),
            sheet_name,
            address,
            Some(name),
        )
    });
    (dependencies.into_iter().collect(), diagnostic)
}

fn evaluate(
    workbook: &Workbook,
    sheet_name: &str,
    address: &str,
    expression: &str,
    results: &BTreeMap<String, Scalar>,
) -> Outcome {
    let mut environment = HashMap::new();
    for sheet in &workbook.sheets {
        for (cell_address, cell) in &sheet.cells {
            let key = format!("{}!{cell_address}", sheet.name);
            let scalar = if cell.formula.is_some() {
                let Some(value) = results.get(&key) else {
                    continue;
                };
                value
            } else {
                &cell.value
            };
            let engine = engine_value(scalar);
            environment.insert(format!("{}!{cell_address}", sheet.name), engine.clone());
            if sheet.name == sheet_name {
                environment.insert(cell_address.clone(), engine);
            }
        }
    }
    match evaluate_result(expression, &environment, sheet_name) {
        Ok(FormulaResult::Scalar(value)) => match value {
            Value::Number(number) => scalar_from_f64(number),
            Value::Text(text) => Outcome::Value(Scalar::Text(text)),
            Value::Bool(value) => Outcome::Value(Scalar::Boolean(value)),
            Value::Blank => Outcome::Value(Scalar::Blank),
            Value::Error(error) => from_formula_error(error, sheet_name, address),
        },
        Ok(FormulaResult::Array(_)) => Outcome::Diagnostic(Diagnostic::cell(
            "unsupported_formula",
            "unsupported",
            "array results are unsupported",
            sheet_name,
            address,
            None,
        )),
        Err(error) => from_formula_error(error, sheet_name, address),
    }
}

fn from_formula_error(error: FormulaError, sheet: &str, address: &str) -> Outcome {
    let rendered = error.to_string();
    if let Some(function) = rendered.strip_prefix("unsupported function ") {
        return Outcome::Diagnostic(Diagnostic::cell(
            "unsupported_formula",
            "unsupported",
            format!("unsupported function {function}"),
            sheet,
            address,
            Some(function.to_string()),
        ));
    }
    if let Some(code) = error.excel_code() {
        return Outcome::Value(Scalar::Error {
            code: code.to_string(),
            message: None,
        });
    }
    Outcome::Diagnostic(Diagnostic::cell(
        "parse_error",
        "parse_error",
        "formula could not be parsed",
        sheet,
        address,
        None,
    ))
}

fn scalar_from_f64(number: f64) -> Outcome {
    if !number.is_finite() {
        return Outcome::Value(Scalar::Error {
            code: "#NUM!".to_string(),
            message: None,
        });
    }
    if number.fract() == 0.0 && number.abs() <= 9_007_199_254_740_992.0 {
        return Outcome::Value(Scalar::Number(Number::from(number as i64)));
    }
    match Number::from_f64(number) {
        Some(value) => Outcome::Value(Scalar::Number(value)),
        None => Outcome::Value(Scalar::Error {
            code: "#NUM!".to_string(),
            message: None,
        }),
    }
}

fn engine_value(value: &Scalar) -> Value {
    match value {
        Scalar::Blank => Value::Blank,
        Scalar::Number(number) => Value::Number(number.as_f64().unwrap_or(0.0)),
        Scalar::Text(text) => Value::Text(text.clone()),
        Scalar::Boolean(value) => Value::Bool(*value),
        Scalar::Error { code, .. } => Value::Error(match code.as_str() {
            "#DIV/0!" => FormulaError::Div0,
            "#VALUE!" => FormulaError::Value,
            "#REF!" => FormulaError::Ref,
            "#NAME?" => FormulaError::Name,
            "#NUM!" => FormulaError::Num,
            "#N/A" => FormulaError::NA,
            "#CALC!" => FormulaError::Calc,
            other => FormulaError::Unsupported(other.to_string()),
        }),
    }
}

fn resolve_sheet<'a>(workbook: &'a Workbook, raw: Option<&str>, current: &str) -> Option<&'a str> {
    let wanted = raw.unwrap_or(current);
    workbook
        .sheets
        .iter()
        .find(|sheet| sheet.name.eq_ignore_ascii_case(wanted))
        .map(|sheet| sheet.name.as_str())
}

fn split_key(key: &str) -> (&str, &str) {
    key.rsplit_once('!').unwrap_or((key, ""))
}

fn parse_workbook(data: &Map<String, JsonValue>) -> Result<Workbook, ModelError> {
    check_fields(
        data,
        &[
            "schema_version",
            "model_version",
            "source_path",
            "metadata",
            "provenance",
            "bindings",
            "diagnostics",
            "sheets",
        ],
        &["schema_version", "model_version", "metadata", "sheets"],
        "",
    )?;
    let schema_version = version(
        &data["schema_version"],
        "schema_version",
        "unsupported_schema_version",
    )?;
    let model_version = version(
        &data["model_version"],
        "model_version",
        "unsupported_model_version",
    )?;
    let metadata = data["metadata"]
        .as_object()
        .cloned()
        .ok_or_else(|| ModelError::new("invalid_model", "metadata must be an object"))?;
    let source_path = maybe(data, "source_path", "source_path", optional_string)?.flatten();
    let provenance = maybe(data, "provenance", "provenance", parse_provenance)?;
    let bindings = maybe(data, "bindings", "bindings", |value, _path| {
        parse_bindings(value)
    })?;
    let diagnostics = match data.get("diagnostics") {
        Some(value) => parse_diagnostics(value)?,
        None => Vec::new(),
    };
    let sheets_raw = data["sheets"]
        .as_array()
        .ok_or_else(|| ModelError::new("invalid_model", "sheets must be an array"))?;
    let mut sheets = Vec::new();
    for (index, item) in sheets_raw.iter().enumerate() {
        sheets.push(parse_sheet(item, index)?);
    }
    let mut seen = BTreeSet::new();
    for sheet in &sheets {
        if !seen.insert(sheet.name.to_ascii_lowercase()) {
            return Err(ModelError::new(
                "invalid_model",
                format!("duplicate sheet name '{}'", sheet.name),
            ));
        }
    }
    Ok(Workbook {
        schema_version,
        model_version,
        metadata,
        sheets,
        source_path,
        provenance,
        bindings,
        diagnostics,
    })
}

fn parse_sheet(data: &JsonValue, index: usize) -> Result<Sheet, ModelError> {
    let path = format!("sheets[{index}]");
    let object = object(data, &path)?;
    check_fields(
        object,
        &["name", "dimensions", "cells"],
        &["name", "cells"],
        &format!("{path}."),
    )?;
    let name = required_string(&object["name"], &format!("{path}.name"))?;
    if name.contains('!') {
        return Err(ModelError::new(
            "invalid_model",
            format!("{path}.name must not contain '!'"),
        ));
    }
    let dimensions = maybe(
        object,
        "dimensions",
        &format!("{path}.dimensions"),
        parse_dimensions,
    )?;
    let cells_raw = object["cells"].as_object().ok_or_else(|| {
        ModelError::new("invalid_model", format!("{path}.cells must be an object"))
    })?;
    let mut cells = BTreeMap::new();
    let mut keys: Vec<_> = cells_raw.keys().cloned().collect();
    keys.sort();
    for address in keys {
        if !is_canonical_a1(&address) {
            return Err(ModelError::new(
                "invalid_model",
                format!("{path}.cells key '{address}' is not a canonical A1 reference"),
            ));
        }
        let cell = parse_cell(&cells_raw[&address], &format!("{path}.cells.{address}"))?;
        if cell.address != address {
            return Err(ModelError::new(
                "invalid_model",
                format!("{path}.cells.{address}.address does not match its map key"),
            ));
        }
        cells.insert(address, cell);
    }
    Ok(Sheet {
        name,
        cells,
        dimensions,
    })
}

fn parse_cell(data: &JsonValue, path: &str) -> Result<Cell, ModelError> {
    let object = object(data, path)?;
    check_fields(
        object,
        &[
            "address",
            "value",
            "data_type",
            "number_format",
            "formula",
            "provenance",
        ],
        &["address", "value", "data_type"],
        &format!("{path}."),
    )?;
    let address = required_string(&object["address"], &format!("{path}.address"))?;
    if !is_canonical_a1(&address) {
        return Err(ModelError::new(
            "invalid_model",
            format!("{path}.address is not a canonical A1 reference"),
        ));
    }
    let value = parse_value(&object["value"], &format!("{path}.value"))?;
    let data_type = object["data_type"].as_str().unwrap_or("");
    if !matches!(data_type, "blank" | "number" | "text" | "boolean" | "error") {
        return Err(ModelError::new(
            "invalid_model",
            format!("{path}.data_type is not a supported data_type"),
        ));
    }
    let data_type = data_type.to_string();
    if data_type != value_type(&value) {
        return Err(ModelError::new(
            "invalid_model",
            format!("{path}.data_type does not match the cell value"),
        ));
    }
    let number_format = maybe(
        object,
        "number_format",
        &format!("{path}.number_format"),
        optional_string,
    )?
    .flatten();
    let formula = maybe(object, "formula", &format!("{path}.formula"), parse_formula)?;
    let provenance = maybe(
        object,
        "provenance",
        &format!("{path}.provenance"),
        parse_provenance,
    )?;
    Ok(Cell {
        address,
        value,
        data_type,
        number_format,
        formula,
        provenance,
    })
}

fn parse_formula(data: &JsonValue, path: &str) -> Result<Formula, ModelError> {
    let object = object(data, path)?;
    check_fields(
        object,
        &["expression", "dependencies", "result"],
        &["expression", "dependencies", "result"],
        &format!("{path}."),
    )?;
    let expression = required_string(&object["expression"], &format!("{path}.expression"))?;
    let dependencies_raw = object["dependencies"].as_array().ok_or_else(|| {
        ModelError::new(
            "invalid_model",
            format!("{path}.dependencies must be an array of strings"),
        )
    })?;
    let mut dependencies = Vec::new();
    for (index, item) in dependencies_raw.iter().enumerate() {
        let Some(text) = item.as_str() else {
            return Err(ModelError::new(
                "invalid_model",
                format!("{path}.dependencies must be an array of strings"),
            ));
        };
        if !is_dependency(text) {
            return Err(ModelError::new(
                "invalid_model",
                format!("{path}.dependencies[{index}] is not a Sheet!A1 reference"),
            ));
        }
        dependencies.push(text.to_string());
    }
    let result = parse_value(&object["result"], &format!("{path}.result"))?;
    Ok(Formula {
        expression,
        dependencies,
        result,
    })
}

fn parse_value(data: &JsonValue, path: &str) -> Result<Scalar, ModelError> {
    match data {
        JsonValue::Null => Ok(Scalar::Blank),
        JsonValue::Bool(value) => Ok(Scalar::Boolean(*value)),
        JsonValue::Number(number) => {
            if number.as_f64().is_some_and(|value| !value.is_finite()) {
                return Err(ModelError::new(
                    "invalid_model",
                    format!("{path} must be a finite number"),
                ));
            }
            Ok(Scalar::Number(number.clone()))
        }
        JsonValue::String(value) => Ok(Scalar::Text(value.clone())),
        JsonValue::Array(_) => Err(ModelError::new(
            "invalid_model",
            format!("{path} must be null, a number, a string, a boolean, or an error"),
        )),
        JsonValue::Object(object) => {
            check_fields(
                object,
                &["error", "message"],
                &["error", "message"],
                &format!("{path}."),
            )?;
            let code = required_string(&object["error"], &format!("{path}.error"))?;
            if !ERROR_CODES.contains(&code.as_str()) {
                return Err(ModelError::new(
                    "invalid_model",
                    format!("{path}.error is not a supported error"),
                ));
            }
            let message = match &object["message"] {
                JsonValue::Null => None,
                JsonValue::String(value) => Some(value.clone()),
                _ => {
                    return Err(ModelError::new(
                        "invalid_model",
                        format!("{path}.message must be a string or null"),
                    ));
                }
            };
            Ok(Scalar::Error { code, message })
        }
    }
}

fn value_type(value: &Scalar) -> &'static str {
    match value {
        Scalar::Blank => "blank",
        Scalar::Number(_) => "number",
        Scalar::Text(_) => "text",
        Scalar::Boolean(_) => "boolean",
        Scalar::Error { .. } => "error",
    }
}

fn parse_provenance(data: &JsonValue, path: &str) -> Result<Provenance, ModelError> {
    let object = object(data, path)?;
    check_fields(
        object,
        &["origin", "source"],
        &["origin"],
        &format!("{path}."),
    )?;
    let origin = required_string(&object["origin"], &format!("{path}.origin"))?;
    if !matches!(origin.as_str(), "authored" | "imported" | "calculated") {
        return Err(ModelError::new(
            "invalid_model",
            format!("{path}.origin is not a supported origin"),
        ));
    }
    let source = maybe(object, "source", &format!("{path}.source"), optional_string)?.flatten();
    Ok(Provenance { origin, source })
}

fn parse_bindings(data: &JsonValue) -> Result<Bindings, ModelError> {
    let object = object(data, "bindings")?;
    check_fields(
        object,
        &["inputs", "outputs"],
        &["inputs", "outputs"],
        "bindings.",
    )?;
    Ok(Bindings {
        inputs: parse_binding_map(&object["inputs"], "bindings.inputs")?,
        outputs: parse_binding_map(&object["outputs"], "bindings.outputs")?,
    })
}

fn parse_binding_map(data: &JsonValue, path: &str) -> Result<BTreeMap<String, CellId>, ModelError> {
    let mapping = data
        .as_object()
        .ok_or_else(|| ModelError::new("invalid_model", format!("{path} must be an object")))?;
    let mut mapped = BTreeMap::new();
    let mut names: Vec<_> = mapping.keys().cloned().collect();
    names.sort();
    for name in names {
        if name.is_empty() {
            return Err(ModelError::new(
                "invalid_model",
                "binding name must not be empty",
            ));
        }
        let item_path = format!("{path}.{name}");
        let item = object(mapping.get(&name).unwrap_or(&JsonValue::Null), &item_path)?;
        check_fields(
            item,
            &["sheet", "address"],
            &["sheet", "address"],
            &format!("{item_path}."),
        )?;
        let sheet = required_string(&item["sheet"], &format!("{item_path}.sheet"))?;
        let address = required_string(&item["address"], &format!("{item_path}.address"))?;
        if !is_canonical_a1(&address) {
            return Err(ModelError::new(
                "invalid_model",
                format!("{item_path}.address is not a canonical A1 reference"),
            ));
        }
        mapped.insert(name, CellId { sheet, address });
    }
    Ok(mapped)
}

fn parse_diagnostics(data: &JsonValue) -> Result<Vec<Diagnostic>, ModelError> {
    let items = data
        .as_array()
        .ok_or_else(|| ModelError::new("invalid_model", "diagnostics must be an array"))?;
    let mut diagnostics = Vec::new();
    for (index, item) in items.iter().enumerate() {
        let path = format!("diagnostics[{index}]");
        let object = object(item, &path)?;
        check_fields(
            object,
            &[
                "code",
                "classification",
                "message",
                "sheet",
                "address",
                "function",
            ],
            &["code", "classification", "message"],
            &format!("{path}."),
        )?;
        let code = required_string(&object["code"], &format!("{path}.code"))?;
        let classification =
            required_string(&object["classification"], &format!("{path}.classification"))?;
        let message = object["message"].as_str().ok_or_else(|| {
            ModelError::new("invalid_model", format!("{path}.message must be a string"))
        })?;
        if !matches!(
            code.as_str(),
            "unsupported_formula"
                | "parse_error"
                | "cycle"
                | "resource_limit"
                | "invalid_reference"
                | "blocked_dependency"
        ) {
            return Err(ModelError::new(
                "invalid_model",
                format!("{path}.code is not a supported diagnostic"),
            ));
        }
        if !matches!(
            classification.as_str(),
            "unsupported" | "parse_error" | "cycle" | "resource_limit" | "invalid_reference"
        ) {
            return Err(ModelError::new(
                "invalid_model",
                format!("{path}.classification is not a supported classification"),
            ));
        }
        let sheet = optional_present_string(object, "sheet", &path)?;
        let address = optional_present_string(object, "address", &path)?;
        if let Some(address) = &address
            && !is_canonical_a1(address)
        {
            return Err(ModelError::new(
                "invalid_model",
                format!("{path}.address is not a canonical A1 reference"),
            ));
        }
        let function = optional_present_string(object, "function", &path)?;
        diagnostics.push(Diagnostic {
            code,
            classification,
            message: message.to_string(),
            sheet,
            address,
            function,
        });
    }
    Ok(diagnostics)
}

fn parse_dimensions(data: &JsonValue, path: &str) -> Result<[u64; 4], ModelError> {
    let Some(items) = data.as_array() else {
        return Err(ModelError::new(
            "invalid_model",
            format!("{path} must be four positive integers"),
        ));
    };
    if items.len() != 4 {
        return Err(ModelError::new(
            "invalid_model",
            format!("{path} must be four positive integers"),
        ));
    }
    let mut values = [0_u64; 4];
    for (index, item) in items.iter().enumerate() {
        let Some(number) = item.as_u64() else {
            return Err(ModelError::new(
                "invalid_model",
                format!("{path} must be four positive integers"),
            ));
        };
        if number < 1 {
            return Err(ModelError::new(
                "invalid_model",
                format!("{path} must be four positive integers"),
            ));
        }
        values[index] = number;
    }
    if values[0] > values[1]
        || values[2] > values[3]
        || values[1] > MAX_ROW
        || values[3] > MAX_COLUMN
    {
        return Err(ModelError::new(
            "invalid_model",
            format!("{path} is out of order"),
        ));
    }
    Ok(values)
}

fn version(value: &JsonValue, path: &str, unsupported: &str) -> Result<i64, ModelError> {
    let Some(number) = value.as_i64() else {
        return Err(ModelError::new(
            "invalid_model",
            format!("{path} must be an integer"),
        ));
    };
    if number != 1 {
        return Err(ModelError::new(unsupported, number.to_string()));
    }
    Ok(number)
}

fn check_fields(
    data: &Map<String, JsonValue>,
    allowed: &[&str],
    required: &[&str],
    prefix: &str,
) -> Result<(), ModelError> {
    for key in required {
        if !data.contains_key(*key) {
            return Err(ModelError::new(
                "missing_required_field",
                format!("{prefix}{key}"),
            ));
        }
    }
    let mut keys: Vec<_> = data.keys().cloned().collect();
    keys.sort();
    for key in keys {
        if !allowed.contains(&key.as_str()) {
            return Err(ModelError::new("unknown_field", format!("{prefix}{key}")));
        }
    }
    Ok(())
}

fn object<'a>(data: &'a JsonValue, path: &str) -> Result<&'a Map<String, JsonValue>, ModelError> {
    data.as_object()
        .ok_or_else(|| ModelError::new("invalid_model", format!("{path} must be an object")))
}

fn required_string(value: &JsonValue, path: &str) -> Result<String, ModelError> {
    match value.as_str() {
        Some(text) if !text.is_empty() => Ok(text.to_string()),
        _ => Err(ModelError::new(
            "invalid_model",
            format!("{path} must be a non-empty string"),
        )),
    }
}

fn optional_string(value: &JsonValue, path: &str) -> Result<Option<String>, ModelError> {
    match value {
        JsonValue::Null => Ok(None),
        JsonValue::String(text) => Ok(Some(text.clone())),
        _ => Err(ModelError::new(
            "invalid_model",
            format!("{path} must be a string or null"),
        )),
    }
}

fn optional_present_string(
    data: &Map<String, JsonValue>,
    key: &str,
    path: &str,
) -> Result<Option<String>, ModelError> {
    let Some(value) = data.get(key) else {
        return Ok(None);
    };
    match value.as_str() {
        Some(text) if !text.is_empty() => Ok(Some(text.to_string())),
        _ => Err(ModelError::new(
            "invalid_model",
            format!("{path}.{key} must be a non-empty string"),
        )),
    }
}

fn maybe<T>(
    data: &Map<String, JsonValue>,
    key: &str,
    path: &str,
    parse: impl FnOnce(&JsonValue, &str) -> Result<T, ModelError>,
) -> Result<Option<T>, ModelError> {
    match data.get(key) {
        Some(value) => Ok(Some(parse(value, path)?)),
        None => Ok(None),
    }
}

fn is_canonical_a1(address: &str) -> bool {
    let bytes = address.as_bytes();
    let mut index = 0;
    while index < bytes.len() && bytes[index].is_ascii_uppercase() {
        index += 1;
    }
    if index == 0 || index > 3 || index == bytes.len() || bytes[index] == b'0' {
        return false;
    }
    if !bytes[index..].iter().all(|byte| byte.is_ascii_digit()) || bytes.len() - index > 7 {
        return false;
    }
    match crate::decode_ref(address) {
        Ok((column, row)) => {
            column >= 1 && column as u64 <= MAX_COLUMN && row >= 1 && row as u64 <= MAX_ROW
        }
        Err(_) => false,
    }
}

fn is_dependency(value: &str) -> bool {
    let Some((sheet, address)) = value.rsplit_once('!') else {
        return false;
    };
    !sheet.is_empty() && !sheet.contains('!') && is_canonical_a1(address)
}

fn workbook_json(workbook: &Workbook) -> JsonValue {
    let mut data = Map::new();
    data.insert(
        "schema_version".into(),
        JsonValue::from(workbook.schema_version),
    );
    data.insert(
        "model_version".into(),
        JsonValue::from(workbook.model_version),
    );
    data.insert(
        "metadata".into(),
        JsonValue::Object(workbook.metadata.clone()),
    );
    data.insert(
        "sheets".into(),
        JsonValue::Array(workbook.sheets.iter().map(sheet_json).collect()),
    );
    if let Some(source_path) = &workbook.source_path {
        data.insert("source_path".into(), JsonValue::String(source_path.clone()));
    }
    if let Some(provenance) = &workbook.provenance {
        data.insert("provenance".into(), provenance_json(provenance));
    }
    if let Some(bindings) = &workbook.bindings {
        data.insert("bindings".into(), bindings_json(bindings));
    }
    if !workbook.diagnostics.is_empty() {
        data.insert(
            "diagnostics".into(),
            JsonValue::Array(workbook.diagnostics.iter().map(diagnostic_json).collect()),
        );
    }
    JsonValue::Object(data)
}

fn sheet_json(sheet: &Sheet) -> JsonValue {
    let mut cells = Map::new();
    for (address, cell) in &sheet.cells {
        cells.insert(address.clone(), cell_json(cell));
    }
    let mut data = Map::new();
    data.insert("name".into(), JsonValue::String(sheet.name.clone()));
    data.insert("cells".into(), JsonValue::Object(cells));
    if let Some(dimensions) = sheet.dimensions {
        data.insert(
            "dimensions".into(),
            JsonValue::Array(dimensions.into_iter().map(JsonValue::from).collect()),
        );
    }
    JsonValue::Object(data)
}

fn cell_json(cell: &Cell) -> JsonValue {
    let mut data = Map::new();
    data.insert("address".into(), JsonValue::String(cell.address.clone()));
    data.insert("value".into(), scalar_json(&cell.value));
    data.insert(
        "data_type".into(),
        JsonValue::String(cell.data_type.clone()),
    );
    if let Some(number_format) = &cell.number_format {
        data.insert(
            "number_format".into(),
            JsonValue::String(number_format.clone()),
        );
    }
    if let Some(formula) = &cell.formula {
        data.insert("formula".into(), formula_json(formula));
    }
    if let Some(provenance) = &cell.provenance {
        data.insert("provenance".into(), provenance_json(provenance));
    }
    JsonValue::Object(data)
}

fn formula_json(formula: &Formula) -> JsonValue {
    json!({
        "expression": formula.expression,
        "dependencies": formula.dependencies,
        "result": scalar_json(&formula.result),
    })
}

fn scalar_json(value: &Scalar) -> JsonValue {
    match value {
        Scalar::Blank => JsonValue::Null,
        Scalar::Number(number) => JsonValue::Number(number.clone()),
        Scalar::Text(text) => JsonValue::String(text.clone()),
        Scalar::Boolean(value) => JsonValue::Bool(*value),
        Scalar::Error { code, message } => json!({
            "error": code,
            "message": message,
        }),
    }
}

fn provenance_json(provenance: &Provenance) -> JsonValue {
    let mut data = Map::new();
    data.insert(
        "origin".into(),
        JsonValue::String(provenance.origin.clone()),
    );
    if let Some(source) = &provenance.source {
        data.insert("source".into(), JsonValue::String(source.clone()));
    }
    JsonValue::Object(data)
}

fn bindings_json(bindings: &Bindings) -> JsonValue {
    json!({
        "inputs": binding_map_json(&bindings.inputs),
        "outputs": binding_map_json(&bindings.outputs),
    })
}

fn binding_map_json(mapping: &BTreeMap<String, CellId>) -> JsonValue {
    let mut data = Map::new();
    for (name, target) in mapping {
        data.insert(
            name.clone(),
            json!({"sheet": target.sheet, "address": target.address}),
        );
    }
    JsonValue::Object(data)
}

fn diagnostic_json(diagnostic: &Diagnostic) -> JsonValue {
    let mut data = Map::new();
    data.insert("code".into(), JsonValue::String(diagnostic.code.clone()));
    data.insert(
        "classification".into(),
        JsonValue::String(diagnostic.classification.clone()),
    );
    data.insert(
        "message".into(),
        JsonValue::String(diagnostic.message.clone()),
    );
    if let Some(sheet) = &diagnostic.sheet {
        data.insert("sheet".into(), JsonValue::String(sheet.clone()));
    }
    if let Some(address) = &diagnostic.address {
        data.insert("address".into(), JsonValue::String(address.clone()));
    }
    if let Some(function) = &diagnostic.function {
        data.insert("function".into(), JsonValue::String(function.clone()));
    }
    JsonValue::Object(data)
}

fn write_canonical(value: &JsonValue) -> String {
    let mut out = String::new();
    write_canonical_into(&mut out, value);
    out
}

fn write_canonical_into(out: &mut String, value: &JsonValue) {
    match value {
        JsonValue::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                write_canonical_into(out, item);
            }
            out.push(']');
        }
        JsonValue::Object(map) => {
            out.push('{');
            let mut keys: Vec<_> = map.keys().collect();
            keys.sort();
            for (index, key) in keys.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                out.push_str(&serde_json::to_string(key).expect("json key"));
                out.push(':');
                write_canonical_into(out, &map[*key]);
            }
            out.push('}');
        }
        other => out.push_str(&serde_json::to_string(other).expect("json leaf")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> Vec<u8> {
        std::fs::read(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../fixtures/operating-scenario.workbook.json"),
        )
        .expect("fixture")
    }

    fn result_number(workbook: &Workbook, sheet: &str, address: &str) -> f64 {
        let formula = workbook
            .sheet(sheet)
            .unwrap()
            .cells
            .get(address)
            .unwrap()
            .formula
            .as_ref()
            .unwrap();
        match &formula.result {
            Scalar::Number(number) => number.as_f64().unwrap(),
            other => panic!("expected number at {sheet}!{address}, got {other:?}"),
        }
    }

    #[test]
    fn fixture_round_trip_preserves_versions_and_does_not_calculate() {
        let workbook = hydrate(&fixture()).unwrap();
        assert_eq!(workbook.schema_version, 1);
        assert_eq!(workbook.model_version, 1);
        assert!(workbook.diagnostics.is_empty());
        let revenue = workbook.sheet("Forecast").unwrap().cells.get("F2").unwrap();
        assert!(matches!(
            revenue.formula.as_ref().unwrap().result,
            Scalar::Blank
        ));
        let again = hydrate(workbook.to_json().as_bytes()).unwrap();
        assert_eq!(again.to_json(), workbook.to_json());
    }

    fn hydrate_edited(edit: impl FnOnce(&mut Map<String, JsonValue>)) -> ModelError {
        let mut document: JsonValue = serde_json::from_slice(&fixture()).unwrap();
        edit(document.as_object_mut().unwrap());
        hydrate(&serde_json::to_vec(&document).unwrap()).unwrap_err()
    }

    #[test]
    fn rejects_missing_and_unsupported_versions() {
        let error = hydrate_edited(|object| {
            object.remove("model_version");
        });
        assert_eq!(error.code, "missing_required_field");
        assert_eq!(error.message, "model_version");

        let error = hydrate_edited(|object| {
            object.insert("model_version".into(), JsonValue::from(2));
        });
        assert_eq!(error.code, "unsupported_model_version");
        assert_eq!(error.message, "2");

        let error = hydrate_edited(|object| {
            object.insert("schema_version".into(), JsonValue::from(9));
        });
        assert_eq!(error.code, "unsupported_schema_version");

        let error = hydrate_edited(|object| {
            object.insert("revision".into(), JsonValue::from(1));
        });
        assert_eq!(error.code, "unknown_field");
        assert_eq!(error.message, "revision");
    }

    #[test]
    fn operating_scenario_values_errors_and_classifications() {
        let calculated = calculate(&hydrate(&fixture()).unwrap()).unwrap();
        assert_eq!(calculated.schema_version, 1);
        assert_eq!(calculated.model_version, 1);
        assert_eq!(result_number(&calculated, "Forecast", "F2"), 7400.0);
        assert_eq!(result_number(&calculated, "Forecast", "F3"), 1440.0);
        assert_eq!(result_number(&calculated, "Forecast", "F4"), 1000.0 / 12.0);
        let division = calculated
            .sheet("Forecast")
            .unwrap()
            .cells
            .get("F5")
            .unwrap()
            .formula
            .as_ref()
            .unwrap();
        assert!(matches!(
            &division.result,
            Scalar::Error { code, message: None } if code == "#DIV/0!"
        ));
        assert_eq!(
            calculated
                .sheet("Forecast")
                .unwrap()
                .cells
                .get("F2")
                .unwrap()
                .formula
                .as_ref()
                .unwrap()
                .dependencies,
            vec![
                "Forecast!C2".to_string(),
                "Forecast!C3".to_string(),
                "Forecast!C4".to_string()
            ]
        );
        assert_eq!(
            calculated
                .sheet("Forecast")
                .unwrap()
                .cells
                .get("C2")
                .unwrap()
                .formula
                .as_ref()
                .unwrap()
                .dependencies,
            vec!["Assumptions!B1".to_string(), "Forecast!B2".to_string()]
        );
        let codes: Vec<_> = calculated
            .diagnostics
            .iter()
            .map(|item| {
                (
                    item.address.as_deref().unwrap(),
                    item.code.as_str(),
                    item.classification.as_str(),
                )
            })
            .collect();
        assert_eq!(
            codes,
            vec![
                ("J1", "unsupported_formula", "unsupported"),
                ("J2", "parse_error", "parse_error"),
            ]
        );
        assert_eq!(calculated.diagnostics[0].function.as_deref(), Some("NOW"));
        let again = calculate(&calculated).unwrap();
        assert_eq!(again.to_json(), calculated.to_json());
    }
}
