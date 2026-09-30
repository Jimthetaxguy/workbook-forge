---
author: Codex
created: 2026-09-24
agent: codex/Codex
date: '2026-09-29T06:30:00-04:00'
type: project-context
task: Build an SDK for Excel in agent-ready formats using independent Python and Rust implementations
status: active
summary: Workbook Forge is an agent-ready Excel compiler; the v1 spine is a versioned canonical model, one shared Python/Rust calculation, Excel recalc roundtrip evidence, and a headless path before broader intake. The model, one bound calculation and workbook intake are built; the Excel proof and the headless path are outstanding.
next_steps:
  - "On a Mac with Excel, run `python3 tools/canonical_excel_receipt.py --fixture fixtures/operating-scenario.workbook.json --output-dir receipts/canonical-operating-scenario-excel --excel` and commit the receipt. Spill placement stays blocked until export can write those cells."
  - Fix the confirmed defects in order of severity, starting with the counting functions. Take expected values from Microsoft's examples or decimal arithmetic, never from either engine.
  - Decide whether bindings get a value type, required status and constraints. That changes the schema and needs a new version.
  - Put the proven intake → model → calculation → export path behind the Python headless CLI, then expose Rust operations with matching behavior.
  - Use docs/product-specifications.md as the durable product and acceptance contract for intake, compilation, headless use, formula hypotheses and evidence-led coverage.
  - Expand the verified agent operation contract against concrete workbook tasks; keep framework/MCP adapters thin and retain source/revision-aware results.
  - Use observed Excel results to validate Workbook Forge profiles and update shared fixtures before expanding formula-family coverage; keep observers optional and formula-recovery candidates separate and uncertain.
  - After the core loop is evidenced, prioritize additional formula families by documented workbook use and dependency value.
remaining:
  - Worksheet spill projection, volatile/iteration/quirk round-trip classes, cross-backend bound export, and broad Excel 365 coverage are not complete.
  - From impl/v1-intake and impl/v1-calc-binding, intake is ported. Typed binding constraints, the calculation session with revisions, and refusal of duplicate JSON keys are not. Agent-headless has not started its implementation.
  - No pull request so far has had checks run on it or a review; mergeability alone is not acceptance evidence. tools/gate.sh is the check to run.
  - 31 confirmed findings from the first review are open. The counting functions and the criteria type rule (parity-02 and parity-04) are fixed on agent/claude-quality-counting-20260930; the fixes carry Workbook Forge profiles that have not been checked in Excel.
  - Six more are open from the review of the unified branch: the toolkit workbook form takes a missing version as 1; versions are not checked on write; a formula that refers to a blank cell has a null result; no calculation report or origin `calculated`; a formula that uses a defined name is reported as a parse error; `_xHHHH_` escapes in text are not decoded.
  - "GitHub reports Jimthetaxguy/workbook-forge as public, verified 2026-09-28; historical private-release preparation notes in docs/run-history.md describe their original checkpoints."
open_questions: []
---
# Workbook Forge Context
## Purpose
Build an **SDK for Excel in agent-ready formats**: expose supported workbook data, formulas, dependencies, presentation and explicit business bindings as structured objects that agents can inspect, calculate, compose and deliver back as editable Excel workbooks. Independent Python and Rust implementations own these operations. The formula glossary aims to enumerate Excel 365 syntax and functions; evaluator and workbook capabilities make narrower, explicit claims.

