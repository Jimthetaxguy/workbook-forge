---
author: Codex
created: 2026-09-24
agent: claude-code/Claude
date: '2026-09-26T00:52:52-04:00'
type: project-context
task: Build a Python and Rust Excel formula catalog and evaluator
status: active
summary: Language-neutral formula inventory with independently implemented Python and Rust evaluators, shared fixtures, and a bounded XLSX adapter.
next_steps:
  - Build an Excel Desktop differential harness for accepted scalar and array formulas, beginning with high-risk documented boundaries.
  - Use observed Excel results to validate Formula Atlas profiles and update shared fixtures before expanding formula-family coverage.
  - After oracle coverage is reliable, prioritize the next formula slice by documented usage and dependency value.
remaining:
  - Workbook spill projection, general worksheet formula semantics beyond targeted scalar dependency closures, and broad Excel 365 coverage are staged, not complete.
  - "Public release as Workbook Forge also needs: a prose and data pass for the remaining Formula Atlas name (docs, docstrings and error messages, fixture and catalog profile labels, schema `$id` values, and the pattern catalog's `formula_atlas_decision` field); renaming the repository folder; and an SPDX string for `project.license` (setuptools deprecates the table form, with removal due 2027-02-18)."
open_questions: []
---
# Formula Atlas Context
## Purpose
Build a custom, reviewable glossary and evaluator for Excel formulas in Python and Rust. The glossary aims to enumerate Excel 365 syntax and worksheet functions; the engines make narrower, explicit claims about what they actually calculate.

