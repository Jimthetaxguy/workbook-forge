from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError

import pytest

import workbook_forge as legacy
from workbook_forge import ArrayValue, ErrorValue, primitives as p


def error_code(value):
    assert isinstance(value, ErrorValue)
    return value.code


def test_direct_functions_use_existing_numeric_semantics():
    assert p.sum(1, 2, 3) == 6
    assert p.average(2, 4) == 3
    assert p.min(2, -4, 10) == -4
    assert p.max(2, -4, 10) == 10
    assert p.count(1, None, "2", True, 3) == 2
    assert p.if_(False, 10) is False
    assert p.if_(True, 10, ErrorValue("#DIV/0!")) == 10
    assert p.iferror(ErrorValue("#DIV/0!"), 7) == 7
    assert p.round(2.5, 0) == 3
    assert p.round(-2.5, 0) == -3
    assert p.abs(-8) == 8
    assert error_code(p.average(None)) == "#DIV/0!"


def test_range_array_and_scalar_profiles_are_explicit():
    cells = [[True, "2", None, 3]]
    values = p.range_values(cells)
    cells[0][-1] = 99
    assert values.rows == ((True, "2", None, 3),)
    with pytest.raises(FrozenInstanceError):
        values.rows = ((0,),)
    # This is the inherited Forge aggregate profile, not an Excel claim.
    assert p.sum(True, "2") == 0
    assert p.sum(values) == p.sum(ArrayValue(values.rows)) == 3
    assert p.count(values) == 1
    assert error_code(p.round(values, 0)) == "#VALUE!"
    assert p.literal(values).to_dict()["expression"]["value"] == {"range": [[True, "2", None, 3]]}
    assert p.literal(ArrayValue(values.rows)).to_dict()["expression"]["value"] == {"array": [[True, "2", None, 3]]}
    assert isinstance(p.literal(values).evaluate(), ArrayValue)
    assert p.literal(values).evaluate().rows == values.rows


def test_named_composition_roundtrip_and_operation_identity():
    units, price, cost, fixed = (p.input(name) for name in ("volumes", "unit_price", "unit_cost", "fixed_cost"))
    profit = p.call("sum", units) * (price - cost) - 3 * fixed
    values = {"volumes": p.range_values([[100], [120], [150]]), "unit_price": 20,
              "unit_cost": 8, "fixed_cost": 1000}
    assert profit.evaluate(values) == 1440
    values["unit_price"] = 25
    assert profit.evaluate(values) == 3290
    document = profit.to_dict()
    restored = p.Expression.from_dict(document)
    assert restored.evaluate(values) == 3290
    assert restored.inspect() == profit.inspect()
    assert profit.inspect()["operations"] == ["excel.SUM", "excel.operator.multiply", "excel.operator.subtract"]
    assert profit.inspect()["inputs"] == sorted(values)
    document["expression"]["operator"] = "+"
    assert profit.evaluate(values) == 3290


def test_lazy_branches_reuse_evaluator_but_inputs_are_required():
    divide = p.literal(1) / 0
    assert p.call("IF", True, 12, divide).evaluate() == 12
    assert p.call("IFERROR", 12, divide).evaluate() == 12
    assert p.call("IFERROR", divide, 7).evaluate() == 7
    branch = p.call("IF", True, 12, p.input("unused"))
    with pytest.raises(p.PrimitiveError) as error:
        branch.evaluate()
    assert error.value.code == "invalid_input"
    assert branch.evaluate({"unused": ErrorValue("#VALUE!")}) == 12


def test_blank_and_omitted_have_distinct_meanings():
    assert p.literal(None).evaluate() is None
    assert p.sum(None, 2) == 2
    assert error_code(p.sum(p.MISSING, 2)) == "#VALUE!"
    assert p.if_(False, p.MISSING, 3) == 3
    omitted = p.literal(p.MISSING)
    for expression in (omitted, omitted + 1):
        with pytest.raises(p.PrimitiveError) as error:
            expression.evaluate()
        assert error.value.code == "invalid_expression"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), [], {}, [[1]], object(), ErrorValue("#SPILL!"), "\ud800"])
def test_invalid_values_fail_without_mutating_expression_or_bindings(value):
    expression = p.input("amount") + 1
    before = expression.to_dict()
    bindings = {"amount": value}
    with pytest.raises(p.PrimitiveError) as error:
        expression.evaluate(bindings)
    assert error.value.code == "invalid_input"
    assert expression.to_dict() == before
    assert bindings["amount"] is value
    assert expression.evaluate({"amount": 5}) == 6


