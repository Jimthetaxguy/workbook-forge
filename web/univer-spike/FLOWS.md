# Formula Lab user flows

What a person can do in the default view (`not-execute`), and the Playwright
test in `tests/acceptance.spec.ts` that covers each flow. Update this file
when a flow changes. Run the suite with `npm run test:e2e`.

| Flow | What happens | Test |
|---|---|---|
| Open the page | The sample workbook loads; the status line reads `univer-spike-chain.xlsx · All changes calculated · Version 0`. | `toolbar: the default view hides developer controls and ?dev=1 shows them` |
| Type a value or formula | The edit goes to Forge; dependents repaint; the version goes up by one. | `value edit, formula edit, undo/redo, export round trip` |
| Edit a cell in place (double click or F2, then type) | The characters are added to what the cell holds: 10 and a typed 5 is 105, as one version step. Text entered with a leading apostrophe stays text (`007` and an 8 is `0078`). Two lines typed with Alt+Enter are kept. | `editing a cell in place keeps the entry: double click or F2, then add characters` |
| Undo, Redo (buttons or Ctrl+Z, Ctrl+Y) | Sent to Forge as intents; the version still moves forward. | same |
| New workbook | A blank sheet, 200 rows by 26 columns; formulas are authored and calculated on the device. | `blank workbook authors new cells, evaluates formulas and exports locally` |
| Open .xlsx | The file is read in the browser. Number formats, bold, italic, font color, fill, alignment, wrap and column widths are shown as saved. A file that cannot be read leaves the current workbook in place. | `display: an opened workbook keeps its number formats, cell styles and column widths, and exports the same values`, `an invalid replacement file leaves the current workbook visible` |
| A number too long for its column | A cell with no number format draws the number with its decimals rounded to what fits (25.76 as 26), and in at most 11 characters however wide the column (2683.3114976 as 2683.311498). Scientific notation is used only when the whole-number part does not fit or has 12 or more digits, and number signs fill the cell when nothing fits. Only the drawing changes: the formula bar, a copy and the download keep the number. Cells with a number format are drawn as before. | `a number too long for its column is drawn with rounded decimals, and in scientific notation only from 12 digits`, `a wide column draws a long number in at most 11 characters, and cells with a number format are drawn as before`, `a narrow column draws 25.76 as 26, and number signs for a number with no room` |
| Download .xlsx | Saves the bytes Forge produces, named after the opened file. | `value edit, formula edit, undo/redo, export round trip` |
| Select a range | The status bar shows Average, Count and Sum when more than one cell is filled. | `status bar: a selection shows Average, Count and Sum, and follows edits and a new workbook` |
| Functions | The side panel lists the functions Forge calculates; search filters by name or description. Suggestions while typing offer the same list. | `functions: the panel lists the catalog, search narrows it, and suggestions offer only what Forge calculates` |
| A formula Forge cannot calculate | The cell stays blank, the Issues button appears with a count, the panel lists the cell and the reason, and clicking an entry selects the cell. Fixing the formula removes it. | `formula issues: refused formulas are counted and listed, clicking one selects its cell, and the button hides when fixed` |
| A formula that returns several cells | The cell stays blank; a notice says why; selecting the cell shows the Array result chip and previews the values in the side panel. Nothing is written to neighboring cells. | `array result: the cell stays blank and selecting it previews the values` |
| Delete | Clears the contents of the selected cells as one step; Ctrl+Z brings them back. | `Delete clears the selected cells as one step and Ctrl+Z brings them back` |
| Copy and paste (Ctrl+C, Ctrl+V) | A copy made on this page pastes its values and formulas; relative references move and `$` references stay. Text from another program lands as if typed. One copy at the top left of the selection, repeated when the selection is a whole multiple of it. The grid grows to hold a long paste. Formats are not pasted. The page never asks to read the clipboard. | `a copied formula moves its relative references and keeps its absolute ones`, `text pasted from another program lands as if typed, as one step`, `a copy repeats over a selection it fits a whole number of times, and a long paste grows the grid` |
| Copy pressed before the change just made (a Delete, a typed entry, a paste, a fill, an undo) has come back from Forge | Nothing is copied and the clipboard is left as it was; a notice says to copy again. This keeps a copy from holding cells that were just deleted. | `a copy pressed while Delete is still being applied is refused and writes nothing to the clipboard, so a paste cannot bring the deleted cells back` |
| Fill (the fill handle, Ctrl+D, Ctrl+R) | Copies formulas and repeated values down, right, up or left as one step. A double click on the handle fills down beside the neighboring column. Dragging back over the selection clears what it gives up. | `the fill handle copies formulas down, right, up and left, and a refused drag changes nothing`, `a double click on the fill handle fills down, dragging it back clears, and a click on it does nothing`, `Ctrl+R and Ctrl+D copy across and down, and the browser does not see the keys`, `a formula cell clicked while another cell is being edited keeps its outline and fill handle` |
| A fill from two or more numbers, text ending in a number, or a day or month name | Continues the series as one step: 1, 3 gives 5, 7, 9; Item1 gives Item2, Item3; Monday gives Tuesday, Wednesday. Dragging up or left runs it backwards. A single number copies. | `the fill handle continues number, text and name series, copies a single number and refuses other patterns` |
| A fill Excel would continue in a way this page does not (a date or time, `Q1 2024`, mixed cells) | Nothing changes; a notice says which series are available. | `the fill handle continues number, text and name series, copies a single number and refuses other patterns`, `the fill handle refuses a date, which Excel would continue day by day` |
| Pasted text whose rows have different lengths | The paste is as many rows as the text has and as wide as its longest row; a cell a short row does not reach is written as empty. If that is more than 20,000 cells nothing changes and the notice says so, however small the text is. | `uneven text pastes its short rows as empty cells, and uneven text over the limit is refused with the usual message`; unit tests `clipboard text: uneven text far over the paste limit is measured without building its rectangle`, `clipboard text: 200,000 one-cell rows are read, where spreading them into Math.max would throw` |
| A paste or fill whose formula would point outside the sheet | Nothing changes; the notice names the cell. | `cut is refused, and a paste whose formula would leave the sheet changes nothing` |
| An action Forge does not take yet (cut, menu paste, clearing formats, insert or remove rows, columns and sheets, formatting) | Nothing changes; a notice says the action is not available yet. | `structural and out-of-scope mutations are blocked with a visible notice`, `cut is refused, and a paste whose formula would leave the sheet changes nothing` |
| Light and dark theme | Follows the system setting; the toggle switches the page and the grid together. No workbook data or preference is stored; the empty IndexedDB database Univer creates at load holds no entries. | `theme: the toggle switches the frame and the grid together and stores nothing` |
| Phone width (375 px) | Every app bar control is reachable; the side panel covers the grid. | `narrow screen: controls stay reachable at 375px and the panel overlays the grid` |
| Tap a cell on a phone or tablet | Selects it and shows its content in the formula bar. The focused editor carries `inputmode="none"`, which asks the browser to keep its keyboard closed. Emulated touch only; see "Phone check list" below. | `a tap selects a cell and leaves the focused editor on inputmode="none" at 16px; whether a real keyboard stays closed is not tested` |
| Start typing on touch: double tap, the Edit button, or a tap in the formula bar | The editor switches to `inputmode="text"` and takes the focus inside the tap. Enter or the check mark commits as one version step; the X leaves the cell as it was; either way the editor returns to `inputmode="none"`. | `a double tap switches the editor to inputmode="text", and Enter commits a value in one version step and switches back`, `the Edit button starts typing in the cell, and the check mark commits a formula in one version step`, `a tap in the formula bar starts typing there; the X leaves the cell unchanged, and Enter commits` |
| Type with an on-screen keyboard open | The shell is sized to the visible area: the formula bar and the cell being edited stay above the keyboard, the app bar and status bar step aside when less than 520 px is visible, and everything returns when the keyboard closes. Tested with a shortened visual viewport and with a shortened window, not with a keyboard. | `visible area cut to half by replacing visualViewport.height (as iOS reports a keyboard): the formula bar and the cell being edited stay inside it, and the layout returns`, `window made half as tall (as Chrome on Android resizes the page for a keyboard): the formula bar and the cell being edited stay inside it` |
| Swipe the grid | Scrolls it both ways with a short glide; the page does not move and the selection does not change. | `a swipe scrolls the grid up, down and sideways without moving the page or selecting, and the next tap selects the cell under the finger` |
| Hold a finger on a cell, then drag; two fingers on the grid | Selects a range (from the fill handle, fills); two fingers zoom the grid from 50 to 300 percent. | `a finger held still and then moved selects a range, and two fingers zoom the grid while the page scale stays 1` |
| Touch sizes | On a device whose main pointer is a finger, controls are at least 44 by 44 px with 8 px between them and text that takes the focus is 16 px. The workbook's row heights and column widths are not changed. | `controls are at least 44 by 44 CSS px with 8px between them at 375px, also while typing and with the panel open` |
| A touch screen and a mouse on one device | The last pointer decides: a tap gets `inputmode="none"`, a mouse click removes it. With a mouse nothing differs from before. | `the last pointer decides: a mouse click takes inputmode off the editors and a tap puts it back`, `mouse and keyboard are unchanged: no inputmode, no Edit button, and the bars keep their sizes` |
| Developer panel (`?dev=1`) | Mode selector, raw revision, source state, last refused command id, XLOOKUP subset note. | `toolbar: the default view hides developer controls and ?dev=1 shows them` |

