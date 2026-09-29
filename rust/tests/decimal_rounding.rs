//! ROUND, ROUNDUP, ROUNDDOWN and TRUNC on numbers as they are written.
//!
//! A two-decimal amount such as 19.99 has no exact binary form. Rounding the
//! stored value exactly made TRUNC(19.99,2) return 19.98 and moved 573 of the
//! 9,999 amounts from 0.01 to 99.99 by a cent.
//!
//! No expected value here comes from the evaluator. Each is built from whole
//! cents with integer arithmetic and written out as decimal text.
use std::collections::HashMap;

use workbook_forge::{Value, evaluate};

/// Whole cents as decimal text, with optional further digits.
fn amount(cents: u32, extra: &str) -> String {
    format!("{}.{:02}{extra}", cents / 100, cents % 100)
}

fn moved(function: &str, places: &str, extra: &str, step: u32) -> Vec<String> {
    let cells = HashMap::new();
    let mut moved = Vec::new();
    for sign in ["", "-"] {
        for cents in 1..10_000 {
            let written = format!("{sign}{}", amount(cents, extra));
            let expected: f64 = format!("{sign}{}", amount(cents + step, ""))
                .parse()
                .unwrap();
            let formula = format!("={function}({written}{places})");
            if evaluate(&formula, &cells, "Sheet1") != Ok(Value::Number(expected)) {
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

#[test]
fn whole_units_are_kept_by_every_function_at_no_decimals() {
    let cells = HashMap::new();
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
            ] {
                assert_eq!(
                    evaluate(&formula, &cells, "Sheet1"),
                    Ok(Value::Number(expected)),
                    "{formula}"
                );
            }
        }
    }
}