@pytest.mark.parametrize("rows", [[], [[]], [[1], [2, 3]], [[p.MISSING]], [[[]]], "abc"])
def test_bad_ranges_refuse(rows):
    with pytest.raises(p.PrimitiveError) as error:
        p.range_values(rows)
    assert error.value.code == "invalid_input"


def test_arity_and_operation_boundaries():
    for name, args in (("SUM", ()), ("SUM", (1,) * 256), ("ROUND", (1,)), ("ABS", (1, 2))):
        with pytest.raises(p.PrimitiveError) as error:
            p.call(name, *args)
        assert error.value.code == "invalid_expression"
    for name in ("PRODUCT", "_xlfn.SUM", "süm"):
        with pytest.raises(p.PrimitiveError) as error:
            p.call(name, 1)
        assert error.value.code == "unsupported_operation"
    with pytest.raises(p.PrimitiveError) as error:
        p.Expression.binary("^", p.literal(2), p.literal(3))
    assert error.value.code == "unsupported_operation"


def test_inputs_case_and_exact_declared_set():
    expression = p.input("Price") + p.input("price")
    assert expression.evaluate({"Price": 1, "price": 2}) == 3
    for bindings in ({"Price": 1}, {"Price": 1, "price": 2, "extra": 0}, []):
        with pytest.raises(p.PrimitiveError) as error:
            expression.evaluate(bindings)
        assert error.value.code == "invalid_input"


def test_native_ast_bridge_never_parses_or_uses_cells(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("formula parsing or worksheet lookup is forbidden")
    monkeypatch.setattr(legacy._Parser, "__init__", forbidden)
    monkeypatch.setattr(legacy, "_cell_value", forbidden)
    monkeypatch.setattr(legacy, "_range_value", forbidden)
    assert p.sum(p.range_values([[2], [3]])) == 5
    assert (p.input("x") * 2).evaluate({"x": 3}) == 6
    assert p.call("IF", True, 4, p.literal(1) / 0).evaluate() == 4


def test_resource_budgets_count_occurrences_and_fail_before_evaluation():
    bound = p.range_values([[1] * 50_001])
    expression = p.call("SUM", p.input("values"), p.input("values"))
    with pytest.raises(p.PrimitiveError) as error:
        expression.evaluate({"values": bound})
    assert error.value.code == "resource_limit"
    with pytest.raises(p.PrimitiveError) as error:
        p.literal("😀" * 16_384)
    assert error.value.code == "resource_limit"
    document = {"kind": "literal", "value": 1}
    for _ in range(64):
        document = {"kind": "call", "function": "ABS", "arguments": [document]}
    with pytest.raises(p.PrimitiveError) as error:
        p.Expression.from_dict({"schema_version": 1, "expression": document})
    assert error.value.code == "resource_limit"


def test_cyclic_and_extra_key_interchange_is_refused():
    cyclic = {"kind": "call", "function": "SUM", "arguments": []}
    cyclic["arguments"].append(cyclic)
    with pytest.raises(p.PrimitiveError) as error:
        p.Expression.from_dict({"schema_version": 1, "expression": cyclic})
    assert error.value.code == "invalid_expression"
    extra = p.literal(1).to_dict()
    extra["expression"]["extra"] = True
    with pytest.raises(p.PrimitiveError):
        p.Expression.from_dict(extra)


def test_numeric_boundary_uses_finite_binary64_and_safe_error_results():
    assert p.literal(9007199254740993).evaluate() == 9007199254740992
    assert p.sum(9007199254740993, 0) == 9007199254740992
    assert error_code(p.sum(1e308, 1e308)) == "#NUM!"
    matrix = p.literal(ArrayValue(((1e308,),))) * 1e308
    assert error_code(matrix.evaluate().rows[0][0]) == "#NUM!"


def test_catalog_is_detached():
    catalog = p.primitive_catalog()
    before = copy.deepcopy(catalog)
    catalog["functions"].clear()
    assert p.primitive_catalog() == before


@pytest.mark.parametrize("function", [p.count, p.sum])
def test_direct_calls_enforce_the_shared_interchange_byte_limit(function):
    arguments = ["x" * 32_767] * 31
    assert function(*arguments) == 0
    with pytest.raises(p.PrimitiveError) as error:
        function(*arguments, arguments[0])
    assert error.value.code == "resource_limit"
