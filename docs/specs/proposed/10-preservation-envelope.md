---
type: technical-specification
status: proposed
base: main 41f28d3
summary: Name the companion that preserves what the workbook model does not represent, built from what import_xlsx already holds in both languages. Contents with their derivation (most from the package bytes; the baseline document also from the import bindings), readers and writers, one guard entry point evaluated on the post-batch document, an invalidation rule per transformation class with each path's floor named rather than averaged, two artifact kinds and their cross-language handoff, the join with provenance and metadata as projections of the envelope, and an inventory defined relative to the owner contract. In-memory first; the portable form stays open. No version field moves.
---

# 10 — Preservation envelope

## Problem

An imported workbook carries far more than cells. Today both importers keep that
material and guard edits against it, but the thing they keep has no name and no
contract. Python holds it as seven private attributes on the toolkit wrapper
(`python/workbook_forge/toolkit.py:93-104`), one of which, `_source_baseline`, is a frozen
`_Baseline` (`python/workbook_forge/xlsx.py:91-103`); Rust holds it as the fields of
`ImportedWorkbook` beside the session (`rust/src/xlsx.rs:1416-1435`); canonical intake
keeps none of it and instead lists what it dropped as `metadata.intake.not_carried`
(`python/workbook_forge/intake.py:70,118-190`). The two importers already disagree on
what they keep (section 1: zip container metadata, the compatibility version as an
integer or a flag, a configurable or fixed insertion bound, part order).

Because the companion is implicit, its readers are inconsistent within one language and
between the two. In Python, `WorkbookModel.apply` runs the 1904, compatibility, text
and feature guards (`toolkit.py:123-148`), but `set_inputs` goes to the engine session
directly (`toolkit.py:231-233`): it writes values only, so the formula guards have nothing
to check, but it skips text coercion (`:150`) and the feature check (`:174`), so a numeric
value lands in an `@` cell and `preview_inputs` and `calculate` see the unguarded value
(Rust `set_inputs` goes through `apply`, `xlsx.rs:1922-1925`). `apply` also leaves the
merged-cell and out-of-grid insertion checks, the `blocked_reason` check and the
style-on-imported-cell check to export and agent preflight (`xlsx.py:1002-1014`;
`agent.py:375-389`), so `set_value` on a merged non-anchor commits and export refuses
later (`test_absent_cell_export.py:321-326` pins exactly that). The agent's detached copy
carries three of the seven attributes (`agent.py:204,209-210`), so the agent's `export`
(`agent.py:354`) reaches `_patch_import` with `guard=None` (`xlsx.py:1173`) and the
export-time feature re-check returns early (`xlsx.py:948`). Rust `patch` takes no guard
at all (`xlsx.rs:2855-2863`). The same session state therefore exports with three
different sets of checks depending on the path. Spec 08 section 6 showed that every
serialized form drops the companion; spec 09 section 4 names "the preservation envelope"
as the owner of what is not in the document and defers its rules here; spec 08 section
7.3 recommends the companion. Writeback to the original file is gate G-C (`CONTEXT.md`
`open_questions`; `docs/specs/proposed/README.md`).

## Outcome

- One named type, `PreservationEnvelope`, in both languages, assembled by import from
  what the importers already compute, with the same contents under the same names.
- Every edit, preview, export and writeback of an imported workbook consults it through
  one entry point that evaluates the post-batch document; no path skips a guard another
  path runs, and the floor is the strictest path's.
- "Preserved" becomes a claim about an operation: each transformation class says whether
  preserved content stays valid, is reported stale or invalidated, or blocks the edit.
- The carry inventory is produced by the envelope relative to the owner contract and
  projected into the document, so a semantic-only consumer learns what a fresh export
  will lack.

## Scope

- The envelope's contents, readers, writers and invalidation rules (Design 1 to 3).
- The semantic-only and preservation-capable artifact kinds and the in-memory
  cross-language handoff (Design 4).
- The join with `provenance` and `metadata` (Design 5).
- Acceptance tests in both languages, including an inventory agreement test on the corpus.

## Non-goals

- A portable serialization of the envelope. Section 4 states what it must carry and
  leaves the container (sidecar, package part, JSON beside the document) open.
- Writeback to the original file (gate G-C). The envelope supplies the "same baseline"
  test; the conflict rule, staging and recovery are G-C's.
