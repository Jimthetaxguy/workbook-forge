//! Everyday worksheet functions: scalar math, aggregates, text, lookup and
//! cash-flow finance.
//!
//! Each function here states its Workbook Forge profile in the catalog's
//! semantic spec. The Python evaluator implements the same rules by hand;
//! neither engine calls the other.

use super::*;

/// Evaluate one argument. An Excel error becomes an error value so that the
/// caller decides which error comes first; a refusal still stops evaluation.
fn argument(expression: &Expr, env: &Environment<'_>) -> Result<CalcValue, FormulaError> {
    match eval(expression, env) {
        Ok(value) => Ok(value),
        Err(error) if error.excel_code().is_some() => Ok(scalar(Value::Error(error))),
        Err(error) => Err(error),
    }
}

/// Scalar arguments in order. A range or array argument is `#VALUE!`; after
/// that the first error value in argument order is returned.
fn scalar_arguments(args: &[Expr], env: &Environment<'_>) -> Result<Vec<Value>, FormulaError> {
    let mut values = Vec::with_capacity(args.len());
    let mut has_range = false;
    for expression in args {
        match argument(expression, env)? {
            CalcValue::Scalar(value) | CalcValue::Referenced(value) => values.push(value),
            CalcValue::Range(_) | CalcValue::Array(_) => has_range = true,
        }
    }
    if has_range {
        return Err(FormulaError::Value);
    }
    for value in &values {
        propagate_value_error(value)?;
    }
    Ok(values)
}

/// Scalar arguments coerced to numbers left to right with the shared coercer.
fn number_arguments(args: &[Expr], env: &Environment<'_>) -> Result<Vec<f64>, FormulaError> {
    scalar_arguments(args, env)?.iter().map(to_number).collect()
}

/// A finite result; a zero result carries no sign.
fn finite_number(result: f64) -> Result<Value, FormulaError> {
    if !result.is_finite() {
        Err(FormulaError::Num)
    } else if result == 0.0 {
        Ok(Value::Number(0.0))
    } else {
        Ok(Value::Number(result))
    }
}

/// How many whole multiples of `significance` the rounded number holds.
///
/// The quotient is rounded to a whole number by the evaluator's directed
/// rounding rule, so a quotient within `BOUNDARY_STEPS` binary64 values of a
/// whole number is that whole number: 0.3/0.1 is three multiples, not 2.99.
fn multiple_count(number: f64, significance: f64, upward: bool) -> Result<f64, FormulaError> {
    let quotient = number / significance;
    if !quotient.is_finite() {
        return Err(FormulaError::Num);
    }
    let mode = if (quotient > 0.0) == upward {
        "away-from-zero"
    } else {
        "toward-zero"
    };
    round_to_precision(quotient, 0, mode)
}

/// `count` multiples of `significance` as one binary64 product.
///
/// A product that is not a whole number and lies within `BOUNDARY_STEPS`
/// binary64 values of its own 15-significant-digit decimal is returned as
/// that decimal, so 15 multiples of 0.1 are 1.5 and not 1.5000000000000002.
fn multiple_product(count: f64, significance: f64) -> Result<Value, FormulaError> {
    let product = count * significance;
    if !product.is_finite() {
        return Err(FormulaError::Num);
    }
    if product == 0.0 || product.fract() == 0.0 {
        return finite_number(product);
    }
    let magnitude = product.abs();
    let written = format!("{magnitude:.14e}");
    if steps_to(magnitude, &written) <= BOUNDARY_STEPS {
        let decimal: f64 = written.parse().map_err(|_| FormulaError::Num)?;
        return finite_number(decimal.copysign(product));
    }
    finite_number(product)
}

fn rounded_multiple(name: &str, numbers: &[f64]) -> Result<Value, FormulaError> {
    let number = numbers[0];
    match name {
        "CEILING" | "FLOOR" => {
            let significance = numbers[1];
            if significance == 0.0 {
                return if name == "CEILING" {
                    Ok(Value::Number(0.0))
                } else {
                    Err(FormulaError::Div0)
                };
            }
            if number > 0.0 && significance < 0.0 {
                return Err(FormulaError::Num);
            }
            let count = multiple_count(number, significance, name == "CEILING")?;
            multiple_product(count, significance)
        }
        "CEILING.MATH" | "FLOOR.MATH" => {
            let significance = numbers.get(1).copied().unwrap_or(1.0).abs();
            let mode = numbers.get(2).copied().unwrap_or(0.0);
            if significance == 0.0 {
                return Ok(Value::Number(0.0));
            }
            // A nonzero mode reverses the direction for negative numbers only.
            let reversed = number < 0.0 && mode != 0.0;
            let upward = (name == "CEILING.MATH") != reversed;
            let count = multiple_count(number, significance, upward)?;
            multiple_product(count, significance)
        }
        _ => {
            // MROUND rounds the computed binary64 quotient half away from zero.
            let multiple = numbers[1];
            if (number > 0.0 && multiple < 0.0) || (number < 0.0 && multiple > 0.0) {
                return Err(FormulaError::Num);
            }
            if number == 0.0 || multiple == 0.0 {
                return Ok(Value::Number(0.0));
            }
            let quotient = number / multiple;
            if !quotient.is_finite() {
                return Err(FormulaError::Num);
            }
            let lower = quotient.floor();
            let count = if quotient - lower >= 0.5 {
                lower + 1.0
            } else {
                lower
            };
            multiple_product(count, multiple)
        }
    }
}

