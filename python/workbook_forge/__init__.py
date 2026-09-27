"""Small, explicit Excel formula evaluator for Workbook Forge.

This module is an original implementation using only the Python standard
library. Unsupported syntax and functions return :class:`ErrorValue`.
"""

from __future__ import annotations

import calendar
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
import math
import re
from collections.abc import Iterable, Mapping
from typing import TypeAlias

MAX_FORMULA_LENGTH_UNITS = 8_192
MAX_FUNCTION_NESTING = 64
MAX_EXPRESSION_NESTING = 96
MAX_WILDCARD_WORK = 5_000_000


@dataclass(frozen=True, slots=True)
class ErrorValue:
    """An Excel-style error with an optional diagnostic for callers."""

    code: str
    message: str = ""


Scalar: TypeAlias = int | float | str | bool | None | ErrorValue


@dataclass(frozen=True, slots=True)
class ArrayValue:
    """A bounded, immutable rectangular formula result in row-major order."""

    rows: tuple[tuple[Scalar, ...], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(tuple(row) for row in self.rows))
        if not self.rows or not self.rows[0]:
            raise ValueError("an array result must have at least one row and column")
        width = len(self.rows[0])
        if any(len(row) != width for row in self.rows):
            raise ValueError("array result rows must have equal length")
        if len(self.rows) * width > 100_000:
            raise ValueError("array result exceeds the 100000-cell evaluation limit")

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.rows), len(self.rows[0]))


FormulaResult: TypeAlias = Scalar | ArrayValue


@dataclass(frozen=True, slots=True)
class FormulaReference:
    """A cell or rectangular range reference found in a parsed formula."""

    start: str
    end: str | None = None
    sheet: str | None = None


@dataclass(frozen=True, slots=True)
class FormulaAnalysis:
    """Static references and function calls in one supported formula tree."""

    references: tuple[FormulaReference, ...]
    functions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    value: str
    position: int


@dataclass(frozen=True, slots=True)
class _Node:
    kind: str
    value: object = None
    children: tuple[_Node, ...] = ()


@dataclass(frozen=True, slots=True)
class _Range:
    values: tuple[Scalar, ...]
    rows: int
    columns: int


@dataclass(frozen=True, slots=True)
class _Criterion:
    operator: str
    expected: object
    wildcard: _WildcardPattern | None = None
    blank: bool = False


@dataclass(frozen=True, slots=True)
class _WildcardToken:
    kind: str
    literal: str | None = None


@dataclass(frozen=True, slots=True)
class _WildcardPattern:
    tokens: tuple[_WildcardToken, ...]


@dataclass(slots=True)
class _WildcardBudget:
    remaining: int = MAX_WILDCARD_WORK

    def consume(self, amount: int) -> bool:
        if amount > self.remaining:
            return False
        self.remaining -= amount
        return True


_UNSET_WILDCARD = object()


_OPTIONAL_OMITTED = object()
_MAX_EXACT_INTEGER = 1 << 53
_MAX_EXACT_EXPRESSION_BITS = 65_536
_MAX_FINITE_BINARY64_INTEGER_BITS = 1024
MAX_TEXT_LENGTH_UNITS = 32_767
_ASCII_LOWER_TABLE = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_CELL_RE = re.compile(r"\$?([A-Za-z]{1,3})\$?([1-9][0-9]*)\Z")
_MAX_EXCEL_DATE_SERIAL = 2_958_465
_LEX_RE = re.compile(
    r'(?P<SPACE>\s+)'
    r'|(?P<STRING>"(?:[^"]|"")*")'
    r"|(?P<SHEET>'(?:[^']|'')*')"
    r'|(?P<ERROR>\#(?:NULL!|DIV/0!|VALUE!|REF!|NAME\?|NUM!|N/A|N/A!|SPILL!|CALC!))'
    r'|(?P<NUMBER>(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)'
    r'|(?P<CELL>\$?[A-Za-z]{1,3}\$?[1-9][0-9]*)'
    r'|(?P<IDENT>[A-Za-z_][A-Za-z0-9_.]*)'
    r'|(?P<OP><=|>=|<>|[+\-*/^%&=<>(),:!])'
)


def _parse_integer_literal(source: str) -> int | ErrorValue:
    """Parse bounded integer tokens in chunks to avoid Python's global digit cap."""
    if len(source) > 19_729:
        return ErrorValue("#NUM!", "integer literal exceeds the supported precision bound")
    value = 0
    for offset in range(0, len(source), 9):
        chunk = source[offset : offset + 9]
        value = value * (10 ** len(chunk)) + int(chunk)
        if value.bit_length() > _MAX_EXACT_EXPRESSION_BITS:
            return ErrorValue("#NUM!", "integer literal exceeds the supported precision bound")
    return value


class _FormulaSyntaxError(ValueError):
    pass


def _tokenize(source: str) -> list[_Token]:
    tokens: list[_Token] = []
    position = 0
    while position < len(source):
        match = _LEX_RE.match(source, position)
        if match is None:
            raise _FormulaSyntaxError(f"unsupported character at offset {position}")
        kind = match.lastgroup
        value = match.group()
        if kind != "SPACE":
            tokens.append(_Token(kind or "", value, position))
        position = match.end()
    tokens.append(_Token("EOF", "", len(source)))
    return tokens


class _Parser:
    def __init__(self, source: str) -> None:
        source = source.lstrip()
        units = 0
        for character in source:
            units += 2 if ord(character) > 0xFFFF else 1
            if units > MAX_FORMULA_LENGTH_UNITS:
                raise _FormulaSyntaxError(
                    f"formula exceeds Excel's {MAX_FORMULA_LENGTH_UNITS}-character limit"
                )
        self.tokens = _tokenize(source)
        self._validate_nesting()
        self.index = 0

    def _validate_nesting(self) -> None:
        stack: list[bool] = []
        function_depth = 0
        for index, token in enumerate(self.tokens):
            if token.value == "(":
                if len(stack) >= MAX_EXPRESSION_NESTING:
                    raise _FormulaSyntaxError(
                        "parenthesis nesting exceeds the Workbook Forge evaluator limit"
                    )
                is_function = index > 0 and self.tokens[index - 1].kind == "IDENT"
                if is_function:
                    function_depth += 1
                    if function_depth > MAX_FUNCTION_NESTING:
                        raise _FormulaSyntaxError(
                            f"function nesting exceeds Excel's {MAX_FUNCTION_NESTING}-level limit"
                        )
                stack.append(is_function)
            elif token.value == ")" and stack:
                if stack.pop():
                    function_depth -= 1

    @property
    def current(self) -> _Token:
        return self.tokens[self.index]

    def consume(self, value: str | None = None) -> _Token:
        token = self.current
        if value is not None and token.value != value:
            raise _FormulaSyntaxError(f"expected {value!r} at offset {token.position}")
        self.index += 1
        return token

    def parse(self) -> _Node:
        node = self.parse_comparison()
        if self.current.kind != "EOF":
            raise _FormulaSyntaxError(f"unexpected {self.current.value!r} at offset {self.current.position}")
        return node

    def parse_comparison(self) -> _Node:
        node = self.parse_concat()
        while self.current.value in ("=", "<>", "<", "<=", ">", ">="):
            op = self.consume().value
            node = _Node("binary", op, (node, self.parse_concat()))
        return node

    def parse_concat(self) -> _Node:
        node = self.parse_additive()
        while self.current.value == "&":
            self.consume()
            node = _Node("binary", "&", (node, self.parse_additive()))
        return node

    def parse_additive(self) -> _Node:
        node = self.parse_multiplicative()
        while self.current.value in ("+", "-"):
            op = self.consume().value
            node = _Node("binary", op, (node, self.parse_multiplicative()))
        return node

    def parse_multiplicative(self) -> _Node:
        node = self.parse_power()
        while self.current.value in ("*", "/"):
            op = self.consume().value
            node = _Node("binary", op, (node, self.parse_power()))
        return node

    def parse_unary(self) -> _Node:
        operators: list[str] = []
        while self.current.value in ("+", "-"):
            operators.append(self.consume().value)
        node = self.parse_postfix()
        for operator in reversed(operators):
            node = _Node("unary", operator, (node,))
        return node

    def parse_postfix(self) -> _Node:
        node = self.parse_primary()
        while self.current.value == "%":
            self.consume("%")
            node = _Node("postfix", "%", (node,))
        return node

    def parse_power(self) -> _Node:
        # Excel applies unary negation before exponentiation and evaluates
        # operators at one precedence level left-to-right (Microsoft operator
        # precedence: https://support.microsoft.com/en-us/excel/calculation-operators-and-precedence-in-excel).
        node = self.parse_unary()
        while self.current.value == "^":
            self.consume()
            node = _Node("binary", "^", (node, self.parse_unary()))
        return node

    def parse_primary(self) -> _Node:
        token = self.current
        if token.kind == "NUMBER":
            self.consume()
            number = (
                float(token.value)
                if any(c in token.value for c in ".Ee")
                else _parse_integer_literal(token.value)
            )
            return _Node("literal", number)
        if token.kind == "STRING":
            self.consume()
            return _Node("literal", token.value[1:-1].replace('""', '"'))
        if token.kind == "ERROR":
            self.consume()
            return _Node("literal", ErrorValue(token.value))
        if token.kind == "IDENT":
            if self.tokens[self.index + 1].value == "!":
                sheet = self.consume().value
                self.consume("!")
                if self.current.kind != "CELL":
                    raise _FormulaSyntaxError("sheet qualifier must be followed by a cell reference")
                address = self.consume().value
                if self.current.value == ":":
                    self.consume(":")
                    if self.current.kind != "CELL":
                        raise _FormulaSyntaxError("range endpoint must be a cell reference")
                    return _Node("range", (address, self.consume().value, sheet))
                return _Node("cell", (address, sheet))
            name = self.consume().value
            upper = name.upper()
            if upper in ("TRUE", "FALSE") and self.current.value != "(":
                return _Node("literal", upper == "TRUE")
            if self.current.value == "(":
                self.consume("(")
                arguments: list[_Node] = []
                if self.current.value != ")":
                    while True:
                        if self.current.value in (",", ")"):
                            arguments.append(_Node("missing"))
                        else:
                            arguments.append(self.parse_comparison())
                        if self.current.value != ",":
                            break
                        self.consume(",")
                        if self.current.value == ")":
                            arguments.append(_Node("missing"))
                            break
                self.consume(")")
                return _Node("call", upper, tuple(arguments))
            raise _FormulaSyntaxError(f"unsupported name {name!r}")
        if token.kind == "SHEET":
            if self.tokens[self.index + 1].value != "!":
                raise _FormulaSyntaxError("quoted sheet name must be followed by '!'")
            sheet = self.consume().value[1:-1].replace("''", "'")
            self.consume("!")
            if self.current.kind != "CELL":
                raise _FormulaSyntaxError("sheet qualifier must be followed by a cell reference")
            address = self.consume().value
            if self.current.value == ":":
                self.consume(":")
                if self.current.kind != "CELL":
                    raise _FormulaSyntaxError("range endpoint must be a cell reference")
                return _Node("range", (address, self.consume().value, sheet))
            return _Node("cell", (address, sheet))
        if token.kind == "CELL":
            address = self.consume().value
            if self.current.value == ":":
                self.consume(":")
                if self.current.kind != "CELL":
                    raise _FormulaSyntaxError("range endpoint must be a cell reference")
                return _Node("range", (address, self.consume().value, None))
            return _Node("cell", (address, None))
        if token.value == "(":
            self.consume("(")
            node = self.parse_comparison()
            self.consume(")")
            return node
        raise _FormulaSyntaxError(f"expected a value at offset {token.position}")


def analyze_formula(formula: str) -> FormulaAnalysis:
    """Parse a formula and expose its cell/range references and function names.

    Unsupported syntax raises ``ValueError`` instead of becoming an evaluator
    error value. Workbook planning uses this distinction to avoid persisting a
    syntax boundary as though Excel had calculated it.
    """

    if not isinstance(formula, str):
        raise TypeError("formula must be a string")
    source = formula.strip()
    if source.startswith("="):
        source = source[1:]
    if not source:
        raise ValueError("formula is empty")
    try:
        root = _Parser(source).parse()
    except (_FormulaSyntaxError, RecursionError) as error:
        raise ValueError(str(error)) from error

    references: list[FormulaReference] = []
    functions: list[str] = []

    pending = [root]
    while pending:
        node = pending.pop()
        if node.kind == "cell":
            address, sheet = node.value
            references.append(FormulaReference(address, None, sheet))
        elif node.kind == "range":
            start, end, sheet = node.value
            references.append(FormulaReference(start, end, sheet))
        elif node.kind == "call":
            functions.append(str(node.value))
        pending.extend(reversed(node.children))
    return FormulaAnalysis(tuple(references), tuple(functions))


def _coordinate(address: str) -> tuple[int, int]:
    match = _CELL_RE.fullmatch(address)
    if match is None:
        raise ValueError(f"invalid A1 address {address!r}")
    column = 0
    for character in match.group(1).upper():
        column = column * 26 + ord(character) - ord("A") + 1
    return int(match.group(2)), column


def _column_name(column: int) -> str:
    result = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _cell_value(address: str, cells: Mapping[str, Scalar], sheet: str | None, current_sheet: str) -> Scalar:
    match = _CELL_RE.fullmatch(address)
    assert match is not None
    key = f"{match.group(1).upper()}{match.group(2)}"
    if sheet is not None:
        return cells.get(f"{sheet.casefold()}!{key}")
    qualified_key = f"{current_sheet.casefold()}!{key}"
    return cells.get(qualified_key, cells.get(key))


def _range_value(start: str, end: str, cells: Mapping[str, Scalar], sheet: str | None, current_sheet: str) -> _Range | ErrorValue:
    try:
        start_row, start_col = _coordinate(start)
        end_row, end_col = _coordinate(end)
    except ValueError as error:
        return ErrorValue("#REF!", str(error))
    start_row, end_row = min(start_row, end_row), max(start_row, end_row)
    start_col, end_col = min(start_col, end_col), max(start_col, end_col)
    if (end_row - start_row + 1) * (end_col - start_col + 1) > 100_000:
        return ErrorValue("#NUM!", "range exceeds the 100000-cell evaluation limit")
    values = tuple(
        _cell_value(f"{_column_name(column)}{row}", cells, sheet, current_sheet) if (
            (f"{sheet.casefold()}!{_column_name(column)}{row}" in cells) if sheet is not None else
            (f"{current_sheet.casefold()}!{_column_name(column)}{row}" in cells or f"{_column_name(column)}{row}" in cells)
        ) else None
        for row in range(start_row, end_row + 1)
        for column in range(start_col, end_col + 1)
    )
    return _Range(values, end_row - start_row + 1, end_col - start_col + 1)


def _flatten(values: tuple[object, ...]) -> list[object]:
    result: list[object] = []
    for value in values:
        if isinstance(value, _Range):
            result.extend(value.values)
        elif isinstance(value, ArrayValue):
            result.extend(item for row in value.rows for item in row)
        else:
            result.append(value)
    return result


def _binary_array_parts(value: object) -> tuple[tuple[int, int], tuple[object, ...]] | None:
    if isinstance(value, ArrayValue):
        return value.shape, tuple(item for row in value.rows for item in row)
    if isinstance(value, _Range):
        return (value.rows, value.columns), value.values
    return None


def _apply_binary_values(op: str, left: object, right: object) -> object:
    left_array = _binary_array_parts(left)
    right_array = _binary_array_parts(right)
    if left_array is None and right_array is None:
        return _apply_binary_scalar(op, left, right)

    if left_array is not None and right_array is not None and left_array[0] != right_array[0]:
        return ErrorValue("#VALUE!", "array operator operands must have identical shapes")
    shape = left_array[0] if left_array is not None else right_array[0]
    assert shape is not None
    rows, columns = shape
    size = rows * columns
    left_values = left_array[1] if left_array is not None else (left,) * size
    right_values = right_array[1] if right_array is not None else (right,) * size
    results = tuple(
        _apply_binary_scalar(op, left_item, right_item)
        for left_item, right_item in zip(left_values, right_values, strict=True)
    )
    return ArrayValue(
        tuple(
            tuple(results[offset : offset + columns])
            for offset in range(0, size, columns)
        )
    )


def _apply_binary_scalar(op: str, left: object, right: object) -> Scalar | ErrorValue:
    if isinstance(left, ErrorValue):
        return left
    if op in ("=", "<>", "<", "<=", ">", ">="):
        left_number = _comparison_number(left)
        if isinstance(left_number, ErrorValue):
            return left_number
        if isinstance(right, ErrorValue):
            return right
        right_number = _comparison_number(right)
        if isinstance(right_number, ErrorValue):
            return right_number
        a, b = left, right
        if isinstance(a, str) and isinstance(b, str):
            if a.isascii() and b.isascii():
                a, b = a.lower(), b.lower()
        elif left_number is not None and right_number is not None:
            a, b = left_number, right_number
        elif op == "=":
            return False
        elif op == "<>":
            return True
        else:
            return ErrorValue("#VALUE!", "values cannot be compared")
        try:
            if op == "=":
                return a == b
            if op == "<>":
                return a != b
            if op == "<":
                return a < b
            if op == "<=":
                return a <= b
            if op == ">":
                return a > b
            return a >= b
        except TypeError:
            return ErrorValue("#VALUE!", "values cannot be compared")
    if isinstance(right, ErrorValue):
        return right
    if op == "&":
        return _join_text_bounded((_text(left), _text(right)), operation="concatenation")
    a, b = _number(left), _number(right)
    if isinstance(a, ErrorValue):
        return a
    if isinstance(b, ErrorValue):
        return b
    try:
        if op == "+":
            result = a + b
            if isinstance(result, int) and result.bit_length() > _MAX_EXACT_EXPRESSION_BITS:
                return ErrorValue("#NUM!", "integer expression exceeds the supported precision bound")
            return result
        if op == "-":
            result = a - b
            if isinstance(result, int) and result.bit_length() > _MAX_EXACT_EXPRESSION_BITS:
                return ErrorValue("#NUM!", "integer expression exceeds the supported precision bound")
            return result
        if op == "*":
            result = a * b
            if isinstance(result, int) and result.bit_length() > _MAX_EXACT_EXPRESSION_BITS:
                return ErrorValue("#NUM!", "integer expression exceeds the supported precision bound")
            return result
        if op == "/":
            return ErrorValue("#DIV/0!", "division by zero") if b == 0 else a / b
        if op == "^":
            if isinstance(a, int) and isinstance(b, int) and b >= 0 and abs(a) > 1:
                minimum_result_bits = (abs(a).bit_length() - 1) * b + 1
                if minimum_result_bits > _MAX_EXACT_EXPRESSION_BITS:
                    return ErrorValue("#NUM!", "integer power exceeds the supported precision bound")
            result = a**b
            if isinstance(result, int) and result.bit_length() > _MAX_EXACT_EXPRESSION_BITS:
                return ErrorValue("#NUM!", "integer power exceeds the supported precision bound")
            if isinstance(result, complex) or (isinstance(result, float) and not math.isfinite(result)):
                return ErrorValue("#NUM!", "power result is outside the numeric domain")
            return result
    except ZeroDivisionError:
        return ErrorValue("#NUM!", "power result is outside the numeric domain")
    except OverflowError:
        return ErrorValue("#NUM!", "numeric overflow")
    return ErrorValue("#VALUE!", f"unsupported operator {op!r}")


def _number(value: object) -> float | int | ErrorValue:
    if isinstance(value, ErrorValue):
        return value
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return 0
        # Python's float parser accepts underscores and Unicode decimal digits;
        # Rust's f64 parser does not. Keep text coercion cross-language stable.
        if not text.isascii() or "_" in text:
            return ErrorValue("#VALUE!", f"{value!r} is not numeric")
        try:
            parsed = float(text)
            return int(parsed) if parsed.is_integer() else parsed
        except ValueError:
            return ErrorValue("#VALUE!", f"{value!r} is not numeric")
    return ErrorValue("#VALUE!", "value cannot be converted to a number")


def _comparison_number(value: object) -> float | ErrorValue | None:
    if isinstance(value, str):
        return None
    normalized = _number(value)
    if isinstance(normalized, ErrorValue):
        return normalized
    try:
        number = float(normalized)
    except OverflowError:
        return ErrorValue("#NUM!", "comparison operand exceeds the finite binary64 range")
    if not math.isfinite(number):
        return ErrorValue("#NUM!", "comparison operand exceeds the finite binary64 range")
    return number


