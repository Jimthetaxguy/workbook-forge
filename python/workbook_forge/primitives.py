"""Workbook-free spreadsheet values and expressions using the Python evaluator.

Expressions compile directly to the existing evaluator AST. Named inputs are
native values, never formula source or invented worksheet references.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import ArrayValue, ErrorValue, _Node, _Range, _WildcardBudget, _eval
from .catalog import _catalog_file

MAX_NODES = 1024
MAX_DEPTH = 64
MAX_INPUTS = 256
MAX_TEXT_UNITS = 32_767
MAX_VALUES = 100_000
MAX_JSON_BYTES = 1024 * 1024
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_ERRORS = frozenset({"#VALUE!", "#DIV/0!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#CALC!"})
_FUNCTIONS = frozenset({"SUM", "AVERAGE", "MIN", "MAX", "COUNT", "IF", "IFERROR", "ROUND", "ABS"})
_OPERATORS = {
    "+": "add", "-": "subtract", "*": "multiply", "/": "divide",
    "=": "equal", "<>": "not_equal", "<": "less_than", "<=": "less_equal",
    ">": "greater_than", ">=": "greater_equal",
}


class PrimitiveError(ValueError):
    """An API, schema, resource or unsupported-operation error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _Missing:
    __slots__ = ()

    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()


def primitive_catalog() -> dict[str, Any]:
    """Load a detached copy of the canonical installed primitive catalog."""
    return json.loads(_catalog_file("primitive-catalog.json").read_text(encoding="utf-8"))


def primitive_expression_schema() -> dict[str, Any]:
    """Return the installed canonical expression schema as detached metadata."""
    return json.loads(_catalog_file("primitive-expression.schema.json").read_text(encoding="utf-8"))


def _scalar(value: object, code: str) -> object:
    if value is None or type(value) is bool:
        return value
    if type(value) in (int, float):
        try:
            value = float(value)
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise PrimitiveError(code, "numbers must be finite binary64 values")
        return value
    if type(value) is str:
        if len(value) > MAX_TEXT_UNITS:
            raise PrimitiveError("resource_limit", "text exceeds the UTF-16 length limit")
        try:
            units = len(value.encode("utf-16-le")) // 2
        except UnicodeError as error:
            raise PrimitiveError(code, "text contains an invalid Unicode scalar") from error
        if units > MAX_TEXT_UNITS:
            raise PrimitiveError("resource_limit", "text exceeds the UTF-16 length limit")
        return value
    if type(value) is ErrorValue and type(value.code) is str and value.code in _ERRORS:
        return value
    raise PrimitiveError(code, "expected a scalar, blank or supported spreadsheet error")


def _matrix(rows: object, code: str) -> tuple[tuple[Any, ...], ...]:
    if not isinstance(rows, (list, tuple)) or not rows:
        raise PrimitiveError(code, "a matrix requires nonempty rows")
    if len(rows) > MAX_VALUES:
        raise PrimitiveError("resource_limit", "matrix exceeds the value limit")
    output: list[tuple[Any, ...]] = []
    width = None
    count = 0
    for row in rows:
        if not isinstance(row, (list, tuple)) or not row:
            raise PrimitiveError(code, "matrix rows must be nonempty scalar collections")
        if width is None:
            width = len(row)
        if len(row) != width:
            raise PrimitiveError(code, "matrix rows must have equal length")
        count += len(row)
        if count > MAX_VALUES:
            raise PrimitiveError("resource_limit", "matrix exceeds the value limit")
        output.append(tuple(_scalar(value, code) for value in row))
    return tuple(output)


