# Proposed specs (handoff)

Proposals, not accepted contracts. The current truth is `CONTEXT.md`,
`README.md`, `docs/specs/compiler-pipeline.md` and `docs/specs/red-flags.md`.
Where these conflict with those files, those files win. Specs 01–07 declare
`base: main c84ec07` in their frontmatter, and spec 08 declares
`base: main 580aacd`. Specs 09–14 were written against `main` `41f28d3` and
still declare that base. Re-base a spec when it is next edited. This index
was corrected on 2026-11-07 against `main` `c506165`; the 2026-11-06 recheck
was against `580aacd`.

Recommended order:

| # | Spec | One line | Needs Excel Mac | James decision |
| --- | --- | --- | --- | --- |
| 01 | [excel-export-gate](01-excel-export-gate.md) | Commit the scalar Excel receipt, then add spill/volatile/iterative/quirk classes and oracle marks | Yes | Whether all of Spec 2 gates 07 |
| 02 | [rust-intake-views](02-rust-intake-views.md) | Rust summary, Markdown scan, cell map and paired check matching Python | No (after 01 per product spec) | Whether Rust gets views |
| 03 | [per-cell-memory-bound](03-per-cell-memory-bound.md) | Typed `resource_limit` for oversized cell text and arrays in both engines | No | Limit values |
| 04 | [toolchain-ruff-and-crate-licenses](04-toolchain-ruff-and-crate-licenses.md) | Recheck ruff 0.17, add a read-only crate license review tool, fix the Cargo license warning | No | Ruff move; which license field |
| 05 | [canonical-calculation-session](05-canonical-calculation-session.md) | Revisioned preview/set-inputs/calculate over the owner model (reframed 2026-11-09) | No | Settled by G-A: the session runs over the owner model (09 step 1); report v2 (09 §4) |
| 06 | [typed-bindings-model-v2](06-typed-bindings-model-v2.md) | Binding type, required flag and constraints with a v1→v2 migrator | No | Approved 2026-11-09 under G-A with the amended binding shape (09 §3); code at 09 steps 2 and 6 |
| 07 | [joined-headless-command](07-joined-headless-command.md) | One thin command: intake → views → calculate → export → receipt | No (gated on 01) | Gate scope; duplicate-key policy |
| 08 | [meaning-ownership-map](08-meaning-ownership-map.md) | Field- and operation-level map of which representation owns which spreadsheet meaning, the companions each route needs, the edges that exist or are missing, and a proposed owner per concept | No | None; it is the input to the convergence decision behind 05 and 06 |
| 09 | [contract-convergence](09-contract-convergence.md) | One versioned contract for workbook meaning with the session and the preservation envelope as companions; both candidate bases costed, one recommended, six migration steps with adapters and evidence | No | **G-A decided 2026-11-09:** Base B, the toolkit binding shape with `kind` optional, both version fields to 2 (09 §3) |
| 10 | [preservation-envelope](10-preservation-envelope.md) | The named companion that preserves what the model does not represent: source identity, baseline, guards, text layout, carry inventory, invalidation rules (proposal in progress) | No | None expected |
| 11 | [source-route](11-source-route.md) | Source generation as one capability: three source modes, the runtime mechanism decided on the equivalence data, reverse lowering, editable-source levels (proposal in progress) | No | None expected |
| 12 | [composition-transformations](12-composition-transformations.md) | Extraction of a dependency closure into a named-input composition and placement back into cells, with provenance and reference rules (proposal in progress) | No | None expected |
| 13 | [export-and-writeback](13-export-and-writeback.md) | Five destination intentions, export planning, and the original-file update contract with compatibility check, staging, conflict protection and recovery (proposal in progress) | No | **G-C: writeback semantics** |
| 14 | [result-range-contract](14-result-range-contract.md) | The spill placement capability chain: array semantics, anchor and ownership, collisions and growth, dependency effects, atomic publication, projection and OOXML emission, Excel evidence (proposal in progress) | Yes, for its evidence | The spill field takes the version after v2 (G-A, 09 §3); whether the first release includes spill |

03 and 04 do not depend on Excel and can run while 01 waits for a Mac
session. 02 also needs no Excel, but product-specifications.md holds Rust views
until the full gate closes, so it is ordered after 01. 06 starts at spec 09 step 2, under the shape G-A fixed.

**G-A**, the schema and model version change, was decided on 2026-11-09 (09 §3:
Base B, the amended toolkit binding shape, both version fields to 2; the spill
field waits for the version after v2). The open decisions for James fall under
the three remaining gates, also listed in `CONTEXT.md` `open_questions`:
**G-B** vision wording (whether `docs/vision.md` moves to the toolkit framing
now or after the contracts settle); **G-C** writeback semantics for the
original file (conflict rule, staging, recovery, what the same baseline means);
**G-D** public contract commitments (anything tagged, published or promised to
a host). The smaller questions collected from PR #8 still stand: duplicate-key
refusal policy and gate scope (07), red-flag Spec 2 before the joined command
(01, 07), `source_path` held in memory but not serialized, Rust views (02),
ruff 0.17 and the crate license field
(04), and the per-cell memory bound values (03).

## Notes

Specs 09–14 were written against `main` `41f28d3`. The result-range contract
is [spec 14](14-result-range-contract.md). The working order is the table above.

The vision doc holds observers, diffusion, repeated-formula collapse and
formula recovery until the spine is green, so none of those are specced here.

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
