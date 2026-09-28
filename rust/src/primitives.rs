//! Workbook-free spreadsheet functions and inspectable native composition.
//!
//! Values compile directly into this crate's evaluator AST. No workbook, cell
//! address, formula text generation, Python bridge, or alternate arithmetic
//! evaluator is involved. Compatibility claims retain the evaluator's profile.
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::io::{self, BufRead, Write};

use serde_json::{Map, Value as Json, json};

use crate::{ArrayValue, CalcValue, Environment, Expr, FormulaError, FormulaResult, Value};

const MAX_NODES: usize = 1024;
const MAX_DEPTH: usize = 64;
const MAX_INPUTS: usize = 256;
const MAX_ELEMENTS: usize = 100_000;
pub const MAX_INTERCHANGE_BYTES: usize = 1024 * 1024;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PrimitiveError {
    pub code: String,
    pub message: String,
}
impl PrimitiveError {
    fn new(code: &str, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }
}
impl std::fmt::Display for PrimitiveError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}
impl std::error::Error for PrimitiveError {}
type Result<T> = std::result::Result<T, PrimitiveError>;
fn invalid(message: impl Into<String>) -> PrimitiveError {
    PrimitiveError::new("invalid_expression", message)
}
fn resource(message: impl Into<String>) -> PrimitiveError {
    PrimitiveError::new("resource_limit", message)
}

