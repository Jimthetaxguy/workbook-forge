# Formula Lab user flows

What a person can do in the default view (`not-execute`), and the Playwright
test in `tests/acceptance.spec.ts` that covers each flow. Update this file
when a flow changes. Run the suite with `npm run test:e2e`.

| Flow | What happens | Test |
|---|---|---|
| Open the page | The sample workbook loads; the status line reads `univer-spike-chain.xlsx · All changes calculated · Version 0`. | `toolbar: the default view hides developer controls and ?dev=1 shows them` |
| Type a value or formula | The edit goes to Forge; dependents repaint; the version goes up by one. | `value edit, formula edit, undo/redo, export round trip` |
| Undo, Redo (buttons or Ctrl+Z, Ctrl+Y) | Sent to Forge as intents; the version still moves forward. | same |
| New workbook | A blank sheet, 200 rows by 26 columns; formulas are authored and calculated on the device. | `blank workbook authors new cells, evaluates formulas and exports locally` |
| Open .xlsx | The file is read in the browser. Number formats, bold, italic, font color, fill, alignment, wrap and column widths are shown as saved. A file that cannot be read leaves the current workbook in place. | `display: an opened workbook keeps its number formats, cell styles and column widths, and exports the same values`, `an invalid replacement file leaves the current workbook visible` |
| Download .xlsx | Saves the bytes Forge produces, named after the opened file. | `value edit, formula edit, undo/redo, export round trip` |
| Select a range | The status bar shows Average, Count and Sum when more than one cell is filled. | `status bar: a selection shows Average, Count and Sum, and follows edits and a new workbook` |
| Functions | The side panel lists the functions Forge calculates; search filters by name or description. Suggestions while typing offer the same list. | `functions: the panel lists the catalog, search narrows it, and suggestions offer only what Forge calculates` |
| A formula Forge cannot calculate | The cell stays blank, the Issues button appears with a count, the panel lists the cell and the reason, and clicking an entry selects the cell. Fixing the formula removes it. | `formula issues: refused formulas are counted and listed, clicking one selects its cell, and the button hides when fixed` |
| A formula that returns several cells | The cell stays blank; a notice says why; selecting the cell shows the Array result chip and previews the values in the side panel. Nothing is written to neighboring cells. | `array result: the cell stays blank and selecting it previews the values` |
| An action Forge does not take yet (paste, fill, Delete, insert or remove rows, columns and sheets, formatting) | Nothing changes; a notice says the action is not available yet. | `structural and out-of-scope mutations are blocked with a visible notice` |
| Light and dark theme | Follows the system setting; the toggle switches the page and the grid together. No workbook data or preference is stored; the empty IndexedDB database Univer creates at load holds no entries. | `theme: the toggle switches the frame and the grid together and stores nothing` |
| Phone width (375 px) | Every app bar control is reachable; the side panel covers the grid. | `narrow screen: controls stay reachable at 375px and the panel overlays the grid` |
| Developer panel (`?dev=1`) | Mode selector, raw revision, source state, last refused command id, XLOOKUP subset note. | `toolbar: the default view hides developer controls and ?dev=1 shows them` |

Every flow runs without a request to another origin. The blank-workbook test
asserts that for its whole session.
