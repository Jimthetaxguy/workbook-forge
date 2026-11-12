#!/usr/bin/env python3
"""Structural spec lock for the Verus proofs in src/.

What is locked (hashed into spec.lock):
  - every fn header: signature plus requires, ensures, recommends, decreases
    (exec, proof and spec fns alike);
  - the full body of every spec fn (the math the contracts are written in);
  - the full body of every example_* fn (the calls that prove the
    preconditions can be met, so the contracts are not vacuous);
  - every type definition (struct, enum) inside the proof files;
  - every trusted marker: #[verifier::...] attributes and uninterp spec fns.

What an agent may still change without approval: exec fn bodies and proof fn
bodies (the code and the proof hints). That is the point of the loop.

Always rejected, whatever spec.lock says: assume(, admit(, axiom fns,
external_body, external, external_fn_specification, assume_specification and
exec_allows_no_decreases_clause.

Usage:
  python3 spec_lock.py check     # exit 1 on any difference from spec.lock
  python3 spec_lock.py approve   # a person rewrites spec.lock after review
"""
import hashlib
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "src"
LOCK = HERE / "spec.lock"

BANNED = [
    (re.compile(r"\bassume\s*\("), "assume("),
    (re.compile(r"\badmit\s*\("), "admit("),
    (re.compile(r"\baxiom\w*"), "axiom"),
    (re.compile(r"#\s*\[\s*verifier\s*::\s*external_body\b"), "#[verifier::external_body]"),
    (re.compile(r"#\s*\[\s*verifier\s*::\s*external\s*\]"), "#[verifier::external]"),
    (re.compile(r"#\s*\[\s*verifier\s*\(\s*external"), "#[verifier(external...)]"),
    (re.compile(r"external_fn_specification"), "external_fn_specification"),
    (re.compile(r"assume_specification"), "assume_specification"),
    (re.compile(r"exec_allows_no_decreases_clause"), "exec_allows_no_decreases_clause"),
]
CLAUSE_WORDS = ("requires", "ensures", "recommends", "decreases", "returns",
                "opens_invariants", "no_unwind", "via", "when")
FN_RE = re.compile(
    r"(?P<attrs>(?:#\s*\[[^\]]*\]\s*)*)"
    r"(?P<vis>pub(?:\s*\([^)]*\))?\s+)?"
    r"(?P<mods>(?:(?:open|closed|broadcast|uninterp|const|unsafe)\s+)*)"
    r"(?P<mode>spec|proof|exec)?\s*(?:\(\s*checked\s*\)\s*)?"
    r"fn\s+(?P<name>\w+)")
TYPE_RE = re.compile(r"(?:pub(?:\s*\([^)]*\))?\s+)?(?P<kind>struct|enum)\s+(?P<name>\w+)")
TRUSTED_ATTR_RE = re.compile(r"#\s*\[\s*verifier\s*::\s*(\w+)[^\]]*\]")
# Attributes that only steer the solver and cannot make a false claim pass.
HARMLESS_ATTRS = {"loop_isolation", "rlimit", "spinoff_prover", "opaque", "reveal"}


def strip_comments_and_strings(text: str) -> str:
    out, i, n = [], 0, len(text)
    while i < n:
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        elif text[i] == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append('""')
            i = j + 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def norm(s: str) -> str:
    return " ".join(s.split())


def h(s: str) -> str:
    return hashlib.sha256(norm(s).encode()).hexdigest()[:16]


def matching_brace(text: str, start: int) -> int:
    depth = 0
    for k in range(start, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return k
    raise SystemExit(f"spec-lock: unbalanced braces near offset {start}")


def body_open(text: str, start: int) -> int:
    """Index of the `{` that opens the fn body, skipping braces that belong
    to a contract clause (for example a match inside ensures)."""
    k, depth = start, 0
    while k < len(text):
        c = text[k]
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == ";" and depth == 0:
            return -1  # declaration without body
        elif c == "{" and depth == 0:
            end = matching_brace(text, k)
            rest = text[end + 1:].lstrip()
            if rest.startswith(",") or any(re.match(rf"{w}\b", rest) for w in CLAUSE_WORDS):
                k = end + 1
                continue
            return k
        k += 1
    return -1


def extract(path: pathlib.Path):
    raw = path.read_text()
    text = strip_comments_and_strings(raw)
    rel = path.name
    items, problems = {}, []
    for pat, label in BANNED:
        for m in pat.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            problems.append(f"{rel}:{line}: banned escape hatch {label}")
    for m in FN_RE.finditer(text):
        name, mode = m.group("name"), m.group("mode") or "exec"
        mods = m.group("mods") or ""
        start = m.start()
        bo = body_open(text, m.end())
        header = text[start:bo] if bo >= 0 else text[start:text.find(";", m.end()) + 1]
        key = f"{rel}::{name}"
        items[f"contract {key}"] = h(header)
        if (mode == "spec" or name.startswith("example_")) and bo >= 0:
            items[f"locked-body {key}"] = h(text[bo:matching_brace(text, bo) + 1])
        if "uninterp" in mods or (mode == "spec" and bo < 0):
            items[f"trusted {key} (uninterpreted spec fn)"] = h(header)
    for m in TYPE_RE.finditer(text):
        bo = text.find("{", m.end())
        semi = text.find(";", m.end())
        if bo < 0 or (0 <= semi < bo):
            body = text[m.start():semi + 1]
        else:
            body = text[m.start():matching_brace(text, bo) + 1]
        items[f"type {rel}::{m.group('name')}"] = h(body)
    for m in TRUSTED_ATTR_RE.finditer(text):
        if m.group(1) in HARMLESS_ATTRS:
            continue
        line_no = text.count("\n", 0, m.start()) + 1
        nxt = FN_RE.search(text, m.end())
        owner = nxt.group("name") if nxt else "?"
        items[f"trusted {rel}::{owner} {norm(m.group(0))}"] = h(m.group(0))
    return items, problems


def scan():
    items, problems = {}, []
    for p in sorted(SRC.glob("*.rs")):
        i, pr = extract(p)
        items.update(i)
        problems.extend(pr)
    return items, problems


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    items, problems = scan()
    if cmd == "approve":
        if problems:
            print("\n".join(problems))
            print("spec-lock: refusing to approve while banned constructs are present")
            return 1
        LOCK.write_text(json.dumps(dict(sorted(items.items())), indent=1) + "\n")
        print(f"spec-lock: wrote {LOCK.name} with {len(items)} locked items")
        return 0
    locked = json.loads(LOCK.read_text())
    fails = list(problems)
    for k, v in locked.items():
        if k not in items:
            fails.append(f"locked item removed: {k}")
        elif items[k] != v:
            fails.append(f"locked item changed: {k}")
    for k in items:
        if k not in locked:
            what = "unlocked trusted code" if k.startswith("trusted") else "new unapproved item"
            fails.append(f"{what}: {k}")
    if fails:
        print("\n".join(fails))
        print(f"spec-lock: FAIL ({len(fails)} problem(s)). A person must review and run 'python3 spec_lock.py approve'.")
        return 1
    print(f"spec-lock: OK ({len(items)} locked items match spec.lock, no escape hatches)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
