//! Typed authoring, bounded dependency calculation, and transient editing sessions.
//!
//! Formulas retain their source text. This module reuses the independent evaluator
//! and never substitutes an imported cached value for a calculated dependency.
use crate::{Expr, FormulaError, FormulaResult, Parser, Value, evaluate_result};
use serde::{Deserialize, Deserializer, Serialize};
use serde_json::{Value as JsonValue, json};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fmt;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};

pub const MAX_WORKBOOK_CELLS: usize = 100_000;
pub const MAX_DEPENDENCIES: usize = 100_000;
pub const MAX_EDIT_BATCH: usize = 10_000;
pub const MAX_WORKERS: usize = 8;
/// Scalar cells and array elements in each of the values and output maps.
/// Output aliases share a separate equal budget, so total retained elements are
/// bounded by twice this value; at most MAX_WORKERS arrays are in flight.
pub const MAX_RESULT_CELLS: usize = 100_000;
/// Catalog parity is checked by the regression suite.
pub const SUPPORTED_FUNCTIONS: &[&str] = &[
    "ABS",
    "AMORDEGRC",
    "AMORLINC",
    "AND",
    "AVERAGE",
    "AVERAGEIF",
    "AVERAGEIFS",
    "COMBIN",
    "COMBINA",
    "CONCAT",
    "COUNT",
    "COUNTA",
    "COUNTBLANK",
    "COUNTIF",
    "COUNTIFS",
    "COUPDAYBS",
    "COUPDAYS",
    "COUPDAYSNC",
    "COUPNCD",
    "COUPNUM",
    "COUPPCD",
    "CUMIPMT",
    "CUMPRINC",
    "DATE",
    "DAY",
    "DAYS",
    "DAYS360",
    "DB",
    "DDB",
    "EDATE",
    "EOMONTH",
    "EVEN",
    "FACT",
    "FACTDOUBLE",
    "FILTER",
    "FIND",
    "FV",
    "GCD",
    "HOUR",
    "IF",
    "IFERROR",
    "IFNA",
    "IFS",
    "INDEX",
    "INT",
    "IPMT",
    "ISBLANK",
    "ISERR",
    "ISERROR",
    "ISEVEN",
    "ISLOGICAL",
    "ISNA",
    "ISNUMBER",
    "ISODD",
    "ISOWEEKNUM",
    "ISTEXT",
    "LCM",
    "LEFT",
    "LEN",
    "LOWER",
    "MATCH",
    "MAX",
    "MAXIFS",
    "MID",
    "MIN",
    "MINIFS",
    "MINUTE",
    "MOD",
    "MONTH",
    "NA",
    "NETWORKDAYS",
    "NETWORKDAYS.INTL",
    "NOT",
    "NPER",
    "ODD",
    "OR",
    "PERMUT",
    "PERMUTATIONA",
    "PMT",
    "PPMT",
    "PV",
    "QUOTIENT",
    "RIGHT",
    "ROUND",
    "ROUNDDOWN",
    "ROUNDUP",
    "SEARCH",
    "SECOND",
    "SEQUENCE",
    "SLN",
    "SORT",
    "SUBSTITUTE",
    "SUM",
    "SUMIF",
    "SUMIFS",
    "SWITCH",
    "SYD",
    "TEXTAFTER",
    "TEXTBEFORE",
    "TEXTJOIN",
    "TIME",
    "TRIM",
    "TRUNC",
    "UNIQUE",
    "UPPER",
    "VDB",
    "VLOOKUP",
    "WEEKDAY",
    "WEEKNUM",
    "WORKDAY",
    "WORKDAY.INTL",
    "XLOOKUP",
    "XMATCH",
    "YEAR",
    "YEARFRAC",
];

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum CellValue {
    Number(f64),
    Text(String),
    Boolean(bool),
    Error {
        error: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        message: Option<String>,
    },
    #[default]
    Blank,
}
impl CellValue {
    fn validate(&self) -> Result<(), ToolkitError> {
        match self {
            Self::Number(n) if !n.is_finite() => {
                Err(ToolkitError::new("invalid_value", "numbers must be finite"))
            }
            Self::Text(text) if text.encode_utf16().count() > crate::MAX_TEXT_LENGTH_UNITS => {
                Err(ToolkitError::new(
                    "resource_limit",
                    "cell text exceeds 32767 UTF-16 code units",
                ))
            }
            Self::Error { error, .. }
                if !matches!(
                    error.as_str(),
                    "#VALUE!"
                        | "#DIV/0!"
                        | "#REF!"
                        | "#NAME?"
                        | "#NUM!"
                        | "#N/A"
                        | "#N/A!"
                        | "#CALC!"
                        | "#NULL!"
                        | "#SPILL!"
                ) =>
            {
                Err(ToolkitError::new(
                    "invalid_value",
                    "unknown Excel error code",
                ))
            }
            _ => Ok(()),
        }
    }
    fn to_engine(&self) -> Value {
        match self {
            Self::Number(n) => Value::Number(*n),
            Self::Text(s) => Value::Text(s.clone()),
            Self::Boolean(b) => Value::Bool(*b),
            Self::Blank => Value::Blank,
            Self::Error { error, .. } => Value::Error(match error.as_str() {
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
    fn from_engine(value: &Value) -> Self {
        match value {
            Value::Number(n) => Self::Number(*n),
            Value::Text(s) => Self::Text(s.clone()),
            Value::Bool(b) => Self::Boolean(*b),
            Value::Blank => Self::Blank,
            Value::Error(error) => Self::Error {
                error: error.to_string(),
                message: None,
            },
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum CalculatedValue {
    Scalar(CellValue),
    Array { rows: Vec<Vec<CellValue>> },
}
impl CalculatedValue {
    fn from_result(value: FormulaResult) -> Self {
        match value {
            FormulaResult::Scalar(value) => Self::Scalar(CellValue::from_engine(&value)),
            FormulaResult::Array(array) => Self::Array {
                rows: array
                    .rows()
                    .iter()
                    .map(|row| row.iter().map(CellValue::from_engine).collect())
                    .collect(),
            },
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CellAddress {
    pub sheet: String,
    pub address: String,
}
impl CellAddress {
    pub fn new(sheet: impl Into<String>, address: impl Into<String>) -> Self {
        Self {
            sheet: sheet.into(),
            address: address.into(),
        }
    }
}
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Style {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub number_format: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bold: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub font_color: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fill_color: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub horizontal: Option<String>,
}
impl Style {
    fn validate(&self) -> Result<(), ToolkitError> {
        if self
            .horizontal
            .as_deref()
            .is_some_and(|a| !matches!(a, "left" | "center" | "right"))
        {
            return Err(ToolkitError::new(
                "invalid_style",
                "horizontal alignment must be left, center or right",
            ));
        }
        for color in [&self.font_color, &self.fill_color].into_iter().flatten() {
            if !matches!(color.len(), 6 | 8) || !color.bytes().all(|b| b.is_ascii_hexdigit()) {
                return Err(ToolkitError::new(
                    "invalid_style",
                    "colors must be six- or eight-digit hex",
                ));
            }
        }
        if self.number_format.as_ref().is_some_and(|s| s.len() > 1024) {
            return Err(ToolkitError::new(
                "resource_limit",
                "number format exceeds 1024 bytes",
            ));
        }
        Ok(())
    }
}
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Cell {
    pub value: CellValue,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub formula: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cached_value: Option<CellValue>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub style: Option<Style>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub blocked_reason: Option<String>,
}
impl Cell {
    pub fn value(value: CellValue) -> Self {
        Self {
            value,
            ..Self::default()
        }
    }
    pub fn formula(formula: impl Into<String>) -> Self {
        Self {
            formula: Some(formula.into()),
            ..Self::default()
        }
    }
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Sheet {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub cells: BTreeMap<String, Cell>,
    #[serde(default)]
    pub column_widths: BTreeMap<String, f64>,
}
impl Sheet {
    pub fn new(name: impl Into<String>) -> Self {
        let name = name.into();
        Self {
            id: name.clone(),
            name,
            cells: BTreeMap::new(),
            column_widths: BTreeMap::new(),
        }
    }
}
fn default_true() -> bool {
    true
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InputBinding {
    pub sheet: String,
    pub address: String,
    pub kind: String,
    #[serde(default = "default_true")]
    pub required: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub min: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub choices: Option<Vec<CellValue>>,
}
impl InputBinding {
    pub fn number(
        sheet: impl Into<String>,
        address: impl Into<String>,
        min: Option<f64>,
        max: Option<f64>,
    ) -> Self {
        Self {
            sheet: sheet.into(),
            address: address.into(),
            kind: "number".into(),
            required: true,
            min,
            max,
            choices: None,
        }
    }
}
/// A document with no `schema_version` is not a version 1 document, and there
/// is no compatibility window that reads it as one (docs/specs/model-versioning.md).
/// Serde fills the field with a value `validate` refuses, so every entry point
/// reports the typed `schema_version` diagnostic instead of a serde message.
fn missing_schema_version() -> u32 {
    0
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkbookModel {
    #[serde(default = "missing_schema_version")]
    pub schema_version: u32,
    #[serde(default)]
    pub revision: u64,
    pub sheets: Vec<Sheet>,
    #[serde(default)]
    pub inputs: BTreeMap<String, InputBinding>,
    #[serde(default)]
    pub outputs: BTreeMap<String, CellAddress>,
}
impl Default for WorkbookModel {
    fn default() -> Self {
        Self {
            schema_version: 1,
            revision: 0,
            sheets: Vec::new(),
            inputs: BTreeMap::new(),
            outputs: BTreeMap::new(),
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ToolkitError {
    pub code: String,
    pub message: String,
}
impl ToolkitError {
    pub fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }
}
impl fmt::Display for ToolkitError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}
impl std::error::Error for ToolkitError {}
impl From<FormulaError> for ToolkitError {
    fn from(error: FormulaError) -> Self {
        let code = match error {
            FormulaError::Parse(_) => "parse_error",
            FormulaError::Unsupported(_) => "unsupported_formula",
            _ => "invalid_formula",
        };
        Self::new(code, error.to_string())
    }
}

/// A parsed A1 reference; one-based coordinates keep Excel boundaries explicit.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct CellReference {
    pub sheet: Option<String>,
    pub row: u32,
    pub column: u32,
    pub absolute_row: bool,
    pub absolute_column: bool,
}
impl CellReference {
    pub fn parse(sheet: Option<String>, address: &str) -> Result<Self, ToolkitError> {
        let mut chars = address.chars().peekable();
        let absolute_column = chars.next_if_eq(&'$').is_some();
        let mut column = String::new();
        while chars.peek().is_some_and(char::is_ascii_alphabetic) {
            column.push(chars.next().unwrap().to_ascii_uppercase());
        }
        let absolute_row = chars.next_if_eq(&'$').is_some();
        let row: String = chars.collect();
        if column.is_empty()
            || row.is_empty()
            || !row.bytes().all(|c| c.is_ascii_digit())
            || row.starts_with('0')
        {
            return Err(ToolkitError::new(
                "invalid_reference",
                format!("invalid A1 reference: {address}"),
            ));
        }
        let (c, r) = crate::decode_ref(&format!("{column}{row}"))
            .map_err(|_| ToolkitError::new("invalid_reference", address))?;
        if c > 16384 || r > 1_048_576 {
            return Err(ToolkitError::new(
                "invalid_reference",
                format!("reference outside Excel grid: {address}"),
            ));
        }
        Ok(Self {
            sheet,
            row: r as u32,
            column: c as u32,
            absolute_row,
            absolute_column,
        })
    }
    fn address(&self) -> String {
        let base = crate::encode_ref(self.column as usize, self.row as usize);
        let split = base.find(|c: char| c.is_ascii_digit()).unwrap();
        format!(
            "{}{}{}{}",
            if self.absolute_column { "$" } else { "" },
            &base[..split],
            if self.absolute_row { "$" } else { "" },
            self.row
        )
    }
    pub fn render(&self) -> String {
        match &self.sheet {
            Some(sheet) => format!("{}!{}", quote_sheet(sheet), self.address()),
            None => self.address(),
        }
    }
    fn shifted(&self, row_delta: i32, column_delta: i32) -> Result<Self, ToolkitError> {
        let mut result = self.clone();
        let row = i64::from(self.row)
            + if self.absolute_row {
                0
            } else {
                i64::from(row_delta)
            };
        let col = i64::from(self.column)
            + if self.absolute_column {
                0
            } else {
                i64::from(column_delta)
            };
        if !(1..=1_048_576).contains(&row) || !(1..=16384).contains(&col) {
            return Err(ToolkitError::new(
                "invalid_reference",
                "formula copy would leave the Excel grid",
            ));
        }
        result.row = row as u32;
        result.column = col as u32;
        Ok(result)
    }
}
fn quote_sheet(sheet: &str) -> String {
    format!("'{}'", sheet.replace('\'', "''"))
}
fn parse_expr(formula: &str) -> Result<Expr, ToolkitError> {
    let mut parser = Parser::new(formula)?;
    let expr = parser.parse_expression(0)?;
    if parser.peek().is_some() {
        return Err(ToolkitError::new(
            "parse_error",
            "unexpected trailing formula input",
        ));
    }
    validate_expression_depth(&expr)?;
    Ok(expr)
}
fn validate_expression_depth(expr: &Expr) -> Result<(), ToolkitError> {
    // The root is level 1, as in the Python expression validator, so a
    // 96-term chain is the deepest tree either engine accepts.
    let mut pending = vec![(expr, 1)];
    while let Some((e, depth)) = pending.pop() {
        if depth > crate::MAX_EXPRESSION_NESTING {
            return Err(ToolkitError::new(
                "resource_limit",
                "toolkit expression tree exceeds 96 levels",
            ));
        }
        match e {
            Expr::Unary(_, child) => pending.push((child, depth + 1)),
            Expr::Binary(_, left, right) => {
                pending.push((left, depth + 1));
                pending.push((right, depth + 1));
            }
            Expr::Call(_, args) => pending.extend(args.iter().map(|arg| (arg, depth + 1))),
            _ => {}
        }
    }
    Ok(())
}
/// A typed expression uses the same parser as the independent evaluator.
#[derive(Clone, Debug, PartialEq)]
pub struct Expression {
    inner: Expr,
}
impl Expression {
    pub fn parse(formula: &str) -> Result<Self, ToolkitError> {
        Ok(Self {
            inner: parse_expr(formula)?,
        })
    }
    pub fn number(value: f64) -> Result<Self, ToolkitError> {
        if !value.is_finite() {
            return Err(ToolkitError::new(
                "invalid_value",
                "expression numbers must be finite",
            ));
        }
        Ok(Self {
            inner: Expr::Number(value, value.to_string()),
        })
    }
    pub fn text(value: impl Into<String>) -> Self {
        Self {
            inner: Expr::Text(value.into()),
        }
    }
    pub fn boolean(value: bool) -> Self {
        Self {
            inner: Expr::Bool(value),
        }
    }
    pub fn missing() -> Self {
        Self {
            inner: Expr::Missing,
        }
    }
    pub fn reference(reference: CellReference) -> Result<Self, ToolkitError> {
        Self::parse(&reference.render())
    }
    pub fn range(start: CellReference, end: CellReference) -> Result<Self, ToolkitError> {
        Self::parse(&format!("{}:{}", start.render(), end.render()))
    }
    pub fn binary(operator: &str, left: Self, right: Self) -> Result<Self, ToolkitError> {
        if !matches!(
            operator,
            "+" | "-" | "*" | "/" | "^" | "&" | "=" | "<>" | "<" | "<=" | ">" | ">="
        ) {
            return Err(ToolkitError::new(
                "invalid_expression",
                "unknown binary operator",
            ));
        }
        let expression = Self {
            inner: Expr::Binary(operator.into(), Box::new(left.inner), Box::new(right.inner)),
        };
        validate_expression_depth(&expression.inner)?;
        Ok(expression)
    }
    pub fn call(name: &str, arguments: Vec<Self>) -> Result<Self, ToolkitError> {
        if name.is_empty()
            || !name
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'.'))
            || name.starts_with(|c: char| c.is_ascii_digit())
        {
            return Err(ToolkitError::new(
                "invalid_expression",
                "invalid function name",
            ));
        }
        let expression = Self {
            inner: Expr::Call(
                name.to_ascii_uppercase(),
                arguments.into_iter().map(|arg| arg.inner).collect(),
            ),
        };
        validate_expression_depth(&expression.inner)?;
        Ok(expression)
    }
    pub fn render(&self) -> Result<String, ToolkitError> {
        let rendered = format!("={}", render_expr(&self.inner, 0, 0)?);
        if rendered.encode_utf16().count() > crate::MAX_FORMULA_LENGTH_UNITS + 1 {
            return Err(ToolkitError::new(
                "resource_limit",
                "rendered formula exceeds 8192 UTF-16 code units",
            ));
        }
        Ok(rendered)
    }
    pub fn functions(&self) -> BTreeSet<String> {
        let mut pending = vec![&self.inner];
        let mut functions = BTreeSet::new();
        while let Some(expr) = pending.pop() {
            match expr {
                Expr::Call(name, args) => {
                    functions.insert(crate::canonical_function_name(name).to_string());
                    pending.extend(args);
                }
                Expr::Unary(_, e) => pending.push(e),
                Expr::Binary(_, a, b) => {
                    pending.push(a);
                    pending.push(b);
                }
                _ => {}
            }
        }
        functions
    }
    pub fn references(&self) -> Result<Vec<FormulaReference>, ToolkitError> {
        references(&self.inner)
    }
    pub fn copied(&self, row_delta: i32, column_delta: i32) -> Result<Self, ToolkitError> {
        Self::parse(&format!(
            "={}",
            render_expr(&self.inner, row_delta, column_delta)?
        ))
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct FormulaReference {
    pub start: CellReference,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub end: Option<CellReference>,
}
fn render_expr(expr: &Expr, dr: i32, dc: i32) -> Result<String, ToolkitError> {
    Ok(match expr {
        Expr::Number(_, token) => token.clone(),
        Expr::Text(s) => format!("\"{}\"", s.replace('"', "\"\"")),
        Expr::Bool(b) => if *b { "TRUE" } else { "FALSE" }.into(),
        Expr::Error(error) => error.to_string(),
        Expr::Missing => String::new(),
        Expr::Native(_) => {
            return Err(ToolkitError::new(
                "unsupported_operation",
                "native primitive values cannot be rendered as worksheet formulas",
            ));
        }
        Expr::Ref(sheet, address) => CellReference::parse(
            if sheet.is_empty() {
                None
            } else {
                Some(sheet.clone())
            },
            address,
        )?
        .shifted(dr, dc)?
        .render(),
        Expr::Range(sheet, start, end) => {
            let s = CellReference::parse(
                if sheet.is_empty() {
                    None
                } else {
                    Some(sheet.clone())
                },
                start,
            )?
            .shifted(dr, dc)?;
            let e = CellReference::parse(None, end)?.shifted(dr, dc)?;
            format!("{}:{}", s.render(), e.render())
        }
        Expr::Unary('%', e) => format!("({})%", render_expr(e, dr, dc)?),
        Expr::Unary(op, e) => format!("{op}({})", render_expr(e, dr, dc)?),
        Expr::Binary(op, a, b) => format!(
            "({}{}{})",
            render_expr(a, dr, dc)?,
            op,
            render_expr(b, dr, dc)?
        ),
        Expr::Call(name, args) => format!(
            "{}({})",
            name,
            args.iter()
                .map(|arg| render_expr(arg, dr, dc))
                .collect::<Result<Vec<_>, _>>()?
                .join(",")
        ),
    })
}
fn references(expr: &Expr) -> Result<Vec<FormulaReference>, ToolkitError> {
    let mut pending = vec![expr];
    let mut result = Vec::new();
    while let Some(expr) = pending.pop() {
        match expr {
            Expr::Ref(sheet, address) => result.push(FormulaReference {
                start: CellReference::parse(
                    if sheet.is_empty() {
                        None
                    } else {
                        Some(sheet.clone())
                    },
                    address,
                )?,
                end: None,
            }),
            Expr::Range(sheet, start, end) => result.push(FormulaReference {
                start: CellReference::parse(
                    if sheet.is_empty() {
                        None
                    } else {
                        Some(sheet.clone())
                    },
                    start,
                )?,
                end: Some(CellReference::parse(
                    if sheet.is_empty() {
                        None
                    } else {
                        Some(sheet.clone())
                    },
                    end,
                )?),
            }),
            Expr::Unary(_, e) => pending.push(e),
            Expr::Binary(_, left, right) => {
                pending.push(right);
                pending.push(left);
            }
            Expr::Call(_, args) => pending.extend(args.iter().rev()),
            _ => {}
        }
    }
    Ok(result)
}
pub fn copy_formula(
    formula: &str,
    row_delta: i32,
    column_delta: i32,
) -> Result<String, ToolkitError> {
    Expression::parse(formula)?
        .copied(row_delta, column_delta)?
        .render()
}
pub fn analyze_formula(formula: &str) -> Result<JsonValue, ToolkitError> {
    let expression = Expression::parse(formula)?;
    Ok(
        json!({"formula":formula,"rendered":expression.render()?,"references":expression.references()?,"functions":expression.functions()}),
    )
}
fn canonical_address(address: &str) -> Result<String, ToolkitError> {
    let reference = CellReference::parse(None, address)?;
    Ok(crate::encode_ref(
        reference.column as usize,
        reference.row as usize,
    ))
}
fn key(sheet: &str, address: &str) -> String {
    format!("{sheet}!{address}")
}
impl WorkbookModel {
    pub fn validate(&self) -> Result<(), ToolkitError> {
        if self.schema_version == 0 {
            return Err(ToolkitError::new(
                "schema_version",
                "schema_version is required",
            ));
        }
        if self.schema_version != 1 {
            return Err(ToolkitError::new(
                "schema_version",
                "expected workbook schema version 1",
            ));
        }
        if self.sheets.len() > 1024 {
            return Err(ToolkitError::new(
                "resource_limit",
                "at most 1024 worksheets",
            ));
        }
        let mut names = BTreeSet::new();
        let mut ids = BTreeSet::new();
        let mut count = 0usize;
        for sheet in &self.sheets {
            if sheet.id.is_empty()
                || !ids.insert(&sheet.id)
                || sheet.name.is_empty()
                || sheet.name.encode_utf16().count() > 31
                || sheet
                    .name
                    .chars()
                    .any(|c| c.is_control() || "[]:*?/\\".contains(c))
                || sheet.name.starts_with('\'')
                || sheet.name.ends_with('\'')
                || !names.insert(sheet.name.to_ascii_lowercase())
            {
                return Err(ToolkitError::new(
                    "invalid_sheet",
                    "sheet names and IDs must be valid and unique",
                ));
            }
            count = count.saturating_add(sheet.cells.len());
            if count > MAX_WORKBOOK_CELLS {
                return Err(ToolkitError::new(
                    "resource_limit",
                    "workbook exceeds 100000 populated cells",
                ));
            }
            for (address, cell) in &sheet.cells {
                if canonical_address(address)? != *address {
                    return Err(ToolkitError::new(
                        "invalid_reference",
                        "cell map keys must be canonical uppercase unanchored A1 addresses",
                    ));
                }
                cell.value.validate()?;
                if let Some(value) = &cell.cached_value {
                    value.validate()?;
                }
                if let Some(style) = &cell.style {
                    style.validate()?;
                }
                if let Some(formula) = &cell.formula {
                    if !matches!(cell.value, CellValue::Blank) {
                        return Err(ToolkitError::new(
                            "invalid_cell",
                            "a formula cell cannot also contain an authored value",
                        ));
                    }
                    if formula.encode_utf16().count() > crate::MAX_FORMULA_LENGTH_UNITS + 1 {
                        return Err(ToolkitError::new(
                            "resource_limit",
                            "formula exceeds 8192 UTF-16 code units",
                        ));
                    }
                }
            }
            for (column, width) in &sheet.column_widths {
                if !column.bytes().all(|c| c.is_ascii_uppercase())
                    || canonical_address(&format!("{column}1")).is_err()
                    || !width.is_finite()
                    || *width <= 0.0
                    || *width > 255.0
                {
                    return Err(ToolkitError::new(
                        "invalid_layout",
                        "column widths require A-XFD keys and finite widths in (0,255]",
                    ));
                }
            }
        }
        if self.inputs.len() > MAX_WORKBOOK_CELLS || self.outputs.len() > MAX_WORKBOOK_CELLS {
            return Err(ToolkitError::new(
                "resource_limit",
                "input and output counts are capped at 100000 each",
            ));
        }
        let mut bound = BTreeSet::new();
        for (name, input) in &self.inputs {
            if name.is_empty() || !matches!(input.kind.as_str(), "number" | "text" | "boolean") {
                return Err(ToolkitError::new(
                    "invalid_input",
                    "inputs require nonempty names and number, text or boolean kinds",
                ));
            }
            let address = self.resolve(&input.sheet, &input.address)?;
            if !bound.insert(address.clone()) {
                return Err(ToolkitError::new(
                    "invalid_input",
                    "multiple inputs cannot bind the same cell",
                ));
            }
            if self
                .get_cell(&address)
                .is_some_and(|cell| cell.formula.is_some() || cell.blocked_reason.is_some())
            {
                return Err(ToolkitError::new(
                    "invalid_input",
                    "input cannot bind a formula or unsupported cell",
                ));
            }
            if input.min.is_some_and(|n| !n.is_finite())
                || input.max.is_some_and(|n| !n.is_finite())
                || input.min.zip(input.max).is_some_and(|(a, b)| a > b)
                || ((input.min.is_some() || input.max.is_some()) && input.kind != "number")
            {
                return Err(ToolkitError::new(
                    "invalid_input",
                    "numeric bounds must be finite, ordered, and used only with numeric inputs",
                ));
            }
            if let Some(choices) = &input.choices {
                if choices.is_empty() || choices.len() > 32 {
                    return Err(ToolkitError::new(
                        "invalid_input",
                        "choice lists must contain 1 to 32 values",
                    ));
                }
                for choice in choices {
                    validate_input_value(input, choice)?;
                }
            }
        }
        for (name, address) in &self.outputs {
            if name.is_empty() {
                return Err(ToolkitError::new(
                    "invalid_output",
                    "outputs require nonempty names",
                ));
            }
            self.resolve(&address.sheet, &address.address)?;
        }
        Ok(())
    }
    fn resolve(&self, sheet: &str, address: &str) -> Result<String, ToolkitError> {
        let actual = self
            .sheets
            .iter()
            .find(|item| item.name.eq_ignore_ascii_case(sheet))
            .ok_or_else(|| {
                ToolkitError::new("invalid_reference", format!("unknown worksheet {sheet}"))
            })?;
        Ok(key(&actual.name, &canonical_address(address)?))
    }
    fn get_cell(&self, key: &str) -> Option<&Cell> {
        let (sheet, address) = key.rsplit_once('!')?;
        self.sheets
            .iter()
            .find(|s| s.name == sheet)?
            .cells
            .get(address)
    }
}
fn validate_input_value(input: &InputBinding, value: &CellValue) -> Result<(), ToolkitError> {
    value.validate()?;
    if matches!(value, CellValue::Blank) {
        return if input.required {
            Err(ToolkitError::new(
                "invalid_input",
                "required input is blank",
            ))
        } else {
            Ok(())
        };
    }
    if !matches!(
        (&*input.kind, value),
        ("number", CellValue::Number(_))
            | ("text", CellValue::Text(_))
            | ("boolean", CellValue::Boolean(_))
    ) {
        return Err(ToolkitError::new(
            "invalid_input",
            format!("input expects {}", input.kind),
        ));
    }
    if let CellValue::Number(n) = value
        && (input.min.is_some_and(|min| *n < min) || input.max.is_some_and(|max| *n > max))
    {
        return Err(ToolkitError::new(
            "invalid_input",
            "input is outside its numeric bounds",
        ));
    }
    if input
        .choices
        .as_ref()
        .is_some_and(|choices| !choices.contains(value))
    {
        return Err(ToolkitError::new(
            "invalid_input",
            "input is not an allowed choice",
        ));
    }
    Ok(())
}
/// Inputs are overrides; omitted bindings use authored workbook defaults, which
/// are validated too. This gives a scenario a useful no-argument execution path.
pub fn validate_inputs(
    model: &WorkbookModel,
    values: &BTreeMap<String, CellValue>,
) -> Result<Vec<Edit>, ToolkitError> {
    model.validate()?;
    for name in values.keys() {
        if !model.inputs.contains_key(name) {
            return Err(ToolkitError::new(
                "unknown_input",
                format!("unknown input {name}"),
            ));
        }
    }
    let mut edits = Vec::new();
    for (name, input) in &model.inputs {
        let key = model.resolve(&input.sheet, &input.address)?;
        let blank = CellValue::Blank;
        let value = values.get(name).unwrap_or_else(|| {
            model
                .get_cell(&key)
                .map(|cell| &cell.value)
                .unwrap_or(&blank)
        });
        validate_input_value(input, value)
            .map_err(|error| ToolkitError::new(error.code, format!("{name}: {}", error.message)))?;
        if values.contains_key(name) {
            edits.push(Edit {
                sheet: input.sheet.clone(),
                address: canonical_address(&input.address)?,
                value: Some(value.clone()),
                formula: None,
                style: None,
            });
        }
    }
    Ok(edits)
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Edit {
    pub sheet: String,
    pub address: String,
    #[serde(
        default,
        deserialize_with = "present_value",
        skip_serializing_if = "Option::is_none"
    )]
    pub value: Option<CellValue>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub formula: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub style: Option<Style>,
}
fn present_value<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<CellValue>, D::Error> {
    CellValue::deserialize(deserializer).map(Some)
}
impl Edit {
    pub fn value(sheet: impl Into<String>, address: impl Into<String>, value: CellValue) -> Self {
        Self {
            sheet: sheet.into(),
            address: address.into(),
            value: Some(value),
            formula: None,
            style: None,
        }
    }
    pub fn formula(
        sheet: impl Into<String>,
        address: impl Into<String>,
        formula: impl Into<String>,
    ) -> Self {
        Self {
            sheet: sheet.into(),
            address: address.into(),
            value: None,
            formula: Some(formula.into()),
            style: None,
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Diagnostic {
    pub code: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sheet: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub address: Option<String>,
}
impl Diagnostic {
    fn for_key(error: ToolkitError, key: Option<&str>) -> Self {
        let parts = key.and_then(|k| k.rsplit_once('!'));
        Self {
            code: error.code,
            message: error.message,
            sheet: parts.map(|p| p.0.into()),
            address: parts.map(|p| p.1.into()),
        }
    }
}
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct CalculationReport {
    pub revision: u64,
    pub outputs: BTreeMap<String, CalculatedValue>,
    pub values: BTreeMap<String, CalculatedValue>,
    pub diagnostics: Vec<Diagnostic>,
    pub evaluated_cells: Vec<String>,
    pub stale: bool,
}

#[derive(Default)]
struct Graph {
    dependencies: BTreeMap<String, BTreeSet<String>>,
    errors: BTreeMap<String, ToolkitError>,
}
impl Graph {
    fn build(model: &WorkbookModel) -> Result<Self, ToolkitError> {
        let mut graph = Self::default();
        let mut total = 0usize;
        let mut expanded_references = 0u64;
        for sheet in &model.sheets {
            for (address, cell) in &sheet.cells {
                let current = key(&sheet.name, address);
                let mut deps = BTreeSet::new();
                if let Some(reason) = &cell.blocked_reason {
                    graph.errors.insert(
                        current.clone(),
                        ToolkitError::new("unsupported_cell", reason),
                    );
                } else if matches!(&cell.value,CellValue::Error{error,..} if matches!(error.as_str(),"#NULL!"|"#SPILL!"))
                {
                    graph.errors.insert(
                        current.clone(),
                        ToolkitError::new(
                            "unsupported_error_value",
                            "this preserved Excel error has no evaluator semantics",
                        ),
                    );
                } else if let Some(formula) = &cell.formula {
                    let parsed = Expression::parse(formula).and_then(|expression| {
                        if let Some(name) = expression
                            .functions()
                            .iter()
                            .find(|name| !SUPPORTED_FUNCTIONS.contains(&name.as_str()))
                        {
                            return Err(ToolkitError::new(
                                "unsupported_formula",
                                format!("unsupported function {name}"),
                            ));
                        }
                        expression.references()
                    });
                    match parsed {
                        Err(error) => {
                            graph.errors.insert(current.clone(), error);
                        }
                        Ok(refs) => {
                            for reference in refs {
                                let sheet_name =
                                    reference.start.sheet.as_deref().unwrap_or(&sheet.name);
                                let end = reference.end.as_ref().unwrap_or(&reference.start);
                                let rows = reference.start.row.min(end.row)
                                    ..=reference.start.row.max(end.row);
                                let cols = reference.start.column.min(end.column)
                                    ..=reference.start.column.max(end.column);
                                let count = u64::from(rows.end() - rows.start() + 1)
                                    * u64::from(cols.end() - cols.start() + 1);
                                if count > MAX_DEPENDENCIES as u64 {
                                    graph.errors.insert(
                                        current.clone(),
                                        ToolkitError::new(
                                            "resource_limit",
                                            "reference range exceeds 100000 cells",
                                        ),
                                    );
                                    break;
                                }
                                expanded_references = expanded_references.saturating_add(count);
                                if expanded_references > MAX_DEPENDENCIES as u64 {
                                    return Err(ToolkitError::new(
                                        "resource_limit",
                                        "expanded workbook references exceed 100000 cells",
                                    ));
                                }
                                let actual = match model
                                    .sheets
                                    .iter()
                                    .find(|s| s.name.eq_ignore_ascii_case(sheet_name))
                                {
                                    Some(s) => &s.name,
                                    None => {
                                        graph.errors.insert(
                                            current.clone(),
                                            ToolkitError::new(
                                                "invalid_reference",
                                                format!("unknown worksheet {sheet_name}"),
                                            ),
                                        );
                                        break;
                                    }
                                };
                                for row in rows {
                                    for col in cols.clone() {
                                        deps.insert(key(
                                            actual,
                                            &crate::encode_ref(col as usize, row as usize),
                                        ));
                                    }
                                }
                                if deps.len() > MAX_DEPENDENCIES {
                                    graph.errors.insert(
                                        current.clone(),
                                        ToolkitError::new(
                                            "resource_limit",
                                            "formula dependencies exceed 100000 cells",
                                        ),
                                    );
                                    break;
                                }
                            }
                        }
                    }
                }
                total = total.saturating_add(deps.len());
                if total > MAX_DEPENDENCIES {
                    return Err(ToolkitError::new(
                        "resource_limit",
                        "workbook exceeds 100000 dependency edges",
                    ));
                }
                graph.dependencies.insert(current, deps);
            }
        }
        let missing: Vec<_> = graph
            .dependencies
            .values()
            .flat_map(|deps| deps.iter())
            .filter(|dep| !graph.dependencies.contains_key(*dep))
            .cloned()
            .collect();
        for dep in missing {
            graph.dependencies.entry(dep).or_default();
        }
        if graph.dependencies.len() > MAX_WORKBOOK_CELLS {
            return Err(ToolkitError::new(
                "resource_limit",
                "populated and referenced workbook cells exceed 100000",
            ));
        }
        Ok(graph)
    }
    fn closure(&self, model: &WorkbookModel) -> Result<BTreeSet<String>, ToolkitError> {
        let mut pending = if model.outputs.is_empty() {
            self.dependencies.keys().cloned().collect::<Vec<_>>()
        } else {
            model
                .outputs
                .values()
                .map(|address| model.resolve(&address.sheet, &address.address))
                .collect::<Result<Vec<_>, _>>()?
        };
        let mut selected = BTreeSet::new();
        while let Some(current) = pending.pop() {
            if selected.insert(current.clone())
                && let Some(deps) = self.dependencies.get(&current)
            {
                pending.extend(deps.iter().cloned());
            }
        }
        Ok(selected)
    }
    fn reverse(&self) -> BTreeMap<String, BTreeSet<String>> {
        let mut reverse: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
        for (current, deps) in &self.dependencies {
            for dep in deps {
                reverse
                    .entry(dep.clone())
                    .or_default()
                    .insert(current.clone());
            }
        }
        reverse
    }
    fn invalidated(&self, dirty: &BTreeSet<String>) -> BTreeSet<String> {
        let reverse = self.reverse();
        let mut invalidated = dirty.clone();
        let mut pending = dirty.iter().cloned().collect::<Vec<_>>();
        while let Some(current) = pending.pop() {
            if let Some(dependents) = reverse.get(&current) {
                for dependent in dependents {
                    if invalidated.insert(dependent.clone()) {
                        pending.push(dependent.clone());
                    }
                }
            }
        }
        invalidated
    }
}
/// Inspect all formulas, including formulas outside the selected output closure.
pub fn inspect(model: &WorkbookModel) -> JsonValue {
    if let Err(error) = model.validate() {
        return json!({"revision":model.revision,"diagnostics":[Diagnostic::for_key(error,None)]});
    }
    match Graph::build(model) {
        Err(error) => {
            json!({"revision":model.revision,"diagnostics":[Diagnostic::for_key(error,None)]})
        }
        Ok(graph) => {
            json!({"revision":model.revision,"sheets":model.sheets.iter().map(|sheet|json!({"id":sheet.id,"name":sheet.name,"populated_cells":sheet.cells.len()})).collect::<Vec<_>>(),"dependencies":graph.dependencies,"diagnostics":graph.errors.into_iter().map(|(key,error)|Diagnostic::for_key(error,Some(&key))).collect::<Vec<_>>(),"inputs":model.inputs,"outputs":model.outputs})
        }
    }
}
fn evaluate_node(
    model: &WorkbookModel,
    graph: &Graph,
    current: &str,
    values: &BTreeMap<String, CalculatedValue>,
) -> Result<CalculatedValue, ToolkitError> {
    if let Some(error) = graph.errors.get(current) {
        return Err(error.clone());
    }
    let Some(cell) = model.get_cell(current) else {
        return Ok(CalculatedValue::Scalar(CellValue::Blank));
    };
    let Some(formula) = &cell.formula else {
        return Ok(CalculatedValue::Scalar(cell.value.clone()));
    };
    let mut environment = HashMap::new();
    if let Some(deps) = graph.dependencies.get(current) {
        for dep in deps {
            match values.get(dep) {
                Some(CalculatedValue::Scalar(value)) => {
                    environment.insert(dep.clone(), value.to_engine());
                }
                Some(CalculatedValue::Array { .. }) => {
                    return Err(ToolkitError::new(
                        "unsupported_array_reference",
                        format!(
                            "worksheet dependency {dep} is an array; spill projection is unsupported"
                        ),
                    ));
                }
                None => {
                    return Err(ToolkitError::new(
                        "unsupported_dependency",
                        format!("dependency {dep} could not be calculated"),
                    ));
                }
            }
        }
    }
    let sheet = current.rsplit_once('!').unwrap().0;
    match evaluate_result(formula, &environment, sheet) {
        // Excel stores a scalar formula referring to an empty cell as numeric
        // zero. Authored blanks and array blanks remain distinct in the model.
        Ok(FormulaResult::Scalar(Value::Blank)) => {
            Ok(CalculatedValue::Scalar(CellValue::Number(0.0)))
        }
        Ok(value) => Ok(CalculatedValue::from_result(value)),
        Err(error) => match error.excel_code() {
            Some(code) => Ok(CalculatedValue::Scalar(CellValue::Error {
                error: code.into(),
                message: None,
            })),
            None => Err(error.into()),
        },
    }
}
fn reserve_result(
    value: &CalculatedValue,
    materialized: &AtomicUsize,
    exhausted: &AtomicBool,
) -> Result<(), ToolkitError> {
    let cells = match value {
        CalculatedValue::Scalar(_) => 1,
        CalculatedValue::Array { rows } => rows.iter().map(Vec::len).sum(),
    };
    if materialized
        .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |used| {
            used.checked_add(cells)
                .filter(|total| *total <= MAX_RESULT_CELLS)
        })
        .is_err()
    {
        exhausted.store(true, Ordering::Relaxed);
        return Err(ToolkitError::new(
            "resource_limit",
            "calculation exceeds 100000 materialized result cells",
        ));
    }
    Ok(())
}
pub fn calculate(model: &WorkbookModel, workers: usize) -> CalculationReport {
    calculate_inner(model, workers, None, &BTreeSet::new())
}
fn calculate_inner(
    model: &WorkbookModel,
    workers: usize,
    previous: Option<&CalculationReport>,
    dirty: &BTreeSet<String>,
) -> CalculationReport {
    let mut report = CalculationReport {
        revision: model.revision,
        ..CalculationReport::default()
    };
    let graph = match model.validate().and_then(|()| {
        validate_inputs(model, &BTreeMap::new())?;
        Graph::build(model)
    }) {
        Ok(graph) => graph,
        Err(error) => {
            report.diagnostics.push(Diagnostic::for_key(error, None));
            return report;
        }
    };
    let selected = match graph.closure(model) {
        Ok(selected) => selected,
        Err(error) => {
            report.diagnostics.push(Diagnostic::for_key(error, None));
            return report;
        }
    };
    let invalidated = graph.invalidated(dirty);
    let reverse = graph.reverse();
    let mut remaining: BTreeMap<String, usize> = selected
        .iter()
        .map(|current| {
            (
                current.clone(),
                graph.dependencies.get(current).map_or(0, BTreeSet::len),
            )
        })
        .collect();
    let mut ready = remaining
        .iter()
        .filter(|(_, count)| **count == 0)
        .map(|(key, _)| key.clone())
        .collect::<Vec<_>>();
    let mut finished = BTreeSet::new();
    let worker_count = workers.clamp(1, MAX_WORKERS);
    let materialized = AtomicUsize::new(0);
    let exhausted = AtomicBool::new(false);
    while !ready.is_empty() {
        ready.sort();
        let mut evaluate = Vec::new();
        for current in &ready {
            if let Some(value) = previous
                .filter(|_| !invalidated.contains(current))
                .and_then(|old| old.values.get(current))
            {
                if let Err(error) = reserve_result(value, &materialized, &exhausted) {
                    report
                        .diagnostics
                        .push(Diagnostic::for_key(error, Some(current)));
                    return report;
                }
                report.values.insert(current.clone(), value.clone());
            } else if model
                .get_cell(current)
                .is_some_and(|cell| cell.formula.is_some())
                && !graph.errors.contains_key(current)
            {
                evaluate.push(current.clone());
            } else {
                match evaluate_node(model, &graph, current, &report.values) {
                    Ok(value) => {
                        if let Err(error) = reserve_result(&value, &materialized, &exhausted) {
                            report
                                .diagnostics
                                .push(Diagnostic::for_key(error, Some(current)));
                            return report;
                        }
                        report.values.insert(current.clone(), value);
                    }
                    Err(error) => report
                        .diagnostics
                        .push(Diagnostic::for_key(error, Some(current))),
                }
            }
        }
        let execute_chunk = |chunk: &[String]| {
            let mut results = Vec::new();
            for current in chunk {
                if exhausted.load(Ordering::Relaxed) {
                    break;
                }
                let result =
                    evaluate_node(model, &graph, current, &report.values).and_then(|value| {
                        reserve_result(&value, &materialized, &exhausted)?;
                        Ok(value)
                    });
                results.push((current.clone(), result));
            }
            results
        };
        let results = if worker_count == 1 || evaluate.len() < 2 {
            execute_chunk(&evaluate)
        } else {
            std::thread::scope(|scope| {
                let chunk_size = evaluate.len().div_ceil(worker_count);
                let handles = evaluate
                    .chunks(chunk_size)
                    .map(|chunk| scope.spawn(|| execute_chunk(chunk)))
                    .collect::<Vec<_>>();
                let mut results = Vec::new();
                for handle in handles {
                    match handle.join() {
                        Ok(mut chunk) => results.append(&mut chunk),
                        Err(_) => results.push((
                            String::new(),
                            Err(ToolkitError::new(
                                "worker_failure",
                                "calculation worker failed",
                            )),
                        )),
                    }
                }
                results
            })
        };
        for (current, result) in results {
            if !current.is_empty() {
                report.evaluated_cells.push(current.clone());
            }
            match result {
                Ok(value) => {
                    report.values.insert(current, value);
                }
                Err(error) => report
                    .diagnostics
                    .push(Diagnostic::for_key(error, Some(&current))),
            }
        }
        if exhausted.load(Ordering::Relaxed) {
            report.evaluated_cells.sort();
            return report;
        }
        let mut next = Vec::new();
        for current in ready {
            finished.insert(current.clone());
            if let Some(dependents) = reverse.get(&current) {
                for dependent in dependents {
                    if let Some(count) = remaining.get_mut(dependent) {
                        *count -= 1;
                        if *count == 0 {
                            next.push(dependent.clone());
                        }
                    }
                }
            }
        }
        ready = next;
    }
    for current in selected.difference(&finished) {
        report.diagnostics.push(Diagnostic::for_key(
            ToolkitError::new(
                "dependency_cycle",
                "cell is part of, or depends on, a circular reference",
            ),
            Some(current),
        ));
    }
    let output_materialized = AtomicUsize::new(0);
    for (name, address) in &model.outputs {
        if let Ok(current) = model.resolve(&address.sheet, &address.address)
            && let Some(value) = report.values.get(&current)
        {
            if let Err(error) = reserve_result(value, &output_materialized, &exhausted) {
                report
                    .diagnostics
                    .push(Diagnostic::for_key(error, Some(&current)));
                break;
            }
            report.outputs.insert(name.clone(), value.clone());
        }
    }
    report.evaluated_cells.sort();
    report.diagnostics.sort_by(|a, b| {
        (&a.sheet, &a.address, &a.code, &a.message)
            .cmp(&(&b.sheet, &b.address, &b.code, &b.message))
    });
    report
}

struct SessionState {
    model: WorkbookModel,
    published: Option<CalculationReport>,
    dirty: BTreeSet<String>,
}
/// Clones share one synchronized transient session. Calculation holds no write
/// lock while evaluating; publication checks the source revision again.
#[derive(Clone)]
pub struct Session {
    state: Arc<Mutex<SessionState>>,
}
impl Session {
    /// Locks the shared state, recovering it if a panic poisoned the mutex.
    /// Recovery is sound because every fallible step under the lock works on
    /// a private copy (`apply` builds and validates a candidate model,
    /// `publish` clones the report) and only infallible field moves follow,
    /// so an unwinding thread cannot leave the model, the published report
    /// and the dirty set half-updated. Without this, one panic would turn
    /// every later call on any clone into a panic through PyO3.
    fn state(&self) -> MutexGuard<'_, SessionState> {
        self.state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }
    pub fn new(model: WorkbookModel) -> Result<Self, ToolkitError> {
        model.validate()?;
        Ok(Self {
            state: Arc::new(Mutex::new(SessionState {
                model,
                published: None,
                dirty: BTreeSet::new(),
            })),
        })
    }
    pub fn snapshot(&self) -> WorkbookModel {
        self.state().model.clone()
    }
    pub fn published(&self) -> Option<CalculationReport> {
        let state = self.state();
        state
            .published
            .as_ref()
            .filter(|report| report.revision == state.model.revision)
            .cloned()
    }
    pub fn apply(
        &self,
        edits: Vec<Edit>,
        expected_revision: Option<u64>,
    ) -> Result<u64, ToolkitError> {
        if edits.len() > MAX_EDIT_BATCH {
            return Err(ToolkitError::new(
                "resource_limit",
                "edit batch exceeds 10000 edits",
            ));
        }
        let mut state = self.state();
        if expected_revision.is_some_and(|expected| expected != state.model.revision) {
            return Err(ToolkitError::new(
                "revision_conflict",
                format!("expected revision does not match {}", state.model.revision),
            ));
        }
        if edits.is_empty() {
            return Ok(state.model.revision);
        }
        let mut candidate = state.model.clone();
        let mut changed = BTreeSet::new();
        for edit in edits {
            if (edit.value.is_some() && edit.formula.is_some())
                || (edit.value.is_none() && edit.formula.is_none() && edit.style.is_none())
            {
                return Err(ToolkitError::new(
                    "invalid_edit",
                    "edit requires one value or formula, optionally a style, or a style alone",
                ));
            }
            let target = candidate.resolve(&edit.sheet, &edit.address)?;
            if !changed.insert(target.clone()) {
                return Err(ToolkitError::new(
                    "invalid_edit",
                    "a batch may edit each cell only once",
                ));
            }
            let (sheet_name, address) = target.rsplit_once('!').unwrap();
            let sheet = candidate
                .sheets
                .iter_mut()
                .find(|sheet| sheet.name == sheet_name)
                .unwrap();
            let cell = sheet.cells.entry(address.into()).or_default();
            if cell.blocked_reason.is_some() {
                return Err(ToolkitError::new(
                    "unsupported_edit",
                    format!("{target} contains preserved-only content"),
                ));
            }
            if let Some(value) = edit.value {
                cell.value = value;
                cell.formula = None;
                cell.cached_value = None;
            }
            if let Some(formula) = edit.formula {
                Expression::parse(&formula)?;
                cell.value = CellValue::Blank;
                cell.formula = Some(formula);
                cell.cached_value = None;
            }
            if let Some(style) = edit.style {
                cell.style = Some(style);
            }
        }
        candidate.validate()?;
        validate_inputs(&candidate, &BTreeMap::new())?;
        Graph::build(&candidate)?;
        candidate.revision = candidate
            .revision
            .checked_add(1)
            .ok_or_else(|| ToolkitError::new("resource_limit", "revision counter exhausted"))?;
        state.model = candidate;
        state.dirty.extend(changed);
        Ok(state.model.revision)
    }
    pub fn calculate(&self, workers: usize) -> CalculationReport {
        let (model, previous, dirty) = {
            let state = self.state();
            (
                state.model.clone(),
                state.published.clone(),
                state.dirty.clone(),
            )
        };
        let report = calculate_inner(&model, workers, previous.as_ref(), &dirty);
        self.publish(report)
    }
    fn publish(&self, mut report: CalculationReport) -> CalculationReport {
        let mut state = self.state();
        if state.model.revision != report.revision {
            report.stale = true;
        } else {
            state.published = Some(report.clone());
            state.dirty.clear();
        }
        report
    }
}

/// Public-safe operating model: each forecast row incurs the same fixed cost.
/// Break-even is #N/A when the contribution margin is not positive; zero units
/// would incorrectly claim the operation can cover its fixed costs.
pub fn operating_scenario() -> WorkbookModel {
    let mut assumptions = Sheet::new("Assumptions");
    let mut forecast = Sheet::new("Forecast");
    let header = Style {
        bold: Some(true),
        font_color: Some("FFFFFF".into()),
        fill_color: Some("19324D".into()),
        ..Style::default()
    };
    let money = Style {
        number_format: Some("$#,##0.00;[Red]($#,##0.00)".into()),
        ..Style::default()
    };
    for (row, label, value) in [
        (1, "Unit price", 20.0),
        (2, "Unit variable cost", 8.0),
        (3, "Fixed cost per period", 1000.0),
    ] {
        assumptions.cells.insert(
            format!("A{row}"),
            Cell::value(CellValue::Text(label.into())),
        );
        assumptions.cells.insert(
            format!("B{row}"),
            Cell {
                style: Some(Style {
                    fill_color: Some("E8F2FF".into()),
                    ..money.clone()
                }),
                ..Cell::value(CellValue::Number(value))
            },
        );
    }
    for (column, label) in [
        ("A", "Period"),
        ("B", "Quantity"),
        ("C", "Revenue"),
        ("D", "Variable costs"),
        ("E", "Profit"),
        ("F", "Summary outputs"),
    ] {
        forecast.cells.insert(
            format!("{column}1"),
            Cell {
                style: Some(header.clone()),
                ..Cell::value(CellValue::Text(label.into()))
            },
        );
    }
    for (row, quantity) in [(2, 100.0), (3, 120.0), (4, 150.0)] {
        forecast.cells.insert(
            format!("A{row}"),
            Cell::value(CellValue::Text(format!("Period {}", row - 1))),
        );
        forecast
            .cells
            .insert(format!("B{row}"), Cell::value(CellValue::Number(quantity)));
        for (column, source) in [
            ("C", "=B2*Assumptions!$B$1"),
            ("D", "=B2*Assumptions!$B$2"),
            ("E", "=C2-D2-Assumptions!$B$3"),
        ] {
            forecast.cells.insert(
                format!("{column}{row}"),
                Cell {
                    style: Some(money.clone()),
                    ..Cell::formula(
                        copy_formula(source, row - 2, 0).expect("scenario references are valid"),
                    )
                },
            );
        }
    }
    forecast.cells.insert(
        "F2".into(),
        Cell {
            style: Some(money.clone()),
            ..Cell::formula("=SUM(C2:C4)")
        },
    );
    forecast.cells.insert(
        "F3".into(),
        Cell {
            style: Some(money),
            ..Cell::formula("=SUM(E2:E4)")
        },
    );
    forecast.cells.insert("F4".into(),Cell{style:Some(Style{number_format:Some("0.00".into()),..Style::default()}),..Cell::formula("=IF(Assumptions!B1>Assumptions!B2,Assumptions!B3/(Assumptions!B1-Assumptions!B2),NA())")});
    for (address, label) in [
        ("G2", "Total revenue"),
        ("G3", "Total profit"),
        ("G4", "Break-even units per period"),
    ] {
        forecast
            .cells
            .insert(address.into(), Cell::value(CellValue::Text(label.into())));
    }
    assumptions
        .column_widths
        .extend([("A".into(), 26.0), ("B".into(), 18.0)]);
    for col in ["A", "B", "C", "D", "E", "F"] {
        forecast.column_widths.insert(col.into(), 20.0);
    }
    forecast.column_widths.insert("G".into(), 32.0);
    WorkbookModel {
        schema_version: 1,
        revision: 0,
        sheets: vec![assumptions, forecast],
        inputs: [
            (
                "unit_price".into(),
                InputBinding::number("Assumptions", "B1", Some(0.0), None),
            ),
            (
                "unit_cost".into(),
                InputBinding::number("Assumptions", "B2", Some(0.0), None),
            ),
            (
                "fixed_cost".into(),
                InputBinding::number("Assumptions", "B3", Some(0.0), None),
            ),
        ]
        .into(),
        outputs: [
            ("revenue".into(), CellAddress::new("Forecast", "F2")),
            ("profit".into(), CellAddress::new("Forecast", "F3")),
            (
                "break_even_units".into(),
                CellAddress::new("Forecast", "F4"),
            ),
        ]
        .into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn model(cells: &[(&str, Cell)], outputs: &[(&str, &str)]) -> WorkbookModel {
        let mut sheet = Sheet::new("Data");
        sheet.cells = cells
            .iter()
            .map(|(address, cell)| (address.to_string(), cell.clone()))
            .collect();
        WorkbookModel {
            sheets: vec![sheet],
            outputs: outputs
                .iter()
                .map(|(name, address)| (name.to_string(), CellAddress::new("Data", *address)))
                .collect(),
            ..WorkbookModel::default()
        }
    }
    fn number(n: f64) -> Cell {
        Cell::value(CellValue::Number(n))
    }
    fn scalar(n: f64) -> CalculatedValue {
        CalculatedValue::Scalar(CellValue::Number(n))
    }
    #[test]
    fn a_document_without_a_version_is_refused_with_a_typed_diagnostic() {
        let mut document: JsonValue = serde_json::to_value(operating_scenario()).unwrap();
        document.as_object_mut().unwrap().remove("schema_version");
        let model: WorkbookModel = serde_json::from_value(document.clone()).unwrap();
        let error = model.validate().unwrap_err();
        assert_eq!(error.code, "schema_version");
        assert_eq!(error.message, "schema_version is required");
        assert!(Session::new(model.clone()).is_err());
        assert_eq!(
            inspect(&model)["diagnostics"][0]["code"],
            JsonValue::from("schema_version")
        );
        document["schema_version"] = JsonValue::from(2);
        let unsupported: WorkbookModel = serde_json::from_value(document).unwrap();
        assert_eq!(unsupported.validate().unwrap_err().code, "schema_version");
    }
    #[test]
    fn scenario_json_roundtrip_and_reference_results() {
        let model = operating_scenario();
        model.validate().unwrap();
        let json = serde_json::to_string(&model).unwrap();
        assert_eq!(serde_json::from_str::<WorkbookModel>(&json).unwrap(), model);
        let result = calculate(&model, 1);
        assert!(result.diagnostics.is_empty(), "{:?}", result.diagnostics);
        assert_eq!(result.outputs["revenue"], scalar(7400.0));
        assert_eq!(result.outputs["profit"], scalar(1440.0));
        assert_eq!(result.outputs["break_even_units"], scalar(1000.0 / 12.0));
        assert_eq!(result, calculate(&model, 8));
    }
    #[test]
    fn no_finite_break_even_is_an_excel_error() {
        let session = Session::new(operating_scenario()).unwrap();
        for price in [8.0, 7.0, 0.0] {
            session
                .apply(
                    vec![Edit::value("Assumptions", "B1", CellValue::Number(price))],
                    None,
                )
                .unwrap();
            let report = session.calculate(1);
            assert!(report.diagnostics.is_empty());
            assert_eq!(
                report.outputs["break_even_units"],
                CalculatedValue::Scalar(CellValue::Error {
                    error: "#N/A".into(),
                    message: None
                })
            );
        }
    }
    #[test]
    fn worksheet_formula_blank_is_zero_but_authored_blank_remains_blank() {
        let workbook = model(
            &[
                ("A1", Cell::value(CellValue::Blank)),
                ("B1", Cell::formula("=A1")),
                ("C1", Cell::formula("=B1+1")),
            ],
            &[("blank", "A1"), ("reference", "B1"), ("derived", "C1")],
        );
        let report = calculate(&workbook, 1);
        assert!(report.diagnostics.is_empty());
        assert_eq!(
            report.outputs["blank"],
            CalculatedValue::Scalar(CellValue::Blank)
        );
        assert_eq!(report.outputs["reference"], scalar(0.0));
        assert_eq!(report.outputs["derived"], scalar(1.0));
        assert_eq!(
            crate::evaluate("=A1", &HashMap::new(), "Data").unwrap(),
            Value::Blank
        );
    }
    #[test]
    fn oversized_unselected_formula_is_preserved_without_blocking_safe_output() {
        let mut workbook = model(
            &[
                ("A1", number(1.0)),
                ("B1", Cell::formula("=SUM(A1:XFD1048576)")),
            ],
            &[("safe", "A1")],
        );
        let report = calculate(&workbook, 1);
        assert!(report.diagnostics.is_empty());
        assert_eq!(report.outputs["safe"], scalar(1.0));
        assert_eq!(
            inspect(&workbook)["diagnostics"][0]["code"],
            "resource_limit"
        );
        workbook
            .outputs
            .insert("unsafe".into(), CellAddress::new("Data", "B1"));
        assert!(
            calculate(&workbook, 1)
                .diagnostics
                .iter()
                .any(|d| d.code == "resource_limit")
        );
    }
    #[test]
    fn copies_mixed_anchors_and_quoted_sheets_without_touching_strings() {
        let formula = "='Bob''s data'!A1+$B2+C$3+$D$4+SUM(E1:F2)+IF(TRUE,\"A1\",0)";
        let copied = copy_formula(formula, 2, 3).unwrap();
        let analysis = analyze_formula(&copied).unwrap();
        let refs = analysis["references"].as_array().unwrap();
        assert_eq!(refs[0]["start"]["sheet"], "Bob's data");
        assert_eq!(refs[0]["start"]["row"], 3);
        assert_eq!(refs[0]["start"]["column"], 4);
        assert_eq!(refs[1]["start"]["column"], 2);
        assert_eq!(refs[1]["start"]["row"], 4);
        assert_eq!(refs[2]["start"]["column"], 6);
        assert_eq!(refs[2]["start"]["row"], 3);
        assert_eq!(refs[3]["start"]["row"], 4);
        assert_eq!(refs[3]["start"]["column"], 4);
        assert!(copied.contains("\"A1\""));
        assert!(copied.contains("'Bob''s data'!D3"));
        assert!(copy_formula("=A1", -1, 0).is_err());
        assert!(copy_formula("=XFD1048576", 1, 0).is_err());
        assert!(copy_formula("=$A$1", -500, -500).is_ok());
    }
    #[test]
    fn typed_expression_uses_evaluator_precedence_and_missing_arguments() {
        let expr = Expression::binary(
            "*",
            Expression::binary(
                "+",
                Expression::number(2.0).unwrap(),
                Expression::number(3.0).unwrap(),
            )
            .unwrap(),
            Expression::number(4.0).unwrap(),
        )
        .unwrap();
        assert_eq!(
            crate::evaluate(&expr.render().unwrap(), &HashMap::new(), "Data").unwrap(),
            Value::Number(20.0)
        );
        let expr = Expression::call(
            "IF",
            vec![
                Expression::boolean(true),
                Expression::missing(),
                Expression::number(3.0).unwrap(),
            ],
        )
        .unwrap();
        assert_eq!(expr.render().unwrap(), "=IF(TRUE,,3)");
        assert_eq!(
            crate::evaluate(&expr.render().unwrap(), &HashMap::new(), "Data"),
            Err(FormulaError::Value)
        );
    }
    #[test]
    fn cross_sheet_chain_uses_fresh_results_and_actual_errors() {
        let mut workbook = model(
            &[
                ("A1", number(4.0)),
                (
                    "B1",
                    Cell {
                        cached_value: Some(CellValue::Number(999.0)),
                        ..Cell::formula("=A1*2")
                    },
                ),
            ],
            &[],
        );
        let mut second = Sheet::new("Other sheet");
        second
            .cells
            .insert("A1".into(), Cell::formula("=Data!B1+1"));
        second.cells.insert("B1".into(), Cell::formula("=A1/0"));
        workbook.sheets.push(second);
        workbook
            .outputs
            .insert("result".into(), CellAddress::new("Other sheet", "A1"));
        workbook
            .outputs
            .insert("error".into(), CellAddress::new("Other sheet", "B1"));
        let report = calculate(&workbook, 1);
        assert!(report.diagnostics.is_empty());
        assert_eq!(report.outputs["result"], scalar(9.0));
        assert_eq!(
            report.outputs["error"],
            CalculatedValue::Scalar(CellValue::Error {
                error: "#DIV/0!".into(),
                message: None
            })
        );
    }
    #[test]
    fn unsupported_formula_only_blocks_its_requested_closure() {
        let mut workbook = model(
            &[
                ("A1", number(3.0)),
                ("B1", Cell::formula("=A1+1")),
                (
                    "C1",
                    Cell {
                        cached_value: Some(CellValue::Number(20.0)),
                        ..Cell::formula("=NOT_A_FUNCTION(A1)")
                    },
                ),
                ("D1", Cell::formula("=C1+1")),
            ],
            &[("safe", "B1")],
        );
        assert!(calculate(&workbook, 1).diagnostics.is_empty());
        workbook
            .outputs
            .insert("unsafe".into(), CellAddress::new("Data", "D1"));
        let report = calculate(&workbook, 1);
        assert_eq!(report.outputs["safe"], scalar(4.0));
        assert!(!report.outputs.contains_key("unsafe"));
        assert!(
            report
                .diagnostics
                .iter()
                .any(|d| d.code == "unsupported_formula")
        );
        assert!(
            report
                .diagnostics
                .iter()
                .any(|d| d.code == "unsupported_dependency")
        );
        assert!(!report.values.contains_key("Data!C1"));
    }
    #[test]
    fn imported_error_codes_are_preserved_and_unsupported_values_are_local() {
        let mut workbook = model(
            &[
                ("A1", number(1.0)),
                (
                    "B1",
                    Cell::value(CellValue::Error {
                        error: "#SPILL!".into(),
                        message: None,
                    }),
                ),
                (
                    "C1",
                    Cell::value(CellValue::Error {
                        error: "#NULL!".into(),
                        message: None,
                    }),
                ),
                (
                    "D1",
                    Cell::value(CellValue::Error {
                        error: "#N/A!".into(),
                        message: None,
                    }),
                ),
                ("E1", Cell::formula("=IFNA(D1,7)")),
            ],
            &[("safe", "A1"), ("alias", "E1")],
        );
        workbook.validate().unwrap();
        let serialized = serde_json::to_string(&workbook).unwrap();
        assert!(serialized.contains("#SPILL!"));
        assert!(serialized.contains("#NULL!"));
        assert!(serialized.contains("#N/A!"));
        let report = calculate(&workbook, 1);
        assert!(report.diagnostics.is_empty());
        assert_eq!(report.outputs["alias"], scalar(7.0));
        workbook
            .outputs
            .insert("unsupported".into(), CellAddress::new("Data", "B1"));
        let report = calculate(&workbook, 1);
        assert!(
            report
                .diagnostics
                .iter()
                .any(|d| d.code == "unsupported_error_value")
        );
        assert!(!report.outputs.contains_key("unsupported"));
    }
    #[test]
    fn preserved_only_nonformula_cells_block_dependents_and_edits() {
        let workbook = model(
            &[
                (
                    "A1",
                    Cell {
                        blocked_reason: Some("table formula region".into()),
                        ..number(2.0)
                    },
                ),
                ("B1", Cell::formula("=A1+1")),
            ],
            &[("output", "B1")],
        );
        let session = Session::new(workbook).unwrap();
        let report = session.calculate(1);
        assert!(report.outputs.is_empty());
        assert!(
            report
                .diagnostics
                .iter()
                .any(|d| d.code == "unsupported_cell")
        );
        assert_eq!(
            session
                .apply(
                    vec![Edit::value("Data", "A1", CellValue::Number(9.0))],
                    None
                )
                .unwrap_err()
                .code,
            "unsupported_edit"
        );
    }
    #[test]
    fn cycles_and_missing_sheets_never_reuse_imported_caches() {
        let workbook = model(
            &[
                ("A1", Cell::formula("=B1")),
                ("B1", Cell::formula("=A1")),
                ("C1", Cell::formula("=Missing!A1")),
            ],
            &[],
        );
        let report = calculate(&workbook, 1);
        assert_eq!(
            report
                .diagnostics
                .iter()
                .filter(|d| d.code == "dependency_cycle")
                .count(),
            2
        );
        assert!(
            report
                .diagnostics
                .iter()
                .any(|d| d.code == "invalid_reference")
        );
        assert!(report.values.is_empty());
    }
    #[test]
    fn array_results_have_shape_without_implicit_spill_projection() {
        let mut workbook = model(
            &[
                ("A1", Cell::formula("=SEQUENCE(2,2)")),
                ("B1", Cell::formula("=A1+1")),
            ],
            &[("array", "A1")],
        );
        let report = calculate(&workbook, 1);
        assert_eq!(
            report.outputs["array"],
            CalculatedValue::Array {
                rows: vec![
                    vec![CellValue::Number(1.0), CellValue::Number(2.0)],
                    vec![CellValue::Number(3.0), CellValue::Number(4.0)]
                ]
            }
        );
        assert!(report.diagnostics.is_empty());
        workbook
            .outputs
            .insert("spill".into(), CellAddress::new("Data", "B1"));
        assert!(
            calculate(&workbook, 1)
                .diagnostics
                .iter()
                .any(|d| d.code == "unsupported_array_reference")
        );
    }
    #[test]
    fn invalid_batches_and_revision_conflicts_are_atomic() {
        let session = Session::new(operating_scenario()).unwrap();
        let before = session.snapshot();
        assert!(
            session
                .apply(
                    vec![
                        Edit::value("Assumptions", "B1", CellValue::Number(30.0)),
                        Edit::value("Assumptions", "B2", CellValue::Number(-1.0))
                    ],
                    Some(0)
                )
                .is_err()
        );
        assert_eq!(session.snapshot(), before);
        assert_eq!(
            session
                .apply(
                    vec![Edit::value("Assumptions", "B1", CellValue::Number(30.0))],
                    Some(0)
                )
                .unwrap(),
            1
        );
        assert_eq!(
            session
                .apply(
                    vec![Edit::value("Assumptions", "B1", CellValue::Number(35.0))],
                    Some(0)
                )
                .unwrap_err()
                .code,
            "revision_conflict"
        );
        assert_eq!(before.sheets[0].cells["B1"].value, CellValue::Number(20.0));
    }
    #[test]
    fn incremental_formula_replacement_updates_dependency_invalidation() {
        let workbook = model(
            &[
                ("A1", number(1.0)),
                ("A2", number(10.0)),
                ("B1", Cell::formula("=A1+1")),
                ("C1", Cell::formula("=B1*2")),
                ("D1", Cell::formula("=A2+1")),
            ],
            &[("main", "C1"), ("other", "D1")],
        );
        let session = Session::new(workbook).unwrap();
        assert_eq!(session.calculate(1).evaluated_cells.len(), 3);
        session
            .apply(
                vec![Edit::value("Data", "A1", CellValue::Number(2.0))],
                None,
            )
            .unwrap();
        let incremental = session.calculate(1);
        assert_eq!(incremental.evaluated_cells, vec!["Data!B1", "Data!C1"]);
        assert_eq!(
            incremental.outputs,
            calculate(&session.snapshot(), 1).outputs
        );
        assert!(session.calculate(1).evaluated_cells.is_empty());
        session
            .apply(vec![Edit::formula("Data", "B1", "=A2+1")], None)
            .unwrap();
        session.calculate(1);
        session
            .apply(
                vec![Edit::value("Data", "A1", CellValue::Number(3.0))],
                None,
            )
            .unwrap();
        let report = session.calculate(1);
        assert!(report.evaluated_cells.is_empty());
        assert_eq!(report.outputs["main"], scalar(22.0));
        session
            .apply(
                vec![Edit::value("Data", "A2", CellValue::Number(20.0))],
                None,
            )
            .unwrap();
        let report = session.calculate(1);
        assert_eq!(report.evaluated_cells.len(), 3);
        assert_eq!(report.outputs, calculate(&session.snapshot(), 1).outputs);
    }
    #[test]
    fn obsolete_calculation_cannot_publish_into_new_revision() {
        let session = Session::new(operating_scenario()).unwrap();
        let old = calculate(&session.snapshot(), 1);
        session
            .apply(
                vec![Edit::value("Assumptions", "B1", CellValue::Number(30.0))],
                None,
            )
            .unwrap();
        assert!(session.publish(old).stale);
        assert!(session.published().is_none());
        let current = session.calculate(2);
        assert!(!current.stale);
        assert_eq!(session.published().unwrap().revision, 1);
    }
    #[test]
    fn concurrent_readers_observe_immutable_snapshot_revisions() {
        let session = Session::new(operating_scenario()).unwrap();
        let original = session.snapshot();
        let copy = session.clone();
        let reader = std::thread::spawn(move || {
            for _ in 0..10 {
                let snapshot = copy.snapshot();
                let report = calculate(&snapshot, 2);
                assert_eq!(report.revision, snapshot.revision);
                assert!(report.diagnostics.is_empty());
            }
        });
        for price in [21.0, 22.0, 23.0] {
            session
                .apply(
                    vec![Edit::value("Assumptions", "B1", CellValue::Number(price))],
                    None,
                )
                .unwrap();
        }
        reader.join().unwrap();
        assert_eq!(original.revision, 0);
        assert_eq!(session.snapshot().revision, 3);
    }
    #[test]
    fn wire_null_is_explicit_clear_and_invalid_fields_are_rejected() {
        let edit: Edit =
            serde_json::from_str(r#"{"sheet":"Data","address":"A1","value":null}"#).unwrap();
        assert_eq!(edit.value, Some(CellValue::Blank));
        let session = Session::new(model(&[("A1", number(3.0))], &[])).unwrap();
        session.apply(vec![edit], None).unwrap();
        assert_eq!(
            session.snapshot().sheets[0].cells["A1"].value,
            CellValue::Blank
        );
        assert!(
            serde_json::from_str::<Edit>(r#"{"sheet":"Data","address":"A1","valuer":7}"#).is_err()
        );
    }
    #[test]
    fn constraints_validate_defaults_overrides_types_bounds_and_choices() {
        let mut workbook = operating_scenario();
        assert!(validate_inputs(&workbook, &BTreeMap::new()).is_ok());
        for value in [
            CellValue::Number(f64::NAN),
            CellValue::Number(f64::INFINITY),
            CellValue::Number(-1.0),
            CellValue::Text("20".into()),
            CellValue::Blank,
        ] {
            assert!(validate_inputs(&workbook, &[("unit_price".into(), value)].into()).is_err());
        }
        assert!(
            validate_inputs(
                &workbook,
                &[("unknown".into(), CellValue::Number(1.0))].into()
            )
            .is_err()
        );
        workbook.inputs.get_mut("unit_price").unwrap().choices =
            Some(vec![CellValue::Number(20.0), CellValue::Number(30.0)]);
        assert!(
            validate_inputs(
                &workbook,
                &[("unit_price".into(), CellValue::Number(21.0))].into()
            )
            .is_err()
        );
        assert!(
            validate_inputs(
                &workbook,
                &[("unit_price".into(), CellValue::Number(30.0))].into()
            )
            .is_ok()
        );
        workbook.sheets[0].cells.get_mut("B1").unwrap().value = CellValue::Number(21.0);
        assert!(
            calculate(&workbook, 1)
                .diagnostics
                .iter()
                .any(|d| d.code == "invalid_input")
        );
    }
    #[test]
    fn materialized_arrays_and_output_aliases_are_cumulatively_bounded() {
        let workbook = model(
            &[
                ("A1", Cell::formula("=SEQUENCE(60000)")),
                ("B1", Cell::formula("=SEQUENCE(60000)")),
                ("C1", Cell::formula("=SEQUENCE(60000)")),
            ],
            &[],
        );
        for workers in [1, 8] {
            let report = calculate(&workbook, workers);
            assert!(
                report
                    .diagnostics
                    .iter()
                    .any(|d| d.code == "resource_limit")
            );
            let retained: usize = report
                .values
                .values()
                .map(|v| match v {
                    CalculatedValue::Scalar(_) => 1,
                    CalculatedValue::Array { rows } => rows.iter().map(Vec::len).sum(),
                })
                .sum();
            assert!(retained <= MAX_RESULT_CELLS);
        }
        let workbook = model(
            &[("A1", Cell::formula("=SEQUENCE(60000)"))],
            &[("one", "A1"), ("two", "A1")],
        );
        let report = calculate(&workbook, 1);
        assert!(
            report
                .diagnostics
                .iter()
                .any(|d| d.code == "resource_limit")
        );
        assert_eq!(report.outputs.len(), 1);
    }
    #[test]
    fn inspect_reports_unknown_functions_without_calculating() {
        let workbook = model(
            &[
                ("A1", Cell::formula("=UNKNOWN(1)")),
                ("B1", Cell::formula("=1/0")),
            ],
            &[],
        );
        let inspection = inspect(&workbook);
        assert_eq!(inspection["diagnostics"].as_array().unwrap().len(), 1);
        assert_eq!(inspection["diagnostics"][0]["code"], "unsupported_formula");
        let catalog: JsonValue = serde_json::from_str(
            &std::fs::read_to_string(concat!(
                env!("CARGO_MANIFEST_DIR"),
                "/../catalog/formulas.json"
            ))
            .unwrap(),
        )
        .unwrap();
        let catalog_names: BTreeSet<_> = catalog["functions"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|f| {
                matches!(
                    f["status"]["rust"].as_str(),
                    Some("implemented" | "conformance-tested")
                )
            })
            .map(|f| f["name"].as_str().unwrap())
            .collect();
        assert_eq!(catalog_names, SUPPORTED_FUNCTIONS.iter().copied().collect());
    }
    #[test]
    fn expression_depth_limit_matches_python_from_the_root() {
        // A flat left-associative chain of n terms is a tree of depth n when
        // the root counts as level 1. Python accepts 96 terms and refuses 97;
        // Rust must draw the line at the same term.
        let chain = |terms: usize| format!("={}", vec!["1"; terms].join("+"));
        assert!(Expression::parse(&chain(96)).is_ok());
        assert_eq!(
            Expression::parse(&chain(97)).unwrap_err().code,
            "resource_limit"
        );
    }

    #[test]
    fn session_recovers_after_a_panic_under_its_lock() {
        let session = Session::new(operating_scenario()).unwrap();
        let revision = session.snapshot().revision;
        let poisoner = session.clone();
        let outcome = std::thread::spawn(move || {
            let _guard = poisoner.state.lock().unwrap();
            panic!("deliberate panic while holding the session lock");
        })
        .join();
        assert!(outcome.is_err(), "the spawned thread must have panicked");
        assert!(session.state.is_poisoned());
        // Every entry point still works on the intact state.
        assert_eq!(session.snapshot().revision, revision);
        assert!(session.published().is_none());
        let next = session
            .apply(
                vec![Edit::value("Assumptions", "B1", CellValue::Number(20.0))],
                Some(revision),
            )
            .unwrap();
        assert_eq!(next, revision + 1);
        let report = session.calculate(1);
        assert!(!report.stale);
        assert_eq!(session.published().map(|r| r.revision), Some(next));
    }

    #[test]
    fn resource_limits_and_style_validation_are_predictable() {
        assert!(Expression::parse(&format!("={}", "1+".repeat(4200))).is_err());
        let expression = format!("={}1", "1+".repeat(150));
        assert_eq!(
            Expression::parse(&expression).unwrap_err().code,
            "resource_limit"
        );
        let workbook = model(&[("A1", Cell::formula("=SUM(A2:A100002)"))], &[]);
        assert!(
            calculate(&workbook, 1)
                .diagnostics
                .iter()
                .any(|d| d.code == "resource_limit")
        );
        let mut workbook = operating_scenario();
        workbook.sheets[0].cells.get_mut("B1").unwrap().style = Some(Style {
            fill_color: Some("not a color".into()),
            ..Style::default()
        });
        assert_eq!(workbook.validate().unwrap_err().code, "invalid_style");
        let session = Session::new(operating_scenario()).unwrap();
        assert_eq!(
            session
                .apply(
                    vec![
                        Edit::value("Assumptions", "B1", CellValue::Number(20.0));
                        MAX_EDIT_BATCH + 1
                    ],
                    None
                )
                .unwrap_err()
                .code,
            "resource_limit"
        );
    }
    #[test]
    fn reversed_ranges_case_insensitivity_and_blank_dependencies() {
        let workbook = model(
            &[
                ("A1", number(1.0)),
                ("A2", number(2.0)),
                ("B1", Cell::formula("=SUM(data!A2:A1)+C1")),
            ],
            &[("out", "B1")],
        );
        let report = calculate(&workbook, 3);
        assert!(report.diagnostics.is_empty());
        assert_eq!(report.outputs["out"], scalar(3.0));
        assert_eq!(
            report.values["Data!C1"],
            CalculatedValue::Scalar(CellValue::Blank)
        );
    }
}
