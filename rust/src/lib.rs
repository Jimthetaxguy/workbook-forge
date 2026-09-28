//! A deliberately bounded Excel formula evaluator and programmable workbook toolkit.
//!
//! This crate focuses on scalar values, A1 references/ranges, common operators,
//! and a small set of frequently used worksheet functions. Unsupported syntax
//! is reported rather than guessed.

pub mod toolkit;
pub mod xlsx;
pub use toolkit::{
    CalculationReport, CellAddress, CellValue, Edit, InputBinding, Session, Sheet, Style,
    ToolkitError, WorkbookModel, analyze_formula, calculate, copy_formula, inspect,
    operating_scenario, validate_inputs,
};

use std::cell::Cell;
use std::collections::{HashMap, HashSet};
use std::fmt;

const MAX_EXCEL_DATE_SERIAL: i64 = 2_958_465;
const MAX_EXACT_INTEGER: f64 = 9_007_199_254_740_992.0;
const MAX_EXACT_INTEGER_U64: u64 = 9_007_199_254_740_992;
const MAX_ARRAY_CELLS: usize = 100_000;
const MAX_TEXT_LENGTH_UNITS: usize = 32_767;
const MAX_FORMULA_LENGTH_UNITS: usize = 8_192;
const MAX_FUNCTION_NESTING: usize = 64;
const MAX_EXPRESSION_NESTING: usize = 96;
const MAX_WILDCARD_WORK: usize = 5_000_000;

#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    Number(f64),
    Text(String),
    Bool(bool),
    Blank,
    Error(FormulaError),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum FormulaError {
    Value,
    Div0,
    Ref,
    Name,
    Num,
    NA,
    Calc,
    Unsupported(String),
    Parse(String),
}

impl FormulaError {
    pub fn excel_code(&self) -> Option<&'static str> {
        match self {
            Self::Value => Some("#VALUE!"),
            Self::Div0 => Some("#DIV/0!"),
            Self::Ref => Some("#REF!"),
            Self::Name => Some("#NAME?"),
            Self::Num => Some("#NUM!"),
            Self::NA => Some("#N/A"),
            Self::Calc => Some("#CALC!"),
            Self::Unsupported(_) | Self::Parse(_) => None,
        }
    }
}

impl fmt::Display for FormulaError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Value => write!(f, "#VALUE!"),
            Self::Div0 => write!(f, "#DIV/0!"),
            Self::Ref => write!(f, "#REF!"),
            Self::Name => write!(f, "#NAME?"),
            Self::Num => write!(f, "#NUM!"),
            Self::NA => write!(f, "#N/A"),
            Self::Calc => write!(f, "#CALC!"),
            Self::Unsupported(reason) => write!(f, "unsupported formula: {reason}"),
            Self::Parse(reason) => write!(f, "formula parse error: {reason}"),
        }
    }
}

impl std::error::Error for FormulaError {}

/// A bounded, rectangular result produced by a formula or materialized range.
#[derive(Clone, Debug, PartialEq)]
pub struct ArrayValue {
    rows: Vec<Vec<Value>>,
    columns: usize,
}

impl ArrayValue {
    fn new(rows: Vec<Vec<Value>>) -> Result<Self, FormulaError> {
        let Some(first_row) = rows.first() else {
            return Err(FormulaError::Value);
        };
        let columns = first_row.len();
        if columns == 0 || rows.iter().any(|row| row.len() != columns) {
            return Err(FormulaError::Value);
        }
        if rows.len().saturating_mul(columns) > MAX_ARRAY_CELLS {
            return Err(FormulaError::Num);
        }
        Ok(Self { rows, columns })
    }

    pub fn rows(&self) -> &[Vec<Value>] {
        &self.rows
    }

    pub fn row_count(&self) -> usize {
        self.rows.len()
    }

    pub fn column_count(&self) -> usize {
        self.columns
    }
}

/// A scalar or shaped formula result. Array values do not imply worksheet spills.
#[derive(Clone, Debug, PartialEq)]
pub enum FormulaResult {
    Scalar(Value),
    Array(ArrayValue),
}

/// Evaluate an Excel-style formula and preserve any rectangular result shape.
///
/// `cells` uses `A1` keys for the current sheet and `Sheet!A1` keys for
/// explicitly qualified references. Missing cells evaluate as blank. Function
/// names and sheet matching are case-insensitive. This returns formula values;
/// it does not project dynamic arrays into worksheet spill cells.
pub fn evaluate_result(
    formula: &str,
    cells: &HashMap<String, Value>,
    sheet_name: &str,
) -> Result<FormulaResult, FormulaError> {
    let mut parser = Parser::new(formula)?;
    let expression = parser.parse_expression(0)?;
    if parser.peek().is_some() {
        return Err(FormulaError::Parse("unexpected trailing input".into()));
    }
    let env = Environment {
        cells,
        sheet_name,
        wildcard_work_remaining: Cell::new(MAX_WILDCARD_WORK),
    };
    match eval(&expression, &env)? {
        CalcValue::Scalar(value) => {
            validate_text_value(&value)?;
            match value {
                Value::Error(error) => Err(error),
                value => Ok(FormulaResult::Scalar(value)),
            }
        }
        CalcValue::Range(rows) => {
            for value in rows.iter().flatten() {
                validate_text_value(value)?;
            }
            Ok(FormulaResult::Array(ArrayValue::new(rows)?))
        }
        CalcValue::Array(value) => {
            for item in value.rows().iter().flatten() {
                validate_text_value(item)?;
            }
            Ok(FormulaResult::Array(value))
        }
    }
}

/// Evaluate a scalar formula; use [`evaluate_result`] to preserve array shape.
pub fn evaluate(
    formula: &str,
    cells: &HashMap<String, Value>,
    sheet_name: &str,
) -> Result<Value, FormulaError> {
    match evaluate_result(formula, cells, sheet_name)? {
        FormulaResult::Scalar(value) => Ok(value),
        FormulaResult::Array(_) => Err(FormulaError::Value),
    }
}

#[derive(Clone, Debug, PartialEq)]
enum Expr {
    Number(f64, String), // parsed binary64 value and original numeric token
    Text(String),
    Bool(bool),
    Error(FormulaError),
    Missing,
    Ref(String, String),           // sheet, A1
    Range(String, String, String), // sheet, start, end
    Unary(char, Box<Expr>),
    Binary(String, Box<Expr>, Box<Expr>),
    Call(String, Vec<Expr>),
}

#[derive(Clone, Debug, PartialEq)]
enum CalcValue {
    Scalar(Value),
    Range(Vec<Vec<Value>>),
    Array(ArrayValue),
}

struct Environment<'a> {
    cells: &'a HashMap<String, Value>,
    sheet_name: &'a str,
    wildcard_work_remaining: Cell<usize>,
}

#[derive(Clone, Debug, PartialEq)]
enum Token {
    Number(f64, String), // parsed binary64 value and original numeric token
    Text(String),
    ErrorNA,
    Ident(String),
    QuotedSheet(String),
    Operator(String),
    LParen,
    RParen,
    Comma,
    Colon,
    Bang,
    Eof,
}

struct Lexer<'a> {
    chars: Vec<char>,
    pos: usize,
    _source: &'a str,
}

impl<'a> Lexer<'a> {
    fn new(source: &'a str) -> Self {
        Self {
            chars: source.chars().collect(),
            pos: 0,
            _source: source,
        }
    }

    fn next(&mut self) -> Result<Token, FormulaError> {
        while self.peek_char().is_some_and(char::is_whitespace) {
            self.pos += 1;
        }
        let Some(ch) = self.peek_char() else {
            return Ok(Token::Eof);
        };
        if ch == '"' {
            return self.read_string();
        }
        if ch == '\'' {
            return self.read_quoted_sheet();
        }
        if ch == '#' {
            let literal: String = self.chars[self.pos..].iter().take(4).collect();
            if literal.eq_ignore_ascii_case("#N/A") {
                self.pos += 4;
                return Ok(Token::ErrorNA);
            }
            return Err(FormulaError::Unsupported(
                "only the #N/A error literal is currently supported".into(),
            ));
        }
        if ch.is_ascii_digit() || (ch == '.' && self.peek_n(1).is_some_and(|c| c.is_ascii_digit()))
        {
            return self.read_number();
        }
        if ch.is_ascii_alphabetic() || ch == '_' || ch == '$' {
            return Ok(Token::Ident(self.read_ident()));
        }
        self.pos += 1;
        match ch {
            '(' => Ok(Token::LParen),
            ')' => Ok(Token::RParen),
            ',' => Ok(Token::Comma),
            ':' => Ok(Token::Colon),
            '!' => Ok(Token::Bang),
            '+' | '-' | '*' | '/' | '^' | '&' | '=' | '<' | '>' => {
                let mut op = ch.to_string();
                if matches!(ch, '<' | '>')
                    && self.peek_char().is_some_and(|c| matches!(c, '=' | '>'))
                {
                    op.push(self.peek_char().unwrap());
                    self.pos += 1;
                }
                Ok(Token::Operator(op))
            }
            '%' => Ok(Token::Operator("%".into())),
            _ => Err(FormulaError::Unsupported(format!("character {ch:?}"))),
        }
    }

    fn peek_char(&self) -> Option<char> {
        self.chars.get(self.pos).copied()
    }
    fn peek_n(&self, n: usize) -> Option<char> {
        self.chars.get(self.pos + n).copied()
    }

    fn read_string(&mut self) -> Result<Token, FormulaError> {
        self.pos += 1;
        let mut out = String::new();
        while let Some(ch) = self.peek_char() {
            self.pos += 1;
            if ch == '"' {
                if self.peek_char() == Some('"') {
                    out.push('"');
                    self.pos += 1;
                } else {
                    return Ok(Token::Text(out));
                }
            } else {
                out.push(ch);
            }
        }
        Err(FormulaError::Parse("unterminated string literal".into()))
    }

    fn read_quoted_sheet(&mut self) -> Result<Token, FormulaError> {
        self.pos += 1;
        let mut out = String::new();
        while let Some(ch) = self.peek_char() {
            self.pos += 1;
            if ch == '\'' {
                if self.peek_char() == Some('\'') {
                    out.push('\'');
                    self.pos += 1;
                } else {
                    return Ok(Token::QuotedSheet(out));
                }
            } else {
                out.push(ch);
            }
        }
        Err(FormulaError::Parse("unterminated sheet name".into()))
    }

    fn read_number(&mut self) -> Result<Token, FormulaError> {
        let start = self.pos;
        while self
            .peek_char()
            .is_some_and(|c| c.is_ascii_digit() || c == '.')
        {
            self.pos += 1;
        }
        if self.peek_char().is_some_and(|c| c == 'e' || c == 'E') {
            self.pos += 1;
            if self.peek_char().is_some_and(|c| c == '+' || c == '-') {
                self.pos += 1;
            }
            while self.peek_char().is_some_and(|c| c.is_ascii_digit()) {
                self.pos += 1;
            }
        }
        let text: String = self.chars[start..self.pos].iter().collect();
        let plain_integer = text.bytes().all(|byte| byte.is_ascii_digit());
        let value = match text.parse::<f64>() {
            Ok(value) => value,
            Err(_) if plain_integer => f64::INFINITY,
            Err(_) => return Err(FormulaError::Parse(format!("invalid number {text}"))),
        };
        Ok(Token::Number(value, text))
    }

    fn read_ident(&mut self) -> String {
        let start = self.pos;
        while self
            .peek_char()
            .is_some_and(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '$'))
        {
            self.pos += 1;
        }
        self.chars[start..self.pos].iter().collect()
    }
}

struct Parser {
    tokens: Vec<Token>,
    pos: usize,
}

impl Parser {
    fn new(formula: &str) -> Result<Self, FormulaError> {
        let source = formula.trim().strip_prefix('=').unwrap_or(formula.trim());
        if source
            .encode_utf16()
            .take(MAX_FORMULA_LENGTH_UNITS + 1)
            .count()
            > MAX_FORMULA_LENGTH_UNITS
        {
            return Err(FormulaError::Value);
        }
        let mut lexer = Lexer::new(source);
        let mut tokens = Vec::new();
        loop {
            let token = lexer.next()?;
            let done = token == Token::Eof;
            tokens.push(token);
            if done {
                break;
            }
        }
        let parser = Self { tokens, pos: 0 };
        parser.validate_nesting()?;
        Ok(parser)
    }

    fn validate_nesting(&self) -> Result<(), FormulaError> {
        let mut stack = Vec::new();
        let mut function_depth = 0;
        for (index, token) in self.tokens.iter().enumerate() {
            match token {
                Token::LParen => {
                    if stack.len() >= MAX_EXPRESSION_NESTING {
                        return Err(FormulaError::Value);
                    }
                    let is_function =
                        index > 0 && matches!(&self.tokens[index - 1], Token::Ident(_));
                    if is_function {
                        function_depth += 1;
                        if function_depth > MAX_FUNCTION_NESTING {
                            return Err(FormulaError::Value);
                        }
                    }
                    stack.push(is_function);
                }
                Token::RParen => {
                    if let Some(is_function) = stack.pop()
                        && is_function
                    {
                        function_depth -= 1;
                    }
                }
                _ => {}
            }
        }
        Ok(())
    }

    fn peek(&self) -> Option<&Token> {
        self.tokens.get(self.pos).filter(|t| **t != Token::Eof)
    }
    fn take(&mut self) -> Token {
        let t = self.tokens.get(self.pos).cloned().unwrap_or(Token::Eof);
        self.pos += 1;
        t
    }

    fn parse_expression(&mut self, min_bp: u8) -> Result<Expr, FormulaError> {
        let mut lhs = match self.take() {
            Token::Number(n, source) => Expr::Number(n, source),
            Token::Text(s) => Expr::Text(s),
            Token::ErrorNA => Expr::Error(FormulaError::NA),
            Token::Operator(op) if op == "+" || op == "-" => {
                let mut operators = vec![op.chars().next().unwrap()];
                while matches!(self.peek(), Some(Token::Operator(next)) if next == "+" || next == "-")
                {
                    let Token::Operator(next) = self.take() else {
                        unreachable!();
                    };
                    operators.push(next.chars().next().unwrap());
                }
                let mut operand = self.parse_expression(27)?;
                for operator in operators.into_iter().rev() {
                    operand = Expr::Unary(operator, Box::new(operand));
                }
                operand
            }
            Token::LParen => {
                let e = self.parse_expression(0)?;
                self.expect(Token::RParen)?;
                e
            }
            Token::Ident(name) if name.eq_ignore_ascii_case("TRUE") => Expr::Bool(true),
            Token::Ident(name) if name.eq_ignore_ascii_case("FALSE") => Expr::Bool(false),
            Token::Ident(name) | Token::QuotedSheet(name) => self.parse_name_or_call(name)?,
            other => {
                return Err(FormulaError::Parse(format!(
                    "expected value, found {other:?}"
                )));
            }
        };

        loop {
            if self.peek() == Some(&Token::Operator("%".into())) {
                if 30 < min_bp {
                    break;
                }
                self.take();
                lhs = Expr::Unary('%', Box::new(lhs));
                continue;
            }
            let Some((op, left_bp, right_bp)) = self.infix_binding() else {
                break;
            };
            if left_bp < min_bp {
                break;
            }
            self.take();
            let rhs = self.parse_expression(right_bp)?;
            if op == ":" {
                lhs = match (lhs, rhs) {
                    (Expr::Ref(s1, a), Expr::Ref(s2, b)) => {
                        if !s1.is_empty() && !s2.is_empty() && !s1.eq_ignore_ascii_case(&s2) {
                            return Err(FormulaError::Unsupported(
                                "range endpoints must refer to the same worksheet".into(),
                            ));
                        }
                        let sheet = if s1.is_empty() { s2 } else { s1 };
                        Expr::Range(sheet, a, b)
                    }
                    _ => {
                        return Err(FormulaError::Unsupported(
                            "range endpoints must be A1 references on one sheet".into(),
                        ));
                    }
                };
            } else {
                lhs = Expr::Binary(op, Box::new(lhs), Box::new(rhs));
            }
        }
        Ok(lhs)
    }

    fn infix_binding(&self) -> Option<(String, u8, u8)> {
        match self.peek()? {
            Token::Colon => Some((":".into(), 35, 36)),
            Token::Operator(op) => match op.as_str() {
                "=" | "<>" | "<" | "<=" | ">" | ">=" => Some((op.clone(), 5, 6)),
                "&" => Some((op.clone(), 10, 11)),
                "+" | "-" => Some((op.clone(), 15, 16)),
                "*" | "/" => Some((op.clone(), 20, 21)),
                // Excel treats negation as higher precedence than exponentiation,
                // and groups repeated exponent operators left-to-right.
                "^" => Some((op.clone(), 26, 27)),
                _ => None,
            },
            _ => None,
        }
    }

    fn parse_name_or_call(&mut self, mut name: String) -> Result<Expr, FormulaError> {
        let sheet_qualifier = if self.peek() == Some(&Token::Bang) {
            self.take();
            Some(name.clone())
        } else {
            None
        };
        if let Some(sheet) = sheet_qualifier {
            let address = match self.take() {
                Token::Ident(s) => s,
                other => {
                    return Err(FormulaError::Parse(format!(
                        "expected cell reference after !, found {other:?}"
                    )));
                }
            };
            validate_cell_ref(&address)?;
            return Ok(Expr::Ref(sheet, address));
        }
        if self.peek() == Some(&Token::LParen) {
            self.take();
            let mut args = Vec::new();
            if self.peek() != Some(&Token::RParen) {
                loop {
                    if matches!(self.peek(), Some(Token::Comma | Token::RParen)) {
                        args.push(Expr::Missing);
                    } else {
                        args.push(self.parse_expression(0)?);
                    }
                    if self.peek() != Some(&Token::Comma) {
                        break;
                    }
                    self.take();
                    if self.peek() == Some(&Token::RParen) {
                        args.push(Expr::Missing);
                        break;
                    }
                }
            }
            self.expect(Token::RParen)?;
            return Ok(Expr::Call(name.to_ascii_uppercase(), args));
        }
        // An unqualified identifier is an A1 reference if it has the expected shape.
        name.make_ascii_uppercase();
        validate_cell_ref(&name)?;
        Ok(Expr::Ref(String::new(), name))
    }

    fn expect(&mut self, expected: Token) -> Result<(), FormulaError> {
        let actual = self.take();
        if actual == expected {
            Ok(())
        } else {
            Err(FormulaError::Parse(format!(
                "expected {expected:?}, found {actual:?}"
            )))
        }
    }
}

fn validate_cell_ref(address: &str) -> Result<(), FormulaError> {
    let clean = address.replace('$', "");
    let split = clean
        .find(|c: char| c.is_ascii_digit())
        .ok_or_else(|| FormulaError::Unsupported(format!("name/reference {address}")))?;
    let (col, row) = clean.split_at(split);
    if col.is_empty()
        || !col.chars().all(|c| c.is_ascii_alphabetic())
        || row.is_empty()
        || !row.chars().all(|c| c.is_ascii_digit())
        || row == "0"
    {
        return Err(FormulaError::Unsupported(format!(
            "only A1 cell references are supported: {address}"
        )));
    }
    Ok(())
}

fn eval(expr: &Expr, env: &Environment<'_>) -> Result<CalcValue, FormulaError> {
    match expr {
        Expr::Number(n, _) if n.is_finite() => Ok(scalar(Value::Number(*n))),
        Expr::Number(_, _) => Err(FormulaError::Num),
        Expr::Text(s) => Ok(scalar(Value::Text(s.clone()))),
        Expr::Bool(b) => Ok(scalar(Value::Bool(*b))),
        Expr::Error(error) => Ok(scalar(Value::Error(error.clone()))),
        Expr::Missing => Err(FormulaError::Value),
        Expr::Ref(sheet, addr) => Ok(scalar(read_cell(env, sheet, addr))),
        Expr::Range(sheet, start, end) => Ok(CalcValue::Range(read_range(env, sheet, start, end)?)),
        Expr::Unary(_, _) => {
            let mut operations = Vec::new();
            let mut current = expr;
            while let Expr::Unary(operator, inner) = current {
                operations.push(*operator);
                current = inner;
            }
            let value = eval_scalar(current, env)?;
            let mut number = to_number(&value)?;
            for operator in operations.into_iter().rev() {
                number = match operator {
                    '+' => number,
                    '-' => -number,
                    '%' => number / 100.0,
                    _ => {
                        return Err(FormulaError::Unsupported(format!(
                            "unary operator {operator}"
                        )));
                    }
                };
            }
            Ok(scalar(Value::Number(number)))
        }
        Expr::Binary(_, _, _) => {
            let mut chain = Vec::new();
            let mut current = expr;
            while let Expr::Binary(operator, left, right) = current {
                chain.push((operator.as_str(), right.as_ref()));
                current = left;
            }
            let mut value = eval(current, env);
            for (operator, right) in chain.into_iter().rev() {
                let right_value = eval(right, env);
                value = apply_binary_results(operator, value, right_value);
            }
            value
        }
        Expr::Call(name, args) if name == "SEQUENCE" => {
            sequence_call(args, env).map(CalcValue::Array)
        }
        Expr::Call(name, args) if name == "FILTER" => filter_call(args, env),
        Expr::Call(name, args) if name == "SORT" => sort_call(args, env),
        Expr::Call(name, args) if name == "UNIQUE" => unique_call(args, env),
        Expr::Call(name, args)
            if matches!(name.as_str(), "IF" | "IFERROR" | "IFNA" | "IFS" | "SWITCH") =>
        {
            selection_call(name, args, env)
        }
        Expr::Call(name, args) => eval_call(name, args, env).map(scalar),
    }
}

fn apply_binary_results(
    op: &str,
    left_value: Result<CalcValue, FormulaError>,
    right_value: Result<CalcValue, FormulaError>,
) -> Result<CalcValue, FormulaError> {
    match (left_value, right_value) {
        (Ok(CalcValue::Scalar(Value::Error(error))), Ok(right))
            if calc_value_shape(&right).is_none() =>
        {
            Ok(scalar(Value::Error(error)))
        }
        (Ok(CalcValue::Scalar(Value::Error(error))), Err(_)) => Ok(scalar(Value::Error(error))),
        (Ok(left), Ok(CalcValue::Scalar(Value::Error(error))))
            if calc_value_shape(&left).is_none()
                && !matches!(op, "=" | "<>" | "<" | "<=" | ">" | ">=") =>
        {
            Ok(scalar(Value::Error(error)))
        }
        (Ok(left), Ok(right)) => apply_binary_values(op, left, right),
        (Err(error), Ok(right @ (CalcValue::Range(_) | CalcValue::Array(_)))) => {
            apply_binary_values(op, scalar(Value::Error(error)), right)
        }
        (Ok(left @ (CalcValue::Range(_) | CalcValue::Array(_))), Err(error)) => {
            apply_binary_values(op, left, scalar(Value::Error(error)))
        }
        (Err(error), _) => Err(error),
        (_, Err(error)) => Err(error),
    }
}

fn scalar(value: Value) -> CalcValue {
    CalcValue::Scalar(value)
}

fn calc_value_shape(value: &CalcValue) -> Option<(usize, usize)> {
    match value {
        CalcValue::Scalar(_) => None,
        CalcValue::Range(rows) => Some((rows.len(), rows.first().map_or(0, Vec::len))),
        CalcValue::Array(array) => Some((array.row_count(), array.column_count())),
    }
}

fn calc_value_matrix(value: CalcValue) -> Vec<Vec<Value>> {
    match value {
        CalcValue::Scalar(value) => vec![vec![value]],
        CalcValue::Range(rows) => rows,
        CalcValue::Array(array) => array.rows,
    }
}

fn result_matrix(value: CalcValue) -> Result<Vec<Vec<Value>>, FormulaError> {
    if let CalcValue::Scalar(Value::Error(error)) = &value {
        return Err(error.clone());
    }
    Ok(calc_value_matrix(value))
}

fn validate_finite_array_numbers(rows: &[Vec<Value>]) -> Result<(), FormulaError> {
    if rows
        .iter()
        .flatten()
        .any(|value| matches!(value, Value::Number(number) if !number.is_finite()))
    {
        return Err(FormulaError::Num);
    }
    Ok(())
}

fn optional_scalar_argument(
    expression: Option<&Expr>,
    env: &Environment<'_>,
) -> Result<Option<Value>, FormulaError> {
    let Some(expression) = expression else {
        return Ok(None);
    };
    if matches!(expression, Expr::Missing) {
        return Ok(None);
    }
    match eval(expression, env)? {
        CalcValue::Scalar(Value::Error(error)) => Err(error),
        CalcValue::Scalar(value) => Ok(Some(value)),
        CalcValue::Range(_) | CalcValue::Array(_) => Err(FormulaError::Value),
    }
}

fn integer_selector(value: &Value) -> Result<i64, FormulaError> {
    let Value::Number(number) = value else {
        return Err(FormulaError::Value);
    };
    if !number.is_finite()
        || number.fract() != 0.0
        || *number < i64::MIN as f64
        || *number > i64::MAX as f64
    {
        return Err(FormulaError::Value);
    }
    Ok(*number as i64)
}

#[derive(Clone, Debug)]
enum SortKey {
    Number(f64),
    Text(String),
}

fn sort_key(value: &Value) -> Result<SortKey, FormulaError> {
    match value {
        Value::Number(number) if number.is_finite() => Ok(SortKey::Number(*number)),
        Value::Number(_) => Err(FormulaError::Num),
        Value::Text(text) => Ok(SortKey::Text(text.to_ascii_lowercase())),
        Value::Bool(_) | Value::Blank | Value::Error(_) => Err(FormulaError::Value),
    }
}

fn compare_sort_keys(left: &SortKey, right: &SortKey) -> std::cmp::Ordering {
    match (left, right) {
        (SortKey::Number(left), SortKey::Number(right)) => left
            .partial_cmp(right)
            .expect("SORT rejects non-finite numbers before comparison"),
        (SortKey::Text(left), SortKey::Text(right)) => left.cmp(right),
        _ => unreachable!("SORT validates one key type before comparison"),
    }
}

