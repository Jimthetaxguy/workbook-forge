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
  src/univer-host.ts / formula-plugins.ts   the configurations compared below,
                    the grid theme and Forge's function help
  src/general-number.ts / general-display.ts   how a number with no number
                    format is drawn when it is too long for its cell
  src/main.ts, index.html, src/shell.css    the page around the grid (app bar,
                    side panel, status bar, notices)
  src/function-notes.ts                     argument lists and one-line
                    descriptions for the Functions panel and suggestions
  tests/acceptance.spec.ts                  Playwright acceptance suite
  FLOWS.md                                  the user flows the suite covers
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
bodies in Playwright. The user may open a local `.xlsx`, and **Download .xlsx**
saves the bytes Forge produces. Univer's model is never serialized. This is scoped
network evidence for the tested path, not a formal privacy audit; remotely
served JavaScript remains part of the trust boundary.

## Command API

`open(bytes)`, `newWorkbook(sheetName)`, `snapshot()`,
`applyEdits(edits, expectedRevision)`, `calculate()`,
`export` with optional `includeLog`, `applyAndCalculate(envelope)`,
`log {fromSeq}`, `subscribe {events}`, `replay(baseline, events)` and
`copyFormulas(items)`. Each
returns `{ok: true, value}` or
`{ok: false, error: {code, message, expected_revision?, current_revision?}}`.
Revisions are decimal strings. Requests and responses are structured-clone
plain data, so a Tauri/wry bridge or in-page MCP tools can carry the same
messages. The worker handles requests in the order they arrive. One worker
holds one workbook, which is one stateless session.

