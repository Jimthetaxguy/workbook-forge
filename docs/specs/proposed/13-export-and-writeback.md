---
type: technical-specification
status: proposed — needs James's decision G-C
base: main 41f28d3
summary: Name the five ways a session's state can leave Workbook Forge (a new workbook, a modified copy of the imported workbook, downloaded bytes, an explicit update of the original file, a live Excel application) and give each its requirements, product and reported identity. Add an export plan the caller can read and decline before any byte is written. Specify the original-file update, which both patch writers refuse today, as its own operation: target and source identity, change plan, compatibility test (the strict fingerprint or the weaker untouched-parts test, both presented for G-C), staging beside the target, apply under destination-appropriate protection, retained recovery, and a report naming the new identity, including what happens when the process dies mid-way. Refusal codes and evidence in both languages. No version field moves.
---

# 13 — Export and writeback

## Problem

A session's state leaves the process today by four paths that grew separately and refuse different things. Both patch writers refuse to overwrite the source (`python/workbook_forge/xlsx.py:1101-1102`; `rust/src/xlsx.rs:1955-1967`) and refuse any existing destination (`xlsx.py:1103-1104`; `xlsx.rs:465-467`); the canonical writer refuses an existing destination and has no source to protect (`python/workbook_forge/canonical_xlsx.py:112-113`); the browser returns bytes and never sees a path (`rust/src/wasm.rs:4,216-219`); both agents write one new file into a host directory (`python/workbook_forge/agent.py:341-348`; `rust/src/agent.rs:271-280`). The preservation baseline is defined as content "used to patch supported changes without rereading or rewriting the original file" (`CONTEXT.md:66`). This proposal takes as its premise that a caller may also ask for an explicit update of the original file, and later for a live Excel adapter, and that continuous synchronisation is not required. Original-file update is therefore a new operation with its own contract, not a refusal to remove: every path that refuses today keeps its refusal.

Two things are missing for that operation. First, a definition of when the original is still the file the session imported: spec 10 supplies the identity (`source.fingerprint`) and leaves the test to James (its open question "same baseline"); the conflict rule, staging and recovery are gate G-C (`CONTEXT.md:33`; `docs/specs/proposed/README.md`). Second, a plan a caller can read before publication. Today a blocked-cell edit is discovered while patching (`xlsx.py:1002-1005`; `xlsx.rs:2940-2942`), structural refusals come first-hit (`xlsx.py:979-989`; `xlsx.rs:2865-2873`), and both writers set full recalculation on open and remove the calc chain on every export, edits or none (`python/workbook_forge/workbook.py:1954-1976`; `xlsx.rs:2490-2545,2968`), which is why five parts are allowed to change in the corpus check (`tools/corpus_check.py:72-78`). The agent reports `{filename, revision, bytes}` and no fingerprint (`agent.py:357`; `agent.rs:297`); only the browser log and the canonical receipt report a `sha256` of what was written (`rust/src/event_log.rs:314-320`; `python/workbook_forge/canonical_roundtrip.py:143`).

## Outcome

- Every destination intention has one contract: what it needs from the session and from the preservation envelope, what it produces, and the `sha256:` identity it reports.
- A caller can ask for the plan of an export, read what will be preserved byte for byte, rewritten, refused and recalculated on open, and decline before anything is written.
- The original file can be updated on explicit request, only when it is still the file the session imported, with the previous bytes retained until the caller releases them, and with a report that states which protection the destination offered.
- Downloaded bytes and the live Excel adapter sit on the same map, so neither is specified by accident later.

## Scope

- The five intentions and their table (Design 1); export planning (Design 2); the original-file update contract with its race analysis and crash behaviour (Design 3); the browser's downloaded bytes against writeback (Design 4); the live Excel adapter's destination contract and the harness rules it keeps (Design 5); refusal codes and the report shape (Design 6); evidence (Design 7).
- Library and CLI surfaces in both languages. A CLI verb for the plan and the update is proposed under `workbook-forge` beside `scenario` and `calculate --xlsx`; its name is not decided here.

## Non-goals