- Row or column insertion and deletion (`docs/xlsx-features.md:86-87`), range moves and
  sheet renames as operations. No API for a rename or move exists in `toolkit.py`,
  `python_engine.py`, `toolkit.rs` or `wasm.rs`; section 3 states what the envelope must
  report when they arrive, not how they are implemented.
- Pivot refresh, chart redraw, external data, macros. Preserved as bytes, as today.
- Any `schema_version` or `model_version` change; see the impact section.

## Design

### 1. Contents

Every field is a pure function of the imported package bytes, **except three**: the two
marked *session*, and `baseline.document`, whose `inputs` and `outputs` come from the
import call, not the bytes (`import_xlsx(path, *, inputs=None, outputs=None)`,
`xlsx.py:292-293,309-310`; Rust `WorkbookModel { inputs, outputs, .. }`,
`xlsx.rs:996-1000,1466-1469`). Section 4's handoff carries the import bindings for that
reason.

| Field | Meaning | Python today | Rust today |
| --- | --- | --- | --- |
| `source.fingerprint` | `sha256:` of the imported bytes; the envelope's identity and its join key to the model (section 5) | not computed by `import_xlsx`; the canonical side has `content_fingerprint` and `require_fingerprint` (`python/workbook_forge/model.py:293-302`); the browser log records `Baseline{kind, name, sha256, bytes}` where `bytes` is a length (`rust/src/event_log.rs:46-58`) | `model::content_fingerprint` (`rust/src/model.rs:1636`); not stored on `ImportedWorkbook` |
| `source.parts` | the package part names, **sorted**; the key set of `baseline.parts`, not a separate list. Zip order is container metadata | `_import_metadata["package_parts"]` (`xlsx.py:337`) | `capabilities["parts"]` from a `BTreeMap`, already sorted (`xlsx.rs:310-312,1344`) |
| `source.workbook_part`, `source.sheet_parts`, `source.sheet_entries`, `source.chartsheets` | which part is the workbook; which part each worksheet is; the full sheet-entry order including chartsheets, which is the order `localSheetId` counts; the chartsheet name to part map | `_import_metadata["sheets"][i]["part"]` (`xlsx.py:484`), re-derived again by `Workbook.open` on the staged seed (`xlsx.py:1166`); `Workbook.sheet_entry_names` (`workbook.py:829`); `metadata["chartsheets"]` (`xlsx.py:518`) | `workbook_part`, `sheet_parts` (worksheets only, chartsheets skipped at `xlsx.rs:1038-1039`), `Decoded.sheet_entries` (`xlsx.rs:806,818`), `chartsheets` (`xlsx.rs:1344`) |
| `source.handle` (*session*, never serialized) | where the bytes came from, for the overwrite refusal only | `_Baseline.source: Path` (`xlsx.py:93`); refusal at `xlsx.py:1101-1102` and `workbook.py:1978-1981` | `source: Option<PathBuf>`, canonicalized (`xlsx.rs:1417,1472`), `None` for a bytes import (`xlsx.rs:1481-1488`); refusal at `xlsx.rs:1954-1967` |
| `baseline.parts` | the imported part bytes, including every `.rels` part | `_Baseline.parts` (`xlsx.py:94`) | `baseline: Package` (`xlsx.rs:1418`) |
| `baseline.container` | zip entry metadata and archive comment | `_Baseline.infos`, `comment` (`xlsx.py:95-96`) | not kept; `encode_checked` re-encodes (`xlsx.rs:491`) |
| `baseline.document` (*depends on import bindings*) | the model as imported, the "before" every patch diffs against; its `inputs`/`outputs` are the import call's | `_Baseline.document_json` (`xlsx.py:97`, built at `:564` from `:309-310`) | `original: WorkbookModel` (`xlsx.rs:1419`) |
| `tables` | table metadata the writers must not disturb | `metadata["tables"]` (`xlsx.py:316,347-362`) | `table_metadata` |
| `merged` | sheet name to merged ranges `(c1, r1, c2, r2)` | `_Baseline.merged` (`xlsx.py:99-101`) | `merged` (`xlsx.rs:1423-1425`) |
| `date_system` | `1900` or `1904` | `uses_1904_date_system` (`xlsx.py:98`; set at `workbook.py:1278`) | `date1904` (`xlsx.rs:1429,972-977`) |
| `compatibility_version` | the integer; 3 or later blocks formula edits | `compatibility_version: int` (`xlsx.py:102-103`; `workbook.py:1284`) | `compat3: bool` (`xlsx.rs:1430,978`); the integer is lost |
| `guards` | validation rules with `enforced` and their raw XML, pivot output bounds, charted sheets **and chart series references** | `FeatureGuard(validations, pivots, charts)` (`xlsx.py:540`; `python/workbook_forge/xlsx_features.py:647-653`); raw per-sheet validation XML in `metadata["sheets"][*]["validation"]` (`xlsx.py:489-492`) | `FeatureGuard{rules, pivots, charted}` holds lowercased charted sheet names only (`rust/src/xlsx_features.rs:949-951,992`); series `refs` live in `capabilities["charts"]` JSON (`xlsx.rs:1344`) and must move into the guard. Both languages derive charted sheets with `referenced_sheets(formula)` (`xlsx_features.py:766-772`; `xlsx_features.rs:955-975`), so a series that reads a defined name yields no sheet and is unprotected today |
| `text.cells`, `text.layouts` | imported `@` cells; per-sheet row and column styles an inserted cell inherits | `_text_cells` (`xlsx.py:541-546`), `_text_layouts` and `_imported_addresses` (`xlsx.py:552-558`; `TextLayout`, `xlsx.py:228-256`) | `text_cells`, `text_layouts` (`xlsx.rs:1434-1435`); imported addresses read from `original` (`xlsx.rs:1888-1893`) |
| `insertion_bound` (*session*) | the largest row and column an absent cell may be inserted at | constants `_EXCEL_MAX_ROWS`, `_EXCEL_MAX_COLUMNS` (`xlsx.py:106-107`) | `insertion_bound`, default Excel's grid, narrowed by `with_insertion_bound` (`xlsx.rs:1428,1518,1723-1731`) |
| `inventory` | what the package holds that **the owner contract** does not carry: `[{where, what, count, sheet?}]`. Relative to the owner (section 5): under the canonical v1 carry set it is today's `_not_carried`; under a toolkit-based owner (spec 09, Base B) `Style` and `column_widths` leave it | canonical only: `_not_carried` (`intake.py:118-190`), entry shape `intake.py:108-115`; the toolkit has prose lists `preserved_only` and `rejected` (`xlsx.py:326-336`) | prose lists in `capabilities` (`xlsx.rs:1344`) |

