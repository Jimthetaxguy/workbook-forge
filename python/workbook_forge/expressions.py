"""Typed authoring conveniences; Rust remains the parser and transformer."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re


def _native():
    try:
        from . import _native as native
    except ImportError as error:
        raise RuntimeError("Native expression APIs require an installed Workbook Forge wheel or `pip install -e .`.") from error
    return native


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


@dataclass(frozen=True)
class Expression:
    """An immutable Excel expression validated by the native formula parser."""

    formula: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "formula", _native().copy_formula(self.formula, 0, 0))

    @property
    def source(self) -> str:
        return self.formula.removeprefix("=")

    @classmethod
    def literal(cls, value: int | float | str | bool) -> Expression:
        if isinstance(value, bool):
            return cls("TRUE" if value else "FALSE")
        if isinstance(value, str):
            return cls('"' + value.replace('"', '""') + '"')
        if type(value) in (int, float) and math.isfinite(value):
            return cls(str(value))
        raise ValueError("literal must be finite numeric, text, or boolean")

    @classmethod
    def reference(cls, cell: CellReference) -> Expression:
        return cls(cell.render())

    @classmethod
    def range(cls, start: CellReference, end: CellReference) -> Expression:
        if start.sheet != end.sheet:
            raise ValueError("range endpoints must have the same sheet")
        return cls(start.render() + ":" + end.render(qualified=False))

    @classmethod
    def call(cls, name: str, *arguments: Expression | None) -> Expression:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name):
            raise ValueError("invalid function name")
        return cls(name + "(" + ",".join("" if arg is None else arg.source for arg in arguments) + ")")

    def binary(self, operator: str, right: Expression) -> Expression:
        if operator not in {"+", "-", "*", "/", "^", "&", "=", "<>", "<", ">", "<=", ">="}:
            raise ValueError("unsupported binary operator")
        return Expression("(" + self.source + ")" + operator + "(" + right.source + ")")

    def copy(self, *, rows: int = 0, columns: int = 0) -> Expression:
        return Expression(_native().copy_formula(self.formula, rows, columns))

    def inspect(self) -> dict:
        return json.loads(_native().analyze_formula(self.formula))

    def __add__(self, right: Expression) -> Expression:
        return self.binary("+", right)

    def __sub__(self, right: Expression) -> Expression:
        return self.binary("-", right)

    def __mul__(self, right: Expression) -> Expression:
        return self.binary("*", right)

    def __truediv__(self, right: Expression) -> Expression:
        return self.binary("/", right)