- Continuous synchronisation, file watching, or merging a destination that changed.
- Removing the source-overwrite refusal from intentions 1 to 3.
- Implementing the live Excel adapter. Design 5 is its destination contract only; nothing in it runs Excel.
- A portable serialization of the envelope (spec 10 Design 4 leaves the container open); this spec names what writeback needs from it.
- Any `schema_version` or `model_version` change; any change to the agent protocol wire format or the browser command API (G-D).

## Design

### 1. Five destination intentions

| Intention | Needs from the session | Needs from the envelope (spec 10 Design 1) | Needs from the destination | Produces | Identity reported | Today |
| --- | --- | --- | --- | --- | --- | --- |
| **1. New workbook** (fresh package) | a committed revision whose calculation is current; the canonical writer calculates itself (`canonical_xlsx.py:100`); the Python toolkit with no baseline writes `_fresh_parts` after the same stale, revision and diagnostic checks as intention 2 (`xlsx.py:1107-1136,1158-1160`); Rust authored sessions use `export_xlsx` (`agent.rs:290`) | none; a semantic-only artifact (spec 10 Design 4) | a path ending `.xlsx` that does not exist (`canonical_xlsx.py:110-113`; `xlsx.rs:458-467`) | a new file | the canonical receipt records `exported_sha256` (`canonical_roundtrip.py:143`); `export_canonical` returns only a path (`:132`) | strict evidence gate is opt-in (`canonical_xlsx.py:98-99,426-457`); array spill refused before any file (`:101-108`); diagnostics stay as formula text without a cache (`:83-85`), so this writer does not refuse diagnostics while the toolkit writers do |
| **2. Modified copy of the imported workbook** (patch export) | a committed revision; no pending edits (agent `expected_revision` required, `agent.py:340`; `agent.rs:269`); results not stale: `stale`, `revision` and `model_digest` must match (`xlsx.py:1107-1112`; `xlsx.rs:1929-1932`) and are re-checked after patching (`xlsx.py:1183-1184`; `xlsx.rs:1943-1945`); no diagnostics (`xlsx.py:1132-1136`; `xlsx.rs:2021-2029` via `:2864`) | everything but `inventory` (spec 10 Design 2 "Patch export"): `baseline.parts` and `baseline.container` seed the staged package (`xlsx.py:1161-1165`; `xlsx.rs:1933`), `baseline.document` is the diff base (`xlsx.py:1170`; `xlsx.rs:1936`), `merged`, `guards`, `text`; `source.handle` only for the refusal | a new `.xlsx` path that is not the source (`xlsx.py:1099-1104`; `xlsx.rs:458-467,1955-1967`); agent: one safe filename in the host directory, no symlink (`agent.py:32,341-348`; `agent.rs:271-280,688-697`; `docs/agent-protocol.md:134-139`) | a new file, hard-linked from a staged file (`xlsx.py:1185`; `xlsx.rs:483`) | the path (`xlsx.py:1186`; `xlsx.rs:1969`); agent `{filename, revision, bytes}` (`agent.py:357`; `agent.rs:297`); no fingerprint | calc chain removed and `calcMode=auto`, `fullCalcOnLoad=1`, `forceFullCalc=1` set unconditionally (`workbook.py:1954-1976`; `xlsx.rs:2490-2545,2968`), so `MAY_CHANGE` parts differ even without edits |
| **3. Downloaded bytes** (browser) | the current revision; `export_bytes` runs `export_package`'s checks (`xlsx.rs:1949-1951`); authored sessions use `export_xlsx_bytes` (`wasm.rs:55-57`) | as intention 2 when imported: `import_xlsx_bytes` keeps everything except `source`, which is `None` (`xlsx.rs:1481-1487`); none for a blank workbook | none; the browser owns file selection and saving (`wasm.rs:4`); the demo offers a download named `forge-export.xlsx` (`web/univer-spike/src/main.ts:130-132`) | bytes; with `includeLog`, the NDJSON log and its name (`web/forge-engine/src/engine.ts:290-293,357-369`) | `session.exported.xlsx_sha256` in the log (`engine.ts:365-366`; `event_log.rs:314-320`); nothing without `includeLog` (`engine.ts:364`) | no overwrite semantics exist in Forge for this path; the host's save decides |
| **4. Explicit update of the original file** (new) | as intention 2, plus the caller's explicit `update_original` intent carried with `expected_revision` | as intention 2 plus `source.handle` (session-only; today `_Baseline.source`, `xlsx.py:93`; `ImportedWorkbook.source`, `xlsx.rs:1417`) and `source.fingerprint` (not stored by either importer today; `content_fingerprint`, `python/workbook_forge/model.py:293-297`; `rust/src/model.rs:1636`) | the file at `source.handle` exists; its current bytes pass the compatibility test (Design 3); its directory accepts a staged sibling and a recovery generation; the adapter states which protection it offers | the updated file, a retained recovery generation, and a new envelope derived from the written bytes (spec 10 Design 2) | `sha256:` of the written bytes, re-read from the destination, and of the recovery generation | refused on both patch paths: `refusing to overwrite the source workbook` / `refusing source overwrite`, classified `output_exists` (`catalog/refusal-codes.json:18,144-147`); pinned at the `Workbook.save_as` layer (`python/tests/test_workbook.py:166-169`) and by classification (`python/tests/test_corpus.py:129`); no test calls `export_xlsx` with the source path and no Rust test pins `:1966` |
| **5. Live Excel application** (later, separate destination) | as intention 4 | `source.fingerprint`, `guards`, `inventory` (spec 10 Design 2 "Live Excel adapter") | Excel Desktop with the workbook open under the expected name and path, named by the caller; the harness rules in Design 5 | an application state and, on save, a saved file | the saved file's `sha256`, which is what a receipt pins (`tools/excel_oracle.py:1487,1535`; `docs/evidence-grades.md:63-78`) | none; the only Excel driver opens its own scratch copy, never the caller's file (`excel_oracle.py:1423,1465`) |

