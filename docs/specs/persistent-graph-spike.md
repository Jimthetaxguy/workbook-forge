---
author: Claude Code (Opus 5.5)
created: 2026-10-25
updated: 2026-10-27
type: spike-report
status: experiment — not proposed for merge as-is
branch: spike/persistent-graph
summary: "The Rust session keeps its dependency graph between edits and, after a second pass, no longer does whole-workbook work on a small edit. Through the browser command path on main 79b3db2, a one-row edit drops from 118 ms (15K-cell model) and 211 ms (50K cells) to about 20 µs. Edits that invalidate thousands of formulas now cost only their evaluation. calculate() still returns a full report, so the legacy calculateJson path keeps one O(n) copy and serialization."
---
# Persistent dependency graph spike

## Question

`Session::apply` and `Session::calculate` in `rust/src/toolkit.rs` each rebuilt
`Graph` from the whole model. Can the session keep one graph, update only the
edges an edit touches, and recalculate only the dirty subgraph? Or does
something in the design force a rebuild?

## Answer

Incremental update works. Nothing in the graph forces a rebuild. Every graph
property the workbook limits rely on is a sum over cells, and every edge
belongs to exactly one formula cell. So an edit can subtract a cell's old
contribution and add its new one. Phase 1 established that.

Phase 1 also showed that the graph wasn't where the time went. Whole-model
validation, model and report copies, closure recomputation and the command
path's full-value diff each cost O(n) per edit. Phase 2 removed them one at a
time, and each removal is a separate commit with its own equivalence tests.
Nothing structural forced any of them. Each came from a model or report being
handed out by copying the whole thing.

What remains O(n) is listed under "What is still whole-workbook".

## Commits

| Commit | Change | 50K grid, one-row command |
|---|---|---|
| (main 79b3db2) | baseline | 211 ms |
| `Spike: persistent dependency graph…` | graph kept between edits, dirty-subgraph walk | 58 ms |
| `Spike: validate only what an edit can change` | incremental validation, no double validation | 45 ms |
| `Spike: edit the session model in place…` | `Arc` model, in-place edit with undo | 39 ms |
| `Spike: recalculate and publish only what an edit invalidated` | `CalculationUpdate`, in-place publish, `calculate_changes` | 8 ms |
| `Spike: build command deltas from the session's own changes` | command path diffs only listed cells | **0.02 ms** |

The middle column comes from the intermediate measurements taken during the
work. Only the first and last rows come from the matched run in "Latency".

## Terms

- **Touched cells:** precedents an edit gained or released. They are the only
  cells whose presence in the graph an edit can change. `Graph::update`
  returns them and the session accumulates them until the next publish.
- **`CalculationUpdate`:** one calculation's result relative to the report it
  started from: the recalculated (pending) cells, the cells that left the
  selection (dropped), their new values and diagnostics, and the running
  result-cell count.
- **`Published`:** the session's last report plus its result-cell count, held
  in an `Arc` and updated in place.
- **`CalculationChanges`:** the public result of `Session::calculate_changes`.
  It holds `(key, before, after)` for every cell whose value may differ from the
  report published when the run started, plus the diagnostics. Cells it doesn't
  list are unchanged.
- **`ReportsChanges`:** the browser-contract trait for targets that publish
  their calculations and can report changes. `ImportedWorkbook` and `Session`
  implement it. Any other `CommandTarget` keeps the full diff.

## What changed

All changes are Rust only. The Python engine is untouched.

### Phase 1: the graph (`toolkit.rs`)

- `cell_precedents` holds the per-cell body of `Graph::build`. The full build
  and the incremental update share it, so the two can't drift apart.
- `Graph` maintains `dependents` (the reverse index), per-cell
  expanded-reference counts, and running totals for the limits.
- `Graph::update(model, changed)` re-derives edges for the edited cells only,
  releases placeholder entries no formula refers to any more, and returns the
  touched cells. On a limit failure, `apply` restores the edited cells and
  calls `reanalyze` with the restored model, which puts back exactly the prior
  graph.
- `Session::apply_and_calculate` applies, then recalculates. `apply` itself
  still doesn't recalculate, because existing callers and tests expect
  `calculate` to report the cells their edit dirtied.

### Phase 2: everything around the graph

