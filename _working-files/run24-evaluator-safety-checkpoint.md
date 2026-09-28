---
author: Codex
created: '2026-09-24T23:07:25-04:00'
agent: codex/agent_consumer
date: '2026-09-28T16:05:17-04:00'
type: run-checkpoint
task: Bound formula parser and wildcard evaluation work
status: accepted
summary: Run 24 adds formula-size and nesting checks, iterative long-chain evaluation, and bounded wildcard matching in Python and Rust; independent review findings were corrected and the final review cleared the scoped changes.
next_steps:
  - Build and run the corrected Excel Desktop differential oracle before expanding formula-family coverage.
remaining:
  - Excel Desktop results are unavailable; all compatibility gaps and profile claims remain open pending direct observations.
open_questions: []
---

# Run 24 — Evaluator safety checkpoint

This is the accepted Run 24 checkpoint from 2026-09-24, when the project was named Formula Atlas. The verification results and next dependency below describe that checkpoint; they were not rerun for the 2026-09-28 documentation review. See [CONTEXT.md](../CONTEXT.md) for current project state.

## Scope

- Enforce 8,192 UTF-16-unit formula length, 64 nested function calls, and a Formula Atlas 96-level parenthesis limit in both evaluators.
- Evaluate long unary, postfix, and left-associated binary chains iteratively.
- Replace wildcard backtracking/full-matrix matching with rolling-row matching and a shared 5,000,000-step budget per evaluation; literal-only escaped patterns use linear work and tilde escapes follow the same rule in both engines.
- Add shared cases for escaped ordinary characters, trailing tilde, Unicode lowercase expansion, a long escaped pattern whose naive DP cost exceeds the work budget while the formula remains within the size cap, and a supplementary-character lookup whose UTF-8 size exceeds the limit while its UTF-16 length remains valid.
- Shorten the existing cancellation stress fixture to stay within the formula-size bound while retaining its expected result.

## Verification

- Python 3.13 full suite: 185 passed; `compileall` passed.
- Rust: 31 tests passed with `CARGO_TARGET_DIR` set to this checkout's `rust/target`; `cargo fmt --check`, `cargo check --locked`, and `cargo clippy --all-targets --locked -- -D warnings` passed.
- All three Draft 2020-12 catalog schemas passed with format checking.
- Shared fixture corpus: 1,371 unique cases, including five Run 24 wildcard work, escape, Unicode, and UTF-16-boundary cases.
- Independent review found differences in work accounting, tilde escapes, Unicode literal comparison, and UTF-8-versus-UTF-16 wildcard length checks. Corrections and five shared regression fixtures are in place; final review found no concrete blocker for Excel-sized inputs.
- No direct Excel Desktop checks were performed. Fixture outcomes establish Python/Rust parity, not Excel conformance.

## Next dependency

Repair and run the bounded Excel Desktop probe, capture saved cell values and provenance, then compare the highest-risk existing profiles against those observations before adding formula families.
