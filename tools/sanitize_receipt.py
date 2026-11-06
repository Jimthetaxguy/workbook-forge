#!/usr/bin/env python3
"""Sanitise an Excel receipt for publication without touching the workbook.

Usage:
  python tools/sanitize_receipt.py --receipt <receipt.json> --workbook <saved.xlsx> \
      --out-dir <new directory> [--root <repository root>] [--dry-run]

An Excel observation is pinned to the bytes Excel saved (docs/evidence-grades.md),
so the workbook is copied byte for byte and its sha256 is checked before and
after the copy. Only the receipt JSON is rewritten: every machine-local path in
it (a value, a key, or a token inside a longer string) becomes

- the repository-relative path, when it resolves inside ``--root``;
- a content fingerprint ``sha256:<hex>`` (``workbook_forge.model.content_fingerprint``),
  when it names a readable file outside the root;
- its basename, when it names nothing on this machine or sits inside prose.

A machine-local path is a POSIX path under one of the home, temporary or
volume roots in ``MACHINE_LOCAL_ROOTS``, a ``~`` path, a Windows drive path or
a ``file://`` URL. System paths such as ``/usr/bin/osascript`` are left alone.
Relative paths are never touched.

The output directory must be new; an existing path is refused before anything
is written. Beside the two files the tool writes ``sanitized.json``: the
source receipt's sha256, the sanitised receipt's own sha256 (the value a
catalog ``excel_observation.receipt_sha256`` should carry), the workbook's
sha256, and the location and replacement of every change. The original paths
are printed to the terminal only; they never reach the output directory.

The workbook's XML parts are scanned for the same path shapes. A hit is a
warning, never an edit: Excel writes the saving folder into
``xl/workbook.xml`` (``absPath``), and such a package cannot be published
without breaking its hash pin. Deciding what to do about that is the
operator's call (docs/excel-observations.md, "Sharing observation evidence").
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sys
from typing import Any
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from workbook_forge.model import content_fingerprint  # noqa: E402

#: Top-level POSIX directories under which a path names this machine rather
#: than the repository: home directories, temporary folders and mounted volumes.
MACHINE_LOCAL_ROOTS = (
    "Users", "home", "root", "private", "var", "tmp", "Volumes", "mnt", "media", "Library", "Applications",
)
_POSIX = "|".join(re.escape(name) for name in MACHINE_LOCAL_ROOTS)
#: One machine-local path token, as far as the next whitespace or quote.
PATH_TOKEN = re.compile(
    rf"""(?<![\w./\\-])(?:file://)?(?:~(?=/)|/(?:{_POSIX})(?=/)|[A-Za-z]:\\)[^\s"'<>|]*"""
)
_TRAILING_PUNCTUATION = ".,;:)]}"
MANIFEST_NAME = "sanitized.json"


class SanitizeError(FileExistsError):
    """The output directory already exists or an input cannot be used."""


@dataclass(frozen=True)
class Change:
    location: str
    kind: str  # "relative", "fingerprint" or "basename"
    original: str
    replacement: str

    def public(self) -> dict[str, str]:
        """The record written beside the receipt: no original path."""
        return {"location": self.location, "kind": self.kind, "replacement": self.replacement}


@dataclass
class SanitizeResult:
    receipt: str
    receipt_sha256: str
    source_receipt_sha256: str
    workbook: str
    workbook_sha256: str
    changes: list[Change] = field(default_factory=list)
    #: (part, token) pairs from scan_workbook; the token is printed, never written.
    workbook_paths: list[tuple[str, str]] = field(default_factory=list)
    receipt_warnings: list[str] = field(default_factory=list)
    written: Path | None = None

    @property
    def workbook_warnings(self) -> list[str]:
        """One warning per package part, without the path it holds."""
        warnings = []
        for part in sorted({part for part, _token in self.workbook_paths}):
            if part == "<package>":
                warnings.append("the workbook is not a ZIP package; its parts were not scanned")
            else:
                warnings.append(f"{part}: machine-local path present; the workbook bytes were not altered")
        return warnings

    def manifest(self) -> dict[str, Any]:
        return {
            "kind": "sanitized-receipt",
            "sanitized_at_utc": datetime.now(timezone.utc).isoformat(),
            "receipt": self.receipt,
            "receipt_sha256": self.receipt_sha256,
            "source_receipt_sha256": self.source_receipt_sha256,
            "workbook": self.workbook,
            "workbook_sha256": self.workbook_sha256,
            "workbook_bytes_unchanged": True,
            "changes": [change.public() for change in self.changes],
            "workbook_warnings": self.workbook_warnings,
            "receipt_warnings": self.receipt_warnings,
        }


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _split_token(token: str) -> tuple[str, str]:
    """Separate punctuation that closes a sentence from the path itself."""
    tail = ""
    while token and token[-1] in _TRAILING_PUNCTUATION:
        tail = token[-1] + tail
        token = token[:-1]
    return token, tail