Across all five, "committed revision" means the revision the caller named is the one published, "pending edits" are refused by the revision check rather than flushed, and "stale results" (`stale: true` in either engine's report, `rust/src/toolkit.rs:1209`; `python/workbook_forge/python_engine.py:516,723`) are refused with `revision_conflict`, never recalculated silently on the caller's behalf.

### 2. Export planning

`plan_export(session, envelope, intention, destination) -> ExportPlan`, in both languages: pure, writes nothing, runs every check the writers run and classifies every package part. The writers compute all of this in passing today; the plan makes it a value the caller can read and decline.

| Field | Meaning | Where it comes from |
| --- | --- | --- |
| `intention`, `destination{kind, handle?, identity_before?}` | which row of Design 1; the handle is never a path in a report (`canonical_roundtrip.py:302-308` `_display` is the rule) | caller |
| `revision`, `calc` | the pass being published (spec 09 section 10 item 1: revision plus calculation context) | the report's `revision` and `calc` |
| `preserved[]` | parts copied byte for byte | the complement of `rewritten` and `removed`; today the complement of `MAY_CHANGE` plus edited sheet parts (`corpus_check.py:72-78,152-161`) |
| `rewritten[{part, reason}]` | sheet parts with changed or inserted cells (Rust replaces a sheet part only when it has patches, `xlsx.rs:2962-2966`; Python writes through `Workbook`), `xl/workbook.xml` (`calcPr`), `[Content_Types].xml` and `xl/_rels/workbook.xml.rels` (calc chain), `xl/sharedStrings.xml` | `MAY_CHANGE` |
| `removed[]` | `xl/calcChain.xml` when present (`workbook.py:1954-1955`; `xlsx.rs:2490-2530`) | the writers |
| `refused[{code, where, message}]` | every refusal the write would raise, collected rather than first-hit: bindings and structure (`xlsx.py:979-989`; `xlsx.rs:2865-2873`), preserved-only cells, insertion bounds, styles, 1904 formulas, compatibility version 3, diagnostics, array spill | the writers' checks, or spec 10's single guard entry point once it exists |
| `recalc_on_open` | `true` today on every export; the plan reports it rather than deciding it | `workbook.py:1974-1976`; `xlsx.rs:2541-2545` |
| `volatile[{sheet, address, functions, rule}]` | cells whose cache will differ from Excel's at every open and is judged by the contract's `volatile_predicate` rule, not by value (`python/tests/test_export_regression.py:97-115`; `fixtures/excel-roundtrip-red-flags.contract.json:171,177`); a volatile function with no rule is listed `unruled` | `workbook_forge.volatile.VOLATILE_FUNCTIONS` and the contract |
| `inventory` | intention 1 only: what the fresh package will not carry | the envelope (spec 10 Design 1) |
| `compatibility{test, expected, observed, result}` | intentions 4 and 5: Design 3 step 3 | the destination's bytes |
| `protection{offered, required}` | intentions 4 and 5: Design 3 step 6 | the destination adapter |

A plan with any `refused` entry is complete and returned; the write operation refuses with the first code. Plans are advisory: the write re-runs every check against the revision it publishes, as both writers already re-check the revision after patching (`xlsx.py:1183-1184`; `xlsx.rs:1943-1945`). For intention 3 the plan is computed in the worker and returned with the bytes; it has no `destination` and no `protection`.

### 3. The original-file update contract

Eight steps. Each is a named phase written to a journal beside the staged file (`<name>.xlsx.forge-writeback.json`, created exclusively, never a machine path inside it) so a later run can finish or reverse.

1. **Identify target and source.** Target = `source.handle` resolved as the importer resolved it (`Path.resolve()`, `workbook.py:694`; `fs::canonicalize`, `xlsx.rs:1472`). Source identity = `source.fingerprint`. A bytes import has no handle (`xlsx.rs:1486`): `writeback_unsupported`. The session revision must equal the caller's `expected_revision`: otherwise `revision_conflict`. An envelope whose fingerprint disagrees with the document's `metadata.source.fingerprint` is `envelope_mismatch` (spec 10 Design 5).
2. **Compute the change plan.** Design 2 for intention 4. Any `refused` entry stops here, before the destination is read.
3. **Establish target compatibility.** Read the destination's bytes once and hash them. Two tests, both presented for G-C:
   - **Strict:** `content_fingerprint(destination bytes) == source.fingerprint`. This is the test the browser log already records and enforces (`event_log.rs:46-58,612-630`). Any other change, even to a sheet the plan does not touch, is `destination_changed`; the caller re-imports and replays its edits.
   - **Weaker** (spec 10's alternative): decode the destination with the importer; every part the plan lists as `preserved` must equal the baseline's bytes for that part; `rewritten` and `removed` parts may differ. A concurrent edit to an untouched sheet passes; one to a sheet the plan rewrites is `destination_changed`. Two costs: a second full parse, and a hole: `[Content_Types].xml`, the workbook part and its `.rels` are always `rewritten`, so a sheet another writer added would pass the test and be dropped by the apply. The weaker test must therefore also require the sheet-entry list and the part set to be unchanged, and even then it lets the other writer's change to an untouched sheet stand while this session's `baseline.document` no longer describes the file.
   Recommendation: strict, as spec 10 recommends. If James chooses the weaker test, the part-set clause is mandatory and the report must name the parts that differed and were kept.
4. **Stage beside the target.** Build the package from the baseline exactly as `export_xlsx` and `export_package` do (`xlsx.py:1155-1180`; `xlsx.rs:1933-1942`), encode, re-open the staged bytes (`xlsx.py:1181-1182`; `xlsx.rs:491-495`), write `.<name>.forge-stage-<pid>-<n>.xlsx` in the target's directory with exclusive creation (the scheme `publish` uses, `xlsx.rs:471-479`), `fsync`.
5. **Retain recovery before apply.** Hard-link the target to `<name>.xlsx.forge-recovery-<first 8 hex of fingerprint>`; the link shares the target's inode, so a concurrent in-place writer changes both. Hash the link; it must still equal step 3's hash. Where hard links are refused, copy and hash the copy. If recovery cannot be retained, stop with `destination_conflict`: the update never runs without it.
6. **Apply with destination-appropriate protection**, strongest first:
   - **exchange:** an atomic swap of staged and target where the platform offers one (an exchange flag on rename, available on current Linux and macOS filesystems). After the swap the previous target sits at the stage path; hash it: equal to step 3 means commit; unequal means swap back and report `destination_changed`. This closes both races below.
   - **lock:** an exclusive lock on the target that the destination's other writers honour, held from step 3 through step 7, then `rename(staged, target)`. Only as strong as the other writers' cooperation; a spreadsheet application's own lock file is not that lock.
   - **hard-link-and-rename:** step 5's link, `rename(staged, target)`, then re-hash the recovery link. An in-place writer is detected (the shared inode changed): `rename(recovery, target)` restores their bytes and the report says `destination_changed`. A replace-writer is **not** detected: one that wrote a new inode and renamed it over the target between step 3 and the rename has been overwritten, and the recovery link still hashes equal. This is the race a check-then-write does not close; closing it is what the destination adapter must provide, as `exchange` or `lock`. The journal records `window: replace_writer_unclosed` when neither is offered.
   - **refuse:** without `exchange` or `lock` the operation refuses with `destination_conflict`, unless James decides on an explicit opt-in (`accept_unclosed_window: true`) under which hard-link-and-rename runs and the report states the window (open question 3). Where the filesystem offers no atomic rename over an existing file, the adapter says so and the operation refuses with `writeback_unsupported`; it never copies over the target.
   The existing create-new paths need none of this: `os.link` and `fs::hard_link` fail when the target exists (`xlsx.py:1185`; `xlsx.rs:483`), so the advisory `exists()` checks (`xlsx.py:1103`; `xlsx.rs:465`) are already backed by an atomic refusal.
7. **Report outcome and new identity.** Design 6 shape: identity before, identity after (`sha256:` of the bytes re-read from the destination), the recovery handle and its hash, the protection used and any window, the plan. The session's envelope is replaced by one derived from the written bytes with the current document as `baseline.document` (spec 10 Design 2); the revision does not move, as agent export leaves it today (`docs/agent-protocol.md:139`).
8. **Release recovery.** `release_recovery(handle)` deletes the generation on the caller's word; or a bounded `generations: N` policy deletes the oldest when the N+1th is retained. The update itself never deletes a generation and never deletes on failure (open question 2).

**If the process dies between stage and apply.** After steps 4 and 5 the target is untouched; a stage file, a recovery link and a journal remain. Under `exchange`, a crash in step 6 leaves either the swap done (the journal says `applying`, the stage path holds the previous bytes; the next run hashes them and either completes the report or swaps back) or not done. Under rename, a crash after the rename but before the journal's `applied` entry leaves the target updated and the recovery link valid; the next run hashes target and recovery and completes. A stage file with no journal, or a journal whose recorded stage hash does not match the stage file, is listed by a `--recover` command and left in place; it is never deleted silently. Today's writers leave a `.workbook-forge-*` directory or `.tmp` file after a crash (`xlsx.py:1155`; `xlsx.rs:471-487`) and nothing resumes or lists them.

### 4. Downloaded bytes differ from writeback

The browser has no intention 4. The host owns the file (`wasm.rs:4`), the engine returns bytes (`engine.ts:364`), and the demo hands them to a download whose name the page chooses (`main.ts:130-134`). What ties the bytes to a revision is the log: `export {includeLog:true}` appends `session.exported{revision, xlsx_sha256}` and returns `{bytes, log, logName}` (`engine.ts:365-368`; `event_log.rs:314-327`; pinned by `web/forge-engine/test/engine.node.test.mjs:205-212` and `rust/tests/event_log.rs:474-483`). The baseline is identified the same way, `session.opened.baseline{sha256, bytes}` (`event_log.rs:46-58`; set at `wasm.rs:228`), and `replay` refuses other bytes with `baseline_mismatch` (`event_log.rs:612-630`). The browser therefore already has the strict compatibility test and the output identity, with no destination at all: a host that later wants intention 4 from a downloaded artifact must present the file and the log together, and the compatibility test is `sha256(file) == session.opened.baseline.sha256`. Without `includeLog` the bytes carry no revision and no identity (`engine.ts:364`). This spec asks the demo to default `includeLog` on and offer the log beside the download under `logName`, so every downloaded artifact is traceable to the revision that produced it; the engine and protocol do not change.

### 5. The live Excel adapter as a destination

Excel holds a copy of the workbook in memory, so "the destination's current bytes" are two things: the saved file and the open document. The adapter would:

- **Inspect:** whether a workbook of the expected name is open and at the expected path (`exists workbook`, `full name`, `excel_oracle.py:1139-1144`); whether any other workbook is open (`:1130`); the global calculation and iteration settings, read-only, with the preflight that refuses when they are unobservable or differ (`:1131-1134,1120-1125`); whether the open document has unsaved changes.
- **Compatibility:** the saved file passes Design 3 step 3 **and** the open document has no unsaved changes; otherwise `destination_changed`. The user's unsaved edits are a concurrent writer.
- **Apply:** ask Excel to set the cells the plan rewrites, as the harness applies declared edits (`:1145`); never patch the file under an open workbook; ask for the full rebuild (`:1146-1147`) instead of relying on `fullCalcOnLoad`; save in place with `save targetBook`, the one save form that works after a rebuild without a dialog (`:247-257`).
- **Request:** nothing it cannot observe; a setting it needs and cannot read is a `blocked` outcome, not an assumption (`docs/excel-observations.md:81-84`).
- **Reconcile:** re-read the saved bytes, reimport them with the toolkit importer (`_model_view`, `:926`), compare values to the session's calculation by the contract's tolerances and volatile rules, record `saved_workbook_sha256` as the new identity (`:1487,1535`), and replace the envelope.

It inherits the writeback conflict model because Excel is a writer that honours none of our locks and replaces the file on save: only compatibility-before and identity-after are available, the report must say `protection: verify_after`, and reconciliation is the check. The harness rules it must keep: never change global calculation, iteration or security settings (`excel_oracle.py:1120-1125`; `docs/excel-observations.md:40-41,77-78`); never open, close or save a workbook the caller did not name, and refuse while another workbook is open because the full rebuild is application-wide (`excel_oracle.py:1130`; `docs/excel-observations.md:72-75`); no cleanup commands after an uncertain failure (`docs/excel-observations.md:43-45`); scratch copies, not the caller's file, for observations (`excel_oracle.py:1465`; `docs/excel-observations.md:50`); receipts sanitised before sharing (`docs/excel-observations.md:256-264`; `tools/sanitize_receipt.py`). "Explicit intent" means the caller names the open workbook as the destination of intention 5; for every other operation the harness's refusal of any open workbook stays the default. No claim about this destination is made until spec 01's receipt exists.

### 6. Refusal codes and the report shape

| Code | Status | When | Agent surface |
| --- | --- | --- | --- |
| `output_exists` | existing (`catalog/refusal-codes.json:18,144-147`) | intentions 1 to 3: the path exists or is the source | `export_error` with the code in the message, as today (`agent.py:227-228`; `agent.rs:279,293`; `docs/agent-protocol.md:29-35`) |
| `revision_conflict` | existing (`:19,151-158`) | `expected_revision` mismatch; stale report; model changed during export | `revision_conflict` |
| `destination_changed` | new | the compatibility test fails before apply, or post-apply verification finds other bytes and the apply was reversed | message prefix under `export_error` |
| `destination_conflict` | new | the target is held by another writer; the exchange reports busy; the adapter offers neither `exchange` nor `lock` and no opt-in was given; recovery cannot be retained | message prefix |
| `writeback_unsupported` | new | no `source.handle` (bytes import, browser); no atomic rename over an existing file; intention 4 asked of a semantic-only artifact | message prefix |
| `envelope_mismatch` | spec 10 Design 5 | the envelope's fingerprint differs from the document's `metadata.source.fingerprint` | message prefix |

The three new codes enter `catalog/refusal-codes.json` with phrase rules so `WorkbookError.code` and `XlsxError::code()` agree (`python/tests/test_corpus.py:112-114,123-135`), and `docs/refusal-codes.md` gains their rows. Both agents keep `export_error` as the envelope code and carry the typed code in the message, as they do for `pivot_output_protected` and `validation_failed` (`docs/agent-protocol.md:124-128`).

Report: `{intention, revision, calc, destination{kind, handle?, identity_before, identity_after}, plan, protection{offered, used, window?}, recovery{handle, sha256, retained_until | generations}, result{bytes, sha256}, refusals[]}`. Intentions 1 to 3 report `identity_after`, `result` and `plan`. The agent `export` result is `{filename, revision, bytes}` with `additionalProperties: false` (`catalog/agent-operations.json:1877-1882`); adding `sha256` to it, or a `plan_export` operation beside the nine (`CONTEXT.md:49`), changes the catalog and is G-D (open question 4). Library and CLI surfaces carry the full report first.

### 7. Evidence

- A destination whose bytes differ from the fingerprint is refused `destination_changed`; afterwards the destination is byte-identical to before and no stage file, recovery link or journal remains.
- A matching destination is updated; the recovery generation hashes to the fingerprint; every part outside `MAY_CHANGE` and the edited sheet parts is byte-identical to the baseline (`corpus_check.preservation_findings`, `:152-161`); reimport of the written file gives the session's values.
- Under hard-link-and-rename an injected in-place writer is detected and reversed; under exchange an injected replace-writer is detected and reversed; under plain rename the replace-writer's bytes are lost and the test asserts exactly that, so the unclosed window is a measured fact rather than a sentence.
- Intentions 1 to 3 are unchanged: `python/tests/test_toolkit_xlsx.py:63-68`, `test_workbook.py:158-169`, `test_absent_cell_export.py`, `test_agent_python.py:191-201`, `rust/tests/absent_cells.rs`, `rust/tests/corpus.rs:42-64` and `engine.node.test.mjs:205-212` pass unchanged.
- The regression link runs on the written file: copy `receipts/canonical-operating-scenario/operating-scenario.exported.xlsx`, import it with `import_xlsx`, update it in place with no edits, and `semantic_differences(read_cells(committed), read_cells(written), volatile_rules=rules)` is empty (`test_export_regression.py:70-74,143-208,238-242`); with `unit_price` 20 to 25 the written caches equal `calculate` on the changed fixture, as the receipt's `after_input_change_preview` lane computes (`canonical_roundtrip.py:99-106`).
- A crash injected after stage, after recovery and after rename each leaves a journal from which `--recover` completes or reverses; the target is never a partial file.
- No report, plan, journal or receipt contains a machine path; a test imports under a directory with a private-looking name and asserts the name is absent from every artifact (`test_canonical_receipt_safety.py` is the pattern).

## Schema / model-version impact

None to the workbook model. The plan and report are new documents, `schemas/export-plan.v1.schema.json` and `schemas/writeback-report.v1.schema.json`, beside `schemas/calculation-report.v1.schema.json`, as spec 01 proposed for the receipt. `source.fingerprint` is computed at import and held in memory (spec 10 Design 1); writing it into the document as a required field is G-A (spec 09). The agent catalog and the browser command API do not change in this spec.

## Acceptance tests

```python
# python/tests/test_export_writeback.py
def test_plan_lists_preserved_rewritten_removed_and_refused_without_writing(tmp_path): ...
    # plan parts == MAY_CHANGE ∪ edited sheet parts; a blocked-cell edit appears in refused; no file created
def test_plan_names_volatile_cells_and_their_contract_rule(): ...          # Forecast!J1 → NOW → number rule
def test_writeback_refuses_bytes_import_as_unsupported(): ...
def test_writeback_refuses_changed_destination_and_leaves_it_untouched(tmp_path): ...   # destination_changed
def test_writeback_updates_matching_destination_and_retains_recovery(tmp_path): ...
    # recovery sha256 == fingerprint; preservation_findings(written) == []; reimport values match
def test_writeback_requires_expected_revision_and_refuses_stale(tmp_path): ...          # revision_conflict
def test_writeback_refuses_without_recovery(tmp_path, monkeypatch): ...                 # destination_conflict
def test_hard_link_rename_detects_in_place_writer_and_reverses(tmp_path): ...
def test_plain_rename_loses_replace_writer_and_reports_unclosed_window(tmp_path): ...
def test_exchange_detects_replace_writer_and_reverses(tmp_path): ...   # skipped with reason where no exchange
def test_crash_after_stage_recovery_and_rename_each_recover(tmp_path, monkeypatch): ...
def test_report_identity_after_equals_sha256_of_destination_bytes(tmp_path): ...
def test_written_file_passes_export_regression_link(tmp_path): ...     # semantic_differences == []
def test_modified_copy_and_fresh_export_paths_are_unchanged(tmp_path): ...   # output_exists on source and on existing
def test_no_artifact_contains_a_machine_path(tmp_path): ...
def test_new_refusal_codes_are_catalogued_and_documented(): ...
```

```rust
// rust/tests/export_writeback.rs
fn plan_lists_preserved_rewritten_removed_and_refused_without_writing() {}
fn plan_names_volatile_cells_and_their_contract_rule() {}
fn writeback_refuses_bytes_import_as_unsupported() {}
fn writeback_refuses_changed_destination_and_leaves_it_untouched() {}
fn writeback_updates_matching_destination_and_retains_recovery() {}
fn writeback_requires_expected_revision_and_refuses_stale() {}
fn hard_link_rename_detects_in_place_writer_and_reverses() {}
fn plain_rename_loses_replace_writer_and_reports_unclosed_window() {}
fn exchange_detects_replace_writer_and_reverses() {}   // cfg-gated to platforms with an exchange primitive
fn crash_after_stage_recovery_and_rename_each_recover() {}
fn source_overwrite_refusal_is_pinned() {}             // closes the gap at xlsx.rs:1966
fn plan_and_report_agree_with_python_on_the_corpus() {}   // shared table tools/corpus/writeback-cases.json
```

```js
// web/forge-engine/test/engine.node.test.mjs (additions)
test('export without includeLog carries no identity; with includeLog the log tags the bytes', ...)
test('export result carries the plan with no destination and no protection', ...)
```

For this document alone: `python -m ruff check python tools examples setup.py` and `python -m pytest -q python/tests/test_project_policy.py python/tests/test_corpus.py::test_capabilities_document_is_current`.

## Dependencies

- Spec 08: section 3 (the export rows of the operations table), section 4a (the patch and fresh-package edges and their tests), section 6 (the browser row: bytes only without `includeLog`).
- Spec 09: section 4 (what is "not in the document": package baseline, the envelope); section 10 items 1, 2 and 7 (pass identity, compare-and-swap publication, `changes_since`), which define the "committed revision and pass" the plan names.
- Spec 10: Design 1 (`source.fingerprint`, `source.handle`, `baseline.parts`, `baseline.container`), Design 2 (the writeback and live-adapter rows; envelope replacement after a successful writeback), Design 3 ("Export without edits"), Design 5 (`envelope_mismatch`), and its open question on the same baseline, which this spec presents as Design 3 step 3.
- `python/tests/test_export_regression.py` (merged): `read_cells`, `volatile_rules`, `semantic_differences` are reused as written.
- Spec 01: no claim about intention 5 before the scalar receipt is committed.

## Open questions for James

1. **G-C, same baseline:** strict fingerprint equality (recommended) or the weaker untouched-parts test with the mandatory part-set clause.
2. **G-C, recovery:** retain until the caller releases (recommended) or a bounded number of generations; location (a sibling `<name>.xlsx.forge-recovery-<hash8>` is proposed); who may delete.
3. **G-C, protection:** refuse when the destination offers neither `exchange` nor `lock` (recommended), or allow an explicit opt-in to hard-link-and-rename with the window reported.
4. **G-D:** whether the agent `export` result gains `sha256`, and whether planning and update become agent operations, both of which change the catalog hash. Whether the browser demo defaults `includeLog` on.
5. **Envelope replacement:** a successful writeback replaces the envelope with one derived from the written bytes (spec 10 Design 2). That re-pins any Excel observation taken on the previous bytes as stale; confirm that is acceptable.
6. **Intention 5:** whether the live Excel adapter's destination contract is decided under G-C now or waits for spec 01's receipt and its own gate.
