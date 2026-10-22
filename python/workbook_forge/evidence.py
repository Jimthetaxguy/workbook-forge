"""First-class evidence grades for formula functions and fixture cases.

Workbook Forge holds four different kinds of support evidence. They are not
interchangeable, and only the last one is evidence about Excel:

``profile``
    Workbook Forge declares the rule (``semantic_specs``, behavior profiles).
``dual_engine``
    The independent Python and Rust engines agree on the shared fixtures.
    Two engines agreeing is not evidence against Excel.
``expected_value``
    A case matches an established expected value from a Microsoft example or
    independent arithmetic, never from either engine.
``excel_observed``
    Microsoft Excel Desktop produced the value, and a committed receipt
    records the observed workbook's sha256, the Excel version and the time
    Excel ran (``event_time``), separately from when the receipt was read.

An Excel observation is pinned to the bytes it observed. When the workbook at
the recorded path no longer hashes to the recorded value, or the receipt itself
changed, the observation is *stale* and no longer counts.

Each catalog function also carries ``profile_class``: ``semantic`` when a
Workbook Forge profile rule can change a calculated value, ``cosmetic`` when it
cannot, and ``unclassified`` until someone has looked. Every function is
``unclassified`` today. The strict export gate treats ``unclassified`` like
``semantic``, because an unreviewed rule may change a value.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

EVIDENCE_GRADES: tuple[str, ...] = ("profile", "dual_engine", "expected_value", "excel_observed")
PROFILE_CLASSES: tuple[str, ...] = ("cosmetic", "semantic", "unclassified")
DEFAULT_PROFILE_CLASS = "unclassified"
OBSERVATION_FIELDS: tuple[str, ...] = (
    "receipt", "receipt_sha256", "workbook", "workbook_sha256", "excel_version", "event_time",
)
# Receipt statuses written by tools/excel_oracle.py when Excel actually ran,
# saved the owned workbook and every check matched.
OBSERVED_RECEIPT_STATUSES = frozenset({"observed"})
_HEX = frozenset("0123456789abcdef")


class EvidenceError(ValueError):
    """An evidence record is malformed or claims more than its grade allows."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _sha256_hex(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def _event_time(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and not value.startswith("~")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class ExcelObservation:
    """One Excel Desktop observation, pinned to the bytes it observed.

    Paths are repository-relative so a committed record names no machine path.
    ``event_time`` is when Excel ran; it is never replaced by the time a
    receipt is read.
    """

    receipt: str
    receipt_sha256: str
    workbook: str
    workbook_sha256: str
    excel_version: str
    event_time: str

    @classmethod
    def from_record(cls, record: Any, *, where: str = "excel_observation") -> "ExcelObservation":
        if not isinstance(record, Mapping):
            raise EvidenceError("invalid_observation", f"{where} must be an object")
        missing = [field for field in OBSERVATION_FIELDS if field not in record]
        if missing:
            raise EvidenceError("invalid_observation", f"{where} is missing {', '.join(missing)}")
        unknown = sorted(set(record) - set(OBSERVATION_FIELDS))
        if unknown:
            raise EvidenceError("invalid_observation", f"{where} has unknown fields {', '.join(unknown)}")
        for field in ("receipt_sha256", "workbook_sha256"):
            if not _sha256_hex(record[field]):
                raise EvidenceError("invalid_observation", f"{where}.{field} must be a lowercase sha256 hex digest")
        for field in ("receipt", "workbook"):
            if not _relative_path(record[field]):
                raise EvidenceError("invalid_observation", f"{where}.{field} must be a repository-relative path")
        if not isinstance(record["excel_version"], str) or not record["excel_version"].strip():
            raise EvidenceError("invalid_observation", f"{where}.excel_version must name the Excel build")
        if not _event_time(record["event_time"]):
            raise EvidenceError("invalid_observation", f"{where}.event_time must be an ISO 8601 time with a zone")
        return cls(**{field: record[field] for field in OBSERVATION_FIELDS})

    def to_record(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ObservationCheck:
    """The current state of an observation against the files on disk."""

    state: str  # "current", "stale" or "missing"
    reasons: tuple[str, ...]

    @property
    def current(self) -> bool:
        return self.state == "current"


def check_observation(observation: ExcelObservation, root: Path) -> ObservationCheck:
    """Compare a recorded observation with the receipt and workbook on disk.

    ``stale`` means a file exists but its hash changed since Excel observed it;
    ``missing`` means a file the observation names is not present.
    """
    root = Path(root)
    reasons: list[str] = []
    missing = False
    for label, relative, expected in (
        ("receipt", observation.receipt, observation.receipt_sha256),
        ("workbook", observation.workbook, observation.workbook_sha256),
    ):
        path = root / relative
        if not path.is_file():
            missing = True
            reasons.append(f"{label} {relative} is not present")
            continue
        actual = file_sha256(path)
        if actual != expected:
            reasons.append(f"{label} {relative} hash changed from {expected[:12]} to {actual[:12]}")
    if missing:
        return ObservationCheck("missing", tuple(reasons))
    if reasons:
        return ObservationCheck("stale", tuple(reasons))
    return ObservationCheck("current", ())


def observation_from_receipt(receipt_path: Path, root: Path) -> ExcelObservation:
    """Build an observation record from a ``tools/excel_oracle.py`` receipt.

    Only a receipt whose status says Excel ran, saved and matched every check
    becomes an observation. A ``prepared``, ``blocked``, ``mismatch`` or
    ``failed`` receipt is refused with ``not_an_observation``.
    """
    root = Path(root).resolve()
    receipt_path = Path(receipt_path).resolve()
    try:
        relative_receipt = receipt_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise EvidenceError("invalid_observation", "receipt must live inside the repository root") from exc
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    status = receipt.get("status")
    if status not in OBSERVED_RECEIPT_STATUSES:
        raise EvidenceError("not_an_observation", f"receipt status {status!r} is not an Excel observation")
    block = receipt.get("excel_observation")
    if not isinstance(block, Mapping):
        raise EvidenceError("not_an_observation", "receipt has no excel_observation block")
    workbook = receipt_path.parent / str(block.get("workbook", ""))
    try:
        relative_workbook = workbook.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise EvidenceError("invalid_observation", "observed workbook must live inside the repository root") from exc
    return ExcelObservation.from_record({
        "receipt": relative_receipt,
        "receipt_sha256": file_sha256(receipt_path),
        "workbook": relative_workbook,
        "workbook_sha256": block.get("workbook_sha256"),
        "excel_version": block.get("excel_version"),
        "event_time": block.get("event_time"),
    }, where=f"receipt {relative_receipt}")


def receipt_observation_block(
    *, workbook_name: str, workbook_sha256: str, excel_version: str | None,
    event_time: datetime | None = None,
) -> dict[str, Any]:
    """The block ``tools/excel_oracle.py`` writes beside an observed receipt."""
    moment = (event_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "kind": "excel_observed",
        "workbook": workbook_name,
        "workbook_sha256": workbook_sha256,
        "excel_version": excel_version,
        "event_time": moment.isoformat().replace("+00:00", "Z"),
    }


@dataclass(frozen=True)
class FunctionEvidence:
    name: str
    evidence_grade: str
    profile_class: str
    excel_observation: ExcelObservation | None


def validate_function_evidence(entry: Mapping[str, Any]) -> FunctionEvidence:
    """Validate one ``catalog/formulas.json`` function entry's evidence fields.

    The rules the schema also states:

    - ``evidence_grade`` is required and is one of ``EVIDENCE_GRADES``.
    - ``profile_class`` defaults to ``unclassified``.
    - ``excel_observation`` is present exactly when the grade is ``excel_observed``.
    - ``excel_compatible: true`` may appear only on an ``excel_observed`` entry.
      Dual-engine agreement can never mark a function Excel-compatible.
    """
    name = str(entry.get("name", "?")).upper()
    grade = entry.get("evidence_grade")
    if grade not in EVIDENCE_GRADES:
        raise EvidenceError("invalid_evidence_grade", f"function {name} has evidence_grade {grade!r}")
    profile_class = entry.get("profile_class", DEFAULT_PROFILE_CLASS)
    if profile_class not in PROFILE_CLASSES:
        raise EvidenceError("invalid_profile_class", f"function {name} has profile_class {profile_class!r}")
    record = entry.get("excel_observation")
    observation = None
    if grade == "excel_observed":
        if record is None:
            raise EvidenceError("missing_receipt", f"function {name} is excel_observed without a receipt")
        observation = ExcelObservation.from_record(record, where=f"function {name} excel_observation")
    elif record is not None:
        raise EvidenceError(
            "grade_mismatch", f"function {name} carries an Excel observation but is graded {grade}",
        )
    if "excel_compatible" in entry:
        claimed = entry["excel_compatible"]
        if not isinstance(claimed, bool):
            raise EvidenceError("invalid_excel_compatible", f"function {name} excel_compatible must be boolean")
        if claimed and grade != "excel_observed":
            raise EvidenceError(
                "excel_compatibility_without_observation",
                f"function {name} is graded {grade}; only an excel_observed grade with a receipt "
                "can mark a function Excel-compatible",
            )
    return FunctionEvidence(name, grade, profile_class, observation)


def validate_case_evidence(case: Mapping[str, Any]) -> str | None:
    """Validate the optional per-case ``evidence`` record in a fixture case.

    A case may carry ``{"evidence": {"grade": ..., "excel_observation": ...}}``.
    Cases without it are covered by the dual-engine fixture run only.
    """
    record = case.get("evidence")
    if record is None:
        return None
    identifier = case.get("id", "?")
    if not isinstance(record, Mapping):
        raise EvidenceError("invalid_case_evidence", f"case {identifier} evidence must be an object")
    grade = record.get("grade")
    if grade not in EVIDENCE_GRADES:
        raise EvidenceError("invalid_evidence_grade", f"case {identifier} has grade {grade!r}")
    if grade == "excel_observed":
        ExcelObservation.from_record(record.get("excel_observation"), where=f"case {identifier} excel_observation")
    elif "excel_observation" in record:
        raise EvidenceError("grade_mismatch", f"case {identifier} carries an Excel observation but is graded {grade}")
    return grade


def is_excel_compatible(evidence: FunctionEvidence, root: Path) -> bool:
    """True only for an ``excel_observed`` grade whose receipt is current."""
    if evidence.evidence_grade != "excel_observed" or evidence.excel_observation is None:
        return False
    return check_observation(evidence.excel_observation, root).current


@dataclass(frozen=True)
class EvidenceRefusal:
    sheet: str
    address: str
    function: str
    evidence_grade: str
    profile_class: str
    reason: str


def gate_refusals(
    formula_cells: Iterable[tuple[str, str, Iterable[str]]],
    evidence_by_function: Mapping[str, FunctionEvidence],
    root: Path,
) -> list[EvidenceRefusal]:
    """List cells the strict export gate refuses.

    A cell is refused when it calls a function whose ``profile_class`` is
    ``semantic`` or ``unclassified`` and that function has no current
    ``excel_observed`` evidence. ``cosmetic`` functions pass. A function with
    no catalog entry is refused as ``uncatalogued``.
    """
    refusals: list[EvidenceRefusal] = []
    for sheet, address, functions in formula_cells:
        for function in sorted({name.upper() for name in functions}):
            evidence = evidence_by_function.get(function)
            if evidence is None:
                refusals.append(EvidenceRefusal(sheet, address, function, "none", DEFAULT_PROFILE_CLASS, "uncatalogued"))
                continue
            if evidence.profile_class == "cosmetic":
                continue
            if evidence.evidence_grade != "excel_observed" or evidence.excel_observation is None:
                refusals.append(EvidenceRefusal(
                    sheet, address, function, evidence.evidence_grade, evidence.profile_class, "no_excel_observation",
                ))
                continue
            check = check_observation(evidence.excel_observation, root)
            if not check.current:
                refusals.append(EvidenceRefusal(
                    sheet, address, function, evidence.evidence_grade, evidence.profile_class,
                    f"excel_observation_{check.state}",
                ))
    return refusals
