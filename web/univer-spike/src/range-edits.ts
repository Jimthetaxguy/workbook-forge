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

/** The built-in lists Excel continues by name: days and months, short and long. */
const NAME_LISTS = [
  'Sun Mon Tue Wed Thu Fri Sat',
  'Sunday Monday Tuesday Wednesday Thursday Friday Saturday',
  'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec',
  'January February March April May June July August September October November December',
].map((line) => line.split(' '));
const LIST_WORDS = new Set(NAME_LISTS.flat().map((word) => word.toLowerCase()));

/**
 * True when Excel would continue this line of cells as a series instead of
 * copying it: two or more typed numbers, a date or time, text that holds a
 * digit ("Item 1"), or a day or month name. Formulas, a single number,
 * booleans and other text are copied by Excel too. `dateLike[i]` says cell i
 * is drawn with a date or time format. The rule is deliberately wide: it
 * flags some lines Excel would simply copy rather than copy one Excel would
 * continue. `planLine` then says whether the series is one this page
 * continues.
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

/**
 * What a fill does with one line of the source (a column when filling down or
 * up, a row when filling across).
 *
 * - `copy`: Excel copies this line, so the source repeats.
 * - `series`: `at(k)` is the value k cells from the line's first cell, so
 *   `at(line.length)` is the first cell after it and `at(-1)` the first cell
 *   before it. `at` returns undefined when the series has no value there.
 * - `refuse`: Excel would continue a series this page does not build, so a
 *   copy would not match Excel.
 */
export type LinePlan = { kind: 'copy' } | { kind: 'refuse' } | { kind: 'series'; at: (k: number) => Scalar | undefined };

/** The largest whole number a text series counts with, so every step is exact. */
const MAX_TEXT_NUMBER = 999_999_999_999_999;

function numberSeries(values: number[]): LinePlan {
  const n = values.length;
  const step = values[1] - values[0];
  let even = true;
  for (let i = 2; i < n; i += 1) if (values[i] - values[i - 1] !== step) even = false;
  const check = (v: number) => (Number.isFinite(v) ? v : undefined);
  if (even) {
    // Evenly spaced: the step is added from the nearer end, which keeps whole numbers exact.
    return { kind: 'series', at: (k) => check(k >= n ? values[n - 1] + step * (k - (n - 1)) : k < 0 ? values[0] + step * k : values[k]) };
  }
  // Otherwise the least-squares line through the values, with x = 0, 1, 2, ...
  const meanX = (n - 1) / 2;
  const meanY = values.reduce((sum, v) => sum + v, 0) / n;
  let [top, bottom] = [0, 0];
  for (let i = 0; i < n; i += 1) {
    top += (i - meanX) * (values[i] - meanY);
    bottom += (i - meanX) ** 2;
  }
  const slope = top / bottom;
  return { kind: 'series', at: (k) => check(k >= 0 && k < n ? values[k] : meanY + slope * (k - meanX)) };
}

/** Text that ends in a whole number after a part with a letter and no digit: "Item1", "Product 12". */
function textNumber(text: string): { prefix: string; number: number } | null {
  const match = /^(\D*\D)(0|[1-9]\d{0,14})$/s.exec(text);
  return match && /\p{L}/u.test(match[1]) ? { prefix: match[1], number: Number(match[2]) } : null;
}

function textSeries(texts: string[]): LinePlan {
  const parts = texts.map(textNumber);
  if (parts.some((part) => part === null || part.prefix !== parts[0]!.prefix)) return { kind: 'refuse' };
  const numbers = parts.map((part) => part!.number);
  const step = numbers.length > 1 ? numbers[1] - numbers[0] : 1;
  for (let i = 2; i < numbers.length; i += 1) if (numbers[i] - numbers[i - 1] !== step) return { kind: 'refuse' };
  return {
    kind: 'series',
    at: (k) => {
      const value = numbers[0] + step * k;
      return value >= 0 && value <= MAX_TEXT_NUMBER ? `${parts[0]!.prefix}${value}` : undefined;
    },
  };
}

/** How a name is cased: all lower, all upper, or a capital then lower. Anything else is null. */
function caseOf(word: string): 'lower' | 'upper' | 'title' | null {
  if (word === word.toLowerCase()) return 'lower';
  if (word === word.toUpperCase()) return 'upper';
  return word === word[0].toUpperCase() + word.slice(1).toLowerCase() ? 'title' : null;
}

function nameSeries(words: string[]): LinePlan {
  const lower = words.map((word) => word.toLowerCase());
  const lists = NAME_LISTS.filter((list) => lower.every((word) => list.some((name) => name.toLowerCase() === word)));
  // "May" alone is in both month lists, so the list it belongs to is not known.
  if (lists.length !== 1) return { kind: 'refuse' };
  const list = lists[0];
  const size = list.length;
  const shape = caseOf(words[0]);
  if (shape === null || words.some((word) => caseOf(word) !== shape)) return { kind: 'refuse' };
  const indexes = lower.map((word) => list.findIndex((name) => name.toLowerCase() === word));
  const mod = (v: number) => ((v % size) + size) % size;
  const step = indexes.length > 1 ? mod(indexes[1] - indexes[0]) : 1;
  for (let i = 2; i < indexes.length; i += 1) if (mod(indexes[i] - indexes[i - 1]) !== step) return { kind: 'refuse' };
  return {
    kind: 'series',
    at: (k) => {
      const name = list[mod(indexes[0] + step * k)];
      return shape === 'lower' ? name.toLowerCase() : shape === 'upper' ? name.toUpperCase() : name;
    },
  };
}

/**
 * Decide what a fill does with one line of the source. Lines Excel copies are
 * copied. Three kinds of series are continued:
 *
 * - numbers: two or more typed numbers continue their straight line. Evenly
 *   spaced numbers keep their step (1, 2 gives 3, 4, 5); otherwise the
 *   least-squares line through them is used, as Excel's linear trend does.
 * - text ending in a whole number ("Item1", "Product 1"): the number counts
 *   up by one, or by the step two or more cells set.
 * - day and month names, short or long: the next name in the list, or every
 *   n-th name when two or more cells set a step. The list wraps round.
 *
 * Anything else that Excel would continue is refused: dates and times, a line
 * that mixes kinds or has a formula or a blank among its series cells, text
 * with a number elsewhere than its end, text numbers that are not evenly
 * spaced, names in mixed case and a lone "May".
 */
export function planLine(line: (Held | undefined)[], dateLike: boolean[]): LinePlan {
  if (!isSeriesLine(line, dateLike)) return { kind: 'copy' };
  if (dateLike.some(Boolean)) return { kind: 'refuse' };
  const values: Scalar[] = [];
  for (const cell of line) {
    if (!cell || cell.f !== undefined || cell.value === null || cell.value === '') return { kind: 'refuse' };
    values.push(cell.value);
  }
  if (values.every((v) => typeof v === 'number')) return numberSeries(values as number[]);
  if (!values.every((v) => typeof v === 'string')) return { kind: 'refuse' };
  const texts = values as string[];
  if (texts.every((text) => LIST_WORDS.has(text.toLowerCase()))) return nameSeries(texts);
  return textSeries(texts);
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
