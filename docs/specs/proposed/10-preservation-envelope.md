---
type: technical-specification
status: proposed
base: main 41f28d3
summary: Name the companion that preserves what the workbook model does not represent, built from what import_xlsx already holds in both languages. Contents, readers and writers, an invalidation rule per transformation class with today's refusals as the floor, two artifact kinds and their cross-language handoff, and the join with provenance and metadata so nothing is stored twice. In-memory first; the portable form stays open. No version field moves.
---

# 10 — Preservation envelope

## Problem

An imported workbook carries far more than cells. Today both importers keep that
material and guard edits against it, but the thing they keep has no name and no
contract. Python holds it as seven private attributes on the toolkit wrapper
(`python/workbook_forge/toolkit.py:98-109`) plus a frozen `_Baseline`
(`python/workbook_forge/xlsx.py:91-103`); Rust holds it as the fields of
`ImportedWorkbook` beside the session (`rust/src/xlsx.rs:1416-1435`); canonical intake
keeps none of it and instead lists what it dropped as `metadata.intake.not_carried`
(`python/workbook_forge/intake.py:70,118-199`). The two importers already disagree on
what they keep (section 1: zip container metadata, the compatibility version as an
integer or a flag, a configurable or fixed insertion bound).

Because the companion is implicit, its readers are inconsistent within one language.
`WorkbookModel.apply` runs the 1904, compatibility, text and feature guards
(`toolkit.py:123-148`), but `WorkbookModel.set_inputs` goes to the engine session
directly (`toolkit.py:231-233`) and runs none of them. The agent's detached copy
carries the baseline and import metadata only (`python/workbook_forge/agent.py:199-211`),
so the agent's `export` (`agent.py:354`) reaches `_patch_import` with `guard=None`
(`xlsx.py:1168-1176`) and the export-time re-checks return early
(`xlsx.py:927-928,948`). A snapshot handoff re-attaches five attributes by hand
(`xlsx.py:1078-1082`). Spec 08 section 6 showed that every serialized form drops the
companion; spec 09 section 4 names "the preservation envelope" as the owner of what is
not in the document and defers its rules here. The vision's contracts C1 and C4 ask for
exactly this: a linked envelope with declared invalidation per edit.

## Outcome

- One named type, `PreservationEnvelope`, in both languages, assembled by import from
  what the importers already compute, with the same contents under the same names.
- Every edit, preview, export and writeback of an imported workbook consults it through
  one entry point; no path skips a guard another path runs.
- "Preserved" becomes a claim about an operation: each transformation class says whether
  preserved content stays valid, is reported stale or invalidated, or blocks the edit.
- The carry inventory is produced by the envelope and projected into the document, so a
  semantic-only consumer learns what a fresh export will lack.

## Scope

- The envelope's contents, readers, writers and invalidation rules (Design 1 to 3).
- The semantic-only and preservation-capable artifact kinds and the in-memory
  cross-language handoff (Design 4).
- The join with `provenance` and `metadata` (Design 5).
- Acceptance tests in both languages, including an inventory agreement test on the corpus.

## Non-goals

- A portable serialization of the envelope. Section 4 states what it must carry and
  leaves the container (sidecar, package part, JSON beside the document) open.
- Writeback to the original file (vision section 8, gate G-C). The envelope supplies the
  "same baseline" test; the conflict rule, staging and recovery are G-C's.
- Row or column insertion and deletion, range moves, sheet renames as operations. None
  is offered by either engine today (`docs/xlsx-features.md:86-87`); section 3 states
  what the envelope must report when they arrive, not how they are implemented.
- Pivot refresh, chart redraw, external data, macros. Preserved as bytes, as today.
- Any `schema_version` or `model_version` change; see the impact section.

## Design

### 1. Contents

Every field is a pure function of the imported package bytes, except the two marked
*session*. That property is what makes the in-memory handoff in section 4 possible.

