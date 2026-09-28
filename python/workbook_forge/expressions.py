"""Independent Python typed expression construction and reference transforms.

The existing Python evaluator supplies the grammar. This module adds immutable
AST authoring, bounded rendering, and copy semantics without loading Rust.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
import re
from typing import Any

from . import (
    ErrorValue,
    MAX_EXPRESSION_NESTING,
    MAX_FORMULA_LENGTH_UNITS,
    MAX_FUNCTION_NESTING,
    _Node,
    _Parser,
    _canonical_function_name,
)

_REFERENCE = re.compile(r"(\$?)([A-Za-z]{1,3})(\$?)([1-9][0-9]*)\Z")
_BINARY_OPERATORS = frozenset({"+", "-", "*", "/", "^", "&", "=", "<>", "<", ">", "<=", ">="})
_ERROR_CODES = frozenset({"#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#SPILL!", "#CALC!"})


@dataclass(frozen=True)
class CellReference:
    """A one-based coordinate with independent copy anchors for each axis."""

    row: int
    column: int
    sheet: str | None = None
    row_absolute: bool = False
    column_absolute: bool = False

    def __post_init__(self) -> None:
        if type(self.row) is not int or type(self.column) is not int:
            raise TypeError("row and column must be integers")
        if not (1 <= self.row <= 1_048_576 and 1 <= self.column <= 16_384):
            raise ValueError("reference is outside Excel worksheet bounds")
        if self.sheet is not None and (not isinstance(self.sheet, str) or not self.sheet):
            raise ValueError("sheet must be a nonempty name")
        if type(self.row_absolute) is not bool or type(self.column_absolute) is not bool:
            raise TypeError("copy anchors must be booleans")

    @classmethod
    def parse(cls, address: str, sheet: str | None = None) -> CellReference:
        match = _REFERENCE.fullmatch(address)
        if match is None:
            raise ValueError(f"invalid A1 reference: {address}")
        column = 0
        for letter in match[2].upper():
            column = column * 26 + ord(letter) - ord("A") + 1
        # At most seven digits can fit in the worksheet. Check before int()
        # so a malformed long row cannot trigger Python's integer digit cap.
        if len(match[4]) > 7:
            raise ValueError("reference is outside Excel worksheet bounds")
        return cls(int(match[4]), column, sheet, bool(match[3]), bool(match[1]))

    def render(self, *, qualified: bool = True) -> str:
        column, letters = self.column, ""
        while column:
            column, digit = divmod(column - 1, 26)
            letters = chr(65 + digit) + letters
        address = ("$" if self.column_absolute else "") + letters
        address += ("$" if self.row_absolute else "") + str(self.row)
        if qualified and self.sheet is not None:
            return "'" + self.sheet.replace("'", "''") + "'!" + address
        return address

    def shifted(self, rows: int = 0, columns: int = 0) -> CellReference:
        if type(rows) is not int or type(columns) is not int:
            raise TypeError("copy offsets must be integers")
        return CellReference(
            self.row if self.row_absolute else self.row + rows,
            self.column if self.column_absolute else self.column + columns,
            self.sheet,
            self.row_absolute,
            self.column_absolute,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sheet": self.sheet,
            "row": self.row,
            "column": self.column,
            "absolute_row": self.row_absolute,
            "absolute_column": self.column_absolute,
        }


class _ExpressionParser(_Parser):
    """Retain numeric lexemes while using the existing Python grammar.

    Converting a token to a binary float and back during a reference transform
    could change its precision or overflow behavior. Numeric AST leaves keep
    the source token; evaluation remains the independent evaluator's job.
    """

    def __init__(self, source: str) -> None:
        super().__init__(source)
        # A cell-shaped token followed by ! is a sheet name; followed by (
        # it is a function name. Reclassify only these unambiguous contexts.
        for index, token in enumerate(self.tokens[:-1]):
            if token.kind == "CELL" and self.tokens[index + 1].value in {"!", "("}:
                self.tokens[index] = replace(token, kind="IDENT")

    def parse_primary(self) -> _Node:
        if self.current.kind == "NUMBER":
            return _Node("number", self.consume().value)
        return super().parse_primary()


def _validate_tree(root: _Node) -> None:
    pending = [(root, 1, 0)]
    while pending:
        node, depth, function_depth = pending.pop()
        if depth > MAX_EXPRESSION_NESTING:
            raise ValueError("expression construction exceeds the 96-level depth limit")
        if node.kind == "call":
            function_depth += 1
            if function_depth > MAX_FUNCTION_NESTING:
                raise ValueError("function nesting exceeds the 64-level limit")
        elif node.kind == "cell":
            address, sheet = node.value
            CellReference.parse(address, sheet)
        elif node.kind == "range":
            start, end, sheet = node.value
            CellReference.parse(start, sheet)
            CellReference.parse(end, sheet)
        pending.extend((child, depth + 1, function_depth) for child in node.children)


def parse_expression(formula: str) -> _Node:
    """Parse and validate a Python AST, including grid and construction limits."""
    if not isinstance(formula, str):
        raise TypeError("formula must be a string")
    source = formula.strip().removeprefix("=")
    if not source:
        raise ValueError("formula is empty")
    try:
        root = _ExpressionParser(source).parse()
    except RecursionError as error:
        raise ValueError("formula exceeds the Python parser nesting limit") from error
    _validate_tree(root)
    return root


def expression_references(root: _Node) -> list[dict[str, Any]]:
    """Inspect references in source order, preserving both copy anchors."""
    references = []
    pending = [root]
    while pending:
        node = pending.pop()
        if node.kind == "cell":
            address, sheet = node.value
            references.append({"start": CellReference.parse(address, sheet).to_dict()})
        elif node.kind == "range":
            start, end, sheet = node.value
            references.append({
                "start": CellReference.parse(start, sheet).to_dict(),
                "end": CellReference.parse(end, sheet).to_dict(),
            })
        pending.extend(reversed(node.children))
    return references


def _render(root: _Node, rows: int = 0, columns: int = 0) -> str:
    """Render iteratively so valid expressions never depend on Python recursion."""
    if type(rows) is not int or type(columns) is not int:
        raise TypeError("copy offsets must be integers")
    pieces: list[str] = []
    pending: list[_Node | str] = [root]
    units = 0
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            units += len(item.encode("utf-16-le")) // 2
            if units > MAX_FORMULA_LENGTH_UNITS:
                raise ValueError("rendered formula exceeds the 8192-character limit")
            pieces.append(item)
            continue
        node = item
        if node.kind == "number":
            pending.append(str(node.value))
        elif node.kind == "literal":
            value = node.value
            if isinstance(value, bool):
                pending.append("TRUE" if value else "FALSE")
            elif isinstance(value, str):
                pending.append('"' + value.replace('"', '""') + '"')
            elif isinstance(value, ErrorValue):
                pending.append(value.code)
            else:
                raise ValueError("unsupported expression literal")
        elif node.kind == "missing":
            continue
        elif node.kind == "cell":
            address, sheet = node.value
            pending.append(CellReference.parse(address, sheet).shifted(rows, columns).render())
        elif node.kind == "range":
            start, end, sheet = node.value
            first = CellReference.parse(start, sheet).shifted(rows, columns)
            last = CellReference.parse(end, sheet).shifted(rows, columns)
            pending.append(first.render() + ":" + last.render(qualified=False))
        elif node.kind == "unary":
            pending.extend([")", node.children[0], str(node.value) + "("])
        elif node.kind == "postfix":
            pending.extend([")" + str(node.value), node.children[0], "("])
        elif node.kind == "binary":
            pending.extend([")", node.children[1], str(node.value), node.children[0], "("])
        elif node.kind == "call":
            pending.append(")")
            for index in range(len(node.children) - 1, -1, -1):
                pending.append(node.children[index])
                if index:
                    pending.append(",")
            pending.append(str(node.value) + "(")
        else:
            raise ValueError(f"unsupported expression node: {node.kind}")
    return "=" + "".join(pieces)


def copy_formula(formula: str, row_delta: int = 0, column_delta: int = 0) -> str:
    """Copy a formula with independently anchored row and column references."""
    return _render(parse_expression(formula), row_delta, column_delta)


def analyze_formula(formula: str) -> dict[str, Any]:
    """Return typed references and function names using the Python parser only."""
    root = parse_expression(formula)
    functions = set()
    pending = [root]
    while pending:
        node = pending.pop()
        if node.kind == "call":
            functions.add(_canonical_function_name(str(node.value)))
        pending.extend(node.children)
    return {
        "formula": formula,
        "rendered": _render(root),
        "references": expression_references(root),
        "functions": sorted(functions),
    }


@dataclass(frozen=True)
class Expression:
    """An immutable typed expression implemented independently in Python."""

    formula: str
    _root: _Node = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        root = parse_expression(self.formula)
        object.__setattr__(self, "_root", root)
        object.__setattr__(self, "formula", _render(root))

    @classmethod
    def _from_node(cls, root: _Node) -> Expression:
        _validate_tree(root)
        instance = object.__new__(cls)
        object.__setattr__(instance, "_root", root)
        object.__setattr__(instance, "formula", _render(root))
        return instance

    @classmethod
    def parse(cls, formula: str) -> Expression:
        return cls(formula)

    def render(self) -> str:
        return self.formula

    @property
    def source(self) -> str:
        return self.formula.removeprefix("=")

    @classmethod
    def literal(cls, value: int | float | str | bool | ErrorValue) -> Expression:
        if isinstance(value, (bool, str)):
            return cls._from_node(_Node("literal", value))
        if isinstance(value, ErrorValue):
            if value.code not in _ERROR_CODES:
                raise ValueError("unsupported error literal")
            return cls._from_node(_Node("literal", value))
        if type(value) in (int, float):
            try:
                if not math.isfinite(value):
                    raise ValueError("literal must be finite numeric, text, or boolean")
                token = str(value)
            except (OverflowError, ValueError) as error:
                raise ValueError("numeric literal exceeds the supported bounds") from error
            return cls._from_node(_Node("number", token))
        raise ValueError("literal must be finite numeric, text, boolean, or an Excel error")

    @classmethod
    def missing(cls) -> Expression:
        """Represent an omitted call argument, distinct from an empty string."""
        return cls._from_node(_Node("missing"))

    @classmethod
    def reference(cls, cell: CellReference) -> Expression:
        return cls._from_node(_Node("cell", (cell.render(qualified=False), cell.sheet)))

    @classmethod
    def range(cls, start: CellReference, end: CellReference) -> Expression:
        if start.sheet != end.sheet:
            raise ValueError("range endpoints must have the same sheet")
        return cls._from_node(_Node("range", (
            start.render(qualified=False), end.render(qualified=False), start.sheet,
        )))

    @classmethod
    def call(cls, name: str, *arguments: Expression | None) -> Expression:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name):
            raise ValueError("invalid function name")
        if any(argument is not None and not isinstance(argument, Expression) for argument in arguments):
            raise TypeError("call arguments must be Expression objects or None for omission")
        return cls._from_node(_Node("call", name.upper(), tuple(
            _Node("missing") if argument is None else argument._root for argument in arguments
        )))

    def binary(self, operator: str, right: Expression) -> Expression:
        if operator not in _BINARY_OPERATORS:
            raise ValueError("unsupported binary operator")
        if not isinstance(right, Expression):
            raise TypeError("binary operand must be an Expression")
        return self._from_node(_Node("binary", operator, (self._root, right._root)))

    def copy(self, *, rows: int = 0, columns: int = 0) -> Expression:
        return Expression(_render(self._root, rows, columns))

    def inspect(self) -> dict[str, Any]:
        return analyze_formula(self.formula)

    def __add__(self, right: Expression) -> Expression:
        return self.binary("+", right)

    def __sub__(self, right: Expression) -> Expression:
        return self.binary("-", right)

    def __mul__(self, right: Expression) -> Expression:
        return self.binary("*", right)

    def __truediv__(self, right: Expression) -> Expression:
        return self.binary("/", right)