- **Validation.** `apply` used to validate the whole model twice per edit:
  once directly, and again inside `validate_inputs`. A session whose model
  already passed validation now checks only the edited cells, the
  populated-cell limit and the input bindings on edited cells
  (`WorkbookModel::validate_edited`), because sheets, bindings and layout
  can't change under an edit. Any failure reruns full validation, so the
  error is the same one a whole-model check reports.
- **Model ownership.** `SessionState.model` is an `Arc<WorkbookModel>`.
  `calculate` shares it, and `apply` edits it in place through
  `Arc::make_mut`, which copies the model only while a calculation still
  holds it. Each edited cell's previous contents go into an undo list. Any
  rejection restores the model and the graph exactly, whether it comes from an
  invalid edit, validation, the revision counter or a graph limit.
- **Calculation.** With no output bindings, the cells to recalculate are the
  invalidated cells plus touched cells the base report doesn't cover. The
  dropped cells are touched cells that left the graph. Nothing scans the whole
  workbook. With output bindings, the closure is still computed, as before.
  The result-cell limit continues from the published count. An incremental
  run that would exceed it is redone in full, so a truncated report is
  identical to a full calculation's.
- **Publishing.** The update is applied to the published report in place.
  `calculate()` still returns a full `CalculationReport`, unchanged for callers,
  which costs one copy. `calculate_changes()` returns only the changes.
- **Command path** (`browser_contract.rs`, `event_log.rs`).
  `apply_calculate_diff` calculates at the expected revision first if the
  target's published report isn't there. It then applies the edit and diffs
  only the cells `calculate_changes` lists. Responses, `changed`, calc events
  and replay are unchanged. `Applied` now carries a calculation summary
  instead of the whole report. Its `values` are kept only for targets that
  fall back to the full diff.

## Evidence

### Equivalence tests

- **Session (`toolkit.rs`): `persistent_graph_and_incremental_reports_match_full_rebuilds`.**
  - 40 seeds × 60 random batches over two sheets, mixing ranges, cross-sheet
    and mixed-case references, unknown sheets, unsupported functions,
    `#DIV/0!`, cycles, NaN, text, and edits to a number-bound input cell, with
    and without output bindings.
  - After every batch, the result (`Ok` revision or the exact error), the
    model and the graph must equal those of a reference session forced
    through whole-model validation and a full graph build.
  - After about two thirds of the batches, the session runs either
    `calculate` or `calculate_changes`. Values, outputs and diagnostics must
    equal a fresh `calculate(snapshot)`. Every listed change must take the
    previous full calculation's value to the current one, and every unlisted
    cell must be equal in both. The published result-cell count must equal the
    sum over its values.
  - The test requires more than 1000 accepted batches, more than 200
    rejections for invalid values or inputs, and more than 50 cyclic reports.
- **Command path: `tests/incremental_delta.rs`.**
  - 20 seeds × 40 commands through `CommandSession` on two copies of an
    imported workbook. One copy reports changes, and the other is wrapped so
    it takes the full diff.
  - The commands include cycles, errors, unsupported functions, stale bases
    and interleaved legacy `apply_edits_envelope` calls.
  - Responses must be identical, and so must event logs once timing and the
    random session ID are removed. `changed` must contain every value that a
    full calculation shows changed.
- **Focused tests:**
  - placeholder release;
  - limit rollback (the graph and the model are both unchanged);
  - an incremental run that crosses the result limit equals a full
    calculation, and the next run recovers;
  - `apply_and_calculate` through creating and breaking a cycle;
  - a calculation published after a newer edit is stale.

### Mutation checks

Each of these deliberate breaks fails at least one test:

- Phase 1:
  - ignoring clean cycle members;
  - keeping empty reverse-index sets;
  - never releasing placeholders.
- Validation:
  - skipping the input-binding check in `validate_edited`;
  - skipping the per-cell check.
- Model ownership:
  - a no-op `restore` (fails three tests, including the existing
    `invalid_batches_and_revision_conflicts_are_atomic`).
- Calculation:
  - ignoring dropped cells;
  - ignoring touched cells;
  - restarting the result-cell count at zero.
- Command path:
  - skipping the calculation at the expected revision (fails two existing
    `command_envelope` tests).

One break wasn't caught: also listing edited cells as changes. The
reason is that they're always listed already, so I removed that redundant
code instead of keeping it.

### Test suite