| Field | Meaning | Python today | Rust today |
| --- | --- | --- | --- |
| `source.fingerprint` | `sha256:` of the imported bytes; the envelope's identity and its join key to the model (section 5) | not computed by `import_xlsx`; the canonical side has `content_fingerprint` and `require_fingerprint` (`python/workbook_forge/model.py:293-302`), the browser log has `Baseline{kind, name, sha256, bytes}` (`rust/src/event_log.rs:46-58`) | `model::content_fingerprint` (`rust/src/model.rs:1636`); not stored on `ImportedWorkbook` |
| `source.parts` | the package part names, in package order | `_import_metadata["package_parts"]` (`xlsx.py:339`) | `capabilities["parts"]` (`xlsx.rs:1344`) |
| `source.workbook_part`, `source.sheet_parts` | which part is the workbook and which part each sheet is | re-derived by `Workbook.open` on the staged seed (`xlsx.py:1166`) | `workbook_part`, `sheet_parts` (`xlsx.rs:1421-1422`) |
| `source.handle` (*session*, never serialized) | where the bytes came from, for the overwrite refusal only | `_Baseline.source: Path` (`xlsx.py:93`); refusal at `xlsx.py:1100-1102` and `workbook.py:1978-1981` | `source: Option<PathBuf>`, canonicalized (`xlsx.rs:1417,1472`), `None` for a bytes import (`xlsx.rs:1481-1488`); refusal at `xlsx.rs:1954-1967` |
| `baseline.parts` | the imported part bytes, including every `.rels` part | `_Baseline.parts` (`xlsx.py:94`) | `baseline: Package` (`xlsx.rs:1418`) |
| `baseline.container` | zip entry metadata and archive comment | `_Baseline.infos`, `comment` (`xlsx.py:95-96`) | not kept; `encode_checked` re-encodes (`xlsx.rs:491`) |
| `baseline.document` | the model as imported, the "before" every patch diffs against | `_Baseline.document_json` (`xlsx.py:97`) | `original: WorkbookModel` (`xlsx.rs:1419`) |
| `merged` | sheet name to merged ranges `(c1, r1, c2, r2)` | `_Baseline.merged` (`xlsx.py:99-101`) | `merged` (`xlsx.rs:1423-1425`) |
| `date_system` | `1900` or `1904` | `uses_1904_date_system` (`xlsx.py:98`; set at `workbook.py:1278`) | `date1904` (`xlsx.rs:1429,972-976`) |
| `compatibility_version` | the integer; 3 or later blocks formula edits | `compatibility_version: int` (`xlsx.py:102-103`; `workbook.py:1284`) | `compat3: bool` (`xlsx.rs:1430,978`); the integer is lost |
| `guards` | validation rules with `enforced`, pivot output bounds, charted sheets and chart series references | `FeatureGuard(validations, pivots, charts)` (`xlsx.py:540`; `python/workbook_forge/xlsx_features.py:647-653`) | `FeatureGuard{rules, pivots, charted}` (`rust/src/xlsx_features.rs:949-953`; `xlsx.rs:1432`) |
| `text.cells`, `text.layouts` | imported `@` cells; per-sheet row and column styles an inserted cell inherits | `_text_cells` (`xlsx.py:541-546`), `_text_layouts` and `_imported_addresses` (`xlsx.py:552-558`; `TextLayout`, `xlsx.py:228-256`) | `text_cells`, `text_layouts` (`xlsx.rs:1434-1435`); imported addresses read from `original` (`xlsx.rs:1888-1893`) |
| `insertion_bound` (*session*) | the largest row and column an absent cell may be inserted at | constants `_EXCEL_MAX_ROWS`, `_EXCEL_MAX_COLUMNS` (`xlsx.py:106-107`) | `insertion_bound`, default Excel's grid, narrowed by `with_insertion_bound` (`xlsx.rs:1428,1518,1723-1731`) |
| `inventory` | what the package holds that the model does not carry: `[{where, what, count, sheet?}]` | canonical only: `_not_carried` (`intake.py:118-199`), entry shape `intake.py:108-115`; the toolkit has prose lists `preserved_only` and `rejected` (`xlsx.py:326-336`) | prose lists in `capabilities` (`xlsx.rs:1344`) |

