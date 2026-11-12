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
        // A value typed directly is not a list. An error value is returned
        // before that check, as HLOOKUP does for its table.
        if let CalcValue::Scalar(value) = &evaluated {
            propagate_value_error(value)?;
        }
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

/// A text result within the 32,767-unit cell text limit.
fn bounded_text(text: String) -> Result<Value, FormulaError> {
    check_text_units(utf16_text_units(&text), MAX_TEXT_LENGTH_UNITS)?;
    Ok(Value::Text(text))
}

/// PROPER: a cased letter that follows another cased letter is lowercased;
/// every other cased letter is uppercased. Each character is mapped alone.
fn proper_case(text: &str) -> String {
    let mut output = String::with_capacity(text.len());
    let mut after_letter = false;
    for character in text.chars() {
        let letter = character.is_lowercase() || character.is_uppercase();
        if !letter {
            output.push(character);
        } else if after_letter {
            output.extend(character.to_lowercase());
        } else {
            output.extend(character.to_uppercase());
        }
        after_letter = letter;
    }
    output
}

/// Text VALUE reads as a number: an optional sign, an optional dollar sign,
/// digits that may be grouped in threes by commas, an optional decimal part,
/// an optional exponent and an optional trailing percent sign.
fn formatted_number_text(text: &str) -> Option<f64> {
    let text = text.trim_matches([' ', '\t', '\r', '\n']);
    let bytes = text.as_bytes();
    let digits_from = |start: usize| {
        bytes
            .get(start..)
            .unwrap_or_default()
            .iter()
            .take_while(|byte| byte.is_ascii_digit())
            .count()
    };
    let mut index = usize::from(matches!(bytes.first(), Some(b'+' | b'-')));
    if bytes.get(index) == Some(&b'$') {
        index += 1;
    }
    let whole = digits_from(index);
    index += whole;
    while bytes.get(index) == Some(&b',') {
        // A grouped number starts with one to three digits, and every comma
        // is followed by exactly three.
        if !(1..=3).contains(&whole) || digits_from(index + 1) != 3 {
            return None;
        }
        index += 4;
    }
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
    let percent = bytes.get(index) == Some(&b'%');
    if percent {
        index += 1;
    }
    if index != bytes.len() {
        return None;
    }
    let plain: String = text
        .chars()
        .filter(|character| !matches!(character, '$' | ',' | '%'))
        .collect();
    let number = plain.parse::<f64>().ok()?;
    let number = if percent { number / 100.0 } else { number };
    number.is_finite().then_some(number)
}

/// VALUE: a number is returned as it is, an empty cell is 0, and text is read
/// as a number or as one of the date and time forms DATEVALUE and TIMEVALUE
/// accept. Anything else is `#VALUE!`.
fn value_of(value: &Value) -> Result<Value, FormulaError> {
    match value {
        Value::Number(number) => finite_number(*number),
        Value::Blank => Ok(Value::Number(0.0)),
        Value::Bool(_) => Err(FormulaError::Value),
        Value::Error(error) => Err(error.clone()),
        Value::Text(text) => {
            if let Some(number) = formatted_number_text(text) {
                return finite_number(number);
            }
            let Value::Number(time) = date_time_text("TIMEVALUE", value)? else {
                return Err(FormulaError::Value);
            };
            // Text with a time and no date has no date serial to add.
            let date = match date_time_text("DATEVALUE", value) {
                Ok(Value::Number(serial)) => serial,
                _ => 0.0,
            };
            finite_number(date + time)
        }
    }
}

/// REPLACE: `count` characters of `old` from one-based `start` give way to
/// `new`. Positions count Unicode scalar values, as MID and LEN do here.
fn replace_text(old: &str, start: f64, count: f64, new: &str) -> Result<Value, FormulaError> {
    if start < 1.0 || count < 0.0 {
        return Err(FormulaError::Value);
    }
    let characters: Vec<char> = old.chars().collect();
    let length = characters.len();
    let begin = if start - 1.0 >= length as f64 {
        length
    } else {
        (start - 1.0) as usize
    };
    let end = if count >= (length - begin) as f64 {
        length
    } else {
        begin + count as usize
    };
    let mut output: String = characters[..begin].iter().collect();
    output.push_str(new);
    output.extend(&characters[end..]);
    bounded_text(output)
}

