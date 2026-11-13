// What the function reference says about each function Forge implements:
// the arguments in Excel's order (optional ones in brackets) and one plain
// sentence. The list of functions itself comes from catalog/formulas.json
// (`virtual:forge-catalog`); a function with no note here is still listed,
// with its argument count. `limit` states a documented Workbook Forge limit
// from the catalog's coverage note; it is not a claim about Excel.
//
// scripts/check-function-notes.mjs checks every note against the catalog's
// argument counts.

export interface FunctionNote {
  /** Arguments in order, optional ones in brackets, `...` when repeatable. */
  args: string;
  summary: string;
  limit?: string;
}

const ARRAY_LIMIT = 'Returns several values. They are not placed into neighboring cells yet; select the cell to preview them.';
const EXACT_ONLY = 'Exact match only, searching first to last or last to first. Approximate, wildcard and binary modes are refused.';
const SAME_SIZE = 'The two ranges must be the same size.';
const NO_RANGES = 'Takes single values, not ranges.';

export const FUNCTION_NOTES: Record<string, FunctionNote> = {
  // Math
  ABS: { args: 'number', summary: 'The number without its sign.' },
  COMBIN: { args: 'number, number_chosen', summary: 'How many ways to choose items when order does not matter.' },
  COMBINA: { args: 'number, number_chosen', summary: 'How many ways to choose items, with repeats, when order does not matter.' },
  EVEN: { args: 'number', summary: 'Rounds away from zero to the nearest even whole number.' },
  FACT: { args: 'number', summary: 'The factorial of a number.' },
  FACTDOUBLE: { args: 'number', summary: 'The double factorial of a number.', limit: NO_RANGES },
  GCD: { args: 'number1, [number2], ...', summary: 'The greatest common divisor.', limit: NO_RANGES },
  INT: { args: 'number', summary: 'Rounds down to the nearest whole number.' },
  LCM: { args: 'number1, [number2], ...', summary: 'The least common multiple.', limit: NO_RANGES },
  MOD: { args: 'number, divisor', summary: 'The remainder after division.' },
  ODD: { args: 'number', summary: 'Rounds away from zero to the nearest odd whole number.' },
  QUOTIENT: { args: 'numerator, denominator', summary: 'The whole-number part of a division.' },
  RAND: { args: '', summary: 'A random number from 0 up to 1. Changes on every calculation.' },
  RANDARRAY: { args: '[rows], [columns], [min], [max], [whole_number]', summary: 'A block of random numbers.', limit: ARRAY_LIMIT },
  RANDBETWEEN: { args: 'bottom, top', summary: 'A random whole number between two numbers. Changes on every calculation.' },
  ROUND: { args: 'number, num_digits', summary: 'Rounds to a number of digits.' },
  ROUNDDOWN: { args: 'number, num_digits', summary: 'Rounds toward zero.' },
  ROUNDUP: { args: 'number, num_digits', summary: 'Rounds away from zero.' },
  SEQUENCE: { args: 'rows, [columns], [start], [step]', summary: 'A list of numbers in order, such as 1, 2, 3.', limit: ARRAY_LIMIT },
  SUM: { args: 'number1, [number2], ...', summary: 'Adds numbers and ranges.' },
  SUMIF: { args: 'range, criteria, [sum_range]', summary: 'Adds the cells that meet one condition.', limit: SAME_SIZE },
  SUMIFS: { args: 'sum_range, criteria_range1, criteria1, ...', summary: 'Adds the cells that meet every condition.' },
  TRUNC: { args: 'number, [num_digits]', summary: 'Cuts a number off at a number of digits without rounding.' },

  // Financial
  AMORDEGRC: { args: 'cost, date_purchased, first_period, salvage, period, rate, [basis]', summary: 'Depreciation for a period under the French declining method.' },
  AMORLINC: { args: 'cost, date_purchased, first_period, salvage, period, rate, [basis]', summary: 'Depreciation for a period under the French straight-line method.' },
  COUPDAYBS: { args: 'settlement, maturity, frequency, [basis]', summary: 'Days from the start of the coupon period to settlement.' },
  COUPDAYS: { args: 'settlement, maturity, frequency, [basis]', summary: 'Days in the coupon period that holds the settlement date.' },
  COUPDAYSNC: { args: 'settlement, maturity, frequency, [basis]', summary: 'Days from settlement to the next coupon date.' },
  COUPNCD: { args: 'settlement, maturity, frequency, [basis]', summary: 'The next coupon date after settlement.' },
  COUPNUM: { args: 'settlement, maturity, frequency, [basis]', summary: 'Coupons payable between settlement and maturity.' },
  COUPPCD: { args: 'settlement, maturity, frequency, [basis]', summary: 'The last coupon date before settlement.' },
  CUMIPMT: { args: 'rate, nper, pv, start_period, end_period, type', summary: 'Total interest paid on a loan between two periods.' },
  CUMPRINC: { args: 'rate, nper, pv, start_period, end_period, type', summary: 'Total principal paid on a loan between two periods.' },
  DB: { args: 'cost, salvage, life, period, [month]', summary: 'Depreciation for a period, fixed declining balance.' },
  DDB: { args: 'cost, salvage, life, period, [factor]', summary: 'Depreciation for a period, double declining balance.' },
  FV: { args: 'rate, nper, pmt, [pv], [type]', summary: 'The future value of an investment with level payments.' },
  IPMT: { args: 'rate, per, nper, pv, [fv], [type]', summary: 'The interest part of one loan payment.' },
  NPER: { args: 'rate, pmt, pv, [fv], [type]', summary: 'How many periods an investment or loan runs.' },
  PMT: { args: 'rate, nper, pv, [fv], [type]', summary: 'The level payment on a loan.' },
  PPMT: { args: 'rate, per, nper, pv, [fv], [type]', summary: 'The principal part of one loan payment.' },
  PV: { args: 'rate, nper, pmt, [fv], [type]', summary: 'The present value of level payments.' },
  SLN: { args: 'cost, salvage, life', summary: 'Straight-line depreciation for one period.' },
  SYD: { args: 'cost, salvage, life, per', summary: "Sum-of-years' digits depreciation for a period." },
  VDB: { args: 'cost, salvage, life, start_period, end_period, [factor], [no_switch]', summary: 'Declining balance depreciation between any two points in time.' },

  // Logical
  AND: { args: 'logical1, [logical2], ...', summary: 'TRUE when every condition is true.' },
  IF: { args: 'logical_test, value_if_true, [value_if_false]', summary: 'One value when a condition is true, another when it is false.' },
  IFERROR: { args: 'value, value_if_error', summary: 'A fallback when a formula returns an error.' },
  IFNA: { args: 'value, value_if_na', summary: 'A fallback when a formula returns #N/A.' },
  IFS: { args: 'logical_test1, value_if_true1, ...', summary: 'The value for the first condition that is true.' },
  NOT: { args: 'logical', summary: 'Reverses TRUE and FALSE.' },
  OR: { args: 'logical1, [logical2], ...', summary: 'TRUE when any condition is true.' },
  SWITCH: { args: 'expression, value1, result1, ...', summary: 'The result paired with the first matching value, or a default.' },

  // Statistical
  AVERAGE: { args: 'number1, [number2], ...', summary: 'The arithmetic mean.' },
  AVERAGEIF: { args: 'range, criteria, [average_range]', summary: 'The mean of the cells that meet one condition.', limit: SAME_SIZE },
  AVERAGEIFS: { args: 'average_range, criteria_range1, criteria1, ...', summary: 'The mean of the cells that meet every condition.' },
  COUNT: { args: 'value1, [value2], ...', summary: 'How many cells hold numbers.' },
  COUNTA: { args: 'value1, [value2], ...', summary: 'How many cells are not empty.' },
  COUNTBLANK: { args: 'range', summary: 'How many cells in a range are empty.' },
  COUNTIF: { args: 'range, criteria', summary: 'How many cells meet one condition.' },
  COUNTIFS: { args: 'criteria_range1, criteria1, ...', summary: 'How many rows meet every condition.' },
  MAX: { args: 'number1, [number2], ...', summary: 'The largest number.' },
  MAXIFS: { args: 'max_range, criteria_range1, criteria1, ...', summary: 'The largest number among cells that meet every condition.' },
  MIN: { args: 'number1, [number2], ...', summary: 'The smallest number.' },
  MINIFS: { args: 'min_range, criteria_range1, criteria1, ...', summary: 'The smallest number among cells that meet every condition.' },
  PERMUT: { args: 'number, number_chosen', summary: 'How many ways to choose items when order matters.' },
  PERMUTATIONA: { args: 'number, number_chosen', summary: 'How many ways to choose items, with repeats, when order matters.' },

  // Text
  CONCAT: { args: 'text1, [text2], ...', summary: 'Joins text from cells and ranges.' },
  FIND: { args: 'find_text, within_text, [start_num]', summary: 'Where one text starts inside another. Case matters.' },
  LEFT: { args: 'text, [num_chars]', summary: 'The first characters of a text.' },
  LEN: { args: 'text', summary: 'How many characters a text has.' },
  LOWER: { args: 'text', summary: 'Text in lower case.' },
  MID: { args: 'text, start_num, num_chars', summary: 'Characters from the middle of a text.' },
  RIGHT: { args: 'text, [num_chars]', summary: 'The last characters of a text.' },
  SEARCH: { args: 'find_text, within_text, [start_num]', summary: 'Where one text starts inside another. Case does not matter.' },
  SUBSTITUTE: { args: 'text, old_text, new_text, [instance_num]', summary: 'Replaces one piece of text with another.' },
  TEXTAFTER: { args: 'text, delimiter, [instance_num], [match_mode], [match_end], [if_not_found]', summary: 'The text after a delimiter.' },
  TEXTBEFORE: { args: 'text, delimiter, [instance_num], [match_mode], [match_end], [if_not_found]', summary: 'The text before a delimiter.' },
  TEXTJOIN: { args: 'delimiter, ignore_empty, text1, ...', summary: 'Joins text with a separator between each piece.' },
  TRIM: { args: 'text', summary: 'Removes extra spaces.' },
  UPPER: { args: 'text', summary: 'Text in upper case.' },

  // Date and time
  DATE: { args: 'year, month, day', summary: 'A date from a year, a month and a day.' },
  DATEDIF: { args: 'start_date, end_date, unit', summary: 'Whole days, months or years between two dates.' },
  DATEVALUE: { args: 'date_text', summary: 'A date written as text, as a date.' },
  DAY: { args: 'serial_number', summary: 'The day of the month, 1 to 31.' },
  DAYS: { args: 'end_date, start_date', summary: 'Days between two dates.' },
  DAYS360: { args: 'start_date, end_date, [method]', summary: 'Days between two dates on a 360-day year.' },
  EDATE: { args: 'start_date, months', summary: 'The same day a number of months earlier or later.' },
  EOMONTH: { args: 'start_date, months', summary: 'The last day of a month, a number of months away.' },
  HOUR: { args: 'serial_number', summary: 'The hour of a time, 0 to 23.' },
  ISOWEEKNUM: { args: 'date', summary: 'The ISO week number of a date.' },
  MINUTE: { args: 'serial_number', summary: 'The minute of a time, 0 to 59.' },
  MONTH: { args: 'serial_number', summary: 'The month of a date, 1 to 12.' },
  NETWORKDAYS: { args: 'start_date, end_date, [holidays]', summary: 'Working days between two dates.' },
  'NETWORKDAYS.INTL': { args: 'start_date, end_date, [weekend], [holidays]', summary: 'Working days between two dates, with your own weekend.' },
  NOW: { args: '', summary: 'The current date and time. Changes on every calculation.' },
  SECOND: { args: 'serial_number', summary: 'The second of a time, 0 to 59.' },
  TIME: { args: 'hour, minute, second', summary: 'A time from hours, minutes and seconds.' },
  TIMEVALUE: { args: 'time_text', summary: 'A time written as text, as a time.' },
  TODAY: { args: '', summary: "Today's date. Changes each day." },
  WEEKDAY: { args: 'serial_number, [return_type]', summary: 'The day of the week as a number.' },
  WEEKNUM: { args: 'serial_number, [return_type]', summary: 'The week number of a date.' },
  WORKDAY: { args: 'start_date, days, [holidays]', summary: 'The date a number of working days away.' },
  'WORKDAY.INTL': { args: 'start_date, days, [weekend], [holidays]', summary: 'The date a number of working days away, with your own weekend.' },
  YEAR: { args: 'serial_number', summary: 'The year of a date.' },
  YEARFRAC: { args: 'start_date, end_date, [basis]', summary: 'The fraction of a year between two dates.' },

  // Lookup
  FILTER: { args: 'array, include, [if_empty]', summary: 'The rows or columns that meet a condition.', limit: ARRAY_LIMIT },
  INDEX: { args: 'array, row_num, [column_num], [area_num]', summary: 'The value at a row and column of a range.' },
  MATCH: { args: 'lookup_value, lookup_array, [match_type]', summary: 'The position of a value in a row or column.' },
  SORT: { args: 'array, [sort_index], [sort_order], [by_col]', summary: 'A range sorted by one of its columns or rows.', limit: ARRAY_LIMIT },
  UNIQUE: { args: 'array, [by_col], [exactly_once]', summary: 'The distinct rows or columns of a range.', limit: ARRAY_LIMIT },
  VLOOKUP: { args: 'lookup_value, table_array, col_index_num, [range_lookup]', summary: 'Finds a value in the first column and returns one from the same row.' },
  XLOOKUP: { args: 'lookup_value, lookup_array, return_array, [if_not_found], [match_mode], [search_mode]', summary: 'Finds a value in one range and returns the matching item from another.', limit: EXACT_ONLY },
  XMATCH: { args: 'lookup_value, lookup_array, [match_mode], [search_mode]', summary: 'The position of a value in a row or column.', limit: EXACT_ONLY },

  // Information
  ISBLANK: { args: 'value', summary: 'TRUE when a cell is empty.' },
  ISERR: { args: 'value', summary: 'TRUE for any error except #N/A.' },
  ISERROR: { args: 'value', summary: 'TRUE for any error.' },
  ISEVEN: { args: 'number', summary: 'TRUE when a number is even.' },
  ISLOGICAL: { args: 'value', summary: 'TRUE for TRUE or FALSE.' },
  ISNA: { args: 'value', summary: 'TRUE for the #N/A error.' },
  ISNUMBER: { args: 'value', summary: 'TRUE for a number.' },
  ISODD: { args: 'number', summary: 'TRUE when a number is odd.' },
  ISTEXT: { args: 'value', summary: 'TRUE for text.' },
  NA: { args: '', summary: 'The #N/A error, meaning no value is available.' },
};