**Workbook Forge** is the product name selected for public release on 2026-09-26. The Python distribution, import package, and Rust core crate are named `workbook_forge`. The canonical checkout tracks GitHub `main`; GitHub visibility is public as of the live 2026-09-28 check. The toolkit implementation was merged to `main` on 2026-09-28.
## Terms
- **Spreadsheet primitive:** an identifiable operation with typed arguments, native implementations, explicit behavior limits and compatibility evidence. It can be used without a workbook.
- **Composition:** a structured calculation combining primitive calls, named inputs and operators while retaining operation identities for inspection. It can power scripts, agents or a later application interface.
- **Semantic profile:** the declared rules for coercion, blanks, errors and result types. An Excel function name identifies a correspondence; it does not prove complete compatibility.
- **XML structural pattern:** an exact namespace-qualified path through an OOXML part; same-named tags inside unrelated extensions do not match worksheet cells.
- **Extraction record:** a bounded read-only observation carrying package part, XML path, worksheet/cell location, original text and interpreted metadata. Formula function status comes from the existing catalogs.
- **Derived shared formula:** an inspection expression copied from a validated shared-group master; it does not enable calculation or editing of grouped cells.
- **Agent-ready format:** a structured representation with explicit types, identities, operations, revisions and diagnostics, preserving the supported workbook meaning needed for an agent task. JSON alone does not establish that contract.
- **Agent tool adapter:** a thin interface mapping tool calls to SDK operations. Python and Rust independently implement nine operations, discovery schemas, focused views and JSON-lines runners; neither adapter owns new calculation semantics. MCP remains future work.
- **Input preview:** a detached calculation of proposed inputs, with before/after outputs and cell changes; it never changes the owned session or writes files.
- **Calculation provenance:** source formulas, references, explicit bindings and model revision attached to a result; imported caches are never evidence that Forge calculated a value.
- **Workbook model:** sparse sheets, authored content, formulas, styles, and explicit input/output bindings, implemented independently in each language and separate from XML.
- **Canonical workbook schema:** versioned serialized contract for `Cell`, `Formula`, `Sheet` and `Workbook`; Python and Rust hydrate native typed structures from the same bytes.
- **`model_version`:** version of the serialized workbook artifact, distinct from schema-version evolution and visible in headless intake summaries.
- **Markdown scan:** readable, potentially lossy view derived from the coordinate-preserving cell map; never the workbook source of record.
- **Cell-store boundary:** cell-store owns the sealed cell event log. Workbook Forge owns compute and conservative OOXML; a later `FORGE_EDGE` may connect them. No fourth tree or duplicate model store.
- **Application binding:** a named input or output attached to an explicit cell; not an Excel defined-name expression.
- **Calculation snapshot:** an immutable model revision used by full or incremental calculation; stale work cannot replace current published results.
- **Preservation baseline:** immutable imported package content used to patch supported changes without rereading or rewriting the original file.
- **Python engine:** the hand-written Python expression, validation, graph-calculation and session implementation; default for Python APIs, with no native execution dependency.
- **Native backend:** optional Python interoperability bridge selecting the separately implemented Rust engine through PyO3. It is not the Python implementation.
- **Catalogued:** a formula feature or function has an entry with provenance and compatibility metadata.
- **Parsed:** the engine recognizes the construct and produces a typed representation.
- **Evaluated:** the engine calculates the construct for its documented input domain.
- **Conformance-tested:** shared cases verify the behavior in Python and Rust; separate Excel spot-check evidence is still needed for Microsoft compatibility claims.
- **Unsupported:** the evaluator refuses the operation with a typed reason; it does not approximate silently.
- **Table formula result range:** cells whose calculation logic is stored in a table part's calculated-column or totals-row metadata; workbook calculation refuses dependencies on these cells unless table semantics are explicitly supported.
- **Evaluator profile:** a deterministic implementation choice used where Excel documentation is incomplete or direct Excel evidence is absent; it must not be presented as verified Excel behavior.
- **Evaluator resource limits:** formulas are capped at 8,192 UTF-16 code units and 64 nested function calls, following documented Excel limits. Workbook Forge also caps parenthesis nesting at 96 and wildcard matching at 5,000,000 matching-state steps per evaluation; those two limits are local safety profiles.
- **Golden corpus:** language-neutral JSONL cases containing formula, workbook context, expected result or error, and compatibility profile.
- **ArrayValue:** a bounded, immutable rectangular formula result with explicit row and column shape; it does not place values into worksheet spill cells. Binary operators lift elementwise over equal shapes, with scalar-only broadcasting.
- **FILTER mask:** a one-dimensional row or column include vector whose length must match the selected source axis; two-dimensional masks are rejected by the current evaluator profile.
- **Binary comparison profile:** Binary operator errors propagate left to right, including non-finite numeric literal errors. ASCII text compares without case; if either text value contains non-ASCII characters, comparison is exact. Blank, Boolean, and numeric values compare after finite binary64 coercion; nonfinite values return `#NUM!`; mixed text/numeric equality is false, inequality is true, and relational comparison returns `#VALUE!`. These are Workbook Forge rules, not verified Excel collation or coercion behavior. Adjacent 16-digit literals are parity probes beyond Microsoft’s documented precision, not Excel result oracles.
- **FormulaResult / evaluate_result:** the shape-preserving result contract shared by Python and Rust; the scalar `evaluate` APIs keep a local `#VALUE!` boundary for top-level arrays and ranges.
- **Gate:** `tools/gate.sh`, the one command that decides whether a commit is acceptable. It rebuilds the native bridge, runs both test suites and the linters, fails on a skipped test, and stores its log under the commit it tested.
- **Review packet:** a plain copy of the files one reviewer may see, with no version history and no status claims. It is built by `tools/review/build_packet.py`.
- **Lens:** the single question a reviewer is asked, and the list of files that question needs. Listed in `tools/review/lenses.json`.
- **Planted defect:** a small deliberate fault used to find out whether the tests, or a reviewer, would notice a real one. A planted defect that the tests miss is a gap in the tests.
- **Boundary allowance:** the two binary64 values either side of a rounding boundary within which a number is taken as lying on the boundary.
- **Finding:** one defect claim with a location, a command that shows it, and the command's output. It is confirmed only when an independent refuter fails to knock it down and the coordinator reproduces it on unchanged code.
## Boundaries
- Target: Excel for Microsoft 365 desktop, with availability/version metadata retained.
- Languages: independent hand-written Python and Rust implementations of formula primitives, workbook models, validation, dependency calculation, editing sessions and OOXML adapters. Shared fixtures compare behavior; neither engine calls the other. The optional native bridge is explicit interoperability.
- Primary workbook format: `.xlsx`; other formats require separately audited adapters.
- Third-party code and dependencies: permissive open-source licenses only, with no copyleft, so the project stays enterprise-friendly. The rule covers development and transitive dependencies; `README.md` lists the accepted licenses and `python/tests/test_project_policy.py` enforces them.
- Jev is a development-time reviewer only. It is not linked into runtime, and its results are advisory.
- Macros and external data are never executed or fetched.
## Current State
The [current-state findings](docs/toolkit-delivery.md#current-state-findings) separate delivered capabilities from evidence limits. Native named-input compositions and cell-based workbook expressions remain separate authoring interfaces; the canonical binding path is on `main` and is not yet the general export API.
The native primitive interface adds nine functions and ten binary operators, typed native values, named-input composition and inspectable operation identities without requiring a workbook. Both packages provide the canonical catalog and expression schema. All three PR boundary findings are fixed; final independent review also closed a direct-call size-limit mismatch. Contributor evidence and the outstanding Excel acceptance gate are recorded in `docs/toolkit-delivery.md`.
At the accepted toolkit checkpoint, coverage was 115 implemented functions, 85 detailed semantic specs, 110 source records, and 1,371 shared fixtures; its complete suite recorded 923 Python and 88 Rust tests. The 521-entry source inventory is broader than implemented coverage, with 406 catalog-only functions. These checkpoint counts describe the toolkit baseline, not Excel-wide compatibility. FILTER, SORT, and UNIQUE return bounded, shape-preserving arrays; worksheet spill projection remains unsupported.

The versioned canonical workbook schema, Python and Rust hydration, the bound operating-scenario calculation and its shared fixtures are on `main`: `schemas/workbook-model.v1.schema.json`, `python/workbook_forge/model.py`, `rust/src/model.rs` and `fixtures/operating-scenario.workbook.json`. Both engines read the same bytes and calculate with their own evaluators. `workbook_forge.intake` reads an `.xlsx` file into that model, and `workbook-forge intake` prints it, or with `--summary` its versions and counts. `impl/v1-intake` and `impl/v1-calc-binding` hold an earlier shape of the model. Their intake is ported. Their typed binding constraints and calculation session are not.

The Excel round-trip harness is `tools/excel_oracle.py`, with `tools/canonical_excel_receipt.py` for the canonical fixture. The committed receipts under `receipts/canonical-operating-scenario/` are preflight only: they record that Excel was not invoked and that Spec 2 is not passed. `docs/excel-observations.md` describes one run of Microsoft Excel 16.113.2 on the older SDK scenario in which all 12 declared checks matched; no receipt for that run is committed. The full red-flag contract is blocked before Excel opens because export cannot place dynamic-array spill cells, and the volatile, iteration, scalar-array and Excel-quirk classes are unobserved.
- The source-linked function inventory contains 521 records; it is a versioned discovery catalog, not an evaluator coverage claim.
- The Python and Rust evaluators implement the same bounded scalar, reference, operator, common-function, conditional-aggregation, and rectangular formula-result slice. Both load `fixtures/formula-cases.jsonl`. The toolkit adds reviewed Serde dependencies and a separate PyO3 bridge; exact license receipts cover both lockfiles.
- `catalog/formulas.json` records per-language status; a function is `conformance-tested` only when shared fixture coverage passes in both engines.
- The Python wheel installs the JSON catalogs under `share/workbook_forge/catalog`; the API resolves either installed data or the source-tree catalog. `catalog/open_source_patterns.json` records only permissively licensed (no copyleft) implementation references, license evidence, and adoption decisions; no third-party source code is copied.
## Where things are
- `docs/primitives.md`: native function calls, inspectable compositions, package discovery and explicit behavior limits.
- `docs/toolkit-delivery.md`: architecture, ownership, milestone checklist, acceptance model and implementation evidence.
- `docs/product-specifications.md`: product outcomes, intake/compiler/agent/formula-hypothesis specifications, research questions and executable dependency order.
- `docs/extraction-patterns.md`: independent XML parsing/extraction contract, limits, formula mappings and source provenance.
- `docs/agent-protocol.md`: the versioned operation, pagination, error, provenance and JSON-lines transport contract; catalog/agent-operations.json owns its discoverable schemas.
- `docs/excel-observations.md`: observation meanings, harness usage and actual Excel evidence.
- `docs/run-history.md`: dated project activity, superseded checkpoint decisions, Runs 1–24 and the pre-loop baseline. Historical counts and repository visibility describe their original checkpoints.
- `docs/behavior-profiles.md`: current behavior and profile notes by function family, plus workbook adapter details.
- `python/workbook_forge/intake.py`: reads an `.xlsx` file into the canonical model; `workbook-forge intake` is its command.
- `docs/review-protocol.md`: how to review this project so that the reviewer does not inherit the builder's view; `tools/review/` enforces it.
- `tools/gate.sh`: the gate.
- `docs/vision.md`: the North Star and the v1 spine order.
- `docs/specs/`: red-flag specs, model versioning rules and the model changelog. `schemas/workbook-model.v1.schema.json` is the canonical schema.
- `receipts/`: committed Excel round-trip receipts. `tools/review/findings.schema.json`: the shape of a review finding.
- `docs/history/`: superseded branch briefs, dated notes and Jev advisory receipts, kept for the record and not current guidance.
- `.autoresearch/state.json`: the authoritative accepted-run ledger. `.autoresearch/config.json` holds the loop criteria; its `eval_command` is `tools/gate.sh`.
## Working conventions
- Keep public examples and records portable: use repository-relative paths or documented environment variables, synthetic data, and project-focused decisions. Do not copy home-directory paths, personal conversations, private contact details or local tool credentials into tracked files.
- Preserve license and source attribution. Before publishing a privacy cleanup, inspect reachable Git history and commit metadata as well as current files; an ordinary cleanup commit does not erase earlier versions.
- Checkpoint accepted work with local git commits. The ignore rules exclude `*.bak-*`, `_archive-*/`, and `.autoresearch/backups/`, so ad-hoc backup copies are no longer needed.
- Append each run's summary to `docs/run-history.md`. Keep `README.md` and this file limited to the current state.
- The Jev critic reads its key from `TYPESAFE_API_KEY`, a `.env` file, or the command in `TYPESAFE_KEY_COMMAND`; tracked files name no secret store and no directory.
- Run the Rust gates with `CARGO_TARGET_DIR` inside the checkout, as `eval_command` does. A shared target directory can mix build artifacts between copies of the crate.
## Latest maintenance
Dated entries live in `docs/run-history.md` under Project activity; this file keeps only the current state.