def _nonfinite_literal_error(node: _Node) -> ErrorValue | None:
    """Return the evaluation error for an overflowing literal operand."""
    while node.kind in {"unary", "postfix"} and node.children:
        node = node.children[0]
    if (
        node.kind == "literal"
        and isinstance(node.value, float)
        and not math.isfinite(node.value)
    ):
        return ErrorValue("#NUM!", "numeric literal exceeds the finite binary64 range")
    return None


def _sequence_call(
    arguments: tuple[_Node, ...],
    cells: Mapping[str, Scalar],
    sheet_name: str,
    budget: _WildcardBudget,
) -> ArrayValue | ErrorValue:
    if not 1 <= len(arguments) <= 4:
        return _arity("SEQUENCE", "1 through 4", len(arguments))
    if arguments[0].kind == "missing" and not any(
        argument.kind != "missing" for argument in arguments[1:]
    ):
        return ErrorValue("#VALUE!", "omitted rows require another SEQUENCE argument")

    evaluated: list[object] = []
    for argument in arguments:
        if argument.kind == "missing":
            evaluated.append(None)
            continue
        value = _eval(argument, cells, sheet_name, budget)
        if isinstance(value, ErrorValue):
            return value
        # Rust rejects a non-finite numeric literal while evaluating that
        # argument. Keep the same left-to-right error precedence here.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                numeric_value = float(value)
            except OverflowError:
                return ErrorValue("#NUM!", "SEQUENCE argument is outside the finite numeric range")
            if not math.isfinite(numeric_value):
                return ErrorValue("#NUM!", "SEQUENCE arguments must be finite")
        evaluated.append(value)

    numeric: list[float | None] = []
    for value in evaluated:
        if value is None:
            numeric.append(None)
            continue
        coerced = _number(value)
        if isinstance(coerced, ErrorValue):
            return coerced
        try:
            number = float(coerced)
        except OverflowError:
            return ErrorValue("#NUM!", "SEQUENCE argument is outside the finite numeric range")
        if not math.isfinite(number):
            return ErrorValue("#NUM!", "SEQUENCE arguments must be finite")
        numeric.append(number)

    rows_number = 1.0 if not numeric or numeric[0] is None else numeric[0]
    columns_number = 1.0 if len(numeric) < 2 or numeric[1] is None else numeric[1]
    start = 1.0 if len(numeric) < 3 or numeric[2] is None else numeric[2]
    step = 1.0 if len(numeric) < 4 or numeric[3] is None else numeric[3]
    rows_truncated = math.trunc(rows_number)
    columns_truncated = math.trunc(columns_number)
    if rows_truncated <= 0 or columns_truncated <= 0:
        return ErrorValue("#NUM!", "SEQUENCE dimensions must be positive in this evaluator profile")
    if rows_truncated > 100_000 or columns_truncated > 100_000 or rows_truncated * columns_truncated > 100_000:
        return ErrorValue("#NUM!", "SEQUENCE exceeds the 100000-cell evaluation limit")

    result_rows: list[tuple[Scalar, ...]] = []
    for row_index in range(rows_truncated):
        row: list[Scalar] = []
        for column_index in range(columns_truncated):
            index = row_index * columns_truncated + column_index
            value = start + index * step
            if not math.isfinite(value):
                return ErrorValue("#NUM!", "SEQUENCE value is outside the finite numeric range")
            row.append(value)
        result_rows.append(tuple(row))
    return ArrayValue(tuple(result_rows))


def _filter_matrix(value: object) -> tuple[tuple[Scalar, ...], ...]:
    if isinstance(value, ArrayValue):
        return value.rows
    if isinstance(value, _Range):
        return tuple(
            value.values[offset : offset + value.columns]
            for offset in range(0, value.rows * value.columns, value.columns)
        )
    return ((value,),)  # type: ignore[return-value]


def _sort_text_key(value: str) -> str:
    """Fold ASCII letters consistently while keeping every Unicode character exact."""
    return value.translate(_ASCII_LOWER_TABLE)


def _formula_array_result(rows: tuple[tuple[Scalar, ...], ...]) -> ArrayValue | ErrorValue:
    normalized: list[tuple[Scalar, ...]] = []
    for row in rows:
        output: list[Scalar] = []
        for value in row:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                output.append(value)
                continue
            try:
                number = float(value)
            except OverflowError:
                return ErrorValue("#NUM!", "array numeric values must be finite binary64 values")
            if not math.isfinite(number):
                return ErrorValue("#NUM!", "array numeric values must be finite binary64 values")
            output.append(number)
        normalized.append(tuple(output))
    return ArrayValue(tuple(normalized))


def _sort_call(
    arguments: tuple[_Node, ...],
    cells: Mapping[str, Scalar],
    sheet_name: str,
    budget: _WildcardBudget,
) -> ArrayValue | ErrorValue:
    if not 1 <= len(arguments) <= 4:
        return _arity("SORT", "1 through 4", len(arguments))
    source = _eval(arguments[0], cells, sheet_name, budget)
    if isinstance(source, ErrorValue):
        return source
    matrix = _filter_matrix(source)

    def optional(index: int, default: object) -> object | ErrorValue:
        if index >= len(arguments) or arguments[index].kind == "missing":
            return default
        value = _eval(arguments[index], cells, sheet_name, budget)
        if isinstance(value, ErrorValue):
            return value
        if isinstance(value, (_Range, ArrayValue)):
            return ErrorValue("#VALUE!", "SORT selectors must be scalar")
        return value

    sort_index_value = optional(1, 1)
    sort_order_value = optional(2, 1)
    by_col_value = optional(3, False)
    for value in (sort_index_value, sort_order_value, by_col_value):
        if isinstance(value, ErrorValue):
            return value

    def selector(value: object, name: str) -> int | ErrorValue:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return ErrorValue("#VALUE!", f"SORT {name} must be an integer selector")
        try:
            number = float(value)
        except OverflowError:
            return ErrorValue("#VALUE!", f"SORT {name} must be an integer selector")
        if not math.isfinite(number) or not number.is_integer():
            return ErrorValue("#VALUE!", f"SORT {name} must be an integer selector")
        return int(number)

    sort_index = selector(sort_index_value, "sort_index")
    sort_order = selector(sort_order_value, "sort_order")
    if isinstance(sort_index, ErrorValue):
        return sort_index
    if isinstance(sort_order, ErrorValue):
        return sort_order
    if sort_order not in (1, -1):
        return ErrorValue("#VALUE!", "SORT sort_order must be 1 or -1")
    if not isinstance(by_col_value, bool):
        return ErrorValue("#VALUE!", "SORT by_col must be a logical value")

    rows, columns = len(matrix), len(matrix[0])
    key_limit = rows if by_col_value else columns
    if not 1 <= sort_index <= key_limit:
        return ErrorValue("#VALUE!", "SORT sort_index is outside the selected axis")
    keys = (
        tuple(matrix[sort_index - 1][column] for column in range(columns))
        if by_col_value
        else tuple(matrix[row][sort_index - 1] for row in range(rows))
    )
    key_error = next((value for value in keys if isinstance(value, ErrorValue)), None)
    if key_error is not None:
        return key_error

    numeric_keys: list[float] = []
    text_keys: list[str] = []
    key_kind: str | None = None
    for value in keys:
        if isinstance(value, bool):
            return ErrorValue("#VALUE!", "SORT supports finite numeric or text sort keys")
        if isinstance(value, (int, float)):
            try:
                number = float(value)
            except OverflowError:
                return ErrorValue("#NUM!", "SORT numeric keys must be finite binary64 values")
            if not math.isfinite(number):
                return ErrorValue("#NUM!", "SORT numeric keys must be finite binary64 values")
            if key_kind not in (None, "number"):
                return ErrorValue("#VALUE!", "SORT keys must have one consistent value type")
            key_kind = "number"
            numeric_keys.append(number)
        elif isinstance(value, str):
            if key_kind not in (None, "text"):
                return ErrorValue("#VALUE!", "SORT keys must have one consistent value type")
            key_kind = "text"
            text_keys.append(_sort_text_key(value))
        else:
            return ErrorValue("#VALUE!", "SORT supports finite numeric or text sort keys")

    order = list(range(len(keys)))
    if key_kind == "number":
        order.sort(key=lambda index: numeric_keys[index], reverse=sort_order == -1)
    else:
        order.sort(key=lambda index: text_keys[index], reverse=sort_order == -1)
    if by_col_value:
        result = tuple(tuple(row[index] for index in order) for row in matrix)
    else:
        result = tuple(matrix[index] for index in order)
    return _formula_array_result(result)


def _unique_cell_key(value: Scalar) -> tuple[object, ...] | ErrorValue:
    if isinstance(value, ErrorValue):
        return value
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except OverflowError:
            return ErrorValue("#NUM!", "UNIQUE numeric values must be finite binary64 values")
        if not math.isfinite(number):
            return ErrorValue("#NUM!", "UNIQUE numeric values must be finite binary64 values")
        return ("number", 0.0 if number == 0.0 else number)
    if isinstance(value, str):
        return ("text-ascii", value.lower()) if value.isascii() else ("text-unicode", value)
    return ("blank",)


def _unique_call(
    arguments: tuple[_Node, ...],
    cells: Mapping[str, Scalar],
    sheet_name: str,
    budget: _WildcardBudget,
) -> ArrayValue | ErrorValue:
    if not 1 <= len(arguments) <= 3:
        return _arity("UNIQUE", "1 through 3", len(arguments))
    source = _eval(arguments[0], cells, sheet_name, budget)
    if isinstance(source, ErrorValue):
        return source
    matrix = _filter_matrix(source)

    def optional(index: int, default: bool) -> bool | ErrorValue:
        if index >= len(arguments) or arguments[index].kind == "missing":
            return default
        value = _eval(arguments[index], cells, sheet_name, budget)
        if isinstance(value, ErrorValue):
            return value
        if isinstance(value, (_Range, ArrayValue)) or not isinstance(value, bool):
            return ErrorValue("#VALUE!", "UNIQUE selectors must be logical values")
        return value

    by_col = optional(1, False)
    exactly_once = optional(2, False)
    if isinstance(by_col, ErrorValue):
        return by_col
    if isinstance(exactly_once, ErrorValue):
        return exactly_once

    for row in matrix:
        for value in row:
            if isinstance(value, ErrorValue):
                return value

    record_count = len(matrix[0]) if by_col else len(matrix)
    keys: list[tuple[tuple[object, ...], ...]] = []
    counts: dict[tuple[tuple[object, ...], ...], int] = {}
    for record_index in range(record_count):
        values = (
            tuple(matrix[row][record_index] for row in range(len(matrix)))
            if by_col
            else matrix[record_index]
        )
        record_key: list[tuple[object, ...]] = []
        for value in values:
            key = _unique_cell_key(value)
            if isinstance(key, ErrorValue):
                return key
            record_key.append(key)
        frozen_key = tuple(record_key)
        keys.append(frozen_key)
        counts[frozen_key] = counts.get(frozen_key, 0) + 1

    selected: list[int] = []
    seen: set[tuple[tuple[object, ...], ...]] = set()
    for index, key in enumerate(keys):
        if exactly_once:
            if counts[key] == 1:
                selected.append(index)
        elif key not in seen:
            selected.append(index)
            seen.add(key)
    if not selected:
        return ErrorValue("#CALC!", "UNIQUE produced no records in this evaluator profile")
    if by_col:
        result = tuple(tuple(row[index] for index in selected) for row in matrix)
    else:
        result = tuple(matrix[index] for index in selected)
    return _formula_array_result(result)


def _filter_call(
    arguments: tuple[_Node, ...],
    cells: Mapping[str, Scalar],
    sheet_name: str,
    budget: _WildcardBudget,
) -> object:
    if not 2 <= len(arguments) <= 3:
        return _arity("FILTER", "2 or 3", len(arguments))
    source = _eval(arguments[0], cells, sheet_name, budget)
    if isinstance(source, ErrorValue):
        return source
    include = _eval(arguments[1], cells, sheet_name, budget)
    if isinstance(include, ErrorValue):
        return include
    source_rows = _filter_matrix(source)
    include_rows = _filter_matrix(include)
    row_count, column_count = len(source_rows), len(source_rows[0])
    include_height, include_width = len(include_rows), len(include_rows[0])

    if include_height == row_count and include_width == 1:
        axis = "rows"
        mask_values = tuple(row[0] for row in include_rows)
    elif include_height == 1 and include_width == column_count:
        axis = "columns"
        mask_values = include_rows[0]
    else:
        return ErrorValue(
            "#VALUE!",
            "FILTER include must be a matching one-dimensional row or column mask",
        )

    mask: list[bool] = []
    for value in mask_values:
        selected = _truth(value)
        if isinstance(selected, ErrorValue):
            return selected
        mask.append(selected)
    selected_indices = [index for index, selected in enumerate(mask) if selected]
    if not selected_indices:
        if len(arguments) == 2:
            return ErrorValue("#CALC!", "FILTER returned no values and has no if_empty fallback")
        fallback = arguments[2]
        if fallback.kind == "missing":
            return None
        return _eval(fallback, cells, sheet_name, budget)

    if axis == "rows":
        result = tuple(source_rows[index] for index in selected_indices)
    else:
        result = tuple(
            tuple(row[index] for index in selected_indices)
            for row in source_rows
        )
    try:
        return ArrayValue(result)
    except ValueError as error:
        return ErrorValue("#NUM!", str(error))


def _switch_equal(left: object, right: object) -> bool | ErrorValue:
    """Compare scalar SWITCH values under the evaluator's explicit type profile."""
    if isinstance(left, ErrorValue):
        return left
    if isinstance(right, ErrorValue):
        return right
    if isinstance(left, (_Range, ArrayValue)) or isinstance(right, (_Range, ArrayValue)):
        return ErrorValue("#VALUE!", "SWITCH values must be scalar")
    if isinstance(left, bool) and isinstance(right, bool):
        return left is right
    if (
        isinstance(left, (int, float)) and not isinstance(left, bool)
        and isinstance(right, (int, float)) and not isinstance(right, bool)
    ):
        return left == right
    if isinstance(left, str) and isinstance(right, str):
        if left.isascii() and right.isascii():
            return left.lower() == right.lower()
        return left == right
    return left is None and right is None


def _decimal_digits(value: object) -> int | ErrorValue:
    """Convert a precision argument to the evaluator's bounded decimal-place count."""
    number = _number(value)
    if isinstance(number, ErrorValue):
        return number
    if isinstance(number, int):
        if abs(number) > 308:
            return ErrorValue("#NUM!", "num_digits must be between -308 and 308")
        return number
    if not math.isfinite(number):
        return ErrorValue("#NUM!", "num_digits must be finite")
    digits = math.trunc(number)
    if abs(digits) > 308:
        return ErrorValue("#NUM!", "num_digits must be between -308 and 308")
    return digits


def _round_to_precision(number: object, digits: int, mode: str) -> float | ErrorValue:
    """Round a numeric value at a power-of-ten precision using Excel's directed modes."""
    try:
        numeric = float(number)
    except (OverflowError, TypeError, ValueError):
        return ErrorValue("#NUM!", "number is outside the supported numeric range")
    if not math.isfinite(numeric):
        return ErrorValue("#NUM!", "number must be finite")
    try:
        scale = 10.0**digits
    except OverflowError:
        return ErrorValue("#NUM!", "num_digits is outside the supported numeric range")
    if not math.isfinite(scale) or scale == 0:
        return ErrorValue("#NUM!", "num_digits is outside the supported numeric range")
    scaled = numeric * scale
    # At large magnitudes, positive decimal precision cannot change a binary float.
    if math.isinf(scaled) and digits > 0:
        return numeric
    if not math.isfinite(scaled):
        return ErrorValue("#NUM!", "rounded number is outside the supported numeric range")
    if mode == "nearest":
        rounded = math.copysign(math.floor(abs(scaled) + 0.5), scaled)
    elif mode == "away-from-zero":
        rounded = math.copysign(math.ceil(abs(scaled)), scaled)
    else:
        rounded = math.trunc(scaled)
    result = rounded / scale
    if not math.isfinite(result):
        return ErrorValue("#NUM!", "rounded number is outside the supported numeric range")
    return result


def _round_to_parity(value: object, odd: bool) -> float | int | ErrorValue:
    """Round a scalar away from zero to an even or odd integer."""
    number = _number(value)
    if isinstance(number, ErrorValue):
        return number
    try:
        numeric = float(number)
    except (OverflowError, TypeError, ValueError):
        return ErrorValue("#NUM!", "number is outside the supported numeric range")
    if not math.isfinite(numeric):
        return ErrorValue("#NUM!", "number must be finite")

    magnitude = abs(numeric)
    if magnitude >= _MAX_EXACT_INTEGER:
        if odd:
            return ErrorValue("#NUM!", "the next odd integer is not exactly representable")
        # Binary64 values at this magnitude are already even integers.
        return numeric

    rounded = math.ceil(magnitude)
    wanted_parity = 1 if odd else 0
    if rounded % 2 != wanted_parity:
        rounded += 1
    return -rounded if numeric < 0 else rounded


def _test_integer_parity(value: object, odd: bool) -> bool | ErrorValue:
    """Test numeric inputs after truncating their fractional part toward zero."""
    if isinstance(value, ErrorValue):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ErrorValue("#VALUE!", "ISEVEN and ISODD require a numeric value")
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        return ErrorValue("#NUM!", "number is outside the supported numeric range")
    if not math.isfinite(numeric):
        return ErrorValue("#NUM!", "number must be finite")
    truncated = math.trunc(numeric)
    return (truncated % 2 == 1) if odd else (truncated % 2 == 0)


def _trunc_call(
    arguments: tuple[_Node, ...],
    cells: Mapping[str, Scalar],
    sheet_name: str,
    budget: _WildcardBudget,
) -> object:
    """Evaluate TRUNC while preserving its omitted optional precision argument."""
    if not 1 <= len(arguments) <= 2:
        return _arity("TRUNC", "1 or 2", len(arguments))
    number = _number(_eval(arguments[0], cells, sheet_name, budget))
    if isinstance(number, ErrorValue):
        return number
    digits: int | ErrorValue
    if len(arguments) == 1 or arguments[1].kind == "missing":
        digits = 0
    else:
        digits = _decimal_digits(_eval(arguments[1], cells, sheet_name, budget))
    if isinstance(digits, ErrorValue):
        return digits
    return _round_to_precision(number, digits, "toward-zero")


def _text(value: object) -> str | ErrorValue:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        try:
            value = float(value)
        except OverflowError:
            return ErrorValue("#NUM!", "number is outside the supported numeric range")
        if not math.isfinite(value):
            return ErrorValue("#NUM!", "number is outside the supported numeric range")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _excel_text_length_units(value: str) -> int:
    """Count UTF-16 code units, the unit used by Excel's cell text limit."""
    return len(value) + sum(ord(character) > 0xFFFF for character in value)


def _text_length_error(operation: str) -> ErrorValue:
    return ErrorValue(
        "#VALUE!",
        f"{operation} exceeds Excel's {MAX_TEXT_LENGTH_UNITS}-UTF-16-unit text limit",
    )


def _join_text_bounded(
    pieces: Iterable[str | ErrorValue], delimiter: str = "", operation: str = "formula text result"
) -> str | ErrorValue:
    """Check the final UTF-16 size before allocating a joined string."""
    output: list[str] = []
    units = 0
    delimiter_units = _excel_text_length_units(delimiter)
    for piece in pieces:
        if isinstance(piece, ErrorValue):
            return piece
        if output:
            units += delimiter_units
        units += _excel_text_length_units(piece)
        if units > MAX_TEXT_LENGTH_UNITS:
            return _text_length_error(operation)
        output.append(piece)
    return delimiter.join(output)


def _check_text_result(value: object) -> FormulaResult:
    """Reject an over-limit scalar or array string at the evaluator boundary."""
    if isinstance(value, str):
        return (
            _text_length_error("formula text result")
            if _excel_text_length_units(value) > MAX_TEXT_LENGTH_UNITS
            else value
        )
    if isinstance(value, ArrayValue):
        for row in value.rows:
            for item in row:
                if isinstance(item, str) and _excel_text_length_units(item) > MAX_TEXT_LENGTH_UNITS:
                    return _text_length_error("formula array text result")
    return value  # type: ignore[return-value]


