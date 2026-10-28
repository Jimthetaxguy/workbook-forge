# Browser spike: Forge engine + Univer view

- `forge-engine/` is the headless package: Forge WASM in a Web Worker behind a
  typed command API (`open`, `newWorkbook`, `snapshot`, `applyEdits`,
  `calculate`, `export` with `includeLog`, `applyAndCalculate`, `log`,
  `subscribe`, `replay`). The grid's edit loop is
  `applyAndCalculate`; `applyEdits` is still available.
  It has no UI imports, so a WebView shell or in-page MCP tools can reuse it.
- `univer-spike/` is a Univer 1.2 adapter that uses only that API.

Findings, configuration matrix, contract and results:
[`docs/specs/univer-spike.md`](../docs/specs/univer-spike.md).