Every flow runs without a request to another origin. The blank-workbook test
asserts that for its whole session.

## Phone check list

The touch flows above were tested with an emulated touch screen in headless
Chromium, which has no on-screen keyboard and is not Safari. Nothing below
has been run on a phone. Run it on an iPhone in Safari and on an Android
phone in Chrome, in portrait, and note the step number of anything that
differs. `docs/specs/univer-spike.md` ("Touch and phone use") has the
reasons.

1. Open the page. Expected: the sample workbook shows, no keyboard opens,
   and the page is not zoomed in.
2. Tap cell B1 once. Expected: B1 is outlined, the formula bar shows
   `=A1*2`, the keyboard stays closed, and the page does not zoom or jump.
3. Tap C1, then D1. Expected: the outline follows each tap, the formula bar
   changes, and the keyboard stays closed.
4. Swipe up, down and sideways on the grid. Expected: the grid scrolls with
   the finger and glides a little after it; the bars above and below stay
   still; the page does not bounce; no range gets selected.
5. Tap A1 twice quickly. Expected: the keyboard opens with letters (not a
   number pad), the cell shows a text cursor after `10`, the X and check
   mark replace the Edit button, and the page does not zoom.
6. With the keyboard open, look at the screen. Expected: the formula bar is
   at the top, A1 is visible above the keyboard, and nothing is hidden
   behind the keyboard. The app bar and the status line may be hidden.
