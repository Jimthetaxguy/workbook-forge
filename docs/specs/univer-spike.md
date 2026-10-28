# Univer engine-handoff spike

Status: prototype, October 24, 2026. Not a general spreadsheet product; it
proves one browser authoring and round-trip path over the existing engine.

**Question.** Can Univer OSS (Apache-2.0, `@univerjs/*` 1.2.0) act purely as a
presentation and editing layer while Forge, running as WASM in a Web Worker,
is the only calculator and the source of truth?

**Answer: yes, with one specific configuration.** The cell editor and formula
bar need Univer's formula plugins to be registered. Univer's engine can still
be kept from calculating. The prototype shows it never calculated and never
rewrote a value; Rust/WASM in a Web Worker owns the workbook state and results.

## Layout

```
web/forge-engine/   headless package: Forge WASM in a Web Worker behind a typed
                    command API. No Univer, React or rxjs imports (tested).
  src/protocol.ts   command names and payload types (host-agnostic)
  src/engine.ts     command handler; transport-free, runs in a worker,
                    a WebView shell, or Node
  src/worker.ts     Web Worker entry, the only owner of BrowserWorkbook
  src/client.ts     typed client over any MessagePort-like channel
  build-wasm.sh     cargo build --target wasm32 + wasm-bindgen (version pinned
                    to rust/Cargo.lock)
web/univer-spike/   Univer adapter and demo, using only the command API
  src/adapter.ts    command interception, projection, undo/redo sent to the
                    worker as intents, and an audit of Univer calculation
                    and writes
  src/univer-host.ts / formula-plugins.ts   the configurations compared below
  tests/acceptance.spec.ts                  Playwright acceptance suite
fixtures/univer-spike-chain.xlsx            A1 = 10 and five dependent formulas
fixtures/realtime-chain.xlsx                event-log fixture: as above, but B1 = 0 literal
rust/examples/univer_spike_fixture.rs       writes both fixtures (--realtime for the second)
rust/src/event_log.rs                       session event log v1, log-based undo/redo, replay
tests/fixtures/sales-bang-boolean.xlsx       legal `!` sheet name and Boolean recovery case
rust/examples/univer_edge_fixture.rs        writes that recovery fixture
```

**Event log (v1).** The worker session keeps an append-only, memory-only log
(`rust/src/event_log.rs`, sequenced under the same lock that assigns
revisions): `session.opened` (baseline name, sha256, bytes, engine), one
`intent` per command (actor, base, `applied`/`rejected` with the error code,
revision, per-cell `before`/`after`) and one `calc` per applied intent
(`[key, before, after]` deltas for formula cells). The command API adds `log
{fromSeq}`, `subscribe {events:true}`, `export {includeLog:true}` (NDJSON
ending in `session.exported`) and `replay(baseline, events)`. Replay of an
imported workbook checks the baseline sha256 and byte length and that every
recorded delta is reproduced. A blank workbook has no sha256; replay checks a
zero-length baseline and a known sheet name, then the same recorded deltas.
Undo/redo are intents too: Ctrl+Z undoes this actor's own latest applied,
not-undone intent and is refused with `undo_conflict` (naming the cells and
the overwriting event) if those cells changed since.

The demo keeps no workbook session on Railway. The blank-workbook author/edit/
calculate/export/reopen path uses only static GET requests with no request
bodies in Playwright. The user may open a local `.xlsx`, and **Export** saves
the bytes Forge produces. Univer's model is never serialized. This is scoped
network evidence for the tested path, not a formal privacy audit; remotely
served JavaScript remains part of the trust boundary.

## Command API

`open(bytes)`, `newWorkbook(sheetName)`, `snapshot()`,
`applyEdits(edits, expectedRevision)`, `calculate()`,
`export` with optional `includeLog`, `applyAndCalculate(envelope)`,
`log {fromSeq}`, `subscribe {events}`, and `replay(baseline, events)`. Each
returns `{ok: true, value}` or
`{ok: false, error: {code, message, expected_revision?, current_revision?}}`.
Revisions are decimal strings. Requests and responses are structured-clone
plain data, so a Tauri/wry bridge or in-page MCP tools can carry the same
messages. The worker handles requests in the order they arrive. One worker
holds one workbook, which is one stateless session.

### Alignment with the agent-workbook TypeScript contract

These names and shapes align with the agent-workbook contract: `ExcelError`, `Scalar`
(`null | boolean | number | string | ExcelError`), `Address {sheet, address}`,
and `Edit = Address & ({value} | {formula})`.

