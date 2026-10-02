#!/usr/bin/env python3
"""Check and combine review findings.

    ledger.py validate FINDINGS.jsonl [--root PACKET]
    ledger.py merge DIRECTORY -o FINDINGS.jsonl
    ledger.py attest FILE...
    ledger.py check-tests --baseline IDS.txt [--renames RENAMES.tsv]
    ledger.py check-gates --fixes FIXES.jsonl
    ledger.py check-refs --map MAP.md

A finding is accepted only when it can be checked: it names a place, gives a
command and that command's output, and lists what else the reviewer tried.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

try:
    from run_repro import refusal
except ModuleNotFoundError:  # loaded by path, outside its own directory
    import importlib.util

    _spec = importlib.util.spec_from_file_location("run_repro", Path(__file__).with_name("run_repro.py"))
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    refusal = _module.refusal

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "tools" / "review" / "findings.schema.json"
_PATCH = re.compile(r"^(diff --git |--- a/|\+\+\+ b/|@@ -\d+)", re.MULTILINE)
_SEVERITY = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def load(path: Path) -> list[dict]:
    findings = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            findings.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ValueError(f"{path.name}:{number}: not valid JSON: {error.msg}") from error
    return findings


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def problems_in(finding: dict, validator, root: Path) -> list[str]:
    """Return every rule one finding breaks. `root` is the reviewer's packet."""
    label = finding.get("id", "<no id>") if isinstance(finding, dict) else "<not an object>"
    found = [f"{label}: {error.message}" for error in validator.iter_errors(finding)]
    if found:
        return found
    location = PurePosixPath(finding["location"]["file"])
    if location.is_absolute() or ".." in location.parts:
        found.append(f"{label}: location must be a path inside the packet")
    if finding["location"]["line_end"] < finding["location"]["line_start"]:
        found.append(f"{label}: location ends before it starts")
    reason = refusal(finding["evidence"]["command"], root)
    if reason is not None:
        found.append(f"{label}: evidence command {reason}")
    if any(_PATCH.search(text) for text in _strings(finding)):
        found.append(f"{label}: contains a patch; reviewers report, they do not fix")
    if finding["status"] == "CONFIRMED" and "root_reproduction" not in finding:
        found.append(f"{label}: CONFIRMED needs the coordinator's own reproduction")
    if finding["status"] == "REFUTED" and not finding.get("refutation"):
        found.append(f"{label}: REFUTED needs the reason")
    contexts = [value for value in finding["contexts"].values() if value]
    if len(contexts) != len(set(contexts)):
        found.append(f"{label}: finder, refuter, fixer and verifier must be different contexts")
    return found


def validate(findings: list[dict], root: Path | None = None) -> list[str]:
    import jsonschema

    root = root or Path.cwd()
    validator = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    found = [problem for finding in findings for problem in problems_in(finding, validator, root)]
    seen: set[str] = set()
    for finding in findings:
        identity = finding.get("id") if isinstance(finding, dict) else None
        if identity in seen:
            found.append(f"{identity}: id is used twice")
        seen.add(identity)
    return found


def _key(finding: dict) -> tuple:
    claim = re.sub(r"[^a-z0-9]+", " ", finding["claim"].lower()).strip()
    return (finding["location"]["file"], finding["location"]["line_start"], claim)


def merge(directory: Path) -> list[dict]:
    """Combine per-lens files. The same defect reported twice is kept once."""
    kept: dict[tuple, dict] = {}
    for path in sorted(directory.glob("*.jsonl")):
        for finding in load(path):
            kept.setdefault(_key(finding), finding)
    return sorted(kept.values(), key=lambda item: (_SEVERITY[item["severity"]], item["id"]))


def attest(files: list[Path]) -> str:
    combined = hashlib.sha256()
    for path in sorted(files):
        combined.update(hashlib.sha256(path.read_bytes()).digest())
    return combined.hexdigest()


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *arguments], check=True, capture_output=True, text=True,
    ).stdout


def check_tests(baseline: Path, renames: Path | None) -> list[str]:
    """Every test that existed at the baseline must still exist or be mapped."""
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    ).stdout
    current = {line.strip() for line in collected.splitlines() if "::" in line}
    mapping: list[tuple[str, str]] = []
    missing = []
    if renames is not None and renames.is_file():
        for line in renames.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                old, new, *rest = line.split("\t")
                mapping.append((old, new))
                wanted = int(rest[0]) if rest else 1
                have = sum(1 for name in current if name == new or name.startswith(new + "["))
                if have < wanted:
                    missing.append(f"{new}: {have} collected, at least {wanted} expected")
    for identity in baseline.read_text(encoding="utf-8").split("\n"):
        if not identity or identity in current:
            continue
        replaced = [new for old, new in mapping if identity == old or identity.startswith(old + "[")]
        if not any(name == new or name.startswith(new + "[") for new in replaced for name in current):
            missing.append(f"{identity}: no longer collected and not mapped")
    return missing


def check_gates(fixes: Path) -> list[str]:
    """Every applied fix, and the current commit, needs a passing gate log."""
    commits = [entry["commit"] for entry in load(fixes) if entry.get("status") == "applied"]
    commits.append(_git("rev-parse", "HEAD").strip())
    found = []
    for commit in dict.fromkeys(commits):
        log = ROOT / ".verification" / "gates" / f"{commit}.log"
        if not log.is_file():
            found.append(f"{commit[:7]}: no gate log")
        elif log.read_text(encoding="utf-8").rstrip().splitlines()[-1] != f"GATE PASS: commit {commit} (clean)":
            found.append(f"{commit[:7]}: the log does not record a pass for this commit with nothing uncommitted")
    return found


def check_refs(document: Path) -> list[str]:
    """Every branch must be named in the alignment map."""
    text = document.read_text(encoding="utf-8")
    names = set()
    for name in _git("for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes/origin").split():
        name = name.removeprefix("origin/")
        if name not in {"HEAD", "origin"}:
            names.add(name)
    return sorted(f"{name}: not in the map" for name in names if name not in text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    validate_parser = commands.add_parser("validate")
    validate_parser.add_argument("findings", type=Path)
    validate_parser.add_argument("--root", type=Path, help="the reviewer's packet directory")
    merge_parser = commands.add_parser("merge")
    merge_parser.add_argument("directory", type=Path)
    merge_parser.add_argument("-o", "--output", type=Path, required=True)
    commands.add_parser("attest").add_argument("files", type=Path, nargs="+")
    tests_parser = commands.add_parser("check-tests")
    tests_parser.add_argument("--baseline", type=Path, required=True)
    tests_parser.add_argument("--renames", type=Path)
    commands.add_parser("check-gates").add_argument("--fixes", type=Path, required=True)
    commands.add_parser("check-refs").add_argument("--map", type=Path, required=True, dest="document")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "validate":
            found = validate(load(arguments.findings), arguments.root)
        elif arguments.command == "merge":
            merged = merge(arguments.directory)
            arguments.output.write_text(
                "".join(json.dumps(item, sort_keys=True) + "\n" for item in merged), encoding="utf-8",
            )
            print(f"kept {len(merged)} findings")
            return 0
        elif arguments.command == "attest":
            print(attest(arguments.files))
            return 0
        elif arguments.command == "check-tests":
            found = check_tests(arguments.baseline, arguments.renames)
        elif arguments.command == "check-gates":
            found = check_gates(arguments.fixes)
        else:
            found = check_refs(arguments.document)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    for problem in found:
        print(problem)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