Not in the envelope: the defined-name table (`_names`, `toolkit.py:98-103`; spec 09
section 4 gives `metadata.defined_names` to the contract; the bytes of
`xl/workbook.xml` stay in `baseline.parts` and come back byte for byte,
`docs/defined-names.md:95-100`); `blocked_reason` (set on cells by import,
`xlsx.py:380-412`, owned by the contract); `revision` (the session). The inspect-facing
`xlsx` block (`toolkit.py:250-251`) and Rust `capabilities()` are projections of the
envelope, generated from it rather than kept beside it.

### 2. Who reads it, who may replace it

| Operation | Reads | Today |
| --- | --- | --- |
| Edit guard: value, formula or style into an existing or absent cell | `date_system`, `compatibility_version`, `guards`, `text`, `merged`, `insertion_bound`, `baseline.document` | Python `apply` (`toolkit.py:123-148`) and agent preflight (`agent.py:364-389`); Rust `apply` → `validate_edits` (`xlsx.rs:1864-1879,1764-1851`). Python `set_inputs` bypasses all of it (`toolkit.py:231-233`); Python `_check_insertion` runs at agent preflight and at export, not in `apply` |
| Formula result check under an enforced rule | `guards`, the names table | `check_formula_results` on a detached copy with the batch applied (`xlsx_features.py:723-764`; `xlsx.rs:1833-1851`) |
| Patch export to a new file or to bytes | everything but `inventory` | `export_xlsx` → `_patch_import` (`xlsx.py:1086-1186,970-1030`); `export_package` → `patch` → `recalc` (`xlsx.rs:1925-1944,2855-2968,2490`). Only changed sheet parts, `calcPr`, and the calcChain removal touch bytes (`tools/corpus_check.py:72-78`) |
| Snapshot handoff: another model instance exported against this baseline | everything | `export_snapshot` in both languages (`xlsx.py:1030-1083`; `xlsx.rs:1531-1673`) |
| Detached preview | `date_system`, `guards`, `text` for the same answer a live edit would give | agent `_detached` copies two of seven attributes (`agent.py:209-210`) |
| Inventory projection into `metadata.not_carried` and the inspect `xlsx` block | `inventory`, `source.parts` | intake only (`intake.py:70`) |
| Writeback planning (future, C8 and G-C) | `source.fingerprint` against the destination's bytes; `baseline` to stage | the overwrite refusals above are the whole of it |
| Live Excel adapter (future) | `source.fingerprint`, `guards`, `inventory` to decide what it may apply and what it must ask Excel to redo | none |

Rules. The envelope is immutable after import, as `_Baseline` already is
(`@dataclass(frozen=True)`, `xlsx.py:91`). Edits never change it; invalidation is
computed per revision from `(envelope, baseline.document, current document)`, as
`_patch_import` and `patch` already do by diffing original against current. Only import,
`with_insertion_bound` (a session choice) and a successful writeback (which replaces the
envelope with one derived from the written bytes) produce a new one. One guard entry
point per language takes `(envelope, current snapshot, edit batch)`; `apply`,
`set_inputs`, agent preflight, `export_snapshot` and the export re-check all call it,
which closes the three gaps in the Problem section without new rules.

### 3. Invalidation by transformation class

Statuses: **valid** (preserved content unaffected), **stale** (kept as stored, but it
now disagrees with model state; reported in the export report and `inspect`),
**invalidated** (it references something no longer there; export must report it and
may not keep it silently), **blocked** (the edit is refused with a code from
`catalog/refusal-codes.json`). Where today's behaviour exists it is the floor; a rule
below may add reporting but may not remove a refusal.

