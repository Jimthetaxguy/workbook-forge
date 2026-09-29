//! The rounding functions on numbers as they are written.
//!
//! A two-decimal amount such as 19.99 has no exact binary form. Rounding the
//! stored value exactly made TRUNC(19.99,2) return 19.98 and moved 573 of the
//! 9,999 amounts from 0.01 to 99.99 by a cent.
//!
//! No expected value here comes from the evaluator. Each is built with
//! integer arithmetic and written out as decimal text.
use std::collections::HashMap;

use workbook_forge::{Value, evaluate};

fn result(formula: &str) -> Result<Value, workbook_forge::FormulaError> {
    evaluate(formula, &HashMap::new(), "Sheet1")
}

fn number(formula: &str) -> f64 {
    match result(formula) {
        Ok(Value::Number(number)) => number,
        other => panic!("{formula} gave {other:?}"),
    }
}

/// `units` with `decimals` digits after the point, as decimal text.
fn written(units: u128, decimals: u32) -> String {
    if decimals == 0 {
        return units.to_string();
    }
    let scale = 10u128.pow(decimals);
    format!(
        "{}.{:0width$}",
        units / scale,
        units % scale,
        width = decimals as usize
    )
}

/// Whole cents as decimal text, with optional further digits.
fn amount(cents: u32, extra: &str) -> String {
    format!("{}{extra}", written(u128::from(cents), 2))
}

fn moved(function: &str, places: &str, extra: &str, step: u32) -> Vec<String> {
    let mut moved = Vec::new();
    for sign in ["", "-"] {
        for cents in 1..10_000 {
            let text = format!("{sign}{}", amount(cents, extra));
            let expected: f64 = format!("{sign}{}", amount(cents + step, ""))
                .parse()
                .unwrap();
            let formula = format!("={function}({text}{places})");
            if result(&formula) != Ok(Value::Number(expected)) {
                moved.push(formula);
            }
        }
    }
    moved
}

#[test]
fn a_two_decimal_amount_rounded_to_two_decimals_is_itself() {
    for function in ["ROUND", "ROUNDUP", "ROUNDDOWN", "TRUNC"] {
        assert_eq!(moved(function, ",2", "", 0), Vec::<String>::new());
    }
}

#[test]
fn half_a_cent_goes_away_from_zero() {
    assert_eq!(moved("ROUND", ",2", "5", 1), Vec::<String>::new());
    assert_eq!(moved("ROUND", ",2", "4999", 0), Vec::<String>::new());
}

#[test]
fn anything_past_the_cent_takes_roundup_to_the_next_cent() {
    assert_eq!(moved("ROUNDUP", ",2", "001", 1), Vec::<String>::new());
}

#[test]
fn nothing_short_of_the_next_cent_reaches_it() {
    assert_eq!(moved("ROUNDDOWN", ",2", "999", 0), Vec::<String>::new());
    assert_eq!(moved("TRUNC", ",2", "999", 0), Vec::<String>::new());
}

/// A small generator with a fixed start, so every run tests the same numbers.
struct Numbers(u64);

impl Numbers {
    fn next(&mut self, below: u64) -> u64 {
        self.0 = self
            .0
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        (self.0 >> 33) % below
    }
}

#[test]
fn a_number_of_up_to_15_digits_rounds_as_integer_arithmetic_says() {
    let mut numbers = Numbers(20_260_929);
    let mut wrong = Vec::new();
    for _ in 0..20_000 {
        // Up to 15 digits in all, of which `decimals` are after the point.
        let length = 1 + numbers.next(15) as u32;
        let units = u128::from(numbers.next(9) + 1) * 10u128.pow(length - 1)
            + u128::from(numbers.next(u64::MAX >> 1)) % 10u128.pow(length - 1);
        let decimals = numbers.next(u64::from(length) + 1) as u32;
        let places = numbers.next(u64::from(decimals) + 1) as u32;
        let sign = if numbers.next(5) < 2 { "-" } else { "" };

        let scale = 10u128.pow(decimals - places);
        let (kept, rest) = (units / scale, units % scale);
        for (function, up) in [
            ("ROUND", rest * 2 >= scale),
            ("ROUNDUP", rest > 0),
            ("ROUNDDOWN", false),
            ("TRUNC", false),
        ] {
            let expected: f64 = format!("{sign}{}", written(kept + u128::from(up), places))
                .parse()
                .unwrap();
            let formula = format!("={function}({sign}{},{places})", written(units, decimals));
            let actual = number(&formula);
            if actual != expected {
                wrong.push((formula, actual, expected));
            }
        }
    }
    assert_eq!(wrong, Vec::new());
}

#[test]
fn the_whole_number_functions_round_as_integer_arithmetic_says() {
    let mut wrong = Vec::new();
    for cents in 1..10_000i64 {
        // 19.99*100 is stored as 1998.9999999999998. The amount in cents is 1999.
        for sign in [1i64, -1] {
            let product = format!(
                "{}{}*100",
                if sign < 0 { "-" } else { "" },
                amount(cents as u32, "")
            );
            for (formula, expected) in [
                (format!("=INT({product})"), sign * cents),
                (format!("=QUOTIENT({product},1)"), sign * cents),
                (format!("=TRUNC({product})"), sign * cents),
                (format!("=EVEN({product})"), sign * (cents + cents % 2)),
                (format!("=ODD({product})"), sign * (cents + 1 - cents % 2)),
            ] {
                let actual = number(&formula);
                if actual != expected as f64 {
                    wrong.push((formula, actual, expected));
                }
            }
        }
    }
    assert_eq!(wrong, Vec::new());
}