def _substitute_bounded(
    text: str, old: str, new: str, instance: int | None = None
) -> str | ErrorValue:
    """Preflight SUBSTITUTE output size before Python creates replacement text."""
    if old == "":
        return ErrorValue("#VALUE!", "SUBSTITUTE old_text must not be empty in this evaluator")
    occurrences = text.count(old)
    if instance is not None and instance > occurrences:
        return text
    replaced = occurrences if instance is None else 1
    result_units = (
        _excel_text_length_units(text)
        - replaced * _excel_text_length_units(old)
        + replaced * _excel_text_length_units(new)
    )
    if result_units > MAX_TEXT_LENGTH_UNITS:
        return _text_length_error("SUBSTITUTE result")
    if instance is None:
        return text.replace(old, new)
    start = 0
    for _ in range(instance):
        found = text.find(old, start)
        if found < 0:
            return text
        start = found + len(old)
    return text[:found] + new + text[found + len(old) :]


def _truth(value: object) -> bool | ErrorValue:
    if isinstance(value, ErrorValue):
        return value
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        if value == "":
            return False
        return ErrorValue("#VALUE!", "text is not accepted as a logical value")
    return ErrorValue("#VALUE!", "value is not logical")


def _numeric_values(values: list[object], *, include_text: bool = False) -> list[float | int] | ErrorValue:
    result: list[float | int] = []
    for value in values:
        if isinstance(value, ErrorValue):
            return value
        if value is None:
            continue
        if isinstance(value, bool):
            if include_text:
                result.append(int(value))
            continue
        if isinstance(value, (int, float)):
            result.append(value)
        elif include_text and isinstance(value, str):
            converted = _number(value)
            if isinstance(converted, ErrorValue):
                return converted
            result.append(converted)
    return result


def _date_to_excel_serial(value: date) -> int:
    serial = (value - date(1899, 12, 31)).days
    return serial + (1 if value >= date(1900, 3, 1) else 0)


def _excel_serial_ymd(serial: float) -> tuple[int, int, int]:
    day_index = math.floor(serial)
    if day_index < 0 or day_index > _MAX_EXCEL_DATE_SERIAL:
        raise OverflowError("date serial is outside Excel's supported range")
    if day_index == 60:
        return 1900, 2, 29
    if day_index >= 61:
        value = date(1899, 12, 31) + timedelta(days=day_index - 1)
    else:
        value = date(1899, 12, 31) + timedelta(days=day_index)
    return value.year, value.month, value.day


def _day_count_serial(value: object, *, error_code: str) -> int | ErrorValue:
    """Truncate a day-count date toward zero and validate the Excel serial range."""
    number = _number(value)
    if isinstance(number, ErrorValue):
        return number
    if isinstance(number, float):
        if not math.isfinite(number):
            return ErrorValue(error_code, "date serial must be finite")
        serial = math.trunc(number)
    else:
        serial = int(number)
    if not 0 <= serial <= _MAX_EXCEL_DATE_SERIAL:
        return ErrorValue(error_code, "date serial is outside the supported range")
    return serial


def _coupon_serial_ymd(serial: int) -> tuple[int, int, int]:
    """Convert an Excel day serial to Y-M-D, including prior coupon dates below zero."""
    if serial >= 0:
        return _excel_serial_ymd(serial)
    value = date(1899, 12, 31) + timedelta(days=serial)
    return value.year, value.month, value.day


def _coupon_ymd_serial(year: int, month: int, day: int) -> int:
    """Convert an internal coupon date to a serial without the public lower bound."""
    if (year, month, day) == (1900, 2, 29):
        return 60
    value = date(year, month, day)
    serial = (value - date(1899, 12, 31)).days
    return serial + (1 if value >= date(1900, 3, 1) else 0)


@dataclass(frozen=True, slots=True)
class _CouponSchedule:
    previous: int
    previous_strict: int
    next: int
    coupons_remaining: int


def _coupon_schedule(settlement: int, maturity: int, frequency: int) -> _CouponSchedule:
    """Build a maturity-anchored coupon schedule under Workbook Forge' date profile."""
    settlement_year, settlement_month, _ = _excel_serial_ymd(settlement)
    maturity_year, maturity_month, maturity_day = _excel_serial_ymd(maturity)
    period_months = 12 // frequency
    maturity_month_index = maturity_year * 12 + maturity_month - 1
    settlement_month_index = settlement_year * 12 + settlement_month - 1
    maturity_is_month_end = maturity_day == _days_in_month(maturity_year, maturity_month)

    def coupon_date(periods_back: int) -> int:
        month_index = maturity_month_index - periods_back * period_months
        year, month_zero = divmod(month_index, 12)
        month = month_zero + 1
        last_day = _days_in_month(year, month)
        day = last_day if maturity_is_month_end else min(maturity_day, last_day)
        return _coupon_ymd_serial(year, month, day)

    periods_back = (maturity_month_index - settlement_month_index) // period_months
    if coupon_date(periods_back) > settlement:
        periods_back += 1
    previous = coupon_date(periods_back)
    return _CouponSchedule(
        previous=previous,
        previous_strict=coupon_date(periods_back + 1) if previous == settlement else previous,
        next=coupon_date(periods_back - 1),
        coupons_remaining=periods_back,
    )


def _days360_serials(start_serial: int, end_serial: int, *, european: bool) -> int:
    """Apply the DAYS360 US/NASD or European endpoint adjustments."""
    sign = 1
    if start_serial > end_serial:
        start_serial, end_serial = end_serial, start_serial
        sign = -1
    start_year, start_month, start_day = _coupon_serial_ymd(start_serial)
    end_year, end_month, end_day = _coupon_serial_ymd(end_serial)
    if european:
        if start_day == 31:
            start_day = 30
        if end_day == 31:
            end_day = 30
    else:
        if start_day == _days_in_month(start_year, start_month):
            start_day = 30
        if end_day == _days_in_month(end_year, end_month):
            if start_day < 30:
                if end_month == 12:
                    end_year, end_month = end_year + 1, 1
                else:
                    end_month += 1
                end_day = 1
            else:
                end_day = 30
    result = 360 * (end_year - start_year) + 30 * (end_month - start_month) + end_day - start_day
    return sign * result


def _yearfrac_actual_actual(start_serial: int, end_serial: int) -> float:
    """Sum signed day segments over their Excel calendar-year denominators."""
    if start_serial == end_serial:
        return 0.0
    sign = 1.0
    if start_serial > end_serial:
        start_serial, end_serial = end_serial, start_serial
        sign = -1.0
    total = 0.0
    cursor = start_serial
    while cursor < end_serial:
        year, _, _ = _excel_serial_ymd(cursor)
        if year == 1899:
            next_year = 1900
            year_length = 365
        elif year == 1900:
            next_year = 1901
            year_length = 366
        else:
            next_year = year + 1
            year_length = 366 if _is_excel_leap_year(year) else 365
        boundary = (
            _date_to_excel_serial(date(next_year, 1, 1))
            if next_year <= 9999
            else _MAX_EXCEL_DATE_SERIAL + 1
        )
        segment_end = min(end_serial, boundary)
        total += (segment_end - cursor) / year_length
        cursor = segment_end
    return sign * total


def _is_excel_leap_year(year: int) -> bool:
    """Return the Gregorian leap-year rule, with Excel's fictional 1900 leap day."""
    return year == 1900 or (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))


def _excel_time_hms(serial: float) -> tuple[int, int, int]:
    if not math.isfinite(serial):
        raise ValueError("date-time serial must be finite")
    _excel_serial_ymd(serial)
    elapsed_seconds = (serial - math.floor(serial)) * 86_400.0
    nearest_second = round(elapsed_seconds)
    # Correct at most half a representable serial step before truncating seconds.
    tolerance = math.ulp(serial) * 43_200.0
    if abs(elapsed_seconds - nearest_second) <= tolerance:
        elapsed_seconds = float(nearest_second)
    second_of_day = math.floor(elapsed_seconds) % 86_400
    return second_of_day // 3_600, (second_of_day // 60) % 60, second_of_day % 60


def _week_selector(value: object) -> int | ErrorValue:
    """Coerce a WEEKDAY/WEEKNUM return_type under the evaluator profile."""
    number = _number(value)
    if isinstance(number, ErrorValue):
        return number
    try:
        finite = math.isfinite(number)
    except OverflowError:
        finite = False
    if not finite:
        return ErrorValue("#NUM!", "week selector must be finite")
    # Microsoft documents allowed integers but not fractional selector coercion;
    # this evaluator profile truncates toward zero.
    return math.trunc(number)


def _excel_sunday_zero_weekday(serial_day: int) -> int:
    # Excel's 1900-system serial arithmetic intentionally preserves the
    # historical one-day WEEKDAY offset before March 1, 1900.
    return (serial_day - 1) % 7


def _excel_day_of_year(serial_day: int, year: int, month: int, day: int) -> int:
    if year == 1900:
        # Serial 60 is the fictional leap day; keep the full serial position.
        return serial_day
    return (date(year, month, day) - date(year, 1, 1)).days + 1


def _excel_jan1_sunday_zero(year: int) -> int:
    if year == 1900:
        return 0  # Serial 1, under Excel's historical weekday sequence.
    if year >= 1901:
        return (_date_to_excel_serial(date(year, 1, 1)) - 1) % 7
    return (date(year, 1, 1).weekday() + 1) % 7


def _iso_weeks_in_year(year: int) -> int:
    jan1_monday_zero = (_excel_jan1_sunday_zero(year) + 6) % 7
    leap_year = year == 1900 or calendar.isleap(year)
    return 53 if jan1_monday_zero == 3 or (jan1_monday_zero == 2 and leap_year) else 52


def _excel_iso_week_number(year: int, day_of_year: int, weekday_sunday_zero: int) -> int:
    weekday_monday_zero = (weekday_sunday_zero + 6) % 7
    week = (day_of_year - weekday_monday_zero + 9) // 7
    if week < 1:
        return _iso_weeks_in_year(year - 1)
    if week > _iso_weeks_in_year(year):
        return 1
    return week


_STANDARD_WEEKEND = (False, False, False, False, False, True, True)


def _workday_date_serial(value: object, *, out_of_range_code: str = "#VALUE!") -> int | ErrorValue:
    number = _number(value)
    if isinstance(number, ErrorValue):
        return number
    try:
        finite = math.isfinite(number)
    except OverflowError:
        finite = False
    if not finite:
        return ErrorValue("#NUM!", "date argument must be finite")
    serial_day = math.floor(number)
    if not 0 <= serial_day <= _MAX_EXCEL_DATE_SERIAL:
        return ErrorValue(out_of_range_code, "date argument is outside the supported range")
    return serial_day


def _workday_holidays(value: object, *, out_of_range_code: str = "#VALUE!") -> set[int] | ErrorValue:
    if isinstance(value, ErrorValue):
        return value
    is_range = isinstance(value, _Range)
    values = value.values if is_range else (value,)
    holidays: set[int] = set()
    for item in values:
        if item is None or item == "":
            continue
        # Text inside a referenced holidays range is ignored as a non-date cell.
        if is_range and isinstance(item, str):
            continue
        serial_day = _workday_date_serial(item, out_of_range_code=out_of_range_code)
        if isinstance(serial_day, ErrorValue):
            return serial_day
        holidays.add(serial_day)
    return holidays


def _is_nonworking_day(serial_day: int, weekend: tuple[bool, ...]) -> bool:
    monday_first_weekday = (_excel_sunday_zero_weekday(serial_day) + 6) % 7
    return weekend[monday_first_weekday]


def _weekend_mask(value: object) -> tuple[bool, ...] | ErrorValue:
    if isinstance(value, str):
        if len(value) != 7 or any(character not in "01" for character in value):
            return ErrorValue("#VALUE!", "weekend mask must contain exactly seven 0/1 characters")
        return tuple(character == "1" for character in value)
    if isinstance(value, _Range):
        return ErrorValue("#VALUE!", "weekend must be a scalar code or mask")
    number = _number(value)
    if isinstance(number, ErrorValue):
        return number
    try:
        finite = math.isfinite(number)
    except OverflowError:
        finite = False
    if not finite or math.trunc(number) != number:
        return ErrorValue("#NUM!", "weekend code must be a supported integer")
    code = int(number)
    if 1 <= code <= 7:
        first_weekend_day = (code - 3) % 7
        return tuple(index in {first_weekend_day, (first_weekend_day + 1) % 7} for index in range(7))
    if 11 <= code <= 17:
        weekend_day = (code - 12) % 7
        return tuple(index == weekend_day for index in range(7))
    return ErrorValue("#NUM!", "weekend code must be 1-7 or 11-17")


def _business_days_in_range(
    first_day: int,
    last_day: int,
    holidays: list[int],
    weekend: tuple[bool, ...] = _STANDARD_WEEKEND,
) -> int:
    if first_day > last_day:
        return 0
    length = last_day - first_day + 1
    full_weeks, remaining_days = divmod(length, 7)
    count = full_weeks * (7 - sum(weekend))
    first_remainder_day = first_day + full_weeks * 7
    for offset in range(remaining_days):
        if not _is_nonworking_day(first_remainder_day + offset, weekend):
            count += 1
    first_holiday = bisect_left(holidays, first_day)
    after_last_holiday = bisect_right(holidays, last_day)
    return count - (after_last_holiday - first_holiday)


def _weekday_holidays(holidays: set[int], weekend: tuple[bool, ...] = _STANDARD_WEEKEND) -> list[int]:
    return sorted(holiday for holiday in holidays if not _is_nonworking_day(holiday, weekend))


def _networkdays_count(
    start_day: int,
    end_day: int,
    holidays: set[int],
    weekend: tuple[bool, ...] = _STANDARD_WEEKEND,
) -> int:
    direction = 1 if start_day <= end_day else -1
    first_day, last_day = sorted((start_day, end_day))
    return direction * _business_days_in_range(first_day, last_day, _weekday_holidays(holidays, weekend), weekend)


def _workday_result(
    start_day: int,
    offset: int,
    holidays: set[int],
    weekend: tuple[bool, ...] = _STANDARD_WEEKEND,
) -> int | None:
    if offset == 0:
        return start_day
    direction = 1 if offset > 0 else -1
    maximum_distance = _MAX_EXCEL_DATE_SERIAL - start_day if direction > 0 else start_day
    if maximum_distance == 0:
        return None
    holiday_days = _weekday_holidays(holidays, weekend)

    def count_at_distance(distance: int) -> int:
        if direction > 0:
            return _business_days_in_range(start_day + 1, start_day + distance, holiday_days, weekend)
        return _business_days_in_range(start_day - distance, start_day - 1, holiday_days, weekend)

    target = abs(offset)
    if count_at_distance(maximum_distance) < target:
        return None
    low, high = 1, maximum_distance
    while low < high:
        middle = (low + high) // 2
        if count_at_distance(middle) >= target:
            high = middle
        else:
            low = middle + 1
    return start_day + direction * low


def _working_day_call(
    name: str,
    arguments: tuple[_Node, ...],
    cells: Mapping[str, Scalar],
    sheet_name: str,
    budget: _WildcardBudget,
) -> object:
    international = name.endswith(".INTL")
    is_workday = name.startswith("WORKDAY")
    maximum_arity = 4 if international else 3
    if not 2 <= len(arguments) <= maximum_arity:
        return _arity(name, f"2 or {maximum_arity}", len(arguments))

    start_value = _eval(arguments[0], cells, sheet_name, budget)
    if isinstance(start_value, ErrorValue):
        return start_value
    if isinstance(start_value, _Range):
        return ErrorValue("#VALUE!", f"{name} date arguments must be scalar")
    date_range_error = "#NUM!" if international else "#VALUE!"
    start_day = _workday_date_serial(start_value, out_of_range_code=date_range_error)
    if isinstance(start_day, ErrorValue):
        return start_day

    second_value = _eval(arguments[1], cells, sheet_name, budget)
    if isinstance(second_value, ErrorValue):
        return second_value
    if isinstance(second_value, _Range):
        return ErrorValue("#VALUE!", f"{name} date/day arguments must be scalar")

    if is_workday:
        days = _number(second_value)
        if isinstance(days, ErrorValue):
            return days
        try:
            finite = math.isfinite(days)
        except OverflowError:
            finite = False
        if not finite or abs(days) > _MAX_EXCEL_DATE_SERIAL + 1:
            return ErrorValue("#NUM!", "working-day offset is outside the supported range")
        offset = math.trunc(days)
        if abs(offset) > _MAX_EXCEL_DATE_SERIAL + 1:
            return ErrorValue("#NUM!", "working-day offset is outside the supported range")
    else:
        end_day = _workday_date_serial(second_value, out_of_range_code=date_range_error)
        if isinstance(end_day, ErrorValue):
            return end_day

    weekend = _STANDARD_WEEKEND
    if international and len(arguments) >= 3 and arguments[2].kind != "missing":
        weekend_value = _eval(arguments[2], cells, sheet_name, budget)
        if isinstance(weekend_value, ErrorValue):
            return weekend_value
        weekend_result = _weekend_mask(weekend_value)
        if isinstance(weekend_result, ErrorValue):
            return weekend_result
        weekend = weekend_result
    if name == "WORKDAY.INTL" and all(weekend):
        return ErrorValue("#VALUE!", "WORKDAY.INTL does not accept a seven-day weekend")

    holidays: set[int] = set()
    holiday_index = 3 if international else 2
    if len(arguments) > holiday_index and arguments[holiday_index].kind != "missing":
        holiday_value = _eval(arguments[holiday_index], cells, sheet_name, budget)
        holiday_result = _workday_holidays(holiday_value, out_of_range_code=date_range_error)
        if isinstance(holiday_result, ErrorValue):
            return holiday_result
        holidays = holiday_result

    if not is_workday:
        return _networkdays_count(start_day, end_day, holidays, weekend)
    result = _workday_result(start_day, offset, holidays, weekend)
    if result is None:
        return ErrorValue("#NUM!", "WORKDAY result is outside the supported date range")
    return result


def _week_call(
    name: str,
    arguments: tuple[_Node, ...],
    cells: Mapping[str, Scalar],
    sheet_name: str,
    budget: _WildcardBudget,
) -> object:
    if name == "ISOWEEKNUM":
        if len(arguments) != 1:
            return _arity(name, "1", len(arguments))
    elif not 1 <= len(arguments) <= 2:
        return _arity(name, "1 or 2", len(arguments))

    serial = _number(_eval(arguments[0], cells, sheet_name, budget))
    if isinstance(serial, ErrorValue):
        return serial
    try:
        if not math.isfinite(serial):
            return ErrorValue("#NUM!", "date serial must be finite")
        year, month, day = _excel_serial_ymd(float(serial))
    except (OverflowError, ValueError):
        return ErrorValue("#NUM!", "date serial is outside the supported range")

    serial_day = math.floor(serial)
    day_of_year = _excel_day_of_year(serial_day, year, month, day)
    weekday_sunday_zero = _excel_sunday_zero_weekday(serial_day)
    if name == "ISOWEEKNUM":
        return _excel_iso_week_number(year, day_of_year, weekday_sunday_zero)

    selector: int | ErrorValue = 1
    if len(arguments) == 2 and arguments[1].kind != "missing":
        selector = _week_selector(_eval(arguments[1], cells, sheet_name, budget))
    if isinstance(selector, ErrorValue):
        return selector

    if name == "WEEKDAY":
        if selector not in {1, 2, 3, 11, 12, 13, 14, 15, 16, 17}:
            return ErrorValue("#NUM!", "WEEKDAY return_type is not supported")
        week_starts = {1: 0, 2: 1, 3: 1, 11: 1, 12: 2, 13: 3, 14: 4, 15: 5, 16: 6, 17: 0}
        offset = (weekday_sunday_zero - week_starts[selector]) % 7
        return offset if selector == 3 else offset + 1

    if selector not in {1, 2, 11, 12, 13, 14, 15, 16, 17, 21}:
        return ErrorValue("#NUM!", "WEEKNUM return_type is not supported")
    if selector == 21:
        return _excel_iso_week_number(year, day_of_year, weekday_sunday_zero)
    week_starts = {1: 0, 2: 1, 11: 1, 12: 2, 13: 3, 14: 4, 15: 5, 16: 6, 17: 0}
    jan1_position = (_excel_jan1_sunday_zero(year) - week_starts[selector]) % 7
    return (jan1_position + day_of_year - 1) // 7 + 1


def _excel_serial_day_year(serial: float | int) -> tuple[int, int]:
    year, _, day = _excel_serial_ymd(float(serial))
    return day, year