| Class | Preserved content touched | Rule | Today |
| --- | --- | --- | --- |
| Value edit, existing cell | charts reading it: valid (reference unchanged); pivot whose source range includes it: **stale**; validation list sourced from it: valid, re-read at the next write; comments, hyperlinks, conditional formats on the cell: valid | pivot output cell: **blocked** `pivot_output_protected`; enforced rule: **blocked** `validation_failed` on an out-of-rule value; `@` cell: number coerced to text | `xlsx_features.py:655-684`; `xlsx.rs:996-1035`; coercion `toolkit.py:150-160`, `xlsx.rs:1866-1875`; pivot staleness is documented but not reported (`docs/xlsx-features.md:69-72`) |
| Formula edit, existing cell | as above, plus the calc chain: removed and `fullCalcOnLoad` set | 1904 workbook: **blocked**; compatibility version ≥ 3: **blocked** `compat_version_3`; `blocked_reason` cell (formula group, table region): **blocked**, catalogued as `preserved_only_edit`; under an enforced rule: checked by result, an uncomputable result **blocked** | `toolkit.py:126-142`; `xlsx.rs:1767-1770`; `xlsx.py:1002-1005`; `xlsx.rs:2940-2942`; `xlsx_features.py:693-721`; calc chain `workbook.py:1954-1976`, `xlsx.rs:2490` |
| Insert an absent cell | the sheet's `dimension` and row `spans`: rewritten; inherited row or column style: applied | outside `insertion_bound`: **blocked** `out_of_bounds`; merged non-anchor: **blocked**, catalogued as `merged_cell` (message prefix `unsupported_edit:` today); Text layout: coerced | `xlsx.py:124-152`; `xlsx.rs:1741-1762,1809-1812`; `toolkit.py:162-171`; `xlsx.rs:1883-1898`; Python checks at export and agent preflight only (`xlsx.py:1006-1010`; `agent.py:384-389`) |
| Remove or move a source cell or range | chart series whose `refs` cover it: **invalidated**; pivot `source.ref` covering it: **stale**, and **invalidated** if the range no longer exists; validation `sqref` or range source covering it: **invalidated**; defined name referring into it: the contract's problem, not the envelope's (`#REF!` in the model) | no operation exists; making a source cell absent is **blocked** (`"source cell cannot be removed"`) | `xlsx.rs:1797-1806,2889-2894`; `xlsx.py:1048-1053`; `docs/xlsx-features.md:86-87` |
| Sheet rename, reorder or removal | chart series on that sheet: **invalidated**; pivot sourced from it: **invalidated**; cross-sheet validation sources: **invalidated**; `xl/workbook.xml` sheet entry: must be patched, not copied | charted sheet: **blocked** `chart_reference_protected`; any other structural change: **blocked** as a structural edit | `xlsx.py:925-941,983-989`; `xlsx.rs:1537-1542,2864-2873` |
| Style edit on an imported cell | `xl/styles.xml` would need an audited transformer | **blocked** | `xlsx.py:1011-1014`; `xlsx.rs:1773,2902-2904` |
| Binding change on an imported workbook | the validation rule Workbook Forge wrote for the binding must still match the preserved rule | mismatch **blocked** at export | `xlsx.py:884-922,979-982`; `xlsx.rs:2864-2866,2877` |
| Export without edits | nothing | every part byte-identical except the five in `MAY_CHANGE` | `tools/corpus_check.py:72-78,152-161`; `docs/capabilities-xlsx.md:14-16` |

The rule set is what spec 09 section 10 item 5 asks for: each structural rule declares
what an edit can change, so validation after an edit is a property of the rule set, not
of the caller.

### 4. Two artifact kinds and the handoff

| Kind | Contents | Can do | Cannot do | Handoff between languages |
| --- | --- | --- | --- | --- |
| Semantic-only | the owner document (spec 09 section 4), including `metadata.not_carried` and `provenance` | calculate, author, write a fresh package (`canonical_xlsx.export_canonical`; Rust `export_xlsx`, `xlsx.rs:2402`) | patch an imported package, enforce its validations, keep its text layout, write back | the document bytes plus an optional `CalcContext`; already works through the native bridge and the dual-engine fixtures |
| Preservation-capable | the document plus the envelope | everything in section 2 | nothing fewer | **today only through a file**: export, then re-import rebuilds the envelope (`python/tests/test_rust_xlsx_interop.py:76`); the native bridge takes the toolkit document alone (spec 08 section 7, edge evidence) |