Not in the envelope: the defined-name table (`_names`, `toolkit.py:93`; spec 09 section 4
gives `metadata.defined_names` to the contract; the bytes of `xl/workbook.xml` stay in
`baseline.parts` and come back byte for byte, `docs/defined-names.md:95-100`);
`blocked_reason` (set on cells by import, `xlsx.py:380-412` and `_block_range`,
`xlsx.py:211-225`, owned by the contract); `revision` (the session). The inspect-facing
`xlsx` block (`toolkit.py:250-251`) and Rust `capabilities()` are projections of the
envelope, generated from it rather than kept beside it.

### 2. Who reads it, who may replace it

| Operation | Reads | Today |
| --- | --- | --- |
| Edit guard: value, formula or style into an existing or absent cell | `date_system`, `compatibility_version`, `guards`, `text`, `merged`, `insertion_bound`, `baseline.document` | Python `apply` (`toolkit.py:123-148`) and agent preflight (`agent.py:364-389`); Rust `apply` → `validate_edits` (`xlsx.rs:1864-1879,1764-1862`). Python `set_inputs` skips text coercion and the feature check (`toolkit.py:231-233`); Python `apply` leaves insertion, `blocked_reason` and style checks to export and agent preflight (`xlsx.py:1002-1014`; `agent.py:375-389`) |
| Formula result check under an enforced rule | `guards`, the names table | `check_formula_results` on a detached copy with the batch applied (`xlsx_features.py:723-764`; `xlsx.rs:1833-1851`) |
| Patch export to a new file or to bytes | everything but `inventory` | Python: `export_xlsx` → `_patch_import` (`xlsx.py:1086-1186,970-1030`) re-checks changed cells against the **current** validation lists (`xlsx.py:944-961`, from `:996`), chart sheets, bindings, insertion, `blocked_reason` and styles. Rust: `export_package` → `patch` → `recalc` (`xlsx.rs:1927-1946,2855-2968,2490`); `patch` takes `(original, model, report, workbook_part, sheet_parts, date1904)`, no guard, merged, compatibility or text, and re-checks bindings (`verify_validation`, `:2877`) and 1904 only, because everything else ran at `apply` or in `export_snapshot` (`:1531-1664`). Only changed sheet parts, `calcPr`, and the calcChain removal touch bytes (`tools/corpus_check.py:72-78`) |
| Snapshot handoff: another model instance exported against this baseline | everything | `export_snapshot` in both languages (`xlsx.py:1030-1083`; `xlsx.rs:1531-1673`). Python re-attaches five attributes by hand (`xlsx.py:1078-1082`) and `_names` through the constructor (`:1043`) |
| Detached preview | `date_system`, `guards`, `text` for the same answer a live edit would give | agent `_detached` copies three of seven attributes (`agent.py:204,209-210`) |
| Inventory projection into `metadata.not_carried` and the inspect `xlsx` block | `inventory`, `source.parts` | intake only (`intake.py:70`) |
| Writeback planning (future, G-C) | `source.fingerprint` against the destination's bytes; `baseline` to stage | the overwrite refusals above are the whole of it |
| Live Excel adapter (future) | `source.fingerprint`, `guards`, `inventory` to decide what it may apply and what it must ask Excel to redo | none |

