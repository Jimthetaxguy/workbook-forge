"""Canonical structured workbook model and versioned JSON interchange.

These types are the single source of truth for intake, calc-binding, export,
and agent-headless slices. ``schemas/workbook-model-v1.schema.json`` defines
the serialized contract; later stages import these types directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import re
from typing import Any, Literal, Self

DataType = Literal["blank", "number", "boolean", "text", "error", "unknown"]
SCHEMA_VERSION = 1
MODEL_VERSION = 1

_ADDRESS_RE = re.compile(r"[A-Z]{1,3}[1-9][0-9]*\Z")
_DEPENDENCY_RE = re.compile(r"[^!]+![A-Z]{1,3}[1-9][0-9]*\Z")
_DATA_TYPES = {"blank", "number", "boolean", "text", "error", "unknown"}


class ModelError(ValueError):
    """Invalid serialized workbook model or unsupported model content."""


class UnsupportedVersionError(ModelError):
    """The document requests a schema or model version this reader cannot load."""


def _expect_object(
    value: Any,
    where: str,
    required: set[str],
    optional: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModelError(f"{where} must be a JSON object")
    missing = required - value.keys()
    if missing:
        raise ModelError(f"{where} is missing required field(s): {', '.join(sorted(missing))}")
    extra = value.keys() - required - optional
    if extra:
        raise ModelError(f"{where} has unsupported field(s): {', '.join(sorted(extra))}")
    return value


def _expect_address(value: Any, where: str) -> str:
    if not isinstance(value, str) or _ADDRESS_RE.fullmatch(value) is None:
        raise ModelError(f"{where} must be a canonical A1 address")
    column_letters = re.match(r"[A-Z]+", value)
    assert column_letters is not None
    column = 0
    for letter in column_letters.group():
        column = column * 26 + ord(letter) - ord("A") + 1
    row_text = value[len(column_letters.group()):]
    if len(row_text) > 7:
        raise ModelError(f"{where} is outside the Excel worksheet grid")
    row = int(row_text)
    if column > 16_384 or row > 1_048_576:
        raise ModelError(f"{where} is outside the Excel worksheet grid")
    return value


def _validate_value(value: Any, where: str) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ModelError(f"{where} must contain a finite JSON number")
    if isinstance(value, dict):
        error_value = _expect_object(value, where, {"error"}, {"message"})
        if not isinstance(error_value["error"], str) or not error_value["error"]:
            raise ModelError(f"{where}.error must be a non-empty string")
        if "message" in error_value and not isinstance(error_value["message"], str):
            raise ModelError(f"{where}.message must be a string")
        return
    raise ModelError(f"{where} must be a JSON scalar or spreadsheet error object")


def _validate_document(document: Any) -> dict[str, Any]:
    root = _expect_object(
        document,
        "workbook",
        {"schema_version", "model_version", "metadata", "bindings", "sheets"},
        {"source_path"},
    )
    for name, supported in (("schema_version", SCHEMA_VERSION), ("model_version", MODEL_VERSION)):
        version = root[name]
        if type(version) is not int:
            raise ModelError(f"workbook.{name} must be an integer")
        if version != supported:
            raise UnsupportedVersionError(
                f"unsupported {name} {version}; this reader supports {supported}"
            )
    if "source_path" in root and not isinstance(root["source_path"], str):
        raise ModelError("workbook.source_path must be a string when present")
    if not isinstance(root["metadata"], dict):
        raise ModelError("workbook.metadata must be a JSON object")
    if not isinstance(root["sheets"], list):
        raise ModelError("workbook.sheets must be an array")
    if not isinstance(root["bindings"], list):
        raise ModelError("workbook.bindings must be an array")

    for sheet_index, raw_sheet in enumerate(root["sheets"]):
        where = f"workbook.sheets[{sheet_index}]"
        sheet = _expect_object(raw_sheet, where, {"name", "cells"}, {"dimensions"})
        if not isinstance(sheet["name"], str) or not sheet["name"]:
            raise ModelError(f"{where}.name must be a non-empty string")
        if "dimensions" in sheet:
            dimensions = sheet["dimensions"]
            if not isinstance(dimensions, list) or len(dimensions) != 4:
                raise ModelError(
                    f"{where}.dimensions must be [min_row, max_row, min_column, max_column]"
                )
            limits = (1_048_576, 1_048_576, 16_384, 16_384)
            for index, (coordinate, limit) in enumerate(zip(dimensions, limits, strict=True)):
                if type(coordinate) is not int or not 1 <= coordinate <= limit:
                    raise ModelError(
                        f"{where}.dimensions[{index}] is outside the Excel worksheet grid"
                    )
            if dimensions[0] > dimensions[1] or dimensions[2] > dimensions[3]:
                raise ModelError(f"{where}.dimensions minimum must not exceed maximum")
        if not isinstance(sheet["cells"], dict):
            raise ModelError(f"{where}.cells must be an object keyed by A1 address")

        for key, raw_cell in sheet["cells"].items():
            cell_where = f"{where}.cells[{key!r}]"
            _expect_address(key, f"{cell_where} key")
            cell = _expect_object(
                raw_cell,
                cell_where,
                {"address", "value", "data_type"},
                {"number_format", "formula"},
            )
            address = _expect_address(cell["address"], f"{cell_where}.address")
            if key != address:
                raise ModelError(f"{cell_where}.address must match its cell-map key")
            _validate_value(cell["value"], f"{cell_where}.value")
            if not isinstance(cell["data_type"], str) or cell["data_type"] not in _DATA_TYPES:
                raise ModelError(f"{cell_where}.data_type is not a supported data type")
            if "number_format" in cell and not isinstance(cell["number_format"], str):
                raise ModelError(f"{cell_where}.number_format must be a string when present")
            if "formula" in cell:
                formula_where = f"{cell_where}.formula"
                formula = _expect_object(
                    cell["formula"],
                    formula_where,
                    {"expression", "dependencies"},
                    {"cached_value"},
                )
                if cell["value"] is not None:
                    raise ModelError(f"{cell_where}.value must be null when formula is present")
                if (
                    not isinstance(formula["expression"], str)
                    or not formula["expression"].startswith("=")
                ):
                    raise ModelError(
                        f"{formula_where}.expression must retain its leading equals sign"
                    )
                dependencies = formula["dependencies"]
                if not isinstance(dependencies, list):
                    raise ModelError(f"{formula_where}.dependencies must be an array")
                if any(not isinstance(dependency, str) for dependency in dependencies):
                    raise ModelError(f"{formula_where}.dependencies entries must be strings")
                if len(set(dependencies)) != len(dependencies):
                    raise ModelError(f"{formula_where}.dependencies must not contain duplicates")
                for dependency in dependencies:
                    if _DEPENDENCY_RE.fullmatch(dependency) is None:
                        raise ModelError(
                            f"{formula_where}.dependencies must use Sheet!A1 addresses"
                        )
                if "cached_value" in formula:
                    if formula["cached_value"] is None:
                        raise ModelError(
                            f"{formula_where}.cached_value must be omitted when blank"
                        )
                    _validate_value(formula["cached_value"], f"{formula_where}.cached_value")

    sheet_names = [sheet["name"] for sheet in root["sheets"]]
    if len(set(sheet_names)) != len(sheet_names):
        raise ModelError("worksheet names must be unique")
    sheet_name_set = set(sheet_names)
    seen_bindings: set[tuple[str, str]] = set()
    for binding_index, raw_binding in enumerate(root["bindings"]):
        where = f"workbook.bindings[{binding_index}]"
        binding = _expect_object(
            raw_binding,
            where,
            {
                "direction",
                "name",
                "sheet",
                "address",
                "value_type",
                "required",
                "constraints",
            },
        )
        if (
            not isinstance(binding["direction"], str)
            or binding["direction"] not in {"input", "output"}
        ):
            raise ModelError(f"{where}.direction must be 'input' or 'output'")
        for field_name in ("name", "sheet"):
            if not isinstance(binding[field_name], str) or not binding[field_name]:
                raise ModelError(f"{where}.{field_name} must be a non-empty string")
        if binding["sheet"] not in sheet_name_set:
            raise ModelError(f"{where}.sheet must name a sheet in this workbook")
        _expect_address(binding["address"], f"{where}.address")
        if (
            not isinstance(binding["value_type"], str)
            or binding["value_type"] not in _DATA_TYPES
        ):
            raise ModelError(f"{where}.value_type is not a supported data type")
        if not isinstance(binding["required"], bool):
            raise ModelError(f"{where}.required must be a boolean")
        identity = (binding["direction"], binding["name"])
        if identity in seen_bindings:
            raise ModelError(f"duplicate {identity[0]} binding name: {identity[1]}")
        seen_bindings.add(identity)
        constraints = _expect_object(
            binding["constraints"],
            f"{where}.constraints",
            set(),
            {"min", "max", "choices"},
        )
        for bound in ("min", "max"):
            if bound in constraints:
                number = constraints[bound]
                if isinstance(number, bool) or not isinstance(number, (int, float)):
                    raise ModelError(f"{where}.constraints.{bound} must be a number")
                if isinstance(number, float) and not math.isfinite(number):
                    raise ModelError(f"{where}.constraints.{bound} must be finite")
        if (
            "min" in constraints
            and "max" in constraints
            and constraints["min"] > constraints["max"]
        ):
            raise ModelError(f"{where}.constraints.min must not exceed max")
        if "choices" in constraints:
            if not isinstance(constraints["choices"], list):
                raise ModelError(f"{where}.constraints.choices must be an array")
            for choice in constraints["choices"]:
                _validate_value(choice, f"{where}.constraints.choices[]")

    # Metadata is deliberately extensible, but still has to be JSON data.
    try:
        json.dumps(root["metadata"], allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ModelError(f"workbook.metadata contains a non-JSON value: {error}") from error
    return root


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ModelError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ModelError(f"non-standard JSON numeric constant: {value}")


@dataclass(frozen=True, slots=True)
class Formula:
    """Formula source and dependencies plus an optional imported cache observation."""

    expression: str
    dependencies: tuple[str, ...] = ()
    cached_value: Any | None = None


@dataclass(frozen=True, slots=True)
class Cell:
    """One cell with an authored literal value or a formula, never both."""

    address: str
    value: Any = None
    formula: Formula | None = None
    data_type: DataType = "unknown"
    number_format: str | None = None


@dataclass(frozen=True, slots=True)
class Sheet:
    """One worksheet: name, cells keyed by A1 address, and used dimensions."""

    name: str
    cells: dict[str, Cell] = field(default_factory=dict)
    dimensions: tuple[int, int, int, int] | None = None  # min_row, max_row, min_col, max_col


@dataclass(frozen=True, slots=True)
class BindingConstraints:
    """Declared numeric bounds or allowed values for a named workbook binding."""

    minimum: int | float | None = None
    maximum: int | float | None = None
    choices: tuple[Any, ...] | None = None


@dataclass(frozen=True, slots=True)
class Binding:
    """A named input or output attached to one explicit worksheet cell."""

    direction: Literal["input", "output"]
    name: str
    sheet: str
    address: str
    value_type: DataType
    required: bool
    constraints: BindingConstraints = field(default_factory=BindingConstraints)


@dataclass(frozen=True, slots=True)
class Workbook:
    """A versioned workbook model that can be hydrated from canonical JSON bytes."""

    sheets: tuple[Sheet, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    source_path: str | None = None
    schema_version: int = SCHEMA_VERSION
    model_version: int = MODEL_VERSION
    bindings: tuple[Binding, ...] = ()

    def sheet(self, name: str) -> Sheet | None:
        for item in self.sheets:
            if item.name == name:
                return item
        return None

    def to_summary_dict(self) -> dict[str, Any]:
        """JSON-friendly intake summary: versions, counts, and graph size."""
        dep_edges: set[tuple[str, str]] = set()
        formula_count = 0
        cell_count = 0
        sheet_summaries = []
        for sheet in self.sheets:
            formulas = 0
            for cell in sheet.cells.values():
                cell_count += 1
                if cell.formula is not None:
                    formulas += 1
                    formula_count += 1
                    src = f"{sheet.name}!{cell.address}"
                    for dep in cell.formula.dependencies:
                        dep_edges.add((src, dep))
            sheet_summaries.append(
                {
                    "name": sheet.name,
                    "cell_count": len(sheet.cells),
                    "formula_count": formulas,
                    "dimensions": list(sheet.dimensions) if sheet.dimensions else None,
                }
            )
        return {
            "schema_version": self.schema_version,
            "model_version": self.model_version,
            "source_path": self.source_path,
            "sheet_count": len(self.sheets),
            "cell_count": cell_count,
            "formula_count": formula_count,
            "binding_count": len(self.bindings),
            "dependency_graph_size": len(dep_edges),
            "sheets": sheet_summaries,
            "metadata": self.metadata,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical v1 document with absent optional fields omitted."""
        sheets: list[dict[str, Any]] = []
        for sheet in self.sheets:
            cells: dict[str, Any] = {}
            for key, cell in sheet.cells.items():
                entry: dict[str, Any] = {
                    "address": cell.address,
                    "value": cell.value,
                    "data_type": cell.data_type,
                }
                if cell.number_format is not None:
                    entry["number_format"] = cell.number_format
                if cell.formula is not None:
                    entry["formula"] = {
                        "expression": cell.formula.expression,
                        "dependencies": list(cell.formula.dependencies),
                    }
                    if cell.formula.cached_value is not None:
                        entry["formula"]["cached_value"] = cell.formula.cached_value
                cells[key] = entry
            sheet_entry: dict[str, Any] = {"name": sheet.name, "cells": cells}
            if sheet.dimensions is not None:
                sheet_entry["dimensions"] = list(sheet.dimensions)
            sheets.append(sheet_entry)

        document: dict[str, Any] = {
            "schema_version": self.schema_version,
            "model_version": self.model_version,
            "metadata": self.metadata,
            "bindings": [
                {
                    "direction": binding.direction,
                    "name": binding.name,
                    "sheet": binding.sheet,
                    "address": binding.address,
                    "value_type": binding.value_type,
                    "required": binding.required,
                    "constraints": {
                        **(
                            {"min": binding.constraints.minimum}
                            if binding.constraints.minimum is not None
                            else {}
                        ),
                        **(
                            {"max": binding.constraints.maximum}
                            if binding.constraints.maximum is not None
                            else {}
                        ),
                        **(
                            {"choices": list(binding.constraints.choices)}
                            if binding.constraints.choices is not None
                            else {}
                        ),
                    },
                }
                for binding in self.bindings
            ],
            "sheets": sheets,
        }
        if self.source_path is not None:
            document["source_path"] = self.source_path
        return document

    def to_bytes(self) -> bytes:
        """Serialize a validated model deterministically as compact UTF-8 JSON."""
        document = _validate_document(self.to_dict())
        try:
            return json.dumps(
                document,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ModelError(f"workbook cannot be serialized as canonical JSON: {error}") from error

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview) -> Self:
        """Hydrate this native model from canonical JSON bytes; reject unknown versions."""
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("workbook model input must be bytes")
        try:
            document = json.loads(
                bytes(data).decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_json_constant,
            )
        except ModelError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ModelError(f"invalid workbook model JSON: {error}") from error

        root = _validate_document(document)
        sheets: list[Sheet] = []
        for raw_sheet in root["sheets"]:
            cells: dict[str, Cell] = {}
            for address, raw_cell in raw_sheet["cells"].items():
                raw_formula = raw_cell.get("formula")
                formula = None
                if raw_formula is not None:
                    formula = Formula(
                        expression=raw_formula["expression"],
                        dependencies=tuple(raw_formula["dependencies"]),
                        cached_value=raw_formula.get("cached_value"),
                    )
                cells[address] = Cell(
                    address=raw_cell["address"],
                    value=raw_cell["value"],
                    formula=formula,
                    data_type=raw_cell["data_type"],  # type: ignore[arg-type]
                    number_format=raw_cell.get("number_format"),
                )
            dimensions = raw_sheet.get("dimensions")
            sheets.append(
                Sheet(
                    name=raw_sheet["name"],
                    cells=cells,
                    dimensions=tuple(dimensions) if dimensions is not None else None,
                )
            )
        return cls(
            sheets=tuple(sheets),
            metadata=root["metadata"],
            source_path=root.get("source_path"),
            schema_version=root["schema_version"],
            model_version=root["model_version"],
            bindings=tuple(
                Binding(
                    direction=raw_binding["direction"],
                    name=raw_binding["name"],
                    sheet=raw_binding["sheet"],
                    address=raw_binding["address"],
                    value_type=raw_binding["value_type"],
                    required=raw_binding["required"],
                    constraints=BindingConstraints(
                        minimum=raw_binding["constraints"].get("min"),
                        maximum=raw_binding["constraints"].get("max"),
                        choices=(
                            tuple(raw_binding["constraints"]["choices"])
                            if "choices" in raw_binding["constraints"]
                            else None
                        ),
                    ),
                )
                for raw_binding in root["bindings"]
            ),
        )
