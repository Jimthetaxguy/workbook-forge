# Browser spike: Forge engine + Univer view

- `forge-engine/` is the headless package: Forge WASM in a Web Worker behind a
  typed command API (`open`, `newWorkbook`, `snapshot`, `applyEdits`,
  `calculate`, `export` with `includeLog`, `applyAndCalculate`, `log`,
  `subscribe`, `replay`, `copyFormulas`). The grid's edit loop is
  `applyAndCalculate`; `applyEdits` is still available. `copyFormulas` says
  what a formula becomes when its cell is copied elsewhere and changes
  nothing.
  It has no UI imports, so a WebView shell or in-page MCP tools can reuse it.
  `snapshot` also carries read-only presentation (cell `style`, sheet
  `columnWidths`) so a view can draw a workbook the way it was saved.
- `univer-spike/` is a Univer 1.2 adapter that uses only that API, inside a
  small product shell: app bar, function reference, formula issues, array
  preview, selection totals, light and dark themes. Delete, copy and paste,
  and fill (the handle, Ctrl+D, Ctrl+R) work on values and formulas, and the
  handle continues number, text-and-number and day and month name series.
  [`univer-spike/FLOWS.md`](univer-spike/FLOWS.md) lists the user flows.

Findings, configuration matrix, contract and results:
[`docs/specs/univer-spike.md`](../docs/specs/univer-spike.md).
