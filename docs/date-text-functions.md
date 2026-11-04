# DATEDIF, DATEVALUE and TIMEVALUE

Both engines (Python and Rust) implement these three functions with the same
rules, pinned by the shared cases in `fixtures/formula-cases.jsonl`
(`datedif-*`, `datevalue-*`, `timevalue-*`). Dates use the 1900 date system,
including the fictitious 1900-02-29 (serial 60).

## DATEVALUE and TIMEVALUE: unambiguous text only

Workbook Forge has no locale, so it accepts only forms that read the same way
everywhere and refuses the rest with `#VALUE!` rather than guessing.

Accepted date forms (leading and trailing spaces and tabs are trimmed; month
names are English, full or three-letter, plus `Sept`, any case):

| Form | Example |
|---|---|
| ISO year-month-day, `-` or `/` | `2026-01-15`, `2026/1/5` |
| month/day/year when the day is above 12 or equals the month | `1/15/2026`, `3/3/2026` |
| day month-name year | `15 Jan 2026`, `15-jan-2026` |
| month-name day year | `January 15, 2026`, `Sept 9 2026` |

Any date form may be followed by a time, separated by a space (or `T` for
ISO): `H:MM`, `H:MM:SS`, `H:MM:SS.fff`, or an hour with `AM`/`PM`
(`6:45 PM`, `9am`). Years run from 1900 through 9999.

Refused with `#VALUE!`:

- two-digit years (`1/15/26`, `15 Jan 26`);
- missing years (`Jan 15`, `1/15`);
- day/month dates that have two readings (`5/6/2026`, `1/2/2026 10:00`);
- impossible dates (`2026-02-30`), years before 1900, unknown month names;
- non-text arguments (`DATEVALUE(46037)`), empty text, text over 255 characters.

`DATEVALUE` returns the whole-day serial and ignores a time part; time-only
text is `#VALUE!`. `TIMEVALUE` returns the fraction of a day and ignores a date
part; date-only text is 0. Hours are 0–23 (1–12 with AM/PM), minutes and
seconds two digits, 0–59.

## DATEDIF(start, end, unit)

Dates may be serials (the time fraction is dropped) or accepted date text.
Units are `Y`, `M`, `D`, `MD`, `YM`, `YD` in any case; anything else is `#NUM!`,
and so is a start after the end or a serial outside 0–2958465. Errors and
ranges are checked in argument order first.

- `Y`, `M`: complete years and months; a month counts only once the start's
  day of month is reached.
- `D`: days between the whole-day serials.
- `YM`: months left after whole years.
- `MD`: days left after whole months. When the end's day is before the start's
  day, Workbook Forge counts from `DATE(end year, end month − 1, start day)`
  and lets DATE roll an impossible day forward. That roll can make the count
  negative: `=DATEDIF(DATE(2015,1,31),DATE(2015,3,1),"MD")` is -2. This is a
  Workbook Forge profile, not an Excel result.
- `YD`: days from the most recent anniversary of the start date (built with
  DATE, so a 29 February start rolls to 1 March in a common year).

## Evidence

The units, the accepted text forms, the refusals, and the `MD`/`YD` anchoring
are Workbook Forge profiles. They have not been spot-checked in Excel Desktop,
and this tree has no receipt for them in `docs/excel-observations.md`.