#[inline(never)]
pub(super) fn scalar_math_call(
    name: &str,
    args: &[Expr],
    env: &Environment<'_>,
) -> Result<Value, FormulaError> {
    let (min, max) = match name {
        "PI" | "TRUE" | "FALSE" => (0, 0),
        "SQRT" | "EXP" | "LN" | "LOG10" | "SIGN" => (1, 1),
        "LOG" => (1, 2),
        "CEILING.MATH" | "FLOOR.MATH" => (1, 3),
        _ => (2, 2),
    };
    arity(name, args, min, max)?;
    let numbers = number_arguments(args, env)?;
    match name {
        "PI" => Ok(Value::Number(std::f64::consts::PI)),
        "TRUE" => Ok(Value::Bool(true)),
        "FALSE" => Ok(Value::Bool(false)),
        "SQRT" => {
            if numbers[0] < 0.0 {
                return Err(FormulaError::Num);
            }
            finite_number(numbers[0].sqrt())
        }
        "POWER" => finite_number(numbers[0].powf(numbers[1])),
        "EXP" => finite_number(numbers[0].exp()),
        "LN" | "LOG10" => {
            if numbers[0] <= 0.0 {
                return Err(FormulaError::Num);
            }
            finite_number(if name == "LN" {
                numbers[0].ln()
            } else {
                numbers[0].log10()
            })
        }
        "LOG" => {
            let number = numbers[0];
            let base = numbers.get(1).copied().unwrap_or(10.0);
            if number <= 0.0 || base <= 0.0 {
                return Err(FormulaError::Num);
            }
            if base == 1.0 {
                return Err(FormulaError::Div0);
            }
            finite_number(if base == 10.0 {
                number.log10()
            } else if base == 2.0 {
                number.log2()
            } else {
                number.ln() / base.ln()
            })
        }
        "SIGN" => Ok(Value::Number(if numbers[0] > 0.0 {
            1.0
        } else if numbers[0] < 0.0 {
            -1.0
        } else {
            0.0
        })),
        _ => rounded_multiple(name, &numbers),
    }
}

/// XOR: TRUE when an odd number of logical values are TRUE.
///
/// Text and empty cells inside an array or reference are ignored, as for AND
/// and OR. Every argument is evaluated, in order.
#[inline(never)]
pub(super) fn xor_call(args: &[Expr], env: &Environment<'_>) -> Result<Value, FormulaError> {
    arity("XOR", args, 1, 254)?;
    let mut tested = false;
    let mut result = false;
    for expression in args {
        let evaluated = argument(expression, env)?;
        let referenced = keeps_reference(expression, &evaluated);
        for value in flatten(evaluated) {
            if referenced && matches!(value, Value::Text(_) | Value::Blank) {
                continue;
            }
            tested = true;
            result ^= truthy(&value)?;
        }
    }
    if !tested {
        return Err(FormulaError::Value);
    }
    Ok(Value::Bool(result))
}

/// One scalar argument. A range or array is `#VALUE!`; an error is returned.
fn scalar_argument(expression: &Expr, env: &Environment<'_>) -> Result<Value, FormulaError> {
    match eval(expression, env)? {
        CalcValue::Scalar(value) | CalcValue::Referenced(value) => {
            propagate_value_error(&value)?;
            Ok(value)
        }
        CalcValue::Range(_) | CalcValue::Array(_) => Err(FormulaError::Value),
    }
}

/// One scalar argument coerced to a number with the shared coercer.
fn number_argument(expression: &Expr, env: &Environment<'_>) -> Result<f64, FormulaError> {
    to_number(&scalar_argument(expression, env)?)
}

