#!/usr/bin/env python3
"""Run one reproduction command under a time and memory limit.

A reviewer who looks for hangs and memory exhaustion hands over commands that
hang and exhaust memory. This runner refuses commands that reach outside the
directory it is given, stops the whole process group when a limit is passed,
and prints one JSON record.

    run_repro.py --cwd DIR [--timeout 120] [--memory-mb 2048] -- COMMAND...
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import threading
import time

DEFAULT_TIMEOUT = 120
DEFAULT_MEMORY_MB = 2048
OUTPUT_LIMIT = 8000

# Commands a reproduction has no reason to contain. Excel is driven through
# osascript, so both are refused: reviews never start a desktop application.
FORBIDDEN_WORDS = ("rm", "git", "curl", "wget", "osascript", "sudo", "ssh")
# `open -a` starts a desktop application on macOS.
FORBIDDEN_FRAGMENTS = ("open -a", "| sh", "| bash")
# The shell expands these into text this check never sees.
FORBIDDEN_CHARACTERS = {
    "$": "a shell variable or substitution",
    "`": "a shell substitution",
    "~": "the home directory",
}
_WORD = re.compile(r"[A-Za-z0-9_./~-]+")


def refusal(command: str, root: Path) -> str | None:
    """Return why the command is refused, or None when it may run.

    This reads the command text. It cannot see what a script named in the
    command does, so it is a check on honest mistakes, not a sandbox.
    """
    if "excel" in command.lower():
        # Every flag and tool that starts Excel has the word in its name.
        return "mentions Excel; reviews never start it"
    for character, meaning in FORBIDDEN_CHARACTERS.items():
        if character in command:
            return f"contains {character!r}, {meaning}"
    for fragment in FORBIDDEN_FRAGMENTS:
        if fragment in command:
            return f"contains {fragment!r}"
    for word in _WORD.findall(command):
        name = word.rsplit("/", 1)[-1]
        if name in FORBIDDEN_WORDS:
            return f"uses {name!r}"
    try:
        arguments = shlex.split(command)
    except ValueError as error:
        return f"cannot be read as a shell command: {error}"
    for argument in arguments:
        if any(character.isspace() for character in argument):
            continue  # quoted text, such as a search pattern, is not a path
        if ".." in Path(argument).parts:
            return f"path {argument!r} climbs out of the working directory"
        if argument.startswith("/") and not _inside(Path(argument), root) and not _is_program(argument):
            return f"path {argument!r} is outside the working directory"
    return None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _is_program(word: str) -> bool:
    """An interpreter or tool may live anywhere. A data file may not."""
    if word == "/dev/null":
        return True
    path = Path(word)
    return path.is_file() and os.access(path, os.X_OK)


def _group_memory_mb(group: int) -> float:
    listing = subprocess.run(
        ["ps", "-A", "-o", "pgid=,rss="], capture_output=True, text=True, check=False,
    ).stdout
    total = 0
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] == str(group) and fields[1].isdigit():
            total += int(fields[1])
    return total / 1024


def run(command: str, cwd: Path, timeout: int, memory_mb: int, environment: dict | None = None) -> dict:
    record = {
        "command": command, "exit_code": None, "timed_out": False,
        "memory_exceeded": False, "refused": None, "seconds": 0.0, "output": "",
    }
    reason = refusal(command, cwd)
    if reason is not None:
        record["refused"] = reason
        return record
    started = time.monotonic()
    process = subprocess.Popen(
        command, shell=True, cwd=cwd, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", start_new_session=True,
    )
    stop = threading.Event()

    def watch() -> None:
        while not stop.wait(0.25):
            if time.monotonic() - started > timeout:
                record["timed_out"] = True
            elif _group_memory_mb(process.pid) > memory_mb:
                record["memory_exceeded"] = True
            else:
                continue
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    output, _ = process.communicate()
    stop.set()
    watcher.join()
    record["exit_code"] = process.returncode
    record["seconds"] = round(time.monotonic() - started, 2)
    record["output"] = output[-OUTPUT_LIMIT:]
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--memory-mb", type=int, default=DEFAULT_MEMORY_MB)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)
    words = [word for word in arguments.command if word != "--"]
    if not words:
        parser.error("a command is required after --")
    command = words[0] if len(words) == 1 else shlex.join(words)
    record = run(command, arguments.cwd, arguments.timeout, arguments.memory_mb)
    print(json.dumps(record, indent=2))
    if record["refused"] or record["timed_out"] or record["memory_exceeded"]:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