fn sort_call(args: &[Expr], env: &Environment<'_>) -> Result<CalcValue, FormulaError> {
    arity("SORT", args, 1, 4)?;
    let matrix = result_matrix(eval(&args[0], env)?)?;
    let sort_index = optional_scalar_argument(args.get(1), env)?.unwrap_or(Value::Number(1.0));
    let sort_order = optional_scalar_argument(args.get(2), env)?.unwrap_or(Value::Number(1.0));
    let by_col = optional_scalar_argument(args.get(3), env)?.unwrap_or(Value::Bool(false));
    let sort_index = integer_selector(&sort_index)?;
    let sort_order = integer_selector(&sort_order)?;
    if !matches!(sort_order, 1 | -1) {
        return Err(FormulaError::Value);
    }
    let by_col = match by_col {
        Value::Bool(value) => value,
        _ => return Err(FormulaError::Value),
    };

    let rows = matrix.len();
    let columns = matrix.first().map_or(0, Vec::len);
    let key_limit = if by_col { rows } else { columns };
    if sort_index < 1 || sort_index as usize > key_limit {
        return Err(FormulaError::Value);
    }
    let keys: Vec<&Value> = if by_col {
        matrix[sort_index as usize - 1].iter().collect()
    } else {
        matrix
            .iter()
            .map(|row| &row[sort_index as usize - 1])
            .collect()
    };
    if let Some(Value::Error(error)) = keys
        .iter()
        .copied()
        .find(|value| matches!(value, Value::Error(_)))
    {
        return Err(error.clone());
    }
    let mut parsed_keys = Vec::with_capacity(keys.len());
    let mut key_kind = None;
    for value in keys {
        let key = sort_key(value)?;
        let is_number = matches!(key, SortKey::Number(_));
        if key_kind.is_some_and(|previous_kind| previous_kind != is_number) {
            return Err(FormulaError::Value);
        }
        key_kind = Some(is_number);
        parsed_keys.push(key);
    }
    let keys = parsed_keys;

    let mut order: Vec<usize> = (0..keys.len()).collect();
    order.sort_by(|left, right| {
        let ordering = compare_sort_keys(&keys[*left], &keys[*right]);
        if sort_order == -1 {
            ordering.reverse()
        } else {
            ordering
        }
    });
    let result: Vec<Vec<Value>> = if by_col {
        matrix
            .iter()
            .map(|row| order.iter().map(|index| row[*index].clone()).collect())
            .collect()
    } else {
        order
            .into_iter()
            .map(|index| matrix[index].clone())
            .collect()
    };
    validate_finite_array_numbers(&result)?;
    ArrayValue::new(result).map(CalcValue::Array)
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
enum UniqueCellKey {
    Number(u64),
    AsciiText(String),
    UnicodeText(String),
    Bool(bool),
    Blank,
}

fn unique_cell_key(value: &Value) -> Result<UniqueCellKey, FormulaError> {
    match value {
        Value::Number(number) if number.is_finite() => {
            let canonical = if *number == 0.0 { 0.0 } else { *number };
            Ok(UniqueCellKey::Number(canonical.to_bits()))
        }
        Value::Number(_) => Err(FormulaError::Num),
        Value::Text(text) if text.is_ascii() => {
            Ok(UniqueCellKey::AsciiText(text.to_ascii_lowercase()))
        }
        Value::Text(text) => Ok(UniqueCellKey::UnicodeText(text.clone())),
        Value::Bool(value) => Ok(UniqueCellKey::Bool(*value)),
        Value::Blank => Ok(UniqueCellKey::Blank),
        Value::Error(error) => Err(error.clone()),
    }
}

fn unique_call(args: &[Expr], env: &Environment<'_>) -> Result<CalcValue, FormulaError> {
    arity("UNIQUE", args, 1, 3)?;
    let matrix = result_matrix(eval(&args[0], env)?)?;
    let by_col = optional_scalar_argument(args.get(1), env)?.unwrap_or(Value::Bool(false));
    let exactly_once = optional_scalar_argument(args.get(2), env)?.unwrap_or(Value::Bool(false));
    let by_col = match by_col {
        Value::Bool(value) => value,
        _ => return Err(FormulaError::Value),
    };
    let exactly_once = match exactly_once {
        Value::Bool(value) => value,
        _ => return Err(FormulaError::Value),
    };
    for row in &matrix {
        for value in row {
            if let Value::Error(error) = value {
                return Err(error.clone());
            }
        }
    }

    let record_count = if by_col {
        matrix.first().map_or(0, Vec::len)
    } else {
        matrix.len()
    };
    let mut record_keys: Vec<Vec<UniqueCellKey>> = Vec::with_capacity(record_count);
    let mut counts: HashMap<Vec<UniqueCellKey>, usize> = HashMap::new();
    for record_index in 0..record_count {
        let record = if by_col {
            matrix
                .iter()
                .map(|row| &row[record_index])
                .collect::<Vec<_>>()
        } else {
            matrix[record_index].iter().collect::<Vec<_>>()
        };
        let key = record
            .into_iter()
            .map(unique_cell_key)
            .collect::<Result<Vec<_>, _>>()?;
        *counts.entry(key.clone()).or_default() += 1;
        record_keys.push(key);
    }
    let mut seen: HashSet<Vec<UniqueCellKey>> = HashSet::new();
    let selected: Vec<usize> = record_keys
        .iter()
        .enumerate()
        .filter_map(|(index, key)| {
            if exactly_once {
                (counts.get(key) == Some(&1)).then_some(index)
            } else {
                seen.insert(key.clone()).then_some(index)
            }
        })
        .collect();
    if selected.is_empty() {
        return Err(FormulaError::Calc);
    }
    let result: Vec<Vec<Value>> = if by_col {
        matrix
            .iter()
            .map(|row| selected.iter().map(|index| row[*index].clone()).collect())
            .collect()
    } else {
        selected
            .into_iter()
            .map(|index| matrix[index].clone())
            .collect()
    };
    validate_finite_array_numbers(&result)?;
    ArrayValue::new(result).map(CalcValue::Array)
}

fn apply_binary_values(
    op: &str,
    left: CalcValue,
    right: CalcValue,
) -> Result<CalcValue, FormulaError> {
    let left_shape = calc_value_shape(&left);
    let right_shape = calc_value_shape(&right);
    if left_shape.is_none() && right_shape.is_none() {
        let (CalcValue::Scalar(left), CalcValue::Scalar(right)) = (left, right) else {
            unreachable!();
        };
        return eval_binary(op, left, right).map(scalar);
    }
    if left_shape.is_some() && right_shape.is_some() && left_shape != right_shape {
        return Err(FormulaError::Value);
    }

    let (rows, columns) = left_shape.or(right_shape).unwrap();
    let left_scalar = matches!(&left, CalcValue::Scalar(_));
    let right_scalar = matches!(&right, CalcValue::Scalar(_));
    let left_matrix = calc_value_matrix(left);
    let right_matrix = calc_value_matrix(right);
    let left_broadcast = left_scalar.then(|| left_matrix[0][0].clone());
    let right_broadcast = right_scalar.then(|| right_matrix[0][0].clone());

    let mut result = Vec::with_capacity(rows);
    for row in 0..rows {
        let mut output_row = Vec::with_capacity(columns);
        for column in 0..columns {
            let left_value = left_broadcast
                .as_ref()
                .cloned()
                .unwrap_or_else(|| left_matrix[row][column].clone());
            let right_value = right_broadcast
                .as_ref()
                .cloned()
                .unwrap_or_else(|| right_matrix[row][column].clone());
            output_row.push(eval_binary(op, left_value, right_value).unwrap_or_else(Value::Error));
        }
        result.push(output_row);
    }
    ArrayValue::new(result).map(CalcValue::Array)
}

fn filter_call(args: &[Expr], env: &Environment<'_>) -> Result<CalcValue, FormulaError> {
    arity("FILTER", args, 2, 3)?;
    let source = eval(&args[0], env)?;
    if let CalcValue::Scalar(Value::Error(error)) = &source {
        return Err(error.clone());
    }
    let include = eval(&args[1], env)?;
    if let CalcValue::Scalar(Value::Error(error)) = &include {
        return Err(error.clone());
    }

    let source = calc_value_matrix(source);
    let include = calc_value_matrix(include);
    let row_count = source.len();
    let column_count = source.first().map_or(0, Vec::len);
    let include_height = include.len();
    let include_width = include.first().map_or(0, Vec::len);

    let (filter_rows, mask): (bool, Vec<&Value>) =
        if include_height == row_count && include_width == 1 {
            (true, include.iter().map(|row| &row[0]).collect())
        } else if include_height == 1 && include_width == column_count {
            (false, include[0].iter().collect())
        } else {
            return Err(FormulaError::Value);
        };

    let mut selected = Vec::with_capacity(mask.len());
    for (index, value) in mask.into_iter().enumerate() {
        if truthy(value)? {
            selected.push(index);
        }
    }
    if selected.is_empty() {
        if args.len() == 2 {
            return Err(FormulaError::Calc);
        }
        if matches!(&args[2], Expr::Missing) {
            return Ok(scalar(Value::Blank));
        }
        return eval(&args[2], env);
    }

    let filtered = if filter_rows {
        selected
            .into_iter()
            .map(|index| source[index].clone())
            .collect()
    } else {
        source
            .iter()
            .map(|row| selected.iter().map(|index| row[*index].clone()).collect())
            .collect()
    };
    ArrayValue::new(filtered).map(CalcValue::Array)
}

fn eval_scalar(expr: &Expr, env: &Environment<'_>) -> Result<Value, FormulaError> {
    match eval(expr, env)? {
        CalcValue::Scalar(v) => Ok(v),
        CalcValue::Range(_) | CalcValue::Array(_) => Err(FormulaError::Value),
    }
}

fn sequence_call(args: &[Expr], env: &Environment<'_>) -> Result<ArrayValue, FormulaError> {
    arity("SEQUENCE", args, 1, 4)?;
    if matches!(args.first(), Some(Expr::Missing))
        && !args[1..]
            .iter()
            .any(|argument| !matches!(argument, Expr::Missing))
    {
        return Err(FormulaError::Value);
    }

    let mut evaluated = Vec::with_capacity(args.len());
    for argument in args {
        if matches!(argument, Expr::Missing) {
            evaluated.push(None);
            continue;
        }
        let value = eval(argument, env)?;
        if let CalcValue::Scalar(Value::Error(error)) = &value {
            return Err(error.clone());
        }
        evaluated.push(Some(value));
    }
    if evaluated
        .iter()
        .flatten()
        .any(|value| matches!(value, CalcValue::Range(_) | CalcValue::Array(_)))
    {
        return Err(FormulaError::Value);
    }

    let mut numbers = Vec::with_capacity(args.len());
    for value in evaluated {
        let number = match value {
            None => None,
            Some(CalcValue::Scalar(value)) => Some(to_number(&value)?),
            Some(CalcValue::Range(_) | CalcValue::Array(_)) => unreachable!(),
        };
        if number.is_some_and(|number| !number.is_finite()) {
            return Err(FormulaError::Num);
        }
        numbers.push(number);
    }

    let rows_number = numbers.first().copied().flatten().unwrap_or(1.0);
    let columns_number = numbers.get(1).copied().flatten().unwrap_or(1.0);
    let start = numbers.get(2).copied().flatten().unwrap_or(1.0);
    let step = numbers.get(3).copied().flatten().unwrap_or(1.0);
    let rows_truncated = rows_number.trunc();
    let columns_truncated = columns_number.trunc();
    if rows_truncated <= 0.0 || columns_truncated <= 0.0 {
        return Err(FormulaError::Num);
    }
    if rows_truncated > MAX_ARRAY_CELLS as f64 || columns_truncated > MAX_ARRAY_CELLS as f64 {
        return Err(FormulaError::Num);
    }
    let rows = rows_truncated as usize;
    let columns = columns_truncated as usize;
    let count = rows.checked_mul(columns).ok_or(FormulaError::Num)?;
    if count > MAX_ARRAY_CELLS {
        return Err(FormulaError::Num);
    }

    let mut result = Vec::with_capacity(rows);
    for row_index in 0..rows {
        let mut row = Vec::with_capacity(columns);
        for column_index in 0..columns {
            let index = row_index * columns + column_index;
            let value = start + index as f64 * step;
            if !value.is_finite() {
                return Err(FormulaError::Num);
            }
            row.push(Value::Number(value));
        }
        result.push(row);
    }
    ArrayValue::new(result)
}

fn read_cell(env: &Environment<'_>, sheet: &str, address: &str) -> Value {
    let actual_sheet = if sheet.is_empty() {
        env.sheet_name
    } else {
        sheet
    };
    let key = format!("{actual_sheet}!{}", address.replace('$', ""));
    env.cells
        .iter()
        .find(|(k, _)| {
            k.eq_ignore_ascii_case(&key)
                || (sheet.is_empty() && k.eq_ignore_ascii_case(&address.replace('$', "")))
        })
        .map(|(_, v)| v.clone())
        .unwrap_or(Value::Blank)
}

fn read_range(
    env: &Environment<'_>,
    sheet: &str,
    start: &str,
    end: &str,
) -> Result<Vec<Vec<Value>>, FormulaError> {
    let (c1, r1) = decode_ref(start)?;
    let (c2, r2) = decode_ref(end)?;
    let rows = r1.min(r2)..=r1.max(r2);
    let cols = c1.min(c2)..=c1.max(c2);
    let count = (rows.end() - rows.start() + 1).saturating_mul(cols.end() - cols.start() + 1);
    if count > 100_000 {
        return Err(FormulaError::Num);
    }
    Ok(rows
        .map(|r| {
            cols.clone()
                .map(|c| read_cell(env, sheet, &encode_ref(c, r)))
                .collect()
        })
        .collect())
}

fn decode_ref(address: &str) -> Result<(usize, usize), FormulaError> {
    let clean = address.replace('$', "");
    let split = clean
        .find(|c: char| c.is_ascii_digit())
        .ok_or(FormulaError::Ref)?;
    let (col, row) = clean.split_at(split);
    let mut col_num = 0usize;
    for ch in col.chars() {
        col_num = col_num
            .checked_mul(26)
            .and_then(|n| n.checked_add(ch.to_ascii_uppercase() as usize - 'A' as usize + 1))
            .ok_or(FormulaError::Ref)?;
    }
    let row_num = row.parse::<usize>().map_err(|_| FormulaError::Ref)?;
    if col_num == 0 || row_num == 0 {
        return Err(FormulaError::Ref);
    }
    Ok((col_num, row_num))
}
fn encode_ref(mut col: usize, row: usize) -> String {
    let mut letters = String::new();
    while col > 0 {
        let rem = (col - 1) % 26;
        letters.insert(0, (b'A' + rem as u8) as char);
        col = (col - 1) / 26;
    }
    format!("{letters}{row}")
}

fn eval_binary(op: &str, a: Value, b: Value) -> Result<Value, FormulaError> {
    if matches!(op, "=" | "<>" | "<" | "<=" | ">" | ">=") {
        if let Value::Error(error) = &a {
            return Err(error.clone());
        }
        return Ok(Value::Bool(compare_operator(&a, &b, op)?));
    }
    if let Value::Error(e) = a {
        return Err(e);
    }
    if let Value::Error(e) = b {
        return Err(e);
    }
    match op {
        "&" => join_values_bounded(&[&a, &b], "", false, MAX_TEXT_LENGTH_UNITS).map(Value::Text),
        "+" => Ok(Value::Number(to_number(&a)? + to_number(&b)?)),
        "-" => Ok(Value::Number(to_number(&a)? - to_number(&b)?)),
        "*" => Ok(Value::Number(to_number(&a)? * to_number(&b)?)),
        "/" => {
            let d = to_number(&b)?;
            if d == 0.0 {
                Err(FormulaError::Div0)
            } else {
                Ok(Value::Number(to_number(&a)? / d))
            }
        }
        "^" => {
            let result = to_number(&a)?.powf(to_number(&b)?);
            if result.is_finite() {
                Ok(Value::Number(result))
            } else {
                Err(FormulaError::Num)
            }
        }
        _ => Err(FormulaError::Unsupported(format!("operator {op}"))),
    }
}

fn compare_operator(a: &Value, b: &Value, op: &str) -> Result<bool, FormulaError> {
    let left_number = finite_comparison_number(a)?;
    let right_number = finite_comparison_number(b)?;
    let order = match (a, b) {
        (Value::Text(left), Value::Text(right)) => {
            if left.is_ascii() && right.is_ascii() {
                left.to_ascii_lowercase().cmp(&right.to_ascii_lowercase())
            } else {
                left.cmp(right)
            }
        }
        (Value::Text(_), _) | (_, Value::Text(_)) => {
            return match op {
                "=" => Ok(false),
                "<>" => Ok(true),
                _ => Err(FormulaError::Value),
            };
        }
        _ => left_number
            .expect("non-text values have a finite numeric comparison value")
            .partial_cmp(
                &right_number.expect("non-text values have a finite numeric comparison value"),
            )
            .ok_or(FormulaError::Value)?,
    };

    Ok(match op {
        "=" => order == std::cmp::Ordering::Equal,
        "<>" => order != std::cmp::Ordering::Equal,
        "<" => order == std::cmp::Ordering::Less,
        "<=" => order != std::cmp::Ordering::Greater,
        ">" => order == std::cmp::Ordering::Greater,
        ">=" => order != std::cmp::Ordering::Less,
        _ => false,
    })
}

fn finite_comparison_number(value: &Value) -> Result<Option<f64>, FormulaError> {
    if matches!(value, Value::Text(_)) {
        return Ok(None);
    }
    let number = to_number(value)?;
    if !number.is_finite() {
        return Err(FormulaError::Num);
    }
    Ok(Some(number))
}

fn compare(a: &Value, b: &Value) -> Result<i8, FormulaError> {
    match (a, b) {
        (Value::Number(x), Value::Number(y)) => Ok(if x < y {
            -1
        } else if x > y {
            1
        } else {
            0
        }),
        (Value::Text(x), Value::Text(y)) => Ok(match x.to_lowercase().cmp(&y.to_lowercase()) {
            std::cmp::Ordering::Less => -1,
            std::cmp::Ordering::Equal => 0,
            std::cmp::Ordering::Greater => 1,
        }),
        (Value::Bool(x), Value::Bool(y)) => Ok(match x.cmp(y) {
            std::cmp::Ordering::Less => -1,
            std::cmp::Ordering::Equal => 0,
            std::cmp::Ordering::Greater => 1,
        }),
        _ => Err(FormulaError::Value),
    }
}
fn to_number(v: &Value) -> Result<f64, FormulaError> {
    match v {
        Value::Number(n) => Ok(*n),
        Value::Bool(b) => Ok(if *b { 1.0 } else { 0.0 }),
        Value::Blank => Ok(0.0),
        Value::Text(s) if s.trim().is_empty() => Ok(0.0),
        Value::Text(s) => s.trim().parse::<f64>().map_err(|_| FormulaError::Value),
        Value::Error(e) => Err(e.clone()),
    }
}

const MAX_BINOMIAL_STEPS: u64 = 1024;
const MAX_EXACT_EXPRESSION_BITS: usize = 65_536;
const MAX_FINITE_BINARY64_INTEGER_BITS: usize = 1024;

#[derive(Clone, Debug)]
struct ExactNatural {
    // Little-endian base-2^32 limbs keep multiplication and division by the
    // small factors in the binomial recurrence simple and overflow-safe.
    limbs: Vec<u32>,
}

impl ExactNatural {
    fn one() -> Self {
        Self { limbs: vec![1] }
    }

    fn from_decimal(source: &str) -> Option<Self> {
        let mut value = Self { limbs: vec![0] };
        for digit in source.bytes() {
            if !digit.is_ascii_digit() {
                return None;
            }
            value.multiply_small(10);
            value.add_small(u64::from(digit - b'0'));
        }
        Some(value)
    }

    fn add_small(&mut self, value: u64) {
        let mut carry = u128::from(value);
        for limb in &mut self.limbs {
            if carry == 0 {
                return;
            }
            let sum = u128::from(*limb) + carry;
            *limb = sum as u32;
            carry = sum >> 32;
        }
        while carry != 0 {
            self.limbs.push(carry as u32);
            carry >>= 32;
        }
    }

    fn compare(&self, other: &Self) -> std::cmp::Ordering {
        self.limbs
            .len()
            .cmp(&other.limbs.len())
            .then_with(|| self.limbs.iter().rev().cmp(other.limbs.iter().rev()))
    }

    fn add_assign(&mut self, other: &Self) {
        let mut carry = 0_u64;
        let width = self.limbs.len().max(other.limbs.len());
        self.limbs.resize(width, 0);
        for index in 0..width {
            let sum = u64::from(self.limbs[index])
                + u64::from(*other.limbs.get(index).unwrap_or(&0))
                + carry;
            self.limbs[index] = sum as u32;
            carry = sum >> 32;
        }
        if carry != 0 {
            self.limbs.push(carry as u32);
        }
    }

    // The caller establishes self >= other.
    fn subtract_assign(&mut self, other: &Self) {
        let mut borrow = 0_u64;
        for index in 0..self.limbs.len() {
            let left = u64::from(self.limbs[index]);
            let right = u64::from(*other.limbs.get(index).unwrap_or(&0)) + borrow;
            if left >= right {
                self.limbs[index] = (left - right) as u32;
                borrow = 0;
            } else {
                self.limbs[index] = ((1_u64 << 32) + left - right) as u32;
                borrow = 1;
            }
        }
        debug_assert_eq!(borrow, 0);
        self.normalize();
    }

    fn multiply_assign(&mut self, other: &Self) {
        if self.is_zero() || other.is_zero() {
            self.limbs = vec![0];
            return;
        }
        let mut product = vec![0_u32; self.limbs.len() + other.limbs.len()];
        for (left_index, left) in self.limbs.iter().copied().enumerate() {
            let mut carry = 0_u64;
            for (right_index, right) in other.limbs.iter().copied().enumerate() {
                let index = left_index + right_index;
                let current =
                    u64::from(left) * u64::from(right) + u64::from(product[index]) + carry;
                product[index] = current as u32;
                carry = current >> 32;
            }
            product[left_index + other.limbs.len()] = carry as u32;
        }
        self.limbs = product;
        self.normalize();
    }

    fn power(&self, mut exponent: u64) -> Option<Self> {
        let mut result = Self::one();
        let mut factor = self.clone();
        while exponent != 0 {
            if exponent & 1 != 0 {
                result.multiply_assign(&factor);
                if result.bit_length() > MAX_EXACT_EXPRESSION_BITS {
                    return None;
                }
            }
            exponent >>= 1;
            if exponent != 0 {
                factor.multiply_assign(&factor.clone());
                if factor.bit_length() > MAX_EXACT_EXPRESSION_BITS {
                    return None;
                }
            }
        }
        Some(result)
    }

    fn is_zero(&self) -> bool {
        self.limbs.iter().all(|limb| *limb == 0)
    }

    fn to_u64(&self) -> Option<u64> {
        match self.limbs.as_slice() {
            [low] => Some(u64::from(*low)),
            [low, high] => Some(u64::from(*low) | (u64::from(*high) << 32)),
            _ => None,
        }
    }

    fn is_one(&self) -> bool {
        self.limbs.as_slice() == [1]
    }

    fn is_odd(&self) -> bool {
        self.limbs.first().is_some_and(|limb| limb & 1 != 0)
    }

    fn shift_left(&self, bits: usize) -> Self {
        if self.is_zero() || bits == 0 {
            return self.clone();
        }
        let limb_shift = bits / 32;
        let bit_shift = bits % 32;
        let mut limbs = vec![0; limb_shift];
        let mut carry = 0_u64;
        for limb in &self.limbs {
            let shifted = (u64::from(*limb) << bit_shift) | carry;
            limbs.push(shifted as u32);
            carry = shifted >> 32;
        }
        if carry != 0 {
            limbs.push(carry as u32);
        }
        Self { limbs }
    }

    fn shift_right_one(&mut self) {
        let mut carry = 0_u32;
        for limb in self.limbs.iter_mut().rev() {
            let next = *limb & 1;
            *limb = (*limb >> 1) | (carry << 31);
            carry = next;
        }
        self.normalize();
    }

    fn set_bit(&mut self, index: usize) {
        self.limbs.resize(self.limbs.len().max(index / 32 + 1), 0);
        self.limbs[index / 32] |= 1 << (index % 32);
    }

    fn divide_remainder(&self, divisor: &Self) -> Option<(Self, Self)> {
        if divisor.is_zero() {
            return None;
        }
        if self.compare(divisor) == std::cmp::Ordering::Less {
            return Some((Self { limbs: vec![0] }, self.clone()));
        }
        let shift = self.bit_length() - divisor.bit_length();
        let mut shifted_divisor = divisor.shift_left(shift);
        let mut remainder = self.clone();
        let mut quotient = Self { limbs: vec![0] };
        for bit in (0..=shift).rev() {
            if remainder.compare(&shifted_divisor) != std::cmp::Ordering::Less {
                remainder.subtract_assign(&shifted_divisor);
                quotient.set_bit(bit);
            }
            shifted_divisor.shift_right_one();
        }
        Some((quotient, remainder))
    }

    fn normalize(&mut self) {
        while self.limbs.len() > 1 && self.limbs.last() == Some(&0) {
            self.limbs.pop();
        }
    }

    fn multiply_small(&mut self, factor: u64) {
        let mut carry = 0_u128;
        for limb in &mut self.limbs {
            let product = u128::from(*limb) * u128::from(factor) + carry;
            *limb = product as u32;
            carry = product >> 32;
        }
        while carry != 0 {
            self.limbs.push(carry as u32);
            carry >>= 32;
        }
    }

    fn divide_small(&mut self, divisor: u64) -> bool {
        let mut remainder = 0_u64;
        for limb in self.limbs.iter_mut().rev() {
            let current = (u128::from(remainder) << 32) | u128::from(*limb);
            *limb = (current / u128::from(divisor)) as u32;
            remainder = (current % u128::from(divisor)) as u64;
        }
        while self.limbs.len() > 1 && self.limbs.last() == Some(&0) {
            self.limbs.pop();
        }
        remainder == 0
    }

    fn bit_length(&self) -> usize {
        let Some(last) = self.limbs.last() else {
            return 0;
        };
        (self.limbs.len() - 1) * 32 + (32 - last.leading_zeros() as usize)
    }

    fn bit(&self, index: usize) -> bool {
        self.limbs
            .get(index / 32)
            .is_some_and(|limb| (limb & (1 << (index % 32))) != 0)
    }

    fn any_bits_below(&self, limit: usize) -> bool {
        let full_limbs = limit / 32;
        if self.limbs.iter().take(full_limbs).any(|limb| *limb != 0) {
            return true;
        }
        let remaining_bits = limit % 32;
        remaining_bits != 0
            && self
                .limbs
                .get(full_limbs)
                .is_some_and(|limb| (limb & ((1_u32 << remaining_bits) - 1)) != 0)
    }

    fn rounded_f64(&self) -> Option<f64> {
        let bit_length = self.bit_length();
        if bit_length == 0 {
            return Some(0.0);
        }

        let shift = bit_length.saturating_sub(53);
        let mut significand = 0_u64;
        for bit_index in (shift..bit_length).rev() {
            significand = (significand << 1) | u64::from(self.bit(bit_index));
        }

        if shift != 0 {
            let guard = self.bit(shift - 1);
            let sticky = self.any_bits_below(shift - 1);
            if guard && (sticky || significand & 1 != 0) {
                significand += 1;
            }
        }

        let mut exponent = bit_length as i32 - 1;
        if significand == (1_u64 << 53) {
            significand >>= 1;
            exponent += 1;
        }
        if exponent > 1023 {
            return None;
        }
        if exponent < 52 {
            return Some(significand as f64);
        }

        let exponent_bits = (exponent + 1023) as u64;
        let fraction_bits = significand - (1_u64 << 52);
        Some(f64::from_bits((exponent_bits << 52) | fraction_bits))
    }
}

fn rounded_ratio_f64(numerator: &ExactNatural, denominator: &ExactNatural) -> Option<f64> {
    if denominator.is_zero() {
        return None;
    }
    if numerator.is_zero() {
        return Some(0.0);
    }

    let mut exponent = numerator.bit_length() as i64 - denominator.bit_length() as i64;
    let below_candidate = if exponent >= 0 {
        numerator.compare(&denominator.shift_left(exponent as usize)) == std::cmp::Ordering::Less
    } else {
        numerator
            .shift_left((-exponent) as usize)
            .compare(denominator)
            == std::cmp::Ordering::Less
    };
    if below_candidate {
        exponent -= 1;
    }
    if exponent > 1023 {
        return None;
    }
    if exponent < -1075 {
        return Some(0.0);
    }

    let normal = exponent >= -1022;
    let (scaled_numerator, scaled_denominator) = if normal {
        let shift = 52 - exponent;
        if shift >= 0 {
            (numerator.shift_left(shift as usize), denominator.clone())
        } else {
            (numerator.clone(), denominator.shift_left((-shift) as usize))
        }
    } else {
        (numerator.shift_left(1074), denominator.clone())
    };
    let (quotient, remainder) = scaled_numerator.divide_remainder(&scaled_denominator)?;
    let mut significand = quotient.to_u64()?;
    let mut complement = scaled_denominator.clone();
    complement.subtract_assign(&remainder);
    let halfway = remainder.compare(&complement);
    if halfway == std::cmp::Ordering::Greater
        || (halfway == std::cmp::Ordering::Equal && significand & 1 != 0)
    {
        significand += 1;
    }

    if normal {
        if significand == 1_u64 << 53 {
            significand >>= 1;
            exponent += 1;
        }
        if exponent > 1023 {
            return None;
        }
        let fraction = significand.checked_sub(1_u64 << 52)?;
        let exponent_bits = (exponent + 1023) as u64;
        Some(f64::from_bits((exponent_bits << 52) | fraction))
    } else {
        Some(f64::from_bits(significand))
    }
}

#[derive(Clone, Debug)]
struct ExactSignedInteger {
    negative: bool,
    magnitude: ExactNatural,
}

impl ExactSignedInteger {
    fn from_decimal(source: &str) -> Option<Self> {
        Some(Self {
            negative: false,
            magnitude: ExactNatural::from_decimal(source)?,
        })
    }

    fn zero() -> Self {
        Self {
            negative: false,
            magnitude: ExactNatural { limbs: vec![0] },
        }
    }

    fn negated(&self) -> Self {
        let mut value = self.clone();
        if !value.magnitude.is_zero() {
            value.negative = !value.negative;
        }
        value
    }

    fn add(&self, other: &Self) -> Self {
        if self.negative == other.negative {
            let mut magnitude = self.magnitude.clone();
            magnitude.add_assign(&other.magnitude);
            return Self {
                negative: self.negative,
                magnitude,
            };
        }
        match self.magnitude.compare(&other.magnitude) {
            std::cmp::Ordering::Greater => {
                let mut magnitude = self.magnitude.clone();
                magnitude.subtract_assign(&other.magnitude);
                Self {
                    negative: self.negative,
                    magnitude,
                }
            }
            std::cmp::Ordering::Less => {
                let mut magnitude = other.magnitude.clone();
                magnitude.subtract_assign(&self.magnitude);
                Self {
                    negative: other.negative,
                    magnitude,
                }
            }
            std::cmp::Ordering::Equal => Self::zero(),
        }
    }

    fn multiply(&self, other: &Self) -> Self {
        let mut magnitude = self.magnitude.clone();
        magnitude.multiply_assign(&other.magnitude);
        Self {
            negative: self.negative != other.negative && !magnitude.is_zero(),
            magnitude,
        }
    }

    fn power_with_exponent(&self, exponent: &Self) -> Option<Self> {
        if self.magnitude.is_one() {
            return Some(Self {
                negative: self.negative && exponent.magnitude.is_odd(),
                magnitude: ExactNatural::one(),
            });
        }
        if exponent.negative {
            return None;
        }
        if let Some(exponent) = exponent.magnitude.to_u64() {
            return self.magnitude.power(exponent).map(|magnitude| Self {
                negative: self.negative && exponent % 2 == 1 && !magnitude.is_zero(),
                magnitude,
            });
        }
        if self.magnitude.is_zero() {
            return Some(Self::zero());
        }
        None
    }

    fn divide_to_f64(&self, denominator: &Self) -> Option<f64> {
        let mut quotient = rounded_ratio_f64(&self.magnitude, &denominator.magnitude)?;
        if self.negative != denominator.negative {
            quotient = -quotient;
        }
        Some(quotient)
    }

    fn rounded_f64(&self) -> Option<f64> {
        let magnitude = self.magnitude.rounded_f64()?;
        Some(if self.negative { -magnitude } else { magnitude })
    }

    fn within_exact_input_boundary(&self) -> bool {
        self.magnitude
            .to_u64()
            .is_some_and(|value| value <= MAX_EXACT_INTEGER as u64)
    }

    fn to_f64(&self) -> f64 {
        let magnitude = self.magnitude.to_u64().unwrap_or(0) as f64;
        if self.negative { -magnitude } else { magnitude }
    }
}

#[derive(Clone, Debug)]
enum ExactIntegerExpression {
    Exact(ExactSignedInteger),
    Number(f64),
    Error(FormulaError),
    NotExact,
}

fn exact_integer_expression(expression: &Expr) -> ExactIntegerExpression {
    match expression {
        Expr::Number(number, source) => {
            if source.chars().all(|ch| ch.is_ascii_digit()) {
                if source.len() > 19_729 {
                    ExactIntegerExpression::Error(FormulaError::Num)
                } else {
                    ExactSignedInteger::from_decimal(source)
                        .map(|value| {
                            if value.magnitude.bit_length() > MAX_EXACT_EXPRESSION_BITS {
                                ExactIntegerExpression::Error(FormulaError::Num)
                            } else {
                                ExactIntegerExpression::Exact(value)
                            }
                        })
                        .unwrap_or(ExactIntegerExpression::NotExact)
                }
            } else {
                ExactIntegerExpression::Number(*number)
            }
        }
        Expr::Unary('+', inner) => exact_integer_expression(inner),
        Expr::Unary('-', inner) => match exact_integer_expression(inner) {
            ExactIntegerExpression::Exact(value) => ExactIntegerExpression::Exact(value.negated()),
            ExactIntegerExpression::Number(value) => ExactIntegerExpression::Number(-value),
            result => result,
        },
        Expr::Binary(operator, left, right) => {
            let left = exact_integer_expression(left);
            let right = exact_integer_expression(right);
            match (operator.as_str(), left, right) {
                ("+", ExactIntegerExpression::Exact(a), ExactIntegerExpression::Exact(b)) => {
                    bounded_exact_integer(a.add(&b))
                }
                ("-", ExactIntegerExpression::Exact(a), ExactIntegerExpression::Exact(b)) => {
                    bounded_exact_integer(a.add(&b.negated()))
                }
                ("*", ExactIntegerExpression::Exact(a), ExactIntegerExpression::Exact(b)) => {
                    bounded_exact_integer(a.multiply(&b))
                }
                ("^", ExactIntegerExpression::Exact(a), ExactIntegerExpression::Exact(b)) => {
                    match a.power_with_exponent(&b) {
                        Some(value) => ExactIntegerExpression::Exact(value),
                        None => match (a.rounded_f64(), b.rounded_f64()) {
                            (Some(base), Some(exponent)) => {
                                let result = base.powf(exponent);
                                if result.is_finite() {
                                    ExactIntegerExpression::Number(result)
                                } else {
                                    ExactIntegerExpression::Error(FormulaError::Num)
                                }
                            }
                            _ => ExactIntegerExpression::Error(FormulaError::Num),
                        },
                    }
                }
                ("/", ExactIntegerExpression::Exact(a), ExactIntegerExpression::Exact(b)) => {
                    if b.magnitude.is_zero() {
                        ExactIntegerExpression::Error(FormulaError::Div0)
                    } else {
                        a.divide_to_f64(&b)
                            .map(ExactIntegerExpression::Number)
                            .unwrap_or(ExactIntegerExpression::Error(FormulaError::Num))
                    }
                }
                (op, a, b) if is_numeric_expression(&a) && is_numeric_expression(&b) => {
                    let Some(a) = numeric_expression_to_f64(a) else {
                        return ExactIntegerExpression::Error(FormulaError::Num);
                    };
                    let Some(b) = numeric_expression_to_f64(b) else {
                        return ExactIntegerExpression::Error(FormulaError::Num);
                    };
                    match op {
                        "+" => ExactIntegerExpression::Number(a + b),
                        "-" => ExactIntegerExpression::Number(a - b),
                        "*" => ExactIntegerExpression::Number(a * b),
                        "/" if b == 0.0 => ExactIntegerExpression::Error(FormulaError::Div0),
                        "/" => ExactIntegerExpression::Number(a / b),
                        "^" => {
                            let result = a.powf(b);
                            if result.is_finite() {
                                ExactIntegerExpression::Number(result)
                            } else {
                                ExactIntegerExpression::Error(FormulaError::Num)
                            }
                        }
                        _ => ExactIntegerExpression::NotExact,
                    }
                }
                (_, ExactIntegerExpression::NotExact, _)
                | (_, _, ExactIntegerExpression::NotExact) => ExactIntegerExpression::NotExact,
                (_, ExactIntegerExpression::Error(error), _) => {
                    ExactIntegerExpression::Error(error)
                }
                (_, _, ExactIntegerExpression::Error(error)) => {
                    ExactIntegerExpression::Error(error)
                }
                _ => ExactIntegerExpression::NotExact,
            }
        }
        _ => ExactIntegerExpression::NotExact,
    }
}

fn is_numeric_expression(expression: &ExactIntegerExpression) -> bool {
    matches!(
        expression,
        ExactIntegerExpression::Exact(_) | ExactIntegerExpression::Number(_)
    )
}

fn bounded_exact_integer(value: ExactSignedInteger) -> ExactIntegerExpression {
    if value.magnitude.bit_length() > MAX_EXACT_EXPRESSION_BITS {
        ExactIntegerExpression::Error(FormulaError::Num)
    } else {
        ExactIntegerExpression::Exact(value)
    }
}

fn numeric_expression_to_f64(expression: ExactIntegerExpression) -> Option<f64> {
    match expression {
        ExactIntegerExpression::Exact(value) => value.rounded_f64(),
        ExactIntegerExpression::Number(value) => Some(value),
        ExactIntegerExpression::Error(_) | ExactIntegerExpression::NotExact => None,
    }
}

fn integer_formula_argument(value: &Value) -> Result<i64, FormulaError> {
    let number = to_number(value)?;
    if !number.is_finite() || number.trunc().abs() > MAX_EXACT_INTEGER {
        return Err(FormulaError::Num);
    }
    let truncated = number.trunc();
    // Keep the sign until every argument has been coerced. Function-domain
    // validation happens afterward, matching Python's shared call order.
    Ok(truncated as i64)
}

fn binomial_float(number: u64, chosen: u64) -> Result<f64, FormulaError> {
    if chosen > number {
        return Err(FormulaError::Num);
    }
    let reduced = chosen.min(number - chosen);
    if reduced > MAX_BINOMIAL_STEPS {
        return Err(FormulaError::Num);
    }
    let mut result = ExactNatural::one();
    for step in 1..=reduced {
        result.multiply_small(number - reduced + step);
        if !result.divide_small(step) {
            return Err(FormulaError::Num);
        }
        // Coefficients grow monotonically after symmetry reduction, so a
        // value above this size cannot return to binary64's finite range.
        if result.bit_length() > 1024 {
            return Err(FormulaError::Num);
        }
    }
    result.rounded_f64().ok_or(FormulaError::Num)
}

fn factorial_float(number: u64) -> Result<f64, FormulaError> {
    if number > 170 {
        return Err(FormulaError::Num);
    }
    let mut result = ExactNatural::one();
    for factor in 2..=number {
        result.multiply_small(factor);
    }
    result.rounded_f64().ok_or(FormulaError::Num)
}

fn double_factorial_float(number: u64) -> Result<f64, FormulaError> {
    if number > 300 {
        return Err(FormulaError::Num);
    }
    let mut result = ExactNatural::one();
    let mut factor = number;
    while factor >= 2 {
        result.multiply_small(factor);
        if result.bit_length() > MAX_FINITE_BINARY64_INTEGER_BITS {
            return Err(FormulaError::Num);
        }
        factor -= 2;
    }
    result.rounded_f64().ok_or(FormulaError::Num)
}

fn permutation_float(number: u64, chosen: u64) -> Result<f64, FormulaError> {
    if chosen == 0 {
        return Ok(1.0);
    }
    // For any n >= r, nPr >= r!; 171! exceeds finite binary64.
    if chosen > 170 {
        return Err(FormulaError::Num);
    }
    let mut result = ExactNatural::one();
    let mut factor = number;
    for _ in 0..chosen {
        result.multiply_small(factor);
        if result.bit_length() > MAX_FINITE_BINARY64_INTEGER_BITS {
            return Err(FormulaError::Num);
        }
        factor -= 1;
    }
    result.rounded_f64().ok_or(FormulaError::Num)
}

fn permutationa_float(number: u64, chosen: u64) -> Result<f64, FormulaError> {
    if chosen == 0 || number == 1 {
        // Includes the evaluator's explicit, unverified 0^0 identity profile.
        return Ok(1.0);
    }
    if number == 0 {
        return Err(FormulaError::Num);
    }
    // For n >= 2, 2^1024 is outside finite binary64.
    if chosen > 1023 {
        return Err(FormulaError::Num);
    }

    let mut result = ExactNatural::one();
    let mut factor = ExactNatural::one();
    factor.multiply_small(number);
    let mut exponent = chosen;
    while exponent != 0 {
        if exponent & 1 != 0 {
            result.multiply_assign(&factor);
            if result.bit_length() > MAX_FINITE_BINARY64_INTEGER_BITS {
                return Err(FormulaError::Num);
            }
        }
        exponent >>= 1;
        if exponent != 0 {
            let previous_factor = factor.clone();
            factor.multiply_assign(&previous_factor);
            if factor.bit_length() > MAX_FINITE_BINARY64_INTEGER_BITS {
                return Err(FormulaError::Num);
            }
        }
    }
    result.rounded_f64().ok_or(FormulaError::Num)
}

fn integer_formula_call(
    name: &str,
    args: &[Expr],
    env: &Environment<'_>,
) -> Result<Value, FormulaError> {
    let expected_arity = match name {
        "GCD" | "LCM" => (1, 255),
        "FACT" | "FACTDOUBLE" => (1, 1),
        _ => (2, 2),
    };
    arity(name, args, expected_arity.0, expected_arity.1)?;

    let exact_arguments: Vec<_> = args.iter().map(exact_integer_expression).collect();
    let mut values = Vec::with_capacity(args.len());
    let mut has_range = false;
    for (argument, exact_argument) in args.iter().zip(&exact_arguments) {
        match exact_argument {
            ExactIntegerExpression::Exact(_) | ExactIntegerExpression::Number(_) => {
                // Exact integer expressions may have intermediates outside binary64's
                // range and still cancel back into this formula's integer input domain.
                values.push(Value::Blank);
                continue;
            }
            ExactIntegerExpression::Error(error) => {
                values.push(Value::Error(error.clone()));
                continue;
            }
            ExactIntegerExpression::NotExact => {}
        }
        let evaluated = match eval(argument, env) {
            Ok(value) => value,
            Err(error) if is_excel_value_error(&error) => CalcValue::Scalar(Value::Error(error)),
            Err(error) => return Err(error),
        };
        match evaluated {
            CalcValue::Scalar(value) => values.push(value),
            CalcValue::Range(_) | CalcValue::Array(_) => has_range = true,
        }
    }
    if has_range {
        return Err(FormulaError::Value);
    }
    if let Some(error) = values.iter().find_map(|value| match value {
        Value::Error(error) => Some(error.clone()),
        _ => None,
    }) {
        return Err(error);
    }
    let integers: Vec<i64> = values
        .iter()
        .zip(exact_arguments)
        .map(|(value, exact_argument)| {
            let exact_value = match exact_argument {
                ExactIntegerExpression::Exact(number) if !number.within_exact_input_boundary() => {
                    return Err(FormulaError::Num);
                }
                ExactIntegerExpression::Exact(number) => Value::Number(number.to_f64()),
                ExactIntegerExpression::Number(number) => Value::Number(number),
                ExactIntegerExpression::Error(error) => return Err(error),
                ExactIntegerExpression::NotExact => value.clone(),
            };
            integer_formula_argument(&exact_value)
        })
        .collect::<Result<_, _>>()?;
    if name == "GCD" {
        if integers.iter().any(|number| *number < 0) {
            return Err(FormulaError::Num);
        }
        if integers
            .iter()
            .any(|number| *number as u64 >= MAX_EXACT_INTEGER_U64)
        {
            return Err(FormulaError::Num);
        }
        let result = integers.iter().fold(0_u64, |mut result, number| {
            let mut remainder = *number as u64;
            while remainder != 0 {
                let next = result % remainder;
                result = remainder;
                remainder = next;
            }
            result
        });
        return Ok(Value::Number(result as f64));
    }
    if name == "LCM" {
        if integers.iter().any(|number| *number < 0) {
            return Err(FormulaError::Num);
        }
        // Microsoft does not document LCM's zero behavior. Workbook Forge uses
        // the conventional zero-absorbing identity as a local evaluator profile.
        if integers.contains(&0) {
            return Ok(Value::Number(0.0));
        }
        let largest_allowed = MAX_EXACT_INTEGER_U64 - 1;
        let mut result = 1_u64;
        for number in integers.iter().map(|number| *number as u64) {
            let mut left = result;
            let mut right = number;
            while right != 0 {
                let remainder = left % right;
                left = right;
                right = remainder;
            }
            let reduced = result / left;
            if reduced > largest_allowed / number {
                return Err(FormulaError::Num);
            }
            result = reduced * number;
        }
        return Ok(Value::Number(result as f64));
    }
    if name == "FACTDOUBLE" {
        let number = integers[0];
        if number < 0 {
            return Err(FormulaError::Num);
        }
        return Ok(Value::Number(double_factorial_float(number as u64)?));
    }
    if name == "FACT" {
        let number = integers[0];
        if number < 0 {
            return Err(FormulaError::Num);
        }
        return Ok(Value::Number(factorial_float(number as u64)?));
    }

    let number = integers[0];
    let chosen = integers[1];
    if name == "PERMUT" {
        if number <= 0 || chosen < 0 || chosen > number {
            return Err(FormulaError::Num);
        }
        return Ok(Value::Number(permutation_float(
            number as u64,
            chosen as u64,
        )?));
    }
    if name == "PERMUTATIONA" {
        if number < 0 || chosen < 0 {
            return Err(FormulaError::Num);
        }
        if number == 0 && chosen > 0 {
            return Err(FormulaError::Num);
        }
        return Ok(Value::Number(permutationa_float(
            number as u64,
            chosen as u64,
        )?));
    }
    if number < 0 || chosen < 0 || chosen > number {
        return Err(FormulaError::Num);
    }
    if name == "COMBIN" {
        return Ok(Value::Number(binomial_float(number as u64, chosen as u64)?));
    }

    // COMBINA counts selections with repetition: C(number + chosen - 1, chosen).
    // Handle its zero/zero identity before forming the transformed top index.
    if number == 0 {
        return Ok(Value::Number(1.0));
    }
    let transformed_top = number as u64 + chosen as u64 - 1;
    Ok(Value::Number(binomial_float(
        transformed_top,
        chosen as u64,
    )?))
}

fn decimal_digits(value: &Value) -> Result<i32, FormulaError> {
    let number = to_number(value)?;
    if !number.is_finite() || number.trunc().abs() > 308.0 {
        return Err(FormulaError::Num);
    }
    Ok(number.trunc() as i32)
}
fn round_to_precision(number: f64, digits: i32, mode: &str) -> Result<f64, FormulaError> {
    if !number.is_finite() {
        return Err(FormulaError::Num);
    }
    let scale = 10f64.powi(digits);
    if !scale.is_finite() || scale == 0.0 {
        return Err(FormulaError::Num);
    }
    let scaled = number * scale;
    // At large magnitudes, positive decimal precision cannot change a binary float.
    if scaled.is_infinite() && digits > 0 {
        return Ok(number);
    }
    if !scaled.is_finite() {
        return Err(FormulaError::Num);
    }
    let rounded = match mode {
        "nearest" => (scaled.abs() + 0.5).floor().copysign(scaled),
        "away-from-zero" => scaled.abs().ceil().copysign(scaled),
        _ => scaled.trunc(),
    };
    let result = rounded / scale;
    if result.is_finite() {
        Ok(result)
    } else {
        Err(FormulaError::Num)
    }
}
fn round_to_parity(number: f64, odd: bool) -> Result<f64, FormulaError> {
    if !number.is_finite() {
        return Err(FormulaError::Num);
    }
    let magnitude = number.abs();
    if magnitude >= MAX_EXACT_INTEGER {
        return if odd {
            Err(FormulaError::Num)
        } else {
            // Binary64 values at this magnitude are already even integers.
            Ok(number)
        };
    }
    let mut rounded = magnitude.ceil();
    let requested_parity = if odd { 1.0 } else { 0.0 };
    if rounded.rem_euclid(2.0) != requested_parity {
        rounded += 1.0;
    }
    let result = if number < 0.0 { -rounded } else { rounded };
    if result.is_finite() {
        Ok(result)
    } else {
        Err(FormulaError::Num)
    }
}

fn test_integer_parity(number: f64, odd: bool) -> Result<bool, FormulaError> {
    if !number.is_finite() {
        return Err(FormulaError::Num);
    }
    let is_odd = number.trunc().rem_euclid(2.0) == 1.0;
    Ok(if odd { is_odd } else { !is_odd })
}
fn number_text(number: f64) -> String {
    if number == 0.0 {
        return "0".into();
    }
    if number.fract() == 0.0 {
        return format!("{number:.0}");
    }
    let shortest = format!("{number:?}");
    if let Some((significand, exponent)) = shortest.split_once('e') {
        let exponent: i32 = exponent
            .parse()
            .expect("Rust's floating-point formatter emits a numeric exponent");
        format!("{significand}e{exponent:+03}")
    } else {
        shortest
    }
}

fn to_text(v: &Value) -> String {
    match v {
        Value::Number(n) => number_text(*n),
        Value::Text(s) => s.clone(),
        Value::Bool(true) => "TRUE".into(),
        Value::Bool(false) => "FALSE".into(),
        Value::Blank => String::new(),
        Value::Error(e) => e.to_string(),
    }
}

fn utf16_text_units(text: &str) -> usize {
    text.encode_utf16().count()
}

fn check_text_units(units: usize, limit: usize) -> Result<(), FormulaError> {
    if units > limit {
        Err(FormulaError::Value)
    } else {
        Ok(())
    }
}

fn validate_text_value(value: &Value) -> Result<(), FormulaError> {
    if let Value::Text(text) = value {
        check_text_units(utf16_text_units(text), MAX_TEXT_LENGTH_UNITS)?;
    }
    Ok(())
}

fn value_text_units(value: &Value) -> usize {
    match value {
        Value::Text(text) => utf16_text_units(text),
        Value::Number(number) => utf16_text_units(&number_text(*number)),
        Value::Bool(true) => 4,
        Value::Bool(false) => 5,
        Value::Blank => 0,
        Value::Error(error) => error.to_string().encode_utf16().count(),
    }
}

fn value_is_empty_text(value: &Value) -> bool {
    match value {
        Value::Blank => true,
        Value::Text(text) => text.is_empty(),
        _ => false,
    }
}

fn push_value_text(output: &mut String, value: &Value) {
    if let Value::Text(text) = value {
        output.push_str(text);
    } else {
        output.push_str(&to_text(value));
    }
}

fn join_values_bounded(
    values: &[&Value],
    delimiter: &str,
    ignore_empty: bool,
    limit: usize,
) -> Result<String, FormulaError> {
    let delimiter_units = utf16_text_units(delimiter);
    let mut units = 0usize;
    let mut included = 0usize;
    for value in values {
        if ignore_empty && value_is_empty_text(value) {
            continue;
        }
        if included > 0 {
            units = units.saturating_add(delimiter_units);
        }
        units = units.saturating_add(value_text_units(value));
        check_text_units(units, limit)?;
        included += 1;
    }

    let mut output = String::new();
    let mut written = 0usize;
    for value in values {
        if ignore_empty && value_is_empty_text(value) {
            continue;
        }
        if written > 0 {
            output.push_str(delimiter);
        }
        push_value_text(&mut output, value);
        written += 1;
    }
    Ok(output)
}

fn substitute_all_bounded(
    text: &str,
    old: &str,
    new: &str,
    limit: usize,
) -> Result<String, FormulaError> {
    if old.is_empty() {
        return Err(FormulaError::Value);
    }
    let occurrences = text.matches(old).count();
    let result_units = utf16_text_units(text)
        .saturating_sub(occurrences.saturating_mul(utf16_text_units(old)))
        .saturating_add(occurrences.saturating_mul(utf16_text_units(new)));
    check_text_units(result_units, limit)?;
    Ok(text.replace(old, new))
}
fn truthy(v: &Value) -> Result<bool, FormulaError> {
    match v {
        Value::Bool(b) => Ok(*b),
        Value::Number(n) => Ok(*n != 0.0),
        Value::Text(s) if s.is_empty() => Ok(false),
        Value::Text(_) => Err(FormulaError::Value),
        Value::Blank => Ok(false),
        Value::Error(e) => Err(e.clone()),
    }
}

#[allow(clippy::needless_return)]
fn eval_call(name: &str, args: &[Expr], env: &Environment<'_>) -> Result<Value, FormulaError> {
    match name {
        "WEEKDAY" | "WEEKNUM" | "ISOWEEKNUM" => return week_call(name, args, env),
        "DAYS360" | "YEARFRAC" => return day_count_call(name, args, env),
        "FV" | "PV" | "PMT" => return tvm_call(name, args, env),
        "NPER" => return nper_call(args, env),
        "SLN" | "SYD" | "DB" | "DDB" => return depreciation_call(name, args, env),
        "VDB" => return vdb_call(args, env),
        "AMORLINC" | "AMORDEGRC" => return amortization_call(name, args, env),
        "IPMT" | "PPMT" => return payment_component_call(name, args, env),
        "CUMIPMT" | "CUMPRINC" => return cumulative_payment_call(name, args, env),
        "COUPDAYBS" | "COUPDAYS" | "COUPDAYSNC" | "COUPNCD" | "COUPNUM" | "COUPPCD" => {
            return coupon_call(name, args, env);
        }
        "WORKDAY" | "NETWORKDAYS" | "WORKDAY.INTL" | "NETWORKDAYS.INTL" => {
            return working_day_call(name, args, env);
        }
        "GCD" | "LCM" | "FACT" | "FACTDOUBLE" | "COMBIN" | "COMBINA" | "PERMUT"
        | "PERMUTATIONA" => {
            return integer_formula_call(name, args, env);
        }
        "NA" => {
            arity(name, args, 0, 0)?;
            return Ok(Value::Error(FormulaError::NA));
        }
        "HOUR" | "MINUTE" | "SECOND" => {
            arity(name, args, 1, 1)?;
            let serial = to_number(&eval_scalar(&args[0], env)?)?;
            let (hour, minute, second) = excel_time_hms(serial)?;
            let value = match name {
                "HOUR" => hour,
                "MINUTE" => minute,
                _ => second,
            };
            return Ok(Value::Number(value as f64));
        }
        "TIME" => {
            arity(name, args, 3, 3)?;
            let mut units = [0_i64; 3];
            for (index, argument) in args.iter().enumerate() {
                let number = to_number(&eval_scalar(argument, env)?)?;
                if !number.is_finite() || !(0.0..=32_767.0).contains(&number) {
                    return Err(FormulaError::Num);
                }
                units[index] = number.trunc() as i64;
            }
            let total_seconds = units[0] * 3_600 + units[1] * 60 + units[2];
            return Ok(Value::Number(
                total_seconds.rem_euclid(86_400) as f64 / 86_400.0,
            ));
        }
        "TRUNC" => {
            arity(name, args, 1, 2)?;
            let number = to_number(&eval_scalar(&args[0], env)?)?;
            let digits = match args.get(1) {
                None | Some(Expr::Missing) => 0,
                Some(expression) => decimal_digits(&eval_scalar(expression, env)?)?,
            };
            return Ok(Value::Number(round_to_precision(
                number,
                digits,
                "toward-zero",
            )?));
        }
        "EVEN" | "ODD" | "ISEVEN" | "ISODD" => {
            arity(name, args, 1, 1)?;
            let evaluated = eval(&args[0], env)?;
            let value = match evaluated {
                CalcValue::Range(_) | CalcValue::Array(_) => return Err(FormulaError::Value),
                CalcValue::Scalar(value) => value,
            };
            if matches!(name, "EVEN" | "ODD") {
                let number = to_number(&value)?;
                return Ok(Value::Number(round_to_parity(number, name == "ODD")?));
            }
            let number = match value {
                Value::Number(number) => number,
                Value::Error(error) => return Err(error),
                _ => return Err(FormulaError::Value),
            };
            return Ok(Value::Bool(test_integer_parity(number, name == "ISODD")?));
        }
        "XLOOKUP" | "XMATCH" => return xlookup_family(name, args, env),
        "TEXTBEFORE" | "TEXTAFTER" => return text_extract(name, args, env),
        "ISBLANK" | "ISNUMBER" | "ISTEXT" | "ISLOGICAL" | "ISERR" | "ISERROR" | "ISNA" => {
            arity(name, args, 1, 1)?;
            let evaluated = match eval(&args[0], env) {
                Ok(value) => value,
                Err(error) if is_excel_value_error(&error) => {
                    return Ok(Value::Bool(error_predicate_matches(name, &error)));
                }
                Err(error) => return Err(error),
            };
            let value = match evaluated {
                CalcValue::Range(_) | CalcValue::Array(_) => return Err(FormulaError::Value),
                CalcValue::Scalar(value) => value,
            };
            if matches!(name, "ISERR" | "ISERROR" | "ISNA") {
                return Ok(Value::Bool(match value {
                    Value::Error(error) => error_predicate_matches(name, &error),
                    _ => false,
                }));
            }
            let result = match name {
                "ISBLANK" => matches!(value, Value::Blank),
                "ISNUMBER" => matches!(value, Value::Number(_)),
                "ISTEXT" => matches!(value, Value::Text(_)),
                "ISLOGICAL" => matches!(value, Value::Bool(_)),
                _ => unreachable!(),
            };
            return Ok(Value::Bool(result));
        }
        "COUNTBLANK" => {
            arity(name, args, 1, 1)?;
            let evaluated = eval(&args[0], env)?;
            let cells = match evaluated {
                CalcValue::Range(rows) => rows.into_iter().flatten().collect::<Vec<_>>(),
                CalcValue::Array(array) => array.rows.into_iter().flatten().collect::<Vec<_>>(),
                CalcValue::Scalar(Value::Error(error)) => return Err(error),
                CalcValue::Scalar(value) => vec![value],
            };
            let count = cells
                .iter()
                .filter(|value| {
                    matches!(value, Value::Blank)
                        || matches!(value, Value::Text(text) if text.is_empty())
                })
                .count();
            return Ok(Value::Number(count as f64));
        }
        "AND" | "OR" => {
            arity(name, args, 1, 255)?;
            let want = name == "AND";
            for arg in args {
                for value in flatten(eval(arg, env)?) {
                    let b = truthy(&value)?;
                    if b != want {
                        return Ok(Value::Bool(!want));
                    }
                }
            }
            return Ok(Value::Bool(want));
        }
        _ => {}
    }
    let values: Vec<CalcValue> = args
        .iter()
        .map(|a| eval(a, env))
        .collect::<Result<_, _>>()?;
    if values
        .iter()
        .any(|value| matches!(value, CalcValue::Array(_)))
        && !matches!(
            name,
            "SUM" | "AVERAGE" | "COUNT" | "COUNTA" | "MIN" | "MAX" | "CONCAT" | "TEXTJOIN"
        )
    {
        return Err(FormulaError::Value);
    }
    if matches!(
        name,
        "UPPER" | "LOWER" | "TRIM" | "SUBSTITUTE" | "FIND" | "SEARCH"
    ) && values
        .iter()
        .any(|value| matches!(value, CalcValue::Range(_) | CalcValue::Array(_)))
    {
        return Err(FormulaError::Value);
    }
    let flat: Vec<Value> = values.iter().cloned().flat_map(flatten).collect();
    match name {
        "SUM" | "AVERAGE" | "COUNT" | "COUNTA" | "MIN" | "MAX" => {
            arity(name, args, 1, 255)?;
            let nums: Vec<f64> = flat
                .iter()
                .filter_map(|v| match v {
                    Value::Number(n) => Some(*n),
                    Value::Error(_) => None,
                    _ => None,
                })
                .collect();
            if let Some(Value::Error(e)) = flat.iter().find(|v| matches!(v, Value::Error(_))) {
                return Err(e.clone());
            }
            let n = match name {
                "COUNT" => flat
                    .iter()
                    .filter(|v| matches!(v, Value::Number(_)))
                    .count() as f64,
                "COUNTA" => flat.iter().filter(|v| !matches!(v, Value::Blank)).count() as f64,
                "SUM" => nums.iter().sum(),
                "AVERAGE" => {
                    if nums.is_empty() {
                        return Err(FormulaError::Div0);
                    } else {
                        nums.iter().sum::<f64>() / nums.len() as f64
                    }
                }
                "MIN" if nums.is_empty() => 0.0,
                "MIN" => nums.iter().copied().fold(f64::INFINITY, f64::min),
                "MAX" if nums.is_empty() => 0.0,
                "MAX" => nums.iter().copied().fold(f64::NEG_INFINITY, f64::max),
                _ => unreachable!(),
            };
            return Ok(Value::Number(n));
        }
        "NOT" => {
            arity(name, args, 1, 1)?;
            return Ok(Value::Bool(!truthy(&one(name, &flat)?)?));
        }
        "ABS" => {
            arity(name, args, 1, 1)?;
            return Ok(Value::Number(to_number(&one(name, &flat)?)?.abs()));
        }
        "ROUND" => {
            arity(name, args, 2, 2)?;
            let number = to_number(&flat[0])?;
            let digits = decimal_digits(&flat[1])?;
            return Ok(Value::Number(round_to_precision(
                number, digits, "nearest",
            )?));
        }
        "ROUNDUP" | "ROUNDDOWN" => {
            arity(name, args, 2, 2)?;
            let number = to_number(&flat[0])?;
            let digits = decimal_digits(&flat[1])?;
            let mode = if name == "ROUNDUP" {
                "away-from-zero"
            } else {
                "toward-zero"
            };
            return Ok(Value::Number(round_to_precision(number, digits, mode)?));
        }
        "INT" => {
            arity(name, args, 1, 1)?;
            let number = to_number(&flat[0])?;
            return if number.is_finite() {
                Ok(Value::Number(number.floor()))
            } else {
                Err(FormulaError::Num)
            };
        }
        "MOD" => {
            arity(name, args, 2, 2)?;
            let number = to_number(&flat[0])?;
            let divisor = to_number(&flat[1])?;
            if !number.is_finite() || !divisor.is_finite() {
                return Err(FormulaError::Num);
            }
            if divisor == 0.0 {
                return Err(FormulaError::Div0);
            }
            let quotient = number / divisor;
            if !quotient.is_finite() {
                return Err(FormulaError::Num);
            }
            let result = number - divisor * quotient.floor();
            return if result.is_finite() {
                Ok(Value::Number(result))
            } else {
                Err(FormulaError::Num)
            };
        }
        "QUOTIENT" => {
            arity(name, args, 2, 2)?;
            let number = to_number(&flat[0])?;
            let divisor = to_number(&flat[1])?;
            if !number.is_finite() || !divisor.is_finite() {
                return Err(FormulaError::Num);
            }
            if divisor == 0.0 {
                return Err(FormulaError::Div0);
            }
            let quotient = number / divisor;
            if quotient.is_finite() {
                return Ok(Value::Number(quotient.trunc()));
            }
            return Err(FormulaError::Num);
        }
        "LEFT" | "RIGHT" => {
            arity(name, args, 1, 2)?;
            let text = to_text(&flat[0]);
            let count = if flat.len() == 1 {
                1
            } else {
                to_number(&flat[1])? as isize
            };
            if count < 0 {
                return Err(FormulaError::Value);
            }
            let chars: Vec<char> = text.chars().collect();
            let count = (count as usize).min(chars.len());
            let out: String = if name == "LEFT" {
                chars[..count].iter().collect()
            } else {
                chars[chars.len() - count..].iter().collect()
            };
            return Ok(Value::Text(out));
        }
        "MID" => {
            arity(name, args, 3, 3)?;
            let chars: Vec<char> = to_text(&flat[0]).chars().collect();
            let start = to_number(&flat[1])? as isize;
            let count = to_number(&flat[2])? as isize;
            if start < 1 || count < 0 {
                return Err(FormulaError::Value);
            }
            let begin = ((start - 1) as usize).min(chars.len());
            let end = (begin + count as usize).min(chars.len());
            return Ok(Value::Text(chars[begin..end].iter().collect()));
        }
        "LEN" => {
            arity(name, args, 1, 1)?;
            return Ok(Value::Number(
                to_text(&one(name, &flat)?).chars().count() as f64
            ));
        }
        "ISBLANK" | "ISNUMBER" | "ISTEXT" | "ISLOGICAL" | "ISERR" | "ISERROR" | "ISNA" => {
            unreachable!("type predicates are handled before eager argument evaluation: {name}");
        }
        "UPPER" | "LOWER" => {
            arity(name, args, 1, 1)?;
            let value = one(name, &flat)?;
            propagate_value_error(&value)?;
            let text = to_text(&value);
            return Ok(Value::Text(if name == "UPPER" {
                text.to_uppercase()
            } else {
                text.to_lowercase()
            }));
        }
        "TRIM" => {
            arity(name, args, 1, 1)?;
            let value = one(name, &flat)?;
            propagate_value_error(&value)?;
            let text = to_text(&value);
            let normalized = text
                .split(' ')
                .filter(|part| !part.is_empty())
                .collect::<Vec<_>>()
                .join(" ");
            return Ok(Value::Text(normalized));
        }
        "SUBSTITUTE" => {
            arity(name, args, 3, 4)?;
            for value in flat.iter().take(4) {
                propagate_value_error(value)?;
            }
            let text = to_text(&flat[0]);
            let old = to_text(&flat[1]);
            let new = to_text(&flat[2]);
            if old.is_empty() {
                return Err(FormulaError::Value);
            }
            let Some(occurrence) = flat.get(3) else {
                return substitute_all_bounded(&text, &old, &new, MAX_TEXT_LENGTH_UNITS)
                    .map(Value::Text);
            };
            let ordinal = to_number(occurrence)?;
            if ordinal < 1.0 || ordinal.fract() != 0.0 || ordinal > usize::MAX as f64 {
                return Err(FormulaError::Value);
            }
            let Some((start, _)) = text.match_indices(&old).nth(ordinal as usize - 1) else {
                return Ok(Value::Text(text));
            };
            let result_units = utf16_text_units(&text)
                .saturating_sub(utf16_text_units(&old))
                .saturating_add(utf16_text_units(&new));
            check_text_units(result_units, MAX_TEXT_LENGTH_UNITS)?;
            let mut output = String::with_capacity(
                text.len()
                    .saturating_sub(old.len())
                    .saturating_add(new.len()),
            );
            output.push_str(&text[..start]);
            output.push_str(&new);
            output.push_str(&text[start + old.len()..]);
            return Ok(Value::Text(output));
        }
        "FIND" | "SEARCH" => {
            arity(name, args, 2, 3)?;
            for value in flat.iter().take(3) {
                propagate_value_error(value)?;
            }
            let needle = to_text(&flat[0]);
            let text = to_text(&flat[1]);
            let start = if flat.len() == 3 {
                to_number(&flat[2])?
            } else {
                1.0
            };
            if start < 1.0 || start.fract() != 0.0 || start > usize::MAX as f64 {
                return Err(FormulaError::Value);
            }
            let start = start as usize;
            let text_chars: Vec<char> = text.chars().collect();
            if start > text_chars.len() + 1 {
                return Err(FormulaError::Value);
            }
            let byte_offset = text_chars
                .iter()
                .take(start - 1)
                .map(|ch| ch.len_utf8())
                .sum::<usize>();
            let haystack = &text[byte_offset..];
            let matched_char_index = if name == "FIND" {
                haystack
                    .find(&needle)
                    .map(|offset| haystack[..offset].chars().count())
            } else {
                let folded_needle = excel_casefold(&needle);
                haystack
                    .char_indices()
                    .find_map(|(offset, _)| {
                        excel_casefold(&haystack[offset..])
                            .starts_with(&folded_needle)
                            .then(|| haystack[..offset].chars().count())
                    })
                    .or_else(|| needle.is_empty().then_some(0))
            };
            let Some(matched_char_index) = matched_char_index else {
                return Err(FormulaError::Value);
            };
            let position = start + matched_char_index;
            return Ok(Value::Number(position as f64));
        }
        "TEXTJOIN" => {
            arity(name, args, 3, 254)?;
            for value in flat.iter().take(2) {
                propagate_value_error(value)?;
            }
            let delimiter = to_text(&flat[0]);
            let ignore_empty = truthy(&flat[1])?;
            let mut parts = Vec::new();
            for value in flat.iter().skip(2) {
                if let Value::Error(error) = value {
                    return Err(error.clone());
                }
                parts.push(value);
            }
            let result =
                join_values_bounded(&parts, &delimiter, ignore_empty, MAX_TEXT_LENGTH_UNITS)?;
            return Ok(Value::Text(result));
        }
        "CONCAT" => {
            arity(name, args, 1, 253)?;
            if let Some(Value::Error(error)) =
                flat.iter().find(|value| matches!(value, Value::Error(_)))
            {
                return Err(error.clone());
            }
            let values: Vec<&Value> = flat.iter().collect();
            return join_values_bounded(&values, "", false, MAX_TEXT_LENGTH_UNITS).map(Value::Text);
        }
        "DATE" => {
            arity(name, args, 3, 3)?;
            return date_value(
                to_number(&flat[0])? as i32,
                to_number(&flat[1])? as i32,
                to_number(&flat[2])? as i32,
            );
        }
        "MONTH" | "DAY" | "YEAR" => {
            arity(name, args, 1, 1)?;
            let date = excel_serial_ymd(to_number(&one(name, &flat)?)?)?;
            let value = match name {
                "MONTH" => date.1,
                "DAY" => date.2,
                _ => date.0,
            };
            return Ok(Value::Number(value as f64));
        }
        "DAYS" => {
            arity(name, args, 2, 2)?;
            let end_date = to_number(&flat[0])?;
            let start_date = to_number(&flat[1])?;
            excel_serial_ymd(end_date)?;
            excel_serial_ymd(start_date)?;
            let difference = end_date - start_date;
            return if difference.is_finite() {
                Ok(Value::Number(difference))
            } else {
                Err(FormulaError::Num)
            };
        }
        "EDATE" | "EOMONTH" => {
            arity(name, args, 2, 2)?;
            let start_serial = to_number(&flat[0])?;
            let months = to_number(&flat[1])?;
            if !months.is_finite() {
                return Err(FormulaError::Num);
            }
            let (year, month, day) = match excel_serial_ymd(start_serial) {
                Ok(date) => date,
                Err(error) if name == "EDATE" && error == FormulaError::Num => {
                    if !start_serial.is_finite() {
                        return Err(FormulaError::Num);
                    }
                    return Err(FormulaError::Value);
                }
                Err(error) => return Err(error),
            };
            let month_offset = months.trunc();
            // Any offset outside this bound must leave Excel's supported date range.
            if month_offset.abs() > 120_000.0 {
                return Err(FormulaError::Num);
            }
            let target_index = year as i64 * 12 + month as i64 - 1 + month_offset as i64;
            let target_year = target_index.div_euclid(12) as i32;
            let target_month = (target_index.rem_euclid(12) + 1) as i32;
            if !(1..=9999).contains(&target_year) {
                return Err(FormulaError::Num);
            }
            let target_day = if name == "EDATE" {
                day.min(days_in_month(target_year, target_month))
            } else {
                days_in_month(target_year, target_month)
            };
            return Ok(Value::Number(
                excel_ymd_to_serial(target_year, target_month, target_day)? as f64,
            ));
        }
        "MATCH" => return match_fn(args, &values, name, &env.wildcard_work_remaining),
        "INDEX" => return index_fn(args, &values, name),
        "VLOOKUP" => return vlookup_fn(args, &values, name, &env.wildcard_work_remaining),
        "COUNTIF" | "COUNTIFS" | "SUMIF" | "SUMIFS" | "AVERAGEIF" | "AVERAGEIFS" | "MINIFS"
        | "MAXIFS" => {
            return criteria_function(name, args, &values, &env.wildcard_work_remaining);
        }
        other => Err(FormulaError::Unsupported(format!("function {other}"))),
    }
}

fn week_call(name: &str, args: &[Expr], env: &Environment<'_>) -> Result<Value, FormulaError> {
    if name == "ISOWEEKNUM" {
        arity(name, args, 1, 1)?;
    } else {
        arity(name, args, 1, 2)?;
    }
    let serial = to_number(&eval_scalar(&args[0], env)?)?;
    let (year, month, day) = excel_serial_ymd(serial)?;
    let serial_day = serial.floor() as i64;
    let day_of_year = excel_day_of_year(serial_day, year, month, day);
    let weekday_sunday_zero = excel_weekday_sunday_zero(serial_day);
    if name == "ISOWEEKNUM" {
        return Ok(Value::Number(
            excel_iso_week_number(year, day_of_year, weekday_sunday_zero) as f64,
        ));
    }

    let selector = match args.get(1) {
        None | Some(Expr::Missing) => 1,
        Some(argument) => {
            let value = to_number(&eval_scalar(argument, env)?)?;
            if !value.is_finite() {
                return Err(FormulaError::Num);
            }
            value.trunc() as i64
        }
    };
    if name == "WEEKDAY" {
        let week_start = match selector {
            1 | 17 => 0,
            2 | 3 | 11 => 1,
            12 => 2,
            13 => 3,
            14 => 4,
            15 => 5,
            16 => 6,
            _ => return Err(FormulaError::Num),
        };
        let offset = (weekday_sunday_zero - week_start).rem_euclid(7);
        let result = if selector == 3 { offset } else { offset + 1 };
        return Ok(Value::Number(result as f64));
    }

    if selector == 21 {
        return Ok(Value::Number(
            excel_iso_week_number(year, day_of_year, weekday_sunday_zero) as f64,
        ));
    }
    let week_start = match selector {
        1 | 17 => 0,
        2 | 11 => 1,
        12 => 2,
        13 => 3,
        14 => 4,
        15 => 5,
        16 => 6,
        _ => return Err(FormulaError::Num),
    };
    let jan1_position = (excel_jan1_sunday_zero(year) - week_start).rem_euclid(7);
    let result = (jan1_position + day_of_year - 1).div_euclid(7) + 1;
    Ok(Value::Number(result as f64))
}

fn day_count_call(name: &str, args: &[Expr], env: &Environment<'_>) -> Result<Value, FormulaError> {
    arity(name, args, 2, 3)?;
    let start = day_count_serial(
        &eval_scalar(&args[0], env)?,
        if name == "DAYS360" {
            FormulaError::Num
        } else {
            FormulaError::Value
        },
    )?;
    let end = day_count_serial(
        &eval_scalar(&args[1], env)?,
        if name == "DAYS360" {
            FormulaError::Num
        } else {
            FormulaError::Value
        },
    )?;

    if name == "DAYS360" {
        let european = match args.get(2) {
            None | Some(Expr::Missing) => false,
            Some(expression) => match eval_scalar(expression, env)? {
                Value::Bool(value) => value,
                Value::Error(error) => return Err(error),
                Value::Blank => false,
                Value::Number(0.0) => false,
                Value::Number(1.0) => true,
                _ => return Err(FormulaError::Value),
            },
        };
        return Ok(Value::Number(days_360_between(start, end, european) as f64));
    }

    let basis = match args.get(2) {
        None | Some(Expr::Missing) => 0,
        Some(expression) => {
            let value = to_number(&eval_scalar(expression, env)?)?;
            if !value.is_finite() {
                return Err(FormulaError::Num);
            }
            let truncated = value.trunc();
            if !(0.0..=4.0).contains(&truncated) {
                return Err(FormulaError::Num);
            }
            truncated as i32
        }
    };
    let year_fraction = match basis {
        0 => days_360_between(start, end, false) as f64 / 360.0,
        1 => actual_actual_fraction(start, end),
        2 => (end - start) as f64 / 360.0,
        3 => (end - start) as f64 / 365.0,
        4 => days_360_between(start, end, true) as f64 / 360.0,
        _ => unreachable!("basis was checked above"),
    };
    Ok(Value::Number(year_fraction))
}

#[derive(Clone, Copy, Debug)]
struct CouponSchedule {
    previous: i64,
    previous_strict: i64,
    next: i64,
    coupons_remaining: i64,
}

fn coupon_call(name: &str, args: &[Expr], env: &Environment<'_>) -> Result<Value, FormulaError> {
    arity(name, args, 3, 4)?;
    let settlement_value = eval_scalar(&args[0], env)?;
    let settlement = day_count_serial(&settlement_value, FormulaError::Value)?;
    let maturity_value = eval_scalar(&args[1], env)?;
    let maturity = day_count_serial(&maturity_value, FormulaError::Value)?;
    let frequency = coupon_integer(&eval_scalar(&args[2], env)?)?;
    if ![1, 2, 4].contains(&frequency) {
        return Err(FormulaError::Num);
    }
    let basis = match args.get(3) {
        None | Some(Expr::Missing) => 0,
        Some(expression) => coupon_integer(&eval_scalar(expression, env)?)?,
    };
    if !(0..=4).contains(&basis) {
        return Err(FormulaError::Num);
    }
    if settlement >= maturity {
        return Err(FormulaError::Num);
    }

    let schedule = coupon_schedule(settlement, maturity, frequency)?;
    let days_bs = if basis == 0 || basis == 4 {
        days_360_between(schedule.previous, settlement, basis == 4)
    } else {
        settlement - schedule.previous
    };
    let result = match name {
        "COUPDAYBS" => days_bs as f64,
        "COUPDAYS" => {
            if basis == 1 {
                (schedule.next - schedule.previous) as f64
            } else if basis == 3 {
                365.0 / frequency as f64
            } else {
                (360 / frequency) as f64
            }
        }
        "COUPDAYSNC" => {
            if basis == 0 {
                (360 / frequency - days_bs) as f64
            } else if basis == 4 {
                days_360_between(settlement, schedule.next, true) as f64
            } else {
                (schedule.next - settlement) as f64
            }
        }
        "COUPNCD" => schedule.next as f64,
        "COUPNUM" => schedule.coupons_remaining as f64,
        "COUPPCD" => schedule.previous_strict as f64,
        _ => return Err(FormulaError::Name),
    };
    Ok(Value::Number(result))
}

fn nper_call(args: &[Expr], env: &Environment<'_>) -> Result<Value, FormulaError> {
    arity("NPER", args, 3, 5)?;
    let mut raw_values = Vec::with_capacity(5);
    for (index, expression) in args.iter().enumerate() {
        let value = match expression {
            Expr::Missing if index >= 3 => Value::Blank,
            Expr::Missing => return Err(FormulaError::Value),
            _ => eval_scalar(expression, env)?,
        };
        raw_values.push(value);
    }
    if let Some(error) = raw_values.iter().find_map(|value| match value {
        Value::Error(error) => Some(error.clone()),
        _ => None,
    }) {
        return Err(error);
    }
    let mut values = Vec::with_capacity(5);
    for value in &raw_values {
        let number = to_number(value)?;
        if !number.is_finite() {
            return Err(FormulaError::Num);
        }
        values.push(number);
    }
    values.resize(5, 0.0);
    let [rate, payment, present_value, future_value, payment_type]: [f64; 5] =
        values.try_into().expect("NPER arity was checked above");
    if rate <= -1.0 {
        return Err(FormulaError::Num);
    }
    if payment_type != 0.0 && payment_type != 1.0 {
        return Err(FormulaError::Num);
    }

    let scale = payment
        .abs()
        .max(present_value.abs())
        .max(future_value.abs());
    if scale == 0.0 {
        return Err(FormulaError::Num);
    }
    let scaled_payment = payment / scale;
    let scaled_present = present_value / scale;
    let scaled_future = future_value / scale;

    let result = if rate == 0.0 {
        let total_value = scaled_present + scaled_future;
        if payment == 0.0 {
            return Err(FormulaError::Num);
        }
        if total_value == 0.0 {
            0.0
        } else if scaled_payment == 0.0 {
            return Err(FormulaError::Num);
        } else {
            -total_value / scaled_payment
        }
    } else {
        let rate_scale = 1.0_f64.max(rate.abs());
        let scaled_rate = rate / rate_scale;
        let payment_factor = (1.0 + rate * payment_type) / rate_scale;
        let numerator = scaled_payment * payment_factor - scaled_future * scaled_rate;
        let denominator = scaled_payment * payment_factor + scaled_present * scaled_rate;
        if !numerator.is_finite() || !denominator.is_finite() {
            return Err(FormulaError::Num);
        }
        if numerator == 0.0 || denominator == 0.0 {
            return Err(FormulaError::Num);
        }
        if numerator.is_sign_negative() != denominator.is_sign_negative() {
            return Err(FormulaError::Num);
        }

        // Preserve tiny rate effects before rounding the numerator/denominator ratio.
        let ratio_delta = -(scaled_rate * (scaled_present + scaled_future) / denominator);
        let log_ratio = if ratio_delta.is_finite() && ratio_delta > -1.0 {
            ratio_delta.ln_1p()
        } else {
            // A log difference avoids overflow in the raw ratio.
            numerator.abs().ln() - denominator.abs().ln()
        };
        log_ratio / rate.ln_1p()
    };
    if !result.is_finite() {
        return Err(FormulaError::Num);
    }
    Ok(Value::Number(result))
}

fn depreciation_call(
    name: &str,
    args: &[Expr],
    env: &Environment<'_>,
) -> Result<Value, FormulaError> {
    let expected_arity = match name {
        "SLN" => (3, 3),
        "SYD" => (4, 4),
        "DB" | "DDB" => (4, 5),
        _ => unreachable!("depreciation_call is only used for depreciation functions"),
    };
    arity(name, args, expected_arity.0, expected_arity.1)?;

    let mut raw_values = Vec::with_capacity(args.len());
    for expression in args {
        if matches!(expression, Expr::Missing) {
            return Err(FormulaError::Value);
        }
        raw_values.push(eval_scalar(expression, env)?);
    }
    if let Some(error) = raw_values.iter().find_map(|value| match value {
        Value::Error(error) => Some(error.clone()),
        _ => None,
    }) {
        return Err(error);
    }

    let mut values = Vec::with_capacity(raw_values.len());
    for value in &raw_values {
        let number = to_number(value)?;
        if !number.is_finite() {
            return Err(FormulaError::Num);
        }
        values.push(number);
    }

    let cost = values[0];
    let salvage = values[1];
    let life = values[2];
    if name == "DB" {
        let month = if values.len() == 4 { 12.0 } else { values[4] };
        return db_depreciation(cost, salvage, life, values[3], month);
    }
    if name == "DDB" {
        let factor = if values.len() == 4 { 2.0 } else { values[4] };
        return ddb_depreciation(cost, salvage, life, values[3], factor);
    }
    if cost < 0.0 || salvage < 0.0 || life <= 0.0 {
        return Err(FormulaError::Num);
    }

    let depreciable_basis = cost - salvage;
    let result = if name == "SLN" {
        depreciable_basis / life
    } else {
        let period = values[3];
        if period <= 0.0 || period > life {
            return Err(FormulaError::Num);
        }
        // The weight stays below 2 across the accepted period range. Scale
        // the basis by life first so tiny fractional lives do not overflow
        // the intermediate 2/life when the final result is still finite.
        let period_weight = 2.0 * ((life - period + 1.0) / (life + 1.0));
        (depreciable_basis / life) * period_weight
    };
    if !result.is_finite() {
        return Err(FormulaError::Num);
    }
    Ok(Value::Number(result))
}

fn amortization_call(
    name: &str,
    args: &[Expr],
    env: &Environment<'_>,
) -> Result<Value, FormulaError> {
    arity(name, args, 6, 7)?;

    let mut raw_values = Vec::with_capacity(args.len());
    let mut missing_required = false;
    for (index, expression) in args.iter().enumerate() {
        match expression {
            Expr::Missing if index == 6 => raw_values.push(Some(Value::Blank)),
            Expr::Missing => {
                raw_values.push(None);
                missing_required = true;
            }
            _ => raw_values.push(Some(eval_scalar(expression, env)?)),
        }
    }
    if let Some(error) =
        raw_values
            .iter()
            .filter_map(Option::as_ref)
            .find_map(|value| match value {
                Value::Error(error) => Some(error.clone()),
                _ => None,
            })
    {
        return Err(error);
    }
    if missing_required {
        return Err(FormulaError::Value);
    }
    let value_at = |index: usize| {
        raw_values[index]
            .as_ref()
            .expect("required amortization argument was checked above")
    };

    let cost = to_number(value_at(0))?;
    let purchase = day_count_serial(value_at(1), FormulaError::Value)?;
    let first_period = day_count_serial(value_at(2), FormulaError::Value)?;
    let salvage = to_number(value_at(3))?;
    let period_value = to_number(value_at(4))?;
    let rate = to_number(value_at(5))?;
    let basis_value = if args.len() == 7 {
        to_number(value_at(6))?
    } else {
        0.0
    };
    if [cost, salvage, rate, period_value, basis_value]
        .iter()
        .any(|value| !value.is_finite())
    {
        return Err(FormulaError::Num);
    }

    if cost <= 0.0 || salvage < 0.0 || salvage >= cost || rate <= 0.0 || purchase >= first_period {
        return Err(FormulaError::Num);
    }
    let period = period_value.trunc();
    if !(0.0..=100_000.0).contains(&period) {
        return Err(FormulaError::Num);
    }
    let period = period as i64;

    let basis_value = basis_value.trunc();
    if basis_value != 0.0 && basis_value != 1.0 && basis_value != 3.0 && basis_value != 4.0 {
        return Err(FormulaError::Num);
    }
    let basis = basis_value as i32;

    let depreciable_basis = cost - salvage;
    let stub_fraction = amortization_stub_fraction(purchase, first_period, basis)?;
    let result = match name {
        "AMORLINC" => amorlinc_amount(cost, depreciable_basis, rate, period, stub_fraction)?,
        "AMORDEGRC" => amordegrc_amount(
            cost,
            salvage,
            depreciable_basis,
            rate,
            period,
            stub_fraction,
        )?,
        _ => return Err(FormulaError::Name),
    };
    if !result.is_finite() {
        return Err(FormulaError::Num);
    }
    Ok(Value::Number(result))
}

fn amortization_stub_fraction(
    purchase: i64,
    first_period: i64,
    basis: i32,
) -> Result<f64, FormulaError> {
    let fraction = match basis {
        0 | 4 => days_360_between(purchase, first_period, basis == 4) as f64 / 360.0,
        1 => {
            let year_days = if amortization_interval_contains_february_29(purchase, first_period)? {
                366.0
            } else {
                365.0
            };
            (first_period - purchase) as f64 / year_days
        }
        3 => (first_period - purchase) as f64 / 365.0,
        _ => return Err(FormulaError::Num),
    };
    if !fraction.is_finite() {
        return Err(FormulaError::Num);
    }
    Ok(fraction)
}

fn amortization_interval_contains_february_29(
    purchase: i64,
    first_period: i64,
) -> Result<bool, FormulaError> {
    let (purchase_year, _, _) = serial_to_date(purchase)?;
    let (first_period_year, _, _) = serial_to_date(first_period)?;
    for year in purchase_year..=first_period_year {
        // The shared date profile explicitly preserves Excel's serial-60
        // compatibility date as the fictional 1900-02-29.
        let leap_day = if year == 1900 {
            Some(60)
        } else if is_gregorian_leap_year(year) {
            Some(excel_ymd_to_serial(year, 2, 29)?)
        } else {
            None
        };
        if leap_day.is_some_and(|serial| (purchase..=first_period).contains(&serial)) {
            return Ok(true);
        }
    }
    Ok(false)
}

fn capped_positive_product(limit: f64, factors: [f64; 3]) -> f64 {
    if limit <= 0.0 || factors.contains(&0.0) {
        return 0.0;
    }

    let mut product = 1.0;
    for factor in factors {
        product *= factor;
        if product.is_infinite() {
            let log_product = factors.iter().map(|factor| factor.ln()).sum::<f64>();
            if log_product >= limit.ln() {
                return limit;
            }
            return log_product.exp().min(limit);
        }
        if product == 0.0 {
            return 0.0;
        }
    }
    product.min(limit)
}

fn amorlinc_amount(
    cost: f64,
    depreciable_basis: f64,
    rate: f64,
    period: i64,
    stub_fraction: f64,
) -> Result<f64, FormulaError> {
    let raw_life = 1.0 / rate;
    if !raw_life.is_finite() {
        return Err(FormulaError::Num);
    }
    let effective_life = raw_life.ceil();
    if !effective_life.is_finite() || effective_life > MAX_EXACT_DEPRECIATION_PERIOD {
        return Err(FormulaError::Num);
    }
    if period as f64 > effective_life {
        return Ok(0.0);
    }

    let prorated_stub = capped_positive_product(depreciable_basis, [cost, rate, stub_fraction]);
    let stub = if prorated_stub == 0.0 {
        capped_positive_product(depreciable_basis, [cost, rate, 1.0])
    } else {
        prorated_stub
    };
    if period == 0 {
        return Ok(stub);
    }

    let first_period_basis = (depreciable_basis - stub).max(0.0);
    let amount_per_period = capped_positive_product(depreciable_basis, [cost, rate, 1.0]);
    let prior_amount = capped_positive_product(
        first_period_basis,
        [amount_per_period, (period - 1) as f64, 1.0],
    );
    let remaining = (first_period_basis - prior_amount).max(0.0);
    let result = amount_per_period.min(remaining);
    if !result.is_finite() {
        return Err(FormulaError::Num);
    }
    Ok(result)
}

fn amordegrc_amount(
    cost: f64,
    salvage: f64,
    depreciable_basis: f64,
    rate: f64,
    period: i64,
    stub_fraction: f64,
) -> Result<f64, FormulaError> {
    let raw_life = 1.0 / rate;
    if !raw_life.is_finite() || raw_life <= 3.0 || (4.0..=5.0).contains(&raw_life) {
        return Err(FormulaError::Num);
    }
    let effective_life = raw_life.ceil();
    if !effective_life.is_finite() || effective_life > MAX_EXACT_DEPRECIATION_PERIOD {
        return Err(FormulaError::Num);
    }
    let coefficient = if (3.0..=4.0).contains(&effective_life) {
        1.5
    } else if (5.0..=6.0).contains(&effective_life) {
        2.0
    } else if effective_life > 6.0 {
        2.5
    } else {
        1.0
    };
    let effective_rate = rate * coefficient;
    if !effective_rate.is_finite() {
        return Err(FormulaError::Num);
    }
    if period as f64 > effective_life {
        return Ok(0.0);
    }

    let prorated_stub =
        capped_positive_product(depreciable_basis, [cost, effective_rate, stub_fraction]);
    let stub_unrounded = if prorated_stub == 0.0 {
        capped_positive_product(depreciable_basis, [cost, effective_rate, 1.0])
    } else {
        prorated_stub
    };
    let rounded_stub = if stub_unrounded >= depreciable_basis {
        depreciable_basis
    } else {
        stub_unrounded.round().min(depreciable_basis)
    };
    if period == 0 {
        return Ok(rounded_stub);
    }

    let mut remaining = cost - rounded_stub;
    let mut depreciation_rate = effective_rate;
    let mut requested_amount = 0.0;
    let schedule_life = effective_life + f64::from(prorated_stub > 0.0);
    for counted_period in 2..=(period + 1) {
        let calc_t = schedule_life - counted_period as f64;
        let mut amount = if calc_t == 2.0 {
            depreciation_rate = 1.0;
            remaining * 0.5
        } else {
            remaining * depreciation_rate
        };
        if !amount.is_finite() {
            return Err(FormulaError::Num);
        }
        if remaining < salvage {
            amount = amount.min((remaining - salvage).max(0.0));
        }
        amount = amount.round();
        if !amount.is_finite() {
            return Err(FormulaError::Num);
        }
        remaining -= amount;
        if !remaining.is_finite() {
            return Err(FormulaError::Num);
        }
        requested_amount = amount;
    }
    Ok(requested_amount)
}

const MAX_EXACT_DEPRECIATION_PERIOD: f64 = 9_007_199_254_740_991.0;

fn depreciation_period(value: f64) -> Result<i64, FormulaError> {
    if value.abs() > MAX_EXACT_DEPRECIATION_PERIOD {
        return Err(FormulaError::Num);
    }
    Ok(value.trunc() as i64)
}

fn db_rate(cost: f64, salvage: f64, life: i64) -> f64 {
    if salvage == 0.0 {
        return 1.0;
    }
    let ratio = salvage / cost;
    let log_ratio = if ratio > 0.5 {
        ((salvage - cost) / cost).ln_1p()
    } else {
        salvage.ln() - cost.ln()
    };
    let raw_rate = -(log_ratio / life as f64).exp_m1();
    (raw_rate * 1000.0 + 0.5).floor() / 1000.0
}

fn db_depreciation(
    cost: f64,
    salvage: f64,
    life: f64,
    period: f64,
    month: f64,
) -> Result<Value, FormulaError> {
    if cost <= 0.0 || salvage < 0.0 || salvage > cost {
        return Err(FormulaError::Num);
    }
    let life = depreciation_period(life)?;
    let period = depreciation_period(period)?;
    let month = depreciation_period(month)?;
    if life < 1 || period < 1 || period > life + 1 || !(1..=12).contains(&month) {
        return Err(FormulaError::Num);
    }

    let rate = db_rate(cost, salvage, life);
    let first = cost * rate * (month as f64 / 12.0);
    let remaining_after_first = cost - first;
    let result = if period == 1 {
        first
    } else if period == life + 1 {
        let remaining = remaining_after_first * (1.0 - rate).powf((life - 1) as f64);
        remaining * rate * ((12 - month) as f64 / 12.0)
    } else {
        let remaining = remaining_after_first * (1.0 - rate).powf((period - 2) as f64);
        remaining * rate
    };
    if !result.is_finite() {
        return Err(FormulaError::Num);
    }
    Ok(Value::Number(result))
}

fn ddb_depreciation(
    cost: f64,
    salvage: f64,
    life: f64,
    period: f64,
    factor: f64,
) -> Result<Value, FormulaError> {
    if cost < 0.0 || salvage < 0.0 || salvage > cost || factor <= 0.0 {
        return Err(FormulaError::Num);
    }
    let life = depreciation_period(life)?;
    let period = depreciation_period(period)?;
    if life < 1 || period < 1 || period > life {
        return Err(FormulaError::Num);
    }

    let rate = factor / life as f64;
    let basis = cost - salvage;
    if period == 1 && rate >= 1.0 {
        return Ok(Value::Number(basis));
    }
    if rate == 0.0 {
        if cost == 0.0 {
            return Ok(Value::Number(0.0));
        }
        // A positive factor divided by a large life can underflow to zero
        // even when cost*factor/life is representable. Compute that amount
        // in log space; the omitted geometric decay is below binary64
        // precision throughout the accepted period range for this branch.
        let amount = (cost.ln() + factor.ln() - (life as f64).ln()).exp();
        let result = amount.min(basis);
        if !result.is_finite() {
            return Err(FormulaError::Num);
        }
        return Ok(Value::Number(result));
    }
    if rate >= 1.0 {
        return Ok(Value::Number(0.0));
    }
    let book = cost * (1.0 - rate).powf((period - 1) as f64);
    let available = (book - salvage).max(0.0);
    let result = (book * rate).min(available);
    if !result.is_finite() {
        return Err(FormulaError::Num);
    }
    Ok(Value::Number(result))
}

fn vdb_switches_at(
    cost: f64,
    salvage: f64,
    life: f64,
    factor: f64,
    rate: f64,
    log_q: f64,
    period: i64,
) -> bool {
    let remaining_life = life - period as f64;
    if remaining_life <= 0.0 || cost <= salvage {
        return false;
    }
    if rate >= 1.0 {
        return period == 0 && life < 1.0;
    }

    let log_book = cost.ln() + period as f64 * log_q;
    if salvage > 0.0 && log_book <= salvage.ln() {
        return false;
    }
    if remaining_life < 1.0 {
        // The DDB amount is capped at remaining basis, while SLN spreads
        // that basis across a sub-period. The reference behavior switches.
        return true;
    }

    let log_available = if salvage == 0.0 {
        log_book
    } else {
        let delta = salvage.ln() - log_book;
        log_book + (-delta.exp_m1()).ln()
    };
    let log_ddb = log_book + factor.ln() - life.ln();
    let log_sln = log_available - remaining_life.ln();
    log_ddb < log_sln
}

fn vdb_switch_period(
    cost: f64,
    salvage: f64,
    life: f64,
    factor: f64,
    rate: f64,
    log_q: f64,
) -> Option<i64> {
    if cost <= salvage {
        return None;
    }
    let period_count = life.ceil() as i64;
    let last = period_count - 1;
    if !vdb_switches_at(cost, salvage, life, factor, rate, log_q, last) {
        return None;
    }
    let mut low = 0_i64;
    let mut high = last;
    while low < high {
        let middle = low + (high - low) / 2;
        if vdb_switches_at(cost, salvage, life, factor, rate, log_q, middle) {
            high = middle;
        } else {
            low = middle + 1;
        }
    }
    Some(low)
}

fn vdb_cap_period(cost: f64, salvage: f64, life: f64, rate: f64, log_q: f64) -> Option<i64> {
    if salvage <= 0.0 || cost <= salvage || rate == 0.0 {
        return None;
    }
    if rate >= 1.0 {
        return Some(0);
    }

    let period_count = life.ceil() as i64;
    let last = period_count - 1;
    let log_cost = cost.ln();
    let log_salvage = salvage.ln();
    let capped_after = |period: i64| log_cost + (period + 1) as f64 * log_q <= log_salvage;
    if !capped_after(last) {
        return None;
    }
    let mut low = 0_i64;
    let mut high = last;
    while low < high {
        let middle = low + (high - low) / 2;
        if capped_after(middle) {
            high = middle;
        } else {
            low = middle + 1;
        }
    }
    Some(low)
}

#[derive(Clone, Copy)]
struct VdbKernel {
    cost: f64,
    salvage: f64,
    basis: f64,
    factor: f64,
    life: f64,
    rate: f64,
    log_q: f64,
    cap_period: Option<i64>,
}

fn vdb_period_amount(kernel: &VdbKernel, period: i64) -> f64 {
    if kernel.basis <= 0.0 {
        return 0.0;
    }
    if kernel.rate >= 1.0 {
        return if period == 0 { kernel.basis } else { 0.0 };
    }
    if kernel.rate == 0.0 {
        let amount = (kernel.cost.ln() + kernel.factor.ln() - kernel.life.ln()).exp();
        let prior = amount * period as f64;
        let remaining = (kernel.basis - prior.min(kernel.basis)).max(0.0);
        return amount.min(remaining);
    }

    let log_book = kernel.cost.ln() + period as f64 * kernel.log_q;
    let book = log_book.exp();
    let available = (book - kernel.salvage).max(0.0);
    (book * kernel.rate).min(available)
}

fn vdb_geometric_sum(kernel: &VdbKernel, start: i64, count: i64) -> f64 {
    if count <= 0 {
        return 0.0;
    }
    let log_first_book = kernel.cost.ln() + start as f64 * kernel.log_q;
    let first_book = log_first_book.exp();
    let fraction_depreciated = -((count as f64 * kernel.log_q).exp_m1());
    (first_book * fraction_depreciated).max(0.0)
}

fn vdb_declining_sum(kernel: &VdbKernel, start: i64, count: i64) -> f64 {
    if count <= 0 || kernel.basis <= 0.0 {
        return 0.0;
    }
    if kernel.rate >= 1.0 {
        return if start == 0 { kernel.basis } else { 0.0 };
    }
    if kernel.rate == 0.0 {
        let amount = (kernel.cost.ln() + kernel.factor.ln() - kernel.life.ln()).exp();
        let prior = amount * start as f64;
        let already_depreciated = prior.min(kernel.basis);
        let available = (kernel.basis - already_depreciated).max(0.0);
        return (amount * count as f64).min(available);
    }

    let uncapped_count = kernel
        .cap_period
        .map_or(count, |cap| count.min((cap - start).max(0)));
    let mut total = vdb_geometric_sum(kernel, start, uncapped_count);
    if let Some(cap) = kernel.cap_period
        && start <= cap
        && cap < start + count
    {
        let log_cap_book = kernel.cost.ln() + cap as f64 * kernel.log_q;
        total += (log_cap_book.exp() - kernel.salvage).max(0.0);
    }
    total.clamp(0.0, kernel.basis)
}

fn vdb_declining_interval(kernel: &VdbKernel, start: f64, end: f64) -> f64 {
    let first_period = start.floor() as i64;
    let last_period = end.floor() as i64;
    if first_period == last_period {
        let amount = vdb_period_amount(kernel, first_period);
        return amount * (end - start);
    }

    let first_amount = vdb_period_amount(kernel, first_period);
    let mut total = first_amount * (first_period as f64 + 1.0 - start);
    let interior_start = first_period + 1;
    let interior_count = (last_period - interior_start).max(0);
    total += vdb_declining_sum(kernel, interior_start, interior_count);
    if end > last_period as f64 {
        let last_amount = vdb_period_amount(kernel, last_period);
        total += last_amount * (end - last_period as f64);
    }
    total.clamp(0.0, kernel.basis)
}

fn vdb_depreciation(
    cost: f64,
    salvage: f64,
    life: f64,
    start: f64,
    end: f64,
    factor: f64,
    no_switch: bool,
) -> Result<Value, FormulaError> {
    if [cost, salvage, life, start, end, factor]
        .iter()
        .any(|value| !value.is_finite())
    {
        return Err(FormulaError::Num);
    }
    if cost <= 0.0 || salvage < 0.0 || salvage > cost || life <= 0.0 || factor <= 0.0 {
        return Err(FormulaError::Num);
    }
    if life > MAX_EXACT_DEPRECIATION_PERIOD {
        return Err(FormulaError::Num);
    }
    if start < 0.0 || end < start || end > life {
        return Err(FormulaError::Num);
    }

    let basis = cost - salvage;
    if basis == 0.0 || start == end {
        return Ok(Value::Number(0.0));
    }

    let rate = if factor >= life { 1.0 } else { factor / life };
    let log_q = if rate < 1.0 { (-rate).ln_1p() } else { 0.0 };
    let cap_period = vdb_cap_period(cost, salvage, life, rate, log_q);
    let kernel = VdbKernel {
        cost,
        salvage,
        basis,
        factor,
        life,
        rate,
        log_q,
        cap_period,
    };
    let switch_period = if no_switch {
        None
    } else {
        vdb_switch_period(cost, salvage, life, factor, rate, log_q)
    };
    let result = if switch_period.is_none_or(|period| end <= period as f64) {
        vdb_declining_interval(&kernel, start, end)
    } else {
        let switch = switch_period.expect("checked above");
        let mut total = 0.0;
        if start < switch as f64 {
            total += vdb_declining_interval(&kernel, start, switch as f64);
        }
        let straight_start = start.max(switch as f64);
        let log_book = if rate < 1.0 {
            cost.ln() + switch as f64 * log_q
        } else {
            cost.ln()
        };
        let remaining_basis = (log_book.exp() - salvage).max(0.0);
        let remaining_life = life - switch as f64;
        total += remaining_basis * ((end - straight_start) / remaining_life);
        total.min(basis)
    };
    if !result.is_finite() {
        return Err(FormulaError::Num);
    }
    Ok(Value::Number(result))
}

fn vdb_call(args: &[Expr], env: &Environment<'_>) -> Result<Value, FormulaError> {
    arity("VDB", args, 5, 7)?;
    let mut raw_values = Vec::with_capacity(args.len());
    for (index, expression) in args.iter().enumerate() {
        let value = match expression {
            Expr::Missing if index >= 5 => Value::Blank,
            Expr::Missing => return Err(FormulaError::Value),
            _ => eval_scalar(expression, env)?,
        };
        raw_values.push(value);
    }
    if let Some(error) = raw_values.iter().find_map(|value| match value {
        Value::Error(error) => Some(error.clone()),
        _ => None,
    }) {
        return Err(error);
    }

    let mut values = Vec::with_capacity(5);
    for value in raw_values.iter().take(5) {
        let number = to_number(value)?;
        if !number.is_finite() {
            return Err(FormulaError::Num);
        }
        values.push(number);
    }
    let factor = if args.len() < 6 || matches!(args[5], Expr::Missing) {
        2.0
    } else {
        let number = to_number(&raw_values[5])?;
        if !number.is_finite() {
            return Err(FormulaError::Num);
        }
        number
    };
    let no_switch = if args.len() < 7 || matches!(args[6], Expr::Missing) {
        false
    } else {
        if matches!(raw_values[6], Value::Number(number) if !number.is_finite()) {
            return Err(FormulaError::Num);
        }
        truthy(&raw_values[6])?
    };
    vdb_depreciation(
        values[0], values[1], values[2], values[3], values[4], factor, no_switch,
    )
}

fn tvm_call(name: &str, args: &[Expr], env: &Environment<'_>) -> Result<Value, FormulaError> {
    arity(name, args, 3, 5)?;
    let may_omit_payment = matches!(name, "FV" | "PV")
        && args
            .get(3)
            .is_some_and(|endpoint| !matches!(endpoint, Expr::Missing));
    let mut raw_values = Vec::with_capacity(5);
    for (index, expression) in args.iter().enumerate() {
        let value = match expression {
            Expr::Missing if index >= 3 => Value::Blank,
            Expr::Missing if index == 2 && may_omit_payment => Value::Blank,
            Expr::Missing => return Err(FormulaError::Value),
            _ => eval_scalar(expression, env)?,
        };
        raw_values.push(value);
    }
    if let Some(error) = raw_values.iter().find_map(|value| match value {
        Value::Error(error) => Some(error.clone()),
        _ => None,
    }) {
        return Err(error);
    }
    let mut values = Vec::with_capacity(5);
    for value in &raw_values {
        let number = to_number(value)?;
        if !number.is_finite() {
            return Err(FormulaError::Num);
        }
        values.push(number);
    }
    values.resize(5, 0.0);
    let values: [f64; 5] = values.try_into().expect("TVM arity was checked above");
    tvm_values(name, values)
}

fn tvm_values(name: &str, values: [f64; 5]) -> Result<Value, FormulaError> {
    let [rate, nper, cash_input, endpoint_value, payment_type] = values;
    if values.iter().any(|value| !value.is_finite()) {
        return Err(FormulaError::Num);
    }
    if rate <= -1.0 {
        return Err(FormulaError::Num);
    }
    if nper < 0.0 {
        return Err(FormulaError::Num);
    }
    if payment_type != 0.0 && payment_type != 1.0 {
        return Err(FormulaError::Num);
    }

    let result = if rate == 0.0 {
        match name {
            "FV" | "PV" => -(endpoint_value + cash_input * nper),
            "PMT" if nper == 0.0 => return Err(FormulaError::Div0),
            "PMT" => -(cash_input + endpoint_value) / nper,
            _ => return Err(FormulaError::Name),
        }
    } else {
        let exponent = nper * rate.ln_1p();
        if !exponent.is_finite() {
            return Err(FormulaError::Num);
        }
        let payment_factor = 1.0 + rate * payment_type;
        if !payment_factor.is_finite() {
            return Err(FormulaError::Num);
        }
        match name {
            "FV" => {
                let growth = exponent.exp();
                let annuity = exponent.exp_m1() / rate;
                -(endpoint_value * growth + cash_input * payment_factor * annuity)
            }
            "PV" => {
                let discount = (-exponent).exp();
                let annuity = -(-exponent).exp_m1() / rate * payment_factor;
                -(endpoint_value * discount + cash_input * annuity)
            }
            "PMT" => {
                let discount = (-exponent).exp();
                let annuity = -(-exponent).exp_m1() / rate * payment_factor;
                if annuity == 0.0 {
                    return Err(FormulaError::Div0);
                }
                -(cash_input + endpoint_value * discount) / annuity
            }
            _ => return Err(FormulaError::Name),
        }
    };
    if !result.is_finite() {
        return Err(FormulaError::Num);
    }
    Ok(Value::Number(result))
}

fn payment_component_call(
    name: &str,
    args: &[Expr],
    env: &Environment<'_>,
) -> Result<Value, FormulaError> {
    arity(name, args, 4, 6)?;
    let mut raw_values = Vec::with_capacity(6);
    for (index, expression) in args.iter().enumerate() {
        let value = match expression {
            Expr::Missing if index >= 4 => Value::Blank,
            Expr::Missing => return Err(FormulaError::Value),
            _ => eval_scalar(expression, env)?,
        };
        raw_values.push(value);
    }
    if let Some(error) = raw_values.iter().find_map(|value| match value {
        Value::Error(error) => Some(error.clone()),
        _ => None,
    }) {
        return Err(error);
    }
    let mut values = Vec::with_capacity(6);
    for value in &raw_values {
        let number = to_number(value)?;
        if !number.is_finite() {
            return Err(FormulaError::Num);
        }
        values.push(number);
    }
    values.resize(6, 0.0);
    let values: [f64; 6] = values
        .try_into()
        .expect("IPMT/PPMT arity was checked above");
    payment_component_values(name, values)
}

fn payment_component_values(name: &str, values: [f64; 6]) -> Result<Value, FormulaError> {
    let [
        rate,
        period,
        nper,
        present_value,
        future_value,
        payment_type,
    ] = values;
    if rate <= -1.0 || (payment_type != 0.0 && payment_type != 1.0) {
        return Err(FormulaError::Num);
    }
    if period < 1.0 || period > nper {
        return Err(FormulaError::Num);
    }

    let pmt_due = match tvm_values(
        "PMT",
        [rate, nper, present_value, future_value, payment_type],
    )? {
        Value::Number(value) => value,
        _ => return Err(FormulaError::Value),
    };
    if rate == 0.0 {
        return Ok(Value::Number(if name == "IPMT" { 0.0 } else { pmt_due }));
    }
    let pmt_end = if payment_type == 0.0 {
        pmt_due
    } else {
        match tvm_values("PMT", [rate, nper, present_value, future_value, 0.0])? {
            Value::Number(value) => value,
            _ => return Err(FormulaError::Value),
        }
    };
    let exponent = (period - 1.0) * rate.ln_1p();
    if !exponent.is_finite() {
        return Err(FormulaError::Num);
    }
    let previous_growth = exponent.exp();
    let previous_growth_delta = exponent.exp_m1();
    if !previous_growth.is_finite() || !previous_growth_delta.is_finite() {
        return Err(FormulaError::Num);
    }
    let interest_end = -(present_value * rate * previous_growth + pmt_end * previous_growth_delta);
    let interest = if payment_type == 1.0 {
        if period == 1.0 {
            0.0
        } else {
            interest_end / (1.0 + rate)
        }
    } else {
        interest_end
    };
    let result = if name == "IPMT" {
        interest
    } else {
        pmt_due - interest
    };
    if !result.is_finite() {
        return Err(FormulaError::Num);
    }
    Ok(Value::Number(result))
}

fn cumulative_payment_call(
    name: &str,
    args: &[Expr],
    env: &Environment<'_>,
) -> Result<Value, FormulaError> {
    arity(name, args, 6, 6)?;
    let mut raw_values = Vec::with_capacity(6);
    for expression in args {
        raw_values.push(eval_scalar(expression, env)?);
    }
    if let Some(error) = raw_values.iter().find_map(|value| match value {
        Value::Error(error) => Some(error.clone()),
        _ => None,
    }) {
        return Err(error);
    }
    let mut values = Vec::with_capacity(6);
    for value in &raw_values {
        let number = to_number(value)?;
        if !number.is_finite() {
            return Err(FormulaError::Num);
        }
        values.push(number);
    }
    let values: [f64; 6] = values
        .try_into()
        .expect("cumulative payment arity was checked above");
    cumulative_payment_values(name, values)
}

fn cumulative_payment_values(name: &str, values: [f64; 6]) -> Result<Value, FormulaError> {
    let [
        rate,
        nper,
        present_value,
        start_period,
        end_period,
        payment_type,
    ] = values;
    if rate <= 0.0 || nper <= 0.0 || present_value <= 0.0 {
        return Err(FormulaError::Num);
    }
    if start_period < 1.0 || end_period < 1.0 || start_period > end_period {
        return Err(FormulaError::Num);
    }
    if payment_type != 0.0 && payment_type != 1.0 {
        return Err(FormulaError::Num);
    }
    const MAX_EXACT_PERIOD: f64 = 9_007_199_254_740_992.0;
    if end_period > nper || end_period > MAX_EXACT_PERIOD {
        return Err(FormulaError::Num);
    }

    let first_period = start_period.ceil();
    let last_period = end_period.floor();
    if first_period > last_period {
        return Ok(Value::Number(0.0));
    }
    let period_count = last_period - first_period + 1.0;

    let log_growth = rate.ln_1p();
    let horizon = nper * rate;
    let interest_first = if payment_type == 1.0 {
        first_period.max(2.0)
    } else {
        first_period
    };
    let cumulative_interest = if name != "CUMIPMT" || interest_first > last_period {
        0.0
    } else {
        let interest_count = last_period - interest_first + 1.0;
        let mut result = if horizon < 1e-12 {
            // The closed form approaches an average-balance limit here. Using
            // it directly avoids cancellation among tiny logarithmic terms.
            let midpoint_before_payment = (interest_first - 1.0) + (interest_count - 1.0) / 2.0;
            let remaining_fraction = (nper - midpoint_before_payment) / nper;
            let interest_factor = interest_count * remaining_fraction;
            -present_value * rate * interest_factor
        } else {
            let first_exponent = (interest_first - 1.0 - nper) * log_growth;
            let range_log = interest_count * log_growth;
            let denominator_log = -(-nper * log_growth).exp_m1();
            let log_average = first_exponent + log_exprel(range_log) + log_log1p_over_rate(rate);
            let interest_weight = -interest_count * log_average.exp_m1();
            -present_value * rate * interest_weight / denominator_log
        };
        if payment_type == 1.0 {
            result /= 1.0 + rate;
        }
        if !result.is_finite() {
            return Err(FormulaError::Num);
        }
        result
    };

    let cumulative_principal = if name != "CUMPRINC" {
        0.0
    } else if horizon < 1e-12 {
        -present_value * (period_count / nper)
    } else {
        let principal_first = if payment_type == 1.0 {
            first_period.max(2.0)
        } else {
            first_period
        };
        let mut result = 0.0;
        if principal_first <= last_period {
            let principal_count = last_period - principal_first + 1.0;
            let principal_range_log = principal_count * log_growth;
            let log_principal_magnitude = present_value.ln()
                + (last_period - nper) * log_growth
                + log1mexp_negative(principal_range_log)
                - log1mexp_negative(nper * log_growth);
            result = -log_principal_magnitude.exp();
            if payment_type == 1.0 {
                result /= 1.0 + rate;
            }
        }
        if payment_type == 1.0 && first_period <= 1.0 && last_period >= 1.0 {
            let payment_due = match tvm_values("PMT", [rate, nper, present_value, 0.0, 1.0])? {
                Value::Number(value) => value,
                _ => return Err(FormulaError::Value),
            };
            result += payment_due;
        }
        result
    };
    let result = if name == "CUMIPMT" {
        cumulative_interest
    } else {
        cumulative_principal
    };
    if !result.is_finite() {
        return Err(FormulaError::Num);
    }
    Ok(Value::Number(result))
}

fn log_exprel(value: f64) -> f64 {
    if value < 1e-4 {
        let square = value * value;
        value / 2.0 + square / 24.0 - square * square / 2880.0
            + square * square * square / 181_440.0
    } else if value > 50.0 {
        value + (-(-value).exp()).ln_1p() - value.ln()
    } else {
        (value.exp_m1() / value).ln()
    }
}

fn log_log1p_over_rate(rate: f64) -> f64 {
    if rate < 1e-4 {
        let square = rate * rate;
        -rate / 2.0 + 5.0 * square / 24.0 - square * rate / 8.0 + 251.0 * square * square / 2880.0
    } else {
        (rate.ln_1p() / rate).ln()
    }
}

fn log1mexp_negative(value: f64) -> f64 {
    if value <= std::f64::consts::LN_2 {
        (-(-value).exp_m1()).ln()
    } else {
        (-(-value).exp()).ln_1p()
    }
}

fn coupon_integer(value: &Value) -> Result<i64, FormulaError> {
    let number = to_number(value)?;
    let truncated = number.trunc();
    if !number.is_finite() || truncated < i64::MIN as f64 || truncated > i64::MAX as f64 {
        return Err(FormulaError::Num);
    }
    Ok(truncated as i64)
}

fn coupon_schedule(
    settlement: i64,
    maturity: i64,
    frequency: i64,
) -> Result<CouponSchedule, FormulaError> {
    let (settlement_year, settlement_month, _) = coupon_serial_to_date(settlement)?;
    let (maturity_year, maturity_month, maturity_day) = coupon_serial_to_date(maturity)?;
    let period_months = 12 / frequency;
    let maturity_month_index = maturity_year as i64 * 12 + maturity_month as i64 - 1;
    let settlement_month_index = settlement_year as i64 * 12 + settlement_month as i64 - 1;
    let maturity_is_month_end = maturity_day == days_in_month(maturity_year, maturity_month);

    let coupon_date = |periods_back: i64| -> Result<i64, FormulaError> {
        let month_index = maturity_month_index - periods_back * period_months;
        let year = month_index.div_euclid(12) as i32;
        let month = month_index.rem_euclid(12) as i32 + 1;
        let last_day = days_in_month(year, month);
        let day = if maturity_is_month_end {
            last_day
        } else {
            maturity_day.min(last_day)
        };
        coupon_ymd_to_serial(year, month, day)
    };

    let mut periods_back =
        (maturity_month_index - settlement_month_index).div_euclid(period_months);
    if coupon_date(periods_back)? > settlement {
        periods_back += 1;
    }
    let previous = coupon_date(periods_back)?;
    Ok(CouponSchedule {
        previous,
        previous_strict: if previous == settlement {
            coupon_date(periods_back + 1)?
        } else {
            previous
        },
        next: coupon_date(periods_back - 1)?,
        coupons_remaining: periods_back,
    })
}

fn coupon_serial_to_date(serial: i64) -> Result<(i32, i32, i32), FormulaError> {
    if (0..=MAX_EXCEL_DATE_SERIAL).contains(&serial) {
        return serial_to_date(serial);
    }
    if serial == 60 {
        return Ok((1900, 2, 29));
    }
    let gregorian_offset = serial + i64::from(serial < 60);
    let (year, month, day) = civil_from_days(days_from_civil(1899, 12, 30) + gregorian_offset);
    Ok((year, month as i32, day as i32))
}

fn coupon_ymd_to_serial(year: i32, month: i32, day: i32) -> Result<i64, FormulaError> {
    if (year, month, day) == (1900, 2, 29) {
        return Ok(60);
    }
    if !(1..=days_in_month(year, month)).contains(&day) {
        return Err(FormulaError::Num);
    }
    let serial = days_from_civil(year, month as u32, day as u32)
        - days_from_civil(1899, 12, 30)
        - i64::from((year, month, day) < (1900, 3, 1));
    Ok(serial)
}

fn day_count_serial(value: &Value, invalid_date: FormulaError) -> Result<i64, FormulaError> {
    let serial = to_number(value)?;
    if !serial.is_finite() {
        return Err(invalid_date);
    }
    let day = serial.trunc();
    if day < 0.0 || day > MAX_EXCEL_DATE_SERIAL as f64 {
        return Err(invalid_date);
    }
    Ok(day as i64)
}

fn days_360_between(start: i64, end: i64, european: bool) -> i64 {
    let (mut start_year, mut start_month, mut start_day) =
        coupon_serial_to_date(start).expect("coupon date serial is representable");
    let (mut end_year, mut end_month, mut end_day) =
        coupon_serial_to_date(end).expect("coupon date serial is representable");
    let sign = if end < start { -1 } else { 1 };
    if sign < 0 {
        std::mem::swap(&mut start_year, &mut end_year);
        std::mem::swap(&mut start_month, &mut end_month);
        std::mem::swap(&mut start_day, &mut end_day);
    }

    if european {
        if start_day == 31 {
            start_day = 30;
        }
        if end_day == 31 {
            end_day = 30;
        }
    } else {
        let start_is_month_end = start_day == days_in_month(start_year, start_month);
        if start_day == 31 || (start_month == 2 && start_is_month_end) {
            start_day = 30;
        }

        let end_is_month_end = end_day == days_in_month(end_year, end_month);
        if end_is_month_end {
            if start_day < 30 {
                if end_month == 12 {
                    end_year += 1;
                    end_month = 1;
                } else {
                    end_month += 1;
                }
                end_day = 1;
            } else {
                end_day = 30;
            }
        }
    }

    sign * ((end_year as i64 - start_year as i64) * 360
        + (end_month as i64 - start_month as i64) * 30
        + end_day as i64
        - start_day as i64)
}

fn actual_actual_fraction(start: i64, end: i64) -> f64 {
    let sign = if end < start { -1.0 } else { 1.0 };
    let (mut cursor, finish) = if start <= end {
        (start, end)
    } else {
        (end, start)
    };
    let mut fraction = 0.0;
    while cursor < finish {
        let (year, _, _) =
            serial_to_date(cursor).expect("day_count_serial validates the start date serial");
        let year_length = if year == 1900 || is_gregorian_leap_year(year) {
            366.0
        } else {
            365.0
        };
        let next_year = if year == 1899 {
            1
        } else if year == 9999 {
            MAX_EXCEL_DATE_SERIAL + 1
        } else {
            excel_ymd_to_serial(year + 1, 1, 1)
                .expect("next calendar-year boundary is in Excel's serial range")
        };
        let segment_end = finish.min(next_year);
        fraction += (segment_end - cursor) as f64 / year_length;
        cursor = segment_end;
    }
    sign * fraction
}

fn is_gregorian_leap_year(year: i32) -> bool {
    year % 400 == 0 || (year % 4 == 0 && year % 100 != 0)
}

fn selection_call(
    name: &str,
    args: &[Expr],
    env: &Environment<'_>,
) -> Result<CalcValue, FormulaError> {
    match name {
        "IF" => {
            arity(name, args, 2, 3)?;
            let condition = truthy(&eval_scalar(&args[0], env)?)?;
            if condition {
                eval(&args[1], env)
            } else if args.len() == 3 {
                eval(&args[2], env)
            } else {
                Ok(scalar(Value::Bool(false)))
            }
        }
        "IFERROR" => {
            arity(name, args, 2, 2)?;
            match eval(&args[0], env) {
                Ok(CalcValue::Scalar(Value::Error(_))) | Err(_) => eval(&args[1], env),
                Ok(value) => Ok(value),
            }
        }
        "IFNA" => {
            arity(name, args, 2, 2)?;
            match eval(&args[0], env) {
                Ok(CalcValue::Scalar(Value::Error(FormulaError::NA))) | Err(FormulaError::NA) => {
                    eval(&args[1], env)
                }
                Ok(value) => Ok(value),
                Err(error) => Err(error),
            }
        }
        "IFS" => ifs_call(args, env),
        "SWITCH" => switch_call(args, env),
        _ => unreachable!("selection_call only handles selection functions"),
    }
}

fn ifs_call(args: &[Expr], env: &Environment<'_>) -> Result<CalcValue, FormulaError> {
    arity("IFS", args, 2, 254)?;
    let (pairs, remainder) = args.as_chunks::<2>();
    if !remainder.is_empty() {
        return Err(FormulaError::Value);
    }
    for pair in pairs {
        match eval_scalar(&pair[0], env)? {
            Value::Bool(true) => return eval_selected_result(&pair[1], env),
            Value::Bool(false) => {}
            Value::Error(error) => return Err(error),
            _ => return Err(FormulaError::Value),
        }
    }
    Ok(scalar(Value::Error(FormulaError::NA)))
}

fn switch_call(args: &[Expr], env: &Environment<'_>) -> Result<CalcValue, FormulaError> {
    arity("SWITCH", args, 3, 254)?;
    let expression = eval_scalar(&args[0], env)?;
    let remaining = args.len() - 1;
    let has_default = remaining & 1 == 1;
    let pair_argument_count = remaining - usize::from(has_default);
    for offset in (0..pair_argument_count).step_by(2) {
        let match_value = eval_scalar(&args[1 + offset], env)?;
        if switch_equal(&expression, &match_value)? {
            return eval_selected_result(&args[2 + offset], env);
        }
    }
    if has_default {
        return eval_selected_result(args.last().expect("default is the final argument"), env);
    }
    Ok(scalar(Value::Error(FormulaError::NA)))
}

fn eval_selected_result(
    expression: &Expr,
    env: &Environment<'_>,
) -> Result<CalcValue, FormulaError> {
    if matches!(expression, Expr::Missing) {
        Ok(scalar(Value::Number(0.0)))
    } else {
        eval(expression, env)
    }
}

fn switch_equal(left: &Value, right: &Value) -> Result<bool, FormulaError> {
    propagate_value_error(left)?;
    propagate_value_error(right)?;
    match (left, right) {
        (Value::Bool(a), Value::Bool(b)) => Ok(a == b),
        (Value::Number(a), Value::Number(b)) => Ok(a == b),
        (Value::Text(a), Value::Text(b)) => Ok(if a.is_ascii() && b.is_ascii() {
            a.eq_ignore_ascii_case(b)
        } else {
            a == b
        }),
        (Value::Blank, Value::Blank) => Ok(true),
        _ => Ok(false),
    }
}

fn text_extract(name: &str, args: &[Expr], env: &Environment<'_>) -> Result<Value, FormulaError> {
    arity(name, args, 2, 6)?;
    let text_value = eval_scalar(&args[0], env)?;
    let delimiter_value = eval_scalar(&args[1], env)?;
    propagate_value_error(&text_value)?;
    propagate_value_error(&delimiter_value)?;
    let text = to_text(&text_value);
    let delimiter = to_text(&delimiter_value);

    let instance_value = optional_scalar(args, 2, env)?;
    let instance = match instance_value {
        None => 1.0,
        Some(value) => to_number(&value)?,
    };
    if !instance.is_finite() || instance.fract() != 0.0 || instance == 0.0 {
        return Err(FormulaError::Value);
    }

    let match_mode_value = optional_scalar(args, 3, env)?;
    let match_mode = match match_mode_value {
        None => 0,
        Some(value) => match to_number(&value)? {
            0.0 => 0,
            1.0 => 1,
            _ => return Err(FormulaError::Value),
        },
    };
    let match_end_value = optional_scalar(args, 4, env)?;
    let match_end = match match_end_value {
        None => false,
        Some(value) => match to_number(&value)? {
            0.0 => false,
            1.0 => true,
            _ => return Err(FormulaError::Value),
        },
    };

    let text_chars: Vec<char> = text.chars().collect();
    if text_chars.is_empty() {
        return Ok(Value::Text(String::new()));
    }
    if instance.abs() > text_chars.len() as f64 {
        return Err(FormulaError::Value);
    }

    if delimiter.is_empty() {
        if instance.abs() != 1.0 {
            return text_extract_fallback(args, env);
        }
        return Ok(Value::Text(match (name, instance.is_sign_positive()) {
            ("TEXTBEFORE", true) | ("TEXTAFTER", false) => String::new(),
            _ => text,
        }));
    }

    let mut matches = text_delimiter_matches(&text, &delimiter, match_mode == 1);
    if match_end && !matches.iter().any(|(_, end)| *end == text_chars.len()) {
        matches.push((text_chars.len(), text_chars.len()));
    }
    let occurrence = instance.abs() as usize;
    let match_index = if instance > 0.0 {
        occurrence.checked_sub(1)
    } else {
        matches.len().checked_sub(occurrence)
    };
    let Some((start, end)) = match_index.and_then(|index| matches.get(index)).copied() else {
        return text_extract_fallback(args, env);
    };
    let result = if name == "TEXTBEFORE" {
        text_chars[..start].iter().collect()
    } else {
        text_chars[end..].iter().collect()
    };
    Ok(Value::Text(result))
}

fn optional_scalar(
    args: &[Expr],
    index: usize,
    env: &Environment<'_>,
) -> Result<Option<Value>, FormulaError> {
    match args.get(index) {
        None | Some(Expr::Missing) => Ok(None),
        Some(expression) => Ok(Some(eval_scalar(expression, env)?)),
    }
}

fn text_extract_fallback(args: &[Expr], env: &Environment<'_>) -> Result<Value, FormulaError> {
    match args.get(5) {
        None | Some(Expr::Missing) => Ok(Value::Error(FormulaError::NA)),
        Some(expression) => eval_scalar(expression, env),
    }
}

fn text_delimiter_matches(
    text: &str,
    delimiter: &str,
    case_insensitive: bool,
) -> Vec<(usize, usize)> {
    let source_chars: Vec<char> = text.chars().collect();
    let delimiter_chars: Vec<char> = if case_insensitive {
        excel_casefold(delimiter).chars().collect()
    } else {
        delimiter.chars().collect()
    };
    if !case_insensitive {
        let mut matches = Vec::new();
        let mut start = 0;
        while start + delimiter_chars.len() <= source_chars.len() {
            if source_chars[start..].starts_with(&delimiter_chars) {
                let end = start + delimiter_chars.len();
                matches.push((start, end));
                start = end;
            } else {
                start += 1;
            }
        }
        return matches;
    }

    let mut folded_chars = Vec::new();
    let mut source_starts = Vec::new();
    let mut source_ends = Vec::new();
    for (source_index, character) in source_chars.iter().enumerate() {
        let folded: Vec<char> = excel_casefold(&character.to_string()).chars().collect();
        folded_chars.extend(folded.iter().copied());
        source_starts.extend(std::iter::repeat_n(source_index, folded.len()));
        source_ends.extend(std::iter::repeat_n(source_index + 1, folded.len()));
    }
    let mut matches = Vec::new();
    let mut cursor = 0;
    while cursor + delimiter_chars.len() <= folded_chars.len() {
        if folded_chars[cursor..].starts_with(&delimiter_chars) {
            let folded_end = cursor + delimiter_chars.len();
            let starts_at_boundary =
                cursor == 0 || source_starts[cursor] != source_starts[cursor - 1];
            let ends_at_boundary = folded_end == source_starts.len()
                || source_starts[folded_end - 1] != source_starts[folded_end];
            if starts_at_boundary && ends_at_boundary {
                matches.push((source_starts[cursor], source_ends[folded_end - 1]));
                cursor = folded_end;
            } else {
                cursor += 1;
            }
        } else {
            cursor += 1;
        }
    }
    matches
}

fn arity(name: &str, args: &[Expr], min: usize, max: usize) -> Result<(), FormulaError> {
    if args.len() < min || args.len() > max {
        Err(FormulaError::Value)
    } else {
        let _ = name;
        Ok(())
    }
}

fn xlookup_family(name: &str, args: &[Expr], env: &Environment<'_>) -> Result<Value, FormulaError> {
    let (min, max) = if name == "XLOOKUP" { (3, 6) } else { (2, 4) };
    arity(name, args, min, max)?;

    let lookup = eval_scalar(&args[0], env)?;
    propagate_value_error(&lookup)?;
    let lookup_values = as_vector(eval(&args[1], env)?)?;

    let return_values = if name == "XLOOKUP" {
        let values = as_vector(eval(&args[2], env)?)?;
        if values.len() != lookup_values.len() {
            return Err(FormulaError::Value);
        }
        Some(values)
    } else {
        None
    };

    let (match_index, search_index) = if name == "XLOOKUP" { (4, 5) } else { (2, 3) };
    let match_mode = if args.len() > match_index {
        mode_argument(name, "match_mode", &args[match_index], env)?
    } else {
        0
    };
    if match_mode != 0 {
        return Err(FormulaError::Unsupported(
            "XLOOKUP/XMATCH currently support exact match_mode=0 only".into(),
        ));
    }
    let search_mode = if args.len() > search_index {
        mode_argument(name, "search_mode", &args[search_index], env)?
    } else {
        1
    };
    if !matches!(search_mode, 1 | -1) {
        return Err(FormulaError::Unsupported(
            "XLOOKUP/XMATCH currently support search_mode=1 or -1 only".into(),
        ));
    }

    let indices: Box<dyn Iterator<Item = usize>> = if search_mode == 1 {
        Box::new(0..lookup_values.len())
    } else {
        Box::new((0..lookup_values.len()).rev())
    };
    let mut found = None;
    for index in indices {
        let candidate = &lookup_values[index];
        if let Value::Error(error) = candidate {
            return Err(error.clone());
        }
        if compare(candidate, &lookup)? == 0 {
            found = Some(index);
            break;
        }
    }
    if let Some(index) = found {
        return if name == "XMATCH" {
            Ok(Value::Number((index + 1) as f64))
        } else {
            let value = return_values.expect("XLOOKUP return array was validated")[index].clone();
            propagate_value_error(&value)?;
            Ok(value)
        };
    }

    if name == "XLOOKUP" && args.len() >= 4 {
        let value = eval_scalar(&args[3], env)?;
        propagate_value_error(&value)?;
        return Ok(value);
    }
    Err(FormulaError::NA)
}

fn as_vector(value: CalcValue) -> Result<Vec<Value>, FormulaError> {
    match value {
        CalcValue::Scalar(value) => Ok(vec![value]),
        CalcValue::Range(rows) if rows.len() == 1 => Ok(rows.into_iter().next().unwrap()),
        CalcValue::Range(rows) if rows.iter().all(|row| row.len() == 1) => Ok(rows
            .into_iter()
            .map(|row| row.into_iter().next().unwrap())
            .collect()),
        CalcValue::Range(_) | CalcValue::Array(_) => Err(FormulaError::Value),
    }
}

fn mode_argument(
    function: &str,
    argument: &str,
    expression: &Expr,
    env: &Environment<'_>,
) -> Result<i32, FormulaError> {
    let value = eval_scalar(expression, env)?;
    propagate_value_error(&value)?;
    let number = to_number(&value)?;
    if !number.is_finite()
        || number.fract() != 0.0
        || number < i32::MIN as f64
        || number > i32::MAX as f64
    {
        return Err(FormulaError::Value);
    }
    let _ = (function, argument);
    Ok(number as i32)
}
fn one(_name: &str, flat: &[Value]) -> Result<Value, FormulaError> {
    flat.first().cloned().ok_or(FormulaError::Value)
}
fn propagate_value_error(value: &Value) -> Result<(), FormulaError> {
    if let Value::Error(error) = value {
        Err(error.clone())
    } else {
        Ok(())
    }
}
fn is_excel_value_error(error: &FormulaError) -> bool {
    matches!(
        error,
        FormulaError::Value
            | FormulaError::Div0
            | FormulaError::Ref
            | FormulaError::Name
            | FormulaError::Num
            | FormulaError::NA
    )
}
fn error_predicate_matches(name: &str, error: &FormulaError) -> bool {
    match name {
        "ISERROR" => error.excel_code().is_some(),
        "ISERR" => error.excel_code().is_some() && !matches!(error, FormulaError::NA),
        "ISNA" => matches!(error, FormulaError::NA),
        _ => false,
    }
}
fn excel_casefold(text: &str) -> String {
    text.chars()
        .flat_map(|ch| match ch {
            'ß' | 'ẞ' => "ss".chars().collect::<Vec<_>>(),
            'ς' => vec!['σ'],
            _ => ch.to_lowercase().collect(),
        })
        .collect()
}
fn flatten(v: CalcValue) -> Vec<Value> {
    match v {
        CalcValue::Scalar(v) => vec![v],
        CalcValue::Range(rows) => rows.into_iter().flatten().collect(),
        CalcValue::Array(array) => array.rows.into_iter().flatten().collect(),
    }
}

#[derive(Clone)]
struct CriteriaGrid {
    rows: usize,
    columns: usize,
    cells: Vec<Value>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum CriteriaOperator {
    Equal,
    NotEqual,
    Less,
    LessEqual,
    Greater,
    GreaterEqual,
}

#[derive(Clone)]
struct Criteria {
    operator: CriteriaOperator,
    operand: Value,
    wildcard: bool,
    wildcard_pattern: Option<WildcardPattern>,
}

fn criteria_function(
    name: &str,
    args: &[Expr],
    values: &[CalcValue],
    wildcard_work_remaining: &Cell<usize>,
) -> Result<Value, FormulaError> {
    let (criteria_ranges, criteria_values, result_range) = match name {
        "COUNTIF" => {
            arity(name, args, 2, 2)?;
            (
                vec![criteria_grid(&values[0], &args[0])?],
                vec![&values[1]],
                None,
            )
        }
        "SUMIF" | "AVERAGEIF" => {
            arity(name, args, 2, 3)?;
            let criteria = criteria_grid(&values[0], &args[0])?;
            let result = if values.len() == 3 {
                criteria_grid(&values[2], &args[2])?
            } else {
                criteria.clone()
            };
            ensure_same_shape(&criteria, &result)?;
            (vec![criteria], vec![&values[1]], Some(result))
        }
        "COUNTIFS" => {
            arity(name, args, 2, 254)?;
            if !args.len().is_multiple_of(2) {
                return Err(FormulaError::Value);
            }
            let mut ranges = Vec::new();
            let mut criteria = Vec::new();
            for i in (0..values.len()).step_by(2) {
                ranges.push(criteria_grid(&values[i], &args[i])?);
                criteria.push(&values[i + 1]);
            }
            (ranges, criteria, None)
        }
        "SUMIFS" | "AVERAGEIFS" => {
            arity(name, args, 3, 255)?;
            if args.len().is_multiple_of(2) {
                return Err(FormulaError::Value);
            }
            let result = criteria_grid(&values[0], &args[0])?;
            let mut ranges = Vec::new();
            let mut criteria = Vec::new();
            for i in (1..values.len()).step_by(2) {
                let range = criteria_grid(&values[i], &args[i])?;
                ensure_same_shape(&result, &range)?;
                ranges.push(range);
                criteria.push(&values[i + 1]);
            }
            (ranges, criteria, Some(result))
        }
        "MINIFS" | "MAXIFS" => {
            arity(name, args, 3, 255)?;
            if args.len().is_multiple_of(2) {
                return Err(FormulaError::Value);
            }
            let result = criteria_grid(&values[0], &args[0])?;
            let mut ranges = Vec::new();
            let mut criteria = Vec::new();
            for i in (1..values.len()).step_by(2) {
                let range = criteria_grid(&values[i], &args[i])?;
                ensure_same_shape(&result, &range)?;
                ranges.push(range);
                criteria.push(&values[i + 1]);
            }
            (ranges, criteria, Some(result))
        }
        _ => unreachable!(),
    };

    let base = &criteria_ranges[0];
    for range in criteria_ranges.iter().skip(1) {
        ensure_same_shape(base, range)?;
    }
    let parsed_criteria = criteria_values
        .into_iter()
        .map(parse_criteria)
        .collect::<Result<Vec<_>, _>>()?;

    let mut count = 0usize;
    let mut sum = 0.0;
    let mut numeric_count = 0usize;
    let mut minimum = f64::INFINITY;
    let mut maximum = f64::NEG_INFINITY;
    for index in 0..base.cells.len() {
        let mut matches = true;
        for (range, criterion) in criteria_ranges.iter().zip(parsed_criteria.iter()) {
            if !criterion_matches(&range.cells[index], criterion, wildcard_work_remaining)? {
                matches = false;
                break;
            }
        }
        if !matches {
            continue;
        }
        if name.starts_with("COUNT") {
            count += 1;
            continue;
        }
        let value = &result_range.as_ref().ok_or(FormulaError::Value)?.cells[index];
        match value {
            Value::Number(number) => {
                sum += number;
                numeric_count += 1;
                minimum = minimum.min(*number);
                maximum = maximum.max(*number);
            }
            Value::Bool(value) if name.starts_with("SUM") || name == "AVERAGEIFS" => {
                sum += if *value { 1.0 } else { 0.0 };
                if name == "AVERAGEIFS" {
                    numeric_count += 1;
                }
            }
            Value::Error(error) => return Err(error.clone()),
            _ => {}
        }
    }
    match name {
        "COUNTIF" | "COUNTIFS" => Ok(Value::Number(count as f64)),
        "SUMIF" | "SUMIFS" => Ok(Value::Number(sum)),
        "AVERAGEIF" | "AVERAGEIFS" if numeric_count == 0 => Err(FormulaError::Div0),
        "AVERAGEIF" | "AVERAGEIFS" => Ok(Value::Number(sum / numeric_count as f64)),
        "MINIFS" if numeric_count == 0 => Ok(Value::Number(0.0)),
        "MINIFS" => Ok(Value::Number(minimum)),
        "MAXIFS" if numeric_count == 0 => Ok(Value::Number(0.0)),
        "MAXIFS" => Ok(Value::Number(maximum)),
        _ => unreachable!(),
    }
}

fn criteria_grid(value: &CalcValue, expression: &Expr) -> Result<CriteriaGrid, FormulaError> {
    let rows = match value {
        CalcValue::Range(rows) => rows,
        CalcValue::Array(_) => return Err(FormulaError::Value),
        CalcValue::Scalar(_) => {
            return match (value, expression) {
                (CalcValue::Scalar(value), Expr::Ref(_, _)) => Ok(CriteriaGrid {
                    rows: 1,
                    columns: 1,
                    cells: vec![value.clone()],
                }),
                _ => Err(FormulaError::Value),
            };
        }
    };
    if rows.is_empty() || rows[0].is_empty() || rows.iter().any(|row| row.len() != rows[0].len()) {
        return Err(FormulaError::Value);
    }
    let row_count = rows.len();
    let column_count = rows[0].len();
    Ok(CriteriaGrid {
        rows: row_count,
        columns: column_count,
        cells: rows.iter().flatten().cloned().collect(),
    })
}

fn ensure_same_shape(a: &CriteriaGrid, b: &CriteriaGrid) -> Result<(), FormulaError> {
    if a.rows == b.rows && a.columns == b.columns {
        Ok(())
    } else {
        Err(FormulaError::Value)
    }
}

fn parse_criteria(value: &CalcValue) -> Result<Criteria, FormulaError> {
    let CalcValue::Scalar(value) = value else {
        return Err(FormulaError::Value);
    };
    if let Value::Error(error) = value {
        return Err(error.clone());
    }
    let (operator, operand, wildcard) = match value {
        Value::Text(text) => {
            let (operator, rest) = if let Some(rest) = text.strip_prefix("<=") {
                (CriteriaOperator::LessEqual, rest)
            } else if let Some(rest) = text.strip_prefix(">=") {
                (CriteriaOperator::GreaterEqual, rest)
            } else if let Some(rest) = text.strip_prefix("<>") {
                (CriteriaOperator::NotEqual, rest)
            } else if let Some(rest) = text.strip_prefix('=') {
                (CriteriaOperator::Equal, rest)
            } else if let Some(rest) = text.strip_prefix('<') {
                (CriteriaOperator::Less, rest)
            } else if let Some(rest) = text.strip_prefix('>') {
                (CriteriaOperator::Greater, rest)
            } else {
                (CriteriaOperator::Equal, text.as_str())
            };
            if rest.is_empty()
                && !matches!(
                    operator,
                    CriteriaOperator::Equal | CriteriaOperator::NotEqual
                )
            {
                return Err(FormulaError::Value);
            }
            let mut escaped = false;
            let mut has_wildcard = false;
            for ch in rest.chars() {
                if escaped {
                    escaped = false;
                } else if ch == '~' {
                    escaped = true;
                } else if matches!(ch, '*' | '?') {
                    has_wildcard = true;
                }
            }
            if has_wildcard
                && !matches!(
                    operator,
                    CriteriaOperator::Equal | CriteriaOperator::NotEqual
                )
            {
                return Err(FormulaError::Value);
            }
            (operator, Value::Text(rest.to_string()), has_wildcard)
        }
        scalar => (CriteriaOperator::Equal, scalar.clone(), false),
    };
    let wildcard_pattern = match &operand {
        Value::Text(pattern) if pattern.contains(['*', '?', '~']) => {
            Some(WildcardPattern::new(pattern)?)
        }
        _ => None,
    };
    Ok(Criteria {
        operator,
        operand,
        wildcard,
        wildcard_pattern,
    })
}

fn criterion_matches(
    candidate: &Value,
    criteria: &Criteria,
    wildcard_work_remaining: &Cell<usize>,
) -> Result<bool, FormulaError> {
    if let Value::Error(error) = candidate {
        return Err(error.clone());
    }
    let blank_operand = matches!(criteria.operand, Value::Blank)
        || matches!(&criteria.operand, Value::Text(text) if text.is_empty());
    if blank_operand
        && matches!(
            criteria.operator,
            CriteriaOperator::Equal | CriteriaOperator::NotEqual
        )
    {
        let is_blank = matches!(candidate, Value::Blank)
            || matches!(candidate, Value::Text(text) if text.is_empty());
        return Ok(if criteria.operator == CriteriaOperator::Equal {
            is_blank
        } else {
            !is_blank
        });
    }
    if let (Value::Text(expected), Value::Text(candidate_text)) = (&criteria.operand, candidate)
        && matches!(
            criteria.operator,
            CriteriaOperator::Equal | CriteriaOperator::NotEqual
        )
    {
        let matched = if let Some(pattern) = &criteria.wildcard_pattern {
            wildcard_match(pattern, candidate_text, wildcard_work_remaining)?
        } else {
            candidate_text.to_lowercase() == expected.to_lowercase()
        };
        return Ok(if criteria.operator == CriteriaOperator::NotEqual {
            !matched
        } else {
            matched
        });
    }
    if criteria.wildcard && !matches!(candidate, Value::Text(_)) {
        return Ok(criteria.operator == CriteriaOperator::NotEqual);
    }
    let ordering = match compare_criteria_values(candidate, &criteria.operand) {
        Ok(ordering) => ordering,
        Err(FormulaError::Value) => return Ok(false),
        Err(error) => return Err(error),
    };
    Ok(match criteria.operator {
        CriteriaOperator::Equal => ordering == 0,
        CriteriaOperator::NotEqual => ordering != 0,
        CriteriaOperator::Less => ordering < 0,
        CriteriaOperator::LessEqual => ordering <= 0,
        CriteriaOperator::Greater => ordering > 0,
        CriteriaOperator::GreaterEqual => ordering >= 0,
    })
}

fn compare_criteria_values(a: &Value, b: &Value) -> Result<i8, FormulaError> {
    match (a, b) {
        (Value::Number(_), Value::Text(text)) => {
            let number = text.parse::<f64>().map_err(|_| FormulaError::Value)?;
            compare(a, &Value::Number(number))
        }
        (Value::Text(text), Value::Number(_)) => {
            let number = text.parse::<f64>().map_err(|_| FormulaError::Value)?;
            compare(&Value::Number(number), b).map(|ordering| -ordering)
        }
        _ => compare(a, b),
    }
}

#[derive(Clone)]
enum WildcardToken {
    AnyMany,
    AnyOne,
    Literal(String),
}

#[derive(Clone)]
struct WildcardPattern {
    tokens: Vec<WildcardToken>,
}

impl WildcardPattern {
    fn new(pattern: &str) -> Result<Self, FormulaError> {
        if pattern
            .encode_utf16()
            .take(MAX_TEXT_LENGTH_UNITS + 1)
            .count()
            > MAX_TEXT_LENGTH_UNITS
        {
            return Err(FormulaError::Value);
        }
        let mut tokens = Vec::new();
        let mut chars = pattern.chars();
        while let Some(ch) = chars.next() {
            match ch {
                '~' => {
                    let literal = chars.next().unwrap_or('~');
                    tokens.push(WildcardToken::Literal(literal.to_lowercase().collect()));
                }
                '*' => {
                    if !matches!(tokens.last(), Some(WildcardToken::AnyMany)) {
                        tokens.push(WildcardToken::AnyMany);
                    }
                }
                '?' => {
                    tokens.push(WildcardToken::AnyOne);
                }
                literal => tokens.push(WildcardToken::Literal(literal.to_lowercase().collect())),
            }
        }
        Ok(Self { tokens })
    }
}

fn wildcard_match(
    pattern: &WildcardPattern,
    text: &str,
    wildcard_work_remaining: &Cell<usize>,
) -> Result<bool, FormulaError> {
    if text.len() > MAX_WILDCARD_WORK {
        return Err(FormulaError::Value);
    }
    let width = text.chars().count();
    let has_wildcard = pattern
        .tokens
        .iter()
        .any(|token| matches!(token, WildcardToken::AnyMany | WildcardToken::AnyOne));

    if !has_wildcard {
        let cost = pattern.tokens.len().saturating_add(width);
        if cost > wildcard_work_remaining.get() {
            return Err(FormulaError::Value);
        }
        wildcard_work_remaining.set(wildcard_work_remaining.get() - cost);
        return Ok(pattern.tokens.len() == width
            && pattern.tokens.iter().zip(text.chars()).all(|(token, character)| {
                matches!(token, WildcardToken::Literal(expected) if expected.chars().eq(character.to_lowercase()))
            }));
    }

    let cost = pattern
        .tokens
        .len()
        .saturating_add(1)
        .saturating_mul(width.saturating_add(1));
    if cost > wildcard_work_remaining.get() {
        return Err(FormulaError::Value);
    }
    wildcard_work_remaining.set(wildcard_work_remaining.get() - cost);

    let characters: Vec<char> = text.chars().collect();
    let mut previous = vec![false; width + 1];
    previous[0] = true;
    for token in &pattern.tokens {
        let mut current = vec![false; width + 1];
        match token {
            WildcardToken::AnyMany => {
                current[0] = previous[0];
                for offset in 1..=width {
                    current[offset] = previous[offset] || current[offset - 1];
                }
            }
            WildcardToken::AnyOne => {
                current[1..].copy_from_slice(&previous[..width]);
            }
            WildcardToken::Literal(expected) => {
                for offset in 1..=width {
                    current[offset] = previous[offset - 1]
                        && expected.chars().eq(characters[offset - 1].to_lowercase());
                }
            }
        }
        previous = current;
    }
    Ok(previous[width])
}

fn match_fn(
    args: &[Expr],
    values: &[CalcValue],
    name: &str,
    wildcard_work_remaining: &Cell<usize>,
) -> Result<Value, FormulaError> {
    arity(name, args, 2, 3)?;
    let lookup = flatten(values[0].clone())
        .into_iter()
        .next()
        .ok_or(FormulaError::Value)?;
    propagate_value_error(&lookup)?;
    let list = flatten(values[1].clone());
    let match_type = if values.len() > 2 {
        to_number(&flatten(values[2].clone())[0])? as i32
    } else {
        1
    };
    let found = if match_type == 0 {
        let mut found = None;
        let mut wildcard_pattern = None;
        for (index, candidate) in list.iter().enumerate() {
            if exact_lookup_equal(
                candidate,
                &lookup,
                &mut wildcard_pattern,
                wildcard_work_remaining,
            )? {
                found = Some(index);
                break;
            }
        }
        found
    } else if match_type == 1 {
        list.iter()
            .enumerate()
            .filter(|(_, v)| compare(v, &lookup).is_ok_and(|o| o <= 0))
            .map(|(i, _)| i)
            .next_back()
    } else if match_type == -1 {
        list.iter()
            .enumerate()
            .filter(|(_, v)| compare(v, &lookup).is_ok_and(|o| o >= 0))
            .map(|(i, _)| i)
            .next_back()
    } else {
        return Err(FormulaError::Value);
    };
    found
        .map(|i| Value::Number((i + 1) as f64))
        .ok_or(FormulaError::NA)
}

fn index_fn(args: &[Expr], values: &[CalcValue], name: &str) -> Result<Value, FormulaError> {
    arity(name, args, 2, 4)?;
    if values.len() == 4 {
        let area = to_number(&flatten(values[3].clone())[0])?;
        if !area.is_finite() || area.fract() != 0.0 || area < 0.0 {
            return Err(FormulaError::Value);
        }
        if area == 0.0 {
            return Err(FormulaError::Unsupported(
                "INDEX area_num=0 can return an array, which this scalar evaluator does not support".into(),
            ));
        }
        if area != 1.0 {
            return Err(FormulaError::Ref);
        }
    }
    let rows = match &values[0] {
        CalcValue::Range(rows) => rows.clone(),
        CalcValue::Array(_) => return Err(FormulaError::Value),
        CalcValue::Scalar(v) => vec![vec![v.clone()]],
    };
    let row = to_number(&flatten(values[1].clone())[0])? as usize;
    let col = if values.len() >= 3 {
        to_number(&flatten(values[2].clone())[0])? as usize
    } else {
        1
    };
    if row == 0 || col == 0 {
        return Err(FormulaError::Value);
    }
    rows.get(row - 1)
        .and_then(|r| r.get(col - 1))
        .cloned()
        .ok_or(FormulaError::Ref)
}

fn vlookup_fn(
    args: &[Expr],
    values: &[CalcValue],
    name: &str,
    wildcard_work_remaining: &Cell<usize>,
) -> Result<Value, FormulaError> {
    arity(name, args, 3, 4)?;
    let lookup = flatten(values[0].clone())
        .into_iter()
        .next()
        .ok_or(FormulaError::Value)?;
    propagate_value_error(&lookup)?;
    let table = match &values[1] {
        CalcValue::Range(rows) => rows.clone(),
        CalcValue::Array(_) => return Err(FormulaError::Value),
        CalcValue::Scalar(v) => vec![vec![v.clone()]],
    };
    let col = to_number(&flatten(values[2].clone())[0])? as usize;
    if col == 0 {
        return Err(FormulaError::Value);
    }
    let exact = values.len() == 4 && !truthy(&flatten(values[3].clone())[0])?;
    let found = if exact {
        let mut found = None;
        let mut wildcard_pattern = None;
        for (index, row) in table.iter().enumerate() {
            if let Some(candidate) = row.first()
                && exact_lookup_equal(
                    candidate,
                    &lookup,
                    &mut wildcard_pattern,
                    wildcard_work_remaining,
                )?
            {
                found = Some(index);
                break;
            }
        }
        found
    } else {
        table
            .iter()
            .enumerate()
            .filter(|(_, row)| {
                row.first()
                    .is_some_and(|v| compare(v, &lookup).is_ok_and(|o| o <= 0))
            })
            .map(|(i, _)| i)
            .next_back()
    }
    .ok_or(FormulaError::NA)?;
    table
        .get(found)
        .and_then(|row| row.get(col - 1))
        .cloned()
        .ok_or(FormulaError::Ref)
}

fn exact_lookup_equal(
    candidate: &Value,
    lookup: &Value,
    wildcard_pattern: &mut Option<Option<WildcardPattern>>,
    wildcard_work_remaining: &Cell<usize>,
) -> Result<bool, FormulaError> {
    propagate_value_error(candidate)?;
    propagate_value_error(lookup)?;
    match (candidate, lookup) {
        (Value::Text(candidate), Value::Text(pattern)) => {
            if wildcard_pattern.is_none() {
                let compiled = if pattern.chars().any(|ch| matches!(ch, '*' | '?' | '~')) {
                    Some(WildcardPattern::new(pattern)?)
                } else {
                    None
                };
                *wildcard_pattern = Some(compiled);
            }
            match wildcard_pattern.as_ref().and_then(Option::as_ref) {
                Some(compiled) => wildcard_match(compiled, candidate, wildcard_work_remaining),
                None => Ok(candidate.to_lowercase() == pattern.to_lowercase()),
            }
        }
        _ => match compare(candidate, lookup) {
            Ok(ordering) => Ok(ordering == 0),
            Err(FormulaError::Value) => Ok(false),
            Err(error) => Err(error),
        },
    }
}

// Excel's 1900 date system is used for DATE/DAY/YEAR. Serial 60 is exposed as
// the historical fictitious 1900-02-29 even though Gregorian date arithmetic
// cannot represent that calendar date.
fn date_value(mut year: i32, month: i32, day: i32) -> Result<Value, FormulaError> {
    if !(0..=9999).contains(&year) {
        return Err(FormulaError::Num);
    }
    if year < 1900 {
        year += 1900;
    }
    let month0 = month.checked_sub(1).ok_or(FormulaError::Num)?;
    year = year
        .checked_add(month0.div_euclid(12))
        .ok_or(FormulaError::Num)?;
    let month = month0.rem_euclid(12) + 1;
    if !(1..=9999).contains(&year) {
        return Err(FormulaError::Num);
    }
    let mut serial_month_first =
        days_from_civil(year, month as u32, 1) - days_from_civil(1899, 12, 30);
    // Excel's 1900 date system omits a real day before March 1900, then
    // reserves serial 60 for its historical fictitious February 29.
    if year == 1900 && month <= 2 {
        serial_month_first -= 1;
    }
    let serial = serial_month_first + day as i64 - 1;
    if !(0..=MAX_EXCEL_DATE_SERIAL).contains(&serial) {
        return Err(FormulaError::Num);
    }
    Ok(Value::Number(serial as f64))
}
fn serial_to_date(serial: i64) -> Result<(i32, i32, i32), FormulaError> {
    if !(0..=MAX_EXCEL_DATE_SERIAL).contains(&serial) {
        return Err(FormulaError::Num);
    }
    if serial == 60 {
        return Ok((1900, 2, 29));
    }
    let gregorian_offset = serial + i64::from(serial < 60);
    let (y, m, d) = civil_from_days(days_from_civil(1899, 12, 30) + gregorian_offset);
    Ok((y, m as i32, d as i32))
}
fn excel_serial_ymd(serial: f64) -> Result<(i32, i32, i32), FormulaError> {
    if !serial.is_finite() {
        return Err(FormulaError::Num);
    }
    let day = serial.floor();
    if day < 0.0 || day > MAX_EXCEL_DATE_SERIAL as f64 {
        return Err(FormulaError::Num);
    }
    serial_to_date(day as i64)
}
fn excel_weekday_sunday_zero(serial_day: i64) -> i32 {
    (serial_day - 1).rem_euclid(7) as i32
}
fn excel_day_of_year(serial_day: i64, year: i32, month: i32, day: i32) -> i32 {
    if year == 1900 {
        // Preserve serial 60 as the fictional leap day in the year sequence.
        return serial_day as i32;
    }
    (days_from_civil(year, month as u32, day as u32) - days_from_civil(year, 1, 1) + 1) as i32
}
fn excel_jan1_sunday_zero(year: i32) -> i32 {
    if year == 1900 {
        return 0; // Serial 1, under Excel's historical weekday sequence.
    }
    if year >= 1901 {
        let serial = days_from_civil(year, 1, 1) - days_from_civil(1899, 12, 31) + 1;
        return (serial - 1).rem_euclid(7) as i32;
    }
    (days_from_civil(year, 1, 1) + 4).rem_euclid(7) as i32
}
fn excel_iso_weeks_in_year(year: i32) -> i32 {
    let jan1_monday_zero = (excel_jan1_sunday_zero(year) + 6).rem_euclid(7);
    let leap_year = year == 1900 || year % 400 == 0 || (year % 4 == 0 && year % 100 != 0);
    if jan1_monday_zero == 3 || (jan1_monday_zero == 2 && leap_year) {
        53
    } else {
        52
    }
}
fn excel_iso_week_number(year: i32, day_of_year: i32, weekday_sunday_zero: i32) -> i32 {
    let weekday_monday_zero = (weekday_sunday_zero + 6).rem_euclid(7);
    let week = (day_of_year - weekday_monday_zero + 9).div_euclid(7);
    if week < 1 {
        return excel_iso_weeks_in_year(year - 1);
    }
    if week > excel_iso_weeks_in_year(year) {
        return 1;
    }
    week
}
const STANDARD_WEEKEND_MASK: [bool; 7] = [false, false, false, false, false, true, true];

fn workday_date_serial(value: &Value, international: bool) -> Result<i64, FormulaError> {
    let serial = to_number(value)?;
    if !serial.is_finite() {
        return Err(FormulaError::Num);
    }
    let day = serial.floor();
    if day < 0.0 || day > MAX_EXCEL_DATE_SERIAL as f64 {
        return Err(if international {
            FormulaError::Num
        } else {
            FormulaError::Value
        });
    }
    Ok(day as i64)
}
fn is_nonworking_day(serial_day: i64, weekend: &[bool; 7]) -> bool {
    let monday_first_weekday = (excel_weekday_sunday_zero(serial_day) + 6).rem_euclid(7) as usize;
    weekend[monday_first_weekday]
}
fn weekend_mask(value: &Value) -> Result<[bool; 7], FormulaError> {
    if let Value::Text(text) = value {
        let characters = text.chars().collect::<Vec<_>>();
        if characters.len() != 7
            || characters
                .iter()
                .any(|character| !matches!(character, '0' | '1'))
        {
            return Err(FormulaError::Value);
        }
        let mut mask = [false; 7];
        for (index, character) in characters.iter().enumerate() {
            mask[index] = *character == '1';
        }
        return Ok(mask);
    }
    let code = to_number(value)?;
    if !code.is_finite() || code.fract() != 0.0 || code < 1.0 || code > 17.0 {
        return Err(FormulaError::Num);
    }
    let code = code as i64;
    let mut mask = [false; 7];
    if (1..=7).contains(&code) {
        let first_day = (code - 3).rem_euclid(7) as usize;
        mask[first_day] = true;
        mask[(first_day + 1) % 7] = true;
        return Ok(mask);
    }
    if (11..=17).contains(&code) {
        mask[(code - 12).rem_euclid(7) as usize] = true;
        return Ok(mask);
    }
    Err(FormulaError::Num)
}
fn workday_holidays(
    expression: &Expr,
    env: &Environment<'_>,
    international: bool,
) -> Result<HashSet<i64>, FormulaError> {
    let evaluated = eval(expression, env)?;
    let (values, is_range) = match evaluated {
        CalcValue::Scalar(value) => (vec![value], false),
        CalcValue::Range(rows) => (rows.into_iter().flatten().collect(), true),
        CalcValue::Array(_) => return Err(FormulaError::Value),
    };
    let mut holidays = HashSet::new();
    for value in values {
        if matches!(value, Value::Blank) || matches!(&value, Value::Text(text) if text.is_empty()) {
            continue;
        }
        // Text cells in a referenced holidays range are treated as non-date cells.
        if is_range && matches!(value, Value::Text(_)) {
            continue;
        }
        holidays.insert(workday_date_serial(&value, international)?);
    }
    Ok(holidays)
}
fn business_days_in_range(
    first_day: i64,
    last_day: i64,
    holidays: &[i64],
    weekend: &[bool; 7],
) -> i64 {
    if first_day > last_day {
        return 0;
    }
    let length = last_day - first_day + 1;
    let full_weeks = length / 7;
    let remainder = length % 7;
    let workdays_per_week = 7 - weekend.iter().filter(|is_weekend| **is_weekend).count() as i64;
    let mut count = full_weeks * workdays_per_week;
    let first_remainder_day = first_day + full_weeks * 7;
    for offset in 0..remainder {
        if !is_nonworking_day(first_remainder_day + offset, weekend) {
            count += 1;
        }
    }
    let first_holiday = holidays.partition_point(|holiday| *holiday < first_day);
    let after_last_holiday = holidays.partition_point(|holiday| *holiday <= last_day);
    count - (after_last_holiday - first_holiday) as i64
}
fn weekday_holidays(holidays: &HashSet<i64>, weekend: &[bool; 7]) -> Vec<i64> {
    let mut days = holidays
        .iter()
        .copied()
        .filter(|holiday| !is_nonworking_day(*holiday, weekend))
        .collect::<Vec<_>>();
    days.sort_unstable();
    days
}
fn networkdays_count(
    start_day: i64,
    end_day: i64,
    holidays: &HashSet<i64>,
    weekend: &[bool; 7],
) -> i64 {
    let (first_day, last_day, direction) = if start_day <= end_day {
        (start_day, end_day, 1)
    } else {
        (end_day, start_day, -1)
    };
    direction
        * business_days_in_range(
            first_day,
            last_day,
            &weekday_holidays(holidays, weekend),
            weekend,
        )
}
fn workday_result(
    start_day: i64,
    offset: i64,
    holidays: &HashSet<i64>,
    weekend: &[bool; 7],
) -> Result<i64, FormulaError> {
    if offset == 0 {
        return Ok(start_day);
    }
    let direction = if offset > 0 { 1 } else { -1 };
    let maximum_distance = if direction > 0 {
        MAX_EXCEL_DATE_SERIAL - start_day
    } else {
        start_day
    };
    if maximum_distance == 0 {
        return Err(FormulaError::Num);
    }
    let holiday_days = weekday_holidays(holidays, weekend);
    let count_at_distance = |distance: i64| {
        if direction > 0 {
            business_days_in_range(start_day + 1, start_day + distance, &holiday_days, weekend)
        } else {
            business_days_in_range(start_day - distance, start_day - 1, &holiday_days, weekend)
        }
    };
    let target = offset.unsigned_abs() as i64;
    if count_at_distance(maximum_distance) < target {
        return Err(FormulaError::Num);
    }
    let mut low = 1;
    let mut high = maximum_distance;
    while low < high {
        let middle = (low + high) / 2;
        if count_at_distance(middle) >= target {
            high = middle;
        } else {
            low = middle + 1;
        }
    }
    Ok(start_day + direction * low)
}
fn working_day_call(
    name: &str,
    args: &[Expr],
    env: &Environment<'_>,
) -> Result<Value, FormulaError> {
    let international = name.ends_with(".INTL");
    let is_workday = name.starts_with("WORKDAY");
    arity(name, args, 2, if international { 4 } else { 3 })?;
    let start = eval_scalar(&args[0], env)?;
    let start_day = workday_date_serial(&start, international)?;
    let second = eval_scalar(&args[1], env)?;

    let offset = if is_workday {
        let days = to_number(&second)?;
        if !days.is_finite() || days.abs() > (MAX_EXCEL_DATE_SERIAL + 1) as f64 {
            return Err(FormulaError::Num);
        }
        days.trunc() as i64
    } else {
        0
    };
    let end_day = if !is_workday {
        Some(workday_date_serial(&second, international)?)
    } else {
        None
    };

    let weekend = if international {
        match args.get(2) {
            None | Some(Expr::Missing) => STANDARD_WEEKEND_MASK,
            Some(expression) => weekend_mask(&eval_scalar(expression, env)?)?,
        }
    } else {
        STANDARD_WEEKEND_MASK
    };
    if name == "WORKDAY.INTL" && weekend.iter().all(|is_weekend| *is_weekend) {
        return Err(FormulaError::Value);
    }

    let holiday_index = if international { 3 } else { 2 };
    let holidays = match args.get(holiday_index) {
        None | Some(Expr::Missing) => HashSet::new(),
        Some(expression) => workday_holidays(expression, env, international)?,
    };
    if let Some(end_day) = end_day {
        return Ok(Value::Number(
            networkdays_count(start_day, end_day, &holidays, &weekend) as f64,
        ));
    }
    Ok(Value::Number(
        workday_result(start_day, offset, &holidays, &weekend)? as f64,
    ))
}
fn excel_time_hms(serial: f64) -> Result<(i64, i64, i64), FormulaError> {
    excel_serial_ymd(serial)?;
    let mut elapsed_seconds = (serial - serial.floor()) * 86_400.0;
    let nearest_second = elapsed_seconds.round();
    let magnitude = serial.abs();
    let next = f64::from_bits(magnitude.to_bits() + 1);
    let half_ulp_seconds = (next - magnitude) * 43_200.0;
    // Correct at most half a representable serial step before truncating seconds.
    if (elapsed_seconds - nearest_second).abs() <= half_ulp_seconds {
        elapsed_seconds = nearest_second;
    }
    let second_of_day = (elapsed_seconds.floor() as i64).rem_euclid(86_400);
    Ok((
        second_of_day / 3_600,
        (second_of_day / 60) % 60,
        second_of_day % 60,
    ))
}
fn days_in_month(year: i32, month: i32) -> i32 {
    if year == 1900 && month == 2 {
        return 29;
    }
    match month {
        4 | 6 | 9 | 11 => 30,
        2 if year % 400 == 0 || (year % 4 == 0 && year % 100 != 0) => 29,
        2 => 28,
        _ => 31,
    }
}
fn excel_ymd_to_serial(year: i32, month: i32, day: i32) -> Result<i64, FormulaError> {
    if !(1..=9999).contains(&year) || !(1..=12).contains(&month) {
        return Err(FormulaError::Num);
    }
    if day < 1 || day > days_in_month(year, month) {
        return Err(FormulaError::Num);
    }
    if (year, month, day) == (1900, 2, 29) {
        return Ok(60);
    }
    let mut serial =
        days_from_civil(year, month as u32, day as u32) - days_from_civil(1899, 12, 31);
    if year > 1900 || (year == 1900 && month >= 3) {
        serial += 1;
    }
    if !(0..=MAX_EXCEL_DATE_SERIAL).contains(&serial) {
        return Err(FormulaError::Num);
    }
    Ok(serial)
}
fn days_from_civil(y: i32, m: u32, d: u32) -> i64 {
    let y = y - i32::from(m <= 2);
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = m as i32 + if m > 2 { -3 } else { 9 };
    let doy = (153 * mp + 2) / 5 + d as i32 - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    (era * 146097 + doe - 719468) as i64
}
fn civil_from_days(z: i64) -> (i32, u32, u32) {
    let z = z + 719468;
    let era = z.div_euclid(146097);
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let mut y = yoe as i32 + era as i32 * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = mp + if mp < 10 { 3 } else { -9 };
    y += i32::from(m <= 2);
    (y, m as u32, d as u32)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Debug, PartialEq)]
    enum JsonValue {
        Object(HashMap<String, JsonValue>),
        Array(Vec<JsonValue>),
        String(String),
        Number(f64),
        Bool(bool),
        Null,
    }

    impl JsonValue {
        fn get(&self, key: &str) -> Option<&Self> {
            self.as_object()?.get(key)
        }

        fn as_object(&self) -> Option<&HashMap<String, JsonValue>> {
            match self {
                Self::Object(object) => Some(object),
                _ => None,
            }
        }

        fn as_str(&self) -> Option<&str> {
            match self {
                Self::String(value) => Some(value),
                _ => None,
            }
        }

        fn as_f64(&self) -> Option<f64> {
            match self {
                Self::Number(value) => Some(*value),
                _ => None,
            }
        }

        fn as_bool(&self) -> Option<bool> {
            match self {
                Self::Bool(value) => Some(*value),
                _ => None,
            }
        }
    }

    struct JsonParser {
        chars: Vec<char>,
        position: usize,
    }

    impl JsonParser {
        fn parse(source: &str) -> Result<JsonValue, String> {
            let mut parser = Self {
                chars: source.chars().collect(),
                position: 0,
            };
            let value = parser.parse_value()?;
            parser.skip_whitespace();
            if parser.peek().is_some() {
                return Err(parser.error("trailing characters"));
            }
            Ok(value)
        }

        fn parse_value(&mut self) -> Result<JsonValue, String> {
            self.skip_whitespace();
            match self.peek() {
                Some('{') => self.parse_object(),
                Some('[') => self.parse_array(),
                Some('"') => self.parse_string().map(JsonValue::String),
                Some('t') => {
                    self.parse_literal("true")?;
                    Ok(JsonValue::Bool(true))
                }
                Some('f') => {
                    self.parse_literal("false")?;
                    Ok(JsonValue::Bool(false))
                }
                Some('n') => {
                    self.parse_literal("null")?;
                    Ok(JsonValue::Null)
                }
                Some('-' | '0'..='9') => self.parse_number(),
                _ => Err(self.error("expected a JSON value")),
            }
        }

        fn parse_object(&mut self) -> Result<JsonValue, String> {
            self.expect('{')?;
            self.skip_whitespace();
            let mut object = HashMap::new();
            if self.consume('}') {
                return Ok(JsonValue::Object(object));
            }
            loop {
                self.skip_whitespace();
                if self.peek() != Some('"') {
                    return Err(self.error("expected an object key"));
                }
                let key = self.parse_string()?;
                self.skip_whitespace();
                self.expect(':')?;
                let value = self.parse_value()?;
                object.insert(key, value);
                self.skip_whitespace();
                if self.consume('}') {
                    return Ok(JsonValue::Object(object));
                }
                self.expect(',')?;
            }
        }

        fn parse_array(&mut self) -> Result<JsonValue, String> {
            self.expect('[')?;
            self.skip_whitespace();
            let mut array = Vec::new();
            if self.consume(']') {
                return Ok(JsonValue::Array(array));
            }
            loop {
                array.push(self.parse_value()?);
                self.skip_whitespace();
                if self.consume(']') {
                    return Ok(JsonValue::Array(array));
                }
                self.expect(',')?;
            }
        }

        fn parse_string(&mut self) -> Result<String, String> {
            self.expect('"')?;
            let mut value = String::new();
            loop {
                match self.next() {
                    Some('"') => return Ok(value),
                    Some('\\') => match self.next() {
                        Some('"') => value.push('"'),
                        Some('\\') => value.push('\\'),
                        Some('/') => value.push('/'),
                        Some('b') => value.push('\u{0008}'),
                        Some('f') => value.push('\u{000c}'),
                        Some('n') => value.push('\n'),
                        Some('r') => value.push('\r'),
                        Some('t') => value.push('\t'),
                        Some('u') => {
                            let first = self.parse_hex_quad()?;
                            let codepoint = match first {
                                0xD800..=0xDBFF => {
                                    if self.next() != Some('\\') || self.next() != Some('u') {
                                        return Err(self.error(
                                            "high surrogate must be followed by a low surrogate",
                                        ));
                                    }
                                    let second = self.parse_hex_quad()?;
                                    if !(0xDC00..=0xDFFF).contains(&second) {
                                        return Err(self.error("invalid low surrogate"));
                                    }
                                    0x10000
                                        + (((first as u32 - 0xD800) << 10)
                                            | (second as u32 - 0xDC00))
                                }
                                0xDC00..=0xDFFF => {
                                    return Err(self.error("unexpected low surrogate"));
                                }
                                _ => first as u32,
                            };
                            value.push(
                                char::from_u32(codepoint)
                                    .ok_or_else(|| self.error("invalid Unicode code point"))?,
                            );
                        }
                        Some(_) => return Err(self.error("invalid string escape")),
                        None => return Err(self.error("unterminated string escape")),
                    },
                    Some(ch) if (ch as u32) < 0x20 => {
                        return Err(self.error("unescaped control character in string"));
                    }
                    Some(ch) => value.push(ch),
                    None => return Err(self.error("unterminated string")),
                }
            }
        }

        fn parse_hex_quad(&mut self) -> Result<u16, String> {
            let mut value = 0_u16;
            for _ in 0..4 {
                let digit = self
                    .next()
                    .and_then(|ch| ch.to_digit(16))
                    .ok_or_else(|| self.error("expected four hexadecimal digits"))?;
                value = (value << 4) | digit as u16;
            }
            Ok(value)
        }

        fn parse_number(&mut self) -> Result<JsonValue, String> {
            let start = self.position;
            self.consume('-');
            match self.peek() {
                Some('0') => {
                    self.position += 1;
                    if self.peek().is_some_and(|ch| ch.is_ascii_digit()) {
                        return Err(self.error("leading zero in number"));
                    }
                }
                Some('1'..='9') => {
                    self.position += 1;
                    while self.peek().is_some_and(|ch| ch.is_ascii_digit()) {
                        self.position += 1;
                    }
                }
                _ => return Err(self.error("expected integer digits")),
            }
            if self.consume('.') {
                let fraction_start = self.position;
                while self.peek().is_some_and(|ch| ch.is_ascii_digit()) {
                    self.position += 1;
                }
                if self.position == fraction_start {
                    return Err(self.error("expected digits after decimal point"));
                }
            }
            if self.peek().is_some_and(|ch| matches!(ch, 'e' | 'E')) {
                self.position += 1;
                if self.peek().is_some_and(|ch| matches!(ch, '+' | '-')) {
                    self.position += 1;
                }
                let exponent_start = self.position;
                while self.peek().is_some_and(|ch| ch.is_ascii_digit()) {
                    self.position += 1;
                }
                if self.position == exponent_start {
                    return Err(self.error("expected exponent digits"));
                }
            }
            let text: String = self.chars[start..self.position].iter().collect();
            let number = text
                .parse::<f64>()
                .map_err(|_| self.error("invalid JSON number"))?;
            if !number.is_finite() {
                return Err(self.error("number is outside the supported finite range"));
            }
            Ok(JsonValue::Number(number))
        }

        fn parse_literal(&mut self, literal: &str) -> Result<(), String> {
            for expected in literal.chars() {
                if self.next() != Some(expected) {
                    return Err(self.error("invalid JSON literal"));
                }
            }
            Ok(())
        }

        fn skip_whitespace(&mut self) {
            while self
                .peek()
                .is_some_and(|ch| matches!(ch, ' ' | '\t' | '\r' | '\n'))
            {
                self.position += 1;
            }
        }

        fn expect(&mut self, expected: char) -> Result<(), String> {
            if self.consume(expected) {
                Ok(())
            } else {
                Err(self.error(&format!("expected {expected:?}")))
            }
        }

        fn consume(&mut self, expected: char) -> bool {
            if self.peek() == Some(expected) {
                self.position += 1;
                true
            } else {
                false
            }
        }

        fn peek(&self) -> Option<char> {
            self.chars.get(self.position).copied()
        }

        fn next(&mut self) -> Option<char> {
            let ch = self.peek()?;
            self.position += 1;
            Some(ch)
        }

        fn error(&self, message: &str) -> String {
            format!("{message} at character {}", self.position)
        }
    }

    fn cells(pairs: &[(&str, Value)]) -> HashMap<String, Value> {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.clone()))
            .collect()
    }
    fn number(formula: &str) -> f64 {
        match evaluate(formula, &HashMap::new(), "Sheet1").unwrap() {
            Value::Number(n) => n,
            v => panic!("expected number, got {v:?}"),
        }
    }

    fn number_in(formula: &str, cells: &HashMap<String, Value>) -> f64 {
        match evaluate(formula, cells, "Sheet1").unwrap() {
            Value::Number(n) => n,
            value => panic!("expected number, got {value:?}"),
        }
    }

    #[test]
    fn sort_key_errors_follow_source_order_for_nonfinite_inputs() {
        let mixed_before_nonfinite = cells(&[
            ("A1", Value::Number(1.0)),
            ("A2", Value::Text("x".into())),
            ("A3", Value::Number(f64::INFINITY)),
        ]);
        assert_eq!(
            evaluate_result("=SORT(A1:A3)", &mixed_before_nonfinite, "Sheet1"),
            Err(FormulaError::Value)
        );

        let nonfinite_before_mixed = cells(&[
            ("A1", Value::Number(f64::INFINITY)),
            ("A2", Value::Text("x".into())),
            ("A3", Value::Number(1.0)),
        ]);
        assert_eq!(
            evaluate_result("=SORT(A1:A3)", &nonfinite_before_mixed, "Sheet1"),
            Err(FormulaError::Num)
        );
    }

    #[test]
    fn sort_and_unique_numeric_payloads_are_binary64_values() {
        let source = cells(&[
            ("A1", Value::Number(9_007_199_254_740_992.0)),
            ("A2", Value::Number(9_007_199_254_740_992.0)),
        ]);
        let sorted = evaluate_result("=SORT(A1:A2)", &source, "Sheet1").unwrap();
        let FormulaResult::Array(sorted) = sorted else {
            panic!("SORT must preserve its array result");
        };
        assert_eq!(sorted.rows()[0][0], Value::Number(9_007_199_254_740_992.0));
        assert_eq!(sorted.rows()[1][0], Value::Number(9_007_199_254_740_992.0));

        let unique = evaluate_result("=UNIQUE(A1:A2)", &source, "Sheet1").unwrap();
        let FormulaResult::Array(unique) = unique else {
            panic!("UNIQUE must preserve its array result");
        };
        assert_eq!(unique.rows()[0][0], Value::Number(9_007_199_254_740_992.0));
    }

    #[test]
    fn sort_rejects_nonfinite_numeric_payloads_outside_the_key_axis() {
        let source = cells(&[
            ("A1", Value::Number(1.0)),
            ("B1", Value::Number(f64::INFINITY)),
            ("A2", Value::Number(2.0)),
            ("B2", Value::Text("x".into())),
        ]);
        assert_eq!(
            evaluate_result("=SORT(A1:B2)", &source, "Sheet1"),
            Err(FormulaError::Num)
        );
    }

    #[test]
    fn combinatorics_rounds_exact_integer_results_once_to_binary64() {
        assert_eq!(number("=COMBIN(54,23)"), 1_085_929_983_159_840.0);
        assert_eq!(number("=COMBIN(60,19)"), 2_044_802_197_953_900.0);
        assert_eq!(
            number("=COMBIN(1029,514)").to_bits(),
            1.429820686498904e308_f64.to_bits()
        );
        assert_eq!(
            number("=FACT(170)").to_bits(),
            7.257415615307999e306_f64.to_bits()
        );
    }

    #[test]
    fn combinatorics_rejects_integer_literal_above_exact_boundary() {
        assert_eq!(
            evaluate("=COMBIN(9007199254740993,0)", &HashMap::new(), "Sheet1"),
            Err(FormulaError::Num)
        );
        assert_eq!(
            evaluate("=FACT(9007199254740993)", &HashMap::new(), "Sheet1"),
            Err(FormulaError::Num)
        );
    }

    fn json_value(value: &JsonValue) -> Value {
        match value {
            JsonValue::Null => Value::Blank,
            JsonValue::Bool(value) => Value::Bool(*value),
            JsonValue::Number(value) => Value::Number(*value),
            JsonValue::String(value) => Value::Text(value.clone()),
            JsonValue::Object(object) => {
                let Some(code) = object.get("error").and_then(JsonValue::as_str) else {
                    panic!("unsupported shared fixture cell value {value:?}");
                };
                Value::Error(match code {
                    "#VALUE!" => FormulaError::Value,
                    "#DIV/0!" => FormulaError::Div0,
                    "#REF!" => FormulaError::Ref,
                    "#NAME?" => FormulaError::Name,
                    "#NUM!" => FormulaError::Num,
                    "#N/A" => FormulaError::NA,
                    "#CALC!" => FormulaError::Calc,
                    _ => panic!("unsupported shared fixture error code {code:?}"),
                })
            }
            _ => panic!("unsupported shared fixture cell value {value:?}"),
        }
    }

    fn matches_expected(actual: Result<FormulaResult, FormulaError>, expected: &JsonValue) -> bool {
        let Some(object) = expected.as_object() else {
            return false;
        };
        if let Some(JsonValue::Array(expected_rows)) = object.get("array") {
            let Ok(FormulaResult::Array(array)) = actual else {
                return false;
            };
            if array.row_count() != expected_rows.len() {
                return false;
            }
            for (actual_row, expected_row) in array.rows().iter().zip(expected_rows) {
                let JsonValue::Array(expected_cells) = expected_row else {
                    return false;
                };
                if actual_row.len() != expected_cells.len() {
                    return false;
                }
                for (actual_cell, expected_cell) in actual_row.iter().zip(expected_cells) {
                    let expected_value = json_value(expected_cell);
                    let matches = match (&expected_value, actual_cell) {
                        (Value::Number(expected), Value::Number(actual)) => {
                            (actual - expected).abs() <= 1e-12 * expected.abs().max(1.0)
                        }
                        _ => expected_value == *actual_cell,
                    };
                    if !matches {
                        return false;
                    }
                }
            }
            return true;
        }
        if let Some(integer) = object
            .get("exact_number")
            .and_then(JsonValue::as_str)
            .and_then(|value| value.parse::<u64>().ok())
        {
            return matches!(actual, Ok(FormulaResult::Scalar(Value::Number(number))) if number == integer as f64);
        }
        if let Some(number) = object.get("number").and_then(JsonValue::as_f64) {
            return matches!(actual, Ok(FormulaResult::Scalar(Value::Number(n))) if (n - number).abs() <= 1e-12 * number.abs().max(1.0));
        }
        if let Some(text) = object.get("text").and_then(JsonValue::as_str) {
            return actual == Ok(FormulaResult::Scalar(Value::Text(text.into())));
        }
        if let Some(boolean) = object.get("bool").and_then(JsonValue::as_bool) {
            return actual == Ok(FormulaResult::Scalar(Value::Bool(boolean)));
        }
        if object.get("blank").and_then(JsonValue::as_bool) == Some(true) {
            return actual == Ok(FormulaResult::Scalar(Value::Blank));
        }
        if let Some(code) = object.get("error").and_then(JsonValue::as_str) {
            return matches!(actual, Err(ref error) if error.excel_code() == Some(code));
        }
        false
    }

    #[test]
    fn shared_golden_formula_cases() {
        let mut cases = 0;
        for (line_number, line) in include_str!("../../fixtures/formula-cases.jsonl")
            .lines()
            .enumerate()
        {
            if line.trim().is_empty() {
                continue;
            }
            let case = JsonParser::parse(line).unwrap_or_else(|error| {
                panic!("fixture line {} is invalid JSON: {error}", line_number + 1)
            });
            let formula = case
                .get("formula")
                .and_then(JsonValue::as_str)
                .expect("fixture formula string");
            let sheet_name = case
                .get("sheet_name")
                .and_then(JsonValue::as_str)
                .unwrap_or("Sheet1");
            let cells: HashMap<String, Value> = case
                .get("cells")
                .expect("fixture cells object")
                .as_object()
                .expect("fixture cells object")
                .iter()
                .map(|(key, value)| (key.clone(), json_value(value)))
                .collect();
            let expected = case.get("expected").expect("fixture expected result");
            let actual = if expected.get("array").is_some() {
                evaluate_result(formula, &cells, sheet_name)
            } else {
                evaluate(formula, &cells, sheet_name).map(FormulaResult::Scalar)
            };
            assert!(
                matches_expected(actual.clone(), expected),
                "case {} (line {}): formula {formula:?}; expected {expected:?}; got {actual:?}",
                case.get("id")
                    .and_then(JsonValue::as_str)
                    .unwrap_or("unnamed"),
                line_number + 1
            );
            cases += 1;
        }
        assert!(cases > 0, "shared fixture file contains no cases");
    }

    #[test]
    fn parser_and_evaluator_bound_nesting_without_rejecting_flat_chains() {
        let nested_64 = format!("=ABS({}1{}", "ABS(".repeat(63), ")".repeat(64));
        let nested_65 = format!("=ABS({}1{}", "ABS(".repeat(64), ")".repeat(65));
        assert_eq!(
            evaluate(&nested_64, &HashMap::new(), "S"),
            Ok(Value::Number(1.0))
        );
        assert_eq!(
            evaluate(&nested_65, &HashMap::new(), "S"),
            Err(FormulaError::Value)
        );

        let grouped_over_profile = format!("{}1{}", "(".repeat(97), ")".repeat(97));
        assert_eq!(
            evaluate(&grouped_over_profile, &HashMap::new(), "S"),
            Err(FormulaError::Value)
        );

        let long_sum = format!("=1{}", "+1".repeat(1_000));
        assert_eq!(
            evaluate(&long_sum, &HashMap::new(), "S"),
            Ok(Value::Number(1_001.0))
        );
        let long_unary = format!("={}1", "+".repeat(4_096));
        assert_eq!(
            evaluate(&long_unary, &HashMap::new(), "S"),
            Ok(Value::Number(1.0))
        );
        let long_postfix = format!("=1{}", "%".repeat(1_000));
        assert_eq!(
            evaluate(&long_postfix, &HashMap::new(), "S"),
            Ok(Value::Number(0.0))
        );

        let too_long = "1".repeat(MAX_FORMULA_LENGTH_UNITS + 1);
        assert_eq!(
            evaluate(&too_long, &HashMap::new(), "S"),
            Err(FormulaError::Value)
        );
    }

    #[test]
    fn wildcard_matching_stops_at_the_formula_work_budget() {
        let formula = format!("=COUNTIF(A1,\"{}\")", "a*".repeat(2_000));
        let input = cells(&[("A1", Value::Text("a".repeat(2_000)))]);
        assert_eq!(evaluate(&formula, &input, "S"), Err(FormulaError::Value));
    }

    #[test]
    fn result_api_preserves_array_shape_and_scalar_api_stays_scalar() {
        let result = evaluate_result("=SEQUENCE(2,3,10,10)", &HashMap::new(), "Sheet1").unwrap();
        let FormulaResult::Array(array) = result else {
            panic!("expected a shaped result array");
        };
        assert_eq!(array.row_count(), 2);
        assert_eq!(array.column_count(), 3);
        assert_eq!(
            array.rows(),
            &[
                vec![
                    Value::Number(10.0),
                    Value::Number(20.0),
                    Value::Number(30.0)
                ],
                vec![
                    Value::Number(40.0),
                    Value::Number(50.0),
                    Value::Number(60.0)
                ],
            ]
        );
        assert!(matches!(
            evaluate("=SEQUENCE(2,3,10,10)", &HashMap::new(), "Sheet1"),
            Err(FormulaError::Value)
        ));
        assert_eq!(number("=SUM(SEQUENCE(3,2))"), 21.0);
    }

    #[test]
    fn result_api_materializes_a_range_without_exposing_internal_calc_values() {
        let cell_values = cells(&[
            ("A1", Value::Number(1.0)),
            ("B1", Value::Number(2.0)),
            ("A2", Value::Number(3.0)),
            ("B2", Value::Number(4.0)),
        ]);
        let result = evaluate_result("=A1:B2", &cell_values, "Sheet1").unwrap();
        let FormulaResult::Array(array) = result else {
            panic!("expected a shaped range result");
        };
        assert_eq!(array.row_count(), 2);
        assert_eq!(array.column_count(), 2);
        assert_eq!(
            array.rows(),
            &[
                vec![Value::Number(1.0), Value::Number(2.0)],
                vec![Value::Number(3.0), Value::Number(4.0)],
            ]
        );
    }

    #[test]
    fn amortization_zero_prorated_stub_uses_full_year_amount() {
        assert_eq!(number("=AMORLINC(1000,43860,43861,100,0,0.2,0)"), 200.0);
        assert_eq!(number("=AMORLINC(1000,43860,43861,100,1,0.2,0)"), 200.0);
        assert_eq!(number("=AMORDEGRC(1000,43860,43861,100,0,2/7,0)"), 429.0);
        assert_eq!(number("=AMORDEGRC(1000,43860,43861,100,1,2/7,0)"), 286.0);
    }

    #[test]
    fn fixture_json_parser_handles_string_escapes_and_surrogate_pairs() {
        let parsed = JsonParser::parse(
            r#"{"value":"quote: \" slash: \\ snowman: \u2603 g-clef: \uD834\uDD1E"}"#,
        )
        .unwrap();
        assert_eq!(
            parsed.get("value").and_then(JsonValue::as_str),
            Some("quote: \" slash: \\ snowman: ☃ g-clef: 𝄞")
        );
        assert!(JsonParser::parse(r#""\uD800""#).is_err());
    }

    #[test]
    fn arithmetic_precedence_and_excel_power_unary() {
        assert_eq!(number("=2+3*4"), 14.0);
        assert_eq!(number("=(2+3)*4"), 20.0);
        assert_eq!(number("=-2^2"), 4.0);
        assert_eq!(number("=2^3^2"), 64.0);
    }

    #[test]
    fn ifna_catches_only_na_and_does_not_evaluate_unused_fallback() {
        let c = cells(&[("A1", Value::Error(FormulaError::NA))]);
        assert_eq!(
            evaluate("=IFNA(A1,42)", &c, "Sheet1"),
            Ok(Value::Number(42.0))
        );
        assert_eq!(
            evaluate("=IFNA(7,1/0)", &c, "Sheet1"),
            Ok(Value::Number(7.0))
        );
        assert_eq!(
            evaluate("=IFNA(1/0,42)", &c, "Sheet1"),
            Err(FormulaError::Div0)
        );
        assert_eq!(
            evaluate("=IFNA(A1,1/0)", &c, "Sheet1"),
            Err(FormulaError::Div0)
        );
        assert_eq!(
            evaluate("=IFNA(#N/A,\"literal fallback\")", &c, "Sheet1"),
            Ok(Value::Text("literal fallback".into()))
        );
        assert_eq!(
            evaluate("=IFERROR(#N/A,\"error fallback\")", &c, "Sheet1"),
            Ok(Value::Text("error fallback".into()))
        );
        assert_eq!(evaluate("=#N/A", &c, "Sheet1"), Err(FormulaError::NA));
        assert!(matches!(
            evaluate("=IFERROR(#DIV/0!,0)", &c, "Sheet1"),
            Err(FormulaError::Unsupported(_))
        ));
    }

    #[test]
    fn information_functions_check_value_types() {
        let c = cells(&[
            ("A1", Value::Blank),
            ("A2", Value::Number(2.0)),
            ("A3", Value::Text(String::new())),
            ("A4", Value::Bool(true)),
            ("A5", Value::Error(FormulaError::NA)),
        ]);
        for (formula, expected) in [
            ("=ISBLANK(A1)", true),
            ("=ISNUMBER(A2)", true),
            ("=ISTEXT(A3)", true),
            ("=ISLOGICAL(A4)", true),
            ("=ISNUMBER(A5)", false),
            ("=ISNUMBER(1/0)", false),
            ("=ISBLANK(\"\")", false),
        ] {
            assert_eq!(
                evaluate(formula, &c, "Sheet1"),
                Ok(Value::Bool(expected)),
                "{formula}"
            );
        }
    }

    #[test]
    fn text_case_trim_substitute_find_and_search() {
        assert_eq!(
            evaluate("=UPPER(\"Abc ß\")", &HashMap::new(), "s"),
            Ok(Value::Text("ABC SS".into()))
        );
        assert_eq!(
            evaluate("=LOWER(\"AbC\")", &HashMap::new(), "s"),
            Ok(Value::Text("abc".into()))
        );
        assert_eq!(
            evaluate("=TRIM(\"  one   two  \")", &HashMap::new(), "s"),
            Ok(Value::Text("one two".into()))
        );
        assert_eq!(
            evaluate("=SUBSTITUTE(\"abab\",\"ab\",\"x\")", &HashMap::new(), "s"),
            Ok(Value::Text("xx".into()))
        );
        assert_eq!(
            evaluate("=SUBSTITUTE(\"abab\",\"ab\",\"x\",2)", &HashMap::new(), "s"),
            Ok(Value::Text("abx".into()))
        );
        assert_eq!(number("=FIND(\"B\",\"abcB\")"), 4.0);
        assert_eq!(number("=SEARCH(\"b\",\"aBc\")"), 2.0);
        assert_eq!(number("=FIND(\"b\",\"abc\",2)"), 2.0);
        assert_eq!(
            evaluate("=FIND(\"z\",\"abc\")", &HashMap::new(), "s"),
            Err(FormulaError::Value)
        );
        assert_eq!(number("=SEARCH(\"ss\",\"ß\")"), 1.0);
        assert_eq!(
            evaluate("=UPPER(A1:A2)", &HashMap::new(), "s"),
            Err(FormulaError::Value)
        );
        assert_eq!(
            evaluate("=SUBSTITUTE(\"abc\",\"\",\"x\",1)", &HashMap::new(), "s"),
            Err(FormulaError::Value)
        );
    }

    #[test]
    fn textjoin_flattens_ranges_and_obeys_ignore_empty() {
        let c = cells(&[
            ("A1", Value::Text("one".into())),
            ("A2", Value::Blank),
            ("A3", Value::Text("two".into())),
            ("A4", Value::Text(String::new())),
        ]);
        assert_eq!(
            evaluate("=TEXTJOIN(\",\",TRUE,A1:A4)", &c, "Sheet1"),
            Ok(Value::Text("one,two".into()))
        );
        assert_eq!(
            evaluate("=TEXTJOIN(\",\",FALSE,A1:A4)", &c, "Sheet1"),
            Ok(Value::Text("one,,two,".into()))
        );
    }

    #[test]
    fn text_builders_preflight_the_utf16_cell_limit() {
        assert_eq!(utf16_text_units("😀"), 2);
        let first = Value::Text("12345".into());
        let second = Value::Text("67890".into());
        assert_eq!(
            join_values_bounded(&[&first, &second], "", false, 8),
            Err(FormulaError::Value)
        );
        assert_eq!(
            substitute_all_bounded("aaaa", "a", "123", 8),
            Err(FormulaError::Value)
        );

        let oversized = "😀".repeat(16_384);
        assert_eq!(
            evaluate(&format!("=\"{oversized}\""), &HashMap::new(), "Sheet1"),
            Err(FormulaError::Value)
        );

        let large = "x".repeat(16_384);
        let cells = cells(&[
            ("A1", Value::Text(large.clone())),
            ("A2", Value::Text(large)),
            ("A3", Value::Text("a".repeat(16_384))),
            ("B1", Value::Text("xx".into())),
        ]);
        for formula in [
            "=A1&A2",
            "=CONCAT(A1:A2)",
            "=TEXTJOIN(\"-\",FALSE,A1:A2)",
            "=SUBSTITUTE(A3,\"a\",B1)",
        ] {
            assert_eq!(
                evaluate(formula, &cells, "Sheet1"),
                Err(FormulaError::Value),
                "{formula}"
            );
        }
    }

    #[test]
    fn comparisons_and_concat() {
        assert_eq!(
            evaluate("=\"A\"&\"B\"", &HashMap::new(), "s"),
            Ok(Value::Text("AB".into()))
        );
        assert_eq!(
            evaluate("=2>=2", &HashMap::new(), "s"),
            Ok(Value::Bool(true))
        );
    }
    #[test]
    fn ranges_aggregates_and_references() {
        let c = cells(&[
            ("A1", Value::Number(2.0)),
            ("A2", Value::Number(3.0)),
            ("A3", Value::Number(4.0)),
            ("B1", Value::Text("x".into())),
        ]);
        assert_eq!(
            evaluate("=SUM(A1:A3)", &c, "Sheet1"),
            Ok(Value::Number(9.0))
        );
        assert_eq!(
            evaluate("=COUNT(A1:B1)", &c, "Sheet1"),
            Ok(Value::Number(1.0))
        );
        assert_eq!(
            evaluate("=COUNTA(A1:B1)", &c, "Sheet1"),
            Ok(Value::Number(2.0))
        );
        assert_eq!(
            evaluate("=MIN(A1:A3)", &c, "Sheet1"),
            Ok(Value::Number(2.0))
        );
        assert_eq!(
            evaluate("=MAX(A1:A3)", &c, "Sheet1"),
            Ok(Value::Number(4.0))
        );
    }
    #[test]
    fn lazy_conditionals_and_errors() {
        assert_eq!(
            evaluate(
                "=IF(A1>2,\"yes\",1/0)",
                &cells(&[("A1", Value::Number(3.0))]),
                "S"
            ),
            Ok(Value::Text("yes".into()))
        );
        assert_eq!(
            evaluate("=IFERROR(1/0,7)", &HashMap::new(), "S"),
            Ok(Value::Number(7.0))
        );
        assert_eq!(
            evaluate("=1/0", &HashMap::new(), "S"),
            Err(FormulaError::Div0)
        );
    }
    #[test]
    fn text_functions() {
        assert_eq!(
            evaluate("=LEFT(\"atlas\",2)", &HashMap::new(), "S"),
            Ok(Value::Text("at".into()))
        );
        assert_eq!(
            evaluate("=RIGHT(\"atlas\",2)", &HashMap::new(), "S"),
            Ok(Value::Text("as".into()))
        );
        assert_eq!(
            evaluate("=MID(\"atlas\",2,3)", &HashMap::new(), "S"),
            Ok(Value::Text("tla".into()))
        );
        assert_eq!(
            evaluate("=LEN(\"atlas\")", &HashMap::new(), "S"),
            Ok(Value::Number(5.0))
        );
    }
    #[test]
    fn lookup_functions() {
        let c = cells(&[
            ("A1", Value::Number(10.0)),
            ("B1", Value::Text("ten".into())),
            ("A2", Value::Number(20.0)),
            ("B2", Value::Text("twenty".into())),
        ]);
        assert_eq!(
            evaluate("=VLOOKUP(20,A1:B2,2,FALSE)", &c, "S"),
            Ok(Value::Text("twenty".into()))
        );
        assert_eq!(
            evaluate("=MATCH(20,A1:A2,0)", &c, "S"),
            Ok(Value::Number(2.0))
        );
        assert_eq!(
            evaluate("=INDEX(A1:B2,2,2)", &c, "S"),
            Ok(Value::Text("twenty".into()))
        );
        assert_eq!(
            evaluate("=INDEX(A1:B2,2,2,1)", &c, "S"),
            Ok(Value::Text("twenty".into()))
        );
        assert_eq!(
            evaluate("=INDEX(A1:B2,2,2,2)", &c, "S"),
            Err(FormulaError::Ref)
        );
        assert!(matches!(
            evaluate("=INDEX(A1:B2,2,2,0)", &c, "S"),
            Err(FormulaError::Unsupported(_))
        ));
        assert_eq!(
            evaluate("=INDEX(A1:B2,2,2,1.5)", &c, "S"),
            Err(FormulaError::Value)
        );
    }

    #[test]
    fn match_and_exact_vlookup_support_case_insensitive_wildcards() {
        let c = cells(&[
            ("A1", Value::Text("Alpha".into())),
            ("B1", Value::Text("first".into())),
            ("A2", Value::Text("Alpine".into())),
            ("B2", Value::Text("second".into())),
            ("A3", Value::Text("A*".into())),
            ("B3", Value::Text("literal star".into())),
        ]);
        for formula in [
            "=MATCH(\"alpha\",A1:A3,0)",
            "=MATCH(\"a*\",A1:A3,0)",
            "=MATCH(\"AL?HA\",A1:A3,0)",
        ] {
            assert_eq!(
                evaluate(formula, &c, "S"),
                Ok(Value::Number(1.0)),
                "{formula}"
            );
        }
        assert_eq!(
            evaluate("=MATCH(\"A~*\",A1:A3,0)", &c, "S"),
            Ok(Value::Number(3.0))
        );
        assert_eq!(
            evaluate("=VLOOKUP(\"al?ha\",A1:B3,2,FALSE)", &c, "S"),
            Ok(Value::Text("first".into()))
        );
        assert_eq!(
            evaluate("=VLOOKUP(\"A~*\",A1:B3,2,FALSE)", &c, "S"),
            Ok(Value::Text("literal star".into()))
        );
    }

    #[test]
    fn xlookup_and_xmatch_support_exact_forward_and_reverse_search() {
        let c = cells(&[
            ("A1", Value::Number(1.0)),
            ("A2", Value::Number(2.0)),
            ("A3", Value::Number(2.0)),
            ("B1", Value::Text("one".into())),
            ("B2", Value::Text("first two".into())),
            ("B3", Value::Text("last two".into())),
        ]);

        assert_eq!(
            evaluate("=XLOOKUP(2,A1:A3,B1:B3)", &c, "S"),
            Ok(Value::Text("first two".into()))
        );
        assert_eq!(
            evaluate("=XLOOKUP(2,A1:A3,B1:B3,1/0)", &c, "S"),
            Ok(Value::Text("first two".into()))
        );
        assert_eq!(
            evaluate("=XLOOKUP(2,A1:A3,B1:B3,\"missing\",0,-1)", &c, "S"),
            Ok(Value::Text("last two".into()))
        );
        assert_eq!(
            evaluate("=XMATCH(2,A1:A3)", &c, "S"),
            Ok(Value::Number(2.0))
        );
        assert_eq!(
            evaluate("=XMATCH(2,A1:A3,0,-1)", &c, "S"),
            Ok(Value::Number(3.0))
        );
    }

    #[test]
    fn xlookup_not_found_modes_shapes_and_errors_are_explicit() {
        let c = cells(&[
            ("A1", Value::Number(1.0)),
            ("A2", Value::Number(2.0)),
            ("B1", Value::Text("one".into())),
            ("B2", Value::Text("two".into())),
            ("C1", Value::Text("top-left".into())),
            ("C2", Value::Text("bottom-left".into())),
            ("D1", Value::Text("top-right".into())),
            ("D2", Value::Text("bottom-right".into())),
            ("E1", Value::Error(FormulaError::NA)),
        ]);
        assert_eq!(
            evaluate("=XLOOKUP(9,A1:A2,B1:B2)", &c, "S"),
            Err(FormulaError::NA)
        );
        assert_eq!(
            evaluate("=XLOOKUP(9,A1:A2,B1:B2,\"missing\")", &c, "S"),
            Ok(Value::Text("missing".into()))
        );
        assert_eq!(
            evaluate("=XLOOKUP(9,A1:A2,B1:B2,1/0)", &c, "S"),
            Err(FormulaError::Div0)
        );
        assert_eq!(
            evaluate("=XLOOKUP(1,A1:A2,B1:B1)", &c, "S"),
            Err(FormulaError::Value)
        );
        assert_eq!(
            evaluate("=XMATCH(1,C1:D2)", &c, "S"),
            Err(FormulaError::Value)
        );
        assert!(matches!(
            evaluate("=XLOOKUP(1,A1:A2,B1:B2,\"missing\",1)", &c, "S"),
            Err(FormulaError::Unsupported(_))
        ));
        assert!(matches!(
            evaluate("=XMATCH(1,A1:A2,0,2)", &c, "S"),
            Err(FormulaError::Unsupported(_))
        ));
        assert_eq!(
            evaluate("=XLOOKUP(E1,A1:A2,B1:B2)", &c, "S"),
            Err(FormulaError::NA)
        );
    }
    #[test]
    fn criteria_functions_support_operators_wildcards_and_blanks() {
        let c = cells(&[
            ("A1", Value::Text("apple".into())),
            ("A2", Value::Text("apricot".into())),
            ("A3", Value::Text("banana".into())),
            ("A4", Value::Blank),
            ("A5", Value::Text(String::new())),
            ("A6", Value::Number(5.0)),
            ("A7", Value::Number(10.0)),
            ("A8", Value::Text("A*".into())),
            ("B1", Value::Number(10.0)),
            ("B2", Value::Number(20.0)),
            ("B3", Value::Number(30.0)),
            ("B4", Value::Number(40.0)),
            ("B5", Value::Number(50.0)),
            ("B6", Value::Number(5.0)),
            ("B7", Value::Number(10.0)),
            ("B8", Value::Number(80.0)),
        ]);
        for (formula, expected) in [
            ("=COUNTIF(A1:A3,\"a*\")", 2.0),
            ("=COUNTIF(A1:A8,\"A~*\")", 1.0),
            ("=COUNTIF(A1:A8,\"\")", 2.0),
            ("=COUNTIF(A6:A7,\">5\")", 1.0),
            ("=COUNTIF(A6:A7,\"=5\")", 1.0),
            ("=SUMIF(A1:A3,\"a*\",B1:B3)", 30.0),
            ("=SUMIF(A6:A7,\">5\")", 10.0),
            ("=AVERAGEIF(A6:A7,\">0\")", 7.5),
        ] {
            assert_eq!(number_in(formula, &c), expected, "{formula}");
        }
        assert_eq!(
            evaluate("=COUNTIF(A1:A8,\">=\")", &c, "S"),
            Err(FormulaError::Value)
        );
        assert_eq!(
            evaluate("=COUNTIF(A1:A8,\">*x\")", &c, "S"),
            Err(FormulaError::Value)
        );
        assert_eq!(
            evaluate("=COUNTIF(A1,\"apple\")", &c, "S"),
            Ok(Value::Number(1.0))
        );
        assert_eq!(evaluate("=COUNTIF(1,1)", &c, "S"), Err(FormulaError::Value));
    }

    #[test]
    fn criteria_multi_functions_match_all_pairs_and_validate_shapes() {
        let c = cells(&[
            ("A1", Value::Text("north".into())),
            ("A2", Value::Text("north".into())),
            ("A3", Value::Text("south".into())),
            ("A4", Value::Text("south".into())),
            ("B1", Value::Number(1.0)),
            ("B2", Value::Number(2.0)),
            ("B3", Value::Number(3.0)),
            ("B4", Value::Number(4.0)),
            ("C1", Value::Text("paid".into())),
            ("C2", Value::Text("unpaid".into())),
            ("C3", Value::Text("paid".into())),
            ("C4", Value::Text("paid".into())),
        ]);
        assert_eq!(
            number_in("=COUNTIFS(A1:A4,\"north\",C1:C4,\"paid\")", &c),
            1.0
        );
        assert_eq!(
            number_in("=SUMIFS(B1:B4,A1:A4,\"south\",C1:C4,\"paid\")", &c),
            7.0
        );
        assert_eq!(
            number_in("=AVERAGEIFS(B1:B4,A1:A4,\"south\",C1:C4,\"paid\")", &c),
            3.5
        );
        assert_eq!(
            evaluate("=COUNTIFS(A1:A4,\"north\",C1:C3,\"paid\")", &c, "S"),
            Err(FormulaError::Value)
        );
        assert_eq!(
            evaluate("=COUNTIFS(A1:A4,\"north\",C1:C4)", &c, "S"),
            Err(FormulaError::Value)
        );
        assert_eq!(
            evaluate("=SUMIF(A1:A4,\"north\",B1:B3)", &c, "S"),
            Err(FormulaError::Value)
        );
        assert_eq!(
            evaluate("=AVERAGEIF(A1:A4,\"missing\",B1:B4)", &c, "S"),
            Err(FormulaError::Div0)
        );
    }

    #[test]
    fn countblank_and_minifs_handle_error_cells_by_range_role() {
        let c = cells(&[
            ("A1", Value::Text("skip".into())),
            ("A2", Value::Text("match".into())),
            ("B1", Value::Error(FormulaError::NA)),
            ("B2", Value::Number(7.0)),
        ]);
        assert_eq!(
            evaluate("=COUNTBLANK(A1:B2)", &c, "S"),
            Ok(Value::Number(0.0))
        );
        assert_eq!(
            evaluate("=MINIFS(B1:B2,A1:A2,\"match\")", &c, "S"),
            Ok(Value::Number(7.0))
        );
        assert_eq!(
            evaluate("=MINIFS(B1:B2,A1:A2,\"skip\")", &c, "S"),
            Err(FormulaError::NA)
        );
    }

    #[test]
    fn dates_and_sheet_qualified_refs() {
        assert_eq!(
            evaluate("=DATE(2024,1,1)", &HashMap::new(), "S"),
            Ok(Value::Number(45292.0))
        );
        assert_eq!(
            evaluate("=YEAR(DATE(2024,1,1))", &HashMap::new(), "S"),
            Ok(Value::Number(2024.0))
        );
        for (formula, expected) in [
            ("=DATE(1900,1,1)", 1.0),
            ("=DATE(1900,2,28)", 59.0),
            ("=DATE(1900,1,60)", 60.0),
            ("=DATE(1900,3,1)", 61.0),
            ("=DATE(0,1,1)", 1.0),
            ("=DAY(59)", 28.0),
            ("=DAY(60)", 29.0),
            ("=DAY(61)", 1.0),
            ("=YEAR(60)", 1900.0),
        ] {
            assert_eq!(number(formula), expected, "{formula}");
        }
        let c = cells(&[("Other!A1", Value::Number(9.0))]);
        assert_eq!(evaluate("=Other!A1+1", &c, "S"), Ok(Value::Number(10.0)));
        let c = cells(&[
            ("Data!A1", Value::Number(1.0)),
            ("Data!A2", Value::Number(2.0)),
            ("Other!A2", Value::Number(9.0)),
        ]);
        assert_eq!(
            evaluate("=SUM(Data!A1:A2)", &c, "S"),
            Ok(Value::Number(3.0))
        );
        assert_eq!(
            evaluate("=SUM(A1:Data!A2)", &c, "Data"),
            Ok(Value::Number(3.0))
        );
        assert!(matches!(
            evaluate("=SUM(Data!A1:Other!A2)", &c, "S"),
            Err(FormulaError::Unsupported(_))
        ));
    }
    #[test]
    fn array_functions_return_shaped_results() {
        let mut cells = HashMap::new();
        cells.insert("A1".into(), Value::Number(1.0));
        cells.insert("A2".into(), Value::Number(0.0));
        assert!(matches!(
            evaluate_result("=FILTER(A1:A2,A1:A2)", &cells, "S"),
            Ok(FormulaResult::Array(_))
        ));
        assert!(matches!(
            evaluate_result("=SORT(A1:A2)", &cells, "S"),
            Ok(FormulaResult::Array(_))
        ));
        assert!(matches!(
            evaluate_result("=UNIQUE(A1:A2)", &cells, "S"),
            Ok(FormulaResult::Array(_))
        ));
        assert_eq!(
            evaluate("=SORT(A1:A2)", &cells, "S"),
            Err(FormulaError::Value)
        );
    }
}
