---
author: Claude Code (Opus 5.5)
created: 2026-10-25
type: spike-report
status: experiment — not proposed for merge as-is
branch: spike/persistent-graph
summary: "The Rust session can keep its dependency graph between edits. An edit updates only the edited cells' edges (about 2 µs), and calculation walks only the invalidated subgraph. Rebased on main d879b3d, edit-to-result latency on a 10K-cell workbook drops from about 38 ms to about 10 ms. The remaining cost comes from whole-workbook copies and validation outside the graph."
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
contribution and add its new one.

The graph itself is no longer where the time goes. The remaining
per-edit cost is whole-workbook copying and validation in the session and the
report, and none of that is graph work (see "Remaining whole-workbook cost").

## What changed (Rust only, `rust/src/toolkit.rs`)

- `cell_precedents` moves the old per-cell body of `Graph::build` into its own
  function. Both the full build and the incremental update call it, so the two
  paths cannot drift apart.
- `Graph` now maintains `dependents` (the reverse index that `reverse()` used to
  recompute on every call), per-cell expanded-reference counts, and running
  totals for the expanded-reference and edge limits.
- `Graph::update(previous, model, changed)` re-derives edges only for the edited
  cells and releases placeholder entries that are no longer referenced (an
  unpopulated cell that no formula refers to any more). If a limit fails, it
  re-derives the same cells from the pre-edit model. That restores the exact
  prior graph without cloning it.
- `SessionState.graph: Option<Arc<Graph>>` holds the graph. It is present only
  when the model has passed validation, input validation and the graph limits,
  and it then matches the model exactly. `apply` uses `Arc::make_mut`, which
  copies the graph only if a calculation is still reading it.
- `calculate_with_graph` evaluates only the selected cells that the dirty set
  invalidated or that the previous report did not cover. Clean cells keep their
  previous values and diagnostics. A clean cell that was on a cycle blocks its
  dependents, which then get the same `dependency_cycle` diagnostic a full run
  gives them. A previous report that was cut short (a global diagnostic,
  `resource_limit` or `worker_failure`) is never reused.
- `Session::apply_and_calculate(edits, expected_revision, workers)` applies the
  edits and then recalculates the dirty subgraph.
- The free functions `calculate` and `inspect` still build a fresh graph. Their
  behavior is unchanged.

### Why `apply` does not recalculate on its own

Existing tests (`incremental_session_*`) and the agent, WASM and XLSX callers
expect `apply` to return a revision and leave calculation to an explicit
`calculate` call whose `evaluated_cells` lists the cells that edit dirtied. If
`apply` recalculated implicitly, the next `calculate` would see an empty dirty
set and evaluate nothing, and every caller would pay calculation cost on each
edit. Automatic recalculation is therefore the opt-in method above. The
existing signatures are unchanged, so the PyO3 and WASM bindings did not need
to change.

## Evidence

### Equivalence to a full rebuild

`persistent_graph_and_incremental_reports_match_full_rebuilds` runs 40 seeds of
60 random edit batches over two sheets. The edits mix values, ranges,
cross-sheet references, mixed-case sheet names, unknown sheets, unsupported
functions, `#DIV/0!` and cycles, with and without output bindings. After every
batch the session graph must equal `Graph::build(snapshot)`, including the
reverse index and totals. After about two thirds of the batches the
incremental report's values, outputs and diagnostics must equal a fresh
`calculate(snapshot)`. The test also asserts that the run produced more than
1500 accepted edits and more than 50 cyclic reports, so it can't pass by only
generating rejected edits.

Mutation check: three deliberate breaks (ignoring clean cycle members, keeping
empty reverse-index sets, never releasing placeholders) each fail this test.

Focused tests cover placeholder release, rollback on a limit failure (the graph
is unchanged and the revision is not consumed), and `apply_and_calculate`
through creating and then breaking a cycle.

### Test suite

- `cargo test` (all targets): 151 passed, 1 ignored, 0 failed (147 existing + 4 new).
- `cargo clippy --all-targets -- -D warnings`: clean. `cargo fmt`: applied.
- `bash tools/gate.sh` passed: 3476 Python tests through the rebuilt native
  bridge, no unexpected skips, the Rust suite, ruff and the whitespace check.
  It ran in a venv local to the worktree, on 030e512 with these changes
  uncommitted.

### Latency (`cargo run --release --example incremental_benchmark`)

Each figure is the median of 25 edits, release build. "Before" runs the same
benchmark against `main`'s own `toolkit.rs`. Compare a before/after pair only
within one table: the two tables were recorded at different times.

**Current: rebased on `main` d879b3d (2026-10-26), which includes #31's O(1)
`read_cell` fast path.**