**Workbook Forge** is the planned public name (James, 2026-09-26). The Python distribution, its import package, and the Rust crate are already named `workbook_forge`. The repository folder, the git branch, and the Formula Atlas name in prose and data are unchanged for now.
## Terms
- **Catalogued:** a formula feature or function has an entry with provenance and compatibility metadata.
- **Parsed:** the engine recognizes the construct and produces a typed representation.
- **Evaluated:** the engine calculates the construct for its documented input domain.
- **Conformance-tested:** shared cases verify the behavior in Python and Rust; separate Excel spot-check evidence is still needed for Microsoft compatibility claims.
- **Unsupported:** the evaluator refuses the operation with a typed reason; it does not approximate silently.
- **Table formula result range:** cells whose calculation logic is stored in a table part's calculated-column or totals-row metadata; workbook calculation refuses dependencies on these cells unless table semantics are explicitly supported.
- **Evaluator profile:** a deterministic implementation choice used where Excel documentation is incomplete or direct Excel evidence is absent; it must not be presented as verified Excel behavior.
- **Evaluator resource limits:** formulas are capped at 8,192 UTF-16 code units and 64 nested function calls, following documented Excel limits. Formula Atlas also caps parenthesis nesting at 96 and wildcard matching at 5,000,000 matching-state steps per evaluation; those two limits are local safety profiles.
- **Golden corpus:** language-neutral JSONL cases containing formula, workbook context, expected result or error, and compatibility profile.
- **ArrayValue:** a bounded, immutable rectangular formula result with explicit row and column shape; it does not place values into worksheet spill cells. Binary operators lift elementwise over equal shapes, with scalar-only broadcasting.
- **FILTER mask:** a one-dimensional row or column include vector whose length must match the selected source axis; two-dimensional masks are rejected by the current evaluator profile.
- **Binary comparison profile:** Binary operator errors propagate left to right, including non-finite numeric literal errors. ASCII text compares without case; if either text value contains non-ASCII characters, comparison is exact. Blank, Boolean, and numeric values compare after finite binary64 coercion; nonfinite values return `#NUM!`; mixed text/numeric equality is false, inequality is true, and relational comparison returns `#VALUE!`. These are Formula Atlas rules, not verified Excel collation or coercion behavior. Adjacent 16-digit literals are parity probes beyond Microsoft’s documented precision, not Excel result oracles.
- **FormulaResult / evaluate_result:** the shape-preserving result contract shared by Python and Rust; the scalar `evaluate` APIs keep a local `#VALUE!` boundary for top-level arrays and ranges.
## Boundaries
- Target: Excel for Microsoft 365 desktop, with availability/version metadata retained.
- Languages: independent Python and Rust implementations against one catalog and fixture corpus.
- Primary workbook format: `.xlsx`; other formats require separately audited adapters.
- Third-party code and dependencies: permissive open-source licenses only, with no copyleft, so the project stays enterprise-friendly. The rule covers development and transitive dependencies; `README.md` lists the accepted licenses and `python/tests/test_project_policy.py` enforces them.
- Jev is a development-time reviewer only. It is not linked into runtime, and its results are advisory.
- Macros and external data are never executed or fetched.
## Current State
Runs 1–24 are accepted. Coverage stands at 115 implemented functions, 85 detailed semantic specs, 110 source records, and 1,371 shared fixtures. Run 24, the latest, bounds formula size, nesting, and wildcard work, and passed the full Python/Rust and schema gates. No direct Microsoft Excel checks have been performed. The 521-entry source inventory remains much broader than implementation coverage; 406 functions remain catalog-only. FILTER, SORT, and UNIQUE return bounded, shape-preserving array results; worksheet spill projection remains unsupported. SORT/UNIQUE comparison, equality, coercion, and output precision include explicit Formula Atlas profiles. Do not describe the package as Excel-complete.
- The source-linked function inventory contains 521 records; it is a versioned discovery catalog, not an evaluator coverage claim.
- The Python and Rust evaluators implement the same bounded scalar, reference, operator, common-function, conditional-aggregation, and rectangular formula-result slice. Both load `fixtures/formula-cases.jsonl`; Rust runtime dependencies are empty.
- `catalog/formulas.json` records per-language status; a function is `conformance-tested` only when shared fixture coverage passes in both engines.
- The Python wheel installs the JSON catalogs under `share/workbook_forge/catalog`; the API resolves either installed data or the source-tree catalog. `catalog/open_source_patterns.json` records only permissively licensed (no copyleft) implementation references, license evidence, and adoption decisions; no third-party source code is copied.
## Where things are
- `docs/run-history.md`: per-run history for Runs 1–24 and the pre-loop baseline, moved here from this file and `README.md` on 2026-09-25.
- `docs/behavior-profiles.md`: current behavior and profile notes by function family, plus workbook adapter details.
- `.autoresearch/state.json`: the authoritative accepted-run ledger. `.autoresearch/config.json` holds the loop criteria and `eval_command`.
- `_working-files/`: dated checkpoint and review notes.
- `_archive-2026-09-25-L1/`: a git-ignored archive of the pre-git backup copies (`*.bak-*`, `.autoresearch/_archive-*`, `.autoresearch/backups/`), with `MANIFEST.tsv` and `ROLLBACK.sh`.
## Working conventions
- Checkpoint accepted work with local git commits. The ignore rules exclude `*.bak-*`, `_archive-*/`, and `.autoresearch/backups/`, so ad-hoc backup copies are no longer needed.
- Append each run's summary to `docs/run-history.md`. Keep `README.md` and this file limited to the current state.
- Run the Rust gates with `CARGO_TARGET_DIR` inside the checkout, as `eval_command` does. The machine-wide `~/.cargo-target` mixes build artifacts between copies of the crate.
## Activity
### 2026-09-26T00:52:52-04:00 — claude-code/Claude
- Changed: at James's request, the Python distribution and the Rust crate are now spelled `workbook_forge`, matching the import and library name. The installed catalog folder (`share/workbook_forge/catalog`) and the temporary-file prefix use the same spelling.
- Why/where: James wants one spelling, `workbook_forge`, everywhere.
- Evidence: the crate tripwire in `test_project_policy.py` failed first on the old `workbook-forge` lock entry. After the change, `eval_command` exits 0 (Python 190 passed; Rust 31 passed; fmt, `check --locked`, and Clippy clean). A fresh-venv install of the new wheel reports metadata Name `workbook_forge`, evaluates `=SUM(1,2,3)` to 6, and reads its catalog from `share/workbook_forge/catalog`. `cargo metadata` reports crate and library `workbook_forge`. The name is unregistered on PyPI and crates.io, and both registries treat it and `workbook-forge` as one name.
- Next/remaining: uncommitted, together with the rename below.
### 2026-09-26T00:46:21-04:00 — claude-code/Claude
- Changed: renamed the Python distribution `formula-atlas` and the Rust crate `formula-atlas-rust` to `workbook-forge`, with import and library name `workbook_forge`. `python/formula_atlas/` moved to `python/workbook_forge/`, and installed catalog data moved from `share/formula-atlas/catalog` to `share/workbook_forge/catalog`. The wheel now ships only `workbook_forge`; before, namespace discovery also packaged `python/tests`. Test imports, the Rust crate tripwire in `test_project_policy.py`, `Cargo.lock`, and the README/CONTEXT package references were updated. Prose, data labels, and history still say Formula Atlas.
- Why/where: James asked to rename the crates and Python packages.
- Evidence: the tests failed first on the missing `workbook_forge` module. After the rename, the full `eval_command` exits 0 (Python 190 passed; Rust 31 passed; fmt, `check --locked`, and Clippy clean). The wheel built from HEAD contained a top-level `tests` package; wheels built from clean and cache-laden copies of the new tree contain only `workbook_forge` and six catalog files. In a fresh venv, run outside the repo, the installed wheel evaluates `=SUM(1,2,3)` to 6 and reads its catalog from the venv's `share/workbook_forge/catalog`. `formula_atlas` and `tests` are not importable. `cargo metadata` reports crate `workbook-forge` with library target `workbook_forge`.
- Next/remaining: uncommitted. The prose/data rename pass and the repository folder rename are listed under `remaining`.
### 2026-09-26T00:40:00-04:00 — claude-code/Claude
- Changed: recorded Workbook Forge as the planned public name under Purpose, and listed the release-prep work it depends on under `remaining`.
- Why/where: James approved the name in a Codex session at 00:28, but it was not yet written in the repo or the shared memory.
- Evidence: `workbook-forge` and `workbookforge` are unregistered on PyPI, crates.io, and npm (HTTP 404 on 2026-09-26). A GitHub search found one undescribed, 0-star `workbook-forge-portfolio` repo and unrelated hits, and a web search found no software product with the name. This is not a trademark search. setuptools 84.0.0 source gives the license-table removal date.
- Next/remaining: this edit is uncommitted. The rename itself waits for release prep.
### 2026-09-25T16:13:46-04:00 — claude-code/Claude
- Changed: replaced the MIT-or-Apache-2.0-only rule with James's intent, permissive open-source licenses with no copyleft. The change covers README, this file, the loop config, and the pattern catalog's data, schema, validator, and tests. Added `python/tests/test_project_policy.py` (schema validation, license check of installed Python dependencies, Rust crate review tripwire). Declared the `test` extra and `rust-version = "1.88"`.
- Why/where: James clarified that the goal is enterprise-friendly open source with no copyleft. The current test tools (pytest pulls in BSD-2-Clause pygments) already comply with that rule but broke the old wording.
- Evidence: the full `eval_command` gate (Python 3.13, 190 passed), Rust 1.88.0 tests with the declared minimum, a 10-case SPDX evaluator check, and a crate tripwire that fails as intended. The pre-change suite also passed on Python 3.12 and 3.14. Running the new policy tests on those versions needs jsonschema's dependencies, which were not in uv's offline cache.
- Next/remaining: Rust toolchain pinning is deferred, because pinning 1.98.1 downloads a separate toolchain.
### 2026-09-25T11:15:29-04:00 — claude-code/Claude
- Changed: moved the per-run history to `docs/run-history.md` and the long behavior notes to `docs/behavior-profiles.md`, word for word, and refreshed the Current State counts. Archived the pre-git backup copies into `_archive-2026-09-25-L1/`.
- Why/where: James asked for a repo cleanup while Codex is paused.
- Evidence: a sentence-level no-loss check against HEAD, and the full Python/Rust gates.
- Next/remaining: Codex remains the coordinating writer of this file. Future run notes go in `docs/run-history.md`.
