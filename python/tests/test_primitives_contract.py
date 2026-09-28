"""Independent workbook-free primitive contracts across Python and standalone Rust.

Fixed expectations describe the bounded Forge profile, not new Excel observations.
The Rust process does not use PyO3; the Python proof blocks workbook and parser paths.
"""
from __future__ import annotations

import copy
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
ERROR_CODES = ["#VALUE!", "#DIV/0!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#CALC!"]
OPERATORS = {
    "+": "add", "-": "subtract", "*": "multiply", "/": "divide",
    "=": "equal", "<>": "not_equal", "<": "less_than", "<=": "less_equal",
    ">": "greater_than", ">=": "greater_equal",
}


def literal(value):
    return {"kind": "literal", "value": value}


def input_(name):
    return {"kind": "input", "name": name}


def call(name, *arguments):
    return {"kind": "call", "function": name, "arguments": list(arguments)}


def binary(operator, left, right):
    return {"kind": "binary", "operator": operator, "left": left, "right": right}


def envelope(node):
    return {"schema_version": 1, "expression": node}


def _normalize(value):
    from workbook_forge import ArrayValue, ErrorValue
    if isinstance(value, ErrorValue):
        return {"error": value.code}
    if isinstance(value, ArrayValue):
        return {"array": [[_normalize(item) for item in row] for row in value.rows]}
    return value


def _native(value, primitives):
    from workbook_forge import ArrayValue, ErrorValue
    if isinstance(value, dict) and set(value) == {"error"}:
        return ErrorValue(value["error"])
    if isinstance(value, dict) and set(value) in ({"range"}, {"array"}):
        key = next(iter(value))
        rows = [[_native(item, primitives) for item in row] for row in value[key]]
        return primitives.range_values(rows) if key == "range" else ArrayValue(rows)
    return value


@pytest.fixture(scope="module")
def rust_primitives():
    cargo = shutil.which("cargo")
    if cargo is None:
        pytest.skip("Rust toolchain unavailable; standalone primitives were not verified")
    target = ROOT / ".verification/primitives-contract-target"
    result = subprocess.run(
        [cargo, "build", "--manifest-path", str(ROOT / "rust/Cargo.toml"),
         "--locked", "--offline", "--example", "primitive_contract"],
        env=dict(os.environ, CARGO_TARGET_DIR=str(target)), capture_output=True,
        text=True, timeout=180,
    )
    assert result.returncode == 0, result.stderr
    return target / "debug/examples" / ("primitive_contract.exe" if os.name == "nt" else "primitive_contract")


@pytest.fixture(params=["python", "rust"])
def primitive(request):
    if request.param == "python":
        module = importlib.import_module("workbook_forge.primitives")

        def run(document, inputs):
            try:
                expression = module.Expression.from_dict(document)
                bound = {name: _native(value, module) for name, value in inputs.items()}
                value = expression.evaluate(bound)
                return {"ok": True, "value": _normalize(value), "inspection": expression.inspect()}
            except module.PrimitiveError as error:
                return {"ok": False, "error": {"code": error.code, "message": str(error)}}
    else:
        runner = request.getfixturevalue("rust_primitives")

        def run(document, inputs):
            result = subprocess.run(
                [str(runner)], input=json.dumps({"expression": document, "inputs": inputs},
                ensure_ascii=False, separators=(",", ":")) + "\n",
                text=True, capture_output=True, timeout=30,
            )
            assert result.returncode == 0, result.stderr
            lines = result.stdout.splitlines()
            assert len(lines) == 1, result.stdout
            return json.loads(lines[0])

    def checked(node, inputs=None, *, wrapped=False):
        document = node if wrapped else envelope(node)
        bindings = {} if inputs is None else inputs
        before = copy.deepcopy((document, bindings))
        result = run(document, bindings)
        assert (document, bindings) == before
        assert type(result["ok"]) is bool
        if result["ok"]:
            assert set(result) == {"ok", "value", "inspection"}
            inspection = result["inspection"]
            assert set(inspection) == {"schema_version", "profile", "expression", "inputs", "operations"}
            assert inspection["schema_version"] == 1
            assert inspection["profile"] == "spreadsheet-primitives-v1"
            assert inspection["inputs"] == sorted(set(inspection["inputs"]))
            assert inspection["operations"] == sorted(set(inspection["operations"]))
            jsonschema.validate(envelope(inspection["expression"]),
                json.loads((ROOT / "catalog/primitive-expression.schema.json").read_text()))
        else:
            assert result["error"]["code"] in {
                "invalid_expression", "invalid_input", "resource_limit", "unsupported_operation"}
            assert isinstance(result["error"]["message"], str) and result["error"]["message"]
        return result

    checked.backend = request.param
    return checked