| Workload (cells) | Edit | Evaluated | Before: apply / calc / total | After: apply / calc / total |
|---|---|---|---|---|
| grid, 4×2500 (10,000) | value | 3 | 12.9 / 25.1 / **38.0 ms** | 3.3 / 6.5 / **9.9 ms** |
| grid, 4×2500 (10,000) | formula | 3 | 12.2 / 24.1 / **36.3 ms** | 3.3 / 6.6 / **9.9 ms** |
| grid + `SUM(D1:D2500)` (10,001) | value | 4 | 13.0 / 26.4 / **39.4 ms** | 3.3 / 7.6 / **10.9 ms** |
| chain of 10,000 | head value | 9,999 | 12.6 / 36.2 / **48.8 ms** | 3.3 / 23.8 / **27.0 ms** |
| chain of 10,000 | tail value | 1 | 12.7 / 24.7 / **37.3 ms** | 3.5 / 6.8 / **10.3 ms** |

**Original: on 030e512, before #31.** Each figure is from the first of two
runs, which agreed within ±10%.

| Workload (cells) | Edit | Evaluated | Before: apply / calc / total | After: apply / calc / total |
|---|---|---|---|---|
| grid, 4×2500 (10,000) | value | 3 | 11.3 / 21.2 / **32.5 ms** | 2.9 / 5.4 / **8.4 ms** |
| grid, 4×2500 (10,000) | formula | 3 | 11.0 / 20.7 / **31.6 ms** | 2.8 / 5.3 / **8.2 ms** |
| grid + `SUM(D1:D2500)` (10,001) | value | 4 | 11.5 / 100.9 / **112 ms** | 2.8 / 83.5 / **86 ms** |
| chain of 10,000 | head value | 9,999 | 11.0 / 29.8 / **40.8 ms** | 2.8 / 18.2 / **21.1 ms** |
| chain of 10,000 | tail value | 1 | 11.0 / 20.8 / **31.8 ms** | 2.8 / 5.3 / **8.1 ms** |

Initial session calculation also fell from about 27 ms to about 16 ms on the grid
and chain workloads, because the graph is built once (in `Session::new`) and the
reverse index is no longer recomputed.

## Remaining whole-workbook cost

Timing each phase of the 8 ms grid edit gave:

| Phase | Time | Cause |
|---|---|---|
| `apply`: clone model | 0.3 ms | `candidate = state.model.clone()` for an atomic edit |
| `apply`: `validate` + `validate_inputs` | 2.6 ms | whole-model validation on every edit |
| `apply`: graph update | **~2 µs** | the incremental part |
| `calculate`: snapshot clone | 0.5 ms | the model and previous report are cloned out of the lock |
| `calculate`: closure + seed | 2.3 ms | the closure is recomputed, and all 10K previous values are copied into the new report |
| `calculate`: publish | 0.3 ms | the report is cloned into `published` |

None of this is caused by the graph. The cause is how the session hands out
data:

1. **`CalculationReport` is a full snapshot.** Every report carries every
   selected cell's value, so even a one-cell recalculation is O(n) to build,
   publish and return. Real O(dirty) latency needs a report that shares
   unchanged values (an `Arc`-backed or persistent map, or a delta report).
   That changes the public report type and the JSON that agents and WASM
   callers receive.
2. **Edits clone and fully validate the whole model.** Copy-on-write sheets
   (`Arc<Sheet>`), plus validation limited to the edited cells and the limits
   they can affect, would remove about 3 ms. The validation rules would need
   an audit first, to confirm each one is local to a cell or a running total
   like the graph limits.
3. **The closure is recomputed on every calculation.** With no output bindings
   it is every graph key. It could be cached and updated alongside the graph.

### Separate finding: O(n²) range reads in the evaluator (fixed on main by #31)

In the original table, the `SUM(D1:D2500)` row spends about 80 ms in a single cell. `read_cell`
in `rust/src/lib.rs` finds each referenced cell by scanning the entire
environment `HashMap` with a case-insensitive comparison. So a range of n
cells costs O(n²). The scan exists because sheet names match
case-insensitively and an unqualified address can fall back to the current
sheet. Normalizing keys when the environment is built would make each lookup
O(1). This spike did not change it. #31 ("Add O(1) exact-key fast path to
read_cell") landed separately on main. In the current table, the same edit
costs about 1 ms more than the grid without the total.

### Also noticed

`Graph::build`'s "workbook exceeds 100000 dependency edges" and per-cell
"formula dependencies exceed 100000 cells" errors cannot be reached. Each
edge comes from an expanded reference, so the expanded-reference limit
always fails first. The checks are kept for parity, and a comment records the
ordering.

## If this graduates

- Keep `Graph::update` and the shared `cell_precedents`. They carry little risk
  and have equivalence coverage.
- Decide the report contract (snapshot or delta) before trying to get latency
  below about 5 ms. That is the structural limit now. #32's delta patches for
  the browser edit loop may already answer part of this question; check them
  first.
- The Python engine (`workbook_forge` Python package) has its own session and
  was not touched. If the Rust behavior changes, parity tests may need a
  matching change there.