/// Text that an aggregate reads as a number when it is typed directly as an
/// argument: an optional sign, digits with an optional decimal point, and an
/// optional exponent. Surrounding spaces, tabs and line breaks are ignored.
fn plain_number_text(text: &str) -> Option<f64> {
    let text = text.trim_matches([' ', '\t', '\r', '\n']);
    let bytes = text.as_bytes();
    let digits_from = |start: usize| {
        bytes[start..]
            .iter()
            .take_while(|byte| byte.is_ascii_digit())
            .count()
    };
    let mut index = usize::from(matches!(bytes.first(), Some(b'+' | b'-')));
    let whole = digits_from(index);
    index += whole;
    let mut fraction = 0;
    if bytes.get(index) == Some(&b'.') {
        index += 1;
        fraction = digits_from(index);
        index += fraction;
    }
    if whole == 0 && fraction == 0 {
        return None;
    }
    if matches!(bytes.get(index), Some(b'e' | b'E')) {
        index += 1;
        if matches!(bytes.get(index), Some(b'+' | b'-')) {
            index += 1;
        }
        let exponent = digits_from(index);
        if exponent == 0 {
            return None;
        }
        index += exponent;
    }
    if index != bytes.len() {
        return None;
    }
    text.parse::<f64>().ok().filter(|number| number.is_finite())
}

/// Append the numbers an aggregate reads from one argument.
///
/// Typed directly, numbers, logical values and plain numeric text count and
/// other text is `#VALUE!`. Inside a reference or array only numbers count.
/// An error value anywhere is returned, the first one in reading order.
fn collect_numbers(
    expression: &Expr,
    env: &Environment<'_>,
    numbers: &mut Vec<f64>,
) -> Result<(), FormulaError> {
    let evaluated = eval(expression, env)?;
    let referenced = keeps_reference(expression, &evaluated);
    for value in flatten(evaluated) {
        match value {
            Value::Error(error) => return Err(error),
            Value::Number(number) => numbers.push(number),
            Value::Bool(flag) if !referenced => numbers.push(if flag { 1.0 } else { 0.0 }),
            Value::Text(text) if !referenced => {
                numbers.push(plain_number_text(&text).ok_or(FormulaError::Value)?);
            }
            _ => {}
        }
    }
    Ok(())
}

/// The median of numbers already sorted in ascending order.
fn sorted_median(sorted: &[f64]) -> f64 {
    let middle = sorted.len() / 2;
    if sorted.len() % 2 == 1 {
        return sorted[middle];
    }
    let (low, high) = (sorted[middle - 1], sorted[middle]);
    let sum = low + high;
    if sum.is_finite() {
        sum / 2.0
    } else {
        low / 2.0 + high / 2.0
    }
}

/// The most frequent number; among equally frequent numbers, the one that
/// appears first. `None` when no number repeats.
fn first_mode(numbers: &[f64]) -> Option<f64> {
    let mut seen: HashMap<u64, (usize, usize)> = HashMap::new();
    for (index, number) in numbers.iter().enumerate() {
        // Negative zero and zero are one value.
        let key = if *number == 0.0 { 0.0_f64 } else { *number }.to_bits();
        seen.entry(key).or_insert((0, index)).0 += 1;
    }
    seen.values()
        .filter(|(count, _)| *count > 1)
        .max_by(|left, right| left.0.cmp(&right.0).then(right.1.cmp(&left.1)))
        .map(|(_, first)| numbers[*first])
}

/// PRODUCT, SUMSQ, MEDIAN, MODE.SNGL, STDEV.S, STDEV.P, VAR.S and VAR.P.
#[inline(never)]
pub(super) fn aggregate_call(
    name: &str,
    args: &[Expr],
    env: &Environment<'_>,
) -> Result<Value, FormulaError> {
    let max = if matches!(name, "PRODUCT" | "SUMSQ" | "MEDIAN") {
        255
    } else {
        254
    };
    arity(name, args, 1, max)?;
    let mut numbers = Vec::new();
    for expression in args {
        collect_numbers(expression, env, &mut numbers)?;
    }
    match name {
        "PRODUCT" => {
            if numbers.is_empty() {
                return Ok(Value::Number(0.0));
            }
            finite_number(numbers.iter().fold(1.0, |product, number| product * number))
        }
        "SUMSQ" => finite_number(
            numbers
                .iter()
                .fold(0.0, |total, number| total + number * number),
        ),
        "MEDIAN" => {
            if numbers.is_empty() {
                return Err(FormulaError::Num);
            }
            numbers.sort_by(f64::total_cmp);
            finite_number(sorted_median(&numbers))
        }
        "MODE.SNGL" => first_mode(&numbers)
            .ok_or(FormulaError::NA)
            .and_then(finite_number),
        _ => {
            let sample = matches!(name, "STDEV.S" | "VAR.S");
            let count = numbers.len();
            if count < if sample { 2 } else { 1 } {
                return Err(FormulaError::Div0);
            }
            let total = numbers.iter().fold(0.0, |total, number| total + number);
            let mean = total / count as f64;
            let squares = numbers.iter().fold(0.0, |total, number| {
                let deviation = number - mean;
                total + deviation * deviation
            });
            let divisor = if sample { count - 1 } else { count };
            let variance = squares / divisor as f64;
            finite_number(if name.starts_with("STDEV") {
                variance.sqrt()
            } else {
                variance
            })
        }
    }
}

