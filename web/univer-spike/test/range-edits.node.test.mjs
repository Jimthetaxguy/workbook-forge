// Unit tests for the pure helpers behind clear, paste and fill. Expected
// values are worked by hand or taken from Microsoft's own examples; none
// comes from either engine.
//
// Run: npm run test:unit
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  MAX_RANGE_CELLS,
  cellCount,
  contains,
  copyIdOf,
  fillSource,
  isBlank,
  isDateFormat,
  isSeriesLine,
  parseClipboardText,
  pasteShape,
  planLine,
  union,
} from '../src/range-edits.ts';

const rect = (startRow, startColumn, endRow, endColumn) => ({ startRow, endRow, startColumn, endColumn });
const value = (v) => ({ value: v });
const formula = (f, v = 0) => ({ value: v, f });

test('rectangles: size, membership and union', () => {
  // B2:D5 is 4 rows by 3 columns.
  const b2d5 = rect(1, 1, 4, 3);
  assert.equal(cellCount(b2d5), 12);
  assert.equal(contains(b2d5, 1, 1), true);
  assert.equal(contains(b2d5, 4, 3), true);
  assert.equal(contains(b2d5, 0, 1), false);
  assert.equal(contains(b2d5, 4, 4), false);
  // B2:D5 with E1 spans B1:E5.
  assert.deepEqual(union(b2d5, rect(0, 4, 0, 4)), rect(0, 1, 4, 4));
  assert.equal(MAX_RANGE_CELLS, 20_000);
});

test('blank: absent, null and empty text are blank; zero, false and a formula are not', () => {
  assert.equal(isBlank(undefined), true);
  assert.equal(isBlank(value(null)), true);
  assert.equal(isBlank(value('')), true);
  assert.equal(isBlank(value(0)), false);
  assert.equal(isBlank(value(false)), false);
  assert.equal(isBlank(formula('A1', null)), false);
});

test('clipboard text: tabs split cells, line breaks split rows, one trailing break is not a row', () => {
  assert.deepEqual(parseClipboardText('a\tb\nc\td'), [['a', 'b'], ['c', 'd']]);
  assert.deepEqual(parseClipboardText('a\tb\r\nc\td\r\n'), [['a', 'b'], ['c', 'd']]);
  assert.deepEqual(parseClipboardText('a\rb'), [['a'], ['b']]);
  assert.deepEqual(parseClipboardText('x'), [['x']]);
  assert.deepEqual(parseClipboardText(''), [['']]);
  // A blank row in the middle and a second trailing break are both kept.
  assert.deepEqual(parseClipboardText('a\n\nb'), [['a'], [''], ['b']]);
  assert.deepEqual(parseClipboardText('a\n\n'), [['a'], ['']]);
});

test('clipboard text: short rows are padded to a rectangle and empty cells are kept', () => {
  assert.deepEqual(parseClipboardText('a\tb\tc\nd'), [['a', 'b', 'c'], ['d', '', '']]);
  assert.deepEqual(parseClipboardText('\tb\n\t'), [['', 'b'], ['', '']]);
});

test('clipboard text: a quoted cell may hold tabs, line breaks and doubled quotes', () => {
  assert.deepEqual(parseClipboardText('"line 1\nline 2"\tb'), [['line 1\nline 2', 'b']]);
  assert.deepEqual(parseClipboardText('"a\tb"'), [['a\tb']]);
  assert.deepEqual(parseClipboardText('"say ""hi"""\tx'), [['say "hi"', 'x']]);
  assert.deepEqual(parseClipboardText('""'), [['']]);
});

test('clipboard text: a quote that never closes, or sits inside a cell, is taken as typed', () => {
  assert.deepEqual(parseClipboardText('"abc\tdef'), [['"abc', 'def']]);
  assert.deepEqual(parseClipboardText('5" pipe\t6'), [['5" pipe', '6']]);
  assert.deepEqual(parseClipboardText('"a"b\tc'), [['"a"b', 'c']]);
});