#[test]
fn a_number_that_is_not_next_to_a_boundary_is_rounded_exactly() {
    for (formula, expected) in [
        // More than two binary64 values from the boundary.
        ("=ROUNDUP(0.0700000000000001,2)", 0.08),
        ("=ROUNDDOWN(19.9899999999999,2)", 19.98),
        ("=TRUNC(19.9899999999999,2)", 19.98),
        ("=ROUND(1.00499999999999,2)", 1.0),
        ("=INT(434.999999999999)", 434.0),
        ("=ROUND(123456789012.3449,2)", 123456789012.34),
        // A whole number below 2^53 is stored exactly.
        ("=ROUND(1000000000000049,-2)", 1000000000000000.0),
        ("=ROUNDUP(1000000000000004,-2)", 1000000000000100.0),
        ("=ROUNDDOWN(1000000000000096,-2)", 1000000000000000.0),
        ("=ROUNDUP(3447688120018501,-2)", 3447688120018600.0),
        ("=ROUND(4910111281310649,-2)", 4910111281310600.0),
        ("=ROUND(4910111281310650,-2)", 4910111281310700.0),
    ] {
        assert_eq!(number(formula), expected, "{formula}");
    }
}

#[test]
fn asking_for_more_places_than_a_number_has_gives_it_back() {
    for text in ["19.99", "6333.47", "0.07", "764.878", "-4557.25787"] {
        let expected: f64 = text.parse().unwrap();
        for places in 5..17 {
            for function in ["ROUND", "ROUNDUP", "ROUNDDOWN", "TRUNC"] {
                let formula = format!("={function}({text},{places})");
                assert_eq!(number(&formula), expected, "{formula}");
            }
        }
    }
}

#[test]
fn a_result_of_zero_has_no_sign() {
    for formula in [
        "=TRUNC(-0.5)",
        "=ROUNDDOWN(-0.004,2)",
        "=ROUND(-0.001,2)",
        "=ROUND(-0,2)",
        "=INT(0.4)",
    ] {
        let zero = number(formula);
        assert!(zero == 0.0 && zero.is_sign_positive(), "{formula}");
    }
}

#[test]
fn nothing_but_zero_is_taken_as_zero() {
    assert_eq!(number("=ROUNDUP(5E-324,308)"), 1e-308);
    assert_eq!(number("=ROUNDUP(1E-320,2)"), 0.01);
    assert_eq!(number("=ROUNDDOWN(5E-324,308)"), 0.0);
}

#[test]
fn whole_units_are_kept_by_every_function_at_no_decimals() {
    for units in [
        "1",
        "7",
        "99",
        "100",
        "123456789",
        "999999999999999",
        "4503599627370497",
        "9007199254740992",
    ] {
        for sign in ["", "-"] {
            let expected: f64 = format!("{sign}{units}").parse().unwrap();
            for formula in [
                format!("=ROUND({sign}{units},0)"),
                format!("=ROUNDUP({sign}{units},0)"),
                format!("=ROUNDDOWN({sign}{units},0)"),
                format!("=TRUNC({sign}{units})"),
                format!("=INT({sign}{units})"),
            ] {
                assert_eq!(number(&formula), expected, "{formula}");
            }
        }
    }
}

#[test]
fn a_number_in_a_document_is_read_as_it_was_written() {
    // Without exact reading these came back one or two binary64 values off.
    for text in [
        "999999999999999.9",
        "249658.74592800002",
        "931.7813040373925",
        "0.1",
        "1e-7",
    ] {
        let expected: f64 = text.parse().unwrap();
        let read: f64 = serde_json::from_str(text).unwrap();
        assert_eq!(read.to_bits(), expected.to_bits(), "{text}");
    }
}

/// The binary64 value `steps` values above a positive `value`, or below when negative.
fn moved_by(value: f64, steps: i64) -> f64 {
    f64::from_bits(value.to_bits().wrapping_add_signed(steps))
}

#[test]
fn two_values_from_a_multiple_is_on_it_and_three_is_not() {
    for (boundary, below, above, places) in [
        ("19.99", "19.98", "20.00", 2),
        ("0.07", "0.06", "0.08", 2),
        ("435", "434", "436", 0),
        (
            "1000000000000.07",
            "1000000000000.06",
            "1000000000000.08",
            2,
        ),
        ("0.3", "0.2", "0.4", 1),
    ] {
        let on_it: f64 = boundary.parse().unwrap();
        let (below, above): (f64, f64) = (below.parse().unwrap(), above.parse().unwrap());
        for steps in -3..=3i64 {
            let stored = moved_by(on_it, steps);
            let down = number(&format!("=ROUNDDOWN({stored:e},{places})"));
            let up = number(&format!("=ROUNDUP({stored:e},{places})"));
            let expected = if steps.abs() <= 2 {
                (on_it, on_it)
            } else if steps < 0 {
                (below, on_it)
            } else {
                (on_it, above)
            };
            assert_eq!((down, up), expected, "{boundary} moved by {steps}");
        }
    }
}

#[test]
fn two_values_from_a_half_is_on_it_and_three_is_not() {
    for (half, below, above, places) in [
        ("1.005", "1.00", "1.01", 2),
        ("0.285", "0.28", "0.29", 2),
        ("2.675", "2.67", "2.68", 2),
        ("1234.5675", "1234.567", "1234.568", 3),
    ] {
        let on_it: f64 = half.parse().unwrap();
        for steps in -3..=3i64 {
            let stored = moved_by(on_it, steps);
            let expected: f64 = if steps < -2 { below } else { above }.parse().unwrap();
            assert_eq!(
                number(&format!("=ROUND({stored:e},{places})")),
                expected,
                "{half} moved by {steps}"
            );
        }
    }
}
