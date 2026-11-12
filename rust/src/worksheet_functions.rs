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