def value(primitive, node, inputs=None):
    result = primitive(node, inputs)
    assert result["ok"], result
    return result["value"]


def rejected(primitive, node, code, inputs=None, *, wrapped=False):
    result = primitive(node, inputs, wrapped=wrapped)
    assert result["ok"] is False, result
    assert result["error"]["code"] == code, result


@pytest.mark.parametrize("item", [None, False, True, 0, -2.5, "", "東京", *[{"error": code} for code in ERROR_CODES]])
def test_literal_values_are_typed_and_errors_are_values(primitive, item):
    result = value(primitive, literal(item))
    assert result == item
    if isinstance(item, bool) or item is None:
        assert type(result) is type(item)


@pytest.mark.parametrize("name,expected", [("SUM", 8), ("AVERAGE", 4), ("MIN", 2), ("MAX", 6), ("COUNT", 2)])
@pytest.mark.parametrize("container", ["scalar", "range", "array"])
def test_aggregate_profile_ignores_booleans_text_and_blanks(primitive, name, expected, container):
    items = [2, True, "100", None, "", 6]
    arguments = [literal(item) for item in items] if container == "scalar" else [literal({container: [items]})]
    assert value(primitive, call(name, *arguments)) == expected
    if name == "SUM":
        assert value(primitive, call(name, literal(True), literal("2"))) == 0


@pytest.mark.parametrize("name,expected", [("SUM", 0), ("AVERAGE", {"error": "#DIV/0!"}), ("MIN", 0), ("MAX", 0), ("COUNT", 0)])
def test_no_numeric_aggregate_values_follow_declared_profile(primitive, name, expected):
    assert value(primitive, call(name, literal({"range": [[None, "", False]]}))) == expected


@pytest.mark.parametrize("name", ["SUM", "AVERAGE", "MIN", "MAX", "COUNT"])
def test_aggregate_errors_propagate_without_becoming_validation_failures(primitive, name):
    assert value(primitive, call(name, literal({"range": [[1, {"error": "#REF!"}, 2]]}))) == {"error": "#REF!"}


@pytest.mark.parametrize("operator,expected", [("+", 10), ("-", 6), ("*", 16), ("/", 4), ("=", False), ("<>", True), ("<", False), ("<=", False), (">", True), (">=", True)])
def test_all_binary_operators_execute_and_are_inspectable(primitive, operator, expected):
    result = primitive(binary(operator, literal(8), literal(2)))
    assert result["ok"] and result["value"] == expected
    assert result["inspection"]["operations"] == ["excel.operator." + OPERATORS[operator]]


@pytest.mark.parametrize("node,expected", [
    (binary("+", literal(None), literal(True)), 1),
    (binary("*", literal("2"), literal(3)), 6),
    (binary("/", literal(1), literal(0)), {"error": "#DIV/0!"}),
    (binary("=", literal("ABC"), literal("abc")), True),
    (binary("=", literal("É"), literal("é")), False),
    (binary("=", literal("2"), literal(2)), False),
    (binary("<", literal("2"), literal(2)), {"error": "#VALUE!"}),
    (binary("+", literal({"error": "#N/A"}), literal({"error": "#REF!"})), {"error": "#N/A"}),
    (call("ROUND", literal(2.5), literal(0)), 3),
    (call("ROUND", literal(-2.5), literal(0)), -3),
    (call("ROUND", literal(314.5), literal(-1)), 310),
    (call("ROUND", literal(3.14159), literal(1.9)), 3.1),
    (call("ABS", literal(-9)), 9),
    (call("ABS", literal("-2")), 2),
    (call("ABS", literal({"array": [[-2]]})), {"error": "#VALUE!"}),
])
def test_coercion_rounding_and_error_profile(primitive, node, expected):
    assert value(primitive, node) == expected


def test_arrays_lift_binary_results_and_ranges_keep_explicit_shape(primitive):
    rows = [[1, None], [3, {"error": "#N/A"}]]
    assert value(primitive, literal({"range": rows})) == {"array": rows}
    assert value(primitive, binary("+", literal({"array": rows}), literal(2))) == {"array": [[3, 2], [5, {"error": "#N/A"}]]}
    assert value(primitive, binary("*", literal({"range": [[2, 3]]}), literal({"array": [[4, 5]]}))) == {"array": [[8, 15]]}
    assert value(primitive, binary("+", literal({"array": [[1, 2]]}), literal({"array": [[1], [2]]}))) == {"error": "#VALUE!"}


