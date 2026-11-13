// Unit tests for how a General-format number is drawn in a cell of a given
// width. Expected values are Microsoft's own examples or rounding worked by
// hand from the digits of the number; none comes from either engine or from
// Univer.
//
// Run: npm run test:unit
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { GENERAL_MAX_CHARS, SCIENTIFIC_FROM_DIGITS, generalNumberText, overflowText } from '../src/general-number.ts';

/** A cell with room for `count` characters, whatever they are. */
const chars = (count) => (text) => text.length <= count;
const anyWidth = () => true;
const drawn = (value, count) => generalNumberText(value, chars(count));

test('the limits are the ones Microsoft publishes', () => {
  // "max overall length for cell display is 11, not including negative sign"
  assert.equal(GENERAL_MAX_CHARS, 11);
  // "scientific (exponential) notation for large numbers (12 or more digits)"
  assert.equal(SCIENTIFIC_FROM_DIGITS, 12);
});

test("Microsoft's example: 25.76 shows as 26 in a cell that is not wide enough", () => {
  assert.equal(drawn(25.76, 5), '25.76');
  // One decimal still fits in four characters: 25.76 is nearer 25.8 than 25.7.
  assert.equal(drawn(25.76, 4), '25.8');
  assert.equal(drawn(25.76, 3), '26');
  assert.equal(drawn(25.76, 2), '26');
  assert.equal(drawn(25.76, 1), null);
});

test('a number of at most eleven characters that fits is drawn as typed', () => {
  assert.equal(generalNumberText(0, anyWidth), '0');
  assert.equal(generalNumberText(42, anyWidth), '42');
  assert.equal(generalNumberText(-42.75, anyWidth), '-42.75');
  assert.equal(generalNumberText(0.5, anyWidth), '0.5');
  // Eleven digits is still below the scientific threshold.
  assert.equal(generalNumberText(12345678901, anyWidth), '12345678901');
  // Eleven characters with the decimal point; the minus sign is not counted.
  assert.equal(generalNumberText(1234567.891, anyWidth), '1234567.891');
  assert.equal(generalNumberText(0.123456789, anyWidth), '0.123456789');
  assert.equal(generalNumberText(-1234567.891, anyWidth), '-1234567.891');
  // A very small number is written out, not handed over as JavaScript's 1.23e-7.
  assert.equal(generalNumberText(0.000000123, anyWidth), '0.000000123');
});

// The standard's notes, as Microsoft prints them: "The application shall
// attempt to display the full number up to 11 digits (inc. decimal point)"
// and "max overall length for cell display is 11, not including negative
// sign, but includes leading zeros and decimal separator." Each expected text
// is the number cut to eleven characters by hand, rounded as Microsoft's
// Support page rounds 25.76 to 26.
test('a number longer than eleven characters is rounded to eleven however wide the cell', () => {
  // 2683. is five characters, so six decimals: .311497|6 rounds up.
  assert.equal(generalNumberText(2683.3114976001525, anyWidth), '2683.311498');
  assert.equal(generalNumberText(-2683.3114976001525, anyWidth), '-2683.311498');
  assert.equal(generalNumberText(2683.3114976, anyWidth), '2683.311498');
  // 0. is two characters, so nine decimals: .086630948|0 rounds down.
  assert.equal(generalNumberText(0.08663094803653162, anyWidth), '0.086630948');
  // Pi is 3.141592653|58979: the nine decimals round up.
  assert.equal(generalNumberText(Math.PI, anyWidth), '3.141592654');
  assert.equal(generalNumberText(1 / 3, anyWidth), '0.333333333');
  assert.equal(generalNumberText(2 / 3, anyWidth), '0.666666667');
  // Fourteen characters as typed: nine decimals, .123456789|012.
  assert.equal(generalNumberText(0.123456789012, anyWidth), '0.123456789');
  assert.equal(drawn(0.123456789012, 14), '0.123456789');
  // Ten and eleven whole digits leave no room for a decimal: a half rounds away from zero.
  assert.equal(generalNumberText(1234567890.5, anyWidth), '1234567891');
  assert.equal(generalNumberText(12345678901.5, anyWidth), '12345678902');
  // Eleven digits are drawn in full and twelve in scientific notation: 1.23456|789012 rounds up.
  assert.equal(generalNumberText(12345678901, anyWidth), '12345678901');
  assert.equal(generalNumberText(123456789012, anyWidth), '1.23457E+11');
  // Below 0.001 with too many digits: 0.000012346 has room for five of them
  // and 1.23457E-05 for six (1.23456|789 rounds up), so scientific notation.
  assert.equal(generalNumberText(0.0000123456789, anyWidth), '1.23457E-05');
  // One zero fewer and both forms have room for six digits: the decimal stays.
  assert.equal(generalNumberText(0.000123456789, anyWidth), '0.000123457');
  // Floating-point noise is beyond the eleventh character: 0.1 + 0.2 is 0.30000000000000004.
  assert.equal(generalNumberText(0.1 + 0.2, anyWidth), '0.3');
  // 1.234567890|49999 rounds down, and the closing zero is not drawn.
  assert.equal(generalNumberText(1.23456789049999, anyWidth), '1.23456789');
});