/// CONCATENATE, EXACT, PROPER, REPT, REPLACE, VALUE, CHAR and CODE.
#[inline(never)]
pub(super) fn text_call(
    name: &str,
    args: &[Expr],
    env: &Environment<'_>,
) -> Result<Value, FormulaError> {
    let (min, max) = match name {
        "CONCATENATE" => (1, 255),
        "EXACT" | "REPT" => (2, 2),
        "REPLACE" => (4, 4),
        _ => (1, 1),
    };
    arity(name, args, min, max)?;
    let values = scalar_arguments(args, env)?;
    match name {
        "CONCATENATE" => {
            let parts: Vec<&Value> = values.iter().collect();
            join_values_bounded(&parts, "", false, MAX_TEXT_LENGTH_UNITS).map(Value::Text)
        }
        "EXACT" => Ok(Value::Bool(to_text(&values[0])? == to_text(&values[1])?)),
        "PROPER" => bounded_text(proper_case(&to_text(&values[0])?)),
        "REPT" => {
            let text = to_text(&values[0])?;
            let times = to_number(&values[1])?.trunc();
            if times < 0.0 {
                return Err(FormulaError::Value);
            }
            if text.is_empty() {
                return Ok(Value::Text(text));
            }
            if utf16_text_units(&text) as f64 * times > MAX_TEXT_LENGTH_UNITS as f64 {
                return Err(FormulaError::Value);
            }
            Ok(Value::Text(text.repeat(times as usize)))
        }
        "REPLACE" => {
            let old = to_text(&values[0])?;
            let start = to_number(&values[1])?.trunc();
            let count = to_number(&values[2])?.trunc();
            let new = to_text(&values[3])?;
            replace_text(&old, start, count, &new)
        }
        "VALUE" => value_of(&values[0]),
        "CHAR" => {
            let code = to_number(&values[0])?.trunc();
            // Codes 128 to 255 depend on the computer's character set.
            if !(1.0..=127.0).contains(&code) {
                return Err(FormulaError::Value);
            }
            Ok(Value::Text(char::from(code as u8).to_string()))
        }
        _ => match to_text(&values[0])?.chars().next() {
            Some(first) if first.is_ascii() && first != '\0' => {
                Ok(Value::Number(f64::from(u32::from(first))))
            }
            _ => Err(FormulaError::Value),
        },
    }
}

/// CHOOSE: evaluate only the selected value and keep it a reference when it
/// is one, so `SUM(CHOOSE(2,A1:A3,B1:B3))` sums the second range.
#[inline(never)]
pub(super) fn choose_call(args: &[Expr], env: &Environment<'_>) -> Result<CalcValue, FormulaError> {
    arity("CHOOSE", args, 2, 255)?;
    let index = number_argument(&args[0], env)?.trunc();
    if index < 1.0 || index > (args.len() - 1) as f64 {
        return Err(FormulaError::Value);
    }
    let chosen = &args[index as usize];
    eval(chosen, env).map(|value| retain_reference(chosen, value))
}

/// ROWS and COLUMNS. A reference is measured from its addresses, so its cells
/// are not read and an error value in one of them does not matter. The
/// evaluator's limit on the size of a range still applies.
#[inline(never)]
pub(super) fn dimension_call(
    name: &str,
    args: &[Expr],
    env: &Environment<'_>,
) -> Result<Value, FormulaError> {
    arity(name, args, 1, 1)?;
    let (rows, columns) = match &args[0] {
        Expr::Ref(_, _) => (1, 1),
        Expr::Range(_, start, end) => {
            let (first_column, first_row) = decode_ref(start)?;
            let (last_column, last_row) = decode_ref(end)?;
            let rows = first_row.abs_diff(last_row) + 1;
            let columns = first_column.abs_diff(last_column) + 1;
            if rows.saturating_mul(columns) > 100_000 {
                return Err(FormulaError::Num);
            }
            (rows, columns)
        }
        expression => match eval(expression, env)? {
            CalcValue::Scalar(value) | CalcValue::Referenced(value) => {
                propagate_value_error(&value)?;
                (1, 1)
            }
            CalcValue::Range(rows) => (rows.len(), rows.first().map_or(0, Vec::len)),
            CalcValue::Array(array) => (array.rows.len(), array.rows.first().map_or(0, Vec::len)),
        },
    };
    let count = if name == "ROWS" { rows } else { columns };
    Ok(Value::Number(count as f64))
}