- `cargo test` (all targets): 172 passed, 1 ignored, 0 failed.
- `cargo clippy --all-targets -- -D warnings`: clean. `cargo fmt`: applied.
- `bash tools/gate.sh` passed on the clean tree at `4aaa3cc` (the last code
  commit). That covers 3,493 Python tests through the rebuilt native bridge,
  with no unexpected skips, plus the Rust suite, ruff and the whitespace
  check.
- The gate doesn't build for WASM. `cargo check --target
  wasm32-unknown-unknown --lib` passes. Clippy on that target reports one
  error, `drop_non_drop` at `xlsx.rs:305`, which `main` has too.

### Latency

Release build, same machine. `main` (79b3db2) and the spike were run back to
back, and each p50 is the better of two runs. The machine was under variable
load, and `main`'s figures here are 14–17% higher than in a quieter run
earlier the same night. The ratios are what matter.

**Browser command path** (`cargo run --release --example command_latency`):
one `set_cells` envelope through `CommandSession::apply_and_calculate` on an
imported workbook. The command applies the edit, calculates, diffs, logs and
responds.

| Workload | Cells | Evaluated | main p50 | spike p50 |
|---|---|---|---|---|
| chain, edit tail | 10,000 | 1 | 40.4 ms | 0.01 ms |
| chain, edit head | 10,000 | 9,999 | 62.1 ms | 34.7 ms |
| grid, edit one row | 10,000 | 3 | 40.3 ms | 0.02 ms |
| financial, edit one row | 15,023 | 3 | 117.9 ms | 0.02 ms |
| financial, edit growth rate | 15,023 | 7,500 | 133.0 ms | 31.7 ms |
| grid, edit one row | 50,000 | 3 | 211.2 ms | 0.02 ms |
| chain, edit tail | 50,000 | 1 | 197.3 ms | 0.01 ms |

**Legacy path** (`cargo run --release --example realtime_latency`): the
existing benchmark from main, `applyEditsResultJson` → `calculateJson` →
serialize, p50 total.

| Workload | main | spike |
|---|---|---|
| chain 10000, edit tail | 39.4 ms | 0.4 ms |
| financial 3000 rows (18K cells), edit one row | 146.1 ms | 1.3 ms |
| chain 50000, edit head (49,999 evaluated) | 353.6 ms | 106.1 ms |

The phase 1 figures are in the earlier version of this report in the
branch history.

## What is still whole-workbook

- **`calculate()` returns a full report.** Anything calling `calculateJson`
  pays one copy of every value plus serialization: 0.2–0.4 ms and
  164–383 KB in the rows above. The command path avoids this. Making the
  legacy path match would mean changing what `calculateJson` returns.
- **Output bindings.** With bindings, every run recomputes the closure and
  scans the base report for cells that left it. That's O(closure + previous
  values) and is the same as phase 1. The browser opens workbooks without
  bindings. An agent workflow with many bindings would need the closure
  maintained alongside the graph.
- **Runs with no reusable base.** The first calculation, and any run after a
  truncated or failed report, is a full calculation. That's by design.
- **Edits that invalidate a lot.** A head edit on a 10K chain now costs only
  its 9,999 evaluations, about 3 µs each. Making that faster is evaluator work
  (parsing every formula on every evaluation, building `HashMap` environments),
  not session work.
- **Concurrency caveat.** The command path's delta assumes nothing else
  writes to the workbook between calculating at the expected revision and
  calculating the changes. Under the browser's single-threaded worker that
  holds. A concurrent native writer could make the "before" values describe
  a different revision. The full-diff path already has the same issue with
  stale reports.

## Also noticed

- `Graph::build`'s "workbook exceeds 100000 dependency edges" and per-cell
  "formula dependencies exceed 100000 cells" errors can't be reached. Each
  edge comes from an expanded reference, so the expanded-reference limit
  always fails first. The checks stay for parity.
- #31 fixed the O(n²) `read_cell` range reads separately on main. Before that
  fix, a single `SUM` over 2,500 cells took about 80 ms.

## If this graduates

- It splits cleanly into four reviewable PRs, in this order:
  1. graph;
  2. validation;
  3. `Arc` model;
  4. update and changes, with the command path.
  Each later PR depends on the earlier ones. Each commit on this branch
  already has its own tests.
- Decide whether `calculateJson` should offer a delta form. That's the
  remaining O(n) on the legacy path.
- The Python engine has its own session and wasn't touched. The gate's
  Python suite, including the cross-engine tests, passes against this
  branch's native build. If a parity test ever compares `evaluated_cells`
  across engines, it will see different counts for incremental runs.