In memory first. Spec 09 step 0 adds `PreservationEnvelope` in both languages,
assembled by `import_xlsx` from section 1, where every field is already computed. A
cross-language handoff carries `(document, source.fingerprint, baseline.parts,
baseline.container?, insertion_bound)`; the receiver runs its own decoder over
`baseline.parts` to re-derive `merged`, `date_system`, `compatibility_version`,
`guards`, `text` and `inventory`, then checks that its `baseline.document` equals the
sender's. Derived fields are never transported, so the two decoders stay independent
and their agreement is tested rather than assumed.

Portable form: open. It must carry the handoff tuple above. Candidates: a sidecar, a
reserved part inside the exported `.xlsx`, or the browser's event log, which already
records `Baseline{sha256, bytes}` and tags the export with its sha256
(`event_log.rs:59-67`; `rust/src/wasm.rs:201-206`). The choice depends on G-C.

### 5. Join with provenance and metadata

| Model field | Owner | Envelope relation |
| --- | --- | --- |
| `provenance.origin` per cell and workbook (`imported`), `provenance.source` = the part name (`python/workbook_forge/canonical_xlsx.py:197,206`) | the contract | the model says *that* a cell came from a part and names it; the envelope holds that part's bytes under the same name in `baseline.parts`. Names in the model, bytes in the envelope |
| `metadata.source.fingerprint` (spec 09 section 4) | intake and import | the one value stored twice, deliberately, as a join key: an envelope whose `source.fingerprint` differs from the document it is attached to is refused (`envelope_mismatch`, a new code) |
| `metadata.not_carried` (today `metadata.intake.not_carried`, `intake.py:70`) | the envelope's `inventory`, projected at serialization | the document copy is read-only output for semantic-only consumers; the inventory is computed once from the package |
| `metadata.defined_names` (`docs/defined-names.md:82-93`) | the contract | not in the envelope; the bytes are preserved, the meaning is modeled |
| `source_path` | nobody: accepted on old documents and dropped (`model.py:971-974`) | `source.handle` is session memory and is never written |
| `blocked_reason` per cell (`xlsx.py:380-412`) | the contract, set only by import | the envelope does not repeat it; the `preserved_only_edit` refusal reads the cell |
| `revision` | the session | the envelope is immutable; statuses in section 3 are computed per revision, not stored |

## Schema / model-version impact

None assumed. `metadata` is an open object (`schemas/workbook-model.v1.schema.json`,
`metadata: {type: object}`; `model.py:969-970`), and `metadata.intake.not_carried`
exists today, so projecting the inventory changes no wire shape. Moving it to
`metadata.not_carried` or adding `metadata.source.fingerprint` as a *required* field
is a `schema_version` matter by `docs/specs/model-versioning.md:7-10` and belongs to
decision G-A in spec 09; this spec works under either outcome by keeping the envelope
beside the document. The toolkit document cannot carry any envelope field without a
shape change (`python/workbook_forge/python_engine.py:72-76` refuses unknown fields;
`rust/src/toolkit.rs:255-312,402` `deny_unknown_fields`), one more reason the envelope
stays a companion. If a portable form is later chosen, it gets its own schema document
(`schemas/preservation-envelope.v1.schema.json`); the workbook model's two version
fields do not move for it.

## Acceptance tests

Shared fixtures: `fixtures/corpus/op_charts.xlsx`, `hb_pivot_cache.xlsx`,
`op_merged_cells.xlsx`, `op_1904_dates.xlsx`, `hb_compat_version_3.xlsx`,
`hb_text_columns.xlsx`; the inventory agreement test runs over every corpus file.
A new shared table `tools/corpus/preservation-cases.json` holds the expected statuses
per class, as `feature-cases.json` does for the guards.