/// A range and an array retain separate evaluator roles, even when their shapes
/// and elements match. Omitted is an argument marker, distinct from Blank.
#[derive(Clone, Debug, PartialEq)]
pub enum PrimitiveValue {
    Scalar(Value),
    Range(Vec<Vec<Value>>),
    Array(Vec<Vec<Value>>),
    Omitted,
}
impl serde::Serialize for PrimitiveValue {
    fn serialize<S: serde::Serializer>(
        &self,
        serializer: S,
    ) -> std::result::Result<S::Ok, S::Error> {
        use serde::ser::{SerializeMap, SerializeSeq};
        validate_value(self, "invalid_input", &mut 0).map_err(serde::ser::Error::custom)?;
        struct Scalar<'a>(&'a Value);
        impl serde::Serialize for Scalar<'_> {
            fn serialize<S: serde::Serializer>(
                &self,
                s: S,
            ) -> std::result::Result<S::Ok, S::Error> {
                match self.0 {
                    Value::Number(n) => s.serialize_f64(*n),
                    Value::Text(v) => s.serialize_str(v),
                    Value::Bool(v) => s.serialize_bool(*v),
                    Value::Blank => s.serialize_none(),
                    Value::Error(error) => {
                        let mut map = s.serialize_map(Some(1))?;
                        map.serialize_entry(
                            "error",
                            &error.excel_code().ok_or_else(|| {
                                serde::ser::Error::custom("unsupported error value")
                            })?,
                        )?;
                        map.end()
                    }
                }
            }
        }
        struct Rows<'a>(&'a [Vec<Value>]);
        impl serde::Serialize for Rows<'_> {
            fn serialize<S: serde::Serializer>(
                &self,
                s: S,
            ) -> std::result::Result<S::Ok, S::Error> {
                struct Row<'a>(&'a [Value]);
                impl serde::Serialize for Row<'_> {
                    fn serialize<S: serde::Serializer>(
                        &self,
                        s: S,
                    ) -> std::result::Result<S::Ok, S::Error> {
                        let mut seq = s.serialize_seq(Some(self.0.len()))?;
                        for value in self.0 {
                            seq.serialize_element(&Scalar(value))?;
                        }
                        seq.end()
                    }
                }
                let mut seq = s.serialize_seq(Some(self.0.len()))?;
                for row in self.0 {
                    seq.serialize_element(&Row(row))?;
                }
                seq.end()
            }
        }
        match self {
            Self::Scalar(value) => Scalar(value).serialize(serializer),
            Self::Range(rows) | Self::Array(rows) => {
                let mut map = serializer.serialize_map(Some(1))?;
                map.serialize_entry(
                    if matches!(self, Self::Range(_)) {
                        "range"
                    } else {
                        "array"
                    },
                    &Rows(rows),
                )?;
                map.end()
            }
            Self::Omitted => Err(serde::ser::Error::custom(
                "omitted arguments have no VALUE representation",
            )),
        }
    }
}
impl From<Value> for PrimitiveValue {
    fn from(value: Value) -> Self {
        Self::Scalar(value)
    }
}
impl From<f64> for PrimitiveValue {
    fn from(value: f64) -> Self {
        Self::Scalar(Value::Number(value))
    }
}
impl From<bool> for PrimitiveValue {
    fn from(value: bool) -> Self {
        Self::Scalar(Value::Bool(value))
    }
}
impl From<String> for PrimitiveValue {
    fn from(value: String) -> Self {
        Self::Scalar(Value::Text(value))
    }
}
impl From<&str> for PrimitiveValue {
    fn from(value: &str) -> Self {
        Self::Scalar(Value::Text(value.into()))
    }
}
impl PrimitiveValue {
    pub fn from_json(value: &Json) -> Result<Self> {
        decode_value(value, "invalid_input")
    }
    pub fn to_json(&self) -> Result<Json> {
        validate_value(self, "invalid_input", &mut 0)?;
        serialized_limit(self)?;
        let value = value_json(self)?;
        byte_limit(&value)?;
        Ok(value)
    }
}
pub fn range_values(rows: Vec<Vec<Value>>) -> Result<PrimitiveValue> {
    let value = PrimitiveValue::Range(rows);
    validate_value(&value, "invalid_input", &mut 0)?;
    Ok(value)
}
pub fn array_values(rows: Vec<Vec<Value>>) -> Result<PrimitiveValue> {
    let value = PrimitiveValue::Array(rows);
    validate_value(&value, "invalid_input", &mut 0)?;
    Ok(value)
}
fn scalar_json(value: &Value) -> Json {
    match value {
        Value::Number(n) => json!(n),
        Value::Text(text) => json!(text),
        Value::Bool(value) => json!(value),
        Value::Blank => Json::Null,
        Value::Error(error) => {
            json!({"error":error.excel_code().expect("validated primitive error")})
        }
    }
}
fn value_json(value: &PrimitiveValue) -> Result<Json> {
    Ok(match value {
        PrimitiveValue::Scalar(value) => scalar_json(value),
        PrimitiveValue::Range(rows) => {
            json!({"range":rows.iter().map(|row|row.iter().map(scalar_json).collect::<Vec<_>>()).collect::<Vec<_>>()})
        }
        PrimitiveValue::Array(rows) => {
            json!({"array":rows.iter().map(|row|row.iter().map(scalar_json).collect::<Vec<_>>()).collect::<Vec<_>>()})
        }
        PrimitiveValue::Omitted => {
            return Err(invalid("omitted arguments have no VALUE representation"));
        }
    })
}
fn decode_scalar(value: &Json, code: &str) -> Result<Value> {
    Ok(match value {
        Json::Null => Value::Blank,
        Json::Bool(value) => Value::Bool(*value),
        Json::String(text) => Value::Text(text.clone()),
        Json::Number(number) => Value::Number(
            number
                .as_f64()
                .filter(|n| n.is_finite())
                .ok_or_else(|| PrimitiveError::new(code, "number must be finite"))?,
        ),
        Json::Object(object) if object.len() == 1 && object.contains_key("error") => {
            Value::Error(match object["error"].as_str() {
                Some("#VALUE!") => FormulaError::Value,
                Some("#DIV/0!") => FormulaError::Div0,
                Some("#REF!") => FormulaError::Ref,
                Some("#NAME?") => FormulaError::Name,
                Some("#NUM!") => FormulaError::Num,
                Some("#N/A") => FormulaError::NA,
                Some("#CALC!") => FormulaError::Calc,
                _ => return Err(PrimitiveError::new(code, "unsupported Excel error value")),
            })
        }
        _ => return Err(PrimitiveError::new(code, "expected a scalar value")),
    })
}
fn decode_value(value: &Json, code: &str) -> Result<PrimitiveValue> {
    let decoded = if let Some(object) = value.as_object() {
        if object.len() == 1 && (object.contains_key("range") || object.contains_key("array")) {
            let (kind, rows) = object.iter().next().unwrap();
            let rows = rows
                .as_array()
                .ok_or_else(|| PrimitiveError::new(code, "matrix rows must be arrays"))?;
            let mut result = Vec::new();
            let mut count = 0usize;
            for row in rows {
                let row = row
                    .as_array()
                    .ok_or_else(|| PrimitiveError::new(code, "matrix rows must be arrays"))?;
                count = count
                    .checked_add(row.len())
                    .filter(|count| *count <= MAX_ELEMENTS)
                    .ok_or_else(|| resource("matrix exceeds 100000 elements"))?;
                result.push(
                    row.iter()
                        .map(|value| decode_scalar(value, code))
                        .collect::<Result<Vec<_>>>()?,
                );
            }
            if kind == "range" {
                PrimitiveValue::Range(result)
            } else {
                PrimitiveValue::Array(result)
            }
        } else {
            PrimitiveValue::Scalar(decode_scalar(value, code)?)
        }
    } else {
        PrimitiveValue::Scalar(decode_scalar(value, code)?)
    };
    validate_value(&decoded, code, &mut 0)?;
    Ok(decoded)
}
fn validate_scalar(value: &Value, code: &str) -> Result<()> {
    match value {
        Value::Number(number) if !number.is_finite() => {
            Err(PrimitiveError::new(code, "number must be finite"))
        }
        Value::Text(text) if text.encode_utf16().count() > 32767 => {
            Err(resource("text exceeds 32767 UTF-16 code units"))
        }
        Value::Error(error) if error.excel_code().is_none() => Err(PrimitiveError::new(
            code,
            "only supported Excel error values are accepted",
        )),
        _ => Ok(()),
    }
}
fn validate_value(value: &PrimitiveValue, code: &str, elements: &mut usize) -> Result<()> {
    let count = match value {
        PrimitiveValue::Scalar(value) => {
            validate_scalar(value, code)?;
            1
        }
        PrimitiveValue::Range(rows) | PrimitiveValue::Array(rows) => {
            let columns = rows.first().map_or(0, Vec::len);
            if columns == 0 || rows.iter().any(|row| row.len() != columns) {
                return Err(PrimitiveError::new(
                    code,
                    "matrix must be nonempty and rectangular",
                ));
            }
            let count = rows
                .len()
                .checked_mul(columns)
                .filter(|count| *count <= MAX_ELEMENTS)
                .ok_or_else(|| resource("matrix exceeds 100000 elements"))?;
            for value in rows.iter().flatten() {
                validate_scalar(value, code)?;
            }
            count
        }
        PrimitiveValue::Omitted => 0,
    };
    *elements = elements
        .checked_add(count)
        .filter(|count| *count <= MAX_ELEMENTS)
        .ok_or_else(|| resource("expression values exceed 100000 cumulative elements"))?;
    Ok(())
}
fn native(value: &PrimitiveValue) -> Result<Expr> {
    Ok(match value {
        PrimitiveValue::Scalar(value) => Expr::Native(CalcValue::Scalar(value.clone())),
        PrimitiveValue::Range(rows) => Expr::Native(CalcValue::Range(rows.clone())),
        PrimitiveValue::Array(rows) => Expr::Native(CalcValue::Array(
            ArrayValue::new(rows.clone()).map_err(|e| invalid(e.to_string()))?,
        )),
        PrimitiveValue::Omitted => Expr::Missing,
    })
}
#[derive(Clone, Debug, PartialEq)]
enum Node {
    Literal(PrimitiveValue),
    Input(String),
    Call(String, Vec<Node>),
    Binary(String, Box<Node>, Box<Node>),
    Omitted,
}
impl serde::Serialize for Node {
    fn serialize<S: serde::Serializer>(
        &self,
        serializer: S,
    ) -> std::result::Result<S::Ok, S::Error> {
        use serde::ser::SerializeMap;
        let mut map = serializer.serialize_map(Some(match self {
            Self::Omitted => 1,
            Self::Literal(_) | Self::Input(_) => 2,
            Self::Call(_, _) => 3,
            Self::Binary(_, _, _) => 4,
        }))?;
        match self {
            Self::Literal(value) => {
                map.serialize_entry("kind", "literal")?;
                map.serialize_entry("value", value)?;
            }
            Self::Input(name) => {
                map.serialize_entry("kind", "input")?;
                map.serialize_entry("name", name)?;
            }
            Self::Omitted => {
                map.serialize_entry("kind", "omitted")?;
            }
            Self::Call(function, arguments) => {
                map.serialize_entry("kind", "call")?;
                map.serialize_entry("function", function)?;
                map.serialize_entry("arguments", arguments)?;
            }
            Self::Binary(operator, left, right) => {
                map.serialize_entry("kind", "binary")?;
                map.serialize_entry("operator", operator)?;
                map.serialize_entry("left", left)?;
                map.serialize_entry("right", right)?;
            }
        }
        map.end()
    }
}
#[derive(Clone, Debug, PartialEq)]
pub struct Expression {
    node: Node,
}
impl Expression {
    pub fn input(name: impl Into<String>) -> Result<Self> {
        Self::checked(Node::Input(name.into()), false)
    }
    pub fn literal(value: impl Into<PrimitiveValue>) -> Result<Self> {
        let value = value.into();
        validate_value(&value, "invalid_input", &mut 0)?;
        if value == PrimitiveValue::Omitted {
            return Ok(Self::omitted());
        }
        Self::checked(Node::Literal(value), false)
    }
    pub fn omitted() -> Self {
        Self {
            node: Node::Omitted,
        }
    }
    pub fn call(function: &str, arguments: Vec<Self>) -> Result<Self> {
        let function = normalize_function(function)?;
        Self::checked(
            Node::Call(
                function,
                arguments.into_iter().map(|arg| arg.node).collect(),
            ),
            false,
        )
    }
    pub fn binary(operator: &str, left: Self, right: Self) -> Result<Self> {
        Self::checked(
            Node::Binary(operator.into(), Box::new(left.node), Box::new(right.node)),
            false,
        )
    }
    fn checked(node: Node, allow_root_omitted: bool) -> Result<Self> {
        let expression = Self { node };
        expression.metadata(allow_root_omitted)?;
        expression.check_envelope_size()?;
        Ok(expression)
    }
    pub fn to_dict(&self) -> Result<Json> {
        self.metadata(false)?;
        self.check_envelope_size()?;
        let document = self.envelope();
        byte_limit(&document)?;
        Ok(document)
    }
    pub fn from_dict(document: &Json) -> Result<Self> {
        let envelope = document
            .as_object()
            .ok_or_else(|| invalid("expression envelope must be an object"))?;
        exact(envelope, &["schema_version", "expression"])?;
        if envelope["schema_version"].as_u64() != Some(1) {
            return Err(invalid("schema_version must be integer 1"));
        }
        preflight_json_tree(&envelope["expression"])?;
        let node = decode_node(&envelope["expression"])?;
        byte_limit(document)?;
        Self::checked(node, false)
    }
    fn check_envelope_size(&self) -> Result<()> {
        #[derive(serde::Serialize)]
        struct Envelope<'a> {
            schema_version: u32,
            expression: &'a Node,
        }
        serialized_limit(&Envelope {
            schema_version: 1,
            expression: &self.node,
        })
    }
    fn envelope(&self) -> Json {
        json!({"schema_version":1,"expression":node_json(&self.node)})
    }
    pub fn inspect(&self) -> Result<Json> {
        let (inputs, operations) = self.metadata(false)?;
        let report = json!({"schema_version":1,"profile":"spreadsheet-primitives-v1","expression":node_json(&self.node),"inputs":inputs,"operations":operations});
        byte_limit(&report)?;
        Ok(report)
    }
    fn metadata(&self, allow_root_omitted: bool) -> Result<(BTreeSet<String>, BTreeSet<String>)> {
        let mut inputs = BTreeSet::new();
        let mut operations = BTreeSet::new();
        let mut pending = vec![(&self.node, 1usize, allow_root_omitted)];
        let mut count = 0usize;
        let mut elements = 0usize;
        while let Some((node, depth, allow_omitted)) = pending.pop() {
            count += 1;
            if count > MAX_NODES || depth > MAX_DEPTH {
                return Err(resource("expression exceeds 1024 nodes or depth 64"));
            }
            match node {
                Node::Literal(value) => validate_value(value, "invalid_expression", &mut elements)?,
                Node::Input(name) => {
                    if !input_name(name) {
                        return Err(invalid(
                            "input names must be ASCII identifiers of at most 64 bytes",
                        ));
                    }
                    inputs.insert(name.clone());
                    if inputs.len() > MAX_INPUTS {
                        return Err(resource("expression exceeds 256 distinct inputs"));
                    }
                }
                Node::Call(function, arguments) => {
                    check_arity(function, arguments.len())?;
                    operations.insert(format!("excel.{function}"));
                    pending.extend(arguments.iter().rev().map(|node| (node, depth + 1, true)));
                }
                Node::Binary(operator, left, right) => {
                    operations.insert(operator_id(operator)?.into());
                    pending.push((right, depth + 1, false));
                    pending.push((left, depth + 1, false));
                }
                Node::Omitted if !allow_omitted => {
                    return Err(invalid(
                        "omitted values are allowed only as immediate call arguments",
                    ));
                }
                Node::Omitted => {}
            }
        }
        Ok((inputs, operations))
    }
    pub fn evaluate(&self, inputs: &BTreeMap<String, PrimitiveValue>) -> Result<FormulaResult> {
        let (names, _) = self.metadata(false)?;
        if inputs.keys().collect::<BTreeSet<_>>() != names.iter().collect() {
            return Err(PrimitiveError::new(
                "invalid_input",
                "all declared inputs are required and unknown inputs are rejected",
            ));
        }
        for value in inputs.values() {
            if *value == PrimitiveValue::Omitted {
                return Err(PrimitiveError::new(
                    "invalid_input",
                    "an input value cannot be omitted",
                ));
            }
            validate_value(value, "invalid_input", &mut 0)?;
        }
        let mut elements = 0;
        let mut pending = vec![&self.node];
        while let Some(node) = pending.pop() {
            match node {
                Node::Literal(value) => validate_value(value, "invalid_expression", &mut elements)?,
                Node::Input(name) => validate_value(&inputs[name], "invalid_input", &mut elements)?,
                Node::Call(_, arguments) => pending.extend(arguments),
                Node::Binary(_, left, right) => {
                    pending.push(left);
                    pending.push(right);
                }
                Node::Omitted => {}
            }
        }
        check_evaluation_size(&self.node, inputs)?;
        let mut elements = 0;
        let expression = compile(&self.node, inputs, &mut elements)?;
        evaluate_ast(&expression)
    }
}
fn input_name(name: &str) -> bool {
    (1..=64).contains(&name.len())
        && name
            .as_bytes()
            .first()
            .is_some_and(|b| b.is_ascii_alphabetic() || *b == b'_')
        && name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
}
fn normalize_function(function: &str) -> Result<String> {
    if !function.is_ascii() {
        return Err(PrimitiveError::new(
            "unsupported_operation",
            "unsupported primitive function",
        ));
    }
    let name = function.to_ascii_uppercase();
    if !matches!(
        name.as_str(),
        "SUM" | "AVERAGE" | "MIN" | "MAX" | "COUNT" | "IF" | "IFERROR" | "ROUND" | "ABS"
    ) {
        return Err(PrimitiveError::new(
            "unsupported_operation",
            "unsupported primitive function",
        ));
    }
    Ok(name)
}
fn check_arity(function: &str, arguments: usize) -> Result<()> {
    let name = normalize_function(function)?;
    let (min, max) = match name.as_str() {
        "SUM" | "AVERAGE" | "MIN" | "MAX" | "COUNT" => (1, 255),
        "IF" => (2, 3),
        "IFERROR" | "ROUND" => (2, 2),
        "ABS" => (1, 1),
        _ => unreachable!(),
    };
    if !(min..=max).contains(&arguments) {
        return Err(invalid(format!(
            "{function} requires {min}..{max} arguments"
        )));
    }
    Ok(())
}
fn operator_id(operator: &str) -> Result<&'static str> {
    Ok(match operator {
        "+" => "excel.operator.add",
        "-" => "excel.operator.subtract",
        "*" => "excel.operator.multiply",
        "/" => "excel.operator.divide",
        "=" => "excel.operator.equal",
        "<>" => "excel.operator.not_equal",
        "<" => "excel.operator.less_than",
        "<=" => "excel.operator.less_equal",
        ">" => "excel.operator.greater_than",
        ">=" => "excel.operator.greater_equal",
        _ => {
            return Err(PrimitiveError::new(
                "unsupported_operation",
                "unsupported primitive operator",
            ));
        }
    })
}
fn exact(object: &Map<String, Json>, keys: &[&str]) -> Result<()> {
    if object.len() != keys.len() || keys.iter().any(|key| !object.contains_key(*key)) {
        return Err(invalid("expression object has missing or unknown fields"));
    }
    Ok(())
}
fn preflight_json_tree(node: &Json) -> Result<()> {
    let mut pending = vec![(node, 1usize, false)];
    let mut count = 0;
    while let Some((node, depth, allow_omitted)) = pending.pop() {
        count += 1;
        if count > MAX_NODES || depth > MAX_DEPTH {
            return Err(resource("expression exceeds 1024 nodes or depth 64"));
        }
        let object = node
            .as_object()
            .ok_or_else(|| invalid("expression node must be an object"))?;
        match object.get("kind").and_then(Json::as_str) {
            Some("literal") => exact(object, &["kind", "value"])?,
            Some("input") => exact(object, &["kind", "name"])?,
            Some("omitted") => {
                exact(object, &["kind"])?;
                if !allow_omitted {
                    return Err(invalid(
                        "omitted values are allowed only as immediate call arguments",
                    ));
                }
            }
            Some("call") => {
                exact(object, &["kind", "function", "arguments"])?;
                let args = object["arguments"]
                    .as_array()
                    .ok_or_else(|| invalid("arguments must be an array"))?;
                pending.extend(args.iter().rev().map(|node| (node, depth + 1, true)));
            }
            Some("binary") => {
                exact(object, &["kind", "operator", "left", "right"])?;
                pending.push((&object["right"], depth + 1, false));
                pending.push((&object["left"], depth + 1, false));
            }
            _ => return Err(invalid("unknown expression node kind")),
        }
    }
    Ok(())
}
fn decode_node(node: &Json) -> Result<Node> {
    Ok(match node["kind"].as_str().expect("preflight node kind") {
        "literal" => Node::Literal(decode_value(&node["value"], "invalid_expression")?),
        "input" => Node::Input(
            node["name"]
                .as_str()
                .ok_or_else(|| invalid("input name must be text"))?
                .into(),
        ),
        "omitted" => Node::Omitted,
        "call" => Node::Call(
            normalize_function(
                node["function"]
                    .as_str()
                    .ok_or_else(|| invalid("function name must be text"))?,
            )?,
            node["arguments"]
                .as_array()
                .unwrap()
                .iter()
                .map(decode_node)
                .collect::<Result<Vec<_>>>()?,
        ),
        "binary" => Node::Binary(
            node["operator"]
                .as_str()
                .ok_or_else(|| invalid("operator must be text"))?
                .into(),
            Box::new(decode_node(&node["left"])?),
            Box::new(decode_node(&node["right"])?),
        ),
        _ => unreachable!(),
    })
}
fn node_json(node: &Node) -> Json {
    match node {
        Node::Literal(value) => {
            json!({"kind":"literal","value":value_json(value).expect("validated literal value")})
        }
        Node::Input(name) => json!({"kind":"input","name":name}),
        Node::Omitted => json!({"kind":"omitted"}),
        Node::Call(function, arguments) => {
            json!({"kind":"call","function":function,"arguments":arguments.iter().map(node_json).collect::<Vec<_>>()})
        }
        Node::Binary(operator, left, right) => {
            json!({"kind":"binary","operator":operator,"left":node_json(left),"right":node_json(right)})
        }
    }
}
fn compile(
    node: &Node,
    inputs: &BTreeMap<String, PrimitiveValue>,
    elements: &mut usize,
) -> Result<Expr> {
    Ok(match node {
        Node::Literal(value) => {
            validate_value(value, "invalid_expression", elements)?;
            native(value)?
        }
        Node::Input(name) => {
            let value = &inputs[name];
            validate_value(value, "invalid_input", elements)?;
            native(value)?
        }
        Node::Omitted => Expr::Missing,
        Node::Call(function, arguments) => Expr::Call(
            function.clone(),
            arguments
                .iter()
                .map(|node| compile(node, inputs, elements))
                .collect::<Result<Vec<_>>>()?,
        ),
        Node::Binary(operator, left, right) => Expr::Binary(
            operator.clone(),
            Box::new(compile(left, inputs, elements)?),
            Box::new(compile(right, inputs, elements)?),
        ),
    })
}
fn normalized_scalar(value: Value) -> Value {
    match value {
        Value::Number(n) if !n.is_finite() => Value::Error(FormulaError::Num),
        value => value,
    }
}
fn evaluate_ast(expression: &Expr) -> Result<FormulaResult> {
    let cells = HashMap::new();
    let environment = Environment {
        cells: &cells,
        sheet_name: "",
        wildcard_work_remaining: std::cell::Cell::new(crate::MAX_WILDCARD_WORK),
    };
    match crate::eval(expression, &environment) {
        Ok(CalcValue::Scalar(value)) => Ok(FormulaResult::Scalar(normalized_scalar(value))),
        Ok(CalcValue::Range(rows)) => Ok(FormulaResult::Array(
            ArrayValue::new(
                rows.into_iter()
                    .map(|row| row.into_iter().map(normalized_scalar).collect())
                    .collect(),
            )
            .map_err(|error| invalid(error.to_string()))?,
        )),
        Ok(CalcValue::Array(array)) => Ok(FormulaResult::Array(
            ArrayValue::new(
                array
                    .rows
                    .into_iter()
                    .map(|row| row.into_iter().map(normalized_scalar).collect())
                    .collect(),
            )
            .map_err(|error| invalid(error.to_string()))?,
        )),
        Err(error) if error.excel_code().is_some() => {
            Ok(FormulaResult::Scalar(Value::Error(error)))
        }
        Err(error) => Err(PrimitiveError::new(
            "unsupported_operation",
            error.to_string(),
        )),
    }
}
/// Invoke one supported function on native values, preserving argument roles.
pub fn call(function: &str, arguments: &[PrimitiveValue]) -> Result<FormulaResult> {
    let name = normalize_function(function)?;
    check_arity(&name, arguments.len())?;
    let mut count = 0;
    for value in arguments {
        validate_value(value, "invalid_input", &mut count)?;
    }
    // Count the same envelope as composition before copying native argument data.
    #[derive(serde::Serialize)]
    #[serde(tag = "kind", rename_all = "lowercase")]
    enum Argument<'a> {
        Literal { value: &'a PrimitiveValue },
        Omitted,
    }
    #[derive(serde::Serialize)]
    struct Call<'a> {
        kind: &'static str,
        function: &'a str,
        arguments: Vec<Argument<'a>>,
    }
    let node = Call {
        kind: "call",
        function: &name,
        arguments: arguments
            .iter()
            .map(|value| match value {
                PrimitiveValue::Omitted => Argument::Omitted,
                value => Argument::Literal { value },
            })
            .collect(),
    };
    check_evaluation_size(&node, &BTreeMap::new())?;
    let args = arguments.iter().map(native).collect::<Result<Vec<_>>>()?;
    evaluate_ast(&Expr::Call(name, args))
}
macro_rules! direct_functions {
    ($($rust:ident => $excel:literal),* $(,)?)=>{$(pub fn $rust(arguments:&[PrimitiveValue])->Result<FormulaResult>{call($excel,arguments)})*};
}
direct_functions!(sum=>"SUM",average=>"AVERAGE",min=>"MIN",max=>"MAX",count=>"COUNT",if_=>"IF",iferror=>"IFERROR",round=>"ROUND",abs=>"ABS");
pub fn primitive_catalog() -> Json {
    serde_json::from_str(include_str!("primitive_catalog.json"))
        .expect("embedded primitive catalog must be valid JSON")
}
/// Return the canonical expression schema without a filesystem dependency.
pub fn primitive_expression_schema() -> Json {
    serde_json::from_str(include_str!("primitive_expression.schema.json"))
        .expect("embedded primitive expression schema must be valid JSON")
}
pub fn result_json(result: &FormulaResult) -> Result<Json> {
    match result {
        FormulaResult::Scalar(value) => validate_scalar(value, "invalid_input")?,
        FormulaResult::Array(array) => {
            for value in array.rows().iter().flatten() {
                validate_scalar(value, "invalid_input")?;
            }
        }
    }
    let value = match result {
        FormulaResult::Scalar(value) => scalar_json(value),
        FormulaResult::Array(array) => {
            json!({"array":array.rows().iter().map(|row|row.iter().map(scalar_json).collect::<Vec<_>>()).collect::<Vec<_>>()})
        }
    };
    byte_limit(&value)?;
    Ok(value)
}
fn check_evaluation_size<T: serde::Serialize>(
    expression: &T,
    inputs: &BTreeMap<String, PrimitiveValue>,
) -> Result<()> {
    #[derive(serde::Serialize)]
    struct Envelope<'a, T> {
        schema_version: u32,
        expression: &'a T,
    }
    #[derive(serde::Serialize)]
    struct Payload<'a, T> {
        expression: Envelope<'a, T>,
        inputs: &'a BTreeMap<String, PrimitiveValue>,
    }
    serialized_limit(&Payload {
        expression: Envelope {
            schema_version: 1,
            expression,
        },
        inputs,
    })
}
fn byte_limit(value: &Json) -> Result<()> {
    serialized_limit(value)
}
fn serialized_limit<T: serde::Serialize>(value: &T) -> Result<()> {
    struct Counter {
        bytes: usize,
        exceeded: bool,
    }
    impl Write for Counter {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            self.bytes = self.bytes.saturating_add(bytes.len());
            if self.bytes > MAX_INTERCHANGE_BYTES {
                self.exceeded = true;
                return Err(io::Error::other("interchange byte limit"));
            }
            Ok(bytes.len())
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }
    let mut count = Counter {
        bytes: 0,
        exceeded: false,
    };
    if let Err(error) = serde_json::to_writer(&mut count, value) {
        if count.exceeded {
            return Err(resource("primitive interchange exceeds 1 MiB"));
        }
        return Err(invalid(error.to_string()));
    }
    Ok(())
}