def _days_in_month(year: int, month: int) -> int:
    if year == 1900 and month == 2:
        return 29
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def _excel_ymd_to_serial(year: int, month: int, day: int) -> int:
    if (year, month, day) == (1900, 2, 29):
        return 60
    return _date_to_excel_serial(date(year, month, day))


def _as_range(value: object, name: str) -> _Range | ErrorValue:
    if isinstance(value, ErrorValue):
        return value
    if isinstance(value, _Range):
        return value
    # A single-cell reference evaluates to its scalar value in the parser, so
    # criterion functions treat that value as a one-cell rectangular range.
    return _Range((value,), 1, 1)  # type: ignore[arg-type]


def _wildcard_pattern(pattern: str) -> _WildcardPattern | ErrorValue | None:
    if not any(character in pattern for character in "*?~"):
        return None
    units = 0
    for character in pattern:
        units += 2 if ord(character) > 0xFFFF else 1
        if units > MAX_TEXT_LENGTH_UNITS:
            return ErrorValue(
                "#VALUE!",
                f"wildcard pattern exceeds the {MAX_TEXT_LENGTH_UNITS}-UTF-16-unit text limit",
            )
    tokens: list[_WildcardToken] = []
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "~":
            if index + 1 < len(pattern):
                index += 1
                literal = pattern[index]
            else:
                literal = "~"
            tokens.append(
                _WildcardToken("literal", literal.lower())
            )
        elif character == "*":
            if not tokens or tokens[-1].kind != "many":
                tokens.append(_WildcardToken("many"))
        elif character == "?":
            tokens.append(_WildcardToken("one"))
        else:
            tokens.append(
                _WildcardToken("literal", character.lower())
            )
        index += 1
    return _WildcardPattern(tuple(tokens))


def _wildcard_matches(
    pattern: _WildcardPattern,
    text: str,
    budget: _WildcardBudget,
) -> bool | ErrorValue:
    """Match a wildcard with rolling memory and a formula-wide work budget."""
    width = len(text)
    if not any(token.kind in {"many", "one"} for token in pattern.tokens):
        cost = len(pattern.tokens) + width
        if not budget.consume(cost):
            return ErrorValue(
                "#VALUE!",
                f"wildcard work exceeds the Workbook Forge {MAX_WILDCARD_WORK}-step limit",
            )
        return len(pattern.tokens) == width and all(
            token.literal is not None and token.literal == character.lower()
            for token, character in zip(pattern.tokens, text)
        )

    cost = (len(pattern.tokens) + 1) * (width + 1)
    if not budget.consume(cost):
        return ErrorValue(
            "#VALUE!",
            f"wildcard work exceeds the Workbook Forge {MAX_WILDCARD_WORK}-step limit",
        )

    previous = [False] * (width + 1)
    previous[0] = True
    for token in pattern.tokens:
        current = [False] * (width + 1)
        if token.kind == "many":
            current[0] = previous[0]
            for offset in range(1, width + 1):
                current[offset] = previous[offset] or current[offset - 1]
        elif token.kind == "one":
            for offset in range(1, width + 1):
                current[offset] = previous[offset - 1]
        else:
            assert token.literal is not None
            for offset in range(1, width + 1):
                current[offset] = (
                    previous[offset - 1]
                    and token.literal == text[offset - 1].lower()
                )
        previous = current
    return previous[width]


def _parse_criterion(value: object) -> _Criterion | ErrorValue:
    if isinstance(value, ErrorValue):
        return value
    if isinstance(value, _Range):
        return ErrorValue("#VALUE!", "criteria must be scalar")
    if value is None:
        return _Criterion("=", None, blank=True)
    if isinstance(value, bool):
        return _Criterion("=", value)
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return ErrorValue("#VALUE!", "criteria must be finite")
        return _Criterion("=", value)
    if not isinstance(value, str):
        return ErrorValue("#VALUE!", "unsupported criteria value")

    operator = "="
    operand = value
    match = re.match(r"^(<=|>=|<>|=|<|>)(.*)$", value, re.DOTALL)
    if match:
        operator, operand = match.groups()
    if operand == "" and operator in {"=", "<>"}:
        return _Criterion(operator, None, blank=True)
    if operand == "" or operand[0] in "<>=":
        return ErrorValue("#VALUE!", "malformed criteria operator or operand")

    wildcard = _wildcard_pattern(operand)
    if isinstance(wildcard, ErrorValue):
        return wildcard
    if wildcard is not None and operator not in {"=", "<>"}:
        return ErrorValue("#VALUE!", "wildcard criteria support equality and inequality only")

    upper = operand.upper()
    if upper in {"TRUE", "FALSE"}:
        expected: object = upper == "TRUE"
    else:
        try:
            numeric = float(operand)
            expected = int(numeric) if numeric.is_integer() else numeric
        except ValueError:
            expected = operand
    return _Criterion(operator, expected, wildcard)


def _criterion_matches(
    value: object,
    criterion: _Criterion,
    budget: _WildcardBudget,
) -> bool | ErrorValue:
    if isinstance(value, ErrorValue):
        return value
    if criterion.blank:
        is_blank = value is None or value == ""
        return is_blank if criterion.operator == "=" else not is_blank
    expected = criterion.expected
    if criterion.wildcard is not None:
        matched = (
            _wildcard_matches(criterion.wildcard, value, budget)
            if isinstance(value, str)
            else False
        )
        if isinstance(matched, ErrorValue):
            return matched
        return matched if criterion.operator == "=" else not matched
    if isinstance(expected, str):
        if isinstance(value, str):
            left: object = value.casefold()
            right: object = expected.casefold()
        elif criterion.operator == "<>":
            return True
        else:
            return False
    elif isinstance(expected, bool):
        if not isinstance(value, bool):
            return criterion.operator == "<>"
        left, right = value, expected
    else:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return criterion.operator == "<>"
        left, right = value, expected
    try:
        if criterion.operator == "=":
            return left == right
        if criterion.operator == "<>":
            return left != right
        if criterion.operator == "<":
            return left < right
        if criterion.operator == "<=":
            return left <= right
        if criterion.operator == ">":
            return left > right
        return left >= right
    except TypeError:
        return False


def _criteria_aggregate(
    name: str,
    args: tuple[object, ...],
    budget: _WildcardBudget,
) -> object:
    if name in {"COUNTIF", "SUMIF", "AVERAGEIF"}:
        criteria_range = _as_range(args[0], name)
        if isinstance(criteria_range, ErrorValue):
            return criteria_range
        criterion = _parse_criterion(args[1])
        if isinstance(criterion, ErrorValue):
            return criterion
        value_range = criteria_range if len(args) < 3 else _as_range(args[2], name)
        if isinstance(value_range, ErrorValue):
            return value_range
        if (criteria_range.rows, criteria_range.columns) != (value_range.rows, value_range.columns):
            return ErrorValue("#VALUE!", f"{name} ranges must have matching dimensions")
        criteria = [criterion]
    else:
        if name == "COUNTIFS":
            value_range = None
            pairs = args
        else:
            value_range = _as_range(args[0], name)
            if isinstance(value_range, ErrorValue):
                return value_range
            pairs = args[1:]
        ranges: list[_Range] = []
        criteria: list[_Criterion] = []
        for index in range(0, len(pairs), 2):
            criterion_range = _as_range(pairs[index], name)
            if isinstance(criterion_range, ErrorValue):
                return criterion_range
            criterion = _parse_criterion(pairs[index + 1])
            if isinstance(criterion, ErrorValue):
                return criterion
            if ranges and (criterion_range.rows, criterion_range.columns) != (ranges[0].rows, ranges[0].columns):
                return ErrorValue("#VALUE!", f"{name} criteria ranges must have matching dimensions")
            if value_range is not None and (criterion_range.rows, criterion_range.columns) != (value_range.rows, value_range.columns):
                return ErrorValue("#VALUE!", f"{name} ranges must have matching dimensions")
            ranges.append(criterion_range)
            criteria.append(criterion)
        if value_range is None:
            value_range = ranges[0]
        criteria_range = ranges[0]
        if name in {"SUMIFS", "AVERAGEIFS", "MINIFS", "MAXIFS"}:
            criteria_range = None
    if name in {"COUNTIF", "SUMIF", "AVERAGEIF"}:
        ranges = [criteria_range]
    else:
        ranges = ranges

    matched_values: list[object] = []
    for position, value in enumerate(value_range.values):
        matched = True
        for range_, criterion in zip(ranges, criteria):
            result = _criterion_matches(range_.values[position], criterion, budget)
            if isinstance(result, ErrorValue):
                return result
            if not result:
                matched = False
                break
        if matched:
            matched_values.append(value)
    if name in {"COUNTIF", "COUNTIFS"}:
        return len(matched_values)
    errors = [value for value in matched_values if isinstance(value, ErrorValue)]
    if errors:
        return errors[0]
    numbers: list[int | float] = [
        1 if value is True else 0 if value is False else value
        for value in matched_values
        if isinstance(value, (int, float))
        and (not isinstance(value, bool) or name == "AVERAGEIFS")
    ]
    if name in {"AVERAGEIF", "AVERAGEIFS"}:
        return (
            ErrorValue("#DIV/0!", f"{name} has no numeric matches")
            if not numbers
            else sum(numbers) / len(numbers)
        )
    if name in {"MINIFS", "MAXIFS"}:
        return 0 if not numbers else (min(numbers) if name == "MINIFS" else max(numbers))
    return sum(
        1 if value is True else 0 if value is False else value
        for value in matched_values
        if isinstance(value, (int, float, bool))
    )


def _eval(
    node: _Node,
    cells: Mapping[str, Scalar],
    sheet_name: str,
    budget: _WildcardBudget,
) -> object:
    if node.kind == "missing":
        return ErrorValue("#VALUE!", "required argument was omitted")
    if node.kind == "literal":
        return node.value
    if node.kind == "cell":
        address, sheet = node.value  # type: ignore[misc]
        return _cell_value(address, cells, sheet, sheet_name)
    if node.kind == "range":
        start, end, sheet = node.value  # type: ignore[misc]
        return _range_value(start, end, cells, sheet, sheet_name)
    if node.kind in {"unary", "postfix"}:
        operations: list[tuple[str, object]] = []
        current = node
        while current.kind in {"unary", "postfix"}:
            operations.append((current.kind, current.value))
            current = current.children[0]
        value = _eval(current, cells, sheet_name, budget)
        for kind, operator in reversed(operations):
            number = _number(value)
            if isinstance(number, ErrorValue):
                return number
            if kind == "postfix":
                value = number / 100
            else:
                value = number if operator == "+" else -number
        return value
    if node.kind == "binary":
        chain: list[tuple[str, _Node]] = []
        current = node
        while current.kind == "binary":
            chain.append((str(current.value), current.children[1]))
            current = current.children[0]
        left = _eval(current, cells, sheet_name, budget)
        if not isinstance(left, (_Range, ArrayValue)):
            left_error = _nonfinite_literal_error(current)
            if left_error is not None:
                left = left_error
        for operator, right_node in reversed(chain):
            right = _eval(right_node, cells, sheet_name, budget)
            if not isinstance(right, (_Range, ArrayValue)):
                right_error = _nonfinite_literal_error(right_node)
                if right_error is not None:
                    right = right_error
            left = _apply_binary_values(operator, left, right)
        return left
    if node.kind == "call":
        name = str(node.value)
        if name == "SEQUENCE":
            return _sequence_call(node.children, cells, sheet_name, budget)
        if name == "FILTER":
            return _filter_call(node.children, cells, sheet_name, budget)
        if name == "SORT":
            return _sort_call(node.children, cells, sheet_name, budget)
        if name == "UNIQUE":
            return _unique_call(node.children, cells, sheet_name, budget)
        if name in {"NETWORKDAYS", "WORKDAY", "NETWORKDAYS.INTL", "WORKDAY.INTL"}:
            return _working_day_call(name, node.children, cells, sheet_name, budget)
        if name in {"WEEKDAY", "WEEKNUM", "ISOWEEKNUM"}:
            return _week_call(name, node.children, cells, sheet_name, budget)
        if name in {"TEXTBEFORE", "TEXTAFTER"}:
            return _text_extract(name, node.children, cells, sheet_name, budget)
        if name == "TRUNC":
            return _trunc_call(node.children, cells, sheet_name, budget)
        if name == "IF":
            if len(node.children) not in (2, 3):
                return _arity(name, "2 or 3", len(node.children))
            condition = _truth(_eval(node.children[0], cells, sheet_name, budget))
            if isinstance(condition, ErrorValue):
                return condition
            branch = node.children[1] if condition else (node.children[2] if len(node.children) == 3 else _Node("literal", False))
            return _eval(branch, cells, sheet_name, budget)
        if name == "IFERROR":
            if len(node.children) != 2:
                return _arity(name, "2", len(node.children))
            value = _eval(node.children[0], cells, sheet_name, budget)
            return _eval(node.children[1], cells, sheet_name, budget) if isinstance(value, ErrorValue) else value
        if name == "IFNA":
            if len(node.children) != 2:
                return _arity(name, "2", len(node.children))
            value = _eval(node.children[0], cells, sheet_name, budget)
            if isinstance(value, ErrorValue) and value.code in {"#N/A", "#N/A!"}:
                return _eval(node.children[1], cells, sheet_name, budget)
            return value
        if name == "IFS":
            if not 2 <= len(node.children) <= 254 or len(node.children) % 2:
                return _arity(name, "2 through 254 even-numbered arguments", len(node.children))
            for index in range(0, len(node.children), 2):
                condition = _eval(node.children[index], cells, sheet_name, budget)
                if isinstance(condition, ErrorValue):
                    return condition
                if not isinstance(condition, bool):
                    return ErrorValue("#VALUE!", "IFS logical tests must evaluate to TRUE or FALSE")
                if condition:
                    result = node.children[index + 1]
                    return 0 if result.kind == "missing" else _eval(result, cells, sheet_name, budget)
            return ErrorValue("#N/A", "IFS found no TRUE logical test")
        if name == "SWITCH":
            if not 3 <= len(node.children) <= 254:
                return _arity(name, "3 through 254", len(node.children))
            expression = _eval(node.children[0], cells, sheet_name, budget)
            if isinstance(expression, ErrorValue):
                return expression
            if isinstance(expression, (_Range, ArrayValue)):
                return ErrorValue("#VALUE!", "SWITCH expression must be scalar")
            remaining = len(node.children) - 1
            has_default = remaining % 2 == 1
            pair_argument_count = remaining - int(has_default)
            for offset in range(0, pair_argument_count, 2):
                match_value = _eval(node.children[1 + offset], cells, sheet_name, budget)
                equal = _switch_equal(expression, match_value)
                if isinstance(equal, ErrorValue):
                    return equal
                if equal:
                    result = node.children[2 + offset]
                    return 0 if result.kind == "missing" else _eval(result, cells, sheet_name, budget)
            if has_default:
                default = node.children[-1]
                return 0 if default.kind == "missing" else _eval(default, cells, sheet_name, budget)
            return ErrorValue("#N/A", "SWITCH found no matching value and has no default")
        if name == "XLOOKUP":
            if not 3 <= len(node.children) <= 6:
                return _arity(name, "3 through 6", len(node.children))
            lookup_value = _eval(node.children[0], cells, sheet_name, budget)
            lookup_array = _eval(node.children[1], cells, sheet_name, budget)
            return_array = _eval(node.children[2], cells, sheet_name, budget)
            match_mode = 0 if len(node.children) < 5 else _eval(node.children[4], cells, sheet_name, budget)
            search_mode = 1 if len(node.children) < 6 else _eval(node.children[5], cells, sheet_name, budget)
            for value in (lookup_value, lookup_array, return_array, match_mode, search_mode):
                if isinstance(value, ErrorValue):
                    return value
            found, result = _exact_lookup(
                lookup_value, lookup_array, return_array, match_mode, search_mode, "XLOOKUP"
            )
            if isinstance(result, ErrorValue):
                return result
            if found:
                return result
            if len(node.children) >= 4:
                fallback = _eval(node.children[3], cells, sheet_name, budget)
                if isinstance(fallback, (_Range, ArrayValue)):
                    return ErrorValue("#VALUE!", "XLOOKUP if_not_found must be scalar in this evaluator")
                return fallback
            return ErrorValue("#N/A", "XLOOKUP value not found")
        args = tuple(
            _OPTIONAL_OMITTED
            if name == "VDB" and index in {5, 6} and child.kind == "missing"
            else ErrorValue("#VALUE!", "an optional depreciation argument cannot be explicitly omitted")
            if name in {"DB", "DDB"} and index == 4 and child.kind == "missing"
            else None
            if (
                name in {"DAYS360", "YEARFRAC"} and index == 2 and child.kind == "missing"
            ) or (
                name in {"AMORLINC", "AMORDEGRC"}
                and index == 6
                and child.kind == "missing"
            ) or (
                name in {"COUPDAYBS", "COUPDAYS", "COUPDAYSNC", "COUPNCD", "COUPNUM", "COUPPCD"}
                and index == 3
                and child.kind == "missing"
            ) or (
                name in {"FV", "PV", "PMT", "NPER"}
                and index in {3, 4}
                and child.kind == "missing"
            ) or (
                name in {"IPMT", "PPMT"}
                and index in {4, 5}
                and child.kind == "missing"
            ) or (
                name in {"FV", "PV"}
                and index == 2
                and len(node.children) >= 4
                and node.children[3].kind != "missing"
                and child.kind == "missing"
            )
            else _eval(child, cells, sheet_name, budget)
            for index, child in enumerate(node.children)
        )
        return _function(name, args, budget)
    return ErrorValue("#VALUE!", f"unsupported syntax node {node.kind!r}")


def _text_extract(
    name: str,
    arguments: tuple[_Node, ...],
    cells: Mapping[str, Scalar],
    sheet_name: str,
    budget: _WildcardBudget,
) -> object:
    if not 2 <= len(arguments) <= 6:
        return _arity(name, "2 through 6", len(arguments))

    def read(index: int) -> tuple[object, bool] | ErrorValue:
        if index >= len(arguments) or arguments[index].kind == "missing":
            return None, False
        value = _eval(arguments[index], cells, sheet_name, budget)
        if isinstance(value, _Range):
            return ErrorValue("#VALUE!", f"{name} arguments must be scalar in this evaluator")
        return value, True

    text_arg = read(0)
    delimiter_arg = read(1)
    if isinstance(text_arg, ErrorValue):
        return text_arg
    if isinstance(delimiter_arg, ErrorValue):
        return delimiter_arg
    text_value, _ = text_arg
    delimiter_value, _ = delimiter_arg
    if isinstance(text_value, ErrorValue):
        return text_value
    if isinstance(delimiter_value, ErrorValue):
        return delimiter_value
    text = _text(text_value)
    if isinstance(text, ErrorValue):
        return text
    delimiter = _text(delimiter_value)
    if isinstance(delimiter, ErrorValue):
        return delimiter

    instance_arg = read(2)
    if isinstance(instance_arg, ErrorValue):
        return instance_arg
    instance_value, instance_present = instance_arg
    instance_number = 1
    if instance_present:
        number = _number(instance_value)
        if isinstance(number, ErrorValue):
            return number
        if isinstance(number, float):
            if not math.isfinite(number) or not number.is_integer():
                return ErrorValue("#VALUE!", f"{name} instance_num must be a non-zero integer")
            number = int(number)
        if not isinstance(number, int) or number == 0:
            return ErrorValue("#VALUE!", f"{name} instance_num must be a non-zero integer")
        instance_number = number

    match_arg = read(3)
    if isinstance(match_arg, ErrorValue):
        return match_arg
    match_value, match_present = match_arg
    match_mode = 0
    if match_present:
        number = _number(match_value)
        if isinstance(number, ErrorValue):
            return number
        if number not in (0, 1):
            return ErrorValue("#VALUE!", f"{name} match_mode must be 0 or 1")
        match_mode = int(number)

    end_arg = read(4)
    if isinstance(end_arg, ErrorValue):
        return end_arg
    end_value, end_present = end_arg
    match_end = 0
    if end_present:
        number = _number(end_value)
        if isinstance(number, ErrorValue):
            return number
        if number not in (0, 1):
            return ErrorValue("#VALUE!", f"{name} match_end must be 0 or 1")
        match_end = int(number)

    if text == "":
        return ""
    if abs(instance_number) > len(text):
        return ErrorValue("#VALUE!", f"{name} instance_num exceeds the text length")

    if delimiter == "":
        if abs(instance_number) != 1:
            return _text_extract_fallback(name, arguments, cells, sheet_name, budget)
        if name == "TEXTBEFORE":
            return "" if instance_number > 0 else text
        return text if instance_number > 0 else ""

    matches = _text_delimiter_matches(text, delimiter, match_mode == 1)
    if match_end and not any(end == len(text) for _, end in matches):
        matches.append((len(text), len(text)))
    match_index = instance_number - 1 if instance_number > 0 else instance_number
    if match_index < 0:
        match_index += len(matches)
    if not 0 <= match_index < len(matches):
        return _text_extract_fallback(name, arguments, cells, sheet_name, budget)
    start, end = matches[match_index]
    return text[:start] if name == "TEXTBEFORE" else text[end:]


