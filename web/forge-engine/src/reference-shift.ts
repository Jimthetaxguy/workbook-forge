// Moves the relative cell references in formula text, keeping everything else
// as it was typed. This is a text pass, not a parser, and it is never trusted
// on its own: the engine's copyFormulas command uses its output only when the
// toolkit reads it as the same formula the toolkit's own copy produced (see
// ForgeEngine.copyFormulas). A reference this pass misreads therefore costs
// the typed spelling, never the formula.

/** Excel's grid: rows 1 to 1,048,576 and columns A to XFD (16,384). */
const MAX_ROW = 1_048_576;
const MAX_COLUMN = 16_384;

const REFERENCE = /(\$?)([A-Za-z]{1,3})(\$?)([0-9]{1,7})/y;
const WORD = /[A-Za-z0-9_.\\]/;
const WORD_RUN = /[A-Za-z_\\][A-Za-z0-9_.\\]*/y;

function columnNumber(letters: string): number {
  let n = 0;
  for (const ch of letters.toUpperCase()) n = n * 26 + (ch.charCodeAt(0) - 64);
  return n;
}

function columnLetters(n: number): string {
  let out = '';
  for (let rest = n; rest > 0; rest = Math.floor((rest - 1) / 26)) out = String.fromCharCode(65 + ((rest - 1) % 26)) + out;
  return out;
}

/**
 * `formula` (with or without its leading "=") as it reads after its cell
 * moves `rowDelta` rows down and `columnDelta` columns right: a row or column
 * without a `$` moves, one with a `$` stays. Text in double quotes and sheet
 * names in single quotes are copied through untouched, as are function names,
 * defined names and sheet prefixes that happen to look like a cell (`LOG10(`,
 * `Q1!`). Returns null when a moved reference would leave the grid.
 */
export function shiftReferences(formula: string, rowDelta: number, columnDelta: number): string | null {
  let out = '';
  let i = 0;
  const n = formula.length;
  while (i < n) {
    const ch = formula[i];
    if (ch === '"' || ch === "'") {
      // Quoted text or a quoted sheet name; a doubled quote is the quote itself.
      let j = i + 1;
      while (j < n) {
        if (formula[j] === ch) {
          if (formula[j + 1] === ch) {
            j += 2;
            continue;
          }
          break;
        }
        j += 1;
      }
      out += formula.slice(i, j + 1);
      i = j + 1;
      continue;
    }
    const startsWord = i === 0 || !WORD.test(formula[i - 1]);
    if (startsWord && (ch === '$' || /[A-Za-z]/.test(ch))) {
      REFERENCE.lastIndex = i;
      const match = REFERENCE.exec(formula);
      const after = match ? formula[i + match[0].length] : undefined;
      if (match && (after === undefined || !/[A-Za-z0-9_.\\$(!]/.test(after))) {
        const [text, columnFixed, letters, rowFixed, digits] = match;
        const column = columnNumber(letters) + (columnFixed ? 0 : columnDelta);
        const row = Number(digits) + (rowFixed ? 0 : rowDelta);
        if (column < 1 || column > MAX_COLUMN || row < 1 || row > MAX_ROW) return null;
        out += `${columnFixed}${columnLetters(column)}${rowFixed}${row}`;
        i += text.length;
        continue;
      }
    }
    if (startsWord) {
      // A name, a function or an unquoted sheet prefix: copy the whole word so
      // its tail is not read as a cell.
      WORD_RUN.lastIndex = i;
      const word = WORD_RUN.exec(formula);
      if (word) {
        out += word[0];
        i += word[0].length;
        continue;
      }
    }
    out += ch;
    i += 1;
  }
  return out;
}