def test_lazy_branches_preserve_missing_blank_and_false(primitive):
    failure = binary("/", literal(1), literal(0))
    omitted = {"kind": "omitted"}
    assert value(primitive, call("IF", literal(True), literal(42), failure)) == 42
    assert value(primitive, call("IF", literal(False), failure, literal(None))) is None
    assert value(primitive, call("IF", literal(False), failure)) is False
    assert value(primitive, call("IF", literal(True), omitted, failure)) == {"error": "#VALUE!"}
    assert value(primitive, call("IF", literal(False), omitted, literal(4))) == 4
    assert value(primitive, call("IFERROR", literal(None), failure)) is None
    assert value(primitive, call("IFERROR", failure, literal("recovered"))) == "recovered"


def test_all_declared_bindings_are_required_even_in_untaken_branches(primitive):
    expression = call("IF", literal(True), input_("Value"), input_("unused"))
    assert value(primitive, expression, {"Value": 7, "unused": {"error": "#DIV/0!"}}) == 7
    rejected(primitive, expression, "invalid_input", {"Value": 7})
    rejected(primitive, expression, "invalid_input", {"Value": 7, "unused": 8, "extra": 9})
    rejected(primitive, expression, "invalid_input", {"value": 7, "unused": 8})


def test_scenario_composition_is_inspectable_without_workbook_addresses(primitive):
    quantity = call("SUM", input_("volumes"))
    revenue = binary("*", quantity, input_("price"))
    variable_cost = binary("*", quantity, input_("unit_cost"))
    fixed_cost = binary("*", input_("fixed"), call("COUNT", input_("volumes")))
    profit = binary("-", binary("-", revenue, variable_cost), fixed_cost)
    bindings = {"volumes": {"range": [[100], [120], [150]]}, "price": 20, "unit_cost": 8, "fixed": 1000}
    result = primitive(profit, bindings)
    assert result["ok"] and result["value"] == 1440
    assert result["inspection"]["expression"] == profit
    assert result["inspection"]["inputs"] == ["fixed", "price", "unit_cost", "volumes"]
    assert result["inspection"]["operations"] == ["excel.COUNT", "excel.SUM", "excel.operator.multiply", "excel.operator.subtract"]
    assert value(primitive, revenue, {"volumes": bindings["volumes"], "price": 20}) == 7400
    bindings["price"] = 25
    assert value(primitive, profit, bindings) == 3290
    assert value(primitive, revenue, {"volumes": bindings["volumes"], "price": 25}) == 9250


def test_function_case_normalization_is_ascii_and_input_case_is_preserved(primitive):
    result = primitive(call("sUm", input_("x"), input_("X")), {"x": 2, "X": 3})
    assert result["ok"] and result["value"] == 5
    assert result["inspection"]["expression"]["function"] == "SUM"
    assert result["inspection"]["inputs"] == ["X", "x"]
    assert result["inspection"]["operations"] == ["excel.SUM"]


@pytest.mark.parametrize("document", [
    {}, {"schema_version": True, "expression": literal(1)},
    {"schema_version": 2, "expression": literal(1)},
    {"schema_version": 1, "expression": literal(1), "extra": 1},
    envelope({"kind": "literal"}), envelope({"kind": "literal", "value": 1, "extra": 2}),
    envelope({"kind": "input", "name": "1bad"}), envelope({"kind": "input", "name": "é"}),
    envelope({"kind": "input", "name": "a" * 65}), envelope({"kind": "input", "name": "x", "default": 0}),
    envelope({"kind": "cell", "address": "A1"}), envelope({"kind": "omitted"}),
    envelope(binary("+", {"kind": "omitted"}, literal(1))),
    envelope({"kind": "call", "function": "SUM", "arguments": {}}),
    envelope({"kind": "call", "function": 1, "arguments": [literal(1)]}),
    envelope({"kind": "binary", "operator": "+", "left": literal(1)}),
    envelope(binary(1, literal(1), literal(2))),
    envelope(literal({"error": "#SPILL!"})), envelope(literal({"error": "#N/A", "message": "no"})),
    envelope(literal({"range": []})), envelope(literal({"array": [[]]})),
    envelope(literal({"array": [[1], [2, 3]]})), envelope(literal([1, 2])),
    envelope(literal({"range": [[{"array": [[1]]}]]})),
])
def test_invalid_interchange_is_rejected_before_evaluation(primitive, document):
    rejected(primitive, document, "invalid_expression", wrapped=True)