Rules. The envelope is immutable after import, as `_Baseline` already is
(`@dataclass(frozen=True)`, `xlsx.py:91`). Edits never change it; invalidation is
computed per revision from `(envelope, baseline.document, current document)`, as
`_patch_import` and `patch` already do by diffing original against current. Only import,
`with_insertion_bound` (a session choice) and a successful writeback (which replaces the
envelope with one derived from the written bytes) produce a new one.

One guard entry point per language takes `(envelope, post-batch document)`: it judges the
document as it would be after the batch, which is what export judges today
(`xlsx.py:952-956`), not the pre-batch snapshot `apply` reads list sources from today
(`toolkit.py:188-196`; `docs/xlsx-features.md:44-45`). A batch that changes a list's source
cell and writes under that list therefore gets one answer, not opposite ones from `apply`
and `export_xlsx`. `apply`, `set_inputs`, agent preflight, `export_snapshot`, the export
re-check and Rust `patch` all call it. That closes the gaps in the Problem section and
changes two pinned behaviours deliberately: `set_value` on a merged non-anchor and
`set_style` on an imported cell refuse at `apply` instead of committing and refusing at
export (`test_absent_cell_export.py:321-326`; `test_toolkit_xlsx.py:160-163` change in the
same pull request).

### 3. Invalidation by transformation class

Statuses: **valid** (preserved content unaffected), **stale** (kept as stored, but it
now disagrees with model state; reported in the export report and `inspect`),
**invalidated** (it references something no longer there; export must report it and
may not keep it silently), **blocked** (the edit is refused with a code from
`catalog/refusal-codes.json`). Today's behaviour differs by path; the floor is the
**strictest path's**, named per row, and a rule may add reporting but may not remove any
path's refusal.

