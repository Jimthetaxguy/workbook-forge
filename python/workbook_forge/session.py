"""The calculation session over the owner model (spec 09 step 1).

The contract is in ``docs/specs/proposed/05-canonical-calculation-session.md``.
A session owns one workbook document and a revision counter that starts at 0;
the revision is session state and never a field of the document. Inputs are
changed by name against an expected revision, and every calculation returns a
``calculation-report.v2`` (``schemas/calculation-report.v2.schema.json``).

Errors are raised as :class:`workbook_forge.python_engine.ToolkitError` with a
typed ``code`` on both backends.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import math
import re
from typing import Any, TypeVar

from .convert import Conversion, ConversionError, to_toolkit
from .model import ModelError, content_fingerprint, hydrate, workbook_defined_names
from .python_engine import MAX_WORKERS, ToolkitError, _resolve
from .toolkit import _encode, _engine
from .volatile import CalcContext

_UINT64_MAX = (1 << 64) - 1
_PREFIX = re.compile(r"([a-z][a-z_]*): (.*)\Z", re.DOTALL)
# The codes either engine raises. The native bridge reports a failure as
# ValueError("code: message"); only a prefix listed here is read as a code, so
# an unrelated message that happens to contain a colon is left alone.
_ENGINE_CODES = frozenset({
    "dependency_cycle", "invalid_cell", "invalid_edit", "invalid_expression",
    "invalid_input", "invalid_json", "invalid_layout", "invalid_model",
    "invalid_output", "invalid_reference", "invalid_request", "invalid_sheet",
    "invalid_style", "invalid_value", "invalid_workers", "name_cycle",
    "parse_error", "resource_limit", "revision_conflict", "schema_version",
    "unknown_input", "unsupported_array_reference", "unsupported_cell",
    "unsupported_dependency", "unsupported_edit", "unsupported_formula",
    "worker_failure",
})

_T = TypeVar("_T")


def _coded(error: BaseException) -> ToolkitError | None:
    """The typed error an engine failure stands for, or None."""
    if isinstance(error, ToolkitError):
        return error
    match = _PREFIX.match(str(error))
    if match is not None and match[1] in _ENGINE_CODES:
        return ToolkitError(match[1], match[2])
    return None


def _engine_call(call: Callable[..., _T], *args: Any, uncoded: str | None = None, **kwargs: Any) -> _T:
    """Run one engine-session call; a coded native failure becomes a ToolkitError.

    ``uncoded`` is the code given to a failure that carries none (the native
    reader's own message for a document it cannot parse). Without it such a
    failure is re-raised as it came.
    """
    try:
        return call(*args, **kwargs)
    except ToolkitError:
        raise
    except (ValueError, TypeError, OverflowError) as error:
        coded = _coded(error)
        if coded is None:
            if uncoded is None:
                raise
            coded = ToolkitError(uncoded, str(error))
        raise coded from error


def _inputs_call(call: Callable[..., _T], *args: Any) -> _T:
    """A named-input write: an unknown name is ``invalid_input`` in this contract.

    The values were checked by :func:`_values_json` before either engine saw
    them, so ``uncoded`` is only a backstop for a payload the native decoder
    still refuses.
    """
    try:
        return _engine_call(call, *args, uncoded="invalid_input")
    except ToolkitError as error:
        if error.code == "unknown_input":
            raise ToolkitError("invalid_input", error.message) from error
        raise


def _shape(root: dict[str, Any]) -> str:
    """Which v1 shape a parsed document is. The only shape sniff in the package.

    The two readers are disjoint on ``model_version``: the canonical reader
    requires it and the toolkit reader refuses it.
    """
    return "canonical" if "model_version" in root else "toolkit"


def _expected(expected_revision: Any) -> int:
    if type(expected_revision) is not int or not 0 <= expected_revision <= _UINT64_MAX:
        raise ToolkitError("invalid_request", "expected_revision must be an unsigned 64-bit integer")
    return expected_revision


def _is_cell_value(value: Any) -> bool:
    """Whether ``value`` is a cell value: blank, boolean, finite number, text
    or an ``{error, message?}`` object."""
    if value is None or isinstance(value, (bool, str)):
        return True
    if isinstance(value, (int, float)):
        try:
            return math.isfinite(float(value))
        except OverflowError:
            return False
    if isinstance(value, dict):
        return (
            "error" in value
            and set(value) <= {"error", "message"}
            and isinstance(value["error"], str)
            and isinstance(value.get("message"), (str, type(None)))
        )
    return False


def _values_json(values: Mapping[str, Any]) -> str:
    """The named-input batch as JSON, checked before either engine sees it.

    Both backends therefore refuse the same malformed batch with the same code,
    whatever the revision: ``invalid_request`` when ``values`` is not a map
    from input names (text) to values, ``invalid_input`` when a value is not a
    cell value.
    """
    if not isinstance(values, Mapping) or any(not isinstance(name, str) for name in values):
        raise ToolkitError("invalid_request", "values must map input names (text) to values")
    for name, value in values.items():
        if not _is_cell_value(value):
            raise ToolkitError("invalid_input", f"{name}: value must be a finite number, text, boolean, blank or error object")
    try:
        text = _encode(dict(values))
        text.encode("utf-8")
    except (ValueError, TypeError) as error:
        # A lone surrogate in a name or in text cannot be written as UTF-8.
        raise ToolkitError("invalid_input", f"values must be valid Unicode JSON data: {error}") from error
    return text


def _worker_count(workers: Any) -> int:
    """The engines' own rule for ``workers``, applied before either is called."""
    if type(workers) is not int or workers < 0:
        raise ToolkitError("invalid_workers", "workers must be a nonnegative integer")
    return min(workers, MAX_WORKERS)


def _strict_number(parse: Callable[[str], Any]) -> Callable[[str], Any]:
    def parsed(text: str) -> Any:
        value = parse(text)
        try:
            finite = math.isfinite(float(value))
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError(f"number out of range: {text[:32]}")
        return value
    return parsed


def _refuse_constant(name: str) -> Any:
    raise ValueError(f"nonfinite JSON constant {name}")


def _parse_root(data: bytes) -> Any:
    """The document's JSON root, read strictly, or ToolkitError ``invalid_model``.

    Strict means what the Rust reader also takes as JSON: UTF-8 with no byte
    order mark, no ``NaN`` or ``Infinity`` constant, no number outside the
    binary64 range. ``open`` in both languages refuses anything else, and a
    root that is not an object, with the same code.
    """
    try:
        root = json.loads(
            data.decode("utf-8"),
            parse_constant=_refuse_constant,
            parse_float=_strict_number(float),
            parse_int=_strict_number(int),
        )
    except (ValueError, RecursionError) as error:
        raise ToolkitError("invalid_model", f"document is not JSON: {error}") from error
    if not isinstance(root, dict):
        raise ToolkitError("invalid_model", "expected an object")
    return root


class CalculationSession:
    """One workbook document, its revision counter and its calculation passes.

    Built from the owner model directly, or with :meth:`open` from either v1
    document shape. A session always starts at revision 0.
    """

    def __init__(self, model: Mapping[str, Any], *, backend: str = "python", names_json: str | None = None):
        if not isinstance(model, Mapping):
            raise ToolkitError("invalid_model", "expected an object")
        try:
            text = _encode(model)
        except (ValueError, TypeError) as error:
            raise ToolkitError("invalid_model", f"model must be finite JSON data: {error}") from error
        self._backend = backend
        self._session = self._engine_session(backend, text, names_json)
        self._conversion: Conversion | None = None
        self._model_version: int | None = None
        self._source_fingerprint: str | None = None

    @staticmethod
    def _engine_session(backend: str, text: str, names_json: str | None):
        # The explicit seed wins over a legacy ``revision`` key in the text, so
        # this layer starts at 0 whatever the document carried.
        return _engine_call(_engine(backend).Session, text, names_json, uncoded="invalid_model", revision=0)

    @classmethod
    def open(
        cls,
        document: str | bytes,
        *,
        input_kinds: Mapping[str, str] | None = None,
        names_json: str | None = None,
        backend: str = "python",
    ) -> CalculationSession:
        """Open a v1 document of either shape.

        A canonical document (its root has ``model_version``) is hydrated and
        converted with :func:`workbook_forge.convert.to_toolkit`; ``conversion``
        then lists what the conversion dropped and added. A toolkit document is
        read as it is, and ``conversion`` lists ``revision`` as dropped when the
        document carried that legacy key. ``source_fingerprint`` identifies the
        exact bytes given here.
        """
        if isinstance(document, str):
            try:
                data = document.encode("utf-8")
            except UnicodeError as error:
                raise ToolkitError("invalid_model", "document is not valid UTF-8") from error
        elif isinstance(document, (bytes, bytearray)):
            data = bytes(document)
        else:
            raise ToolkitError("invalid_model", "document must be JSON bytes or text")
        fingerprint = content_fingerprint(data)
        root = _parse_root(data)
        session = object.__new__(cls)
        session._backend = backend
        session._source_fingerprint = fingerprint
        if _shape(root) == "canonical":
            try:
                workbook = hydrate(data)
                if names_json is None:
                    names = workbook_defined_names(workbook)
                    if names is not None:
                        names_json = _encode([entry.to_json() for entry in names.entries])
                converted, conversion = to_toolkit(workbook, input_kinds=input_kinds)
            except (ModelError, ConversionError) as error:
                raise ToolkitError(error.code, error.message) from error
            session._session = cls._engine_session(backend, _encode(converted), names_json)
            session._conversion = conversion
            session._model_version = workbook.model_version
            return session
        if input_kinds:
            raise ToolkitError("invalid_request", "input_kinds applies only to a canonical document")
        session._session = cls._engine_session(backend, data.decode("utf-8"), names_json)
        # The reader checked the legacy key and left it out of the model; say so
        # at the one entry that sees the raw root.
        session._conversion = Conversion(dropped=("revision",), added=()) if "revision" in root else None
        session._model_version = None
        return session

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def revision(self) -> int:
        return self._session.revision

    @property
    def conversion(self) -> Conversion | None:
        """What :meth:`open` dropped and added; None when nothing was converted."""
        return self._conversion

    @property
    def model_version(self) -> int | None:
        """The canonical document's ``model_version``; None for the toolkit shape."""
        return self._model_version

    @property
    def source_fingerprint(self) -> str | None:
        """``sha256:`` identity of the bytes given to :meth:`open`; None otherwise."""
        return self._source_fingerprint

    def snapshot(self) -> dict[str, Any]:
        """A detached copy of the document. It has no ``revision`` key."""
        return json.loads(self._session.snapshot())

    def _report(self, session: Any, workers: int, context: CalcContext | None) -> dict[str, Any]:
        if context is not None and not isinstance(context, CalcContext):
            raise ToolkitError("invalid_request", "context must be a CalcContext")
        calc_json = None if context is None else _encode(context.to_dict())
        return json.loads(_engine_call(
            session.calculate_report, _worker_count(workers), calc_json,
            model_version=self._model_version, source_fingerprint=self._source_fingerprint,
        ))

    def calculate(self, *, workers: int = 1, context: CalcContext | None = None) -> dict[str, Any]:
        """Calculate the current revision and return a ``calculation-report.v2``."""
        return self._report(self._session, workers, context)

    def set_inputs(self, values: Mapping[str, Any], expected_revision: int) -> int:
        """Commit named inputs atomically at ``expected_revision``; the new revision."""
        expected = _expected(expected_revision)
        return _inputs_call(self._session.set_inputs, _values_json(values), expected)

    def preview_inputs(
        self, values: Mapping[str, Any], expected_revision: int, *, context: CalcContext | None = None
    ) -> dict[str, Any]:
        """Report what :meth:`set_inputs` would change, on a fork.

        The owned session is not touched: no revision, published pass or dirty
        cell of it changes. Without a ``context`` each of the two passes
        captures its own.
        """
        expected = _expected(expected_revision)
        payload = _values_json(values)
        fork = self._session.fork()
        base = fork.revision
        if expected != base:
            raise ToolkitError("revision_conflict", f"expected revision {expected} does not match {base}")
        before_document = json.loads(fork.snapshot())
        before = self._report(fork, 1, context)
        proposed = _inputs_call(fork.set_inputs, payload, expected)
        after_document = json.loads(fork.snapshot())
        after = self._report(fork, 1, context)
        changes = []
        for name in sorted(values):
            binding = before_document["inputs"][name]
            sheet, address = _resolve(before_document, binding["sheet"], binding["address"]).rsplit("!", 1)
            changes.append({
                "sheet": sheet,
                "address": address,
                "before": _cell_value(before_document, sheet, address),
                "after": _cell_value(after_document, sheet, address),
            })
        return {
            "applied": False,
            "base_revision": expected,
            "proposed_revision": proposed,
            "changes": changes,
            "before": before,
            "after": after,
        }


def _cell_value(document: dict[str, Any], sheet: str, address: str) -> Any:
    for item in document["sheets"]:
        if item["name"] == sheet:
            return item["cells"].get(address, {}).get("value")
    return None