Where this API differs, and why:

| Topic | PR #19 (agent-workbook-v1 client) | This spike | Why |
|---|---|---|---|
| Operations | `discover, describe, read, calculate, explain, preview_inputs, set_inputs, edit, export` over a JSON request/response transport | `open, newWorkbook, snapshot, applyEdits, calculate, export, applyAndCalculate, log, subscribe, replay` over postMessage | A worker holding the WASM workbook needs byte-level `open` and `export`. `applyEdits` corresponds to `edit` and `snapshot` to a full `read`. `log`, `subscribe`, `export` with `includeLog`, and `replay` are the memory-only session event log. |
| Revision | `number`, a safe integer | decimal `string` | wasm-bindgen passes `u64` as `BigInt`. The string form never loses precision and needs no BigInt in hosts. |
| Errors | thrown `ClientError(code, message, uncertain)` | returned `{ok:false, error:{code, message, expected_revision, current_revision}}` | Results cross a worker boundary, and conflicts carry both revisions. |
| `Edit` styles | allowed | omitted | Imported workbooks reject style edits (`validate_edits`). |
| Calculation | `outputs` (bound outputs), `evaluated_cell_count`, diagnostics with counts | `values` for every cell as `Sheet!A1`, `stale`, `diagnostics` | The view needs every formula cell's value, not only bound outputs. |
| Reads | paged `read` with dependencies | one full `snapshot` | The spike fixture is small. Paging is the obvious next step for large sheets. |

The adapter and worker share one TypeScript contract for `Scalar`/`Address`/
`Edit`. A second agent-workbook message family is not implemented; the local
MCP host-to-tab round trip remains unproven.

## WASM contract changes (`rust/src/wasm.rs`)

These are additive. The existing methods are unchanged.

- `BrowserWorkbook.revisionString(): string`
- `BrowserWorkbook.applyEditsResultJson(editsJson, expectedRevision: string): string`
  returns `{"ok":true,"revision":"N"}` or
  `{"ok":false,"error":{"code","message","expected_revision","current_revision"}}`.
  It never throws for a rejected edit, and a rejected batch applies nothing.
  Codes: `revision_conflict`, `out_of_bounds`, `unknown_sheet`, `unsupported_edit`,
  `invalid_request`, `invalid_revision`, `edit_rejected`, plus any toolkit
  `code: message` pass-through such as `parse_error`. (`absent_cell` is
  retired: see "Absent-cell authoring on imported workbooks" below.)

The logic lives in `rust/src/browser_contract.rs`, which is compiled for every
target. It is covered natively by `rust/tests/browser_contract.rs` (5 tests)
and by a `wasm_bindgen_test` in `wasm.rs`. No Cargo dependencies were added.

## Formula Lab and XLOOKUP boundary

The blank-workbook path exercises the formula bar and real grid selection with
arithmetic, `SUM`, `IF`, `INDEX`/`MATCH`, and `XLOOKUP`. The Rust and Python
evaluators preserve XLOOKUP's six positional arguments:

```text
=XLOOKUP(lookup_value, lookup_array, return_array, [if_not_found], [match_mode], [search_mode])
```

This demo supports an omitted/blank lookup value, one-dimensional row or
column ranges, exact matching (`match_mode` omitted or `0`), forward or reverse
linear search (`search_mode` omitted, `1`, or `-1`), and an optional scalar
`if_not_found` result. Missing middle arguments keep their positions, so a
reverse search can be written `=XLOOKUP("pear",C1:C3,D1:D3,,,-1)`.

