// Pure helpers for the edits that touch more than one cell: clear, paste and
// fill. Nothing here calculates or rewrites a formula; the adapter asks Forge
// for every copied formula (`copyFormulas`) and sends the result as ordinary
// edits.

import type { Scalar } from '@workbook-forge/engine';

/** Zero-based, inclusive. */
export interface Rect {
  startRow: number;
  endRow: number;
  startColumn: number;
  endColumn: number;
}

/** What Forge holds in one cell: its value, and its formula without the "=". */
export interface Held {
  value: Scalar;
  f?: string;
}

/** The most cells one paste or fill may write. Larger requests are refused. */
export const MAX_RANGE_CELLS = 20_000;

export const rows = (rect: Rect) => rect.endRow - rect.startRow + 1;
export const columns = (rect: Rect) => rect.endColumn - rect.startColumn + 1;
export const cellCount = (rect: Rect) => rows(rect) * columns(rect);

export function contains(rect: Rect, row: number, col: number): boolean {
  return row >= rect.startRow && row <= rect.endRow && col >= rect.startColumn && col <= rect.endColumn;
}

export function union(a: Rect, b: Rect): Rect {
  return {
    startRow: Math.min(a.startRow, b.startRow),
    endRow: Math.max(a.endRow, b.endRow),
    startColumn: Math.min(a.startColumn, b.startColumn),
    endColumn: Math.max(a.endColumn, b.endColumn),
  };
}

/** A cell that holds nothing to copy: absent, or held and blank. */
export function isBlank(cell: Held | undefined | null): boolean {
  return !cell || (cell.f === undefined && (cell.value === null || cell.value === ''));
}

/**
 * Clipboard plain text as rows of cell text. This is the tab-separated form
 * spreadsheets put on the clipboard: tabs between cells, a line break between
 * rows, and a cell that itself holds a tab, a line break or a quote wrapped in
 * double quotes with inner quotes doubled. A field that opens with a quote
 * but never closes it is taken as typed. One trailing line break is the end
 * of the last row, not an extra empty row. Short rows are padded with empty
 * cells so the result is a rectangle.
 */
export function parseClipboardText(text: string): string[][] {
  const out: string[][] = [];
  let row: string[] = [];
  let i = 0;
  const n = text.length;
  const isBreak = (ch: string | undefined) => ch === '\n' || ch === '\r';
  const endRow = () => {
    out.push(row);
    row = [];
  };
  for (;;) {
    let field = '';
    let quoted = false;
    if (text[i] === '"') {
      // Look for the closing quote: one not doubled, followed by a tab, a line break or the end.
      let j = i + 1;
      let value = '';
      while (j < n) {
        if (text[j] === '"') {
          if (text[j + 1] === '"') {
            value += '"';
            j += 2;
            continue;
          }
          if (j + 1 === n || text[j + 1] === '\t' || isBreak(text[j + 1])) {
            quoted = true;
            field = value;
            i = j + 1;
          }
          break;
        }
        value += text[j];
        j += 1;
      }
    }
    if (!quoted) {
      const start = i;
      while (i < n && text[i] !== '\t' && !isBreak(text[i])) i += 1;
      field = text.slice(start, i);
    }
    row.push(field);
    if (i >= n) {
      endRow();
      break;
    }
    if (text[i] === '\t') {
      i += 1;
      continue;
    }
    // A line break: \r\n, \n or \r.
    i += text[i] === '\r' && text[i + 1] === '\n' ? 2 : 1;
    endRow();
    if (i >= n) break; // the trailing break ended the last row
  }
  const width = Math.max(0, ...out.map((r) => r.length));
  for (const r of out) while (r.length < width) r.push('');
  return out;
}

/**
 * How many rows and columns a paste writes. A source that fits the selection
 * a whole number of times in both directions is repeated to fill it (one
 * copied cell fills any selection); otherwise one copy goes at the
 * selection's top left.
 */
export function pasteShape(sourceRows: number, sourceColumns: number, selection: Rect): { rows: number; columns: number } {
  const [h, w] = [rows(selection), columns(selection)];
  if (h % sourceRows === 0 && w % sourceColumns === 0) return { rows: h, columns: w };
  return { rows: sourceRows, columns: sourceColumns };
}

/**
 * The source cell a filled cell copies. The source repeats as a tile away
 * from its own edge: below or right of it the tile restarts at its first row
 * or column, above or left of it the tile is counted back from its last.
 */
export function fillSource(source: Rect, row: number, col: number): { row: number; col: number } {
  const wrap = (at: number, start: number, end: number) => {
    const size = end - start + 1;
    if (at > end) return start + ((at - start) % size);
    if (at < start) return end - ((end - at) % size);
    return at;
  };
  return { row: wrap(row, source.startRow, source.endRow), col: wrap(col, source.startColumn, source.endColumn) };
}

/** Day and month names Excel continues as a list when they are filled. */
const LIST_WORDS = new Set(
  [
    'sun mon tue wed thu fri sat',
    'sunday monday tuesday wednesday thursday friday saturday',
    'jan feb mar apr may jun jul aug sep oct nov dec',
    'january february march april june july august september october november december',
  ].flatMap((line) => line.split(' ')),
);

/**
 * True when Excel would continue this line of cells as a series instead of
 * copying it, so a plain copy would not match Excel: two or more typed
 * numbers, a date or time, text that holds a digit ("Item 1"), or a day or
 * month name. Formulas, a single number, booleans and other text are copied
 * by Excel too. `dateLike[i]` says cell i is drawn with a date or time
 * format. The rule is deliberately wide: it refuses some lines Excel would
 * simply copy rather than copy one Excel would continue.
 */
export function isSeriesLine(line: (Held | undefined)[], dateLike: boolean[]): boolean {
  let numbers = 0;
  for (let i = 0; i < line.length; i += 1) {
    const cell = line[i];
    if (!cell || cell.f !== undefined) continue;
    if (typeof cell.value === 'number') {
      numbers += 1;
      if (dateLike[i]) return true;
    } else if (typeof cell.value === 'string') {
      if (/\d/.test(cell.value) || LIST_WORDS.has(cell.value.trim().toLowerCase())) return true;
    }
  }
  return numbers >= 2;
}

/** True when an Excel number format code draws a date or a time. */
export function isDateFormat(code: string): boolean {
  const bare = code.replace(/"[^"]*"/g, '').replace(/\\./g, '').replace(/\[[^\]]*\]/g, '');
  return /[dmyhs]/i.test(bare);
}

/** Univer marks the HTML it copies with an id; this reads it back. */
export function copyIdOf(html: string): string | null {
  return /data-copy-id="([^"\s]+)"/.exec(html)?.[1] ?? null;
}
