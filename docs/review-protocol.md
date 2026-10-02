---
author: Claude
created: 2026-09-29
agent: claude-code
date: 2026-09-29
type: process-contract
status: first-use
summary: How to review Workbook Forge so the reviewer does not inherit the builder's view. Any agent or person can follow it.
---
# Adversarial review protocol

A review is useful when the reviewer can be wrong in a different way from the
builder. That does not happen by asking a second agent to "review carefully".
A reviewer who reads the builder's notes, commit messages and status reports
starts from the builder's conclusions and tends to confirm them.

This protocol controls what a reviewer sees, in what order, and what it must
hand back. It does not depend on which model or vendor runs the review. Every
rule below is enforced by a command in `tools/review/`.

## Roles

| Role | Does | Must not |
|---|---|---|
| Coordinator | Builds packets, plants defects, runs every reproduction, keeps the ledger | Write findings |
| Reviewer | Reads one packet through one lens and reports defects | Edit code, read another reviewer's output, see the repository history |
| Refuter | Tries to show a finding is wrong | See the reviewer's reasoning |
| Fixer | Changes code for one confirmed finding | Review its own change |
| Verifier | Checks a fix against the finding's reproduction | See the fixer's explanation |

One context holds one role for one finding. The ledger rejects a finding whose
finder, refuter, fixer and verifier are not all different.

## What a reviewer sees

A reviewer works in a **packet**: a plain copy of the files its lens allows,
outside the repository.

| Rule | Reason | Check |
|---|---|---|
| Only files the lens allows | Each lens asks one question. Extra material invites a general opinion | `build_packet.py verify` |
| No version-control data | Commit messages and authorship say what the builder intended | `build_packet.py verify` |
| No status blocks in documents | "Implemented" and "verified" are claims, and claims are what the review tests | Removed on copy; `verify` fails if one remains |
| File identities are plain SHA-256 | They cannot be used to look up history | `manifest.json` |
| The reviewer changes nothing | A reviewer who starts fixing starts defending the fix | `build_packet.py check` |

`tools/review/lenses.json` lists what each lens may see.

## The order of reading

A reviewer who opens the code first judges the code against itself. So each
review is two separate runs.

1. **Stage 1** receives the contract only: the specification, schema or
   protocol document. It writes `expectations.md`: numbered statements of what
   must be true, and for each one how it would try to break it.
2. **Stage 2** receives the code and the stage 1 expectations. Each finding
   names the expectation it tests, or says `unanticipated`.

The two stages are separate runs so that stage 1 cannot look ahead.

## What a reviewer hands back

One JSON object per line, matching `tools/review/findings.schema.json`.

- **A defect needs a reproduction**: a command and the output it printed.
  A description of what the code "would do" is not evidence.
- **A clean result needs the attacks attempted.** `attacks_attempted` may not
  be empty. "Found nothing" without a list of what was tried is not accepted.
- **There is no field for strengths.** The schema refuses unknown fields.
- **No patches.** A finding that contains a diff is rejected.
- **Commands stay inside the packet.** A command may run only the programs
  listed in `tools/review/run_repro.py`, or a program inside the packet. It
  may join commands with `&&`, `||`, `;` and `|`. Outside quotes it may not
  use any other shell syntax: no redirection, no `$`, no `~`, no patterns
  that match file names. It may not name a path outside the packet, or the
  two scripts that drive Excel. Reviews never start Excel or any other
  desktop application.

Check a file of findings with:

```sh
python tools/review/ledger.py validate findings.jsonl --root PACKET
```

## How a finding becomes confirmed

| Status | Meaning |
|---|---|
| `OPEN` | Reported, not yet examined |
| `REFUTED` | A refuter showed it is wrong. The reason is recorded |
| `PLAUSIBLE` | Not refuted, and the coordinator could not reproduce it |
| `CONFIRMED` | Not refuted, and the coordinator reproduced it on unmodified code |
| `STALE` | The cited file changed and the reproduction no longer shows the defect |

The refuter receives the claim, the location, the reproduction and the code.
It does not receive the reviewer's reasoning. When it is unsure, it refutes.

The coordinator runs every reproduction through `tools/review/run_repro.py`,
which enforces a time limit and a memory limit and stops the whole process
group when either is passed.

A verdict holds for the code it was read against. When a cited file changes,
the coordinator runs the reproduction once more. The finding stays open if the
defect still shows.

A later review round may only re-test open findings and changed files. It may
not raise new objections to code that did not change.

## Knowing whether to trust a clean result

A test suite or a reviewer that never reports a problem tells you nothing
until you know it can notice one. `tools/review/canaries.py` makes small
deliberate defects.

1. `run` applies each defect alone to a clean checkout and runs the tests.
   A defect the tests miss is a gap in the tests, and is itself a finding.
2. `plant` copies defects the tests missed into a reviewer's packet. The
   reviewer is told, truthfully, that some defects were planted.
3. `score` counts how many each reviewer found.

A reviewer who finds none of its planted defects has its clean results marked
untrusted. Otherwise the count is reported as it is. With three defects per
lens a percentage would be noise, so none is calculated.

Only defects the tests miss are planted. A defect the tests catch would be
found by running the tests, which measures the tests and not the reviewer.

## Two things agreement does not show

- **The two engines agreeing is not evidence that either is right.** Python
  and Rust are written from one specification. They can share a mistake. A
  difference between them is a finding. Expected values come from Microsoft's
  documented examples or from arithmetic worked independently of both.
- **A test that compares a file to its own copy cannot fail for the right
  reason.** It shows the copies match, not that the content is correct.

## Limits of this protocol

- The command check reads the text of a command. It cannot see what a script
  named in the command does, and it cannot see inside code passed to
  `python -c`. It catches honest mistakes. It does not contain a reviewer who
  sets out to get around it. Only running reviews in a sandbox would.
- The command check refuses a `sed` address written as `/text/`, because it
  looks like a path. Use line numbers.
- `run_repro.py` stops the command and its ordinary child processes. A
  process that starts its own session is outside the limit. `tools/gate.sh`
  does this, so a reproduction must not call it.
- After `canaries.py run`, the compiled Rust programs on disk were built from
  the last planted defect. Build them again before using them.
- pytest reads `pythonpath` from `pyproject.toml` and puts it ahead of
  `PYTHONPATH`. To test a changed copy of the package, pass
  `-o pythonpath=DIRECTORY`. Without it the original is imported and every
  change appears to go unnoticed.
- A lens must include every module that its files import. A packet whose
  code cannot be imported forces the reviewer to write a stand-in.
- A planted defect counts as found when a finding of no more than 40 lines,
  not refuted, covers its line. This shows the reviewer looked there. It does
  not show the reviewer understood the defect, so read the finding.

- The packet keeps a reviewer away from the builder's material by
  arrangement, not by force. A reviewer running on the same machine can read
  other paths if it chooses to. The ledger catches evidence that cites them.
- Each finding records a hash of the instruction files the reviewer's tool
  loaded. If those instructions favour building over doubting, the review
  inherits that. Record it and weigh the result accordingly.
- A defect in Rust source is judged by `cargo test` and by the Python tests
  that launch the Rust example programs. The optional native bridge is not
  rebuilt for each planted defect.