| Class | Preserved content touched | Rule | Today, by path |
| --- | --- | --- | --- |
| Value edit, existing cell | charts reading it: valid (reference unchanged); pivot whose source range includes it: **stale**; validation list sourced from it: every cell written under that list since import is **re-checked against the current list at the guard and at export, and blocked `validation_failed` if it no longer matches**; comments, hyperlinks, conditional formats on the cell: valid | pivot output cell: **blocked** `pivot_output_protected`; enforced rule: **blocked** `validation_failed` on an out-of-rule value; `@` cell: number coerced to text | Guards: `xlsx_features.py:655-684`; `xlsx_features.rs:999-1033`; coercion `toolkit.py:150-160`, `xlsx.rs:1866-1875`. List re-check: the Python toolkit export does it (`xlsx.py:944-961`; `rule_violation` resolves the range at check time, `xlsx_features.py:420-430,335-353`), the Python agent export (`guard=None`) and Rust `patch` (no guard) write the same state; the floor is the toolkit export's. Fixture: `fixtures/corpus/xw_dropdowns.xlsx` F2:F50 is range-sourced. Pivot staleness is documented but not reported (`docs/xlsx-features.md:69-72`) |
| Formula edit, existing or absent cell | as above, plus the calc chain: removed and `fullCalcOnLoad` set | 1904 workbook: **blocked** on every path; compatibility version ≥ 3: **blocked** `compat_version_3`; `blocked_reason` cell (formula group, table region): **blocked**, catalogued as `preserved_only_edit`; under an enforced rule: checked by result, an uncomputable result **blocked** | `apply` refuses 1904 and compat formulas in both languages (`toolkit.py:126-142`; `xlsx.rs:1767-1770`); `blocked_reason` at `xlsx.py:1002-1005`, `xlsx.rs:2940-2942`; results `xlsx_features.py:693-721`; calc chain `workbook.py:1954-1976`, `xlsx.rs:2490`. **Hole today:** Python's `export_snapshot` (`xlsx.py:1030-1083`) refuses compat ≥ 3 formulas (`:1067-1076`) but has no 1904 check; `_patch_import` has none; `export_xlsx` refuses 1904 only when `cache_values` is non-empty (`:1150-1152`); a new formula at an absent address no output reaches has no cache and no `blocked_reason` (import sets it only on imported formula cells), so a 1904 workbook exports with a new formula in Python. Rust `patch` refuses it (`xlsx.rs:2910-2912`). No test covers the route; the rule closes it |
| Insert an absent cell | the sheet's `dimension` and row `spans`: rewritten; inherited row or column style: applied | outside `insertion_bound`: **blocked** `out_of_bounds`; merged non-anchor: **blocked**, catalogued as `merged_cell` (message prefix `unsupported_edit:` today); Text layout: coerced | `xlsx.py:124-152`; `xlsx.rs:1741-1762,1809-1812`; `toolkit.py:162-171`; `xlsx.rs:1883-1898`; Python checks at export and agent preflight only (`xlsx.py:1006-1010`; `agent.py:384-389`), Rust at `apply` |
| Remove or move a source cell or range | chart series whose `refs` cover it: **invalidated** (needs the refs in `guards`; a series reading a defined name is reported as unprotected until names are resolved); pivot `source.ref` covering it: **stale**, and **invalidated** if the range no longer exists; validation `sqref` or range source covering it: **invalidated**; defined name referring into it: the contract's problem, not the envelope's (`#REF!` in the model) | no operation exists; making a source cell absent is **blocked** | Rust only: `validate_edits` `restore_absent` (`xlsx.rs:1795-1806`) and `patch` (`:2888-2893`); Python has no `restore_absent`, its one refusal is in `export_snapshot` (`xlsx.py:1053`) |
| Sheet rename, reorder or removal | chart series on that sheet: **invalidated**; pivot sourced from it: **invalidated**; cross-sheet validation sources: **invalidated**; `xl/workbook.xml` sheet entry: must be patched, not copied | charted sheet: **blocked** `chart_reference_protected`; any other structural change: **blocked** as a structural edit | `xlsx.py:925-941,983-989`; `xlsx.rs:1537-1542,2865-2873` |
| Style edit on an imported cell | `xl/styles.xml` would need an audited transformer | **blocked** at the guard, not only at export | `xlsx.py:1011-1014`; `xlsx.rs:1773,2902-2904`; today Python commits and refuses at export (`test_toolkit_xlsx.py:160-163`) |
| Binding change on an imported workbook | the validation rule Workbook Forge wrote for the binding must still match the preserved rule | mismatch **blocked** at export | `xlsx.py:884-922,979-982`; `xlsx.rs:2864-2866,2877` |
| Export without edits | nothing | every part byte-identical except the parts in `MAY_CHANGE` **and every sheet part holding a formula cell in the calculation closure**, which both writers re-serialise to refresh caches; with no bound outputs the closure is every formula cell, so an unedited export rewrites every formula-bearing sheet (spec 13 Design 2, `cache_refresh`) | `tools/corpus_check.py:72-78,152-161` reports no finding only because its lane binds the edited cell as the sole output (`:262`); the rewrite is at `xlsx.py:1137-1148,1177-1179` with `workbook.py:1251-1254,1985-1990` and `xlsx.rs:2929-2939,2961-2965`; the closure at `python_engine.py:415` and `toolkit.rs:1363-1365`; `docs/capabilities-xlsx.md:14-16` |

The rule set is what spec 09 section 10 item 5 asks for: each structural rule declares
what an edit can change, so validation after an edit is a property of the rule set, not
of the caller.

### 4. Two artifact kinds and the handoff

| Kind | Contents | Can do | Cannot do | Handoff between languages |
| --- | --- | --- | --- | --- |
| Semantic-only | the owner document (spec 09 section 4), including `metadata.not_carried` and `provenance` | calculate, author, write a fresh package (`canonical_xlsx.export_canonical`; Rust `export_xlsx`, `xlsx.rs:2402`) | patch an imported package, enforce its validations, keep its text layout, write back | the document bytes plus an optional `CalcContext`; already works through the native bridge and the toolkit-shape cross-engine fixtures |
| Preservation-capable | the document plus the envelope | everything in section 2 | nothing fewer | **today only through a file**: export, then re-import rebuilds the envelope (`python/tests/test_rust_xlsx_interop.py:76`); the native bridge takes the toolkit document alone (spec 08 section 7, edge evidence) |