def _as_path(token: str) -> tuple[Path, str]:
    """A filesystem path for the token, and the basename to fall back on."""
    text = token[len("file://"):] if token.startswith("file://") else token
    if re.match(r"[A-Za-z]:\\", text):
        return Path(text), PureWindowsPath(text).name
    if text.startswith("~"):
        return Path(text).expanduser(), PurePosixPath(text).name
    return Path(text), PurePosixPath(text).name


def rewrite_path(token: str, root: Path, *, whole: bool) -> tuple[str, str]:
    """(replacement, kind) for one machine-local path token.

    ``whole`` is true when the token is the entire string, which is the only
    place a content fingerprint reads well.
    """
    path, basename = _as_path(token)
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        resolved = None
    if resolved is not None:
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            relative = None
        if relative is not None and str(relative) != ".":
            return relative.as_posix(), "relative"
        if whole and resolved.is_file():
            try:
                return content_fingerprint(resolved.read_bytes()), "fingerprint"
            except OSError:
                pass
    return basename or "path", "basename"


def sanitize_text(text: str, root: Path, location: str, changes: list[Change]) -> str:
    """Rewrite every machine-local path token in one string."""
    stripped = text.strip()
    match = PATH_TOKEN.fullmatch(stripped)
    if match is not None and stripped == text:
        replacement, kind = rewrite_path(text, root, whole=True)
        if replacement != text:
            changes.append(Change(location, kind, text, replacement))
        return replacement

    def substitute(found: re.Match[str]) -> str:
        token, tail = _split_token(found.group(0))
        if not token:
            return found.group(0)
        replacement, kind = rewrite_path(token, root, whole=False)
        if replacement != token:
            changes.append(Change(location, kind, token, replacement))
        return replacement + tail

    return PATH_TOKEN.sub(substitute, text)


def sanitize_document(document: Any, root: Path, location: str = "", changes: list[Change] | None = None) -> Any:
    """Rewrite machine-local paths in every string of a JSON document."""
    if changes is None:
        changes = []
    if isinstance(document, str):
        return sanitize_text(document, root, location or "<root>", changes)
    if isinstance(document, list):
        return [sanitize_document(item, root, f"{location}[{index}]", changes) for index, item in enumerate(document)]
    if isinstance(document, dict):
        out: dict[str, Any] = {}
        for key, value in document.items():
            # The location of a key change names the parent only, so an
            # original path never reaches the change record through its label.
            new_key = sanitize_text(key, root, f"{location}[key]" if location else "[key]", changes) if isinstance(key, str) else key
            here = f"{location}.{new_key}" if location else str(new_key)
            out[new_key] = sanitize_document(value, root, here, changes)
        return out
    return document


def scan_workbook(path: Path) -> list[tuple[str, str]]:
    """(part name, token) for every machine-local path inside the package's text parts.

    A package that is not a ZIP yields one pair with an empty token.
    """
    found: list[tuple[str, str]] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if not info.filename.lower().endswith((".xml", ".rels", ".txt", ".json")):
                    continue
                text = archive.read(info).decode("utf-8", errors="ignore")
                tokens = sorted({_split_token(match.group(0))[0] for match in PATH_TOKEN.finditer(text)})
                found.extend((info.filename, token) for token in tokens if token)
    except zipfile.BadZipFile:
        found.append(("<package>", ""))
    return found


def _check_output_directory(out_dir: Path) -> None:
    if out_dir.exists() or out_dir.is_symlink():
        raise SanitizeError(f"output directory {out_dir} must be new; existing paths are never replaced")
    resolved = out_dir.resolve()
    if resolved == ROOT.resolve() or ROOT.resolve().is_relative_to(resolved):
        raise SanitizeError(f"output directory {out_dir} must be new; refusing the repository root or a parent of it")


def _hash_warnings(receipt: Any, workbook_sha256: str) -> list[str]:
    """Does any hash the receipt records name the workbook that was passed?"""
    recorded: set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str) and key.endswith("sha256") and "workbook" in key:
                    recorded.add(value)
                else:
                    collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(receipt)
    if recorded and workbook_sha256 not in recorded:
        return ["no workbook hash recorded in the receipt equals the workbook passed with --workbook"]
    return []