/// Handle one conformance request. This is a deterministic transport surface,
/// not an agent framework or a formula-text parser.
pub fn handle_contract(request: &Json) -> Json {
    let response = (|| -> Result<Json> {
        let object = request
            .as_object()
            .ok_or_else(|| invalid("request must be an object"))?;
        exact(object, &["expression", "inputs"])?;
        // Validate the expression's node depth before traversing JSON recursively.
        let expression = Expression::from_dict(&object["expression"])?;
        let values = object["inputs"]
            .as_object()
            .ok_or_else(|| PrimitiveError::new("invalid_input", "inputs must be an object"))?;
        let inputs = values
            .iter()
            .map(|(name, value)| {
                PrimitiveValue::from_json(value).map(|value| (name.clone(), value))
            })
            .collect::<Result<BTreeMap<_, _>>>()?;
        byte_limit(request)?;
        let response = json!({"ok":true,"value":result_json(&expression.evaluate(&inputs)?)?,"inspection":expression.inspect()?});
        byte_limit(&response)?;
        Ok(response)
    })();
    response.unwrap_or_else(error_json)
}
fn error_json(error: PrimitiveError) -> Json {
    json!({"ok":false,"error":{"code":error.code,"message":error.message.chars().take(4096).collect::<String>()}})
}
/// Read bounded JSON lines; malformed or oversized input cannot consume the next
/// request or leave a half-written response.
pub fn serve_contract<R: BufRead, W: Write>(mut input: R, mut output: W) -> io::Result<()> {
    loop {
        let mut line = Vec::new();
        let mut overflow = false;
        let mut received = false;
        loop {
            let buffer = input.fill_buf()?;
            if buffer.is_empty() {
                break;
            }
            received = true;
            let newline = buffer.iter().position(|byte| *byte == b'\n');
            let length = newline.unwrap_or(buffer.len());
            if !overflow {
                if line.len().saturating_add(length) > MAX_INTERCHANGE_BYTES {
                    overflow = true;
                    line.clear();
                } else {
                    line.extend_from_slice(&buffer[..length]);
                }
            }
            input.consume(length + usize::from(newline.is_some()));
            if newline.is_some() {
                break;
            }
        }
        if !received {
            break;
        }
        let response = if overflow {
            error_json(resource("request exceeds 1 MiB"))
        } else {
            match bounded_json(&line) {
                Ok(request) => handle_contract(&request),
                Err(error) => error_json(error),
            }
        };
        serde_json::to_writer(&mut output, &response)?;
        output.write_all(b"\n")?;
        output.flush()?;
    }
    Ok(())
}
fn bounded_json(bytes: &[u8]) -> Result<Json> {
    // Raw structural depth protects serde and Value destruction. Braces inside
    // escaped strings are data and do not count toward the bound.
    let mut depth = 0usize;
    let mut quoted = false;
    let mut escape = false;
    for byte in bytes {
        if quoted {
            if escape {
                escape = false;
            } else if *byte == b'\\' {
                escape = true;
            } else if *byte == b'"' {
                quoted = false;
            }
        } else {
            match byte {
                b'"' => quoted = true,
                b'{' | b'[' => {
                    depth += 1;
                    if depth > 2 * MAX_DEPTH + 8 {
                        return Err(resource("JSON nesting exceeds the primitive profile"));
                    }
                }
                b'}' | b']' => depth = depth.saturating_sub(1),
                _ => {}
            }
        }
    }
    let mut decoder = serde_json::Deserializer::from_slice(bytes);
    decoder.disable_recursion_limit();
    let value = <Json as serde::Deserialize>::deserialize(&mut decoder)
        .map_err(|error| invalid(error.to_string()))?;
    decoder.end().map_err(|error| invalid(error.to_string()))?;
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn number(result: Result<FormulaResult>) -> f64 {
        match result.unwrap() {
            FormulaResult::Scalar(Value::Number(value)) => value,
            result => panic!("expected number, got {result:?}"),
        }
    }
    fn error_value(result: Result<FormulaResult>, error: FormulaError) {
        assert_eq!(result.unwrap(), FormulaResult::Scalar(Value::Error(error)));
    }
    fn expression(node: Json) -> Json {
        json!({"schema_version":1,"expression":node})
    }
    fn literal(value: Json) -> Json {
        json!({"kind":"literal","value":value})
    }
    fn call_node(function: &str, args: Vec<Json>) -> Json {
        json!({"kind":"call","function":function,"arguments":args})
    }
    #[test]
    fn all_nine_direct_functions_use_native_values_and_legacy_coercion() {
        let values = [
            PrimitiveValue::from(2.0),
            PrimitiveValue::from(4.0),
            PrimitiveValue::from(true),
            PrimitiveValue::from("9"),
        ];
        assert_eq!(number(sum(&values)), 6.0);
        assert_eq!(number(average(&values)), 3.0);
        assert_eq!(number(min(&values)), 2.0);
        assert_eq!(number(max(&values)), 4.0);
        assert_eq!(number(count(&values)), 2.0);
        assert_eq!(
            number(if_(&[
                true.into(),
                5.0.into(),
                PrimitiveValue::Scalar(Value::Error(FormulaError::Div0))
            ])),
            5.0
        );
        assert_eq!(
            number(iferror(&[
                PrimitiveValue::Scalar(Value::Error(FormulaError::NA)),
                7.0.into()
            ])),
            7.0
        );
        assert_eq!(number(round(&[1.235.into(), 2.0.into()])), 1.24);
        assert_eq!(number(abs(&[(-3.0).into()])), 3.0);
        error_value(
            average(&[PrimitiveValue::Scalar(Value::Blank)]),
            FormulaError::Div0,
        );
        error_value(sum(&[PrimitiveValue::Omitted]), FormulaError::Value);
        assert_eq!(number(sum(&[PrimitiveValue::Scalar(Value::Blank)])), 0.0);
        assert_eq!(sum(&[]).unwrap_err().code, "invalid_expression");
    }
    #[test]
    fn direct_calls_enforce_the_combined_interchange_byte_limit() {
        let text = PrimitiveValue::from("x".repeat(32767));
        let mut arguments = vec![text.clone(); 31];
        assert_eq!(number(count(&arguments)), 0.0);
        arguments.push(text);
        assert_eq!(count(&arguments).unwrap_err().code, "resource_limit");
        arguments.push(f64::NAN.into());
        assert_eq!(count(&arguments).unwrap_err().code, "invalid_input");
    }
    #[test]
    fn arrays_ranges_and_error_values_keep_distinct_roles() {
        let range = range_values(vec![vec![Value::Number(-3.0)]]).unwrap();
        let array = array_values(vec![vec![Value::Number(-3.0)]]).unwrap();
        assert_eq!(number(abs(std::slice::from_ref(&range))), 3.0);
        error_value(abs(&[array]), FormulaError::Value);
        let expression = Expression::literal(range).unwrap();
        assert_eq!(
            result_json(&expression.evaluate(&BTreeMap::new()).unwrap()).unwrap(),
            json!({"array":[[-3.0]]})
        );
        assert!(range_values(Vec::new()).is_err());
        assert!(array_values(vec![vec![Value::Blank], vec![]]).is_err());
        error_value(
            sum(&[PrimitiveValue::Scalar(Value::Error(FormulaError::Ref))]),
            FormulaError::Ref,
        );
        error_value(sum(&[f64::MAX.into(), f64::MAX.into()]), FormulaError::Num);
    }
    #[test]
    fn composition_is_lazy_and_requires_every_declared_input() {
        let division = Expression::binary(
            "/",
            Expression::literal(1.0).unwrap(),
            Expression::literal(0.0).unwrap(),
        )
        .unwrap();
        let choice = Expression::call(
            "IF",
            vec![
                Expression::literal(true).unwrap(),
                Expression::input("price").unwrap(),
                division.clone(),
            ],
        )
        .unwrap();
        assert_eq!(
            number(choice.evaluate(&BTreeMap::from([("price".into(), 12.0.into())]))),
            12.0
        );
        let caught =
            Expression::call("IFERROR", vec![division, Expression::literal(7.0).unwrap()]).unwrap();
        assert_eq!(number(caught.evaluate(&BTreeMap::new())), 7.0);
        let hidden_input = Expression::call(
            "IF",
            vec![
                Expression::literal(true).unwrap(),
                Expression::literal(1.0).unwrap(),
                Expression::input("unused").unwrap(),
            ],
        )
        .unwrap();
        assert_eq!(
            hidden_input.evaluate(&BTreeMap::new()).unwrap_err().code,
            "invalid_input"
        );
        assert_eq!(
            choice
                .evaluate(&BTreeMap::from([
                    ("price".into(), 12.0.into()),
                    ("extra".into(), 1.0.into())
                ]))
                .unwrap_err()
                .code,
            "invalid_input"
        );
        assert_eq!(
            choice
                .evaluate(&BTreeMap::from([("Price".into(), 12.0.into())]))
                .unwrap_err()
                .code,
            "invalid_input"
        );
        assert_eq!(
            Expression::binary(
                "+",
                Expression::omitted(),
                Expression::literal(1.0).unwrap()
            )
            .unwrap_err()
            .code,
            "invalid_expression"
        );
        assert_eq!(
            Expression::omitted()
                .evaluate(&BTreeMap::new())
                .unwrap_err()
                .code,
            "invalid_expression"
        );
    }
    #[test]
    fn binary_rules_lift_arrays_and_preserve_excel_errors() {
        let input = Expression::literal(
            array_values(vec![vec![Value::Number(1.0), Value::Number(2.0)]]).unwrap(),
        )
        .unwrap();
        let result = Expression::binary("+", input, Expression::literal(true).unwrap())
            .unwrap()
            .evaluate(&BTreeMap::new())
            .unwrap();
        assert_eq!(result_json(&result).unwrap(), json!({"array":[[2.0,3.0]]}));
        let comparison = Expression::binary(
            "=",
            Expression::literal("abc").unwrap(),
            Expression::literal("ABC").unwrap(),
        )
        .unwrap();
        assert_eq!(
            comparison.evaluate(&BTreeMap::new()).unwrap(),
            FormulaResult::Scalar(Value::Bool(true))
        );
        let mixed = Expression::binary(
            "<",
            Expression::literal("abc").unwrap(),
            Expression::literal(1.0).unwrap(),
        )
        .unwrap();
        error_value(mixed.evaluate(&BTreeMap::new()), FormulaError::Value);
    }
    #[test]
    fn metadata_roundtrip_and_validation_have_stable_operation_identity() {
        let document = expression(call_node(
            "sUm",
            vec![json!({"kind":"input","name":"values"}), literal(json!(2))],
        ));
        let parsed = Expression::from_dict(&document).unwrap();
        let encoded = parsed.to_dict().unwrap();
        assert_eq!(encoded["expression"]["function"], "SUM");
        assert_eq!(Expression::from_dict(&encoded).unwrap(), parsed);
        assert_eq!(
            parsed.inspect().unwrap()["operations"],
            json!(["excel.SUM"])
        );
        assert_eq!(parsed.inspect().unwrap()["inputs"], json!(["values"]));
        assert_eq!(primitive_catalog()["profile"], "spreadsheet-primitives-v1");
        for document in [
            json!({"schema_version":1,"expression":literal(json!(1)),"extra":true}),
            expression(json!({"kind":"input","name":"has space"})),
            expression(json!({"kind":"omitted"})),
            expression(literal(json!({"array":[[1],[2,3]]}))),
            expression(literal(json!({"error":"#VALUE!","extra":true}))),
        ] {
            assert_eq!(
                Expression::from_dict(&document).unwrap_err().code,
                "invalid_expression"
            );
        }
        assert_eq!(
            Expression::from_dict(&expression(call_node("UNKNOWN", vec![literal(json!(1))])))
                .unwrap_err()
                .code,
            "unsupported_operation"
        );
        assert_eq!(
            Expression::literal(f64::NAN).unwrap_err().code,
            "invalid_input"
        );
        assert_eq!(
            Expression::literal("x".repeat(32768)).unwrap_err().code,
            "resource_limit"
        );
    }
    #[test]
    fn direct_evaluation_bounds_repeated_inputs_and_combined_payload() {
        let expression = Expression::call(
            "SUM",
            vec![
                Expression::input("values").unwrap(),
                Expression::input("values").unwrap(),
            ],
        )
        .unwrap();
        let values = range_values(vec![vec![Value::Number(1.0); 50001]]).unwrap();
        assert_eq!(
            expression
                .evaluate(&BTreeMap::from([("values".into(), values)]))
                .unwrap_err()
                .code,
            "resource_limit"
        );
        let text = Value::Text("x".repeat(32000));
        let literal = range_values(vec![vec![text.clone(); 20]]).unwrap();
        let binding = range_values(vec![vec![text; 20]]).unwrap();
        let expression = Expression::call(
            "SUM",
            vec![
                Expression::literal(literal).unwrap(),
                Expression::input("values").unwrap(),
            ],
        )
        .unwrap();
        assert!(
            serde_json::to_vec(&expression.to_dict().unwrap())
                .unwrap()
                .len()
                < MAX_INTERCHANGE_BYTES
        );
        assert!(
            serde_json::to_vec(&binding.to_json().unwrap())
                .unwrap()
                .len()
                < MAX_INTERCHANGE_BYTES
        );
        assert_eq!(
            expression
                .evaluate(&BTreeMap::from([("values".into(), binding)]))
                .unwrap_err()
                .code,
            "resource_limit"
        );
    }
    #[test]
    fn node_depth_and_input_limits_preflight_without_recursive_overrun() {
        let mut node = literal(json!(-1));
        for _ in 0..63 {
            node = call_node("ABS", vec![node]);
        }
        let deepest = expression(node.clone());
        assert_eq!(
            number(
                Expression::from_dict(&deepest)
                    .unwrap()
                    .evaluate(&BTreeMap::new())
            ),
            1.0
        );
        assert_eq!(
            Expression::from_dict(&expression(call_node("ABS", vec![node])))
                .unwrap_err()
                .code,
            "resource_limit"
        );
        let inputs = (0..257)
            .map(|i| json!({"kind":"input","name":format!("i{i}")}))
            .collect::<Vec<_>>();
        let nested = expression(call_node(
            "SUM",
            vec![
                call_node("SUM", inputs[..255].to_vec()),
                call_node("SUM", inputs[255..].to_vec()),
            ],
        ));
        assert_eq!(
            Expression::from_dict(&nested).unwrap_err().code,
            "resource_limit"
        );
        let wide = expression(call_node(
            "SUM",
            vec![call_node("SUM", vec![literal(json!(1)); 255]); 5],
        ));
        assert_eq!(
            Expression::from_dict(&wide).unwrap_err().code,
            "resource_limit"
        );
    }
    #[test]
    fn transport_handles_depth64_and_recovers_after_malformed_or_oversized_lines() {
        let mut node = literal(json!(-1));
        for _ in 0..63 {
            node = call_node("ABS", vec![node]);
        }
        let request = json!({"expression":expression(node),"inputs":{}});
        let mut input = serde_json::to_vec(&request).unwrap();
        input.push(b'\n');
        input.extend_from_slice(b"{bad}\n");
        input.extend(std::iter::repeat_n(b'x', MAX_INTERCHANGE_BYTES + 1));
        input.push(b'\n');
        input.extend_from_slice(
            &serde_json::to_vec(&json!({"expression":expression(literal(json!(5))),"inputs":{}}))
                .unwrap(),
        );
        let mut output = Vec::new();
        serve_contract(std::io::Cursor::new(input), &mut output).unwrap();
        let reports = output
            .split(|byte| *byte == b'\n')
            .filter(|line| !line.is_empty())
            .map(|line| bounded_json(line).unwrap())
            .collect::<Vec<_>>();
        assert_eq!(reports.len(), 4);
        assert_eq!(reports[0]["value"], 1.0);
        assert_eq!(reports[1]["error"]["code"], "invalid_expression");
        assert_eq!(reports[2]["error"]["code"], "resource_limit");
        assert_eq!(reports[3]["value"], 5.0);
    }
    #[test]
    fn known_excel_prefixes_dispatch_and_map_without_rewriting_formulas() {
        let cells = HashMap::from([
            ("A1".into(), Value::Number(1.0)),
            ("A2".into(), Value::Number(2.0)),
            ("B1".into(), Value::Number(10.0)),
            ("B2".into(), Value::Number(20.0)),
        ]);
        assert_eq!(
            crate::evaluate("=_xlfn.XLOOKUP(2,A1:A2,B1:B2)", &cells, "S").unwrap(),
            Value::Number(20.0)
        );
        assert_eq!(
            crate::evaluate("=SUM(_xlfn._xlws.FILTER(B1:B2,A1:A2>1))", &cells, "S").unwrap(),
            Value::Number(20.0)
        );
        let formula = "=_xlfn.XLOOKUP(2,A1:A2,B1:B2)";
        let analysis = crate::toolkit::analyze_formula(formula).unwrap();
        assert_eq!(analysis["formula"], formula);
        assert_eq!(analysis["functions"], json!(["XLOOKUP"]));
        assert!(
            crate::toolkit::copy_formula(formula, 1, 0)
                .unwrap()
                .contains("_XLFN.XLOOKUP")
        );
        assert_eq!(
            crate::toolkit::analyze_formula("=_xlfn.NOT_REAL(1)").unwrap()["functions"],
            json!(["_XLFN.NOT_REAL"])
        );
        assert!(crate::evaluate("=_xlws._xlfn.XLOOKUP(2,A1:A2,B1:B2)", &cells, "S").is_err());
    }
}