```python
# python/tests/test_preservation_envelope.py
def test_envelope_is_assembled_from_import_and_holds_no_path(tmp_path): ...
    # import under tmp_path / "private-dir-xyz"; that name absent from the envelope's
    # serializable fields; source.fingerprint == content_fingerprint(bytes)
def test_untouched_parts_are_byte_identical_after_a_value_edit(): ...
    # every part outside tools/corpus_check.MAY_CHANGE and the edited sheet part
def test_envelope_inventory_agrees_with_intake_not_carried_on_every_corpus_file(): ...
def test_chart_whose_source_range_is_deleted_is_reported_invalidated_not_kept(): ...
    # a current document missing the series' cells: the status report names the
    # chart part and its refs as invalidated; export refuses rather than keeping it
def test_pivot_source_edit_is_reported_stale_and_output_write_blocks(): ...
def test_charted_sheet_rename_blocks_and_uncharted_rename_reports_dependents(): ...
def test_1904_and_compat3_formula_edits_block_and_value_edits_stay_valid(): ...
def test_set_inputs_and_agent_export_run_the_same_guards_as_apply(): ...
def test_detached_preview_and_snapshot_handoff_carry_the_whole_envelope(): ...
def test_envelope_fingerprint_must_match_metadata_source(): ...   # envelope_mismatch
def test_envelope_rederived_from_baseline_bytes_equals_the_import_envelope(): ...
def test_semantic_only_artifact_exports_fresh_and_declares_its_inventory(): ...
```

```rust
// rust/tests/preservation_envelope.rs
fn envelope_is_assembled_from_import_and_holds_no_path() {}
fn untouched_parts_are_byte_identical_after_a_value_edit() {}
fn envelope_inventory_matches_the_shared_corpus_table() {}   // same table Python checks
fn chart_whose_source_range_is_deleted_is_reported_invalidated_not_kept() {}
fn pivot_source_edit_is_reported_stale_and_output_write_blocks() {}
fn charted_sheet_rename_blocks_and_uncharted_rename_reports_dependents() {}
fn date1904_and_compat3_formula_edits_block_and_value_edits_stay_valid() {}
fn compatibility_version_is_kept_as_an_integer_not_a_flag() {}
fn snapshot_handoff_carries_the_whole_envelope() {}
fn envelope_rederived_from_baseline_bytes_equals_the_import_envelope() {}
fn python_handoff_tuple_rederives_the_same_envelope_in_rust() {}   // interop lane
```

Existing tests that must pass unchanged: `test_absent_cell_export.py:166,181,197,321,331,347`,
`test_toolkit_xlsx.py:95,166,269`, `test_xlsx_features.py:72,91,101,160`,
`test_intake.py:530,569`; `rust/tests/xlsx_features.rs:246,291,384`,
`corpus.rs:304,368`, `absent_cells.rs:258,429`. For this document alone:
`python -m ruff check python tools examples setup.py` and
`python -m pytest -q python/tests/test_project_policy.py python/tests/test_corpus.py::test_capabilities_document_is_current`.

## Dependencies

- Spec 08: sections 1 and 2 (the companions row), 6 (which routes lose them), 7.3 (the
  owner recommendation this spec fulfils).
- Spec 09: section 4 (what is "not in the document"), step 0 (the in-memory
  `PreservationEnvelope` this spec defines; step 0 needs no decision), section 10
  item 5 (rule scopes). Nothing here waits on G-A; only a portable form embedded in
  the document would.
- G-C consumes section 2's writeback row and section 5's fingerprint join.

## Open questions for James

None are required to build the in-memory envelope. Two policy choices are his:

- **Stale pivot output after a source edit:** report and export (recommended; today it
  is allowed and unreported, `docs/xlsx-features.md:69-72`), or block the export until
  the caller acknowledges it. Reporting keeps today's behaviour honest without a new
  refusal; blocking would make every pivot workbook harder to edit than Excel does.
- **"Same baseline" for G-C:** equality of `source.fingerprint` with the destination's
  current bytes (recommended; it is what the browser log already records) or a weaker
  test on the parts the writeback would not touch. The weaker test lets a concurrent
  edit to an untouched sheet pass; the strict one refuses it and asks for a re-import.