test('0.08663094803653162 is rounded to the decimals that fit, never to scientific notation', () => {
  const value = 0.08663094803653162;
  // The digits are 0.0866309480365...; each line rounds at one place fewer.
  assert.equal(generalNumberText(value, anyWidth), '0.086630948'); // eleven characters at most
  assert.equal(drawn(value, 18), '0.086630948');
  assert.equal(drawn(value, 11), '0.086630948'); // ...9480|3 rounds down
  assert.equal(drawn(value, 10), '0.08663095'); // ...094|8 rounds up
  assert.equal(drawn(value, 9), '0.0866309'); // ...309|4 rounds down
  assert.equal(drawn(value, 8), '0.086631'); // ...630|9 rounds up
  assert.equal(drawn(value, 7), '0.08663'); // ...663|0 rounds down
  assert.equal(drawn(value, 6), '0.0866'); // ...866|3 rounds down
  assert.equal(drawn(value, 5), '0.087'); // 0.086|6 rounds up
  assert.equal(drawn(value, 4), '0.09'); // 0.08|6 rounds up
  assert.equal(drawn(value, 3), '0.1'); // 0.0|8 rounds up
  assert.equal(drawn(value, 2), '0'); // below one half
  assert.equal(drawn(value, 1), '0');
  assert.equal(drawn(value, 0), null);
});

test('-2683.3114976001525 keeps its whole-number part and loses decimals', () => {
  const value = -2683.3114976001525;
  // Eleven characters not counting the minus sign: 2683.311498 (...1497|6 rounds up).
  assert.equal(generalNumberText(value, anyWidth), '-2683.311498');
  assert.equal(drawn(value, 18), '-2683.311498');
  assert.equal(drawn(value, 12), '-2683.311498');
  // Five decimals round to .31150 (...3114|9 with a carry); the closing zero is not drawn.
  assert.equal(drawn(value, 11), '-2683.3115');
  assert.equal(drawn(value, 10), '-2683.3115');
  assert.equal(drawn(value, 9), '-2683.311');
  assert.equal(drawn(value, 8), '-2683.31');
  assert.equal(drawn(value, 7), '-2683.3');
  assert.equal(drawn(value, 6), '-2683');
  assert.equal(drawn(value, 5), '-2683');
  // The whole-number part no longer fits and neither does -3E+03 (six characters).
  assert.equal(drawn(value, 4), null);
});

test('an 88px column in 11pt Arial draws the two numbers as rounded decimals', () => {
  // Arial's published advance widths, in thousandths of the font size:
  // digits 556, period 278, hyphen 333, E 667, plus 584. 11pt is 14.6667px.
  const advance = { '.': 278, '-': 333, E: 667, '+': 584 };
  const width = (text) => [...text].reduce((sum, c) => sum + (advance[c] ?? 556), 0) * (14.6667 / 1000);
  // The view's rule: 2px of padding each side, and one digit kept clear.
  const fits = (text) => width(text) + width('0') <= 88 - 2 - 2;
  // 0.08663095 is 77.5px, 85.6px with the clearance; 0.0866309 is 69.3px.
  assert.equal(generalNumberText(0.08663094803653162, fits), '0.0866309');
  // -2683.3115 is 74.2px, 82.4px with the clearance.
  assert.equal(generalNumberText(-2683.3114976001525, fits), '-2683.3115');
  // Twelve digits: 1.2346E+11 is 79.5px, 87.7px with the clearance; 1.235E+11 is 71.4px.
  assert.equal(generalNumberText(123456789012, fits), '1.235E+11');
});

test('rounding goes half away from zero on the decimal digits', () => {
  // 2.675 is stored a hair below 2.675 and toFixed(2) gives 2.67.
  assert.equal(drawn(2.675, 4), '2.68');
  assert.equal(drawn(-2.675, 5), '-2.68');
  assert.equal(drawn(0.125, 4), '0.13');
  assert.equal(drawn(1.5, 1), '2');
  assert.equal(drawn(2.5, 1), '3');
  assert.equal(drawn(-2.5, 2), '-3');
  // A carry runs through the nines and the closing zeros are dropped.
  assert.equal(drawn(9.996, 4), '10');
  assert.equal(drawn(999.9996, 7), '1000');
  assert.equal(drawn(0.006, 4), '0.01');
  assert.equal(drawn(1.0004, 5), '1');
});

