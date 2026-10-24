# Univer engine-handoff spike

Status: spike, October 24, 2026. Branch `spike/univer-engine-handoff`. Not a
product decision; it answers one question.

**Question.** Can Univer OSS (Apache-2.0, `@univerjs/*` 1.2.0) act purely as a
presentation and editing layer while Forge, running as WASM in a Web Worker,
is the only calculator and the source of truth?

**Answer: yes, with one specific configuration.** The cell editor and formula
bar need Univer's formula plugins to be registered. Univer's engine can still
be kept from calculating, and the spike shows it never calculated and never
rewrote a value.

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
  src/adapter.ts    command interception, projection, Forge-backed undo/redo,
                    and an audit of Univer calculation and writes
  src/univer-host.ts / formula-plugins.ts   the configurations compared below
  tests/acceptance.spec.ts                  Playwright acceptance suite
fixtures/univer-spike-chain.xlsx            A1 = 10 and five dependent formulas
rust/examples/univer_spike_fixture.rs       writes that fixture
```

The demo keeps nothing between sessions and makes no server calls. The fixture
is inlined at build time, the user may open a local `.xlsx`, and **Export**
saves the bytes Forge produces. Univer's model is never serialized.

## Command API

`open(bytes)`, `snapshot()`, `applyEdits(edits, expectedRevision)`,
`calculate()` and `export()`. Each returns `{ok: true, value}` or
`{ok: false, error: {code, message, expected_revision?, current_revision?}}`.
Revisions are decimal strings. Requests and responses are structured-clone
plain data, so a Tauri/wry bridge or in-page MCP tools can carry the same
messages. The worker handles requests in the order they arrive. One worker
holds one workbook, which is one stateless session.

### Alignment with draft PR #19 (`typescript/`, head 974e0fb, not merged)

These names and shapes are shared with PR #19: `ExcelError`, `Scalar`
(`null | boolean | number | string | ExcelError`), `Address {sheet, address}`,
and `Edit = Address & ({value} | {formula})`.

Where this API differs, and why:

| Topic | PR #19 (agent-workbook-v1 client) | This spike | Why |
|---|---|---|---|
| Operations | `discover, describe, read, calculate, explain, preview_inputs, set_inputs, edit, export` over a JSON request/response transport | `open, snapshot, applyEdits, calculate, export` over postMessage | A worker holding the WASM workbook needs byte-level `open` and `export`. `applyEdits` corresponds to `edit` and `snapshot` to a full `read`. |
| Revision | `number`, a safe integer | decimal `string` | wasm-bindgen passes `u64` as `BigInt`. The string form never loses precision and needs no BigInt in hosts. |
| Errors | thrown `ClientError(code, message, uncertain)` | returned `{ok:false, error:{code, message, expected_revision, current_revision}}` | Results cross a worker boundary, and conflicts carry both revisions. |
| `Edit` styles | allowed | omitted | Imported workbooks reject style edits (`validate_edits`). |
| Calculation | `outputs` (bound outputs), `evaluated_cell_count`, diagnostics with counts | `values` for every cell as `Sheet!A1`, `stale`, `diagnostics` | The view needs every formula cell's value, not only bound outputs. |
| Reads | paged `read` with dependencies | one full `snapshot` | The spike fixture is small. Paging is the obvious next step for large sheets. |

If #19 lands, the adapter can take its `Scalar`/`Address`/`Edit` types from
there, and the worker can answer `agent-workbook-v1` operations as a second
message family. The intent is one TypeScript contract, not two.

## WASM contract changes (`rust/src/wasm.rs`)

These are additive. The existing methods are unchanged.

- `BrowserWorkbook.revisionString(): string`
- `BrowserWorkbook.applyEditsResultJson(editsJson, expectedRevision: string): string`
  returns `{"ok":true,"revision":"N"}` or
  `{"ok":false,"error":{"code","message","expected_revision","current_revision"}}`.
  It never throws for a rejected edit, and a rejected batch applies nothing.
  Codes: `revision_conflict`, `absent_cell`, `unknown_sheet`, `unsupported_edit`,
  `invalid_request`, `invalid_revision`, `edit_rejected`, plus any toolkit
  `code: message` pass-through such as `parse_error`.

The logic lives in `rust/src/browser_contract.rs`, which is compiled for every
target. It is covered natively by `rust/tests/browser_contract.rs` (5 tests)
and by a `wasm_bindgen_test` in `wasm.rs`. No Cargo dependencies were added.

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
  - `univer.command.undo` / `redo` (keyboard and toolbar) go to the adapter's
    Forge history, unless an in-cell text editor has focus. Undo applies the
    recorded inverse edits at the expected revision, so revisions only move
    forward: 2 → 3 on undo.
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
- **Rejection.** When Forge refuses an edit, for example `absent_cell` for a
  cell the imported workbook does not hold, the notice names the code and both
  revisions, and Univer is re-projected from Forge.

## Acceptance results

`web/univer-spike/tests/acceptance.spec.ts`, headless Chromium (Playwright
1.64.1 driving system Chromium 156), production build served statically:
**7 passed**.

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
   unchanged. An edit to absent cell H10 is refused by Forge (`absent_cell`)
   and nothing is written.

The control test shows the spy catches Univer's own calculation. The
`engine-only` and `no-formula-plugins` tests document that no editor is created.

Expected values come from independent arithmetic, written in the fixture
generator and the tests, never from either engine.

## Bundle size (Vite 7 production build)

| Part | Raw | Gzip |
|---|---|---|
| Main JS (Univer core, sheets, sheets-ui, docs, UI, React, adapter; engine-formula is pulled in by `@univerjs/sheets`) | 6038 KiB | 1635 KiB |
| Main CSS | 113 KiB | 17 KiB |
| Lazy sheets-formula + sheets-formula-ui (JS+CSS) | 220 KiB | 63 KiB |
| Forge worker JS | 7 KiB | 3 KiB |
| Forge WASM | 1493 KiB | 466 KiB |
| **Loaded at startup** | **7872 KiB** | **2183 KiB** |
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

- Imported workbooks still reject edits to absent cells, so typing into an
  empty cell is refused visibly. A blank-workbook constructor and new-cell
  insertion remain Rust-side work.
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