def _text_extract_fallback(
    name: str,
    arguments: tuple[_Node, ...],
    cells: Mapping[str, Scalar],
    sheet_name: str,
    budget: _WildcardBudget,
) -> object:
    if len(arguments) < 6 or arguments[5].kind == "missing":
        return ErrorValue("#N/A", f"{name} delimiter was not found")
    fallback = _eval(arguments[5], cells, sheet_name, budget)
    if isinstance(fallback, _Range):
        return ErrorValue("#VALUE!", f"{name} if_not_found must be scalar in this evaluator")
    return fallback


def _text_delimiter_matches(text: str, delimiter: str, case_insensitive: bool) -> list[tuple[int, int]]:
    if not case_insensitive:
        matches: list[tuple[int, int]] = []
        cursor = 0
        while (found := text.find(delimiter, cursor)) >= 0:
            matches.append((found, found + len(delimiter)))
            cursor = found + len(delimiter)
        return matches

    folded_parts: list[str] = []
    source_starts: list[int] = []
    source_ends: list[int] = []
    for source_index, character in enumerate(text):
        folded = character.casefold()
        folded_parts.append(folded)
        source_starts.extend([source_index] * len(folded))
        source_ends.extend([source_index + 1] * len(folded))
    folded_text = "".join(folded_parts)
    folded_delimiter = delimiter.casefold()
    matches = []
    cursor = 0
    while (found := folded_text.find(folded_delimiter, cursor)) >= 0:
        end_offset = found + len(folded_delimiter)
        begins_at_character_boundary = found == 0 or source_starts[found] != source_starts[found - 1]
        ends_at_character_boundary = (
            end_offset == len(source_starts)
            or source_starts[end_offset - 1] != source_starts[end_offset]
        )
        if begins_at_character_boundary and ends_at_character_boundary:
            matches.append((source_starts[found], source_ends[end_offset - 1]))
            cursor = end_offset
        else:
            cursor = found + 1
    return matches


def _arity(name: str, expected: str, actual: int) -> ErrorValue:
    return ErrorValue("#VALUE!", f"{name} expects {expected} argument(s), received {actual}")


def _is_lookup_match(candidate: object, lookup_value: object) -> bool:
    if isinstance(candidate, str) and isinstance(lookup_value, str):
        return candidate.casefold() == lookup_value.casefold()
    return candidate == lookup_value


def _wildcard_lookup_match(
    candidate: object,
    lookup_value: object,
    budget: _WildcardBudget,
    pattern_cache: list[object],
) -> bool | ErrorValue:
    if isinstance(candidate, str) and isinstance(lookup_value, str):
        if pattern_cache[0] is _UNSET_WILDCARD:
            pattern_cache[0] = _wildcard_pattern(lookup_value)
        wildcard = pattern_cache[0]
        if isinstance(wildcard, ErrorValue):
            return wildcard
        if wildcard is not None:
            assert isinstance(wildcard, _WildcardPattern)
            return _wildcard_matches(wildcard, candidate, budget)
    return _is_lookup_match(candidate, lookup_value)


def _exact_match(
    lookup_value: object,
    lookup_array: object,
    match_mode: object,
    search_mode: object,
    function_name: str,
) -> int | ErrorValue:
    """Return a one-based position for supported exact vector lookups."""
    if isinstance(lookup_value, ErrorValue):
        return lookup_value
    if isinstance(lookup_value, _Range):
        return ErrorValue("#VALUE!", f"{function_name} lookup_value must be scalar")
    if isinstance(lookup_array, ErrorValue):
        return lookup_array
    if not isinstance(lookup_array, _Range):
        return ErrorValue("#VALUE!", f"{function_name} lookup_array must be a cell range")
    if lookup_array.rows != 1 and lookup_array.columns != 1:
        return ErrorValue("#VALUE!", f"{function_name} lookup_array must be one-dimensional")
    match = _number(match_mode)
    direction = _number(search_mode)
    if isinstance(match, ErrorValue):
        return match
    if isinstance(direction, ErrorValue):
        return direction
    if match != 0:
        return ErrorValue("#VALUE!", f"{function_name} supports exact match_mode=0 only")
    if direction not in (1, -1):
        return ErrorValue("#VALUE!", f"{function_name} search_mode must be 1 or -1")
    indices = range(len(lookup_array.values))
    if direction == -1:
        indices = range(len(lookup_array.values) - 1, -1, -1)
    for index in indices:
        candidate = lookup_array.values[index]
        if isinstance(candidate, ErrorValue):
            return candidate
        if _is_lookup_match(candidate, lookup_value):
            return index + 1
    return ErrorValue("#N/A", f"{function_name} value not found")


def _exact_lookup(
    lookup_value: object,
    lookup_array: object,
    return_array: object,
    match_mode: object,
    search_mode: object,
    function_name: str,
) -> tuple[bool, object]:
    if isinstance(return_array, ErrorValue):
        return False, return_array
    if not isinstance(return_array, _Range):
        return False, ErrorValue("#VALUE!", f"{function_name} return_array must be a cell range")
    if return_array.rows != 1 and return_array.columns != 1:
        return False, ErrorValue("#VALUE!", f"{function_name} return_array must be one-dimensional")
    if not isinstance(lookup_array, _Range):
        return False, ErrorValue("#VALUE!", f"{function_name} lookup_array must be a cell range")
    if lookup_array.rows != 1 and lookup_array.columns != 1:
        return False, ErrorValue("#VALUE!", f"{function_name} lookup_array must be one-dimensional")
    if len(lookup_array.values) != len(return_array.values):
        return False, ErrorValue("#VALUE!", f"{function_name} lookup and return arrays must have equal size")
    position = _exact_match(lookup_value, lookup_array, match_mode, search_mode, function_name)
    if isinstance(position, ErrorValue):
        if position.code == "#N/A":
            return False, None
        return False, position
    result = return_array.values[position - 1]
    return True, result


def _integer_formula_argument(value: object) -> int | ErrorValue:
    """Convert a scalar to the bounded integer profile used by integer formulas."""
    number = _number(value)
    if isinstance(number, ErrorValue):
        return number
    if isinstance(number, int) and abs(number) > _MAX_EXACT_INTEGER:
        return ErrorValue("#NUM!", "integer formula argument exceeds the exact-integer profile")
    try:
        numeric = float(number)
    except (OverflowError, TypeError, ValueError):
        return ErrorValue("#NUM!", "integer formula argument is outside the supported range")
    if not math.isfinite(numeric):
        return ErrorValue("#NUM!", "integer formula argument must be finite")
    integer = math.trunc(numeric)
    if abs(integer) > _MAX_EXACT_INTEGER:
        return ErrorValue("#NUM!", "integer formula argument exceeds the exact-integer profile")
    return integer


def _binomial_float(number: int, chosen: int) -> float | ErrorValue:
    """Return the correctly rounded binary64 value of an exact binomial coefficient."""
    if number < 0 or chosen < 0 or chosen > number:
        return ErrorValue("#NUM!", "combination arguments are outside the valid domain")
    reduced = min(chosen, number - chosen)
    # Once the symmetric lower argument exceeds this bound, even the smallest
    # possible central coefficient is beyond binary64's finite range.
    if reduced > 1024:
        return ErrorValue("#NUM!", "combination exceeds the bounded evaluation profile")
    try:
        return float(math.comb(number, chosen))
    except OverflowError:
        return ErrorValue("#NUM!", "combination result exceeds the supported numeric range")


def _bounded_exact_integer_float(value: int, description: str) -> float | ErrorValue:
    """Round a bounded exact nonnegative integer once to finite binary64."""
    if value.bit_length() > _MAX_FINITE_BINARY64_INTEGER_BITS:
        return ErrorValue("#NUM!", f"{description} exceeds the supported numeric range")
    try:
        return float(value)
    except OverflowError:
        return ErrorValue("#NUM!", f"{description} exceeds the supported numeric range")


def _double_factorial_float(number: int) -> float | ErrorValue:
    """Compute n!! exactly; 300!! is the largest finite result in this profile."""
    if number > 300:
        return ErrorValue("#NUM!", "double factorial result exceeds the supported numeric range")
    result = 1
    factor = number
    while factor >= 2:
        result *= factor
        if result.bit_length() > _MAX_FINITE_BINARY64_INTEGER_BITS:
            return ErrorValue("#NUM!", "double factorial result exceeds the supported numeric range")
        factor -= 2
    return _bounded_exact_integer_float(result, "double factorial result")


def _permutation_float(number: int, chosen: int) -> float | ErrorValue:
    """Form nPr as an exact falling product without factorial intermediates."""
    if chosen == 0:
        return 1.0
    # For any n >= r, nPr >= r!; 171! is outside finite binary64.
    if chosen > 170:
        return ErrorValue("#NUM!", "permutation result exceeds the supported numeric range")
    result = 1
    factor = number
    for _ in range(chosen):
        result *= factor
        if result.bit_length() > _MAX_FINITE_BINARY64_INTEGER_BITS:
            return ErrorValue("#NUM!", "permutation result exceeds the supported numeric range")
        factor -= 1
    return _bounded_exact_integer_float(result, "permutation result")


def _permutationa_float(number: int, chosen: int) -> float | ErrorValue:
    """Compute n^r by bounded exact exponentiation, then round once to binary64."""
    if chosen == 0 or number == 1:
        # Includes the evaluator's explicit, unverified 0^0 identity profile.
        return 1.0
    if number == 0:
        return ErrorValue(
            "#NUM!", "PERMUTATIONA does not accept zero objects for a positive selection"
        )
    # For n >= 2, 2^1024 is already outside finite binary64.
    if chosen > 1023:
        return ErrorValue("#NUM!", "permutation result exceeds the supported numeric range")

    result = 1
    factor = number
    exponent = chosen
    while exponent:
        if exponent & 1:
            result *= factor
            if result.bit_length() > _MAX_FINITE_BINARY64_INTEGER_BITS:
                return ErrorValue("#NUM!", "permutation result exceeds the supported numeric range")
        exponent >>= 1
        if exponent:
            factor *= factor
            if factor.bit_length() > _MAX_FINITE_BINARY64_INTEGER_BITS:
                return ErrorValue("#NUM!", "permutation result exceeds the supported numeric range")
    return _bounded_exact_integer_float(result, "permutation result")


def _integer_formula_call(name: str, args: tuple[object, ...]) -> object:
    numbers = [_integer_formula_argument(argument) for argument in args]
    if any(isinstance(value, ErrorValue) for value in numbers):
        return next(value for value in numbers if isinstance(value, ErrorValue))
    integers = [int(value) for value in numbers]

    if name == "GCD":
        if any(number < 0 for number in integers):
            return ErrorValue("#NUM!", "GCD requires nonnegative arguments")
        if any(number >= _MAX_EXACT_INTEGER for number in integers):
            return ErrorValue("#NUM!", "GCD argument must be less than 2^53")
        result = 0
        for number in integers:
            while number:
                result, number = number, result % number
        return result

    if name == "LCM":
        if any(number < 0 for number in integers):
            return ErrorValue("#NUM!", "LCM requires nonnegative arguments")
        # Microsoft does not document LCM's zero behavior. Workbook Forge uses
        # the conventional zero-absorbing identity as a local evaluator profile.
        if any(number == 0 for number in integers):
            return 0
        result = 1
        largest_allowed = _MAX_EXACT_INTEGER - 1
        for number in integers:
            divisor = math.gcd(result, number)
            reduced = result // divisor
            if reduced > largest_allowed // number:
                return ErrorValue("#NUM!", "LCM result must be less than 2^53")
            result = reduced * number
        return result

    if name == "FACTDOUBLE":
        number = integers[0]
        if number < 0:
            return ErrorValue("#NUM!", "FACTDOUBLE requires a nonnegative number")
        return _double_factorial_float(number)

    if name == "FACT":
        number = integers[0]
        if number < 0:
            return ErrorValue("#NUM!", "FACT requires a nonnegative number")
        if number > 170:
            return ErrorValue("#NUM!", "factorial result exceeds the supported numeric range")
        return float(math.factorial(number))

    number, chosen = integers
    if name == "PERMUT":
        if number <= 0 or chosen < 0 or chosen > number:
            return ErrorValue("#NUM!", "PERMUT arguments are outside the valid domain")
        return _permutation_float(number, chosen)

    if name == "PERMUTATIONA":
        if number < 0 or chosen < 0:
            return ErrorValue("#NUM!", "PERMUTATIONA arguments are outside the supported domain")
        if number == 0 and chosen > 0:
            return ErrorValue(
                "#NUM!", "PERMUTATIONA does not accept zero objects for a positive selection"
            )
        return _permutationa_float(number, chosen)

    if number < 0 or chosen < 0 or chosen > number:
        return ErrorValue("#NUM!", f"{name} arguments are outside the valid domain")
    if name == "COMBIN":
        return _binomial_float(number, chosen)

    # COMBINA counts selections with repetition: C(number + chosen - 1, chosen).
    # Handle the zero/zero identity before forming the transformed top index.
    if number == 0:
        return 1.0
    return _binomial_float(number + chosen - 1, chosen)


