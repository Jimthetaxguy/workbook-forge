---
author: unknown
created: null
agent: codex/Codex
date: 2026-09-28
type: behavior-reference
status: active
summary: Current evaluator and workbook boundaries, distinguishing shared fixtures from independent Excel evidence.
provenance_note: Original creator and creation time were not recorded; these notes were consolidated on 2026-09-25.
---
# Workbook Forge behavior profiles

This page explains the supported evaluator behavior and workbook boundaries.
The [formula catalog](../catalog/formulas.json) (`semantic_specs`) is the maintained
per-function record. A *profile* declares Workbook Forge's rules; it does not by
itself establish Excel compatibility. The [native primitive guide](primitives.md)
describes the smaller direct-call and named-input interface over these evaluators.

## General

Unsupported syntax returns typed errors instead of approximations. Shared fixtures
verify Python/Rust agreement and their stated expected results. Three targeted
formula observations have succeeded in Excel; broader function-family checks and
the complete workbook roundtrip remain outstanding. See [Excel observations](excel-observations.md)
for the exact evidence. The engines are not full Excel calculation engines.

Both evaluators cap formula length at 8,192 UTF-16 code units and function nesting
at 64 levels. Parenthesis nesting is limited to 96 levels, and wildcard matching
to 5,000,000 matching-state steps per evaluation. The latter two limits are
Workbook Forge safety profiles. Resource limits also apply at the workbook,
agent transport and native composition boundaries; one interface's larger input
budget does not raise the evaluator limit. The toolkit expression tree is capped
at 96 levels counted from the root as level 1 in both engines, so a flat
`1+1+...` chain of 96 terms is accepted and one of 97 terms is refused with
`resource_limit`; the evaluator itself limits parenthesis nesting, not chain length.

Arithmetic (`+`, `-`, `*`, `/`, `^`) returns `#NUM!` whenever its binary64 result
is not finite, so an overflow such as `1E308*10` is an error rather than an
infinite number; no formula value is ever non-finite. Text that a language float
parser would read as infinity or NaN (`"inf"`, `"Infinity"`, `"nan"`) is not
numeric text and coerces to `#VALUE!` like any other non-numeric text. Microsoft
documents 9.99999999999999E+307 as the largest allowed number but not the exact
error for an overflowing operator; the `#NUM!` choice is a Workbook Forge profile.

Known OOXML compatibility prefixes are normalized for function dispatch and
support lookup while imported formula text remains intact. A catalog entry or
recognized prefix does not imply that an unsupported function can calculate.

## Text limits and number-to-text conversion