@pytest.mark.parametrize("node", [call("PRODUCT", literal(1)), call("ſUM", literal(1)), binary("^", literal(2), literal(3))])
def test_unsupported_operations_do_not_fall_through_to_larger_evaluator(primitive, node):
    rejected(primitive, node, "unsupported_operation")


@pytest.mark.parametrize("name,arguments", [
    ("SUM", []), ("SUM", [literal(1)] * 256), ("AVERAGE", []),
    ("IF", [literal(True)]), ("IF", [literal(True), literal(1), literal(2), literal(3)]),
    ("IFERROR", [literal(1)]), ("ROUND", [literal(1)]), ("ABS", [literal(1), literal(2)]),
])
def test_wrong_arity_is_expression_validation_not_spreadsheet_error(primitive, name, arguments):
    rejected(primitive, call(name, *arguments), "invalid_expression")


@pytest.mark.parametrize("bad", [[], {}, {"unknown": 1}])
def test_invalid_native_bindings_are_input_errors(primitive, bad):
    rejected(primitive, input_("x"), "invalid_input", {"x": bad})


def test_utf16_text_limit_is_not_unicode_codepoint_count(primitive):
    accepted = "😀" * 16383 + "x"
    assert value(primitive, literal(accepted)) == accepted
    rejected(primitive, literal(accepted + "x"), "resource_limit")
    rejected(primitive, input_("x"), "resource_limit", {"x": accepted + "x"})


def test_expression_depth_is_root_inclusive(primitive):
    expression = literal(1)
    for _ in range(63):
        expression = call("ABS", expression)
    assert value(primitive, expression) == 1
    rejected(primitive, call("ABS", expression), "resource_limit")


def test_node_and_distinct_input_budgets_are_enforced(primitive):
    children = [call("SUM", *[literal(1)] * 255) for _ in range(4)]
    children[-1]["arguments"].pop()
    assert value(primitive, call("SUM", *children)) == 1019
    children[-1]["arguments"].append(literal(1))
    rejected(primitive, call("SUM", *children), "resource_limit")
    names = [f"v{i}" for i in range(257)]
    expression = call("SUM", call("SUM", *[input_(name) for name in names[:255]]), input_(names[255]))
    assert value(primitive, expression, {name: 1 for name in names[:256]}) == 256
    expression["arguments"].append(input_(names[256]))
    rejected(primitive, expression, "resource_limit", {name: 1 for name in names})


def test_element_budget_counts_repeated_inputs_and_untaken_literals(primitive):
    values = {"array": [[1] * 50000]}
    expression = call("SUM", input_("x"), input_("x"))
    assert value(primitive, expression, {"x": values}) == 100000
    rejected(primitive, call("SUM", expression, literal(1)), "resource_limit", {"x": values})
    rejected(primitive, call("IF", literal(True), literal(1), literal({"range": [[0] * 99999]})), "resource_limit")


def test_interchange_size_is_bounded_before_evaluation(primitive):
    text = "x" * 32767
    assert value(primitive, call("COUNT", *[literal(text)] * 31)) == 0
    rejected(primitive, call("COUNT", *[literal(text)] * 32), "resource_limit")


def test_python_builders_and_callables_do_not_use_parsers_cells_or_native_bridge():
    script = r'''import importlib.abc, json, sys
class BlockForbidden(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"workbook_forge._native", "workbook_forge.workbook", "workbook_forge.xlsx", "workbook_forge.toolkit", "workbook_forge.python_engine", "workbook_forge.expressions"}:
            raise AssertionError("forbidden workbook/native import: " + fullname)
sys.meta_path.insert(0, BlockForbidden())
import workbook_forge as legacy
def forbidden(*args, **kwargs):
    raise AssertionError("formula parser or cell evaluator was called")
for name in ["evaluate", "evaluate_result", "_tokenize", "_Parser", "_cell_value", "_range_value"]:
    setattr(legacy, name, forbidden)
from workbook_forge import primitives as p
assert p.sum(1, 2, 3) == 6
assert p.average(p.range_values([[2], [4]])) == 3
assert p.min(8, 2) == 2 and p.max(8, 2) == 8
assert p.count(1, True, "2") == 1
assert p.if_(False, 1, 9) == 9
assert p.iferror(legacy.ErrorValue("#REF!"), 4) == 4
assert p.round(-2.5, 0) == -3 and p.abs(-7) == 7
x = p.input("x")
expr = p.Expression.binary("*", p.call("SUM", x), p.literal(2))
assert expr.evaluate({"x": p.range_values([[2], [3]])}) == 10
assert p.Expression.from_dict(expr.to_dict()).inspect() == expr.inspect()
assert expr.inspect()["inputs"] == ["x"]
print(json.dumps({"status": "passed", "value": 10}))
'''
    result = subprocess.run([sys.executable, "-c", textwrap.dedent(script)], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"status": "passed", "value": 10}