def _function(
    name: str,
    args: tuple[object, ...],
    budget: _WildcardBudget,
) -> object:
    fixed = {
        "ABS": (1, 1), "EVEN": (1, 1), "ODD": (1, 1), "ISEVEN": (1, 1), "ISODD": (1, 1),
        "GCD": (1, 255), "LCM": (1, 255),
        "FACT": (1, 1), "FACTDOUBLE": (1, 1), "COMBIN": (2, 2), "COMBINA": (2, 2),
        "PERMUT": (2, 2), "PERMUTATIONA": (2, 2),
        "ROUND": (2, 2), "INT": (1, 1), "TRUNC": (1, 2),
        "ROUNDUP": (2, 2), "ROUNDDOWN": (2, 2), "MOD": (2, 2), "QUOTIENT": (2, 2),
        "LEFT": (1, 2), "RIGHT": (1, 2),
        "MID": (3, 3), "LEN": (1, 1), "DATE": (3, 3), "HOUR": (1, 1),
        "MINUTE": (1, 1), "SECOND": (1, 1), "TIME": (3, 3), "MONTH": (1, 1),
        "DAY": (1, 1), "YEAR": (1, 1), "WEEKDAY": (1, 2), "WEEKNUM": (1, 2),
        "ISOWEEKNUM": (1, 1), "DAYS": (2, 2), "DAYS360": (2, 3),
        "YEARFRAC": (2, 3), "EDATE": (2, 2),
        "FV": (3, 5), "PV": (3, 5), "PMT": (3, 5), "NPER": (3, 5),
        "IPMT": (4, 6), "PPMT": (4, 6),
        "CUMIPMT": (6, 6), "CUMPRINC": (6, 6),
        "SLN": (3, 3), "SYD": (4, 4), "DB": (4, 5), "DDB": (4, 5), "VDB": (5, 7),
        "AMORLINC": (6, 7), "AMORDEGRC": (6, 7),
        "COUPDAYBS": (3, 4), "COUPDAYS": (3, 4), "COUPDAYSNC": (3, 4),
        "COUPNCD": (3, 4), "COUPNUM": (3, 4), "COUPPCD": (3, 4),
        "EOMONTH": (2, 2), "WORKDAY": (2, 3), "NETWORKDAYS": (2, 3),
        "WORKDAY.INTL": (2, 4), "NETWORKDAYS.INTL": (2, 4),
        "NOT": (1, 1), "VLOOKUP": (3, 4), "MATCH": (2, 3),
        "INDEX": (2, 4), "XMATCH": (2, 4), "ISBLANK": (1, 1), "ISNUMBER": (1, 1),
        "ISTEXT": (1, 1), "ISLOGICAL": (1, 1), "UPPER": (1, 1),
        "LOWER": (1, 1), "TRIM": (1, 1), "FIND": (2, 3), "SEARCH": (2, 3),
        "SUBSTITUTE": (3, 4), "COUNTIF": (2, 2), "SUMIF": (2, 3),
        "AVERAGEIF": (2, 3), "COUNTBLANK": (1, 1),
        "TEXTBEFORE": (2, 6), "TEXTAFTER": (2, 6),
        "ISERR": (1, 1), "ISERROR": (1, 1), "ISNA": (1, 1), "NA": (0, 0),
    }
    if name not in {
        "SUM", "AVERAGE", "COUNT", "COUNTA", "MIN", "MAX", "IF", "IFERROR", "IFNA", "XLOOKUP",
        "AND", "OR", "CONCAT", "TEXTJOIN", "COUNTIFS", "SUMIFS", "AVERAGEIFS",
        "MINIFS", "MAXIFS", *fixed,
    }:
        return ErrorValue("#NAME?", f"unsupported function {name}")
    if name in fixed and not fixed[name][0] <= len(args) <= fixed[name][1]:
        low, high = fixed[name]
        expected = str(low) if low == high else f"{low} through {high}"
        return _arity(name, expected, len(args))
    if name in {"SUM", "AVERAGE", "COUNT", "COUNTA", "MIN", "MAX", "AND", "OR", "CONCAT"} and not args:
        return _arity(name, "at least 1", 0)
    if name == "CONCAT" and len(args) > 253:
        return _arity(name, "1 through 253", len(args))
    if name == "TEXTJOIN" and len(args) < 3:
        return _arity(name, "at least 3", len(args))
    if name == "TEXTJOIN" and len(args) > 254:
        return _arity(name, "3 through 254", len(args))
    if name == "COUNTIFS" and (len(args) < 2 or len(args) > 254 or len(args) % 2):
        return _arity(name, "2 through 254 arguments in range/criteria pairs", len(args))
    if name in {"SUMIFS", "AVERAGEIFS", "MINIFS", "MAXIFS"} and (
        len(args) < 3 or len(args) > 255 or len(args) % 2 == 0
    ):
        return _arity(name, "3 through 255 arguments in range/criteria pairs", len(args))
    if name == "XMATCH" and isinstance(args[0], ErrorValue):
        return args[0]
    # Excel's type and error predicates report a Boolean instead of
    # propagating a scalar error. Ranges remain outside this evaluator slice.
    if name in {"ISBLANK", "ISNUMBER", "ISTEXT", "ISLOGICAL", "ISERR", "ISERROR", "ISNA"}:
        if isinstance(args[0], (_Range, ArrayValue)):
            return ErrorValue("#VALUE!", f"{name} does not accept a range argument in this evaluator")
        value = args[0]
        if name in {"ISERR", "ISERROR", "ISNA"}:
            if not isinstance(value, ErrorValue):
                return False
            is_na = value.code in {"#N/A", "#N/A!"}
            return name == "ISERROR" or (name == "ISNA" and is_na) or (name == "ISERR" and not is_na)
        if name == "ISBLANK":
            return value is None
        if name == "ISNUMBER":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if name == "ISTEXT":
            return isinstance(value, str)
        return isinstance(value, bool)
    if name in {"ISEVEN", "ISODD"}:
        if isinstance(args[0], (_Range, ArrayValue)):
            return ErrorValue("#VALUE!", f"{name} does not accept a range argument in this evaluator")
        return _test_integer_parity(args[0], odd=name == "ISODD")
    if name == "NA":
        return ErrorValue("#N/A", "NA returns the #N/A error value")
    if name == "COUNTBLANK":
        value = args[0]
        if isinstance(value, ErrorValue):
            return value
        values = (
            value.values
            if isinstance(value, _Range)
            else tuple(item for row in value.rows for item in row)
            if isinstance(value, ArrayValue)
            else (value,)
        )
        return sum(item is None or item == "" for item in values)
    if any(isinstance(argument, ArrayValue) for argument in args) and name not in {
        "SUM", "AVERAGE", "COUNT", "COUNTA", "MIN", "MAX", "AND", "OR", "CONCAT", "TEXTJOIN"
    }:
        return ErrorValue("#VALUE!", f"{name} does not accept a computed array argument in this evaluator")
    if name in {
        "COUNTIF", "COUNTIFS", "SUMIF", "SUMIFS", "AVERAGEIF", "AVERAGEIFS", "MINIFS", "MAXIFS"
    }:
        return _criteria_aggregate(name, args, budget)
    if any(isinstance(argument, (_Range, ArrayValue)) for argument in args) and name not in {
            "SUM", "AVERAGE", "COUNT", "COUNTA", "MIN", "MAX", "AND", "OR", "CONCAT", "TEXTJOIN", "MATCH", "XMATCH", "INDEX", "VLOOKUP",
            "COUNTBLANK", "COUNTIF", "COUNTIFS", "SUMIF", "SUMIFS", "AVERAGEIF", "AVERAGEIFS",
            "MINIFS", "MAXIFS"
    }:
        return ErrorValue("#VALUE!", f"{name} does not accept a range argument in this evaluator")
    flat = _flatten(args)
    if any(isinstance(value, ErrorValue) for value in flat):
        return next(value for value in flat if isinstance(value, ErrorValue))
    if name == "NPER":
        return _nper_call(args)
    if name in {"SLN", "SYD", "DB", "DDB", "VDB"}:
        return _depreciation_call(name, args)
    if name in {"AMORLINC", "AMORDEGRC"}:
        return _amor_depreciation_call(name, args)
    if name in {"IPMT", "PPMT"}:
        return _payment_component_call(name, args)
    if name in {"CUMIPMT", "CUMPRINC"}:
        return _cumulative_payment_call(name, args)
    if name in {"FV", "PV", "PMT"}:
        return _tvm_call(name, args)
    if name in {"SUM", "AVERAGE", "COUNT", "MIN", "MAX"}:
        numbers = _numeric_values(flat)
        if isinstance(numbers, ErrorValue):
            return numbers
        if name == "COUNT":
            return sum(isinstance(value, (int, float)) and not isinstance(value, bool) for value in flat)
        if name == "SUM":
            return sum(numbers)
        if name == "AVERAGE":
            return ErrorValue("#DIV/0!", "AVERAGE has no numeric values") if not numbers else sum(numbers) / len(numbers)
        if not numbers:
            return 0
        return min(numbers) if name == "MIN" else max(numbers)
    if name == "COUNTA":
        return sum(value is not None for value in flat)
    if name in {"AND", "OR"}:
        truth_values = [_truth(value) for value in flat if value is not None]
        if any(isinstance(value, ErrorValue) for value in truth_values):
            return next(value for value in truth_values if isinstance(value, ErrorValue))
        return all(truth_values) if name == "AND" else any(truth_values)
    if name == "NOT":
        result = _truth(args[0])
        return result if isinstance(result, ErrorValue) else not result
    if name == "ABS":
        value = _number(args[0])
        return value if isinstance(value, ErrorValue) else abs(value)
    if name in {"GCD", "LCM", "FACT", "FACTDOUBLE", "COMBIN", "COMBINA", "PERMUT", "PERMUTATIONA"}:
        return _integer_formula_call(name, args)
    if name in {"EVEN", "ODD"}:
        return _round_to_parity(args[0], odd=name == "ODD")
    if name in {"ROUND", "ROUNDUP", "ROUNDDOWN"}:
        number, digits_value = _number(args[0]), args[1]
        if isinstance(number, ErrorValue):
            return number
        digits = _decimal_digits(digits_value)
        if isinstance(digits, ErrorValue):
            return digits
        mode = {"ROUND": "nearest", "ROUNDUP": "away-from-zero", "ROUNDDOWN": "toward-zero"}[name]
        return _round_to_precision(number, digits, mode)
    if name == "INT":
        number = _number(args[0])
        if isinstance(number, ErrorValue):
            return number
        try:
            numeric = float(number)
        except (OverflowError, TypeError, ValueError):
            return ErrorValue("#NUM!", "number is outside the supported numeric range")
        if not math.isfinite(numeric):
            return ErrorValue("#NUM!", "number must be finite")
        return math.floor(numeric)
    if name in {"HOUR", "MINUTE", "SECOND"}:
        serial = _number(args[0])
        if isinstance(serial, ErrorValue):
            return serial
        try:
            hour, minute, second = _excel_time_hms(float(serial))
        except (OverflowError, ValueError):
            return ErrorValue("#NUM!", "date-time serial is outside the supported range")
        return {"HOUR": hour, "MINUTE": minute, "SECOND": second}[name]
    if name == "TIME":
        units: list[int] = []
        for argument in args:
            number = _number(argument)
            if isinstance(number, ErrorValue):
                return number
            try:
                numeric = float(number)
            except (OverflowError, TypeError, ValueError):
                return ErrorValue("#NUM!", "TIME arguments are outside the supported range")
            if not math.isfinite(numeric) or not 0 <= numeric <= 32_767:
                return ErrorValue("#NUM!", "TIME arguments must be from 0 through 32767")
            units.append(math.trunc(numeric))
        total_seconds = units[0] * 3_600 + units[1] * 60 + units[2]
        return (total_seconds % 86_400) / 86_400
    if name in {"MONTH", "DAY", "YEAR"}:
        serial = _number(args[0])
        if isinstance(serial, ErrorValue):
            return serial
        try:
            year, month, day = _excel_serial_ymd(float(serial))
        except (OverflowError, ValueError):
            return ErrorValue("#NUM!", "date serial is outside supported range")
        return {"MONTH": month, "DAY": day, "YEAR": year}[name]
    if name == "DAYS":
        end_date, start_date = (_number(value) for value in args)
        if isinstance(end_date, ErrorValue):
            return end_date
        if isinstance(start_date, ErrorValue):
            return start_date
        try:
            end_number, start_number = float(end_date), float(start_date)
        except (OverflowError, TypeError, ValueError):
            return ErrorValue("#NUM!", "date serial is outside supported range")
        if not math.isfinite(end_number) or not math.isfinite(start_number):
            return ErrorValue("#NUM!", "date serial must be finite")
        try:
            _excel_serial_ymd(end_number)
            _excel_serial_ymd(start_number)
        except (OverflowError, ValueError):
            return ErrorValue("#NUM!", "date serial is outside supported range")
        return end_number - start_number
    if name in {"DAYS360", "YEARFRAC"}:
        date_error_code = "#NUM!" if name == "DAYS360" else "#VALUE!"
        start_serial = _day_count_serial(args[0], error_code=date_error_code)
        if isinstance(start_serial, ErrorValue):
            return start_serial
        end_serial = _day_count_serial(args[1], error_code=date_error_code)
        if isinstance(end_serial, ErrorValue):
            return end_serial
        if name == "DAYS360":
            method = 0 if len(args) < 3 or args[2] is None else args[2]
            if isinstance(method, bool):
                european = method
            elif isinstance(method, (int, float)):
                if isinstance(method, float) and not math.isfinite(method):
                    return ErrorValue("#VALUE!", "DAYS360 method must be FALSE/0 or TRUE/1")
                if method not in (0, 1):
                    return ErrorValue("#VALUE!", "DAYS360 method must be FALSE/0 or TRUE/1")
                european = method == 1
            else:
                return ErrorValue("#VALUE!", "DAYS360 method must be FALSE/0 or TRUE/1")
            return _days360_serials(start_serial, end_serial, european=european)

        basis_value: object = 0 if len(args) < 3 else args[2]
        basis_number = _number(basis_value)
        if isinstance(basis_number, ErrorValue):
            return basis_number
        if isinstance(basis_number, float) and not math.isfinite(basis_number):
            return ErrorValue("#NUM!", "YEARFRAC basis must be from 0 through 4")
        basis = math.trunc(basis_number)
        if not 0 <= basis <= 4:
            return ErrorValue("#NUM!", "YEARFRAC basis must be from 0 through 4")
        if basis in (0, 4):
            return _days360_serials(start_serial, end_serial, european=(basis == 4)) / 360.0
        actual_days = end_serial - start_serial
        if basis == 1:
            return _yearfrac_actual_actual(start_serial, end_serial)
        return actual_days / (360.0 if basis == 2 else 365.0)
    if name in {"COUPDAYBS", "COUPDAYS", "COUPDAYSNC", "COUPNCD", "COUPNUM", "COUPPCD"}:
        if len(args) < 3 or len(args) > 4:
            return _arity(name, "3 or 4", len(args))
        settlement = _day_count_serial(args[0], error_code="#VALUE!")
        if isinstance(settlement, ErrorValue):
            return settlement
        maturity = _day_count_serial(args[1], error_code="#VALUE!")
        if isinstance(maturity, ErrorValue):
            return maturity
        frequency_value = _number(args[2])
        if isinstance(frequency_value, ErrorValue):
            return frequency_value
        if isinstance(frequency_value, float) and not math.isfinite(frequency_value):
            return ErrorValue("#NUM!", "coupon frequency must be 1, 2, or 4")
        frequency = math.trunc(frequency_value)
        if frequency not in (1, 2, 4):
            return ErrorValue("#NUM!", "coupon frequency must be 1, 2, or 4")
        basis_value = 0 if len(args) < 4 or args[3] is None else _number(args[3])
        if isinstance(basis_value, ErrorValue):
            return basis_value
        if isinstance(basis_value, float) and not math.isfinite(basis_value):
            return ErrorValue("#NUM!", "coupon basis must be from 0 through 4")
        basis = math.trunc(basis_value)
        if not 0 <= basis <= 4:
            return ErrorValue("#NUM!", "coupon basis must be from 0 through 4")
        if settlement >= maturity:
            return ErrorValue("#NUM!", "settlement must be earlier than maturity")

        schedule = _coupon_schedule(settlement, maturity, frequency)
        days_bs = (
            _days360_serials(schedule.previous, settlement, european=(basis == 4))
            if basis in (0, 4)
            else settlement - schedule.previous
        )
        if name == "COUPDAYBS":
            return days_bs
        if name == "COUPDAYS":
            if basis == 1:
                return schedule.next - schedule.previous
            return 365.0 / frequency if basis == 3 else 360 // frequency
        if name == "COUPDAYSNC":
            if basis == 0:
                period_days = 360 // frequency
                return period_days - days_bs
            if basis == 4:
                return _days360_serials(settlement, schedule.next, european=True)
            return schedule.next - settlement
        if name == "COUPNCD":
            return schedule.next
        if name == "COUPNUM":
            return schedule.coupons_remaining
        return schedule.previous_strict
    if name in {"EDATE", "EOMONTH"}:
        start_number, months_number = (_number(value) for value in args)
        if isinstance(start_number, ErrorValue):
            return start_number
        if isinstance(months_number, ErrorValue):
            return months_number
        try:
            serial = float(start_number)
            months_value = float(months_number)
        except (OverflowError, TypeError, ValueError):
            return ErrorValue("#NUM!", "date arguments are outside supported range")
        if not math.isfinite(serial) or not math.isfinite(months_value):
            return ErrorValue("#NUM!", "date arguments must be finite")
        try:
            year, month, day = _excel_serial_ymd(serial)
        except (OverflowError, ValueError):
            code = "#VALUE!" if name == "EDATE" else "#NUM!"
            return ErrorValue(code, "start_date is outside the supported date range")
        month_offset = math.trunc(months_value)
        target_index = year * 12 + (month - 1) + month_offset
        target_year, target_month_zero = divmod(target_index, 12)
        target_month = target_month_zero + 1
        if not 1 <= target_year <= 9999:
            return ErrorValue("#NUM!", "target month is outside the supported date range")
        if name == "EDATE":
            last_day = _days_in_month(target_year, target_month)
            target_day = min(day, last_day)
        else:
            target_day = _days_in_month(target_year, target_month)
        try:
            return _excel_ymd_to_serial(target_year, target_month, target_day)
        except (OverflowError, ValueError):
            return ErrorValue("#NUM!", "target date is outside the supported date range")
    if name == "MOD":
        number, divisor = _number(args[0]), _number(args[1])
        if isinstance(number, ErrorValue):
            return number
        if isinstance(divisor, ErrorValue):
            return divisor
        try:
            numerator, denominator = float(number), float(divisor)
        except (OverflowError, TypeError, ValueError):
            return ErrorValue("#NUM!", "MOD arguments are outside the supported numeric range")
        if not math.isfinite(numerator) or not math.isfinite(denominator):
            return ErrorValue("#NUM!", "MOD arguments must be finite")
        if denominator == 0:
            return ErrorValue("#DIV/0!", "MOD divisor is zero")
        quotient = numerator / denominator
        if not math.isfinite(quotient):
            return ErrorValue("#NUM!", "MOD quotient is outside the supported numeric range")
        result = numerator - denominator * math.floor(quotient)
        return result if math.isfinite(result) else ErrorValue("#NUM!", "MOD result is not finite")
    if name == "QUOTIENT":
        number, divisor = _number(args[0]), _number(args[1])
        if isinstance(number, ErrorValue):
            return number
        if isinstance(divisor, ErrorValue):
            return divisor
        try:
            numerator, denominator = float(number), float(divisor)
        except (OverflowError, TypeError, ValueError):
            return ErrorValue("#NUM!", "QUOTIENT arguments are outside the supported numeric range")
        if not math.isfinite(numerator) or not math.isfinite(denominator):
            return ErrorValue("#NUM!", "QUOTIENT arguments must be finite")
        if denominator == 0:
            return ErrorValue("#DIV/0!", "QUOTIENT divisor is zero")
        quotient = numerator / denominator
        if not math.isfinite(quotient):
            return ErrorValue("#NUM!", "QUOTIENT result is outside the supported numeric range")
        return math.trunc(quotient)
    if name in {"LEFT", "RIGHT"}:
        text = _text(args[0])
        if isinstance(text, ErrorValue):
            return text
        count = 1 if len(args) == 1 else _number(args[1])
        if isinstance(count, ErrorValue):
            return count
        if not isinstance(count, int) or count < 0:
            return ErrorValue("#VALUE!", "character count must be a non-negative integer")
        return text[:count] if name == "LEFT" else (text[-count:] if count else "")
    if name == "MID":
        text = _text(args[0])
        if isinstance(text, ErrorValue):
            return text
        start, count = _number(args[1]), _number(args[2])
        if isinstance(start, ErrorValue):
            return start
        if isinstance(count, ErrorValue):
            return count
        if not isinstance(start, int) or not isinstance(count, int) or start < 1 or count < 0:
            return ErrorValue("#VALUE!", "MID start must be at least 1 and length non-negative")
        return text[start - 1 : start - 1 + count]
    if name == "LEN":
        text = _text(args[0])
        return text if isinstance(text, ErrorValue) else len(text)
    if name == "UPPER":
        text = _text(args[0])
        return text if isinstance(text, ErrorValue) else _check_text_result(text.upper())
    if name == "LOWER":
        text = _text(args[0])
        return text if isinstance(text, ErrorValue) else _check_text_result(text.lower())
    if name == "TRIM":
        # Excel TRIM removes ASCII spaces at the ends and collapses runs of
        # ASCII spaces internally; it does not normalize every Unicode space.
        text = _text(args[0])
        return text if isinstance(text, ErrorValue) else re.sub(r" +", " ", text.strip(" "))
    if name == "SUBSTITUTE":
        text, old, new = (_text(value) for value in args[:3])
        for value in (text, old, new):
            if isinstance(value, ErrorValue):
                return value
        if len(args) == 3:
            return _substitute_bounded(text, old, new)
        instance = _number(args[3])
        if isinstance(instance, ErrorValue):
            return instance
        if not isinstance(instance, int) or instance < 1:
            return ErrorValue("#VALUE!", "SUBSTITUTE instance_num must be a positive integer")
        return _substitute_bounded(text, old, new, instance)
    if name in {"FIND", "SEARCH"}:
        needle, haystack = _text(args[0]), _text(args[1])
        if isinstance(needle, ErrorValue):
            return needle
        if isinstance(haystack, ErrorValue):
            return haystack
        start = 1 if len(args) < 3 else _number(args[2])
        if isinstance(start, ErrorValue):
            return start
        if not isinstance(start, int) or start < 1 or start > len(haystack) + 1:
            return ErrorValue("#VALUE!", f"{name} start_num must be from 1 through text length plus 1")
        if name == "SEARCH":
            # Wildcards are deliberately treated as ordinary characters here.
            needle, haystack = needle.casefold(), haystack.casefold()
        found = haystack.find(needle, start - 1)
        return found + 1 if found >= 0 else ErrorValue("#VALUE!", f"{name} text was not found")
    if name == "TEXTJOIN":
        delimiter = _text(args[0])
        if isinstance(delimiter, ErrorValue):
            return delimiter
        ignore_empty = _truth(args[1])
        if isinstance(ignore_empty, ErrorValue):
            return ignore_empty

        def pieces() -> Iterable[str | ErrorValue]:
            for value in _flatten(args[2:]):
                piece = _text(value)
                if isinstance(piece, ErrorValue) or not (ignore_empty and piece == ""):
                    yield piece

        return _join_text_bounded(pieces(), delimiter, "TEXTJOIN result")
    if name == "CONCAT":
        return _join_text_bounded(
            (_text(value) for value in flat if value is not None), operation="CONCAT result"
        )
    if name == "DATE":
        year, month, day = (_number(value) for value in args)
        for value in (year, month, day):
            if isinstance(value, ErrorValue):
                return value
        if not all(isinstance(value, int) for value in (year, month, day)):
            return ErrorValue("#VALUE!", "DATE arguments must be integers")
        if year < 0 or year > 9999:
            return ErrorValue("#NUM!", "DATE year is outside Excel's supported range")
        # Excel maps years 0..1899 to 1900..3799 and normalizes month overflow.
        year = year + 1900 if year < 1900 else year
        year += (month - 1) // 12
        month = (month - 1) % 12 + 1
        try:
            month_start = date(year, month, 1)
            # Add the day offset in Excel serial space so serial 60 remains
            # the fictional 1900-02-29 during DATE overflow normalization.
            serial = _date_to_excel_serial(month_start) + day - 1
            if not 0 <= serial <= _MAX_EXCEL_DATE_SERIAL:
                return ErrorValue("#NUM!", "DATE result is outside Excel's supported range")
            return serial
        except (OverflowError, ValueError):
            return ErrorValue("#NUM!", "DATE result is outside supported date range")
    if name in {"DAY", "YEAR"}:
        serial = _number(args[0])
        if isinstance(serial, ErrorValue):
            return serial
        if not isinstance(serial, (int, float)) or not math.isfinite(serial):
            return ErrorValue("#NUM!", "date serial must be finite")
        try:
            day, year = _excel_serial_day_year(serial)
            return day if name == "DAY" else year
        except (OverflowError, ValueError):
            return ErrorValue("#NUM!", "date serial is outside supported range")
    if name == "MATCH":
        lookup = args[0]
        source = args[1]
        if not isinstance(source, _Range):
            return ErrorValue("#VALUE!", "MATCH lookup_array must be a cell range")
        if source.rows != 1 and source.columns != 1:
            return ErrorValue("#VALUE!", "MATCH lookup_array must be one-dimensional")
        vector = list(source.values)
        match_type = 1 if len(args) < 3 else _number(args[2])
        if isinstance(match_type, ErrorValue):
            return match_type
        if match_type not in (-1, 0, 1):
            return ErrorValue("#VALUE!", "MATCH type must be -1, 0, or 1")
        if match_type == 0:
            pattern_cache = [_UNSET_WILDCARD]
            for index, value in enumerate(vector, 1):
                matched = _wildcard_lookup_match(value, lookup, budget, pattern_cache)
                if isinstance(matched, ErrorValue):
                    return matched
                if matched:
                    return index
        else:
            candidate = None
            for index, value in enumerate(vector, 1):
                try:
                    valid = value <= lookup if match_type == 1 else value >= lookup
                except TypeError:
                    return ErrorValue("#VALUE!", "MATCH values are not comparable")
                if valid:
                    candidate = index
                else:
                    break
            if candidate is not None:
                return candidate
        return ErrorValue("#N/A", "MATCH value not found")
    if name == "XMATCH":
        if len(args) >= 3 and isinstance(args[2], ErrorValue):
            return args[2]
        match_mode = 0 if len(args) < 3 else _number(args[2])
        search_mode = 1 if len(args) < 4 else _number(args[3])
        return _exact_match(args[0], args[1], match_mode, search_mode, "XMATCH")
    if name == "VLOOKUP":
        lookup = args[0]
        table = args[1]
        if not isinstance(table, _Range):
            return ErrorValue("#VALUE!", "VLOOKUP table_array must be a cell range")
        column = _number(args[2])
        if isinstance(column, ErrorValue):
            return column
        if not isinstance(column, int) or column < 1 or column > table.columns:
            return ErrorValue("#REF!", "VLOOKUP column index is outside table_array")
        exact = False  # Excel defaults VLOOKUP to approximate matching.
        if len(args) == 4:
            exact_value = _truth(args[3])
            if isinstance(exact_value, ErrorValue):
                return exact_value
            exact = not exact_value
        first_column = [table.values[row * table.columns] for row in range(table.rows)]
        candidate: int | None = None
        pattern_cache = [_UNSET_WILDCARD]
        for index, value in enumerate(first_column):
            matched = (
                _wildcard_lookup_match(value, lookup, budget, pattern_cache)
                if exact
                else _is_lookup_match(value, lookup)
            )
            if isinstance(matched, ErrorValue):
                return matched
            if matched:
                candidate = index
                break
            if not exact:
                try:
                    if value < lookup:
                        candidate = index
                    elif value > lookup:
                        break
                except TypeError:
                    return ErrorValue("#VALUE!", "VLOOKUP values are not comparable")
        if candidate is None:
            return ErrorValue("#N/A", "VLOOKUP value not found")
        return table.values[candidate * table.columns + column - 1]
    if name == "INDEX":
        array = args[0]
        if not isinstance(array, _Range):
            return ErrorValue("#VALUE!", "INDEX array must be a cell range")
        row = _number(args[1])
        column = 1 if len(args) < 3 else _number(args[2])
        area = 1 if len(args) < 4 else _number(args[3])
        if isinstance(row, ErrorValue):
            return row
        if isinstance(column, ErrorValue):
            return column
        if isinstance(area, ErrorValue):
            return area
        if not isinstance(area, int) or area < 1:
            return ErrorValue("#VALUE!", "INDEX area_num must be a positive integer")
        if area != 1:
            return ErrorValue("#REF!", "INDEX supports only area_num=1 for a single rectangular reference")
        if not isinstance(row, int) or not isinstance(column, int) or row < 1 or column < 1:
            return ErrorValue("#VALUE!", "INDEX row and column numbers must be positive integers")
        if row > array.rows or column > array.columns:
            return ErrorValue("#REF!", "INDEX row or column is outside the array")
        return array.values[(row - 1) * array.columns + column - 1]
    return ErrorValue("#NAME?", f"unsupported function {name}")


