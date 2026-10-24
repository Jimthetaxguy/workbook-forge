# Proposed specs (handoff, based on main c84ec07)

Proposals, not accepted contracts. The current truth is `CONTEXT.md`,
`README.md`, `docs/specs/compiler-pipeline.md` and `docs/specs/red-flags.md`.
Where these conflict with those files, those files win.

Recommended order:

| # | Spec | One line | Needs Excel Mac | James decision |
| --- | --- | --- | --- | --- |
| 01 | [excel-export-gate](01-excel-export-gate.md) | Commit the scalar Excel receipt, then add spill/volatile/iterative/quirk classes and oracle marks | Yes | Whether all of Spec 2 gates 07 |
| 02 | [rust-intake-views](02-rust-intake-views.md) | Rust summary, Markdown scan, cell map and paired check matching Python | No (after 01 per product spec) | Whether Rust gets views |
| 03 | [per-cell-memory-bound](03-per-cell-memory-bound.md) | Typed `resource_limit` for oversized cell text and arrays in both engines | No | Limit values |
| 04 | [toolchain-ruff-and-crate-licenses](04-toolchain-ruff-and-crate-licenses.md) | Recheck ruff 0.17, add a read-only crate license review tool, fix the Cargo license warning | No | Ruff move; which license field |
| 05 | [canonical-calculation-session](05-canonical-calculation-session.md) | Revisioned preview/set-inputs/calculate over the canonical Workbook | No | Session at all; report v2 |
| 06 | [typed-bindings-model-v2](06-typed-bindings-model-v2.md) | Binding type, required flag and constraints with a v1→v2 migrator | No | **Model v2 approval required before any code** |
| 07 | [joined-headless-command](07-joined-headless-command.md) | One thin command: intake → views → calculate → export → receipt | No (gated on 01) | Gate scope; duplicate-key policy |

03 and 04 do not depend on Excel and can run while 01 waits for a Mac
session. 02 also needs no Excel, but product-specifications.md holds Rust views
until the full gate closes, so it is ordered after 01. 06 must not start without James's written approval.

Open decisions for James collected from PR #8: duplicate-key refusal policy,
Spec 2 before the joined command, typed bindings / model v2, the canonical
calculation session, `source_path` held in memory but not serialized, Rust
views, ruff 0.17, crate license review, and the per-cell memory bound.

## Notes from the four skipped docs

- Missing spec: a declared result-range (spill placement) contract. The full
  gate in 01 cannot close without it (product-specifications.md, plan item 3).
  Not written here.
- Suggested working order: 01 → 03 → 04 → 05 → 02 → 07, with 06 whenever James
  approves. The vision doc also holds observers, diffusion, repeated-formula
  collapse and formula recovery until the spine is green, so none are specced.

## Revisions (Oct 14)

Checked against `docs/vision.md`, `docs/product-specifications.md`,
`docs/excel-observations.md` and `docs/primitives.md` at main c84ec07.

- 01: the spill refusal already exists (`UnsupportedWorkbook`, `blocked` /
  `unsupported_capability`). Replaced the proposed `unsupported_spill_placement`
  code and its test with the existing behaviour.
- 01: volatile tolerances and allowlists already live in the round-trip
  contract. Removed the proposed separate allowlist file.
- 01: added the harness rules: no workbook open, no global setting changes, no
  cleanup after an uncertain failure, and receipt sanitising before commit.
  The 2026-09-28 SDK round trip is unreceipted narrative, not a finished
  observation.
- 01: replaced the refusal-code question with the missing result-range contract.
- 02: dependency changed from "parallel with 01" to "after the full gate", per
  product-specifications.md. Index row and note updated to match.
- 03: text limit is 32,767 UTF-16 units, not characters. Array cap now points
  at the existing primitives limits (100,000 elements, 1 MiB) and no longer
  proposes 1,048,576.
- 06: added binding `shape` (Spec 2 requires shapes). Added the primitives rule
  (all declared inputs required, extras refused) as a default to match.
- 07: the summary prints `model_version`. Dependency is now the full gate, not
  only the scalar receipt.