7. Type `5` and press Enter (or Go, or the return key). Expected: the
   keyboard closes, A1 shows 105, B1 shows 210, the status line says
   Version 1. If Enter does nothing on Android, tap the check mark and note
   it.
8. Tap the empty cell A3, then tap **Edit**. Expected: the keyboard opens
   and A3 is being edited.
9. Type `=A1*3` and tap the check mark. Expected: the keyboard closes, A3
   shows 315 and the status line says Version 2.
10. Tap B1, then tap the formula text in the formula bar. Expected: the
    keyboard opens and the cursor is in the formula bar.
11. Type `+7`, then tap the X. Expected: the keyboard closes and B1 still
    shows 210 with `=A1*2`.
12. Scroll down, double tap a cell near the bottom of the screen (row 12 or
    lower) and wait for the keyboard. Expected: the grid moves so that the
    cell is visible above the keyboard, with the editing box on the cell.
13. Type `12`, tap the check mark. Expected: the keyboard closes, the full
    layout returns (app bar and status line back), and no blank band is
    left at the bottom or top.
14. Hold a finger still on a cell for about half a second, then drag.
    Expected: a range is selected; the browser's own menu or magnifier does
    not get in the way.
15. Put two fingers on the grid and move them apart. Expected: the grid's
    zoom percent rises and the cells grow; the bars keep their size.
16. Put two fingers on the app bar and move them apart. Expected: the whole
    page zooms, as on any web page (pinch zoom is not switched off). Pinch
    back.
17. Turn the phone sideways and repeat steps 2 and 5. Expected: the same,
    with fewer rows visible.
18. iPhone only: in steps 5, 8 and 10, watch for the page zooming in when
    the keyboard opens. Expected: no zoom.
19. Android only: in step 6, the page should be resized by the keyboard, not
    covered by it. Expected: the sheet tabs and zoom control sit just above
    the keyboard.
20. With a hardware keyboard attached (tablet), tap a cell and type.
    Expected: typing starts in the cell without an on-screen keyboard.