`copyFormulas(items)` answers what each formula becomes when its cell is
copied `rowDelta` rows down and `columnDelta` columns right: relative
references move and absolute (`$`) parts stay, as in Microsoft's
[reference table](https://support.microsoft.com/en-us/office/switch-between-relative-absolute-and-mixed-references-dfec08cd-ae65-4f56-839e-5f0d8d0baca9).
It reads no workbook, changes nothing and is not logged; the caller sends the
results as ordinary edits. Each answer is `{formula, spelling}` or `{error}`.
The toolkit's own copy (`toolkit::copy_formula`) decides the result and writes
it fully bracketed (`(A2+B2)`). The engine also moves the references in the
text as typed (`A2+B2`, `src/reference-shift.ts`) and returns that with
`spelling: "kept"` only when the toolkit reads it back as the same formula as
its own copy; otherwise it returns the toolkit's text with
`spelling: "bracketed"`. A reference that would leave the grid is an
`invalid_reference` error for that item.

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

- `copyFormulasJson(itemsJson): string` (a free function, no workbook) takes
  `[{"formula","row_delta","column_delta"}]` and returns
  `{"ok":true,"formulas":[{"formula"} | {"error":{"code","message"}}]}` in the
  same order, from `toolkit::copy_formula`. Malformed input is
  `{"ok":false,"error"}`; it never throws.

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
`1`, `2`) and binary search modes (`2`, `-2`). Those refusals are listed under
Formula issues. It does not project dynamic array results into neighboring
worksheet cells. A two-dimensional XLOOKUP return is `#VALUE!` in the cell.
Any other formula whose result is a multi-cell array is left blank in the
grid, with a notice. A 1×1 array result is shown as its single value. This
split matches the Developer panel after `68fe35e`. The blank cell is not the
whole answer: the notice names the cell and its shape, the status bar shows an
**Array result** chip while such a cell is selected, and the side panel lists
the values Forge calculated. This is a preview only. Nothing is written to
neighboring cells, and no spill placement, blocker or spill-reference
behavior is implemented or claimed.

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
  - Delete (`clear-selection-content`), Ctrl+V (`paste-by-short-key`) and the
    fill handle, Ctrl+D and Ctrl+R (`auto-fill`) are each translated into one
    Forge `set_cells` intent and never executed by Univer. See "Clear, paste
    and fill" below.
  - Every other `sheet.command.*`, `univer.command.*` and `formula*` command is
    blocked with a notice in the page (`#notices`), except a short allow-list
    of view commands (selection, scroll, zoom, sheet activation, copy). This is
    fail-closed: insert/delete rows and columns, sheet add/remove, cut, menu
    paste, clearing formats, merge, styling and so on are refused, never
    translated into cell edits. The notice is a plain sentence chosen by
    `refusalText(id)` (for example "Inserting rows and columns is not
    available yet."); the command id is kept in `audit.blocked` and shown in
    the Developer panel.
- **Presentation.** `snapshot` carries each cell's presentation when the
  model has one (`style`: number format, bold, italic, font color, fill,
  horizontal alignment, wrap) and each sheet's `columnWidths`. Both fields are
  additive and optional; `schema_version` and `model_version` are unchanged.
  The adapter turns them into Univer styles when it builds the workbook, and
  `@univerjs/sheets-numfmt` draws the number format. Presentation is read
  only: the value Univer holds stays the number Forge calculated, and
  `divergence()` still compares raw values. Column widths are drawn at 7 px
  per Excel character unit, which is an approximation of Excel's
  font-dependent width, not a measurement. A cell with no number format is
  drawn by the rule in "General numbers" below.
- **Projection.** After every accepted edit the adapter applies the `changed`
  delta from `applyAndCalculate`, then writes those cells as `{v, f}` with one
  `sheet.mutation.set-range-values` per sheet. Reading `snapshot` and
  `calculate` is the open path and the revision-gap refresh, not the
  accepted-edit path. Only this path may write cells.
- **Audit.** Every mutation is classified. Formula calculation requests and
  outputs, any cell or structure mutation not from the projection, and the
  `univerAPI.getFormula()` `calculationStart/End/ResultApplied` callbacks are
  all recorded. `divergence()` compares Univer's raw and displayed value and
  formula for every Forge cell against the last projection.
- **Rejection.** When Forge refuses an edit, for example `out_of_bounds` for a
  cell past the session's grid, the notice names the code and both revisions,
  and Univer is re-projected from Forge. A rejected cell the projection never
  held is blanked, so editor text does not linger there.
- **Editing a cell in place.** When an edit starts from what the cell
  already holds (a double click, F2, or a double tap on a phone) and
  characters are added, Univer 1.2.0 sends the entry as rich text: `v` is
  null and the text is in `p.body.dataStream`, because the characters added
  carry a text color the old ones do not. An entry of two lines (Alt+Enter)
  arrives the same way. The adapter used to read only `v` there, so Forge was
  sent a null value and the cell was emptied: double clicking a cell that
  held 10, typing 5 and pressing Enter cleared it. The adapter now reads the
  text of such an entry as if it had been typed, with a line feed between
  lines; formats in the rich text are not written, as everywhere else. One
  rule is added for a cell holding text that would not be text if typed
  afresh, `007` or `=A1` entered with a leading apostrophe. Univer's editor
  shows such a cell without the apostrophe, so the edited entry arrives
  without it and stays text: `007` and an 8 is the text `0078`, not the
  number 78. That rule is Workbook Forge's own and was not observed in
  Excel, which shows the apostrophe in its editor.

## Clear, paste and fill

Each of these is one Forge intent, so it is one version step and one undo
step, and a refusal changes nothing. They write values and formulas only,
never formats. The pure rules (shapes, tiling, series detection, clipboard
text) are in `src/range-edits.ts` with unit tests in
`test/range-edits.node.test.mjs` (`npm run test:unit`).

- **Delete** clears the contents of every selected cell that holds something.
  Cells that are already empty are not touched.
- **Copy** (Ctrl+C) stays Univer's own command, so the browser clipboard gets
  the text and HTML Univer writes. The adapter also keeps what Forge held in
  the copied range at that moment, keyed by the id Univer puts in the
  clipboard HTML. A copy is a snapshot: editing the source afterwards does
  not change what is pasted.
- **Paste** (Ctrl+V, Cmd+V). When the clipboard still holds this page's own
  copy, values are copied as they are and each formula is what `copyFormulas`
  returns for its new place. Anything else on the clipboard is pasted as
  text: tab-separated columns, one row per line, quoted cells as spreadsheet
  programs write them, and each cell is read exactly as if it had been typed
  there. The page never calls the browser's clipboard read functions (the
  content arrives in the paste event), so the browser does not ask for
  clipboard permission.
- **Paste shape.** One copy at the top-left cell of the selection. When the
  selection is a whole multiple of the copy in both directions, the copy is
  repeated to fill it. This is Workbook Forge's rule; it has not been checked
  against Excel.