Python and Rust cap formula-produced text at 32,767 UTF-16 code units. `&`, `CONCAT`, `TEXTJOIN`, and `SUBSTITUTE` preflight result size before building joined or replaced strings; the evaluator boundary also checks literal, case-converted, and array text results. This follows [Excel's documented 32,767-character cell limit](https://support.microsoft.com/en-us/excel/excel-specifications-and-limits) while counting supplementary Unicode characters as two UTF-16 units.

An error value passed to a text function or to `&` propagates unchanged: `LEN(NA())` is `#N/A`, and `MID(A1,1,2)` on an error cell returns that error, never the first characters of the error's name. This matches Microsoft's general rule that an error in an argument is the result.

Numeric-to-text conversion is shared across Python and Rust for `CONCAT`, `TEXTJOIN`, `&`, and text functions: it uses shortest round-tripping decimal text, omits `.0` for integer-valued numbers, renders negative zero as `0`, and uses lowercase scientific notation with at least two exponent digits. Finite numeric values use binary64; integer literals outside its finite range return `#NUM!`. Microsoft says concatenation uses the underlying number value and recommends `TEXT` when explicit display formatting is needed ([combine text and numbers](https://support.microsoft.com/en-us/excel/combine-text-and-numbers)); the exact default spelling is a Workbook Forge profile and has not been checked directly in Excel.

## Functions by family

### Logical selection (IFS, SWITCH)

`IFS` returns the first true condition's value; `SWITCH` returns the result for the first matching value, with optional defaults. Both use a documented short-circuiting profile that still needs an Excel spot-check; their selected branches may return shaped arrays through `evaluate_result`. The SWITCH comparison profile treats ASCII text without case and non-ASCII text exactly; it does not coerce across types or apply locale collation, and it still needs an Excel spot-check.

### Counting (COUNT, COUNTA)

`COUNT` counts numbers and does not count error values or logical values found in a range; `COUNTA` counts every non-empty cell, including error values. Neither returns an error found in its arguments. This follows Microsoft's COUNT and COUNTA examples, in which a range holding a date, two numbers, TRUE and `#DIV/0!` counts as 3 and 5. `SUM`, `AVERAGE`, `MIN` and `MAX` still return the first error in their range.

### Criteria and conditional aggregation

Criteria support comparison operators, case-insensitive text, `*` and `?` wildcards, and `~` escapes. `COUNTBLANK` counts empty cells and empty text but excludes zero. `MINIFS` and `MAXIFS` require equal-shaped ranges and return zero when no numeric result matches.

A criterion operand takes the type its text spells: `TRUE` and `FALSE` are logical, numeric text such as `">1"` is a number, and anything else is text. A cell is compared only against an operand of its own type; a cell of another type is unequal, so `<>` selects it and every other operator skips it. `COUNTIF(A1:A7,">1")` over 5, 7, `fig`, `kiwi`, TRUE, the text `TRUE` and the text `2` is therefore 2, and `COUNTIF(A7,2)` over the text `2` is 0. This is a Workbook Forge profile shared by both engines; it has not been checked in Excel, which is reported to count numbers stored as text against a numeric criterion.

An error cell in a criteria range never propagates: it is skipped by every criterion except `<>`, which counts it as unequal, and an error criterion. Criterion text spelling an Excel error code (`"#N/A"`, `"<>#N/A"`) or an error value (`NA()`) selects cells holding that error; errors have no order, so a relational operator with an error operand selects nothing. An error in the summed, averaged, or min/max range is read only for matched cells, and then propagates. The error rules are a Workbook Forge profile that has not been checked in Excel.

`SUMIF` and `AVERAGEIF` currently require matching criteria and value-range shapes even though Excel aligns differently sized value ranges from their top-left cell; that gap is explicit in the semantic catalog.

### Text (TEXTBEFORE, TEXTAFTER)

`TEXTBEFORE` and `TEXTAFTER` support omitted optional arguments, negative occurrence numbers, end-of-text matching, and lazy fallback evaluation; locale-specific collation is not modeled.

### Rounding and remainders

`INT` floors toward negative infinity; `TRUNC` and `ROUNDDOWN` move toward zero; `ROUNDUP` moves away from zero; `MOD` follows the divisor's sign; and `QUOTIENT` truncates its quotient toward zero. Decimal precision is bounded to -308 through 308, and fractional `num_digits` values are truncated toward zero by this implementation; Microsoft does not specify that fractional case, and it has not been checked in Excel.

`ROUND`, `ROUNDUP`, `ROUNDDOWN`, `TRUNC`, `INT`, `EVEN`, `ODD` and `QUOTIENT` share one rounding rule. The stored binary value is rounded exactly, with one exception: a number within two binary64 values of a boundary is taken as lying on that boundary. The boundaries are the multiples of the requested place and, for `ROUND`, the halves between them. When more than one boundary is that close, the one fewest values away is taken, and a multiple before a half. A whole number below 2^53 is stored exactly and is always rounded exactly. Nothing but zero is taken as zero, and a result of zero carries no sign.

The exception exists because a decimal number is rarely stored exactly. 19.99 is stored as 19.989999999999998, which is the binary64 value nearest to it, so `TRUNC(19.99,2)` is 19.99 and not 19.98. In the same way `ROUNDUP(0.07,2)` is 0.07 and `ROUND(1.005,2)` is 1.01. Two values of allowance also cover one operation on written numbers: `ROUNDUP(0.1+0.2,1)` is 0.3 and `INT(4.35*100)` is 435.

What follows from the rule:

- A number written with up to 15 significant digits is always rounded as decimal arithmetic on the written number would round it, at any magnitude and any number of places.
- A number that differs from a boundary by more than two binary64 values is rounded exactly. `ROUND(123456789012.3449,2)` is 123456789012.34, and `ROUNDUP(1000000000000004,-2)` is 1000000000000100.
- A number with 16 or 17 significant digits that sits within two values of a boundary is taken as lying on it. `TRUNC(0.9999999999999999)` and `INT(0.9999999999999999)` are 1.
- Noise from a longer chain of operations can exceed two values. `ROUNDDOWN` of a long sum can then land a cent low. Use `ROUND` on the sum first.

This is a Workbook Forge rule. Microsoft states that Excel keeps 15 significant digits ([specifications and limits](https://support.microsoft.com/en-us/excel/excel-specifications-and-limits)) and does not state how the rounding functions treat the digits past them, and the rule has not been checked in Excel. Observations that would settle it: `ROUNDUP(0.1+0.2,1)`, `TRUNC(4.35*100)`, `INT(4.35*100)`, `ROUND(123456789012+0.3449,2)`, `ROUNDUP(1E15+4,-2)` and `TRUNC(1-2^-53)`.

Python and Rust implement the rule separately, Python with decimal arithmetic and Rust on decimal digits, and agree bit for bit. Expected values in the fixtures and tests come from Microsoft's published examples and from decimal or integer arithmetic on the written number, never from either engine.

`MOD` still works on the stored value: `MOD(19.99*100,1)` is 0.9999999999997726 while `INT(19.99*100)` is 1999. `DB` rounds its rate to three places by the same rule, but it computes the rate with logarithms, and that noise can exceed two values when the rate falls on a half.

### Dates and times

`MONTH`, `DAY`, and `YEAR` read the integer date portion of numeric serials; `DAYS` subtracts numeric serials, preserving time fractions; `EDATE` clamps to the target month; and `EOMONTH` returns the target month's last day. `HOUR`, `MINUTE`, and `SECOND` extract whole-second components from date/time serials; the 1900 serial ceiling and negative/non-finite error behavior are unverified evaluator boundaries. `TIME` normalizes components and returns a fraction of a day. The extractor's half-ULP precision correction and `TIME` fractional-component truncation are evaluator profile rules that need Excel spot-checks. `WEEKDAY` supports return types 1, 2, 3, and 11–17; `WEEKNUM` supports System 1 selectors 1, 2, and 11–17 plus ISO selector 21; and `ISOWEEKNUM` uses ISO week-year rules. These functions floor time fractions. Fractional selector truncation and exact results around the fictional serial 60 remain unverified evaluator-profile choices. Human-readable time strings such as `6:45 PM` are documented by Microsoft for the extractors but are not parsed here; numeric text follows the shared number coercion. `EDATE` and `EOMONTH` truncate fractional month offsets toward zero. Their date semantics use the 1900 system, preserve serial 60, and do not model workbook-specific 1904 settings or locale-sensitive text dates. `EDATE` and `EOMONTH` return whole-day serials and discard start-date time fractions as profile behavior that still needs an Excel spot-check. Both refuse a result before 1899-12-31 (serial 0) with `#NUM!`, which Microsoft documents for a result outside the supported range; `EOMONTH(1,-1)` is the lowest result, 0, and `EDATE(1,-1)` is `#NUM!` in both engines.

### Error predicates

`ISNA`, `ISERR`, and `ISERROR` distinguish the `#N/A` error from other supported Excel error values.

### Business calendars (WORKDAY, NETWORKDAYS, and the .INTL variants)

`WORKDAY` uses Saturday/Sunday weekends, truncates fractional `days` as Microsoft documents, and excludes holiday dates; its zero-offset weekend result, fractional date handling, and holiday-cell coercion still need Excel spot-checks. `NETWORKDAYS` counts weekday endpoints inclusively; reversed-range sign and fractional holiday handling are evaluator profiles. Non-finite numeric date expressions return `#NUM!` as an evaluator profile that Microsoft does not specify and that has not been checked in Excel. `WORKDAY.INTL` and `NETWORKDAYS.INTL` support numeric weekend selectors 1-7 and 11-17 or Monday-first seven-character masks. The all-one mask returns zero in `NETWORKDAYS.INTL`; `WORKDAY.INTL` rejects it, though Microsoft does not specify the exact error code. Both accept scalar or cell-range holidays, while array constants remain outside the parser. Weekend coercion, fractional date handling, holiday-cell details, and serial boundaries are marked as profiles pending Excel spot-checks. Microsoft’s last NETWORKDAYS.INTL example says 22 in the prose but 20 in the result column; the shared case follows the result column. Both use the 1900 date system, reject locale date text and array constants, and have unverified boundaries at serials 0 and 60.

### Day count (DAYS360, YEARFRAC)

`DAYS360` implements the default US NASD and European methods, and `YEARFRAC` implements bases 0–4 with Microsoft’s documented argument truncation and error codes. The fixture corpus includes Microsoft’s published examples. DAYS360 accepts omitted, blank-cell, FALSE, or numeric 0 method values for US NASD and TRUE or numeric 1 for European; text selectors, including numeric text, return #VALUE! as an evaluator profile. The year-segmented basis-1 calculation, negative YEARFRAC direction, fractional DAYS360 date handling, and serial-60 behavior are explicit evaluator profiles; direct Excel spot-checks have not been run.

### Coupon schedules

`COUPDAYBS`, `COUPDAYS`, `COUPDAYSNC`, `COUPNCD`, `COUPNUM`, and `COUPPCD` share a maturity-anchored schedule in this evaluator and calculate their outputs separately. Microsoft documents the argument truncation, frequency and basis ranges, output meanings, and principal errors; exact coupon-date generation, equality behavior, basis details, serial 60, and leap-year COUPDAYS behavior remain evaluator profiles until checked against Excel.

### Time value of money and payment components

FV, PV, and PMT use per-period rates and payment counts, documented cash-flow signs, and beginning/end payment timing. Their shared factors use an exact zero-rate branch and stable logarithmic/exponential calculations. NPER solves the inverse equation and preserves negative and fractional period counts; zero-rate limits, logarithm-domain errors, non-unique cases, coercion, and finite-range boundaries are explicitly marked evaluator profiles. Its normalized operands and log1p/log-difference strategy are independently implemented in Python and Rust. These numerical choices and exact internal precision still need direct Excel checks. IPMT and PPMT divide each payment into interest and principal, with `PPMT = PMT - IPMT`; timing, period bounds, coercion, and numerical edge cases are recorded as evaluator profiles where Microsoft leaves behavior unspecified. `CUMIPMT` and `CUMPRINC` aggregate payment components over inclusive intervals with stable O(1) geometric sums. Their exact identity is `CUMPRINC = period_count × PMT - CUMIPMT`, while the evaluator computes principal directly to avoid cancellation. Beginning-of-period interest skips period 1. Fractional endpoints select enclosed whole periods, empty fractional ranges return zero, and `end_period > nper` is rejected as an evaluator profile. Numeric coercion, exact binary64 period limits, and error precedence remain profiles. Apache ExcelFinancialFunctions informed the component-sum pattern; no source code was copied. None of these financial functions has been spot-checked in Excel.

### Depreciation

`SLN` spreads depreciable cost evenly across life periods; `SYD` applies decreasing sum-of-years-digits weights; `DB` uses Microsoft’s three-decimal declining rate with partial first/final periods; and `DDB` applies a factor-based declining rate capped at the salvage basis. DB/DDB input bounds, truncation, coercion, and errors remain evaluator profiles where Microsoft is silent or contradictory. VDB adds fractional-interval depreciation and optional switching to straight-line depreciation when its amount exceeds declining-balance depreciation; its default factor is 2. The evaluator uses a bounded geometric kernel and a logarithmic-time switch search. Fractional interval allocation and undocumented limits remain evaluator profiles; shared logical coercion makes `no_switch` true for any nonzero number and rejects nonempty text. An Apache-2.0 compatibility reference reports an Excel discrepancy for split intervals. Apache-2.0 ExcelFinancialFunctions and dual MIT/Apache Formualizer inform kernel organization and compatibility probes only; no source code is copied. AMORLINC and deprecated AMORDEGRC add date-prorated French depreciation: AMORLINC retains fractional currency values, while AMORDEGRC selects a life coefficient and rounds each step half up to whole units. Basis 1 uses a 366-day denominator only when the inclusive interval contains February 29, otherwise 365; serial 60 remains an explicit 1900-system evaluator profile. The Apache reference profile uses a full-rate first amount when the raw stub is exactly zero, extends AMORDEGRC schedule life only for a positive raw stub, and stops later amounts after book value falls below salvage. These detailed profiles have not been checked in Excel. Microsoft marks AMORDEGRC deprecated and retained for compatibility with old workbooks.

## Workbook adapter

Two public workbook surfaces serve different needs:

| Surface | Purpose and limits |
| --- | --- |
| Python `WorkbookModel` and `workbook_forge.xlsx`; Rust `toolkit` and `xlsx` | Independent workbook authoring, full/incremental calculation, revisioned sessions, import, generation and preservation-aware export |
| Python `workbook_forge.workbook.Workbook` | Lower-level package inspection, supported cell patches, and targeted scalar calculation with `calculate_cells_to` |

The [programmable workbook guide](../README.md#programmable-workbooks) shows the
first surface. The lower-level Python `Workbook` is also the package foundation
used by its toolkit adapter; it is not a second Python calculation engine.
A toolkit session can invalidate affected dependencies and reuse unrelated results.
The lower-level `calculate_cells_to` operation computes a requested closure for
one output file and has no incremental session of its own.

### Package handling and preservation

Both languages support conservative reading and patching of macro-free transitional
`.xlsx` packages. They validate relationships and content types, preserve untouched
package-part payloads, and write to a new output path. This preserves the bytes of
untouched uncompressed parts; it does not promise an identical ZIP archive.
Imported models retain an immutable source baseline. Directly changing the original
Python `Workbook` object does not update an already imported model; reimport after
such changes to establish a new baseline.

Cell text in shared strings, inline strings and string formula caches is read
with the OOXML `_xHHHH_` escapes decoded (ECMA-376 part 1, ST_Xstring): `_x0009_`
is a tab, `_x000D_` a carriage return, and `_x005F_x0041_` the literal text
`_x0041_`. Decoding is one pass from the left, so the six characters after a
decoded `_x005F_` are never rescanned. An escape that names a lone surrogate is
left as written. Both writers use the same escapes for the characters XML 1.0
cannot hold, for the carriage return, and for any literal `_xHHHH_`, so text
survives a round trip. Excel's 32,767-character limit is measured on the text,
not on its escaped spelling.

Package limits include 130 MiB compressed input, 128 MiB expanded contents,
32 MiB XML parts and central directory, and 10,000 entries. XML parsing is bounded
to 1,000,000 elements and depth 128 per part. The Python reader scans directory
records before constructing ZIP entry objects and reads members in requests of
at most 64 KiB. Its ZIP64 footer handling uses a guarded CPython `zipfile` helper;
runtimes without that helper refuse the operation. Rust retains its own bounded
ZIP/XML implementation and UTF-8 XML profile. These bounds do not constitute full
OOXML schema validation; see the [extraction contract](extraction-patterns.md).

### Calculation and edits

Authored values, formulas, imported caches and calculated results remain distinct.
Caches are not calculation inputs. Exported results must match the model revision
and supported meaning; a stale or unsupported result refuses export. Supported
edits invalidate stale calculation-chain metadata and request Excel recalculation
on next open. A scalar formula reference to a blank cell is cached as numeric zero;
an explicit empty string remains a string cache, matching
[Microsoft's documented reference behavior](https://support.microsoft.com/en-us/excel/clear-cells-of-contents-or-formats).
The canonical model follows the same rule: a scalar formula whose value is a
blank reference (`=B1`, `=IF(TRUE,B1)` with B1 empty) has `Formula.result` 0 in
both engines, never `null`, so a null result always means "not calculated".
Text context is the evaluator's own coercion: `=B1&"x"` is `"x"`. The referenced
cell itself stays an authored blank.

Python `calculate_cells_to` accepts only functions marked `conformance-tested`
for Python and evaluates the requested scalar formula cells and their transitive
formula dependencies. Its preparation limits include 100,000 formula characters,
100,000 cells per reference, 250,000 total range-expansion cells counting repeated
and overlapping ranges, 100,000 formula-to-cell dependency edges, and 250,000
grouped/table range checks. The evaluator's stricter formula-length limit still
applies. Dependency discovery includes references in lazy branches, so static
cycles or unsupported content in the closure can prevent calculation even when
a branch would not be selected at runtime.

Both toolkit sessions cap populated workbook cells and dependency edges at
100,000. Atomic engine edit batches contain at most 10,000 edits; the
[agent contract](agent-protocol.md) imposes a smaller limit of 100. Imported agent
edits and Rust preservation-aware edits require original existing cells. Supported
fresh-generation styles do not imply unrestricted imported-style editing.

Macros and external data are never executed or fetched by either SDK. Excel may
recalculate formulas and update workbook-defined external links when a person opens
the output, depending on Excel settings. Macro-enabled `.xlsm`, legacy `.xls`,
chart sheets, strict OOXML, structured table references, grouped/table formula
calculation, worksheet spill placement and 1904-date formula calculation remain
outside the supported workbook profile. Unsupported formula text can be retained
for inspection and preservation; calculation depending on it is refused.

Array results such as FILTER, SORT and UNIQUE are supported by the shape-preserving
formula API. That does not imply that the workbook adapters can place or edit
worksheet spill cells. Input bindings remain explicit application metadata and
must be supplied when importing XLSX; Excel defined names do not establish those
bindings automatically.