test('twelve or more digits are always in scientific notation', () => {
  // Microsoft's rule; 1.23456789012 rounds to 1.23457 in an 11-character text.
  assert.equal(generalNumberText(123456789012, anyWidth), '1.23457E+11');
  assert.equal(generalNumberText(-123456789012, anyWidth), '-1.23457E+11');
  assert.equal(generalNumberText(123456789012.5, anyWidth), '1.23457E+11');
  assert.equal(generalNumberText(100000000000, anyWidth), '1E+11');
  assert.equal(generalNumberText(1234567890123456800, anyWidth), '1.23457E+18');
  assert.equal(generalNumberText(1e21, anyWidth), '1E+21');
  // A three-digit exponent leaves room for one digit fewer.
  assert.equal(generalNumberText(1.23456789e100, anyWidth), '1.2346E+100');
  // A narrower cell shortens the mantissa: 1.2346, 1.235, 1.23, 1.2, 1.
  assert.equal(drawn(123456789012, 10), '1.2346E+11');
  assert.equal(drawn(123456789012, 9), '1.235E+11');
  assert.equal(drawn(123456789012, 8), '1.23E+11');
  assert.equal(drawn(123456789012, 7), '1.2E+11');
  assert.equal(drawn(123456789012, 6), '1E+11');
  assert.equal(drawn(123456789012, 5), '1E+11');
  assert.equal(drawn(123456789012, 4), null);
});

test('a whole-number part that does not fit goes to scientific notation', () => {
  assert.equal(drawn(12345678901, 11), '12345678901');
  assert.equal(drawn(12345678901, 10), '1.2346E+10');
  assert.equal(drawn(12345678901, 8), '1.23E+10');
  assert.equal(drawn(12345678901, 5), '1E+10');
  assert.equal(drawn(12345678901, 4), null);
  assert.equal(drawn(123456, 6), '123456');
  assert.equal(drawn(123456, 5), '1E+05');
  assert.equal(drawn(-123456, 6), '-1E+05');
  // 1234567.891234 keeps eleven characters; without room for the whole part, 1E+06.
  assert.equal(drawn(1234567.891234, 13), '1234567.891');
  assert.equal(drawn(1234567.891234, 7), '1234568');
  assert.equal(drawn(1234567.891234, 6), '1E+06');
  // Rounding 99999999999.7 to a whole number makes a twelfth digit.
  assert.equal(drawn(99999999999.7, 12), '1E+11');
});

test('a small number goes to scientific notation when that shows more of it', () => {
  // Below 0.001: 0.000012346 has five digits of the number, 1.23457E-05 has six.
  assert.equal(drawn(0.0000123456789, 14), '1.23457E-05');
  assert.equal(drawn(0.0000123456789, 11), '1.23457E-05');
  // Both forms have six digits, so the decimal one stays.
  assert.equal(drawn(0.000123456789, 11), '0.000123457');
  // 0.001 and above stays decimal even when scientific would show more.
  assert.equal(drawn(0.00123456789, 8), '0.001235');
  // 0.0000001 against 1.23E-07.
  assert.equal(drawn(0.000000123, 11), '0.000000123');
  assert.equal(drawn(0.000000123, 9), '1.23E-07');
  // Decimals that would all be zero are not drawn while scientific notation fits.
  assert.equal(drawn(0.004, 5), '0.004');
  assert.equal(drawn(0.0004, 5), '4E-04');
  assert.equal(drawn(0.0004, 4), '0');
  assert.equal(drawn(-0.0004, 4), '0');
});

test('a number below one half with no room is drawn as 0, with no minus sign', () => {
  assert.equal(drawn(0.4, 2), '0');
  assert.equal(drawn(-0.4, 2), '0');
  assert.equal(drawn(0.4, 1), '0');
  assert.equal(drawn(0.5, 1), '1');
  assert.equal(drawn(-0.5, 2), '-1');
  assert.equal(drawn(-0.5, 1), null);
});

test('what cannot be drawn at all is null, and the cell shows number signs', () => {
  assert.equal(drawn(0, 0), null);
  assert.equal(drawn(123456, 4), null);
  assert.equal(generalNumberText(Number.NaN, anyWidth), null);
  assert.equal(generalNumberText(Number.POSITIVE_INFINITY, anyWidth), null);
  // 84px of room and an 8px sign: ten signs. Never fewer than one.
  assert.equal(overflowText(84, 8), '##########');
  assert.equal(overflowText(84, 8.2), '##########');
  assert.equal(overflowText(5, 8), '#');
  assert.equal(overflowText(84, 0), '#');
});

test('every drawn text fits its cell and has at most eleven characters besides a minus sign', () => {
  const values = [0.08663094803653162, -2683.3114976001525, 2683.3114976, 25.76, 1 / 3, -2 / 3, Math.PI * 1e5, -Math.E / 1e7, 123456789012.345, 6.02214076e23];
  for (const value of values) {
    for (let room = 0; room <= 24; room++) {
      const text = drawn(value, room);
      if (text === null) continue;
      assert.ok(text.length <= room, `${value} in ${room}: ${text}`);
      assert.ok(text.replace(/^-/, '').length <= GENERAL_MAX_CHARS, `${value} in ${room}: ${text}`);
      assert.match(text, /^-?\d+(\.\d*[1-9])?(E[+-]\d{2,3})?$/, `${value} in ${room}: ${text}`);
    }
  }
});