/// What a lookup compares: a number, text without case, or a logical value.
/// An empty cell and an error value have no key and never match.
#[derive(PartialEq, PartialOrd)]
enum LookupKey {
    Number(f64),
    Text(String),
    Logical(bool),
}

fn lookup_key(value: &Value) -> Option<LookupKey> {
    match value {
        Value::Number(number) => Some(LookupKey::Number(*number)),
        Value::Text(text) => Some(LookupKey::Text(
            text.chars().flat_map(char::to_lowercase).collect(),
        )),
        Value::Bool(flag) => Some(LookupKey::Logical(*flag)),
        Value::Blank | Value::Error(_) => None,
    }
}

/// The order of two keys of the same type; keys of different types have none.
fn key_order(left: &LookupKey, right: &LookupKey) -> Option<std::cmp::Ordering> {
    if std::mem::discriminant(left) == std::mem::discriminant(right) {
        left.partial_cmp(right)
    } else {
        None
    }
}

/// The position of the largest key of the lookup's type that is not above
/// the lookup; among equal keys, the last one.
fn approximate_position(keys: &[Value], lookup: &Value) -> Option<usize> {
    use std::cmp::Ordering::{Greater, Less};
    let lookup = lookup_key(lookup)?;
    let mut best: Option<(usize, LookupKey)> = None;
    for (index, value) in keys.iter().enumerate() {
        let Some(key) = lookup_key(value) else {
            continue;
        };
        if matches!(key_order(&key, &lookup), None | Some(Greater)) {
            continue;
        }
        if best
            .as_ref()
            .is_none_or(|(_, held)| key_order(&key, held) != Some(Less))
        {
            best = Some((index, key));
        }
    }
    best.map(|(index, _)| index)
}

/// The position of the first key equal to the lookup. A text lookup that
/// holds `?`, `*` or `~` is a wildcard pattern over text keys.
fn exact_position(
    keys: &[Value],
    lookup: &Value,
    env: &Environment<'_>,
) -> Result<Option<usize>, FormulaError> {
    let Some(lookup_key_value) = lookup_key(lookup) else {
        return Ok(None);
    };
    let pattern = match lookup {
        Value::Text(text) if text.chars().any(|ch| matches!(ch, '*' | '?' | '~')) => {
            Some(WildcardPattern::new(text)?)
        }
        _ => None,
    };
    for (index, value) in keys.iter().enumerate() {
        let matched = match (&pattern, value) {
            (Some(pattern), Value::Text(text)) => {
                wildcard_match(pattern, text, &env.wildcard_work_remaining)?
            }
            (Some(_), _) => false,
            (None, _) => lookup_key(value).is_some_and(|key| {
                key_order(&key, &lookup_key_value) == Some(std::cmp::Ordering::Equal)
            }),
        };
        if matched {
            return Ok(Some(index));
        }
    }
    Ok(None)
}

/// HLOOKUP: find a value in the first row of a table and return the value in
/// the same column from the requested row.
#[inline(never)]
pub(super) fn hlookup_call(args: &[Expr], env: &Environment<'_>) -> Result<Value, FormulaError> {
    arity("HLOOKUP", args, 3, 4)?;
    let lookup = scalar_argument(&args[0], env)?;
    // The table is a range, a computed array, or one cell as a one-by-one
    // table. A value typed directly is not a table.
    let table = match eval(&args[1], env)? {
        CalcValue::Range(rows) => rows,
        CalcValue::Array(array) => array.rows,
        CalcValue::Referenced(value) => vec![vec![value]],
        CalcValue::Scalar(value) if matches!(args[1], Expr::Ref(_, _)) => vec![vec![value]],
        CalcValue::Scalar(value) => {
            propagate_value_error(&value)?;
            return Err(FormulaError::Value);
        }
    };
    let row = number_argument(&args[2], env)?.trunc();
    if row < 1.0 {
        return Err(FormulaError::Value);
    }
    if row > table.len() as f64 {
        return Err(FormulaError::Ref);
    }
    let approximate = match args.get(3) {
        Some(expression) => truthy(&scalar_argument(expression, env)?)?,
        None => true,
    };
    let keys = table.first().ok_or(FormulaError::NA)?;
    let found = if approximate {
        approximate_position(keys, &lookup)
    } else {
        exact_position(keys, &lookup, env)?
    };
    let column = found.ok_or(FormulaError::NA)?;
    Ok(table[row as usize - 1][column].clone())
}