def _nper_call(args: tuple[object, ...]) -> object:
    """Invert the periodic cash-flow equation under an explicit scalar profile."""
    values: list[float] = []
    for argument in args:
        number = _number(argument)
        if isinstance(number, ErrorValue):
            return number
        try:
            numeric = float(number)
        except (OverflowError, TypeError, ValueError):
            return ErrorValue("#NUM!", "financial argument is outside the supported numeric range")
        if not math.isfinite(numeric):
            return ErrorValue("#NUM!", "financial arguments must be finite")
        values.append(numeric)

    rate, payment, present_value = values[:3]
    optional = values[3:] + [0.0] * (2 - len(values[3:]))
    future_value, payment_type = optional
    if rate <= -1.0:
        return ErrorValue("#NUM!", "rate at or below -100% is outside this evaluator profile")
    if payment_type not in (0.0, 1.0):
        return ErrorValue("#NUM!", "type must be 0 or 1 in this evaluator")

    scale = max(abs(payment), abs(present_value), abs(future_value))
    if scale == 0.0:
        return ErrorValue("#NUM!", "NPER has no unique finite solution for zero cash flows")
    scaled_payment = payment / scale
    scaled_present = present_value / scale
    scaled_future = future_value / scale

    if rate == 0.0:
        total_value = scaled_present + scaled_future
        if payment == 0.0:
            return ErrorValue("#NUM!", "NPER has no unique finite solution when rate and payment are zero")
        if total_value == 0.0:
            return 0.0
        if scaled_payment == 0.0:
            return ErrorValue("#NUM!", "NPER result exceeds the supported numeric range")
        result = -total_value / scaled_payment
    else:
        rate_scale = max(1.0, abs(rate))
        scaled_rate = rate / rate_scale
        payment_factor = (1.0 + rate * payment_type) / rate_scale
        numerator = scaled_payment * payment_factor - scaled_future * scaled_rate
        denominator = scaled_payment * payment_factor + scaled_present * scaled_rate
        if not math.isfinite(numerator) or not math.isfinite(denominator):
            return ErrorValue("#NUM!", "NPER logarithm terms exceed the supported numeric range")
        if numerator == 0.0 or denominator == 0.0:
            return ErrorValue("#NUM!", "NPER has no finite logarithmic solution")
        if (numerator < 0.0) != (denominator < 0.0):
            return ErrorValue("#NUM!", "NPER logarithm ratio has no real solution")

        # This difference-over-denominator form preserves tiny rate effects
        # that would disappear if numerator / denominator were rounded first.
        try:
            ratio_delta = -(scaled_rate * (scaled_present + scaled_future) / denominator)
        except OverflowError:
            ratio_delta = math.inf
        if math.isfinite(ratio_delta) and ratio_delta > -1.0:
            log_ratio = math.log1p(ratio_delta)
        else:
            # Taking the difference of logs avoids overflow in the raw ratio.
            log_ratio = math.log(abs(numerator)) - math.log(abs(denominator))
        log_growth = math.log1p(rate)
        result = log_ratio / log_growth

    if not math.isfinite(result):
        return ErrorValue("#NUM!", "NPER result must be finite")
    return result


_MAX_EXACT_DEPRECIATION_PERIOD = 9_007_199_254_740_991


def _depreciation_period(value: float, label: str) -> int | ErrorValue:
    """Truncate a period input and keep it within the exact binary64 integer range."""
    if abs(value) > _MAX_EXACT_DEPRECIATION_PERIOD:
        return ErrorValue("#NUM!", f"{label} exceeds the supported exact-period range")
    return math.trunc(value)


def _db_rate(cost: float, salvage: float, life: int) -> float:
    """Return DB's three-decimal rate without losing near-equal cost/salvage bits."""
    if salvage == 0.0:
        return 1.0
    ratio = salvage / cost
    log_ratio = (
        math.log1p((salvage - cost) / cost)
        if ratio > 0.5
        else math.log(salvage) - math.log(cost)
    )
    raw_rate = -math.expm1(log_ratio / life)
    return math.floor(raw_rate * 1000.0 + 0.5) / 1000.0


def _db_depreciation(cost: float, salvage: float, life: float, period: float, month: float) -> float | ErrorValue:
    if cost <= 0.0 or salvage < 0.0 or salvage > cost:
        return ErrorValue("#NUM!", "DB profile requires cost>0 and 0<=salvage<=cost")
    integer_life = _depreciation_period(life, "life")
    integer_period = _depreciation_period(period, "period")
    integer_month = _depreciation_period(month, "month")
    if isinstance(integer_life, ErrorValue):
        return integer_life
    if isinstance(integer_period, ErrorValue):
        return integer_period
    if isinstance(integer_month, ErrorValue):
        return integer_month
    if integer_life < 1 or integer_period < 1 or integer_period > integer_life + 1:
        return ErrorValue("#NUM!", "DB life and period must fit the positive life-plus-final-period profile")
    if not 1 <= integer_month <= 12:
        return ErrorValue("#NUM!", "DB month must truncate to an integer from 1 through 12")

    rate = _db_rate(cost, salvage, integer_life)
    first = cost * rate * (integer_month / 12.0)
    remaining_after_first = cost - first
    if integer_period == 1:
        result = first
    elif integer_period == integer_life + 1:
        remaining = remaining_after_first * math.pow(1.0 - rate, integer_life - 1)
        result = remaining * rate * ((12 - integer_month) / 12.0)
    else:
        remaining = remaining_after_first * math.pow(1.0 - rate, integer_period - 2)
        result = remaining * rate
    return result if math.isfinite(result) else ErrorValue("#NUM!", "DB result must be finite")


def _ddb_depreciation(cost: float, salvage: float, life: float, period: float, factor: float) -> float | ErrorValue:
    if cost < 0.0 or salvage < 0.0 or salvage > cost or factor <= 0.0:
        return ErrorValue("#NUM!", "DDB profile requires cost>=salvage>=0 and factor>0")
    integer_life = _depreciation_period(life, "life")
    integer_period = _depreciation_period(period, "period")
    if isinstance(integer_life, ErrorValue):
        return integer_life
    if isinstance(integer_period, ErrorValue):
        return integer_period
    if integer_life < 1 or integer_period < 1 or integer_period > integer_life:
        return ErrorValue("#NUM!", "DDB period must truncate to an integer from 1 through life")

    rate = factor / integer_life
    depreciable_basis = cost - salvage
    if integer_period == 1 and rate >= 1.0:
        return depreciable_basis
    if rate == 0.0:
        if cost == 0.0:
            return 0.0
        # A positive factor divided by a large life can underflow to zero
        # even when cost*factor/life is representable. Compute that amount
        # in log space; the omitted geometric decay is below binary64
        # precision throughout the accepted period range for this branch.
        try:
            amount = math.exp(math.log(cost) + math.log(factor) - math.log(integer_life))
        except OverflowError:
            return ErrorValue("#NUM!", "DDB result must be finite")
        result = min(amount, depreciable_basis)
        return result if math.isfinite(result) else ErrorValue("#NUM!", "DDB result must be finite")
    if rate >= 1.0:
        return 0.0
    book_value = cost * math.pow(1.0 - rate, integer_period - 1)
    available = max(book_value - salvage, 0.0)
    result = min(book_value * rate, available)
    return result if math.isfinite(result) else ErrorValue("#NUM!", "DDB result must be finite")


def _vdb_switches_at(
    cost: float,
    salvage: float,
    life: float,
    factor: float,
    rate: float,
    log_q: float,
    period: int,
) -> bool:
    remaining_life = life - period
    if remaining_life <= 0.0 or cost <= salvage:
        return False
    if rate >= 1.0:
        return period == 0 and life < 1.0

    log_book = math.log(cost) + period * log_q
    if salvage > 0.0 and log_book <= math.log(salvage):
        return False
    if remaining_life < 1.0:
        # The DDB amount is capped at the remaining basis, while SLN spreads
        # that basis over a sub-period. The reference behavior switches here.
        return True

    log_available = (
        log_book
        if salvage == 0.0
        else log_book + math.log(-math.expm1(math.log(salvage) - log_book))
    )
    log_ddb = log_book + math.log(factor) - math.log(life)
    log_sln = log_available - math.log(remaining_life)
    return log_ddb < log_sln


def _vdb_switch_period(cost: float, salvage: float, life: float, factor: float, rate: float, log_q: float) -> int | None:
    """Find the first DDB period where SLN wins, in logarithmic time."""
    if cost <= salvage:
        return None
    period_count = math.ceil(life)
    last = period_count - 1
    if not _vdb_switches_at(cost, salvage, life, factor, rate, log_q, last):
        return None
    low = 0
    high = last
    while low < high:
        middle = (low + high) // 2
        if _vdb_switches_at(cost, salvage, life, factor, rate, log_q, middle):
            high = middle
        else:
            low = middle + 1
    return low


def _vdb_cap_period(cost: float, salvage: float, life: float, rate: float, log_q: float) -> int | None:
    """Find the final capped DDB period without traversing the schedule."""
    if salvage <= 0.0 or cost <= salvage or rate == 0.0:
        return None
    if rate >= 1.0:
        return 0

    period_count = math.ceil(life)
    log_cost = math.log(cost)
    log_salvage = math.log(salvage)

    def capped_after(period: int) -> bool:
        return log_cost + (period + 1) * log_q <= log_salvage

    last = period_count - 1
    if not capped_after(last):
        return None
    low = 0
    high = last
    while low < high:
        middle = (low + high) // 2
        if capped_after(middle):
            high = middle
        else:
            low = middle + 1
    return low


def _vdb_period_amount(
    cost: float,
    salvage: float,
    basis: float,
    factor: float,
    life: float,
    rate: float,
    log_q: float,
    period: int,
) -> float:
    if basis <= 0.0:
        return 0.0
    if rate >= 1.0:
        return basis if period == 0 else 0.0
    if rate == 0.0:
        amount = math.exp(math.log(cost) + math.log(factor) - math.log(life))
        prior = amount * period
        remaining = max(basis - min(prior, basis), 0.0)
        return min(amount, remaining)

    log_book = math.log(cost) + period * log_q
    book = math.exp(log_book)
    available = max(book - salvage, 0.0)
    return min(book * rate, available)


def _vdb_geometric_sum(cost: float, rate: float, log_q: float, start: int, count: int) -> float:
    if count <= 0:
        return 0.0
    log_first_book = math.log(cost) + start * log_q
    first_book = math.exp(log_first_book)
    fraction_depreciated = -math.expm1(count * log_q)
    return max(first_book * fraction_depreciated, 0.0)


def _vdb_declining_sum(
    cost: float,
    salvage: float,
    basis: float,
    factor: float,
    life: float,
    rate: float,
    log_q: float,
    cap_period: int | None,
    start: int,
    count: int,
) -> float:
    if count <= 0 or basis <= 0.0:
        return 0.0
    if rate >= 1.0:
        return basis if start == 0 else 0.0
    if rate == 0.0:
        amount = math.exp(math.log(cost) + math.log(factor) - math.log(life))
        prior = amount * start
        already_depreciated = min(prior, basis)
        available = max(basis - already_depreciated, 0.0)
        return min(amount * count, available)

    uncapped_count = count if cap_period is None else max(min(count, cap_period - start), 0)
    total = _vdb_geometric_sum(cost, rate, log_q, start, uncapped_count)
    if cap_period is not None and start <= cap_period < start + count:
        log_cap_book = math.log(cost) + cap_period * log_q
        total += max(math.exp(log_cap_book) - salvage, 0.0)
    return min(max(total, 0.0), basis)


def _vdb_declining_interval(
    start: float,
    end: float,
    cost: float,
    salvage: float,
    basis: float,
    factor: float,
    life: float,
    rate: float,
    log_q: float,
    cap_period: int | None,
) -> float:
    first_period = math.floor(start)
    last_period = math.floor(end)
    if first_period == last_period:
        amount = _vdb_period_amount(cost, salvage, basis, factor, life, rate, log_q, first_period)
        return amount * (end - start)

    first_amount = _vdb_period_amount(cost, salvage, basis, factor, life, rate, log_q, first_period)
    total = first_amount * (first_period + 1.0 - start)
    interior_start = first_period + 1
    interior_count = max(last_period - interior_start, 0)
    total += _vdb_declining_sum(
        cost, salvage, basis, factor, life, rate, log_q, cap_period, interior_start, interior_count
    )
    if end > last_period:
        last_amount = _vdb_period_amount(cost, salvage, basis, factor, life, rate, log_q, last_period)
        total += last_amount * (end - last_period)
    return min(max(total, 0.0), basis)


def _vdb_depreciation(
    cost: float,
    salvage: float,
    life: float,
    start: float,
    end: float,
    factor: float,
    no_switch: bool,
) -> float | ErrorValue:
    if cost <= 0.0 or salvage < 0.0 or salvage > cost or life <= 0.0 or factor <= 0.0:
        return ErrorValue("#NUM!", "VDB profile requires cost>0, 0<=salvage<=cost, life>0, and factor>0")
    if life > _MAX_EXACT_DEPRECIATION_PERIOD:
        return ErrorValue("#NUM!", "VDB life exceeds the supported exact-period range")
    if start < 0.0 or end < start or end > life:
        return ErrorValue("#NUM!", "VDB profile requires 0<=start_period<=end_period<=life")

    basis = cost - salvage
    if basis == 0.0 or start == end:
        return 0.0

    rate = 1.0 if factor >= life else factor / life
    log_q = math.log1p(-rate) if rate < 1.0 else 0.0
    cap_period = _vdb_cap_period(cost, salvage, life, rate, log_q)
    switch_period = None if no_switch else _vdb_switch_period(cost, salvage, life, factor, rate, log_q)
    if switch_period is None or end <= switch_period:
        result = _vdb_declining_interval(
            start, end, cost, salvage, basis, factor, life, rate, log_q, cap_period
        )
    else:
        result = 0.0
        if start < switch_period:
            result += _vdb_declining_interval(
                start, float(switch_period), cost, salvage, basis, factor, life, rate, log_q, cap_period
            )
        straight_start = max(start, float(switch_period))
        log_book = math.log(cost) + switch_period * log_q if rate < 1.0 else math.log(cost)
        remaining_basis = max(math.exp(log_book) - salvage, 0.0)
        remaining_life = life - switch_period
        result += remaining_basis * ((end - straight_start) / remaining_life)
        result = min(result, basis)

    return result if math.isfinite(result) else ErrorValue("#NUM!", "VDB result must be finite")


def _depreciation_call(name: str, args: tuple[object, ...]) -> object:
    """Evaluate scalar depreciation kernels after shared coercion and validation."""
    if name == "VDB":
        values: list[float] = []
        for argument in args[:5]:
            number = _number(argument)
            if isinstance(number, ErrorValue):
                return number
            try:
                numeric = float(number)
            except (OverflowError, TypeError, ValueError):
                return ErrorValue("#NUM!", "VDB argument is outside the supported numeric range")
            if not math.isfinite(numeric):
                return ErrorValue("#NUM!", "VDB arguments must be finite")
            values.append(numeric)

        factor = 2.0
        if len(args) >= 6 and args[5] is not _OPTIONAL_OMITTED:
            number = _number(args[5])
            if isinstance(number, ErrorValue):
                return number
            try:
                factor = float(number)
            except (OverflowError, TypeError, ValueError):
                return ErrorValue("#NUM!", "VDB factor is outside the supported numeric range")
            if not math.isfinite(factor):
                return ErrorValue("#NUM!", "VDB arguments must be finite")

        no_switch = False
        if len(args) >= 7 and args[6] is not _OPTIONAL_OMITTED:
            no_switch = _truth(args[6])
            if isinstance(no_switch, ErrorValue):
                return no_switch
            if isinstance(args[6], (int, float)) and not isinstance(args[6], bool):
                if not math.isfinite(float(args[6])):
                    return ErrorValue("#NUM!", "VDB arguments must be finite")

        return _vdb_depreciation(*values, factor, no_switch)

    values: list[float] = []
    for argument in args:
        number = _number(argument)
        if isinstance(number, ErrorValue):
            return number
        try:
            numeric = float(number)
        except (OverflowError, TypeError, ValueError):
            return ErrorValue("#NUM!", "depreciation argument is outside the supported numeric range")
        if not math.isfinite(numeric):
            return ErrorValue("#NUM!", "depreciation arguments must be finite")
        values.append(numeric)

    cost, salvage, life = values[:3]
    if name == "DB":
        month = 12.0 if len(args) == 4 else values[4]
        return _db_depreciation(cost, salvage, life, values[3], month)
    if name == "DDB":
        factor = 2.0 if len(args) == 4 else values[4]
        return _ddb_depreciation(cost, salvage, life, values[3], factor)
    if cost < 0.0 or salvage < 0.0 or life <= 0.0:
        return ErrorValue("#NUM!", "cost and salvage must be nonnegative and life must be positive")

    depreciable_basis = cost - salvage
    if name == "SLN":
        result = depreciable_basis / life
    else:
        period = values[3]
        if period <= 0.0 or period > life:
            return ErrorValue("#NUM!", "period must be greater than zero and no greater than life")
        # Keep the period weight bounded before scaling the basis. Dividing
        # 2 by a tiny fractional life first can overflow even when the final
        # product is finite.
        period_weight = 2.0 * ((life - period + 1.0) / (life + 1.0))
        result = (depreciable_basis / life) * period_weight

    if not math.isfinite(result):
        return ErrorValue("#NUM!", "depreciation result must be finite")
    return result


def _round_half_away_from_zero(value: float) -> float:
    """Round a finite number to an integer, resolving exact halves away from zero."""
    rounded = math.floor(abs(value) + 0.5)
    return math.copysign(float(rounded), value)


def _amor_stub_day_count(purchase: int, first_period: int, basis: int) -> tuple[int, int]:
    """Return stub days and the basis denominator, counting serial 60 as 1900-02-29."""
    if basis in (0, 4):
        return _days360_serials(purchase, first_period, european=(basis == 4)), 360
    actual_days = first_period - purchase
    if basis == 1:
        purchase_year, _, _ = _excel_serial_ymd(purchase)
        first_year, _, _ = _excel_serial_ymd(first_period)
        # Serial 60 is the Excel 1900-system phantom February 29 in this profile.
        includes_february_29 = purchase <= 60 <= first_period or any(
            purchase <= _excel_ymd_to_serial(year, 2, 29) <= first_period
            for year in range(purchase_year, first_year + 1)
            if year != 1900 and _is_excel_leap_year(year)
        )
        return actual_days, 366 if includes_february_29 else 365
    return actual_days, 365