In memory first. Spec 09 step 0 adds `PreservationEnvelope` in both languages,
assembled by `import_xlsx` from section 1, where every field is already computed. A
cross-language handoff carries `(document, source.fingerprint, baseline.parts,
baseline.container?, insertion_bound, import bindings)`; the receiver runs its own
decoder over `baseline.parts` with those bindings to re-derive `baseline.document`,
`merged`, `date_system`, `compatibility_version`, `guards`, `tables`, `text` and
`inventory`, then checks that its re-derived `baseline.document` equals the sender's cell
for cell and sheet for sheet, with the bindings compared separately. Derived fields are
never transported, so the two decoders stay independent and their agreement is tested
rather than assumed.

Portable form: open. It must carry the handoff tuple above. Candidates: a sidecar, a
reserved part inside the exported `.xlsx`, or an extension of the browser's event log,
which today records only a hash and a byte length (`Baseline{sha256, bytes: usize}`,
`event_log.rs:46-58,64`) and tags the export with its sha256 (`rust/src/wasm.rs:201-206`),
so it would need the parts added. The choice depends on G-C.

### 5. Join with provenance and metadata

The envelope is authoritative for everything it holds. Two document fields are
**projections** of it, written at serialization and read-only for consumers, and one
value is a deliberate **join key** stored on both sides.

| Model field | Owner | Envelope relation |
| --- | --- | --- |
| `provenance.origin` per cell and workbook (`imported`), `provenance.source` = the part name (`python/workbook_forge/canonical_xlsx.py:197,206`) | the contract | the model says *that* a cell came from a part and names it; the envelope holds that part's bytes under the same name in `baseline.parts`. Names in the model, bytes in the envelope |
| `metadata.source.fingerprint` (spec 09 section 4) | intake and import | the join key, stored on both sides deliberately: an envelope whose `source.fingerprint` differs from the document it is attached to is refused (`envelope_mismatch`, a new code) |
| `metadata.source` part names (spec 09 section 4) | projection of `source.parts` | written from the envelope at serialization; never edited in the document |
| `metadata.not_carried` (today `metadata.intake.not_carried`, `intake.py:70`) | projection of `inventory` | written from the envelope at serialization; the inventory is computed once from the package relative to the owner contract (section 1) |
| `metadata.defined_names` (`docs/defined-names.md:82-93`) | the contract | not in the envelope; the bytes are preserved, the meaning is modeled |
| `source_path` | nobody: accepted on old documents and dropped (`model.py:971-974`) | `source.handle` is session memory and is never written |
| `blocked_reason` per cell (`xlsx.py:380-412,211-225`) | the contract, set only by import | the envelope does not repeat it; the `preserved_only_edit` refusal reads the cell |
| `revision` | the session | the envelope is immutable; statuses in section 3 are computed per revision, not stored |

Under spec 09's Base A, `provenance.source` per cell and `source.sheet_parts` are one
fact expressed twice (a name on the cell; a map in the envelope); the projection rule
above makes the envelope the writer of both. Under Base B, the toolkit document already
carries part names in `_import_metadata` (`xlsx.py:484`); that attribute becomes the
projection and stops being a second source.

## Schema / model-version impact

None assumed. `metadata` is an open object (`schemas/workbook-model.v1.schema.json`,
`metadata: {type: object}`; `model.py:969-970`), and `metadata.intake.not_carried`
exists today, so projecting the inventory changes no wire shape. Moving it to
`metadata.not_carried` or adding `metadata.source.fingerprint` as a *required* field
is a `schema_version` matter by `docs/specs/model-versioning.md:7-10` and belongs to
decision G-A in spec 09. Every field of the envelope except `inventory` is the same
under either base; `inventory` is defined relative to the owner's carry set and its
corpus agreement test is written per owner. The toolkit document cannot carry any
envelope field without a shape change (`python/workbook_forge/python_engine.py:72-76`
refuses unknown fields; `rust/src/toolkit.rs:255-312,403` `deny_unknown_fields`), one
more reason the envelope stays a companion. If a portable form is later chosen, it gets
its own schema document (`schemas/preservation-envelope.v1.schema.json`); the workbook
model's two version fields do not move for it.

## Acceptance tests