/// Every entry of an XNPV or XIRR series, which must all be numbers.
fn numeric_series(expression: &Expr, env: &Environment<'_>) -> Result<Vec<f64>, FormulaError> {
    let mut numbers = Vec::new();
    for value in flatten(eval(expression, env)?) {
        match value {
            Value::Error(error) => return Err(error),
            Value::Number(number) if number.is_finite() => numbers.push(number),
            Value::Number(_) => return Err(FormulaError::Num),
            _ => return Err(FormulaError::Value),
        }
    }
    Ok(numbers)
}

/// The numbers a periodic cash-flow function reads from one argument, by the
/// aggregate rule: typed directly, numbers, logical values and plain numeric
/// text count; inside a reference or array only numbers count.
fn cash_flows(expression: &Expr, env: &Environment<'_>) -> Result<Vec<f64>, FormulaError> {
    let mut numbers = Vec::new();
    collect_numbers(expression, env, &mut numbers)?;
    if numbers.iter().any(|number| !number.is_finite()) {
        return Err(FormulaError::Num);
    }
    Ok(numbers)
}

/// Whether the flows hold at least one positive and one negative number.
fn has_both_signs(flows: &[f64]) -> bool {
    flows.iter().any(|flow| *flow > 0.0) && flows.iter().any(|flow| *flow < 0.0)
}

/// The time of each dated flow in 365-day years after the first date.
///
/// Dates are truncated to whole days. A date outside the serial range is
/// `#VALUE!`; a different number of values and dates, or a date before the
/// first one, is `#NUM!`.
fn schedule_years(values: &[f64], dates: &[f64]) -> Result<Vec<f64>, FormulaError> {
    let mut days = Vec::with_capacity(dates.len());
    for date in dates {
        let day = date.trunc();
        if !(0.0..=MAX_EXCEL_DATE_SERIAL as f64).contains(&day) {
            return Err(FormulaError::Value);
        }
        days.push(day);
    }
    if values.len() != days.len() {
        return Err(FormulaError::Num);
    }
    let first = days[0];
    if days.iter().any(|day| *day < first) {
        return Err(FormulaError::Num);
    }
    Ok(days.iter().map(|day| (day - first) / 365.0).collect())
}

/// The next Newton estimate of a rate from the equation's value and slope.
fn newton_step(rate: f64, value: f64, slope: f64) -> Result<f64, FormulaError> {
    if !value.is_finite() || !slope.is_finite() || slope == 0.0 {
        return Err(FormulaError::Num);
    }
    let next = rate - value / slope;
    if next.is_finite() {
        Ok(next)
    } else {
        Err(FormulaError::Num)
    }
}

/// Newton step for IRR: the flows are one period apart and the first is now.
fn periodic_rate_step(rate: f64, flows: &[f64]) -> Result<f64, FormulaError> {
    let base = 1.0 + rate;
    let mut discount = 1.0;
    let mut value = 0.0;
    let mut slope = 0.0;
    for (index, flow) in flows.iter().enumerate() {
        if discount == 0.0 {
            return Err(FormulaError::Num);
        }
        let term = flow / discount;
        value += term;
        slope -= index as f64 * term / base;
        discount *= base;
    }
    newton_step(rate, value, slope)
}

