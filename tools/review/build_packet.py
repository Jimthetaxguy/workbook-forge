#!/usr/bin/env python3
"""Build and check review packets.

A packet is a plain copy of the files one review lens may see. It holds no
version-control data, no authorship and no status claims, so a reviewer judges
the code and its contract and nothing else.

    build_packet.py build  --source CHECKOUT --lens NAME --stage 1|2 --out DIR
    build_packet.py verify --lens NAME --stage 1|2 DIR
    build_packet.py seal   DIR
    build_packet.py check  DIR

`build` also writes DIR.map.json beside the packet. That file links each
packet file back to its source, records what the packet held when it was
sealed, and is for the coordinator only. Run `seal` again after adding a file
to a packet.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys

LENSES = Path(__file__).with_name("lenses.json")
MANIFEST = "manifest.json"
# Files the coordinator adds to a packet. They are not copied from the source.
COORDINATOR_FILES = frozenset({MANIFEST, "BRIEF.md", "expectations.md", "claims.jsonl"})
# A reviewer's own scripts, and what Python leaves behind when it runs.
REVIEWER_DIRECTORIES = frozenset({"_scratch", "__pycache__"})
_FRONT_MATTER = "---\n"


class PacketError(Exception):
    """The packet breaks a rule of the review protocol."""


def load_lens(name: str) -> dict:
    lenses = json.loads(LENSES.read_text(encoding="utf-8"))["lenses"]
    if name not in lenses:
        raise PacketError(f"unknown lens {name!r}; known: {', '.join(sorted(lenses))}")
    return lenses[name]


def allowed_patterns(lens: dict, stage: int) -> list[str]:
    patterns = list(lens["stage1"])
    if stage == 2:
        patterns += lens["stage2"]
    return patterns


def matches(path: str, patterns: list[str]) -> bool:
    """Match a repository-relative path. `*` stays inside one directory."""
    parts = PurePosixPath(path).parts
    for pattern in patterns:
        wanted = PurePosixPath(pattern).parts
        if "**" in wanted:
            prefix = wanted[: wanted.index("**")]
            if parts[: len(prefix)] == prefix and len(parts) > len(prefix):
                return True
        elif len(wanted) == len(parts) and all(
            fnmatch.fnmatchcase(part, want) for part, want in zip(parts, wanted)
        ):
            return True
    return False


def strip_front_matter(text: str) -> str:
    """Remove a leading YAML block, which carries status and authorship."""
    if not text.startswith(_FRONT_MATTER):
        return text
    end = text.find("\n---\n", len(_FRONT_MATTER) - 1)
    if end == -1:
        return text
    return text[end + len("\n---\n"):].lstrip("\n")


def has_status_front_matter(text: str) -> bool:
    if not text.startswith(_FRONT_MATTER):
        return False
    end = text.find("\n---\n", len(_FRONT_MATTER) - 1)
    block = text[: end if end != -1 else len(text)]
    return any(line.startswith("status:") for line in block.splitlines())


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tracked_files(source: Path) -> list[str]:
    listed = subprocess.run(
        ["git", "-C", str(source), "ls-files", "-z"],
        check=True, capture_output=True,
    ).stdout.decode("utf-8")
    return sorted(name for name in listed.split("\0") if name)


def build(source: Path, lens_name: str, stage: int, out: Path) -> dict:
    if out.exists() and any(out.iterdir()):
        raise PacketError(f"{out} is not empty; choose a new directory")
    patterns = allowed_patterns(load_lens(lens_name), stage)
    files: dict[str, str] = {}
    origin: dict[str, dict] = {}
    for name in tracked_files(source):
        if not matches(name, patterns):
            continue
        original = (source / name).read_bytes()
        content = original
        if name.endswith(".md"):
            content = strip_front_matter(original.decode("utf-8")).encode("utf-8")
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        files[name] = digest(content)
        origin[name] = {"source_sha256": digest(original), "rewritten": content != original}
    if not files:
        raise PacketError(f"lens {lens_name!r} stage {stage} matched no files in {source}")
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"lens": lens_name, "stage": stage, "files": files}
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tip = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    _sidecar(out).write_text(
        json.dumps({"tip": tip, "lens": lens_name, "stage": stage, "files": origin}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    seal(out)
    return manifest


def _sidecar(packet: Path) -> Path:
    return packet.with_name(packet.name + ".map.json")


def _unlisted(packet: Path, manifest: dict) -> list[str]:
    """Files in the packet that the manifest does not list."""
    found = []
    for path in packet_files(packet):
        relative = path.relative_to(packet).as_posix()
        if relative == MANIFEST or relative in manifest["files"]:
            continue
        if REVIEWER_DIRECTORIES & set(PurePosixPath(relative).parts):
            continue
        found.append(relative)
    return found


def seal(packet: Path) -> None:
    """Record, outside the packet, what the packet holds now.

    The manifest sits inside the packet, where a reviewer can rewrite it. The
    record beside the packet is what `check` trusts. Seal again after adding
    a file to the packet or planting a defect in it.
    """
    sidecar = _sidecar(packet)
    record = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else {}
    manifest_bytes = (packet / MANIFEST).read_bytes()
    record["manifest_sha256"] = digest(manifest_bytes)
    record["coordinator_files"] = {
        name: digest((packet / name).read_bytes())
        for name in _unlisted(packet, json.loads(manifest_bytes))
    }
    sidecar.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def packet_files(packet: Path) -> list[Path]:
    return sorted(path for path in packet.rglob("*") if not path.is_dir())


def verify(packet: Path, lens_name: str, stage: int) -> list[str]:
    """Return every rule the packet breaks. An empty list means it is clean."""
    problems: list[str] = []
    patterns = allowed_patterns(load_lens(lens_name), stage)
    for path in sorted(packet.rglob("*")):
        relative = path.relative_to(packet).as_posix()
        parts = PurePosixPath(relative).parts
        if ".git" in parts:
            # Report the entry once, not every file beneath it.
            if parts[-1] == ".git":
                problems.append(f"{relative}: version-control data is not allowed")
            continue
        if path.is_symlink():
            problems.append(f"{relative}: links are not allowed; they can point outside the packet")
            continue
        if path.is_dir() or relative in COORDINATOR_FILES:
            continue
        if not matches(relative, patterns):
            problems.append(f"{relative}: outside what lens {lens_name!r} stage {stage} may see")
            continue
        if relative.endswith(".md") and has_status_front_matter(path.read_text(encoding="utf-8")):
            problems.append(f"{relative}: carries a status block")
    if not (packet / MANIFEST).is_file():
        problems.append(f"{MANIFEST}: missing")
    return problems


def check(packet: Path) -> list[str]:
    """Show whether a reviewer changed, added or removed anything.

    Files under `_scratch/` are the reviewer's own and are not reported.
    """
    manifest_bytes = (packet / MANIFEST).read_bytes()
    manifest = json.loads(manifest_bytes)
    sidecar = _sidecar(packet)
    record = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else {}
    if record.get("manifest_sha256") not in (None, digest(manifest_bytes)):
        return [f"{MANIFEST}: changed since the packet was built"]
    expected = dict(manifest["files"])
    expected.update(record.get("coordinator_files", {}))
    problems: list[str] = []
    for name, wanted in expected.items():
        path = packet / name
        if not path.is_file():
            problems.append(f"{name}: removed")
        elif digest(path.read_bytes()) != wanted:
            problems.append(f"{name}: changed")
    for name in _unlisted(packet, manifest):
        if name not in expected and not ("coordinator_files" not in record and name in COORDINATOR_FILES):
            problems.append(f"{name}: added")
    return sorted(problems)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--source", type=Path, required=True)
    build_parser.add_argument("--lens", required=True)
    build_parser.add_argument("--stage", type=int, choices=(1, 2), required=True)
    build_parser.add_argument("--out", type=Path, required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--lens", required=True)
    verify_parser.add_argument("--stage", type=int, choices=(1, 2), required=True)
    verify_parser.add_argument("packet", type=Path)
    check_parser = commands.add_parser("check")
    check_parser.add_argument("packet", type=Path)
    seal_parser = commands.add_parser("seal")
    seal_parser.add_argument("packet", type=Path)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "build":
            manifest = build(arguments.source, arguments.lens, arguments.stage, arguments.out)
            print(f"built {len(manifest['files'])} files for lens {arguments.lens} stage {arguments.stage}")
            return 0
        if arguments.command == "seal":
            seal(arguments.packet)
            return 0
        if arguments.command == "verify":
            problems = verify(arguments.packet, arguments.lens, arguments.stage)
        else:
            problems = check(arguments.packet)
    except PacketError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