@dataclass(frozen=True, slots=True)
class RangeValues:
    """Immutable values with range identity, separate from computed arrays."""

    rows: tuple[tuple[Any, ...], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", _matrix(self.rows, "invalid_input"))


def range_values(rows: object) -> RangeValues:
    return RangeValues(rows)  # type: ignore[arg-type]


def _native(value: object, code: str = "invalid_input") -> object:
    if isinstance(value, RangeValues):
        rows = _matrix(value.rows, code)
        return _Range(tuple(item for row in rows for item in row), len(rows), len(rows[0]))
    if isinstance(value, ArrayValue):
        return ArrayValue(_matrix(value.rows, code))
    return _scalar(value, code)


def _value_count(value: object) -> int:
    if isinstance(value, _Range):
        return len(value.values)
    if isinstance(value, ArrayValue):
        return len(value.rows) * len(value.rows[0])
    return 1


def _encoded_value(value: object) -> object:
    if isinstance(value, ErrorValue):
        return {"error": value.code}
    if isinstance(value, _Range):
        rows = [value.values[offset:offset + value.columns]
                for offset in range(0, len(value.values), value.columns)]
        return {"range": [[_encoded_value(item) for item in row] for row in rows]}
    if isinstance(value, ArrayValue):
        return {"array": [[_encoded_value(item) for item in row] for row in value.rows]}
    return value


def _decode_scalar(value: object) -> object:
    if type(value) is dict:
        if set(value) != {"error"} or type(value["error"]) is not str:
            raise PrimitiveError("invalid_expression", "malformed encoded error scalar")
        value = ErrorValue(value["error"])
    return _scalar(value, "invalid_expression")


def _decode_value(value: object) -> object:
    if type(value) is dict and set(value) in ({"range"}, {"array"}):
        kind = next(iter(value))
        rows = value[kind]
        if type(rows) is not list or not rows or len(rows) > MAX_VALUES:
            if type(rows) is list and len(rows) > MAX_VALUES:
                raise PrimitiveError("resource_limit", "matrix exceeds the value limit")
            raise PrimitiveError("invalid_expression", "encoded matrix requires nonempty rows")
        decoded = []
        count = 0
        for row in rows:
            if type(row) is not list:
                raise PrimitiveError("invalid_expression", "encoded matrix rows must be arrays")
            count += len(row)
            if count > MAX_VALUES:
                raise PrimitiveError("resource_limit", "matrix exceeds the value limit")
            decoded.append([_decode_scalar(item) for item in row])
        bounded = _matrix(decoded, "invalid_expression")
        if kind == "array":
            return ArrayValue(bounded)
        return _Range(tuple(item for row in bounded for item in row), len(bounded), len(bounded[0]))
    return _decode_scalar(value)


def _function_name(name: object) -> str:
    if type(name) is not str:
        raise PrimitiveError("invalid_expression", "function name must be text")
    if not name.isascii():
        raise PrimitiveError("unsupported_operation", "function must name a supported operation")
    normalized = name.upper()
    if normalized not in _FUNCTIONS:
        raise PrimitiveError("unsupported_operation", f"unsupported function: {name}")
    return normalized


def _check_json_size(value: object) -> None:
    count = 0
    try:
        for piece in json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":")).iterencode(value):
            count += len(piece.encode("utf-8"))
            if count > MAX_JSON_BYTES:
                raise PrimitiveError("resource_limit", "interchange exceeds the JSON byte limit")
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, PrimitiveError):
            raise
        raise PrimitiveError("invalid_expression", "expression is not valid bounded JSON") from error


def _node_dict(node: _Node) -> dict[str, Any]:
    if node.kind == "native_value":
        return {"kind": "literal", "value": _encoded_value(node.value)}
    if node.kind == "input":
        return {"kind": "input", "name": node.value}
    if node.kind == "missing":
        return {"kind": "omitted"}
    if node.kind == "call":
        return {"kind": "call", "function": node.value,
                "arguments": [_node_dict(child) for child in node.children]}
    return {"kind": "binary", "operator": node.value,
            "left": _node_dict(node.children[0]), "right": _node_dict(node.children[1])}


def _preflight(root: _Node, *, final: bool = True,
               bindings: Mapping[str, object] | None = None) -> tuple[list[str], list[str]]:
    inputs: set[str] = set()
    operations: set[str] = set()
    nodes = values = 0
    pending = [(root, 1, None)]
    while pending:
        node, depth, parent = pending.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise PrimitiveError("resource_limit", "expression exceeds its node or depth limit")
        if not isinstance(node, _Node):
            raise PrimitiveError("invalid_expression", "malformed expression node")
        if node.kind == "input":
            if type(node.value) is not str or _NAME.fullmatch(node.value) is None:
                raise PrimitiveError("invalid_expression", "invalid input name")
            inputs.add(node.value)
            if len(inputs) > MAX_INPUTS:
                raise PrimitiveError("resource_limit", "expression exceeds its named-input limit")
            if bindings is not None and node.value in bindings:
                values += _value_count(bindings[node.value])
        elif node.kind == "native_value":
            values += _value_count(node.value)
        elif node.kind == "missing":
            if final and parent != "call":
                raise PrimitiveError("invalid_expression", "omitted values must be immediate call arguments")
        elif node.kind == "call":
            operations.add("excel." + str(node.value))
        elif node.kind == "binary":
            if node.value not in _OPERATORS:
                raise PrimitiveError("unsupported_operation", "unsupported binary operator")
            operations.add("excel.operator." + _OPERATORS[node.value])
        else:
            raise PrimitiveError("invalid_expression", "unsupported expression node")
        if values > MAX_VALUES:
            raise PrimitiveError("resource_limit", "expression exceeds its cumulative value limit")
        pending.extend((child, depth + 1, node.kind) for child in reversed(node.children))
    return sorted(inputs), sorted(operations)