Shared fixtures: `fixtures/corpus/op_charts.xlsx`, `hb_pivot_cache.xlsx`,
`op_merged_cells.xlsx`, `op_1904_dates.xlsx`, `hb_compat_version_3.xlsx`,
`hb_text_columns.xlsx`, `xw_dropdowns.xlsx` (range-sourced list); the inventory
agreement test runs over every corpus file. A new shared table
`tools/corpus/preservation-cases.json` holds the expected statuses per class and path, as
`feature-cases.json` does for the guards.

```python
# python/tests/test_preservation_envelope.py
def test_envelope_is_assembled_from_import_and_holds_no_path(tmp_path): ...
    # import under tmp_path / "private-dir-xyz"; that name absent from the envelope's
    # serializable fields; source.fingerprint == content_fingerprint(bytes)
def test_untouched_parts_are_byte_identical_after_a_value_edit(): ...
    # every part outside tools/corpus_check.MAY_CHANGE and the edited sheet part
def test_envelope_inventory_matches_the_owner_carry_set_on_every_corpus_file(): ...
    # under the canonical v1 carry set equals intake not_carried; the toolkit-owner expectation is a second column
def test_chart_whose_source_range_is_deleted_is_reported_invalidated_not_kept(): ...
    # a current document missing the series' cells: the status report names the
    # chart part and its refs as invalidated; export refuses rather than keeping it
def test_chart_series_reading_a_defined_name_is_reported_unprotected(): ...
def test_pivot_source_edit_is_reported_stale_and_output_write_blocks(): ...
def test_list_source_edit_recheck_blocks_on_every_export_path(): ...
    # toolkit export, agent export and the snapshot export give one answer on xw_dropdowns
def test_1904_formula_at_absent_address_is_refused_on_every_export_path(): ...
    # closes the Python export_snapshot and _patch_import hole
def test_charted_sheet_rename_blocks_and_uncharted_rename_reports_dependents(): ...
def test_compat3_formula_edits_block_and_value_edits_stay_valid(): ...
def test_set_inputs_and_agent_export_run_the_same_guards_as_apply(): ...
def test_guard_judges_the_post_batch_document(): ...
    # a batch that changes a list source and writes under it gets one answer at apply and export
def test_merged_non_anchor_and_imported_style_edits_refuse_at_apply(): ...
    # replaces the export-time assertions in test_absent_cell_export.py:321-326 and test_toolkit_xlsx.py:160-163
def test_detached_preview_and_snapshot_handoff_carry_the_whole_envelope(): ...
def test_envelope_fingerprint_must_match_metadata_source(): ...   # envelope_mismatch
def test_envelope_rederived_from_baseline_bytes_and_import_bindings_equals_the_import_envelope(): ...
def test_semantic_only_artifact_exports_fresh_and_declares_its_inventory(): ...
```

```rust
// rust/tests/preservation_envelope.rs
fn envelope_is_assembled_from_import_and_holds_no_path() {}
fn untouched_parts_are_byte_identical_after_a_value_edit() {}
fn envelope_inventory_matches_the_shared_corpus_table() {}   // same table Python checks
fn chart_series_refs_live_in_the_guard_and_drive_invalidation() {}
fn chart_whose_source_range_is_deleted_is_reported_invalidated_not_kept() {}
fn pivot_source_edit_is_reported_stale_and_output_write_blocks() {}
fn list_source_edit_recheck_blocks_in_patch() {}   // patch gains the guard
fn date1904_formula_at_absent_address_is_refused() {}   // already true; pinned
fn charted_sheet_rename_blocks_and_uncharted_rename_reports_dependents() {}
fn compat3_formula_edits_block_and_value_edits_stay_valid() {}
fn compatibility_version_is_kept_as_an_integer_not_a_flag() {}
fn snapshot_handoff_carries_the_whole_envelope() {}
fn envelope_rederived_from_baseline_bytes_and_import_bindings_equals_the_import_envelope() {}
fn python_handoff_tuple_rederives_the_same_envelope_in_rust() {}   // interop lane
```

Existing tests that must pass unchanged: `test_absent_cell_export.py:166,181,197,331,347`,
`test_toolkit_xlsx.py:95,166,269`, `test_xlsx_features.py:72,91,101,160`,
`test_intake.py:530,569`; `rust/tests/xlsx_features.rs:246,291,384`,
`corpus.rs:304,368`, `absent_cells.rs:258,429`. Two existing tests change deliberately,
as section 2 states: `test_absent_cell_export.py:321-326` and
`test_toolkit_xlsx.py:160-163`. For this document alone:
`python -m ruff check python tools examples setup.py` and
`python -m pytest -q python/tests/test_project_policy.py python/tests/test_corpus.py::test_capabilities_document_is_current`.