/// Newton step for XIRR: each flow is discounted over its 365-day years.
fn dated_rate_step(rate: f64, flows: &[f64], years: &[f64]) -> Result<f64, FormulaError> {
    let base = 1.0 + rate;
    let mut value = 0.0;
    let mut slope = 0.0;
    for (flow, year) in flows.iter().zip(years) {
        let discount = base.powf(*year);
        if discount == 0.0 {
            return Err(FormulaError::Num);
        }
        let term = flow / discount;
        value += term;
        slope -= year * term / base;
    }
    newton_step(rate, value, slope)
}

/// Newton step for RATE on
/// `pv*(1+r)^n + pmt*(1+r*type)*((1+r)^n-1)/r + fv = 0`.
fn annuity_rate_step(rate: f64, terms: &[f64; 5]) -> Result<f64, FormulaError> {
    let [nper, payment, present, future, due] = *terms;
    if rate == 0.0 {
        let value = present + payment * nper + future;
        let slope = present * nper + payment * (due * nper + nper * (nper - 1.0) / 2.0);
        return newton_step(rate, value, slope);
    }
    let exponent = nper * rate.ln_1p();
    let growth = exponent.exp();
    let growth_slope = nper * growth / (1.0 + rate);
    let annuity = exponent.exp_m1() / rate;
    let annuity_slope = (growth_slope - annuity) / rate;
    let timing = 1.0 + rate * due;
    let value = present * growth + payment * timing * annuity + future;
    let slope = present * growth_slope + payment * (due * annuity + timing * annuity_slope);
    newton_step(rate, value, slope)
}

/// Newton's method on the rate, starting from `guess`.
///
/// The rate is returned when a Newton step moves it by no more than
/// `tolerance`. A step that would put the rate at or below -100 percent moves
/// it halfway to -100 percent instead, and counts as a try. `tries` tries
/// without a result is `#NUM!`, as is a guess at or below -100 percent.
fn solve_rate(
    guess: f64,
    tolerance: f64,
    tries: usize,
    step: impl Fn(f64) -> Result<f64, FormulaError>,
) -> Result<Value, FormulaError> {
    let mut rate = guess;
    for _ in 0..tries {
        if rate <= -1.0 {
            return Err(FormulaError::Num);
        }
        let next = step(rate)?;
        if next <= -1.0 {
            rate = (rate - 1.0) / 2.0;
        } else if (next - rate).abs() <= tolerance {
            return finite_number(next);
        } else {
            rate = next;
        }
    }
    Err(FormulaError::Num)
}

/// An optional guess: 0.1 when the argument is left out.
fn rate_guess(expression: Option<&Expr>, env: &Environment<'_>) -> Result<f64, FormulaError> {
    match expression {
        Some(expression) => {
            let guess = number_argument(expression, env)?;
            if guess.is_finite() {
                Ok(guess)
            } else {
                Err(FormulaError::Num)
            }
        }
        None => Ok(0.1),
    }
}

/// One finite scalar rate argument.
fn rate_argument(expression: &Expr, env: &Environment<'_>) -> Result<f64, FormulaError> {
    let rate = number_argument(expression, env)?;
    if rate.is_finite() {
        Ok(rate)
    } else {
        Err(FormulaError::Num)
    }
}