The evaluator currently rejects approximate and wildcard match modes (`-1`,
`1`, `2`), binary search modes (`2`, `-2`), and multi-cell return arrays. It
does not project dynamic array results into neighboring worksheet cells.
Microsoft documents those broader XLOOKUP behaviors, including a return array
that can produce multiple columns; this prototype must not imply that full
compatibility. The official syntax and behavior are described in [Microsoft's
XLOOKUP documentation](https://support.microsoft.com/en-us/excel/functions/xlookup-function).

The OOXML boundary writes `_xlfn.XLOOKUP` and `_xlfn.XMATCH` so Excel recognizes
newer function names when reopening an export, while both Python and Rust
workbook models expose normal formula-bar syntax without that package marker.

## Univer configurations tested

| Mode | Plugins / config | Cell editor + formula bar | Formula display (`f` and Forge `v`) | Univer calculated? |
|---|---|---|---|---|
| `univer-default` (control) | engine-formula + sheets-formula(-ui), stock config | yes | yes | **yes**: on load, `set-trigger-formula-calculation-start` / `set-formula-calculation-result` and `univerAPI.getFormula()` `calculationStart/End` fired |
| `load-only` | sheets-formula `initialFormulaComputing: CalculationMode.NO_CALCULATION` | yes | yes | no, across load, edits, undo and redo |
| **`not-execute` (recommended)** | `load-only` + `UniverFormulaEnginePlugin {notExecuteFormula: true}` + `UniverSheetsPlugin {notExecuteFormula: true}` | yes | yes | no. The engine also does not register its `CalculateController`, so it *cannot* calculate. |
| `engine-only` | engine-formula `{notExecuteFormula: true}`, no sheets-formula(-ui) | **no**: Univer creates no editor units | values and `f` render | no |
| `no-formula-plugins` | no engine-formula / sheets-formula(-ui) plugins | **no**: same | values and `f` render | no |

Findings:

- **The editors depend on sheets-formula.** Without `UniverSheetsFormulaPlugin`
  and `UniverSheetsFormulaUIPlugin`, Univer 1.2 never creates
  `__INTERNAL_EDITOR__DOCS_NORMAL` or `__INTERNAL_EDITOR__DOCS_FORMULA_BAR`, so
  keystrokes never reach an editor. Dropping the formula plugins is therefore
  not viable for an editable grid. `@univerjs/engine-formula` is a hard package
  dependency of `@univerjs/sheets`, so it is bundled whether or not its plugin
  is registered.
- **sheets-formula's `notExecuteFormula` does not mean "don't calculate."** It
  switches sheets-formula to Univer's RPC worker mode and requires
  `IRPCChannelService`. It is left unset, and `initialFormulaComputing` is used
  instead.
- `load-only` alone also showed no calculation, but only because every edit is
  intercepted before Univer's dirty tracking sees it. `not-execute` adds the
  structural guarantee.
- **A value diff alone cannot detect silent recalculation.** In the control,
  Univer's results equal Forge's for this fixture, so the cells look right. The
  mutation and facade spy is what catches it.

## How the adapter keeps Univer non-authoritative

- **Interception.** `ICommandService.beforeCommandExecuted` throws Univer's
  `CustomCommandExecutionError`, which cancels the command:
  - `sheet.command.set-range-values` (the cell editor's and the facade's write
    path) is translated into Forge `Edit`s and is never executed by Univer.
  - `univer.command.undo` / `redo` (keyboard and toolbar) are sent to the
    worker as an `undo` or `redo` intent, unless an in-cell text editor has
    focus. The worker reverts this actor's latest applied, not-undone intent
    on the session event log, or refuses with `undo_conflict` when those cells
    changed since. Undoing an intent whose `before` is absent removes the
    cell; undoing one whose `before` is `{"value": null}` leaves a blank cell.
    Redo re-applies the `after`. Revisions only move forward: 2 → 3 on undo.
  - Every other `sheet.command.*`, `univer.command.*` and `formula*` command is
    blocked with a notice in the page (`#notices`), except a short allow-list
    of view commands (selection, scroll, zoom, sheet activation, copy). This is
    fail-closed: insert/delete rows and columns, sheet add/remove, paste, the
    fill handle (`auto-fill`), clear content, merge, styling and so on are
    refused, never translated into cell edits.
- **Projection.** After every accepted edit the adapter reads Forge's
  `snapshot` and `calculate` at the same revision, then writes changed cells
  as `{v, f}` with one `sheet.mutation.set-range-values` per sheet. Only this
  path may write cells.
- **Audit.** Every mutation is classified. Formula calculation requests and
  outputs, any cell or structure mutation not from the projection, and the
  `univerAPI.getFormula()` `calculationStart/End/ResultApplied` callbacks are
  all recorded. `divergence()` compares Univer's raw and displayed value and
  formula for every Forge cell against the last projection.
- **Rejection.** When Forge refuses an edit, for example `out_of_bounds` for a
  cell past the session's grid, the notice names the code and both revisions,
  and Univer is re-projected from Forge. A rejected cell the projection never
  held is blanked, so editor text does not linger there.

## Absent-cell authoring on imported workbooks

An imported XLSX only materializes the cells its `<sheetData>` holds. Typing
into any other cell of an imported workbook inserts that cell: the edit goes
through the same command envelope as any other, there is no pre-seeding of
placeholder cells, and the UI has no special path for it.

- **Bounds.** Insertion is allowed at rows 1..=1,048,576 and columns
  1..=16,384 (A..XFD), or inside a smaller bound a host declares with
  `ImportedWorkbook::with_insertion_bound(rows, columns)`. Outside it the edit
  is refused with `out_of_bounds` and applies nothing. Cells the workbook
  already holds stay editable wherever they are. A non-anchor cell of a
  `<mergeCell>` range is refused with `unsupported_edit`.
- **Validation** reads the session's current model, not the original import:
  a cell an earlier edit inserted is an existing cell afterwards, and a cell an
  undo made absent again is an insertion again.
- **Calculation.** The inserted cell is in the session model, so the calc graph
  is rebuilt with it: formulas whose ranges covered the blank position (for
  example `SUM(A1:A3)` with A2 absent) recalculate, and the calc event's
  `deltas` and the response's `changed` show them.
- **OOXML.** Export keeps the immutable package baseline and patches the
  worksheet part. An inserted cell is placed in its `<row>` in column order
  (middle or end); a missing `<row r="N">` is created between its
  neighbours in row order (inside `<sheetData/>` when the sheet was empty).
  A row that receives a cell is rewritten whole: its attributes and other
  cells are kept byte for byte, edited source cells in it are written inside
  it, and a `spans` attribute widens to cover the new columns. New rows carry
  `spans` only when the sheet's rows already use it. `<dimension ref>` grows
  to cover inserted cells. Text is written as an inline string, the existing
  writer convention; a formula is written with its calculated cache, and the
  calc chain is dropped with `fullCalcOnLoad` as for every export. The new
  cell takes the row's style when the row is `customFormat`, else its
  column's `style`.
- **Absent, blank and cleared.** These stay distinct:
  - *absent*: the workbook does not hold the cell. The event log writes it as
    `{"absent": true}`.
  - *blank / cleared*: the workbook holds the cell with no value. A source
    cell cleared with `value: null` stays in the model and in the XML as an
    empty `<c>` element that keeps its attributes; the log writes it
    as `{"value": null}`.
  - Clearing an absent cell creates a blank model cell (logged `before`
    absent, `after` `{"value": null}`), and export writes nothing for a new
    cell with no value, formula or style, so on disk it stays absent.
  - **Undo rule:** undoing an intent whose `before` is absent removes the cell
    from the model (and so from the export); undoing one whose `before` is
    `{"value": null}` leaves a blank cell. Redo re-applies the `after`. Replay
    applies each applied intent's `after`, including `{"absent": true}`, so it
    reproduces removal exactly. A source cell can never be made absent.
  - Removal is internal (`Edit::restore_absent`, not on the wire): hosts cannot
    delete cells, only undo their insertion.

## Acceptance results

`web/univer-spike/tests/acceptance.spec.ts`, headless Chromium (Playwright
1.64.1 driving system Chromium 156), production build served statically:
**15 passed** (2026-10-27).

For `not-execute` and `load-only`:

1. On load, the five XLSX formulas display Forge values (20, 25, 75, 55, 185)
   with their formulas. The formula bar shows `=B1+5` for C1.
2. Typing `20` into A1 through the real cell editor gives Forge revision 1 and
   the displayed values 40, 45, 135, 95, 335.
3. Typing `=B1+100` into C1 gives revision 2, displayed values 140, 420, 380,
   1000, and `=B1+100` in the formula bar. Forge's snapshot agrees.
4. Ctrl+Z is routed to Forge: revision 3, C1 back to `=B1+5`, F1 = 335.
   Redo gives revision 4.
5. A stale `applyEdits` at revision 3 returns `revision_conflict` with
   `expected_revision: "3"` and `current_revision: "4"`.
6. Forge's `export()` bytes, reopened in a fresh worker, give revision 0, every
   formula including `B1+100`, and values 20, 40, 140, 420, 380, 1000.
7. Univer made no calculation requests, produced no outputs and fired no facade
   events. There were no foreign writes and no divergence.
8. Insert row/column, remove row, insert sheet, auto-fill, paste and clear
   content are all blocked with a visible notice. Sheet shape and revision are
   unchanged.
9. A blank workbook can be created in the browser, edited through the cell
   editor, and recalculated by Forge. The test uses mouse range selection for
   `SUM`, then arithmetic, `IF`, `INDEX`/`MATCH`, and XLOOKUP with a custom
   not-found value, omitted lookup value, and omitted middle mode arguments in
   a six-position reverse-search call. Export/reopen through a fresh worker
   preserves formulas and values. The observed browser requests were GETs with
   no request bodies.
10. Switching to an imported workbook whose valid sheet name contains `!`
    preserves the full sheet name. After Forge rejects an edit past Excel's
    last column (`out_of_bounds`), the workbook projection is restored with
    its Boolean cell still a Boolean, and cells from the previous workbook are
    gone.
12. Imported workbook, absent cells: typing `=SUM(A1:A3)` into absent H1
    shows 10; typing `5` into absent A2, inside that range, gives revision 2
    and repaints H1 to 15. Ctrl+Z (routed to Forge) gives revision 3 with A2
    absent again in Forge and blank in Univer, and H1 back to 10. Export and
    reopen keep H1's formula with A2 absent; after redo, export and reopen
    keep A2 = 5 and H1 = 15. Runs in `not-execute` and `load-only`.
11. Selecting malformed XLSX bytes displays an `open_failed` notice and keeps
    the previous workbook, values, and revision visible. These two recovery
    cases run in both `not-execute` and `load-only` modes.

The control test shows the spy catches Univer's own calculation. The
`engine-only` and `no-formula-plugins` tests document that no editor is created.

Expected values come from independent arithmetic, written in the fixture
generator and the tests, never from either engine.

## Bundle size (Vite 7 production build)

| Part | Raw | Gzip |
|---|---|---|
| Main JS (Univer core, sheets, sheets-ui, docs, UI, React, adapter; engine-formula is pulled in by `@univerjs/sheets`) | 6040 KiB | 1635 KiB |
| Main CSS | 113 KiB | 17 KiB |
| Lazy sheets-formula + sheets-formula-ui (JS+CSS) | 220 KiB | 63 KiB |
| Forge worker JS | 8 KiB | 3 KiB |
| Forge WASM | 1566 KiB | 486 KiB |
| **Loaded at startup** | **7947 KiB** | **2203 KiB** |
| Lazy hyphenation dictionaries (docs; not loaded for en-US) | 4472 KiB | 990 KiB |

Run `npm run build && npm run size` in `web/univer-spike`.

## Licenses

All runtime and dev npm packages are permissive (MIT, Apache-2.0,
BSD-3-Clause, ISC, 0BSD). `npm run check:licenses` checks `package-lock.json`
against the README's accepted SPDX list. The repository's Python policy test
covers Cargo and Python only, so `catalog/dependency-licenses.json` is
unchanged, and no crates were added. **Vite 8 was rejected**: it hard-depends
on `lightningcss` (MPL-2.0), which the policy excludes even for development
tools. Vite 7.3.7 (esbuild/rollup) is used instead.

## Known limits and next steps

- Absent-cell insertion on imported workbooks covers values and formulas.
  Style edits, merged non-anchor cells, and structural edits (insert/delete
  rows or columns) remain refused. A cell inserted next to a table does not
  resize the table.
- XLOOKUP has a bounded subset: exact mode, one-dimensional ranges, scalar
  results, forward/reverse linear search, and optional `if_not_found`. Other
  match/search modes and array spills remain unsupported.
- Clearing content (Delete) is blocked rather than translated into blank-value
  edits. It is a small extension if wanted.
- Projection writes the whole sheet each refresh, which is fine for a spike.
  Large sheets need `changed_cells` from Forge (PR #19 already models this) and
  paged reads.
- The allow-list is for Univer 1.2.0. New command ids in later versions are
  blocked by default, so pin the version and re-run the suite when upgrading.
- The `wasm_bindgen_test` browser run could not create a WebDriver session in
  the spike's arm64 sandbox, although chromedriver itself works. The same
  contract is exercised natively and by the real WASM build in Node and in
  Chromium.

## Running it

```
bash web/bootstrap-sandbox.sh            # optional: toolchain on a fresh Linux box
bash web/forge-engine/build-wasm.sh      # needs wasm-bindgen-cli 0.2.129
cd web/forge-engine && npm test          # in-process engine tests (Node)
cd web/univer-spike && npm ci
npm run typecheck && npm run check:licenses
CHROMIUM_PATH=/usr/bin/chromium npm run test:e2e   # builds, serves dist/, runs Playwright
npm run dev                              # interactive demo; default ?mode=not-execute
```