- **Fill.** The fill handle, Ctrl+D and Ctrl+R repeat the source over the
  rest of the dragged range in all four directions; formulas move their
  relative references ([Microsoft: fill a formula into adjacent
  cells](https://support.microsoft.com/en-us/office/fill-a-formula-down-into-adjacent-cells-041edfe2-05bc-40e6-b933-ef48c3f308c6)).
  Dragging the handle back over the selection clears the cells it gives up.
  A double click on the handle fills down as far as the column to the left
  (or, if that is empty, the right) has entries. That range is Univer's rule;
  the only Microsoft support for it found was a
  [Q&A answer](https://learn.microsoft.com/en-us/answers/questions/5907196/how-to-propogate-a-formula-through-an-entire-colum),
  not product documentation.
- **Series.** Excel continues a series where the fill handle is dragged from
  two or more numbers, a date, or text ending in a number or naming a day or
  month ([fill data
  automatically](https://support.microsoft.com/en-us/office/fill-data-automatically-in-worksheet-cells-74e31bdd-d993-45da-aa82-35a236c5b5db),
  [enter a series](https://support.microsoft.com/en-us/office/enter-a-series-of-numbers-dates-or-other-items-41e0bbf2-7198-4d78-8545-fdd4709976b4),
  [project values in a series](https://support.microsoft.com/en-us/office/project-values-in-a-series-5311f5cf-149e-4d06-81dd-5aaad87e5400)).
  `planLine` decides each column of a fill down or up, and each row of a fill
  across, on its own. Three kinds are continued:
  - Numbers. Two or more typed numbers continue their straight line: evenly
    spaced numbers keep their step (1, 2 gives 3, 4, 5; 1, 3 gives 5, 7, 9;
    100, 95 gives 90, 85), and unevenly spaced numbers follow their
    least-squares line (3, 5, 8 gives 31/3 then 77/6). Dragging up or left
    runs the line backwards, which is Microsoft's "drag up or to the left" to
    fill in decreasing order ([automatically number
    rows](https://support.microsoft.com/en-us/office/automatically-number-rows-in-excel-76ce49e3-d8d2-459b-bd85-ee1d3973e6e6)).
    Results are ordinary double arithmetic and are not rounded, so a long
    series of fractions may differ from Excel in its last digit.
  - Text ending in a whole number, such as `Item1` or `Product 1`. One cell
    counts up by one (`Item2`, `Item3`, `Item4`); two or more evenly spaced
    cells set the step.
  - Day and month names, short or long (`Mon`, `Monday`, `Jan`, `January`).
    One cell gives the next name (Monday gives Tuesday, Wednesday, Thursday);
    two or more set the step (Monday, Wednesday gives Friday). The list wraps
    round, and the source's capitals are kept.

  Every other series is refused with a notice, because a copy there would
  not match Excel: dates and times (formats are not filled, so a date would
  arrive as a bare number), a line that mixes kinds or has a formula or a
  blank among its series cells, text whose number is not at its end or has a
  leading zero, text numbers that are not evenly spaced, a text series that
  would count below zero, names in mixed case, and a lone `May` (it is in
  both month lists). A single number copies, as it does in Excel ("For the
  series 2, 2, 2, 2..., type 2 in first cell only"). Ctrl+D and Ctrl+R always
  copy.

  Not taken from Microsoft and not observed in Excel: the least-squares rule
  for three or more uneven numbers (Microsoft calls the fill a "linear
  best-fit trend" and states least squares for the Series command), wrapping
  a name list round, running names and text numbers backwards when dragged up
  or left, and keeping the source's capitals.
- **All or nothing.** If any formula in a paste or fill cannot be copied, or
  would point outside the sheet, nothing is written and the notice names the
  cell. Excel would write `#REF!` in that cell instead.
- **Limits.** 20,000 cells per paste or fill. The view's grid grows to hold a
  paste or fill that runs past it (to the last row + 100 and last column + 10,
  capped at Excel's 1,048,576 by 16,384).
- **Two repairs to Univer 1.2.0's selection drawing**, both in the adapter and
  each pinned by an acceptance test that fails without it. The fill handle
  remembers the range of a drag that changed nothing and sends it again when
  the handle is next clicked; the adapter forgets it on every pointer press
  (this reaches two private Univer fields). Leaving an open cell editor by
  clicking a formula cell makes Univer erase that cell's outline after the
  editor has closed, so the cell shows no outline or fill handle; the adapter
  ignores an erase that arrives while no editor is open.

## General numbers

A cell with no number format has Excel's General format. When its number is
too long for the column, the view draws it with the decimals rounded to what
fits. It used to draw scientific notation: in a default 88 px column,
0.08663094803653162 was drawn as 8.6631E-02 and -2683.3114976001525 as
-2.68E+03. They are now drawn as 0.0866309 and -2683.3115 (the default font,
11 pt Arial). A number is never drawn in more than 11 characters, not counting
a minus sign, however wide the column: with room to spare the same two numbers
are drawn as 0.086630948 and -2683.311498, and the formula bar is where the
rest is read.

Only the drawn text changes. The number Forge holds, the number in Univer's
model, the formula bar, a copy and the exported file are untouched, and a cell
with a number format of its own is drawn as before.

**Microsoft's rule.** "Available number formats in Excel"
(https://support.microsoft.com/en-us/office/available-number-formats-in-excel-0afe8f52-97db-41f1-b972-4b46e9f1e8d2)
says of General, for Excel on the desktop: "The default number format that
Excel applies when you type a number. For the most part, numbers that are
formatted with the General format are displayed just the way you type them.
However, if the cell is not wide enough to show the entire number, the General
format rounds the numbers with decimals. The General number format also uses
scientific (exponential) notation for large numbers (12 or more digits)." For
Excel for the web the same page says: "Default number format. If the cell
isn't wide enough to show the entire number, this format rounds the number.
For example, 25.76 shows as 26. Also, if the number is 12 or more digits,
General format displays the value with scientific (exponential) notation." It
also says: "Applying a number format only changes how the number is displayed
and doesn't affect cell values that's used to perform calculations. You can
see the actual value in the formula bar." (The quotations in this section are
Microsoft's words with plain apostrophes and without its bold type. The pages
were read on 2026-11-12.)

The limit of 11 characters and the details of the switch to scientific
notation are from the Office Open XML standard's notes on the General format
(ISO/IEC 29500-1), as Microsoft prints them on Microsoft Learn under
"NumberingFormat Class"
(https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.spreadsheet.numberingformat?view=openxml-3.0.1):
"The application shall attempt to display the full number up to 11 digits
(inc. decimal point). If the number is too large, the application shall
attempt to show exponential format. If the number has too many significant
digits, the display shall be truncated. The optimal method of display is based
on the available cell width. If the number cannot be displayed using any of
these formats in the available width, the application shall show "#" across
the width of the cell." On when to switch: "The cell value shall have at least
five digits for xE-xx. If the exponent is bigger than the size allowed, a
floating point number cannot fit, so try exponential notation. Similarly, for
negative exponents, check if there is space for even one (non-zero) digit in
floating point format. Finally, if there isn't room for all of the significant
digits in floating point format (for a negative exponent), exponential format
shall display more digits if the exponent is less than -3. (The 3 is because
E-xx takes 4 characters, and the leading 0 in floating point takes only 1
character. Thus, for an exponent less than -3, there is more than 3 additional
leading 0's, more than enough to compensate for the size of the E-xx.)" And,
headed "Floating point rule": "For general formatting in cells, max overall
length for cell display is 11, not including negative sign, but includes
leading zeros and decimal separator." Microsoft's troubleshooting article "Long numbers are
displayed incorrectly in Excel"
(https://learn.microsoft.com/en-us/troubleshoot/microsoft-365-apps/excel/long-numbers-incorrectly-in-excel)
gives the same figure: "Default number format in Excel is General therefore
you can display up to 11 digits in a cell."

**The rule the view draws by** (`generalNumberText` in
`src/general-number.ts`, a pure function with unit tests in
`test/general-number.node.test.mjs`):

1. A number whose whole-number part has 12 or more digits is drawn in
   scientific notation, whatever the column width: 123456789012 is
   1.23457E+11.
2. Any other number is drawn as typed when that is at most 11 characters,
   not counting a minus sign, and fits: 12345678901 and 1234567.891.
3. Otherwise the decimals are rounded to the longest text of at most 11
   characters that fits. In a narrow column 25.76 becomes 25.8 and then 26. In
   a column of any width 2683.3114976001525 is 2683.311498,
   0.08663094803653162 is 0.086630948 and pi is 3.141592654. Closing zeros
   are not drawn.
4. Scientific notation is used instead, with the longest mantissa that fits in
   the same 11 characters, when the whole-number part does not fit (12345678901
   in a default column is 1.235E+10), when the rounded decimals would be all
   zeros, or when the number is below 0.001 and scientific notation has room
   for more of its digits (0.0000123456789 in 11 characters is 1.23457E-05,
   not 0.000012346).
5. A number that rounds to zero and has room for neither form is drawn as 0.
6. Anything else is drawn as number signs across the cell.

**Which parts are Workbook Forge's own.** These are not stated by Microsoft
and none was observed in Excel:

- "12 or more digits" is read as the digits of the whole-number part.
- The 11 characters are applied to scientific notation as well
  (1.23457E+11). The standard states them under its floating point rule and
  gives no length for the exponential form.
- "Fits" means the text and one more digit of clearance fit in the column
  less 2 px of padding on each side. This is Univer's own test for a General
  number that is too long, kept so the view has one notion of fitting. Excel
  measures in its own font and padding, so the number of decimals shown in a
  column of the same nominal width can differ by one or two.
- Rounding is half away from zero, done on the number's shortest decimal
  digits (2.675 in room for four characters is 2.68). The standard says "the
  display shall be truncated"; Microsoft's Support page says "rounds" and
  gives 25.76 as 26, which is what is followed.
- Whether scientific notation "shows more digits" is decided by counting how
  many of the number's digits each form has room for; a tie stays decimal.
  The standard does not say how its "exponent" is counted. Counted as in
  1.23E-04, 0.000123456789 has an exponent below -3, yet both forms have room
  for six of its digits (0.000123457 and 1.23457E-04), so the decimal form
  stays; the standard's own sum (four characters for E-xx against "more than
  3 additional leading 0's") starts the switch at four zeros after the
  decimal point, which is the same answer. 0.0000123456789 is 1.23457E-05 on
  either reading.
- The standard's "at least five digits for xE-xx" is read as the five
  characters the shortest scientific text takes (1E-05); a text that does not
  fit is not drawn, and no separate test is made.
- 0, with no minus sign, for a number below one half that fits in no other
  form. Before this change the view drew 0 there for any number below one; a
  number from one half up now rounds to 1 or shows number signs.
- The number of number signs: as many as fit in the padded width, at least
  one. The view already drew them this way.
- A very small number that fits is written out (0.000000123), not drawn as
  the 1.23E-7 Univer hands the renderer.

**Where the scientific notation came from, and the hook.** It was Univer's own
drawing, not the adapter. In `@univerjs/engine-render` 1.2.0,
`SpreadsheetSkeleton._applyNumberDisplay` calls `getGeneralNumberDisplayText`,
which, for a General number whose text is too wide, tries only the patterns
`0E+00`, `0.#E+00`, `0.##E+00` and so on, then 0 for a number below one, then
number signs. Univer has no setting for this and no public hook at that step.
Its one public hook nearby, the `CELL_CONTENT` interceptor that
`@univerjs/sheets-numfmt` uses, changes what every reader of the cell gets,
including a copy and `divergence()`, so it cannot be used to change the
drawing alone. `src/general-display.ts` therefore wraps the private method on
the prototype: Univer's step runs first, then the text for a General number
cell is replaced by the rule above, measured with Univer's own text
measurement. `installGeneralNumberDisplay()` returns false when the method is
missing, the host exposes that as `generalNumberDisplay`, and the first
General-number acceptance test asserts it is true and that the drawn text is
the rounded decimal, so an upgrade that moves the method fails the suite.
Nothing in `node_modules` is patched.

The number that is rounded is the one the cell holds, not the one Univer
shows. Univer trims floating-point noise from a General number before showing
it (to 12 significant digits when that is within 1e-10 of the number,
otherwise to 15). Rounding the trimmed number again can go wrong:
1.23456789049999 is trimmed to 1.2345678905 and would then round up to
1.234567891, where the number itself rounds to 1.23456789. So the drawing
reads the cell's stored number (`worksheet.getCellRaw`, public in Univer)
whenever it is the shown number to within the trim, and the shown number
otherwise. Univer's formula bar and the text it puts on the clipboard still
show the trimmed number: 0.0866309480365 for 0.08663094803653162. That was so
before this change and is not changed by it. Forge's number, Univer's raw cell
value, a paste on the same page and the export keep every digit.

**Correction of 2026-11-12.** As first committed, rule 2 drew a number as
typed whenever it fitted, even beyond 11 characters (2683.3114976 in a wide
column), following the owner's brief. The standard's notes cap General at 11
characters at any width, and the primary source outranks the brief, so the cap
now applies everywhere. The same commit moved the rounding from Univer's shown
number to the stored number.

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
**15 passed** on 2026-10-27. That figure is the snapshot from that day's run.
The suite in this file now defines 40 tests. The six added with the product
shell are listed after item 12, the ten added with clear, paste and fill and
the one added with series fill after item 18, the three added with General
numbers after item 28, and the one added with editing in place after item
31; a run of the first 39 is recorded in `docs/run-history.md` (Run 31) as
branch-local evidence.

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
8. Insert row/column, remove row, insert sheet, cut, menu paste, fill
   options, clearing formats and a formatting change are all blocked with a
   visible notice in plain words, and the command id is recorded in
   `audit.blocked`. Sheet shape and revision are unchanged.
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

Added with the product shell (default `not-execute` view):

13. Display. Opening `fixtures/corpus/op_styles_number_formats.xlsx` shows
    `2024-02-29`, `12.75%`, `$1,234.50`, `-42.75`, `3/8`, `9,877` and `42:00`
    for the stored numbers, bold blue labels, the yellow fill and right
    alignment, and column widths of 126 and 154 px. Expected strings are read
    from the fixture's XML (stored value plus format code), not from an
    engine. Forge still holds 45351, 0.1275 and -42.75, and there is no
    divergence.
14. Status bar. Selecting A1:A3 of 10, 20.5 and a text cell shows Average
    15.25, Count 3, Sum 30.5 (independent arithmetic); the totals follow an
    edit and clear on a new workbook.
15. Functions. The panel lists exactly the catalog's implemented functions,
    search narrows it, and the in-cell suggestion list offers XLOOKUP in
    Forge's words and offers nothing for `=bessel` (functions Univer knows and
    Forge does not implement).
16. Array result. `=SEQUENCE(3,2)` leaves the cell blank, raises the notice,
    and selecting the cell previews 1 to 6 in three rows of two (Microsoft's
    SEQUENCE definition).
17. Theme. The toggle switches the page and the grid together. Afterwards
    `localStorage`, `sessionStorage` and cookies are empty, and every
    IndexedDB store holds zero entries.
18. Narrow screen. At 375 px every app bar control is on screen and the side
    panel covers the grid at full width.

Added with clear, paste and fill (default `not-execute` view). After every one
of these tests the suite also checks: no divergence, no cell write that did
not come from the projection, no call to the browser's clipboard read
functions, and no calculation by Univer.

19. Delete. Clearing a selected range is one version step; dependents
    recalculate; Ctrl+Z restores every cell in one step.
20. Text paste. Tab-separated text, including a quoted cell that contains a
    line break, lands as if typed (numbers as numbers, `=` entries as
    formulas) in one version step.
21. Formula paste. `=$A$1+A$1+$A1+A1` copied from D3 to F5 becomes
    `=$A$1+C$1+$A3+C3`, `=B4*C4` copied one row down becomes `=B5*C5` and
    `=$B$4*$C$4` is unchanged: the three cases in Microsoft's reference
    article. Editing the source after the copy does not change the paste.
22. Ctrl+R and Ctrl+D copy across and down, and both key presses are
    `defaultPrevented`, so the browser's own Ctrl+D and Ctrl+R do not fire.
23. Fill handle. Dragging copies formulas down, right, up and left. A drag
    left whose formula would point before column A is refused and changes
    nothing, and the next drag works.
24. Fill handle gestures. A double click fills down beside the neighboring
    column as one step; dragging back clears; after a drag that changed
    nothing, a click on another cell's handle does nothing.
25. Leaving an editor. After a double click opens an editor, or a value is
    typed, clicking a formula cell and dragging its handle fills on the first
    drag; a formula that is still being typed can still pick a cell by click.
26. Series. Dragging from 1, 3 fills 5, 7, 9 as one undoable step; `Item1`
    fills `Item2` to `Item4`; `Monday` fills Tuesday to Thursday across a
    row; dragging up from 5, 6 fills 4 and 3; a single 2 copies; dragging
    `Item1` up two cells is refused whole; `Q1 2024` and a date cell are
    refused with the series notice; Ctrl+D copies 1, 3 as they are.
27. Refusals. Cut is refused; a paste whose formula would point outside the
    sheet changes nothing and names the cell.
28. Shape. A 1 by 2 copy fills a 2 by 4 selection; a selection that is not a
    whole multiple gets one copy; a 300-row paste ending at row 309 grows
    the grid to 409 rows.

Expected values in items 19 to 28 are Microsoft's published examples or
arithmetic done by hand in the test, never an engine's output.

General numbers (see "General numbers" above). These tests record every text
the grid canvas is asked to draw during a full repaint.

29. Default column. In a new workbook, 0.08663094803653162,
    -2683.3114976001525 and 2683.3114976 typed into 88 px cells are each
    drawn as a shorter decimal and never in scientific notation; 25.76 is
    drawn as typed; 123456789012 and `=A3*10` (13 digits) are drawn in
    scientific notation and nothing else is. Univer's raw values, Forge's
    snapshot, a paste of a copy and the reopened export hold every digit; the
    formula bar shows 2683.3114976 in full and at least 12 significant digits
    of the two long numbers. The host reports that the drawing rule is
    installed.
30. Wide column. In `op_styles_number_formats.xlsx`, 2683.3114976 is drawn as
    2683.311498 in the 154 px column and shorter in the 88 px column beside
    it. In the same wide column 0.08663094803653162 is drawn as 0.086630948,
    -2683.3114976001525 as -2683.311498, 0.0000123456789 as 1.23457E-05,
    `=PI()` as 3.141592654, 12345678901 and 1234567.891 as typed,
    123456789012 as 1.23457E+11 and 1.23456789049999 as 1.23456789. No number
    is drawn in more than 11 characters besides a minus sign, and the cells
    with number formats draw the same text as before (2024-02-29, 12.75%,
    $1,234.50, 6.02E+23 and the rest).
31. Narrow column. In a 34 px column, 25.76 is drawn as 26 (Microsoft's
    example), 123456 as number signs and 0.0866 as the longest rounding that
    fits. The view cannot resize a column, so the test narrows one with
    Univer's own column-width change, which writes no cell.

In items 29 to 31 each expected text is the longest entry, of a list rounded
by hand in the test, that the stated fitting rule lets into the column,
measured in the test with the font the text was drawn in; the wide-column
texts of item 30 are the numbers cut to 11 characters by hand. None is taken
from Univer's output or from either engine. With the drawing rule taken out,
all three fail, and with the rounding put back on Univer's shown number item
30 fails on 1.23456789049999 (both checked by doing it).

Editing in place (see "How the adapter keeps Univer non-authoritative").

32. Editing in place. A double click on A1 (10), a typed 5 and Enter make
    A1 105 in one version step, and the chain follows (210, 215, 645, 435,
    1610). F2 on the text `north` and ` east` gives `north east`; F2 on
    `=A1*2` and `+1` gives `=A1*2+1`, 211. Removing everything in place
    empties the cell. `007` entered with a leading apostrophe, then F2 and
    an 8, is the text `0078`. Two lines typed with Alt+Enter are kept with a
    line feed between them. Expected values are hand arithmetic. With the
    adapter change taken out the test fails at its first step (checked by
    doing it).

**Repaint latency gate.** A typed commit into A1 of the event-log fixture
must repaint its dependent B1 in under 100 ms, measured from the adapter's
commit timestamp to the first animation frame that shows the new value. The
gate is a dedicated test that makes one unmeasured warm-up commit and then
five measured ones, and asserts the **median** is under 100 ms and no single
sample reaches 1000 ms. The event-log test still records its own first
commit's latency as an annotation but no longer gates on it: one sample right
after load carries WASM and JIT warm-up and a busy host's scheduling noise,
and failed on an unchanged main. The median still fails whenever the typical
commit-to-paint path is slower than 100 ms. `SPIKE_REPAINT_GATE_MS` sets a
different budget for a host known to be slower; the results above use the
default. On a 2-core shared arm64 sandbox the warmed samples were 112-149 ms
(median 128 ms), so there the gate fails on main too: that is host speed, not
noise, and the median does not hide it.

The control test shows the spy catches Univer's own calculation. The
`engine-only` and `no-formula-plugins` tests document that no editor is created.

Expected values come from independent arithmetic, written in the fixture
generator and the tests, never from either engine.

## Bundle size (Vite 7 production build)

| Part | Raw | Gzip |
|---|---|---|
| Main JS (Univer core, sheets, sheets-ui, docs, UI, React, adapter; engine-formula is pulled in by `@univerjs/sheets`) | 6085 KiB | 1651 KiB |
| Main CSS | 123 KiB | 19 KiB |
| Lazy sheets-formula + sheets-formula-ui + sheets-numfmt (JS+CSS) | 232 KiB | 67 KiB |
| Forge worker JS | 18 KiB | 6 KiB |
| Forge WASM | 2327 KiB | 689 KiB |
| **Loaded at startup** | **8785 KiB** | **2432 KiB** |
| Lazy hyphenation dictionaries (docs; not loaded for en-US) | 4472 KiB | 990 KiB |

Measured on 2026-11-12 with clear, paste and fill. Against the product-shell
build (8747 KiB raw, 2421 KiB gzip at startup) they add 11 KiB to the main
script, 2 KiB to the worker and 25 KiB to the WASM (`copyFormulasJson`).

General number drawing adds 3 KiB raw (2 KiB gzip) to the main script,
measured on 2026-11-12 by building with and without it.

Run `npm run build && npm run size` in `web/univer-spike`.

## Licenses

All runtime and dev npm packages are permissive (MIT, Apache-2.0,
BSD-3-Clause, ISC, 0BSD), including `@univerjs/sheets-numfmt` and
`@univerjs/themes` (both Apache-2.0, 1.2.0). The page uses system font stacks
only: no font files are bundled or fetched. `npm run check:licenses` checks `package-lock.json`
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
- Paste and fill write values and formulas, never formats. Cut, pasting from
  a menu, Paste Special and clearing formats are refused with a notice. The
  fill handle continues numbers, text ending in a number and day and month
  names; date and time series, growth series and other patterns are refused.
- Pasted text is read as if typed, so text such as `1,234`, `12%` or a date
  stays text, exactly as typing it does today.
- A formula that would point outside the sheet refuses the whole paste or
  fill; Excel writes `#REF!` in that cell. Formulas the toolkit's copy does
  not cover (array constants, whole-column or whole-row references, defined
  names, table references, `A1#`) cannot be copied and refuse the action the
  same way.
- Copied formulas keep the function-name case as typed. When the typed
  spelling cannot be confirmed the copy is written fully bracketed.
- Repeating a copy over a larger selection and the tiling order when filling
  up or left are Workbook Forge's rules and have not been observed in Excel.
- The two selection repairs depend on Univer 1.2.0 internals. Their tests fail
  if a Univer upgrade moves them.
- General number drawing wraps a private method of Univer 1.2.0's renderer
  (`SpreadsheetSkeleton._applyNumberDisplay`). Its acceptance test fails if an
  upgrade moves it, and the view then falls back to Univer's own drawing. The
  rule was not observed in Excel; the parts that are Workbook Forge's own are
  listed under "General numbers". Text copied to another program and the
  formula bar show Univer's trimmed number (12 to 15 significant digits), not
  all 17 digits a double can carry.
- `divergence()` reports a "displayed" difference for every General number
  with more than 12 significant digits (0.08663094803653162, `=1/3`,
  `=PI()`), because Univer trims the number it shows. The raw value still
  matches Forge. This is older than General number drawing and is not changed
  by it; the comparison should allow for the trim.
- Presentation is read only. Number formats, bold, italic, font color, fill,
  horizontal alignment, wrap and column widths are shown; borders, row
  heights, merged-cell drawing, conditional formats and theme colors are not.
  Formatting commands are refused.
- A formula that uses a function outside the catalog leaves its cell blank and
  is listed under Formula issues. Excel would show `#NAME?` for a name it does
  not know; this view does not imitate that.
- Univer's suggestion list stays open for 300 ms after the last keystroke, and
  Enter picks the highlighted suggestion while it is open. Typing a whole
  formula and pressing Enter within that window after a function-name prefix
  completes the name instead of committing. This is Univer 1.2.0 behavior and
  is the same on the previous build.
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
npm run test:unit                        # paste, fill and General number rules (Node, no browser)
npm run typecheck && npm run check:licenses && npm run check:functions
CHROMIUM_PATH=/usr/bin/chromium npm run test:e2e   # builds, serves dist/, runs Playwright
npm run dev                              # interactive demo; always not-execute (?dev=1 for the Developer panel)
```

### Page chrome

The default URL is the user view and always runs `not-execute`.

- **App bar.** The name, a status line such as `univer-spike-chain.xlsx · All
  changes calculated · Version 0` (the version is the Forge revision), Undo,
  Redo, New workbook, Open .xlsx, Download .xlsx, Functions and the theme
  toggle. **Issues** appears with a count only while Forge reports
  calculation diagnostics.
- **Grid.** Univer's formula bar and canvas, themed to match the page.
  Delete, Ctrl+C, Ctrl+V, Ctrl+D, Ctrl+R and the fill handle work on values
  and formulas. Univer's own toolbar and context menu are off: nearly every
  action they offer is one Forge would refuse.
- **Side panel.** One of three views. **Functions** lists the functions Forge
  calculates, by category, with argument lists and one-line descriptions from
  `src/function-notes.ts`, searchable by name or description, and states how
  many of Microsoft's listed functions that is. `npm run check:functions`
  fails when a note disagrees with the catalog's argument counts. **Formula
  issues** lists each diagnostic; clicking one selects its cell. **Array
  result** previews a multi-cell result (see the XLOOKUP section).
- **Status bar.** The on-device note, the Array result chip, and Average,
  Count and Sum for a selection with more than one filled cell. The totals are
  computed in the page from the values Forge projected, for display only.
- **Notices.** A toast. Information fades after nine seconds; an error stays
  until dismissed.
- **Theme.** Light or dark from the system setting, with a toggle. The choice
  is kept in memory for the visit. No workbook data or preference is written
  to browser storage. Univer 1.2.0's interface package does create an
  IndexedDB database named `UniverLocalStorage` when the page loads; it stays
  empty here, because the Univer features that write to browser storage (the
  emoji picker and the mobile function panel) are not mounted on this page.
  Acceptance item 17 checks that it holds no entries.
- **Suggestions.** In every mode where Forge calculates, the in-cell
  suggestion list and argument help are replaced with Forge's own function
  list and wording, so the editor does not suggest a function Forge would
  refuse. `univer-default` keeps Univer's list.

Below 720 px the side panel covers the grid; below 480 px it is full width and
the button labels collapse to icons.

`?dev=1`, or any `?mode=` other than `not-execute`, adds a collapsed
**Developer** panel: the mode selector, the raw revision (`Forge r0 · mode
…`), the source state (built-in sample, opened file or new workbook), the id
of the last refused command, and the XLOOKUP subset note. The Playwright suite
boots the comparison modes with `?mode=<mode>`.
