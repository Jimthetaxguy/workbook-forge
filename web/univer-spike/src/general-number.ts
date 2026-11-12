// How a number in a General-format cell is drawn in a cell of a given width.
// Pure rules, no Univer and no DOM: the caller says whether a text fits.
// Only the drawn text is decided here. The value Forge holds, the formula
// bar, a copy and an export keep the whole number.
//
// Sources, quoted in docs/specs/univer-spike.md ("General numbers"):
//   Microsoft Support, "Available number formats in Excel": a General number
//     is shown as typed; a cell that is not wide enough rounds the decimals;
//     12 or more digits use scientific notation.
//   The Office Open XML standard's notes on the General format, as printed
//     on Microsoft Learn (NumberingFormat class): at most 11 characters, not
//     counting the minus sign; when to switch to exponential notation; "#"
//     across the cell when nothing fits.
// The spec lists which parts of the rule are Workbook Forge's own.

/** The most characters a rounded General number takes, not counting a minus sign. */
export const GENERAL_MAX_CHARS = 11;
/** A whole-number part of this many digits or more is always drawn in scientific notation. */
export const SCIENTIFIC_FROM_DIGITS = 12;

/** A positive number as its shortest decimal digits: d1.d2d3... times ten to the `exponent`. */
interface Digits {
  /** Significant digits, no leading or trailing zero; empty for zero. */
  digits: string;
  exponent: number;
}

function digitsOf(magnitude: number): Digits {
  // toExponential() with no argument gives as many digits as the number needs
  // to be read back exactly, the same digits String() prints.
  const [mantissa, exponent] = magnitude.toExponential().split('e');
  return { digits: mantissa.replace('.', '').replace(/0+$/, ''), exponent: Number(exponent) };
}

/** Keep `keep` significant digits, rounding a half away from zero. `keep` may be zero or negative. */
function roundDigits({ digits, exponent }: Digits, keep: number): Digits {
  if (keep >= digits.length) return { digits, exponent };
  if (keep < 0) return { digits: '', exponent };
  const kept = digits.slice(0, keep).split('').map(Number);
  if (digits.charCodeAt(keep) >= 53 /* '5' */) {
    let at = kept.length - 1;
    while (at >= 0 && kept[at] === 9) kept[at--] = 0;
    if (at >= 0) kept[at] += 1;
    else {
      // Every kept digit was a 9 (or none was kept): 9.99 becomes 10.0.
      kept.unshift(1);
      exponent += 1;
    }
  }
  return { digits: kept.join('').replace(/0+$/, ''), exponent };
}

/** Ordinary decimal notation, with no exponent and no trailing zeros. */
function plain({ digits, exponent }: Digits): string {
  if (digits === '') return '0';
  if (exponent < 0) return `0.${'0'.repeat(-exponent - 1)}${digits}`;
  const whole = digits.slice(0, exponent + 1).padEnd(exponent + 1, '0');
  const decimals = digits.slice(exponent + 1);
  return decimals ? `${whole}.${decimals}` : whole;
}

/** Scientific notation as Excel writes it: 1.23457E+11, 8.7E-02, 1E+100. */
function scientific({ digits, exponent }: Digits): string {
  const mantissa = digits.length > 1 ? `${digits[0]}.${digits.slice(1)}` : digits;
  return `${mantissa}E${exponent < 0 ? '-' : '+'}${String(Math.abs(exponent)).padStart(2, '0')}`;
}

interface Candidate {
  text: string;
  /** How many of the number's significant digits the text has room for. */
  shown: number;
}

/**
 * The longest rounded text of at most 11 characters that fits, trying
 * `keepFor(n)` significant digits for n = most down to 0. A text that has
 * rounded to zero is tried without the minus sign.
 */
function longest(
  number: Digits,
  sign: string,
  most: number,
  keepFor: (n: number) => number,
  write: (rounded: Digits) => string,
  fits: (text: string) => boolean,
): (Candidate & { zero: boolean }) | null {
  for (let n = Math.max(most, 0); n >= 0; n--) {
    const rounded = roundDigits(number, keepFor(n));
    const zero = rounded.digits === '';
    const text = zero ? '0' : sign + write(rounded);
    // A carry can add a character: 99999999999.7 rounds to a twelfth digit.
    if (text.length - (zero ? 0 : sign.length) > GENERAL_MAX_CHARS) continue;
    if (fits(text)) return { text, shown: keepFor(n), zero };
  }
  return null;
}

/**
 * The text drawn for `value` in a General cell, or null when no form of it
 * fits and the cell shows number signs (see `overflowText`).
 *
 * `fits(text)` is true when `text` can be drawn in the cell. The steps:
 *
 * 1. A whole-number part of 12 or more digits: scientific notation, always.
 * 2. Otherwise the number as typed, when that fits.
 * 3. Otherwise the decimals are rounded (a half goes away from zero) to the
 *    longest text that fits, at most 11 characters not counting the minus
 *    sign. 25.76 becomes 25.8 and then 26.
 * 4. Scientific notation instead, with the longest mantissa that fits in the
 *    same 11 characters, when the whole-number part does not fit, when the
 *    rounded decimals would have no digit other than zero, or when the number
 *    is below 0.001 and scientific notation has room for more of its digits.
 * 5. A number below one half that fits in neither form is drawn as 0.
 * 6. Anything else: null.
 */
export function generalNumberText(value: number, fits: (text: string) => boolean): string | null {
  if (!Number.isFinite(value)) return null;
  if (value === 0) return fits('0') ? '0' : null;
  const sign = value < 0 ? '-' : '';
  const number = digitsOf(Math.abs(value));
  // E, the exponent's sign and at least two digits, after a one-digit mantissa and its point.
  const exponentChars = 2 + Math.max(2, String(Math.abs(number.exponent)).length);
  const exponential = longest(
    number,
    sign,
    Math.min(GENERAL_MAX_CHARS - 2 - exponentChars, number.digits.length - 1),
    (mantissaDecimals) => mantissaDecimals + 1,
    scientific,
    fits,
  );
  if (number.exponent + 1 >= SCIENTIFIC_FROM_DIGITS) return exponential?.text ?? null;

  const typed = sign + plain(number);
  if (fits(typed)) return typed;

  // Characters of the whole-number part; a number below one starts with "0".
  const whole = Math.max(number.exponent + 1, 1);
  const typedDecimals = number.digits.length - number.exponent - 1;
  const rounded = longest(
    number,
    sign,
    Math.min(GENERAL_MAX_CHARS - whole - 1, typedDecimals - 1),
    (decimals) => number.exponent + 1 + decimals,
    plain,
    fits,
  );
  if (rounded && !rounded.zero) {
    const moreInScientific = exponential && number.exponent < -3 && exponential.shown > rounded.shown;
    return moreInScientific ? exponential.text : rounded.text;
  }
  return exponential?.text ?? rounded?.text ?? null;
}

/** Number signs across the cell, as many as fit and at least one. */
export function overflowText(availableWidth: number, numberSignWidth: number): string {
  if (!(numberSignWidth > 0)) return '#';
  return '#'.repeat(Math.max(1, Math.floor(availableWidth / numberSignWidth)));
}