def sanitize(
    receipt_path: Path,
    workbook_path: Path,
    out_dir: Path | None,
    *,
    root: Path = ROOT,
    dry_run: bool = False,
) -> SanitizeResult:
    receipt_path, workbook_path = Path(receipt_path), Path(workbook_path)
    root = Path(root).resolve()
    if not receipt_path.is_file():
        raise SanitizeError(f"receipt {receipt_path} is not a file")
    if not workbook_path.is_file():
        raise SanitizeError(f"workbook {workbook_path} is not a file")
    if out_dir is not None:
        _check_output_directory(Path(out_dir))
    elif not dry_run:
        raise SanitizeError("--out-dir is required unless --dry-run is given")
    source_bytes = receipt_path.read_bytes()
    try:
        document = json.loads(source_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SanitizeError(f"receipt {receipt_path} is not UTF-8 JSON: {error}") from error
    changes: list[Change] = []
    sanitized = sanitize_document(document, root, changes=changes)
    if changes:
        receipt_bytes = (
            json.dumps(sanitized, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        ).encode("utf-8")
    else:
        # A no-op keeps the receipt bytes exactly, so its hash is unchanged too.
        receipt_bytes = source_bytes
    workbook_sha256 = file_sha256(workbook_path)
    result = SanitizeResult(
        receipt=receipt_path.name,
        receipt_sha256=hashlib.sha256(receipt_bytes).hexdigest(),
        source_receipt_sha256=hashlib.sha256(source_bytes).hexdigest(),
        workbook=workbook_path.name,
        workbook_sha256=workbook_sha256,
        changes=changes,
        workbook_paths=scan_workbook(workbook_path),
        receipt_warnings=_hash_warnings(document, workbook_sha256),
    )
    if dry_run or out_dir is None:
        return result
    out_dir = Path(out_dir)
    # Exclusive creation also refuses a concurrent creator.
    out_dir.mkdir(parents=True, exist_ok=False)
    workbook_copy = out_dir / workbook_path.name
    shutil.copyfile(workbook_path, workbook_copy)
    copied = file_sha256(workbook_copy)
    if copied != workbook_sha256:
        raise AssertionError(
            f"workbook copy hash {copied} differs from source {workbook_sha256}; the output must not be used"
        )
    (out_dir / receipt_path.name).write_bytes(receipt_bytes)
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(result.manifest(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    result.written = out_dir
    return result


def report(result: SanitizeResult, *, dry_run: bool) -> str:
    lines = []
    for change in result.changes:
        lines.append(f"rewrote {change.location}: {change.original!r} -> {change.replacement!r} ({change.kind})")
    if not result.changes:
        lines.append("no machine-local path in the receipt; its bytes are unchanged")
    for part, token in result.workbook_paths:
        if token:
            lines.append(f"WARNING workbook {part}: machine-local path {token!r}; the workbook bytes were not altered")
        else:
            lines.append("WARNING workbook is not a ZIP package; its parts were not scanned")
    for warning in result.receipt_warnings:
        lines.append(f"WARNING receipt {warning}")
    lines.append(f"workbook {result.workbook} sha256 {result.workbook_sha256} (bytes unchanged)")
    lines.append(f"receipt {result.receipt} sha256 {result.receipt_sha256}")
    if result.source_receipt_sha256 != result.receipt_sha256:
        lines.append(f"source receipt sha256 was {result.source_receipt_sha256}")
    if dry_run:
        lines.append("dry run: nothing was written")
    elif result.written is not None:
        lines.append(f"wrote {result.written}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--receipt", type=Path, required=True, help="the receipt JSON to sanitise")
    parser.add_argument("--workbook", type=Path, required=True, help="the workbook the receipt observed; copied unchanged")
    parser.add_argument("--out-dir", type=Path, help="new directory for the sanitised receipt, the workbook copy and the manifest")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root that paths are made relative to")
    parser.add_argument("--dry-run", action="store_true", help="report the changes without writing anything")
    args = parser.parse_args(argv)
    try:
        result = sanitize(args.receipt, args.workbook, args.out_dir, root=args.root, dry_run=args.dry_run)
    except SanitizeError as error:
        print(f"sanitize_receipt: {error}", file=sys.stderr)
        return 2
    print(report(result, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