test('paste shape: a copy repeats only when it fits the selection a whole number of times', () => {
  // One cell fills any selection.
  assert.deepEqual(pasteShape(1, 1, rect(0, 0, 3, 2)), { rows: 4, columns: 3 });
  // 1x2 into 2x4: two across, two down.
  assert.deepEqual(pasteShape(1, 2, rect(0, 0, 1, 3)), { rows: 2, columns: 4 });
  // 2x2 into 3x4: three rows is not a multiple of two, so one copy.
  assert.deepEqual(pasteShape(2, 2, rect(0, 0, 2, 3)), { rows: 2, columns: 2 });
  // 2x3 into a single cell: one copy from that cell.
  assert.deepEqual(pasteShape(2, 3, rect(5, 5, 5, 5)), { rows: 2, columns: 3 });
});

test('fill source: the copy repeats away from the source edge', () => {
  // Source A1:A2 (rows 0 and 1) filled down: rows 2, 3, 4 take rows 0, 1, 0.
  const a1a2 = rect(0, 0, 1, 0);
  assert.deepEqual([2, 3, 4].map((row) => fillSource(a1a2, row, 0).row), [0, 1, 0]);
  // Source A4:A5 (rows 3 and 4) filled up: rows 2, 1, 0 take rows 4, 3, 4.
  const a4a5 = rect(3, 0, 4, 0);
  assert.deepEqual([2, 1, 0].map((row) => fillSource(a4a5, row, 0).row), [4, 3, 4]);
  // Source B1:D1 (columns 1 to 3) filled right: columns 4 to 7 take 1, 2, 3, 1.
  const b1d1 = rect(0, 1, 0, 3);
  assert.deepEqual([4, 5, 6, 7].map((col) => fillSource(b1d1, 0, col).col), [1, 2, 3, 1]);
  // Source D1:F1 (columns 3 to 5) filled left: columns 2, 1, 0 take 5, 4, 3.
  const d1f1 = rect(0, 3, 0, 5);
  assert.deepEqual([2, 1, 0].map((col) => fillSource(d1f1, 0, col).col), [5, 4, 3]);
  // A cell inside the source is its own source.
  assert.deepEqual(fillSource(b1d1, 0, 2), { row: 0, col: 2 });
});

// Microsoft, "Fill data automatically in worksheet cells": "For a series like
// 1, 2, 3, 4, 5..., type 1 and 2 in the first two cells ... For the series
// 2, 2, 2, 2..., type 2 in first cell only."
// Microsoft, "Enter a series of numbers, dates, or other items": Item1 fills
// as Item2, Item3; Monday fills as Tuesday, Wednesday.
test('series: what Excel would continue is told apart from what it copies', () => {
  const none = [false, false, false];
  assert.equal(isSeriesLine([value(1), value(2)], none), true);
  assert.equal(isSeriesLine([value(2)], none), false);
  assert.equal(isSeriesLine([value('Item1')], none), true);
  assert.equal(isSeriesLine([value('Item 1')], none), true);
  assert.equal(isSeriesLine([value('Monday')], none), true);
  assert.equal(isSeriesLine([value(' jan ')], none), true);
  // A single number drawn as a date continues day by day.
  assert.equal(isSeriesLine([value(46000)], [true]), true);
  // Formulas, plain text, booleans and blanks are copied.
  assert.equal(isSeriesLine([formula('A1*2', 20)], none), false);
  assert.equal(isSeriesLine([formula('A1*2', 20), formula('A2*2', 40)], none), false);
  assert.equal(isSeriesLine([value('Total')], none), false);
  assert.equal(isSeriesLine([value(true), value(false)], none), false);
  assert.equal(isSeriesLine([undefined, value(null)], none), false);
  // One number beside a formula or a label is still a single number.
  assert.equal(isSeriesLine([value(5), formula('A1+1', 6), value('Total')], none), false);
});

