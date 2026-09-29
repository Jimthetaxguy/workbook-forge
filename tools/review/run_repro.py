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

# A reproduction may run these programs and nothing else. Anything that can
# start a desktop application, reach the network, or change files outside
# the packet is absent from the list, so it does not need to be named.
ALLOWED_PROGRAMS = frozenset({
    "python", "python3", "python3.13", "sh",
    "grep", "sed", "head", "tail", "wc", "cat", "sort", "uniq", "cut", "ls", "diff", "cmp", "true",
})
# Between commands. Each command on either side is checked on its own.
SEPARATORS = ("&&", "||", ";", "|")
# Outside quotes, the shell gives every other character a meaning: expansion,
# redirection, a pattern that matches file names.
PLAIN = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-./=:,+@%")
# These two scripts drive Microsoft Excel. Their tests do not.
_STARTS_EXCEL = re.compile(r"(?<!test_)(excel_oracle|excel_receipt)")
# A slash that begins a path: not inside a word, and followed by a letter.
_ABSOLUTE = re.compile(r"(?<![\w.])/[A-Za-z]")


class _Refused(Exception):
    pass


def _words(command: str) -> list[tuple[str, bool]]:
    """Split a command into words. The flag says whether any part was quoted."""
    words: list[tuple[str, bool]] = []
    text, quoted, started, quote = "", False, False, ""
    index = 0
    while index < len(command):
        character = command[index]
        if quote:
            if character == quote:
                quote = ""
            elif quote == '"' and character == "\\" and index + 1 < len(command):
                # An escape. The next character is taken as it is.
                text += command[index + 1]
                index += 1
            elif quote == '"' and character in "$`":
                raise _Refused(f"contains {character!r} inside double quotes, which the shell expands")
            else:
                text += character
        elif character in "'\"":
            quote, quoted, started = character, True, True
        elif character.isspace():
            if started:
                words.append((text, quoted))
            text, quoted, started = "", False, False
        elif command.startswith(("&&", "||"), index):
            if started:
                words.append((text, quoted))
            words.append((command[index:index + 2], False))
            text, quoted, started = "", False, False
            index += 1
        elif character in ";|":
            if started:
                words.append((text, quoted))
            words.append((character, False))
            text, quoted, started = "", False, False
        elif character in PLAIN:
            text += character
            started = True
        else:
            raise _Refused(f"contains {character!r} outside quotes, which the shell gives a meaning")
        index += 1
    if quote:
        raise _Refused("has a quotation mark that is never closed")
    if started:
        words.append((text, quoted))
    return words


# Programs that run what they are given. The others only read it.
RUNNERS = frozenset({"python", "python3", "python3.13", "sh"})


def _check_word(word: str, root: Path, runs: bool) -> None:
    if runs and _STARTS_EXCEL.search(word):
        raise _Refused(f"runs {word!r}, which can start Excel")
    if "../" in word or word == ".." or word.endswith("/.."):
        raise _Refused(f"{word!r} climbs out of the working directory")
    if _ABSOLUTE.search(word):
        raise _Refused(f"{word!r} names a path from the top of the file system")
    for piece in re.split(r"[=:,]", word):
        if piece and "/" in piece and not any(c.isspace() for c in piece):
            if (root / piece).is_symlink() or not _inside(root / piece, root):
                raise _Refused(f"{piece!r} leads outside the working directory")


def _check_program(word: str, root: Path) -> None:
    if "/" in word:
        if not (word.startswith("./") or word.startswith("_")) or not _inside(root / word, root):
            raise _Refused(f"program {word!r} is not inside the working directory")
        return
    if word not in ALLOWED_PROGRAMS:
        raise _Refused(f"runs {word!r}; allowed programs are {', '.join(sorted(ALLOWED_PROGRAMS))}")


def refusal(command: str, root: Path) -> str | None:
    """Return why the command is refused, or None when it may run.

    A command is one or more simple commands joined by `&&`, `||`, `;` or
    `|`. Each may set variables, must run an allowed program or one inside
    the working directory, and may name only paths inside that directory.

    This reads the command text. It cannot see what a script named in the
    command does, so it is a check on honest mistakes, not a sandbox.
    """
    try:
        expect_program = True
        seen = False
        runs = True
        for word, quoted in _words(command):
            if not quoted and word in SEPARATORS:
                if expect_program:
                    raise _Refused(f"has nothing to run before {word!r}")
                expect_program = True
                continue
            seen = True
            if expect_program and not quoted and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", word):
                _check_word(word.split("=", 1)[1], root, runs=True)
                continue
            if expect_program:
                _check_program(word, root)
                expect_program = False
                runs = word in RUNNERS or "/" in word
            _check_word(word, root, runs)
        if not seen or expect_program:
            raise _Refused("has nothing to run")
    except _Refused as reason:
        return str(reason)
    return None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


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