## Dependencies

- Spec 08: sections 1 and 2 (the companions row), 6 (which routes lose them), 7.3 (the
  owner recommendation this spec fulfils).
- Spec 09: section 4 (what is "not in the document"), step 0 (the in-memory
  `PreservationEnvelope` this spec defines; the envelope needs no decision, its
  `inventory` definition follows G-A), section 10 item 5 (rule scopes).
- G-C consumes section 2's writeback row and section 5's fingerprint join.

## Open questions for James

None are required to build the in-memory envelope. Three policy choices are his:

- **Stale pivot output after a source edit:** report and export (recommended; today it
  is allowed and unreported, `docs/xlsx-features.md:69-72`), or block the export until
  the caller acknowledges it. Reporting keeps today's behaviour honest without a new
  refusal; blocking would make every pivot workbook harder to edit than Excel does.
- **"Same baseline" for G-C:** equality of `source.fingerprint` with the destination's
  current bytes (recommended; it is what the browser log already records) or a weaker
  test on the parts the writeback would not touch. The weaker test lets a concurrent
  edit to an untouched sheet pass; the strict one refuses it and asks for a re-import.
- **The two pinned behaviours that move from export to `apply`** (merged non-anchor
  value, imported-cell style): refusing earlier is stricter for callers who relied on
  committing first; the recommendation is to move them, because the toolkit export,
  the agent export and Rust already disagree about them today.

## Refutation record

An independent refuter read the first draft under the `contract` and `silent-failure`
lenses: 5 refuted, 12 weakened, 16 confirmed, 10 citation errors. All applied above.

| Finding | Effect |
| --- | --- |
| Refuted: every field is a pure function of the bytes (the baseline document embeds the import bindings) | Section 1 corrected; the handoff carries the bindings and compares cells |
| Refuted: the fingerprint is the only value stored twice (part names appear in the document and the envelope; `source.parts` repeats the baseline's keys; `not_carried` copies the inventory) | Section 5 rewritten as two projections and one join key; `source.parts` defined as the baseline's key set |
| Refuted (silent failure): a list-source value edit is "valid" (the Python toolkit export re-checks and refuses; the agent export and Rust `patch` write it) | Section 3 row 1 names the three behaviours and takes the toolkit export as the floor |
| Refuted (silent failure): 1904 formula edits are "blocked" (Python's snapshot export admits a new formula at an absent address; Rust refuses) | Section 3 row 2 names the hole; a test closes it |
| Refuted: motivation cited to vision contracts and sections that do not exist | Replaced with spec 08, spec 09 and the gate definitions in the tree |
| Weakened: three of seven attributes copied, not two; `apply` also skips the `blocked_reason` and style checks and two pinned tests change; `set_inputs` skips text coercion and the feature check only, Rust goes through `apply`; Rust `patch` takes no guard; the guard must say which document it judges; `inventory` depends on the owner's carry set; the event-log baseline holds a length, not bytes; Rust's guard lacks chart series refs and defined-name series are unprotected; Rust part order is sorted; sheet entries, chartsheets, tables and raw validation XML were omitted; a citation about rows and columns did not carry the rename claim; the attribute count double-counted the baseline | All corrected in the Problem, sections 1 to 5 and the tests |
| Refuted after the fact (by spec 13's refuter): "Export without edits" preserves everything but the five `MAY_CHANGE` parts (both writers re-serialise every formula-bearing sheet in the calculation closure to refresh caches) | Design 3 last row corrected |
| Citation errors: `toolkit.py:93-104`; `xlsx.py:337`; `xlsx_features.rs:999-1033`; `intake.py:118-190`; `xlsx.rs:1927-1946`; `_block_range` at `xlsx.py:211-225`; the event-log baseline; four off-by-one ranges | Corrected |
| Confirmed: the `set_inputs` bypass; the agent export's `guard=None` chain; `_Baseline` frozen with the cited fields; `ImportedWorkbook` fields; lists re-read at write time; pivot refusal order; charted-sheet rename refusal; the Rust-only `restore_absent` refusal; the five `MAY_CHANGE` parts; every named fixture and test exists | Unchanged |

The refuter's verdict: the companion shape holds on either base; the first draft's text
was not yet a contract a test could fail because of the baseline-document dependency, the
base-dependent inventory and the averaged invalidation floor. This version states each.
