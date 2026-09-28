//! Compose an operating scenario without a workbook or formula strings.
use std::collections::BTreeMap;
use workbook_forge::Value;
use workbook_forge::primitives::{Expression, PrimitiveValue, range_values, result_json};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let volume = Expression::call("SUM", vec![Expression::input("volumes")?])?;
    let revenue = Expression::binary("*", volume.clone(), Expression::input("unit_price")?)?;
    let contribution = Expression::binary(
        "-",
        Expression::input("unit_price")?,
        Expression::input("unit_cost")?,
    )?;
    let costs = Expression::binary(
        "*",
        Expression::literal(3.0)?,
        Expression::input("fixed_cost")?,
    )?;
    let profit = Expression::binary("-", Expression::binary("*", volume, contribution)?, costs)?;
    let mut inputs = BTreeMap::from([
        ("unit_price".into(), PrimitiveValue::from(20.0)),
        ("unit_cost".into(), PrimitiveValue::from(8.0)),
        ("fixed_cost".into(), PrimitiveValue::from(1000.0)),
        (
            "volumes".into(),
            range_values(vec![
                vec![Value::Number(100.0)],
                vec![Value::Number(120.0)],
                vec![Value::Number(150.0)],
            ])?,
        ),
    ]);
    let mut runs = Vec::new();
    for price in [20.0, 25.0] {
        inputs.insert("unit_price".into(), PrimitiveValue::from(price));
        let revenue_inputs = BTreeMap::from([
            ("unit_price".into(), inputs["unit_price"].clone()),
            ("volumes".into(), inputs["volumes"].clone()),
        ]);
        runs.push(serde_json::json!({"unit_price":price,"revenue":result_json(&revenue.evaluate(&revenue_inputs)?)?,"profit":result_json(&profit.evaluate(&inputs)?)?}));
    }
    println!(
        "{}",
        serde_json::json!({"runs":runs,"revenue":revenue.inspect()?,"profit":profit.inspect()?})
    );
    Ok(())
}