def _compile(node: _Node, bindings: Mapping[str, object]) -> _Node:
    if node.kind == "input":
        return _Node("native_value", bindings[node.value])
    return _Node(node.kind, node.value, tuple(_compile(child, bindings) for child in node.children))


def _result(value: object) -> object:
    if isinstance(value, _Range):
        value = ArrayValue(tuple(value.values[offset:offset + value.columns]
                                 for offset in range(0, len(value.values), value.columns)))
    if isinstance(value, ArrayValue):
        return ArrayValue(tuple(tuple(_result(item) for item in row) for row in value.rows))
    if type(value) in (int, float):
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            return ErrorValue("#NUM!", "calculated number exceeds the finite binary64 range")
    return value


@dataclass(frozen=True, slots=True, init=False)
class Expression:
    """An immutable, validated spreadsheet expression without workbook state."""

    _root: _Node

    @classmethod
    def _from_node(cls, node: _Node) -> Expression:
        _preflight(node, final=False)
        _check_json_size({"schema_version": 1, "expression": _node_dict(node)})
        instance = object.__new__(cls)
        object.__setattr__(instance, "_root", node)
        return instance

    @classmethod
    def input(cls, name: str) -> Expression:
        return cls._from_node(_Node("input", name))

    @classmethod
    def literal(cls, value: object) -> Expression:
        return cls._from_node(_Node("missing") if value is MISSING else _Node("native_value", _native(value)))

    @classmethod
    def call(cls, name: str, *arguments: Expression) -> Expression:
        name = _function_name(name)
        if any(not isinstance(argument, Expression) for argument in arguments):
            raise PrimitiveError("invalid_expression", "Expression.call requires Expression arguments")
        _check_arity(name, len(arguments))
        return cls._from_node(_Node("call", name, tuple(argument._root for argument in arguments)))

    @classmethod
    def binary(cls, operator: str, left: Expression, right: Expression) -> Expression:
        if type(operator) is not str:
            raise PrimitiveError("invalid_expression", "binary operator must be text")
        if operator not in _OPERATORS:
            raise PrimitiveError("unsupported_operation", "unsupported binary operator")
        if not isinstance(left, Expression) or not isinstance(right, Expression):
            raise PrimitiveError("invalid_expression", "binary operands must be Expression objects")
        return cls._from_node(_Node("binary", operator, (left._root, right._root)))

    def evaluate(self, inputs: Mapping[str, object] | None = None) -> object:
        names, _ = _preflight(self._root)
        if inputs is None:
            inputs = {}
        if not isinstance(inputs, Mapping) or set(inputs) != set(names):
            raise PrimitiveError("invalid_input", "inputs must bind every declared name and no unknown names")
        bindings: dict[str, object] = {}
        for name in names:
            bindings[name] = _native(inputs[name])
            # Account for every occurrence before allocating the next input;
            # aliases cannot multiply a large matrix past the shared budget.
            _preflight(self._root, bindings=bindings)
        _check_json_size({
            "expression": {"schema_version": 1, "expression": _node_dict(self._root)},
            "inputs": {name: _encoded_value(value) for name, value in bindings.items()},
        })
        root = _compile(self._root, bindings)
        # Compilation permits no cell/range reference nodes. There is no
        # worksheet environment; inputs already occupy native-value AST nodes.
        try:
            return _result(_eval(root, None, "", _WildcardBudget()))  # type: ignore[arg-type]
        except (ArithmeticError, ValueError, TypeError) as error:
            return ErrorValue("#VALUE!", str(error))

    def to_dict(self) -> dict[str, Any]:
        _preflight(self._root)
        result = {"schema_version": 1, "expression": _node_dict(self._root)}
        _check_json_size(result)
        return result

    def inspect(self) -> dict[str, Any]:
        names, operations = _preflight(self._root)
        result = {"schema_version": 1, "profile": "spreadsheet-primitives-v1",
                  "expression": _node_dict(self._root), "inputs": names, "operations": operations}
        _check_json_size(result)
        return result

    @classmethod
    def from_dict(cls, data: object) -> Expression:
        if (type(data) is not dict or set(data) != {"schema_version", "expression"}
                or type(data["schema_version"]) is not int or data["schema_version"] != 1):
            raise PrimitiveError("invalid_expression", "invalid expression envelope")
        counter = [0, 0]
        active: set[int] = set()

        def decode(node: object, depth: int) -> _Node:
            counter[0] += 1
            if counter[0] > MAX_NODES or depth > MAX_DEPTH:
                raise PrimitiveError("resource_limit", "expression exceeds its node or depth limit")
            if type(node) is not dict or type(node.get("kind")) is not str or id(node) in active:
                raise PrimitiveError("invalid_expression", "invalid or cyclic expression node")
            kind = node["kind"]
            required = {"literal": {"kind", "value"}, "input": {"kind", "name"},
                        "call": {"kind", "function", "arguments"},
                        "binary": {"kind", "operator", "left", "right"}, "omitted": {"kind"}}
            if kind not in required or set(node) != required[kind]:
                raise PrimitiveError("invalid_expression", "expression node has invalid fields")
            active.add(id(node))
            try:
                if kind == "literal":
                    value = _decode_value(node["value"])
                    counter[1] += _value_count(value)
                    if counter[1] > MAX_VALUES:
                        raise PrimitiveError("resource_limit", "expression exceeds its cumulative value limit")
                    return _Node("native_value", value)
                if kind == "input":
                    return _Node("input", node["name"])
                if kind == "omitted":
                    return _Node("missing")
                if kind == "call":
                    name = _function_name(node["function"])
                    arguments = node["arguments"]
                    if type(arguments) is not list:
                        raise PrimitiveError("invalid_expression", "call arguments must be an array")
                    _check_arity(name, len(arguments))
                    return _Node("call", name, tuple(decode(child, depth + 1) for child in arguments))
                operator = node["operator"]
                if type(operator) is not str:
                    raise PrimitiveError("invalid_expression", "binary operator must be text")
                if operator not in _OPERATORS:
                    raise PrimitiveError("unsupported_operation", "unsupported binary operator")
                return _Node("binary", operator,
                             (decode(node["left"], depth + 1), decode(node["right"], depth + 1)))
            finally:
                active.remove(id(node))

        expression = cls._from_node(decode(data["expression"], 1))
        _preflight(expression._root)
        return expression

    def __add__(self, other: object) -> Expression:
        return self.binary("+", self, _lift(other))

    def __radd__(self, other: object) -> Expression:
        return self.binary("+", _lift(other), self)

    def __sub__(self, other: object) -> Expression:
        return self.binary("-", self, _lift(other))

    def __rsub__(self, other: object) -> Expression:
        return self.binary("-", _lift(other), self)

    def __mul__(self, other: object) -> Expression:
        return self.binary("*", self, _lift(other))

    def __rmul__(self, other: object) -> Expression:
        return self.binary("*", _lift(other), self)

    def __truediv__(self, other: object) -> Expression:
        return self.binary("/", self, _lift(other))

    def __rtruediv__(self, other: object) -> Expression:
        return self.binary("/", _lift(other), self)


