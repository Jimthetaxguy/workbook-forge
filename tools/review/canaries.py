#!/usr/bin/env python3
"""Plant known defects, to measure the tests and the reviewers.

A reviewer or a test suite that reports no problems is only believable if it
can be shown to notice problems. This tool makes small, deliberate defects.

    canaries.py run   --checkout DIR --defects DEFECTS.json --out RESULTS.json
    canaries.py plant --packet DIR --results RESULTS.json [--limit 3]
    canaries.py score --findings FINDINGS.jsonl --planted PLANTED.json...

`run` applies each defect alone to a clean checkout, runs the tests, and puts
the file back. A defect the tests miss is a gap in the tests. `plant` copies
such defects into a review packet. `score` reports how many each reviewer
found. Finding none means that reviewer's clean verdicts are not trusted.

A defect in Rust source is judged by `cargo test` and by the Python tests that
build and launch the Rust example programs. The optional native bridge is not
rebuilt for each defect, so tests that reach Rust through the bridge do not see it.

DEFECTS.json is a list of objects with id, file, find, replace and why. `find`
must occur exactly once in the file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import build_packet

MAX_DEFECTS = 12
TEST_TIMEOUT = 900
NEARBY_LINES = 5
MAX_FINDING_LINES = 40


class DefectError(Exception):
    """A defect cannot be applied as written."""


def apply(text: str, defect: dict) -> tuple[str, int]:
    """Return the changed text and the first line the change touches."""
    count = text.count(defect["find"])
    if count != 1:
        raise DefectError(f"{defect['id']}: 'find' occurs {count} times in {defect['file']}, expected once")
    if defect["find"] == defect["replace"]:
        raise DefectError(f"{defect['id']}: 'replace' equals 'find'")
    line = text[: text.index(defect["find"])].count("\n") + 1
    return text.replace(defect["find"], defect["replace"]), line


def _git(checkout: Path, *arguments: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(checkout), *arguments],
        check=True, capture_output=True, text=True, input=stdin,
    ).stdout


def _require_clean(checkout: Path) -> None:
    status = _git(checkout, "status", "--porcelain")
    if status.strip():
        raise DefectError(f"checkout has uncommitted changes:\n{status}")


def _test(checkout: Path, defect: dict, python: Path) -> dict:
    """Run the tests that could notice the defect. Return what each step did."""
    environment = dict(os.environ, CARGO_TARGET_DIR=str(checkout / "rust" / "target"))
    steps = []
    if defect["file"].endswith(".rs"):
        steps.append(("cargo test", [
            "cargo", "test", "--quiet", "--manifest-path", "rust/Cargo.toml", "--locked", "--offline",
        ]))
    steps.append(("pytest", [str(python), "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"]))
    outcome = {}
    for name, command in steps:
        try:
            finished = subprocess.run(
                command, cwd=checkout, env=environment, capture_output=True,
                text=True, timeout=TEST_TIMEOUT, check=False,
            )
        except subprocess.TimeoutExpired:
            outcome[name] = "timed out"
            break
        except OSError as error:
            raise DefectError(f"{name} could not start: {error}") from error
        # pytest exits 1 when a test fails. Any other non-zero code means the
        # run itself broke, which says nothing about the defect.
        if name == "pytest" and finished.returncode not in (0, 1):
            raise DefectError(f"pytest did not run (exit {finished.returncode}): {finished.stdout[-300:]}")
        outcome[name] = "failed" if finished.returncode else "passed"
        if outcome[name] != "passed":
            break
    return outcome


def run(checkout: Path, defects: list[dict], python: Path) -> list[dict]:
    if len(defects) > MAX_DEFECTS:
        raise DefectError(f"{len(defects)} defects given; the limit is {MAX_DEFECTS}")
    _require_clean(checkout)
    for kind in sorted({defect["file"].rsplit(".", 1)[-1] for defect in defects}):
        before = _test(checkout, {"file": f"unchanged.{kind}"}, python)
        if any(result != "passed" for result in before.values()):
            raise DefectError(f"the tests do not pass before any defect is applied: {before}")
    results = []
    for defect in defects:
        path = checkout / defect["file"]
        changed, line = apply(path.read_text(encoding="utf-8"), defect)
        path.write_text(changed, encoding="utf-8")
        patch = _git(checkout, "diff")
        try:
            outcome = _test(checkout, defect, python)
        finally:
            _git(checkout, "apply", "-R", stdin=patch)
        _require_clean(checkout)
        caught = [name for name, result in outcome.items() if result != "passed"]
        results.append({**defect, "line": line, "steps": outcome, "caught_by": caught, "survived": not caught})
    return results


def plant(packet: Path, results: list[dict], limit: int) -> list[dict]:
    """Copy surviving defects into a packet and record them beside it."""
    manifest_path = packet / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    planted = []
    for defect in results:
        if len(planted) >= limit:
            break
        if not defect["survived"] or defect["file"] not in manifest["files"]:
            continue
        path = packet / defect["file"]
        changed, line = apply(path.read_text(encoding="utf-8"), defect)
        path.write_text(changed, encoding="utf-8")
        # The manifest follows the planted text, so a later integrity check
        # still shows whether the reviewer altered anything.
        manifest["files"][defect["file"]] = hashlib.sha256(changed.encode("utf-8")).hexdigest()
        planted.append({"id": defect["id"], "file": defect["file"], "line": line, "lens": manifest["lens"]})
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sidecar = packet.with_name(packet.name + ".planted.json")
    sidecar.write_text(json.dumps(planted, indent=2) + "\n", encoding="utf-8")
    build_packet.seal(packet)
    return planted


def matches(finding: dict, defect: dict) -> bool:
    """Whether a finding points at a planted defect.

    A finding that cites a whole file points at nothing, and a refuted
    finding found nothing, so neither counts.
    """
    location = finding["location"]
    span = location["line_end"] - location["line_start"] + 1
    return (
        finding.get("status") != "REFUTED"
        and span <= MAX_FINDING_LINES
        and finding["lens"] == defect["lens"]
        and location["file"] == defect["file"]
        and location["line_start"] - NEARBY_LINES <= defect["line"] <= location["line_end"] + NEARBY_LINES
    )


def score(findings: list[dict], planted: list[dict]) -> dict:
    """Report, for each lens, how many planted defects its reviewer found."""
    report: dict[str, dict] = {}
    for defect in planted:
        entry = report.setdefault(defect["lens"], {"planted": 0, "found": 0, "missed": []})
        entry["planted"] += 1
        if any(matches(finding, defect) for finding in findings):
            entry["found"] += 1
        else:
            entry["missed"].append(defect["id"])
    for entry in report.values():
        entry["clean_verdicts_trusted"] = entry["found"] > 0
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--checkout", type=Path, required=True)
    run_parser.add_argument("--defects", type=Path, required=True)
    run_parser.add_argument("--out", type=Path, required=True)
    run_parser.add_argument("--python", type=Path)
    plant_parser = commands.add_parser("plant")
    plant_parser.add_argument("--packet", type=Path, required=True)
    plant_parser.add_argument("--results", type=Path, required=True)
    plant_parser.add_argument("--limit", type=int, default=3)
    score_parser = commands.add_parser("score")
    score_parser.add_argument("--findings", type=Path, required=True)
    score_parser.add_argument("--planted", type=Path, nargs="+", required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "run":
            python = arguments.python or arguments.checkout / ".venv" / "bin" / "python"
            results = run(arguments.checkout, json.loads(arguments.defects.read_text(encoding="utf-8")), python)
            arguments.out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
            survived = [item["id"] for item in results if item["survived"]]
            print(f"{len(results)} defects, {len(survived)} missed by the tests: {', '.join(survived) or 'none'}")
        elif arguments.command == "plant":
            planted = plant(arguments.packet, json.loads(arguments.results.read_text(encoding="utf-8")), arguments.limit)
            print(f"planted {len(planted)}")
        else:
            findings = [
                json.loads(line)
                for line in arguments.findings.read_text(encoding="utf-8").splitlines() if line.strip()
            ]
            planted = [item for path in arguments.planted for item in json.loads(path.read_text(encoding="utf-8"))]
            print(json.dumps(score(findings, planted), indent=2, sort_keys=True))
    except (DefectError, subprocess.CalledProcessError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
