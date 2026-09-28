"""Compose and calculate a business scenario without workbook or cell objects."""

from __future__ import annotations

import json

from workbook_forge import primitives as p


def scenario() -> dict:
    price = p.input("unit_price")
    cost = p.input("unit_cost")
    volumes = p.input("volumes")
    fixed = p.input("fixed_cost")
    quantity = p.call("SUM", volumes)
    revenue = quantity * price
    profit = quantity * (price - cost) - 3 * fixed
    inputs = {"unit_price": 20, "unit_cost": 8,
              "volumes": p.range_values([[100], [120], [150]]), "fixed_cost": 1000}

    def outputs() -> dict:
        return {
            "revenue": revenue.evaluate({name: inputs[name] for name in revenue.inspect()["inputs"]}),
            "profit": profit.evaluate(inputs),
        }

    before = outputs()
    inputs["unit_price"] = 25
    after = outputs()
    assert before == {"revenue": 7400, "profit": 1440}
    assert after == {"revenue": 9250, "profit": 3290}
    restored = p.Expression.from_dict(profit.to_dict())
    assert restored.evaluate(inputs) == after["profit"]
    return {"profile": "spreadsheet-primitives-v1", "before": before, "after": after,
            "profit": profit.inspect(), "revenue": revenue.inspect()}


if __name__ == "__main__":
    print(json.dumps(scenario(), indent=2, allow_nan=False))