def _lift(value: object) -> Expression:
    return value if isinstance(value, Expression) else Expression.literal(value)


def input(name: str) -> Expression:
    return Expression.input(name)


def literal(value: object) -> Expression:
    return Expression.literal(value)


def call(name: str, *arguments: object) -> Expression:
    _check_arity(_function_name(name), len(arguments))
    return Expression.call(name, *(_lift(argument) for argument in arguments))


def _direct(name: str, arguments: tuple[object, ...]) -> object:
    _check_arity(name, len(arguments))
    return Expression.call(name, *(Expression.literal(value) for value in arguments)).evaluate()


def sum(*values: object) -> object:
    return _direct("SUM", values)


def average(*values: object) -> object:
    return _direct("AVERAGE", values)


def min(*values: object) -> object:
    return _direct("MIN", values)


def max(*values: object) -> object:
    return _direct("MAX", values)


def count(*values: object) -> object:
    return _direct("COUNT", values)


def if_(*values: object) -> object:
    return _direct("IF", values)


def iferror(*values: object) -> object:
    return _direct("IFERROR", values)


def round(*values: object) -> object:
    return _direct("ROUND", values)


def abs(*values: object) -> object:
    return _direct("ABS", values)


def _check_arity(name: str, count: int) -> None:
    # Filled from the canonical shared catalog, not evaluator-private defaults.
    catalog = primitive_catalog()
    operation = next(item for item in catalog["functions"] if item["id"] == "excel." + name)
    arity = operation["arity"]
    if not arity["min"] <= count <= arity["max"]:
        raise PrimitiveError("invalid_expression", f"{name} requires {arity['min']} through {arity['max']} arguments")


__all__ = ["ArrayValue", "ErrorValue", "Expression", "MISSING", "PrimitiveError", "RangeValues",
           "abs", "average", "call", "count", "if_", "iferror", "input", "literal", "max",
           "min", "primitive_catalog", "primitive_expression_schema", "range_values", "round", "sum"]