// The series a fill continues. Sources for the expected values:
//   "Project values in a series" (1, 2 gives 3, 4, 5; 1, 3 gives 5, 7, 9; 100, 95 gives 90, 85; best fit for more values)
//   https://support.microsoft.com/en-us/office/project-values-in-a-series-5311f5cf-149e-4d06-81dd-5aaad87e5400
//   "Fill data automatically in worksheet cells" (2, 4 gives 6, 8; a single 2 repeats)
//   https://support.microsoft.com/en-us/office/fill-data-automatically-in-worksheet-cells-74e31bdd-d993-45da-aa82-35a236c5b5db
//   "Enter a series of numbers, dates, or other items" (Item1 gives Item2, Item3, Item4; Monday gives Tuesday,
//   Wednesday, Thursday; Monday, Wednesday gives Friday)
//   https://support.microsoft.com/en-us/office/enter-a-series-of-numbers-dates-or-other-items-41e0bbf2-7198-4d78-8545-fdd4709976b4
//   "Automatically number rows in Excel" (drag up or left to fill in decreasing order)
//   https://support.microsoft.com/en-us/office/automatically-number-rows-in-excel-76ce49e3-d8d2-459b-bd85-ee1d3973e6e6
//   "What is Excel?" (Jan and Feb, 2014 and 2015 start a series)
//   https://support.microsoft.com/en-us/excel/get-started/what-is-excel
const plan = (...values) => planLine(values.map(value), values.map(() => false));
const next = (p, from, count) => Array.from({ length: count }, (_, i) => p.at(from + i));

test('series: evenly spaced numbers keep their step, in Microsoft\'s own examples', () => {
  assert.deepEqual(next(plan(1, 2), 2, 3), [3, 4, 5]);
  assert.deepEqual(next(plan(1, 3), 2, 3), [5, 7, 9]);
  assert.deepEqual(next(plan(100, 95), 2, 2), [90, 85]);
  assert.deepEqual(next(plan(2, 4), 2, 2), [6, 8]);
  assert.deepEqual(next(plan(2014, 2015), 2, 2), [2016, 2017]);
  // Three evenly spaced values keep the same step: 10, 20, 30 then 40, 50.
  assert.deepEqual(next(plan(10, 20, 30), 3, 2), [40, 50]);
  // Dragging up or left runs the series backwards: before 1, 2 come 0 and -1.
  assert.deepEqual([plan(1, 2).at(-1), plan(1, 2).at(-2)], [0, -1]);
  // The source cells keep their own values.
  assert.deepEqual(next(plan(1, 3), 0, 2), [1, 3]);
});

test('series: unevenly spaced numbers follow their least-squares line', () => {
  // 3, 5, 8 at x = 0, 1, 2: the mean of y is 16/3 at x = 1 and the slope is
  // ((0-1)(3-16/3) + (2-1)(8-16/3)) / 2 = 5/2. So x = 3 gives 16/3 + 5 = 31/3,
  // x = 4 gives 16/3 + 15/2 = 77/6, and x = -1 gives 16/3 - 5 = 1/3.
  const p = plan(3, 5, 8);
  const close = (got, want) => assert.ok(Math.abs(got - want) < 1e-12, `${got} is not ${want}`);
  close(p.at(3), 31 / 3);
  close(p.at(4), 77 / 6);
  close(p.at(-1), 1 / 3);
  assert.deepEqual(next(p, 0, 3), [3, 5, 8]);
  // A series that leaves the range of numbers has no value there.
  assert.equal(plan(1e308, 1.7e308).at(2), undefined);
});

test('series: text ending in a number counts up', () => {
  assert.deepEqual(next(plan('Item1'), 1, 3), ['Item2', 'Item3', 'Item4']);
  assert.deepEqual(next(plan('Product 9'), 1, 2), ['Product 10', 'Product 11']);
  // Two cells set the step: Item1, Item3 then Item5, Item7.
  assert.deepEqual(next(plan('Item1', 'Item3'), 2, 2), ['Item5', 'Item7']);
  // Backwards it stops at zero; there is no Item-1.
  assert.deepEqual([plan('Item1').at(-1), plan('Item1').at(-2)], ['Item0', undefined]);
  for (const refused of [['Item 01'], ['Q1 2024'], ['1st Period'], ['Item1', 'Item3', 'Item4'], ['Item1', 'Part2'], ['-1'], ['Item1', 2]]) {
    assert.equal(plan(...refused).kind, 'refuse', JSON.stringify(refused));
  }
});