/** Catalog category ids -> the headings the reference shows, in display order. */
export const CATEGORY_LABELS: [id: string, label: string][] = [
  ['math-trigonometry', 'Math'],
  ['statistical', 'Statistics'],
  ['logical', 'Logic'],
  ['lookup-reference', 'Lookup'],
  ['text', 'Text'],
  ['date-time', 'Date and time'],
  ['financial', 'Financial'],
  ['information', 'Information'],
  ['engineering', 'Engineering'],
  ['database', 'Database'],
  ['compatibility', 'Compatibility'],
  ['cube', 'Cube'],
  ['web', 'Web'],
  ['add-in-automation', 'Add-in'],
];

/** "2 arguments", "1 to 3 arguments", "2 or more arguments". */
export function arityText(min: number, max: number | null): string {
  const plural = (n: number) => `${n} argument${n === 1 ? '' : 's'}`;
  if (max === null || max >= 254) return min === 0 ? 'Any number of arguments' : `${min} or more arguments`;
  if (min === max) return min === 0 ? 'No arguments' : plural(min);
  return `${min} to ${plural(max)}`;
}

export interface FunctionArgument {
  name: string;
  optional: boolean;
  /** The argument may be given again and again (a trailing `...` in the note). */
  repeat: boolean;
}

/** `'number1, [number2], ...'` -> number1 (required), number2 (optional, repeatable). */
export function parseArguments(args: string): FunctionArgument[] {
  const parts = args.split(',').map((part) => part.trim()).filter(Boolean);
  const repeat = parts.at(-1) === '...';
  const named = repeat ? parts.slice(0, -1) : parts;
  return named.map((part, index) => {
    const optional = part.startsWith('[') && part.endsWith(']');
    return { name: optional ? part.slice(1, -1) : part, optional, repeat: repeat && index === named.length - 1 };
  });
}
