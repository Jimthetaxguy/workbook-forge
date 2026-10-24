//! Matched synthetic canonical-calculation workload, without intake or export.
//! Run: cargo run --release --locked --example benchmark_canonical
use std::hint::black_box;
use std::time::Instant;
use workbook_forge::model::{calculate, hydrate};

fn main() {
    for shape in ["chain", "fanout", "independent"] {
        for size in [100, 500, 1500] {
            let mut cells = serde_json::Map::new();
            for row in 1..=size {
                let expression = match shape {
                    "chain" if row > 1 => format!("=A{}+1", row - 1),
                    "fanout" if row > 1 => "=A1+1".to_string(),
                    _ => "=1".to_string(),
                };
                let address = format!("A{row}");
                cells.insert(
                    address.clone(),
                    serde_json::json!({
                        "address": address, "value": null, "data_type": "blank",
                        "formula": {"expression": expression, "dependencies": [], "result": null}
                    }),
                );
            }
            let bytes = serde_json::to_vec(&serde_json::json!({
                "schema_version": 1, "model_version": 1, "metadata": {},
                "bindings": {"inputs": {}, "outputs": {"last": {"sheet": "S", "address": format!("A{size}")}}},
                "diagnostics": [], "sheets": [{"name": "S", "cells": cells}]
            })).unwrap();
            let workbook = hydrate(&bytes).unwrap();
            let expected = match shape {
                "chain" => size,
                "fanout" => 2,
                _ => 1,
            };
            let result = calculate(&workbook).unwrap();
            assert!(result.diagnostics.is_empty());
            let actual = &result.sheet("S").unwrap().cells[&format!("A{size}")]
                .formula
                .as_ref()
                .unwrap()
                .result;
            assert_eq!(
                *actual,
                workbook_forge::model::Scalar::Number(expected.into())
            );
            let mut samples = Vec::new();
            for _ in 0..5 {
                let start = Instant::now();
                black_box(calculate(black_box(&workbook)).unwrap());
                samples.push(start.elapsed().as_secs_f64() * 1000.0);
            }
            samples.sort_by(f64::total_cmp);
            println!(
                "{shape},{size},median_ms={:.3},min_ms={:.3},max_ms={:.3}",
                samples[2], samples[0], samples[4]
            );
        }
    }
}