/// LARGE and SMALL: the k-th largest or smallest number.
#[inline(never)]
pub(super) fn order_statistic_call(
    name: &str,
    args: &[Expr],
    env: &Environment<'_>,
) -> Result<Value, FormulaError> {
    arity(name, args, 2, 2)?;
    let mut numbers = Vec::new();
    collect_numbers(&args[0], env, &mut numbers)?;
    let position = number_argument(&args[1], env)?;
    if position.fract() != 0.0 || position < 1.0 || position > numbers.len() as f64 {
        return Err(FormulaError::Num);
    }
    numbers.sort_by(f64::total_cmp);
    let position = position as usize;
    let index = if name == "LARGE" {
        numbers.len() - position
    } else {
        position - 1
    };
    finite_number(numbers[index])
}

/// RANK.EQ and RANK: one more than the count of numbers ahead of `number`.
#[inline(never)]
pub(super) fn rank_call(
    name: &str,
    args: &[Expr],
    env: &Environment<'_>,
) -> Result<Value, FormulaError> {
    arity(name, args, 2, 3)?;
    let number = number_argument(&args[0], env)?;
    let evaluated = eval(&args[1], env)?;
    if !keeps_reference(&args[1], &evaluated) {
        return Err(FormulaError::Value);
    }
    let mut numbers = Vec::new();
    for value in flatten(evaluated) {
        match value {
            Value::Error(error) => return Err(error),
            Value::Number(listed) => numbers.push(listed),
            _ => {}
        }
    }
    let ascending = match args.get(2) {
        Some(expression) => number_argument(expression, env)? != 0.0,
        None => false,
    };
    if !numbers.contains(&number) {
        return Err(FormulaError::NA);
    }
    let ahead = numbers
        .iter()
        .filter(|listed| {
            if ascending {
                **listed < number
            } else {
                **listed > number
            }
        })
        .count();
    Ok(Value::Number((ahead + 1) as f64))
}

/// AVERAGEA: like AVERAGE, but logical values and text in a reference count.
#[inline(never)]
pub(super) fn averagea_call(args: &[Expr], env: &Environment<'_>) -> Result<Value, FormulaError> {
    arity("AVERAGEA", args, 1, 255)?;
    let mut total = 0.0;
    let mut count = 0_usize;
    for expression in args {
        let evaluated = eval(expression, env)?;
        let referenced = keeps_reference(expression, &evaluated);
        for value in flatten(evaluated) {
            total += match value {
                Value::Error(error) => return Err(error),
                Value::Blank => continue,
                Value::Number(number) => number,
                Value::Bool(flag) => {
                    if flag {
                        1.0
                    } else {
                        0.0
                    }
                }
                Value::Text(_) if referenced => 0.0,
                Value::Text(text) => plain_number_text(&text).ok_or(FormulaError::Value)?,
            };
            count += 1;
        }
    }
    if count == 0 {
        return Err(FormulaError::Div0);
    }
    finite_number(total / count as f64)
}

/// SUMPRODUCT: the sum of the products of corresponding entries.
#[inline(never)]
pub(super) fn sumproduct_call(args: &[Expr], env: &Environment<'_>) -> Result<Value, FormulaError> {
    arity("SUMPRODUCT", args, 1, 255)?;
    let mut arrays = Vec::with_capacity(args.len());
    for expression in args {
        arrays.push(calc_value_matrix(argument(expression, env)?));
    }
    let rows = arrays[0].len();
    let columns = arrays[0].first().map_or(0, Vec::len);
    if arrays
        .iter()
        .any(|array| array.len() != rows || array.iter().any(|row| row.len() != columns))
    {
        return Err(FormulaError::Value);
    }
    for value in arrays.iter().flatten().flatten() {
        propagate_value_error(value)?;
    }
    let mut total = 0.0;
    for row in 0..rows {
        for column in 0..columns {
            // An entry that is not a number counts as zero.
            total += arrays.iter().fold(1.0, |product, array| {
                product
                    * match array[row][column] {
                        Value::Number(number) => number,
                        _ => 0.0,
                    }
            });
        }
    }
    finite_number(total)
}
