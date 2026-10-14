---
type: technical-specification
status: proposed
base: main c84ec07
summary: Give Rust the same summary, Markdown scan, cell map and paired check that Python intake prints, compared through shared fixtures.
---

# 02 — Rust Markdown scan and cell map

## Problem

`python/workbook_forge/views.py` and `workbook-forge intake --summary|--markdown|--map|--paired`
exist. Rust does not print these views (PR #8, compiler-pipeline step 4). A
Rust application or agent therefore has the model but not the two views that
cross-check it, and the "two views that cross-check one workbook" contract has
one implementation only.

## Outcome

A Rust caller can produce the summary, Markdown scan, cell map and paired
check from a hydrated `Workbook`, and the output agrees with Python's on the
shared fixtures (except for an `engine` field, if one is present).

## Scope

- New module `rust/src/views.rs`, exported from `rust/src/lib.rs`.
- Functions over the hydrated `model::Workbook`: `summary`, `markdown_scan`,
  `cell_map`, `paired_check`.
- A `rust/examples/intake_views.rs` that reads canonical JSON and prints a
  view, matching the Python flags.
- Shared expected outputs under `fixtures/views/` produced by reading the
  Python implementation's *documented contract*, then checked by hand on the
  small fixtures. Neither engine is the reference for the other.

## Non-goals

- Rust xlsx intake into the canonical model (Python-only today; separate work).
- Weak-label and neighbouring-table heuristics (still open in Python too).
- Changing the Python view output.

## Design

- Hand-written, no new crates. Serde is already reviewed.
- The cell map is coordinate-preserving and is the source; the Markdown scan
  is derived from it and every region points back to a `Sheet!A1` range.
- File identity is the sha256 fingerprint already used by Python views. A
  path held by the caller is never printed.
- Paired check: lists cells named by the scan but missing from the map, and
  formula corners that are not stored, with the same keys as Python.

## Schema / model-version impact

None. Views are derived documents, not model data.

## Acceptance tests

```rust
// rust/tests/intake_views.rs
#[test] fn summary_matches_shared_fixture_operating_scenario() {}
#[test] fn cell_map_preserves_every_populated_address() {}
#[test] fn markdown_regions_link_back_to_cell_ranges() {}
#[test] fn paired_check_reports_missing_formula_corner() {}
#[test] fn views_never_contain_source_path() {}   // Workbook with source_path set in memory
#[test] fn views_identify_file_by_sha256_fingerprint() {}
```

```python
# python/tests/test_dual_engines.py (new cases, skipped only when the native bridge is not built)
def test_python_and_rust_cell_map_agree_on_shared_fixtures(): ...
def test_python_and_rust_markdown_scan_agree_on_shared_fixtures(): ...
```

## Dependencies

After the full Excel gate (01). product-specifications.md says the paired
views stay on the Python intake command until that gate closes; Rust views
come after it. Do not start this in parallel with 01 unless James overrides that.

## Open question for James

- **Decision (from PR #8):** should Rust grow these views at all? This spec
  assumes yes. If no, record that in CONTEXT.md and drop this spec.