test('series: day and month names continue their list and wrap round', () => {
  assert.deepEqual(next(plan('Monday'), 1, 3), ['Tuesday', 'Wednesday', 'Thursday']);
  // Monday, Wednesday skips every other day: Friday, then Sunday and Tuesday.
  assert.deepEqual(next(plan('Monday', 'Wednesday'), 2, 3), ['Friday', 'Sunday', 'Tuesday']);
  assert.deepEqual(next(plan('Jan', 'Feb'), 2, 2), ['Mar', 'Apr']);
  assert.deepEqual(next(plan('Fri'), 1, 3), ['Sat', 'Sun', 'Mon']);
  assert.deepEqual(next(plan('November'), 1, 3), ['December', 'January', 'February']);
  // Before Monday comes Sunday.
  assert.equal(plan('Monday').at(-1), 'Sunday');
  // The source's capitals are kept.
  assert.deepEqual(next(plan('mon'), 1, 1), ['tue']);
  assert.deepEqual(next(plan('MARCH'), 1, 1), ['APRIL']);
  // Apr, May is the short list; May, June is the long one.
  assert.deepEqual(next(plan('Apr', 'May'), 2, 1), ['Jun']);
  assert.deepEqual(next(plan('May', 'June'), 2, 1), ['July']);
  for (const refused of [['May'], ['mONDAY'], ['Mon', 'Tuesday'], ['Mon', 'tue'], ['Mon', 'Tue', 'Thu'], ['Jan', 'Mon'], [' jan ']]) {
    assert.equal(plan(...refused).kind, 'refuse', JSON.stringify(refused));
  }
});

test('series: what Excel copies is copied, and dates and mixed lines are refused', () => {
  const none = [false, false, false];
  // Microsoft: "For the series 2, 2, 2, 2..., type 2 in first cell only."
  assert.equal(plan(2).kind, 'copy');
  assert.equal(plan('Total').kind, 'copy');
  assert.equal(plan(true, false).kind, 'copy');
  assert.equal(planLine([formula('A1*2', 20), formula('A2*2', 40)], none).kind, 'copy');
  assert.equal(planLine([value(5), formula('A1+1', 6), value('Total')], none).kind, 'copy');
  // A number drawn as a date or a time is a date series, which is not built.
  assert.equal(planLine([value(46000)], [true]).kind, 'refuse');
  assert.equal(planLine([value(46000), value(46001)], [true, true]).kind, 'refuse');
  // A formula or a blank among the numbers, and numbers mixed with text.
  assert.equal(planLine([value(1), value(2), formula('A1+1', 3)], none).kind, 'refuse');
  assert.equal(planLine([value(1), undefined, value(3)], none).kind, 'refuse');
  assert.equal(planLine([value(1), value(2), value('Total')], none).kind, 'refuse');
});

test('date formats: date and time codes are recognised, number codes are not', () => {
  for (const code of ['m/d/yyyy', 'yyyy-mm-dd', 'd-mmm-yy', 'h:mm AM/PM', '[h]:mm:ss', 'mmm yyyy', '[$-409]mmmm d, yyyy']) {
    assert.equal(isDateFormat(code), true, code);
  }
  for (const code of ['General', '0', '0.00', '#,##0', '0%', '0.00E+00', '# ?/?', '@', '#,##0.00_);[Red](#,##0.00)', '0 "days"', '0\\h', '[$$-409]#,##0.00']) {
    assert.equal(isDateFormat(code), false, code);
  }
});

test('copy id: read from the marker Univer puts in the HTML it copies', () => {
  assert.equal(copyIdOf('<google-sheets-html-origin><table data-copy-id="abc123"><tr><td>1</td></tr></table>'), 'abc123');
  assert.equal(copyIdOf('<table><tr><td>1</td></tr></table>'), null);
});