def _capped_positive_product(limit: float, factors: tuple[float, ...]) -> float:
    """Multiply positive factors without overflow, saturating the result at limit."""
    if limit <= 0.0 or any(factor == 0.0 for factor in factors):
        return 0.0
    if not math.isfinite(limit):
        return 0.0
    if any(math.isnan(factor) or factor < 0.0 for factor in factors):
        return 0.0
    if any(math.isinf(factor) for factor in factors):
        return limit

    product = 1.0
    for factor in factors:
        product *= factor
        if math.isinf(product):
            log_product = math.fsum(math.log(item) for item in factors)
            if log_product >= math.log(limit):
                return limit
            try:
                recovered = math.exp(log_product)
            except OverflowError:
                return limit
            return min(recovered, limit) if math.isfinite(recovered) else limit
        if product == 0.0:
            return 0.0
    return min(product, limit)


def _amor_depreciation_call(name: str, args: tuple[object, ...]) -> float | ErrorValue:
    """Evaluate AMORLINC or AMORDEGRC under the bounded day-count profile."""
    cost_value = _number(args[0])
    if isinstance(cost_value, ErrorValue):
        return cost_value
    purchase = _day_count_serial(args[1], error_code="#VALUE!")
    if isinstance(purchase, ErrorValue):
        return purchase
    first_period = _day_count_serial(args[2], error_code="#VALUE!")
    if isinstance(first_period, ErrorValue):
        return first_period
    salvage_value = _number(args[3])
    if isinstance(salvage_value, ErrorValue):
        return salvage_value
    period_value = _number(args[4])
    if isinstance(period_value, ErrorValue):
        return period_value
    rate_value = _number(args[5])
    if isinstance(rate_value, ErrorValue):
        return rate_value
    basis_value: object = 0 if len(args) < 7 or args[6] is _OPTIONAL_OMITTED else args[6]
    basis_number = _number(basis_value)
    if isinstance(basis_number, ErrorValue):
        return basis_number

    try:
        cost = float(cost_value)
        salvage = float(salvage_value)
        rate = float(rate_value)
    except (OverflowError, TypeError, ValueError):
        return ErrorValue("#NUM!", "depreciation argument is outside the supported numeric range")
    if not all(math.isfinite(value) for value in (cost, salvage, rate)):
        return ErrorValue("#NUM!", "depreciation arguments must be finite")
    if isinstance(period_value, float) and not math.isfinite(period_value):
        return ErrorValue("#NUM!", "period must be finite")
    if isinstance(basis_number, float) and not math.isfinite(basis_number):
        return ErrorValue("#NUM!", "basis must be finite")
    period = math.trunc(period_value)
    basis = math.trunc(basis_number)

    if cost <= 0.0 or not 0.0 <= salvage < cost or rate <= 0.0:
        return ErrorValue("#NUM!", "profile requires cost>0, 0<=salvage<cost, and rate>0")
    if purchase >= first_period:
        return ErrorValue("#NUM!", "purchase date must precede first_period")
    if not 0 <= period <= 100_000:
        return ErrorValue("#NUM!", "period must be from 0 through 100000")
    if basis not in (0, 1, 3, 4):
        return ErrorValue("#NUM!", "basis must be 0, 1, 3, or 4")

    try:
        raw_life = 1.0 / rate
    except (OverflowError, ZeroDivisionError):
        return ErrorValue("#NUM!", "depreciation life is outside the supported range")
    if not math.isfinite(raw_life) or raw_life > _MAX_EXACT_DEPRECIATION_PERIOD:
        return ErrorValue("#NUM!", "depreciation life exceeds the supported exact-period range")
    effective_life = math.ceil(raw_life)
    if name == "AMORDEGRC" and (
        raw_life <= 3.0 or 4.0 <= raw_life <= 5.0
    ):
        return ErrorValue("#NUM!", "raw life is outside the AMORDEGRC supported profile")

    if name == "AMORDEGRC":
        if 3 <= effective_life <= 4:
            coefficient = 1.5
        elif 5 <= effective_life <= 6:
            coefficient = 2.0
        elif effective_life > 6:
            coefficient = 2.5
        else:
            coefficient = 1.0
        effective_rate = rate * coefficient
        if not math.isfinite(effective_rate):
            return ErrorValue("#NUM!", "effective depreciation rate must be finite")
    else:
        effective_rate = rate

    if period > effective_life:
        return 0.0
    stub_days, days_in_year = _amor_stub_day_count(purchase, first_period, basis)
    stub_fraction = stub_days / days_in_year
    depreciable_basis = cost - salvage
    if not math.isfinite(stub_fraction):
        return ErrorValue("#NUM!", "stub fraction must be finite")
    prorated_stub_product = _capped_positive_product(
        depreciable_basis, (cost, effective_rate, stub_fraction)
    )

    if name == "AMORLINC":
        stub = prorated_stub_product
        if stub == 0.0:
            stub = _capped_positive_product(depreciable_basis, (cost, rate, 1.0))
        if period == 0:
            return stub
        first_period_basis = max(depreciable_basis - stub, 0.0)
        amount_per_period = _capped_positive_product(
            depreciable_basis, (cost, rate, 1.0)
        )
        prior_amount = _capped_positive_product(
            first_period_basis, (amount_per_period, float(period - 1), 1.0)
        )
        remaining = max(first_period_basis - prior_amount, 0.0)
        return min(amount_per_period, remaining)

    stub_product = prorated_stub_product
    if stub_product == 0.0:
        stub_product = _capped_positive_product(
            depreciable_basis, (cost, effective_rate, 1.0)
        )
    stub = (
        depreciable_basis
        if stub_product >= depreciable_basis
        else min(_round_half_away_from_zero(stub_product), depreciable_basis)
    )
    if period == 0:
        return stub
    remaining = cost - stub
    depreciation_rate = effective_rate
    # Reference-derived profile extends only calc_t; period eligibility uses base life above.
    terminal_life = effective_life + int(prorated_stub_product > 0.0)
    amount = 0.0
    for counted_period in range(2, period + 2):
        calc_t = terminal_life - counted_period
        if calc_t == 2:
            amount = remaining * 0.5
            depreciation_rate = 1.0
        else:
            amount = remaining * depreciation_rate
        if not math.isfinite(amount):
            return ErrorValue("#NUM!", "period depreciation must be finite")
        if remaining < salvage:
            amount = min(amount, max(remaining - salvage, 0.0))
        amount = _round_half_away_from_zero(amount)
        remaining -= amount
        if not math.isfinite(remaining):
            return ErrorValue("#NUM!", "remaining depreciation must be finite")
    return amount


def _tvm_call(name: str, args: tuple[object, ...]) -> object:
    """Evaluate scalar time-value-of-money functions under a shared rate profile."""
    values: list[float] = []
    for argument in args:
        number = _number(argument)
        if isinstance(number, ErrorValue):
            return number
        try:
            numeric = float(number)
        except (OverflowError, TypeError, ValueError):
            return ErrorValue("#NUM!", "financial argument is outside the supported numeric range")
        if not math.isfinite(numeric):
            return ErrorValue("#NUM!", "financial arguments must be finite")
        values.append(numeric)

    rate, nper, cash_input = values[:3]
    optional = values[3:] + [0.0] * (2 - len(values[3:]))
    endpoint_value, payment_type = optional
    if rate <= -1.0:
        return ErrorValue("#NUM!", "rate at or below -100% is outside this evaluator profile")
    if nper < 0.0:
        return ErrorValue("#NUM!", "negative payment periods are outside this evaluator profile")
    if payment_type not in (0.0, 1.0):
        return ErrorValue("#NUM!", "type must be 0 or 1 in this evaluator")

    if rate == 0.0:
        if name == "FV":
            result = -(endpoint_value + cash_input * nper)
        elif name == "PV":
            result = -(endpoint_value + cash_input * nper)
        else:
            if nper == 0.0:
                return ErrorValue("#DIV/0!", "PMT has zero payment periods")
            result = -(cash_input + endpoint_value) / nper
    else:
        exponent = nper * math.log1p(rate)
        if not math.isfinite(exponent):
            return ErrorValue("#NUM!", "rate and nper exceed the supported numeric range")
        payment_factor = 1.0 + rate * payment_type
        if not math.isfinite(payment_factor):
            return ErrorValue("#NUM!", "payment timing factor is outside the supported range")
        try:
            if name == "FV":
                growth = math.exp(exponent)
                annuity = math.expm1(exponent) / rate
                result = -(endpoint_value * growth + cash_input * payment_factor * annuity)
            else:
                discount = math.exp(-exponent)
                annuity = -math.expm1(-exponent) / rate * payment_factor
                if name == "PV":
                    result = -(endpoint_value * discount + cash_input * annuity)
                else:
                    if annuity == 0.0:
                        return ErrorValue("#DIV/0!", "PMT annuity factor is zero")
                    result = -(cash_input + endpoint_value * discount) / annuity
        except OverflowError:
            return ErrorValue("#NUM!", "financial result exceeds the supported numeric range")

    if not math.isfinite(result):
        return ErrorValue("#NUM!", "financial result must be finite")
    return result


def _payment_component_call(name: str, args: tuple[object, ...]) -> object:
    """Split a periodic payment into interest and principal under a scalar profile."""
    values: list[float] = []
    for argument in args:
        number = _number(argument)
        if isinstance(number, ErrorValue):
            return number
        try:
            numeric = float(number)
        except (OverflowError, TypeError, ValueError):
            return ErrorValue("#NUM!", "financial argument is outside the supported numeric range")
        if not math.isfinite(numeric):
            return ErrorValue("#NUM!", "financial arguments must be finite")
        values.append(numeric)

    rate, period, nper, present_value = values[:4]
    optional = values[4:] + [0.0] * (2 - len(values[4:]))
    future_value, payment_type = optional
    if rate <= -1.0:
        return ErrorValue("#NUM!", "rate at or below -100% is outside this evaluator profile")
    if payment_type not in (0.0, 1.0):
        return ErrorValue("#NUM!", "type must be 0 or 1 in this evaluator")
    if period < 1.0 or period > nper:
        return ErrorValue("#NUM!", "period must be within 1 through nper in this evaluator")

    payment_due = _tvm_call(
        "PMT", (rate, nper, present_value, future_value, payment_type)
    )
    if isinstance(payment_due, ErrorValue):
        return payment_due
    if rate == 0.0:
        return 0.0 if name == "IPMT" else payment_due

    payment_end = payment_due
    if payment_type == 1.0:
        payment_end = _tvm_call(
            "PMT", (rate, nper, present_value, future_value, 0.0)
        )
        if isinstance(payment_end, ErrorValue):
            return payment_end

    exponent = (period - 1.0) * math.log1p(rate)
    if not math.isfinite(exponent):
        return ErrorValue("#NUM!", "period growth exceeds the supported numeric range")
    try:
        previous_growth = math.exp(exponent)
        previous_growth_delta = math.expm1(exponent)
    except OverflowError:
        return ErrorValue("#NUM!", "period growth exceeds the supported numeric range")

    interest_end = -(
        present_value * rate * previous_growth
        + payment_end * previous_growth_delta
    )
    if payment_type == 1.0:
        interest = 0.0 if period == 1.0 else interest_end / (1.0 + rate)
    else:
        interest = interest_end
    result = interest if name == "IPMT" else payment_due - interest
    if not math.isfinite(result):
        return ErrorValue("#NUM!", "payment component must be finite")
    return result


def _cumulative_payment_call(name: str, args: tuple[object, ...]) -> object:
    """Evaluate cumulative loan components with a closed-form period sum.

    Fractional range endpoints select the integer payment periods they enclose;
    an end period beyond nper is rejected as an explicit evaluator profile.
    """
    values: list[float] = []
    for argument in args:
        number = _number(argument)
        if isinstance(number, ErrorValue):
            return number
        try:
            numeric = float(number)
        except (OverflowError, TypeError, ValueError):
            return ErrorValue("#NUM!", "financial argument is outside the supported numeric range")
        if not math.isfinite(numeric):
            return ErrorValue("#NUM!", "financial arguments must be finite")
        values.append(numeric)

    rate, nper, present_value, start_period, end_period, payment_type = values
    if rate <= 0.0 or nper <= 0.0 or present_value <= 0.0:
        return ErrorValue("#NUM!", "rate, nper, and pv must be positive")
    if start_period < 1.0 or end_period < 1.0 or start_period > end_period:
        return ErrorValue("#NUM!", "period range must be ordered and start at 1 or later")
    if payment_type not in (0.0, 1.0):
        return ErrorValue("#NUM!", "type must be 0 or 1")
    max_exact_period = float(2**53)
    if end_period > nper or end_period > max_exact_period:
        return ErrorValue("#NUM!", "end_period must not exceed nper or the exact period limit")

    first_period = math.ceil(start_period)
    last_period = math.floor(end_period)
    if first_period > last_period:
        return 0.0
    period_count = float(last_period - first_period + 1)

    log_growth = math.log1p(rate)
    horizon = nper * rate
    interest_first = max(first_period, 2) if payment_type == 1.0 else first_period
    if name != "CUMIPMT" or interest_first > last_period:
        cumulative_interest = 0.0
    else:
        interest_count = float(last_period - interest_first + 1)
        if horizon < 1e-12:
            # The exact closed form approaches a simple average balance as the
            # whole loan's rate tends to zero. This branch avoids subtracting
            # log terms whose true sum can be smaller than one ULP.
            midpoint_before_payment = (
                (interest_first - 1.0) + (interest_count - 1.0) / 2.0
            )
            remaining_fraction = (nper - midpoint_before_payment) / nper
            interest_factor = interest_count * remaining_fraction
            cumulative_interest = -present_value * rate * interest_factor
        else:
            first_exponent = (interest_first - 1.0 - nper) * log_growth
            range_log = interest_count * log_growth
            denominator_log = -math.expm1(-nper * log_growth)
            log_average = (
                first_exponent
                + _log_exprel(range_log)
                + _log_log1p_over_rate(rate)
            )
            # Each selected period contributes 1 - q**(period-1-nper).
            # Computing the complement through expm1 preserves small values.
            interest_weight = -interest_count * math.expm1(log_average)
            cumulative_interest = (
                -present_value * rate * interest_weight / denominator_log
            )
        if payment_type == 1.0:
            cumulative_interest /= 1.0 + rate
        if not math.isfinite(cumulative_interest):
            return ErrorValue("#NUM!", "cumulative interest must be finite")

    if name != "CUMPRINC":
        cumulative_principal = 0.0
    elif horizon < 1e-12:
        cumulative_principal = -present_value * (period_count / nper)
    else:
        principal_first = max(first_period, 2) if payment_type == 1.0 else first_period
        cumulative_principal = 0.0
        if principal_first <= last_period:
            principal_count = float(last_period - principal_first + 1)
            principal_range_log = principal_count * log_growth
            log_principal_magnitude = (
                math.log(present_value)
                + (last_period - nper) * log_growth
                + _log1mexp_negative(principal_range_log)
                - _log1mexp_negative(nper * log_growth)
            )
            try:
                cumulative_principal = -math.exp(log_principal_magnitude)
            except OverflowError:
                return ErrorValue("#NUM!", "cumulative principal exceeds the supported numeric range")
            if payment_type == 1.0:
                cumulative_principal /= 1.0 + rate
        if payment_type == 1.0 and first_period <= 1.0 <= last_period:
            payment_due = _tvm_call("PMT", (rate, nper, present_value, 0.0, 1.0))
            if isinstance(payment_due, ErrorValue):
                return payment_due
            cumulative_principal += float(payment_due)

    result = cumulative_interest if name == "CUMIPMT" else cumulative_principal
    if not math.isfinite(result):
        return ErrorValue("#NUM!", "cumulative payment component must be finite")
    return result


def _log_exprel(value: float) -> float:
    """Return log((exp(value)-1)/value) without losing small values."""
    if value < 1e-4:
        square = value * value
        return (
            value / 2.0
            + square / 24.0
            - square * square / 2880.0
            + square * square * square / 181440.0
        )
    if value > 50.0:
        return value + math.log1p(-math.exp(-value)) - math.log(value)
    return math.log(math.expm1(value) / value)


def _log_log1p_over_rate(rate: float) -> float:
    """Return log(log1p(rate)/rate), including its small-rate series."""
    if rate < 1e-4:
        square = rate * rate
        return (
            -rate / 2.0
            + 5.0 * square / 24.0
            - square * rate / 8.0
            + 251.0 * square * square / 2880.0
        )
    return math.log(math.log1p(rate) / rate)


def _log1mexp_negative(value: float) -> float:
    """Return log(1-exp(-value)) for a positive value without cancellation."""
    if value <= math.log(2.0):
        return math.log(-math.expm1(-value))
    return math.log1p(-math.exp(-value))


def evaluate_result(
    formula: str,
    cells: Mapping[str, Scalar] | None = None,
    sheet_name: str = "Sheet1",
) -> FormulaResult:
    """Evaluate a formula and preserve any rectangular result shape.

    ``cells`` maps A1 addresses on ``sheet_name`` to scalar values. Unknown
    cells behave as blank cells (zero in scalar arithmetic, blank in ranges).
    The result is a calculation value; worksheet spill placement is not modeled.
    """

    if not isinstance(formula, str):
        return ErrorValue("#VALUE!", "formula must be a string")
    if not isinstance(sheet_name, str) or not sheet_name:
        return ErrorValue("#VALUE!", "sheet_name must be a non-empty string")
    if cells is None:
        cells = {}
    if not isinstance(cells, Mapping):
        return ErrorValue("#VALUE!", "cells must be a mapping of A1 addresses to scalar values")
    normalized: dict[str, Scalar] = {}
    for address, value in cells.items():
        if not isinstance(address, str):
            return ErrorValue("#REF!", f"invalid cell address {address!r}")
        if "!" in address:
            sheet, cell = address.rsplit("!", 1)
            if not sheet or _CELL_RE.fullmatch(cell) is None or any(character in sheet for character in "[]"):
                return ErrorValue("#REF!", f"invalid qualified cell address {address!r}")
            key = f"{sheet.casefold()}!{cell.replace('$', '').upper()}"
        elif _CELL_RE.fullmatch(address) is not None:
            key = address.replace("$", "").upper()
        else:
            return ErrorValue("#REF!", f"invalid cell address {address!r}")
        if value is not None and not isinstance(value, (int, float, str, bool, ErrorValue)):
            return ErrorValue("#VALUE!", f"cell {address!r} is not a scalar value")
        normalized[key] = value
    source = formula.strip()
    if source.startswith("="):
        source = source[1:]
    if not source:
        return ErrorValue("#VALUE!", "formula is empty")
    try:
        budget = _WildcardBudget()
        value = _eval(_Parser(source).parse(), normalized, sheet_name, budget)
        if isinstance(value, ArrayValue):
            return _check_text_result(value)
        if isinstance(value, _Range):
            return _check_text_result(ArrayValue(
                tuple(
                    value.values[offset : offset + value.columns]
                    for offset in range(0, value.rows * value.columns, value.columns)
                )
            ))
        return _check_text_result(value)
    except _FormulaSyntaxError as error:
        return ErrorValue("#VALUE!", str(error))
    except RecursionError:
        return ErrorValue("#VALUE!", "formula exceeds the evaluator's safe nesting limit")
    except (ArithmeticError, ValueError, TypeError) as error:
        return ErrorValue("#VALUE!", str(error))


def evaluate(
    formula: str,
    cells: Mapping[str, Scalar] | None = None,
    sheet_name: str = "Sheet1",
) -> Scalar | ErrorValue:
    """Evaluate a scalar formula; use :func:`evaluate_result` for arrays."""
    result = evaluate_result(formula, cells, sheet_name)
    if isinstance(result, ArrayValue):
        return ErrorValue("#VALUE!", "formula result is an array; use evaluate_result to preserve shape")
    return result


__all__ = [
    "ArrayValue",
    "ErrorValue",
    "FormulaAnalysis",
    "FormulaReference",
    "FormulaResult",
    "Scalar",
    "analyze_formula",
    "evaluate",
    "evaluate_result",
]