def test_catalog_exposes_only_the_bounded_profile_and_common_error_values():
    module = importlib.import_module("workbook_forge.primitives")
    catalog = json.loads((ROOT / "catalog/primitive-catalog.json").read_text())
    assert module.primitive_catalog() == catalog
    assert (ROOT / "catalog/primitive-catalog.json").read_bytes() == (ROOT / "rust/src/primitive_catalog.json").read_bytes()
    assert catalog["profile"] == "spreadsheet-primitives-v1"
    functions = {item["name"]: item for item in catalog["functions"]}
    assert set(functions) == {"SUM", "AVERAGE", "MIN", "MAX", "COUNT", "IF", "IFERROR", "ROUND", "ABS"}
    assert {item["symbol"]: item["id"] for item in catalog["operators"]} == {symbol: "excel.operator." + name for symbol, name in OPERATORS.items()}
    for entry in functions.values():
        assert entry["compatibility"] == "bounded-forge-profile"
        assert set(entry["supported_error_values"]) == set(ERROR_CODES)
        assert entry["limitations"] and "not an Excel observation" in entry["evidence"]
    for name in ["SUM", "AVERAGE", "MIN", "MAX", "COUNT"]:
        warning = " ".join(functions[name]["limitations"]).lower()
        assert "boolean" in warning and "text" in warning and "scalar" in warning
    detached = module.primitive_catalog()
    detached["functions"].clear()
    assert module.primitive_catalog() == catalog


def test_expression_schema_is_available_and_detached_from_package_api():
    module = importlib.import_module("workbook_forge.primitives")
    canonical = (ROOT / "catalog/primitive-expression.schema.json").read_bytes()
    assert canonical == (ROOT / "rust/src/primitive_expression.schema.json").read_bytes()
    schema = json.loads(canonical)
    assert module.primitive_expression_schema() == schema
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(module.call("SUM", 1, 2).to_dict(), schema)
    detached = module.primitive_expression_schema()
    detached.clear()
    assert module.primitive_expression_schema() == schema


def test_numeric_values_use_binary64_before_arithmetic(primitive):
    integer = 9007199254740993
    rounded = 9007199254740992
    assert value(primitive, literal(integer)) == rounded
    assert value(primitive, input_("x"), {"x": integer}) == rounded
    assert value(primitive, literal({"array": [[integer]]})) == {"array": [[rounded]]}
    assert value(primitive, binary("-", literal(integer), literal(rounded))) == 0


def test_nonfinite_results_are_spreadsheet_errors_and_array_errors_stay_local(primitive):
    assert value(primitive, binary("*", literal(1e308), literal(2))) == {"error": "#NUM!"}
    assert value(primitive, call("SUM", literal(1e308), literal(1e308))) == {"error": "#NUM!"}
    mixed = {"array": [[1, {"error": "#N/A"}]]}
    # Existing IFERROR profile only replaces a scalar error, not array elements.
    assert value(primitive, call("IFERROR", literal(mixed), literal(9))) == mixed


def test_expression_and_bindings_share_one_interchange_byte_budget(primitive):
    text = "x" * 32767
    names = [f"v{i}" for i in range(16)]
    expression = call("COUNT", *[literal(text)] * 16, *[input_(name) for name in names])
    bindings = {name: text for name in names}
    assert len(json.dumps(envelope(expression), separators=(",", ":")).encode()) < 1048576
    assert len(json.dumps(bindings, separators=(",", ":")).encode()) < 1048576
    rejected(primitive, expression, "resource_limit", bindings)
    expression["arguments"].pop(0)
    assert value(primitive, expression, bindings) == 0


def test_rust_line_transport_recovers_after_malformed_and_oversized_requests(rust_primitives):
    good = json.dumps({"expression": envelope(call("SUM", literal(2), literal(3))), "inputs": {}}).encode()
    requests = b"{invalid}\n" + b"x" * (1048576 + 1) + b"\n" + good + b"\n"
    process = subprocess.run([str(rust_primitives)], input=requests, capture_output=True, timeout=30)
    assert process.returncode == 0, process.stderr
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    assert len(responses) == 3
    assert [item["error"]["code"] for item in responses[:2]] == ["invalid_expression", "resource_limit"]
    assert responses[2]["ok"] and responses[2]["value"] == 5