/// The six RATE arguments as numbers: nper, pmt, pv, fv, type and guess.
fn rate_terms(args: &[Expr], env: &Environment<'_>) -> Result<[f64; 6], FormulaError> {
    let future_given = args
        .get(3)
        .is_some_and(|future| !matches!(future, Expr::Missing));
    let mut values = Vec::with_capacity(6);
    let mut has_range = false;
    for (index, expression) in args.iter().enumerate() {
        // An empty fv or type counts as zero, and so does an empty pmt when
        // fv is given. Any other empty argument is `#VALUE!`.
        let may_be_empty = matches!(index, 3 | 4) || (index == 1 && future_given);
        if may_be_empty && matches!(expression, Expr::Missing) {
            values.push(Value::Number(0.0));
            continue;
        }
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
    let mut terms = [0.0, 0.0, 0.0, 0.0, 0.0, 0.1];
    for (term, value) in terms.iter_mut().zip(&values) {
        *term = to_number(value)?;
        if !term.is_finite() {
            return Err(FormulaError::Num);
        }
    }
    Ok(terms)
}

/// NPV, IRR, XNPV, XIRR, RATE and MIRR.
#[inline(never)]
pub(super) fn cash_flow_call(
    name: &str,
    args: &[Expr],
    env: &Environment<'_>,
) -> Result<Value, FormulaError> {
    match name {
        "NPV" => {
            arity(name, args, 2, 255)?;
            let rate = rate_argument(&args[0], env)?;
            let mut flows = Vec::new();
            for expression in &args[1..] {
                flows.extend(cash_flows(expression, env)?);
            }
            let base = 1.0 + rate;
            if base == 0.0 {
                return Err(FormulaError::Div0);
            }
            let mut discount = 1.0;
            let mut total = 0.0;
            for flow in &flows {
                discount *= base;
                if discount == 0.0 {
                    return Err(FormulaError::Num);
                }
                total += flow / discount;
            }
            finite_number(total)
        }
        "IRR" => {
            arity(name, args, 1, 2)?;
            let flows = cash_flows(&args[0], env)?;
            let guess = rate_guess(args.get(1), env)?;
            if !has_both_signs(&flows) {
                return Err(FormulaError::Num);
            }
            solve_rate(guess, 1e-7, 20, |rate| periodic_rate_step(rate, &flows))
        }
        "XNPV" => {
            arity(name, args, 3, 3)?;
            let rate = rate_argument(&args[0], env)?;
            let flows = numeric_series(&args[1], env)?;
            let dates = numeric_series(&args[2], env)?;
            let years = schedule_years(&flows, &dates)?;
            if rate <= -1.0 {
                return Err(FormulaError::Num);
            }
            let base = 1.0 + rate;
            let mut total = 0.0;
            for (flow, year) in flows.iter().zip(&years) {
                let discount = base.powf(*year);
                if discount == 0.0 {
                    return Err(FormulaError::Num);
                }
                total += flow / discount;
            }
            finite_number(total)
        }
        "XIRR" => {
            arity(name, args, 2, 3)?;
            let flows = numeric_series(&args[0], env)?;
            let dates = numeric_series(&args[1], env)?;
            let guess = rate_guess(args.get(2), env)?;
            let years = schedule_years(&flows, &dates)?;
            if !has_both_signs(&flows) {
                return Err(FormulaError::Num);
            }
            solve_rate(guess, 1e-8, 100, |rate| {
                dated_rate_step(rate, &flows, &years)
            })
        }
        "RATE" => {
            arity(name, args, 3, 6)?;
            let [nper, payment, present, future, due, guess] = rate_terms(args, env)?;
            if nper <= 0.0 || (due != 0.0 && due != 1.0) {
                return Err(FormulaError::Num);
            }
            let terms = [nper, payment, present, future, due];
            // The annuity equation can hold at -100 percent itself, which is
            // not a rate RATE returns. A search that settles within the
            // accuracy of that boundary has found it, not a rate above it.
            match solve_rate(guess, 1e-7, 20, |rate| annuity_rate_step(rate, &terms))? {
                Value::Number(rate) if rate + 1.0 <= 1e-7 => Err(FormulaError::Num),
                value => Ok(value),
            }
        }
        _ => {
            arity(name, args, 3, 3)?;
            let flows = cash_flows(&args[0], env)?;
            let finance = rate_argument(&args[1], env)?;
            let reinvest = rate_argument(&args[2], env)?;
            if finance <= -1.0 || reinvest <= -1.0 {
                return Err(FormulaError::Num);
            }
            if !has_both_signs(&flows) {
                return Err(FormulaError::Div0);
            }
            let finance_base = 1.0 + finance;
            let reinvest_base = 1.0 + reinvest;
            let mut discount = 1.0;
            let mut cost = 0.0;
            let mut income = 0.0;
            for flow in &flows {
                income = income * reinvest_base + flow.max(0.0);
                if *flow < 0.0 {
                    if discount == 0.0 {
                        return Err(FormulaError::Num);
                    }
                    cost += flow / discount;
                }
                discount *= finance_base;
            }
            if cost == 0.0 || !cost.is_finite() || !income.is_finite() {
                return Err(FormulaError::Num);
            }
            let periods = (flows.len() - 1) as f64;
            finite_number((-income / cost).powf(1.0 / periods) - 1.0)
        }
    }
}
