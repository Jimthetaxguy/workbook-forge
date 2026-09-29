"""Calculation sessions that consume the canonical native Workbook model."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import math
from threading import RLock
from collections.abc import Mapping
from typing import Any

from . import ArrayValue, ErrorValue, analyze_formula, evaluate_result
from . import _column_name, _coordinate
from .catalog import function_status
from .model import Cell, Formula, Workbook

MAX_DEPENDENCIES = 100_000


class CalculationError(ValueError):
    """A refused canonical-model calculation or input update."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    message: str
    sheet: str | None = None
    address: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.sheet is not None:
            result["sheet"] = self.sheet
        if self.address is not None:
            result["address"] = self.address
        return result


@dataclass(frozen=True, slots=True)
class CalculationReport:
    """Fresh formula results, kept separate from imported formula caches."""

    backend: str
    schema_version: int
    model_version: int
    revision: int
    outputs: Mapping[str, Any]
    values: Mapping[str, Any]
    diagnostics: tuple[Diagnostic, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "schema_version": self.schema_version,
            "model_version": self.model_version,
            "revision": self.revision,
            "outputs": {name: _json_value(value) for name, value in sorted(self.outputs.items())},
            "values": {key: _json_value(value) for key, value in sorted(self.values.items())},
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


def _json_value(value: Any) -> Any:
    if isinstance(value, ErrorValue):
        return {"error": value.code}
    if isinstance(value, ArrayValue):
        return {"rows": [[_json_value(item) for item in row] for row in value.rows]}
    if isinstance(value, dict) and isinstance(value.get("error"), str):
        return {"error": value["error"]}
    return value


def _engine_value(value: Any) -> Any:
    if isinstance(value, dict) and isinstance(value.get("error"), str):
        return ErrorValue(value["error"], str(value.get("message", "")))
    return value


def _type_matches(value_type: str, value: Any) -> bool:
    if value_type == "number":
        if type(value) not in (int, float):
            return False
        try:
            return math.isfinite(value)
        except OverflowError:
            return False
    if value_type == "boolean":
        return type(value) is bool
    if value_type == "text":
        return isinstance(value, str)
    if value_type == "blank":
        return value is None
    if value_type == "error":
        return isinstance(value, dict) and isinstance(value.get("error"), str)
    if value_type == "unknown":
        return value is None or type(value) in (int, float, bool, str) or (
            isinstance(value, dict) and isinstance(value.get("error"), str)
        )
    return False


def _same_choice(value: Any, choice: Any) -> bool:
    return type(value) is type(choice) and value == choice


def _cell_key(sheet: str, address: str) -> str:
    return f"{sheet}!{address}"


def _reference_cells(formula: Formula, current_sheet: str, workbook: Workbook) -> tuple[str, ...]:
    try:
        analysis = analyze_formula(formula.expression)
    except (TypeError, ValueError, RecursionError) as error:
        raise CalculationError("parse_error", str(error)) from error

    for function in analysis.functions:
        try:
            status = function_status(function, "python")
        except KeyError as error:
            raise CalculationError("unsupported_formula", f"unsupported function {function}") from error
        if status not in {"evaluated", "conformance-tested"}:
            raise CalculationError("unsupported_formula", f"function {function} is not evaluated")

    sheet_names = {sheet.name.casefold(): sheet.name for sheet in workbook.sheets}
    dependencies: set[str] = set()
    for reference in analysis.references:
        referenced_sheet = reference.sheet or current_sheet
        canonical_sheet = sheet_names.get(referenced_sheet.casefold())
        if canonical_sheet is None:
            raise CalculationError("invalid_reference", f"unknown worksheet {referenced_sheet}")
        start_row, start_column = _coordinate(reference.start)
        end_row, end_column = _coordinate(reference.end or reference.start)
        first_row, last_row = sorted((start_row, end_row))
        first_column, last_column = sorted((start_column, end_column))
        cell_count = (last_row - first_row + 1) * (last_column - first_column + 1)
        if cell_count > MAX_DEPENDENCIES - len(dependencies):
            raise CalculationError("resource_limit", "formula dependency expansion exceeds 100000 cells")
        for row in range(first_row, last_row + 1):
            for column in range(first_column, last_column + 1):
                dependencies.add(_cell_key(canonical_sheet, f"{_column_name(column)}{row}"))

    declared = set(formula.dependencies)
    if declared != dependencies:
        raise CalculationError(
            "invalid_dependencies",
            "declared formula dependencies do not match references in the formula",
        )
    return tuple(sorted(dependencies))


class _Calculator:
    def __init__(self, workbook: Workbook):
        self.workbook = workbook
        self.sheets = {sheet.name.casefold(): sheet for sheet in workbook.sheets}
        self.states: dict[str, str] = {}
        self.values: dict[str, Any] = {}
        self.diagnostics: dict[str, Diagnostic] = {}

    def _resolve(self, sheet: str, address: str) -> tuple[str, Cell | None]:
        actual_sheet = self.sheets.get(sheet.casefold())
        if actual_sheet is None:
            raise CalculationError("invalid_binding", f"unknown worksheet {sheet}")
        return actual_sheet.name, actual_sheet.cells.get(address)

    def _fail_cell(self, key: str, code: str, message: str) -> None:
        sheet, address = key.rsplit("!", 1)
        self.diagnostics.setdefault(key, Diagnostic(code, message, sheet, address))
        self.states[key] = "failed"

    def evaluate_cell(self, key: str) -> Any | None:
        state = self.states.get(key)
        if state == "done":
            return self.values[key]
        if state == "visiting":
            self._fail_cell(key, "dependency_cycle", "formula dependency cycle")
            return None
        if state == "failed":
            return None

        sheet_name, address = key.rsplit("!", 1)
        sheet, cell = self._resolve(sheet_name, address)
        if cell is None:
            self.values[key] = None
            self.states[key] = "done"
            return None
        formula = cell.formula
        if formula is None:
            self.values[key] = _engine_value(cell.value)
            self.states[key] = "done"
            return self.values[key]

        self.states[key] = "visiting"
        try:
            dependencies = _reference_cells(formula, sheet, self.workbook)
            for dependency in dependencies:
                if self.evaluate_cell(dependency) is None and self.states.get(dependency) == "failed":
                    self._fail_cell(key, "unsupported_dependency", f"dependency {dependency} could not be calculated")
                    return None
            environment: dict[str, Any] = {}
            for dependency in dependencies:
                value = self.values.get(dependency)
                if isinstance(value, ArrayValue):
                    self._fail_cell(key, "unsupported_array_reference", f"dependency {dependency} is an array")
                    return None
                environment[dependency] = value
            result = evaluate_result(formula.expression, environment, sheet)
        except CalculationError as error:
            self._fail_cell(key, error.code, error.message)
            return None

        if isinstance(result, ErrorValue) and not result.code.startswith("#"):
            self._fail_cell(key, "unsupported_formula", result.message or result.code)
            return None
        if result is None:
            result = 0
        self.values[key] = result
        self.states[key] = "done"
        return result

    def calculate(self, revision: int) -> CalculationReport:
        outputs = [binding for binding in self.workbook.bindings if binding.direction == "output"]
        for binding in sorted(outputs, key=lambda item: item.name):
            try:
                sheet, cell = self._resolve(binding.sheet, binding.address)
            except CalculationError as error:
                self.diagnostics[f"output:{binding.name}"] = Diagnostic(error.code, error.message)
                continue
            if cell is None:
                self.diagnostics[f"output:{binding.name}"] = Diagnostic(
                    "invalid_binding", f"output {binding.name} points to an empty cell", sheet, binding.address
                )
                continue
            self.evaluate_cell(_cell_key(sheet, binding.address))

        outputs_by_name: dict[str, Any] = {}
        for binding in outputs:
            sheet, cell = self._resolve(binding.sheet, binding.address)
            key = _cell_key(sheet, binding.address)
            if cell is not None and self.states.get(key) == "done":
                outputs_by_name[binding.name] = self.values[key]

        ordered_diagnostics = tuple(
            self.diagnostics[key]
            for key in sorted(self.diagnostics)
        )
        return CalculationReport(
            backend="python",
            schema_version=self.workbook.schema_version,
            model_version=self.workbook.model_version,
            revision=revision,
            outputs=outputs_by_name,
            values=self.values.copy(),
            diagnostics=ordered_diagnostics,
        )


class CanonicalCalculationSession:
    """Calculate directly from a hydrated ``workbook_forge.model.Workbook``."""

    def __init__(self, workbook: Workbook):
        if not isinstance(workbook, Workbook):
            raise TypeError("CanonicalCalculationSession requires model.Workbook")
        workbook.to_bytes()  # Validate direct-constructed instances at the boundary.
        self._workbook = deepcopy(workbook)
        self._revision = 0
        self._lock = RLock()
        self._validate_bindings()
        self.set_inputs({})

    @property
    def workbook(self) -> Workbook:
        with self._lock:
            return deepcopy(self._workbook)

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def _validate_bindings(self) -> None:
        seen_input_cells: set[str] = set()
        sheets = {sheet.name.casefold(): sheet for sheet in self._workbook.sheets}
        for binding in self._workbook.bindings:
            sheet = sheets.get(binding.sheet.casefold())
            if sheet is None:
                raise CalculationError("invalid_binding", f"unknown worksheet {binding.sheet}")
            cell = sheet.cells.get(binding.address)
            if binding.direction == "input":
                key = _cell_key(sheet.name, binding.address)
                if key in seen_input_cells:
                    raise CalculationError("invalid_input", "multiple inputs bind the same cell")
                seen_input_cells.add(key)
                if cell is None or cell.formula is not None:
                    raise CalculationError("invalid_input", "input must bind an existing literal cell")
            elif cell is None:
                raise CalculationError("invalid_binding", "output must bind an existing cell")

    def set_inputs(
        self,
        values: Mapping[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> int:
        if not isinstance(values, Mapping):
            raise CalculationError("invalid_input", "input overrides must be a mapping")
        if any(not isinstance(name, str) for name in values):
            raise CalculationError("invalid_input", "input names must be strings")
        with self._lock:
            if expected_revision is not None and (
                type(expected_revision) is not int or expected_revision != self._revision
            ):
                raise CalculationError("revision_conflict", "model revision conflict")
            input_bindings = {
                binding.name: binding
                for binding in self._workbook.bindings
                if binding.direction == "input"
            }
            unknown = sorted(set(values) - set(input_bindings))
            if unknown:
                raise CalculationError("unknown_input", f"unknown input {unknown[0]}")

            updates: dict[str, Any] = {}
            for name, binding in input_bindings.items():
                sheet = next(item for item in self._workbook.sheets if item.name.casefold() == binding.sheet.casefold())
                cell = sheet.cells[binding.address]
                value = values[name] if name in values else cell.value
                if value is None and not binding.required:
                    updates[name] = value
                    continue
                if not _type_matches(binding.value_type, value):
                    raise CalculationError("invalid_input", f"{name}: input expects {binding.value_type}")
                constraints = binding.constraints
                if type(value) in (int, float) and (
                    constraints.minimum is not None and value < constraints.minimum
                    or constraints.maximum is not None and value > constraints.maximum
                ):
                    raise CalculationError("invalid_input", f"{name}: input is outside its numeric bounds")
                if constraints.choices is not None and not any(
                    _same_choice(value, choice) for choice in constraints.choices
                ):
                    raise CalculationError("invalid_input", f"{name}: input is not an allowed choice")
                updates[name] = value

            if not values:
                return self._revision
            candidate_sheets = list(self._workbook.sheets)
            for name, value in updates.items():
                if name not in values:
                    continue
                binding = input_bindings[name]
                for index, sheet in enumerate(candidate_sheets):
                    if sheet.name.casefold() == binding.sheet.casefold():
                        candidate_cells = dict(sheet.cells)
                        candidate_cells[binding.address] = replace(
                            candidate_cells[binding.address],
                            value=value,
                            data_type=binding.value_type,
                        )
                        candidate_sheets[index] = replace(sheet, cells=candidate_cells)
                        break
            self._workbook = replace(self._workbook, sheets=tuple(candidate_sheets))
            self._revision += 1
            return self._revision

    def calculate(self) -> CalculationReport:
        with self._lock:
            workbook = deepcopy(self._workbook)
            revision = self._revision
        return _Calculator(workbook).calculate(revision)
