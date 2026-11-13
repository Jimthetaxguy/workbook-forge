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
itself establish Excel compatibility. Each function's `evidence_grade` (`profile`,
`dual_engine`, `expected_value`, `excel_observed`) and `profile_class` (`semantic`,
`cosmetic`, `unclassified`) are recorded in the catalog; see
[evidence grades](evidence-grades.md). The [native primitive guide](primitives.md)
describes the smaller direct-call and named-input interface over these evaluators.

## General

Unsupported syntax returns typed errors instead of approximations. Shared fixtures
verify Python and Rust agreement and their stated expected results. No Excel
receipt for a function family is committed. The catalog grade is `dual_engine`.
Broader function-family checks and the complete workbook round trip remain
outstanding. See [Excel observations](excel-observations.md). The engines are
not full Excel calculation engines.

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

### Selected references in SUM and AVERAGE

A one-cell reference chosen by `IF`, `IFERROR`, `IFNA`, `IFS`, `SWITCH`, `CHOOSE` or `XLOOKUP`, and the cell `INDEX` returns from a range, is still a reference when `SUM` or `AVERAGE` receives it. `INDEX` counts only when its first argument is a range: `=SUM(INDEX(A1:A2,1),2)` with `A1` holding TRUE is 2. With `A1` holding TRUE, `=SUM(A1)`, `=SUM(IF(TRUE,A1))` and `=SUM(CHOOSE(1,A1))` are all 0, and `=AVERAGE(IF(TRUE,A1),10)` is 10. The same holds when `A1` holds the text `5` or other text. A selected empty cell is also still a reference, so `=AVERAGE(IF(TRUE,A1),10)` with `A1` empty is 10, not 5; a selected cell holding zero is counted, and an error value in the selected cell is returned. A value typed directly and selected the same way stays a typed value: `=SUM(IF(TRUE,TRUE))` is 1, `=SUM(IF(TRUE,"5"))` is 5 and `=AVERAGE(IF(TRUE,TRUE),10)` is 5.5.

Microsoft states the two halves of this rule but not the selected case. Its page [Use the SUM function to sum numbers in a range](https://support.microsoft.com/en-us/excel/use-the-sum-function-to-sum-numbers-in-a-range) gives `=SUM("5", 15, TRUE)` as 21 ("The text value "5" is first translated into a number, and the logical value TRUE is first translated into the number 1.") and `=SUM(A5,A6, 2)` as 2, because "non-numeric values in references are not translated" and "the values in those cells are ignored". The [AVERAGE page](https://support.microsoft.com/en-us/excel/functions/average-function) says "If a range or cell reference argument contains text, logical values, or empty cells, those values are ignored; however, cells with the value zero are included." The [CHOOSE page](https://support.microsoft.com/en-us/excel/functions/choose-function) says of `=SUM(CHOOSE(2,A1:A10,B1:B10,C1:C10))`: "The CHOOSE function is evaluated first, returning the reference B1:B10." No Microsoft sentence says what `SUM` or `AVERAGE` does with a one-cell reference selected by `IF` or the other functions. Treating it as a reference is a Workbook Forge profile. It is the rule this evaluator already applied to `COUNT`, `AND`, `OR`, `XOR` and the range statistics, and it has not been checked in Excel.

The marker that carries "this value came from a cell" now also travels with a selected empty cell. That moves more than `AVERAGE`. Every result below was run on `main` and on this change, with `A1` empty, and each now equals what the same cell named directly gives. The list holds the cases that were found, and it is not claimed to be complete.

- Both engines: `=XOR(IF(TRUE,A1))` is `#VALUE!` instead of FALSE; `=RANK(1,IF(TRUE,A1))` and `=RANK.EQ(1,IF(TRUE,A1))` are `#N/A` instead of `#VALUE!`; `=HLOOKUP(1,IF(TRUE,A1),1)` is `#N/A` and `=HLOOKUP(1,IF(TRUE,A1),2)` is `#REF!`, both instead of `#VALUE!`.
- Rust only, where Python already gave the new result: `=AND(IF(TRUE,A1),TRUE)` is TRUE instead of FALSE; `=AND(IF(TRUE,A1))` and `=OR(IF(TRUE,A1))` are `#VALUE!` instead of FALSE; `=COUNTIF(IF(TRUE,A1),1)` and `=COUNTIFS(IF(TRUE,A1),1)` are 0 and `=COUNTIF(IF(TRUE,A1),"")` is 1; `=SUMIF(IF(TRUE,A1),1)` is 0, and so is `=SUMIF(B1:B1,1,IF(TRUE,A1))` with `B1` holding 1, where the selected cell is the sum range; `=AVERAGEIF(IF(TRUE,A1),1)` is `#DIV/0!`; `=SUMIFS(IF(TRUE,A1),IF(TRUE,A1),"")`, `=MAXIFS(IF(TRUE,A1),IF(TRUE,A1),"")` and `=MINIFS(IF(TRUE,A1),IF(TRUE,A1),"")` are 0 and `=AVERAGEIFS(IF(TRUE,A1),IF(TRUE,A1),"")` is `#DIV/0!`. Each of these was `#VALUE!` in Rust before, so an older difference between the engines is gone.

Boundaries that this rule does not change, and limits of the rule itself:

- `IFERROR` returns empty text, a typed value, when the cell it selects is empty. `=AVERAGE(IFERROR(A1,0),10)` with `A1` empty is therefore `#VALUE!`, and `=SUM(IFERROR(A1,0),10)` is 10.
- `IFNA` does not do the same in either engine. Microsoft's [IFNA page](https://support.microsoft.com/en-us/excel/functions/ifna-function) says "If value or value_if_na is an empty cell, IFNA treats it as an empty string value ("")." Both engines instead keep the empty cell as an empty reference: `=ISBLANK(IFNA(A1,1))` is TRUE while `=ISBLANK(IFERROR(A1,1))` is FALSE. That is older than this rule and was not changed. One result that depends on it did move: `=AVERAGE(IFNA(A1,0),10)` with `A1` empty was 5 and is now 10 in both engines. Neither value follows from Microsoft's sentence, under which the result would be `#VALUE!` as for `IFERROR`, so no fixture pins it.
- Nothing here was observed in Excel. The `IFERROR` and `IFNA` cases are the first to check there: it is a judgement that has not been verified, not a fact, that these two functions may hand back a value and not a reference in Excel, in which case `=SUM(IFERROR(A1,0))` with `A1` holding TRUE would be 1 there and not 0.
- `INDEX` in the Rust engine also accepts a typed value or one cell as its first argument, where the Python engine returns `#VALUE!` for both. Directly inside `SUM` or `AVERAGE` such a result is read as it was before this rule: `=SUM(INDEX(TRUE,1))` is 1, `=SUM(INDEX("5",1))` is 5 and `=AVERAGE(INDEX(TRUE,1),10)` is 5.5 in Rust, the same as the typed values without `INDEX`. No Python change was needed, because the Python `INDEX` only ever returns a cell of a range or an error.
- The same Rust `INDEX` result placed behind `IF`, `IFERROR`, `IFNA`, `IFS`, `SWITCH`, `CHOOSE` or `XLOOKUP` is not covered by the item above. The selecting function marks every `INDEX` result as a reference, and `SUM` and `AVERAGE` now honor that mark, so `=SUM(IF(TRUE,INDEX(TRUE,1)))` is 0 in Rust (it was 1) and `=SUM(IF(TRUE,INDEX(A1,1)),2)` with `A1` holding the text `bad` is 2 in Rust (it was `#VALUE!`). Python returns `#VALUE!` for both, before and after. Telling the two kinds of `INDEX` result apart behind a selecting function needs a second mark, or a change to what `COUNT`, `AND` and `OR` read, and neither was made. The mark reaches further than `SUM` and `AVERAGE`. With `A1` empty, the Rust `INDEX(A1,1)` behind a selecting function is now an empty reference to the functions listed above for a selected empty cell: in Rust `=COUNTIF(IF(TRUE,INDEX(A1,1)),"")` is 1, `=SUMIF(IF(TRUE,INDEX(A1,1)),"<>zz")` and `=SUMIFS(IF(TRUE,INDEX(A1,1)),IF(TRUE,INDEX(A1,1)),"<>zz")` are 0, `=AVERAGEIF(IF(TRUE,INDEX(A1,1)),"<>zz")` is `#DIV/0!`, and `=RANK(4,IF(TRUE,INDEX(A1,1)))`, `=RANK.EQ(4,IF(TRUE,INDEX(A1,1)))` and `=HLOOKUP(4,IF(TRUE,INDEX(A1,1)),1)` are `#N/A`. Python returns `#VALUE!` for each, and a second review found Rust returned `#VALUE!` for each before this rule. That review ran 440,660 formulas built around `INDEX` and counted 5,259 new differences between the engines of this kind (`SUM` 2,263, `AVERAGE` 2,068, `COUNTIF` 232, and 116 each for `SUMIF`, `SUMIFS`, `AVERAGEIF`, `RANK`, `RANK.EQ` and `HLOOKUP`), every one with `INDEX` over a typed value, one cell or a selected cell. Most lost an agreement on `#VALUE!`. A few lost an agreement on a number: `=AVERAGE(IFERROR(INDEX(A1,1),0),10)` with `A1` holding FALSE was 5 in both engines and is now 10 in Rust and 5 in Python.
- `XLOOKUP` over one-cell arrays is a second, smaller case of the same kind. The Python engine refuses one cell as a lookup array and the Rust engine accepts it, which is older than this rule. With `C1` holding 1 and `A1` holding the text `bad`, `=SUM(XLOOKUP(1,C1,A1))` and `=AVERAGE(XLOOKUP(1,C1,A1),10)` were `#VALUE!` in both engines; Rust now reads the cell `XLOOKUP` returns as a reference and gives 0 and 10, and Python still returns `#VALUE!`.
- `COUNT`, `AND`, `OR`, `XOR` and the range statistics still read every `INDEX` result as a reference in Rust, as they did before this rule, including `INDEX` over a typed value: `=COUNT(INDEX(TRUE,1))` is 0 while `=COUNT(TRUE)` is 1, and `=AND(INDEX("x",1),TRUE)` is TRUE while `=AND("x",TRUE)` is `#VALUE!`. Python gives 0 and `#VALUE!` for the two `INDEX` forms: its `INDEX` returns `#VALUE!` there, which `COUNT` does not count and `AND` passes on.
- `MIN` and `MAX` ignore every value that is not a number, typed or referenced, so `=MIN(TRUE,5)` and `=MAX("7",5)` are 5. Microsoft's [MIN page](https://support.microsoft.com/en-us/office/min-function-61635d12-920f-4ce2-a70f-96f202dcc152) says "Logical values and text representations of numbers that you type directly into the list of arguments are counted." That older gap is not a selected-reference question and is left as it was.
- A one-cell reference returned by `INDEX` is not accepted as an `HLOOKUP` table (`=HLOOKUP(1,INDEX(A1:A2,1),1)` is `#VALUE!` in both engines), and the Rust engine does not accept it as a criteria range (`=COUNTIF(INDEX(A1:A2,1),1)` is `#VALUE!` in Rust and 1 in Python). Those arguments ask for a range, which is a different rule from the typed-or-referenced one above.
- The source compiler runtimes (profiles 3 to 6) read a selected cell as a value, so compiled `SUM(IF(TRUE,A1))` still counts a logical value in `A1`. Those runtimes are versioned and were not changed.

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

`MONTH`, `DAY`, and `YEAR` read the integer date portion of numeric serials; `DAYS` subtracts numeric serials, preserving time fractions; `EDATE` clamps to the target month; and `EOMONTH` returns the target month's last day. Serial 0 is an engine profile, not an Excel observation. Both engines return `MONTH(0)` as 1, `DAY(0)` as 0, and `YEAR(0)` as 1900 before the EDATE and EOMONTH conversion. `HOUR`, `MINUTE`, and `SECOND` extract whole-second components from date/time serials; the 1900 serial ceiling and negative/non-finite error behavior are unverified evaluator boundaries. `TIME` normalizes components and returns a fraction of a day. The extractor's half-ULP precision correction and `TIME` fractional-component truncation are evaluator profile rules that need Excel spot-checks. `WEEKDAY` supports return types 1, 2, 3, and 11–17; `WEEKNUM` supports System 1 selectors 1, 2, and 11–17 plus ISO selector 21; and `ISOWEEKNUM` uses ISO week-year rules. These functions floor time fractions. Fractional selector truncation and exact results around the fictional serial 60 remain unverified evaluator-profile choices. Human-readable time strings such as `6:45 PM` are documented by Microsoft for the extractors but are not parsed here; numeric text follows the shared number coercion. `EDATE` and `EOMONTH` truncate fractional month offsets toward zero. Their date semantics use the 1900 system, preserve serial 60, and do not model workbook-specific 1904 settings or locale-sensitive text dates. `EDATE` and `EOMONTH` return whole-day serials and discard start-date time fractions as profile behavior that still needs an Excel spot-check. Both refuse a result before 1899-12-31 (serial 0) with `#NUM!`, which Microsoft documents for a result outside the supported range; `EOMONTH(1,-1)` is the lowest result, 0, and `EDATE(1,-1)` is `#NUM!` in both engines. DATEDIF, DATEVALUE and TIMEVALUE are specified in [date and text functions](date-text-functions.md), and NOW, TODAY, RAND, RANDBETWEEN and RANDARRAY in [volatile functions](volatile-functions.md).

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

### Scalar math and logical constants

`SQRT`, `POWER`, `EXP`, `LN`, `LOG`, `LOG10`, `PI`, `SIGN`, `CEILING`, `FLOOR`, `CEILING.MATH`, `FLOOR.MATH`, `MROUND`, `TRUE`, `FALSE` and `XOR` follow Microsoft's pages for their definitions, defaults and documented errors ([SQRT](https://support.microsoft.com/en-us/excel/functions/sqrt-function), [POWER](https://support.microsoft.com/en-us/excel/functions/power-function), [EXP](https://support.microsoft.com/en-us/excel/functions/exp-function), [LN](https://support.microsoft.com/en-us/excel/functions/ln-function), [LOG](https://support.microsoft.com/en-us/excel/functions/log-function), [LOG10](https://support.microsoft.com/en-us/excel/functions/log10-function), [PI](https://support.microsoft.com/en-us/excel/functions/pi-function), [SIGN](https://support.microsoft.com/en-us/excel/functions/sign-function), [CEILING](https://support.microsoft.com/en-us/excel/functions/ceiling-function), [FLOOR](https://support.microsoft.com/en-us/excel/functions/floor-function), [CEILING.MATH](https://support.microsoft.com/en-us/excel/functions/ceiling-math-function), [FLOOR.MATH](https://support.microsoft.com/en-us/excel/functions/floor-math-function), [MROUND](https://support.microsoft.com/en-us/excel/functions/mround-function), [TRUE](https://support.microsoft.com/en-us/excel/functions/true-function), [FALSE](https://support.microsoft.com/en-us/excel/functions/false-function), [XOR](https://support.microsoft.com/en-us/excel/functions/xor-function)). The rest is a Workbook Forge profile that has not been checked in Excel.

Each scalar argument is one value. The checks run in a fixed order: a wrong argument count returns `#VALUE!`; then a range or array argument returns `#VALUE!`; then the first error value in argument order is returned; then the arguments are converted left to right with the shared numeric conversion (blank is 0, logical values are 1 and 0, ASCII numeric text is its number, other text is `#VALUE!`); then the function's own domain is checked. An explicitly empty argument returns `#VALUE!`. A zero result carries no sign. A result that is not a finite binary64 number returns `#NUM!`.

- `SQRT` of a negative number, `LN` and `LOG10` of a number at or below zero, and `LOG` with a number or base at or below zero return `#NUM!`. `LOG` with base 1 returns `#DIV/0!`. `EXP` and `POWER` return `#NUM!` when the result overflows or is not real. `POWER(0,0)` is 1 and `POWER(0,-1)` is `#NUM!`, the results the `^` operator already gave in this evaluator. Microsoft's POWER page does not cover these cases; Excel's results for them have not been checked and may differ. Third-party reports say that Excel returns `#NUM!` for `POWER(0,0)` and `#DIV/0!` for `POWER(0,-1)` ([a question in Microsoft's community forum](https://learn.microsoft.com/en-us/answers/questions/4850441/excel-formula-bug-00-gives-num), [Trump Excel](https://trumpexcel.com/excel-functions/power/), [Better Solutions](https://bettersolutions.com/excel/functions/power-function.htm)). This project has not observed Excel, so neither the reports nor the profile is verified, and the same doubt applies to `^`. `POWER` is kept equal to `^`.
- `PI()`, `TRUE()` and `FALSE()` take no arguments; any argument returns `#VALUE!`.
- `XOR` takes 1 to 254 arguments. Inside a reference, text and empty cells are ignored, and a range with no logical values returns `#VALUE!`, as documented. Numbers are read as logical values, nonzero as TRUE. Text typed directly returns `#VALUE!`, except empty text, which is FALSE.
- `FLOOR` returns `#NUM!` for a positive number with a negative significance, as Microsoft documents; `CEILING` applies the same rule, which its page does not state. A significance of 0 returns 0 in `CEILING` and `#DIV/0!` in `FLOOR`; Microsoft documents neither. `CEILING.MATH` and `FLOOR.MATH` use the absolute value of significance, return 0 for significance 0, and reverse the direction for negative numbers when mode is not zero; a mode of 0 is the same as no mode.
- Microsoft's `FLOOR.MATH` page contradicts itself about mode. Its remark says "By using 0 or a negative number as the Mode argument, you can change the direction of the rounding for negative numbers." Its example describes `FLOOR.MATH(-5.5,2,-1)` as "using a non-zero Mode, which reverses rounding direction". The evaluator follows the example, so `FLOOR.MATH(-5.5,2,0)` is -6 and `FLOOR.MATH(-5.5,2,1)` is -4. Neither reading has been checked in Excel.
- Microsoft's `CEILING.MATH` page says number "must be between -2.229E-308.and 9.99E+307." That range excludes the page's own examples -8.1 and -5.5 and is not applied; any finite number is rounded.
- The multiple functions divide and multiply in binary64. A quotient within two binary64 values of a whole number is taken as that whole number, so `FLOOR(0.3,0.1)` is 0.3 although the stored quotient is 2.9999999999999996. A product within two binary64 values of its 15-digit decimal form is returned as that decimal. `MROUND` rounds the plain binary64 quotient half away from zero without that allowance, which reproduces Microsoft's own remark that `MROUND(6.05,0.1)` is 6.0 while `MROUND(7.05,0.1)` is 7.1. `MROUND` with a number and multiple of opposite signs returns `#NUM!`, as documented; a zero multiple returns 0.

### Range statistics and aggregates

`PRODUCT`, `SUMSQ`, `SUMPRODUCT`, `MEDIAN`, `LARGE`, `SMALL`, `RANK.EQ`, `RANK`, `STDEV.S`, `STDEV.P`, `VAR.S`, `VAR.P`, `AVERAGEA` and `MODE.SNGL` follow Microsoft's pages ([PRODUCT](https://support.microsoft.com/en-us/excel/functions/product-function), [SUMSQ](https://support.microsoft.com/en-us/excel/functions/sumsq-function), [SUMPRODUCT](https://support.microsoft.com/en-us/excel/functions/sumproduct-function), [MEDIAN](https://support.microsoft.com/en-us/excel/functions/median-function), [LARGE](https://support.microsoft.com/en-us/excel/functions/large-function), [SMALL](https://support.microsoft.com/en-us/excel/functions/small-function), [RANK.EQ](https://support.microsoft.com/en-us/excel/functions/rank-eq-function), [RANK](https://support.microsoft.com/en-us/excel/functions/rank-function), [STDEV.S](https://support.microsoft.com/en-us/excel/functions/stdev-s-function), [STDEV.P](https://support.microsoft.com/en-us/excel/functions/stdev-p-function), [VAR.S](https://support.microsoft.com/en-us/excel/functions/var-s-function), [VAR.P](https://support.microsoft.com/en-us/excel/functions/var-p-function), [AVERAGEA](https://support.microsoft.com/en-us/excel/functions/averagea-function), [MODE.SNGL](https://support.microsoft.com/en-us/excel/functions/mode-sngl-function)). `RANK` is catalogued as a separate compatibility function with the same rules as `RANK.EQ`.

Arguments are read left to right and the first problem met is returned. A value typed directly counts when it is a number, a logical value (1 or 0) or text holding a plain decimal number; other text returns `#VALUE!`, and so does an explicitly empty argument. Inside a reference or array only numbers count; text, logical values and empty cells are ignored. An error value anywhere, including inside a reference, is returned. Microsoft's function pages disagree on this point: several, including `SUMSQ`, `STDEV.S` and `VAR.S`, say that error values in a reference are ignored. Returning them is a Workbook Forge profile chosen so that a broken input never yields a number, and it has not been checked in Excel.

- `PRODUCT` with no number returns 0. `MEDIAN` with no number returns `#NUM!`. `STDEV.S` and `VAR.S` need two numbers and `STDEV.P` and `VAR.P` one; fewer returns `#DIV/0!`. Microsoft documents none of these three cases. Means and sums are plain left-to-right binary64 sums; an overflow returns `#NUM!`.
- `MODE.SNGL` returns the value that appears most often and, among equally frequent values, the one that appears first; when nothing repeats it returns `#N/A`, as documented.
- `LARGE` and `SMALL` return `#NUM!` for an empty array and for k below 1 or above the count, as documented. A k that is not a whole number also returns `#NUM!`: Microsoft does not say how a fractional k is rounded, so it is refused rather than guessed.
- `RANK.EQ` compares with exact binary64 equality and returns `#N/A` when the number is not in the list. A nonzero order ranks ascending, as documented. A value typed directly as ref returns `#VALUE!`, but an error value given as ref, such as `NA()`, is returned; Microsoft's pages are silent on that. Microsoft's `RANK` page describes ref as "A reference to a list of numbers."; `RANK` also accepts a computed array, as the `RANK.EQ` page allows.
- `AVERAGEA` counts text in a reference as 0 and logical values as 1 and 0 and skips empty cells; with nothing to count it returns `#DIV/0!`. Microsoft's page contradicts itself on its own example; the evaluator follows the example, and the semantic spec records the conflict.
- `SUMPRODUCT` requires arrays of the same dimensions and returns `#VALUE!` otherwise, as documented; that check comes before any error value. Entries that are not numbers count as zero, as documented. A single value is a one-by-one array. The data of Microsoft's example are published as images, so they are not used as fixture values.

### Text, selection and dimensions

`CONCATENATE`, `EXACT`, `PROPER`, `REPT`, `REPLACE`, `VALUE`, `CHAR`, `CODE`, `CHOOSE`, `HLOOKUP`, `ROWS` and `COLUMNS` follow Microsoft's pages ([CONCATENATE](https://support.microsoft.com/en-us/excel/functions/concatenate-function), [EXACT](https://support.microsoft.com/en-us/excel/functions/exact-function), [PROPER](https://support.microsoft.com/en-us/excel/functions/proper-function), [REPT](https://support.microsoft.com/en-us/excel/functions/rept-function), [REPLACE](https://support.microsoft.com/en-us/excel/functions/replace-function), [VALUE](https://support.microsoft.com/en-us/excel/functions/value-function), [CHAR](https://support.microsoft.com/en-us/excel/functions/char-function), [CODE](https://support.microsoft.com/en-us/excel/functions/code-function), [CHOOSE](https://support.microsoft.com/en-us/excel/functions/choose-function), [HLOOKUP](https://support.microsoft.com/en-us/excel/functions/hlookup-function), [ROWS](https://support.microsoft.com/en-us/excel/functions/rows-function), [COLUMNS](https://support.microsoft.com/en-us/excel/functions/columns-function)). The scalar text functions use the same order of checks as the scalar math functions and the shared number-to-text conversion. A result longer than 32,767 UTF-16 units returns `#VALUE!`. Character positions count Unicode scalar values, as `LEN` and `MID` do in this evaluator.

- `CONCATENATE` takes 1 to 255 items, as documented. The documented 8,192-character total ("up to a total of 8,192 characters") is not applied; the 32,767-unit result limit is. One example on Microsoft's page prints a result that does not match its formula, and the spec records it.
- `EXACT` compares code points after converting both values to text, so `EXACT(1,"1")` is TRUE.
- `PROPER` capitalises the first letter of the text and any letter that follows a character that is not a letter, and lowercases the other letters, as documented. A letter here is a character with the Unicode Lowercase or Uppercase property, mapped one character at a time; Excel's own letter classes are not known.
- `REPT` truncates the count, as documented; a negative count returns `#VALUE!`. `REPLACE` truncates fractional positions and returns `#VALUE!` for a start below 1 or a negative count.
- `VALUE` reads one fixed number grammar: optional surrounding spaces, a sign, a `$`, comma thousands groups, a decimal point, an exponent and a trailing `%`. Text that is not such a number is tried as the unambiguous date and time forms `DATEVALUE` and `TIMEVALUE` accept. Anything else, including a date whose day and month order depends on regional settings, is refused with `#VALUE!` rather than guessed. A logical value returns `#VALUE!` and an empty cell returns 0.
- `CHAR` returns characters 1 through 127 only and `CODE` reads an ASCII first character only. For 128 through 255 and for other characters Excel returns a value from the computer's character set (Microsoft names ANSI for Windows and the Macintosh character set); the evaluator has no character set to consult, so it refuses those with `#VALUE!`.
- `CHOOSE` takes 2 to 255 arguments, truncates the index, returns `#VALUE!` for an index below 1 or above the number of values, as documented, and evaluates only the selected value. A selected reference stays a reference, so `SUM(CHOOSE(2,A1:A3,B1:B3))` sums the second range. One selected cell is a reference too: with `A1` holding TRUE, `SUM(CHOOSE(1,A1))` is 0 and `AVERAGE(CHOOSE(1,A1),10)` is 10; see [Selected references in SUM and AVERAGE](#selected-references-in-sum-and-average). An array index is not supported. The formula parser does not accept a function as a range endpoint, so Microsoft's Example 2, `=SUM(A2:CHOOSE(2,A3,A4,A5))` with the result 80, is refused and has no fixture.
- `HLOOKUP` searches the first row. An exact search (range_lookup FALSE) returns the leftmost match, with `?`, `*` and `~` wildcards for text, as documented. An approximate search returns the largest value that is not above the lookup value among values of the same type, and the rightmost of equal values, whatever the order of the row; Microsoft says an unsorted row may give a wrong value, so results on unsorted rows are not asserted as Excel results. A row index below 1 returns `#VALUE!` and one beyond the table returns `#REF!`, as documented.
- `ROWS` and `COLUMNS` measure a reference from its addresses without reading its cells, and a computed array from its shape. A single value counts as 1. A reference of more than 100,000 cells returns `#NUM!`, the evaluator's range limit; whole-row and whole-column references are not supported. The formula parser has no array constants, so Microsoft's `{...}` examples for these functions and for `HLOOKUP` are not covered.

### Cash-flow finance

`NPV`, `IRR`, `XNPV`, `XIRR`, `RATE` and `MIRR` follow Microsoft's pages ([NPV](https://support.microsoft.com/en-us/excel/functions/npv-function), [IRR](https://support.microsoft.com/en-us/excel/functions/irr-function), [XNPV](https://support.microsoft.com/en-us/excel/functions/xnpv-function), [XIRR](https://support.microsoft.com/en-us/excel/functions/xirr-function), [RATE](https://support.microsoft.com/en-us/excel/functions/rate-function), [MIRR](https://support.microsoft.com/en-us/excel/functions/mirr-function)). No result of these functions has been checked in Excel.

`NPV`, `IRR` and `MIRR` read their cash flows by the aggregate rule above: inside a reference only numbers count, and text, logical values and empty cells are skipped and take no period, as documented. `XNPV` and `XIRR` pair values with dates, so nothing can be skipped: every value and every date must be a number, and text, a logical value or an empty cell returns `#VALUE!`. Dates are truncated to whole days; a date outside serial numbers 0 through 2,958,465 returns `#VALUE!`; a different number of values and dates, or a date before the first date, returns `#NUM!`, as documented. An error value anywhere in the arguments is returned. Microsoft's `NPV` page says error values in a reference are ignored; returning them is a Workbook Forge profile.

- `NPV` at a rate of -100 percent returns `#DIV/0!`; below -100 percent the equation is evaluated as written; with no numbers the result is 0. Microsoft documents none of these. For values typed directly, Microsoft's page says "Arguments that are empty cells, logical values, or text representations of numbers, error values, or text that cannot be translated into numbers are ignored." The evaluator departs from that sentence as written: `NPV(1,TRUE,"4")` counts both values and is 1.5, `NPV(1,"abc")` is `#VALUE!`, and an error value typed directly is returned, as in the other aggregates.
- `XNPV` returns `#NUM!` for a rate at or below -100 percent and evaluates a negative rate above it. Microsoft says the values must hold a positive and a negative value but names no error for `XNPV`, so the evaluator does not enforce it there. Excel's handling of negative rates has not been checked. Microsoft says of dates "All other dates must be later than this date, but they may occur in any order." and names an error only for a date before the first; the evaluator accepts a later payment dated on the first date and does not discount it.
- `IRR` and `XIRR` return `#NUM!` without both a positive and a negative value (documented for `XIRR`; for `IRR` Microsoft states the requirement without naming the error, so `#NUM!` there is a profile). `MIRR` returns `#DIV/0!` in that case, as documented, and `#NUM!` for a rate at or below -100 percent.
- `RATE` needs a positive number of periods and a type of 0 or 1; otherwise it returns `#NUM!`. An empty fv or type is 0, as in `PMT`. An empty pmt is 0 only when fv is given.

Microsoft documents the accuracy and the number of tries of the three iterative functions, and no technique: `IRR` is accurate within 0.00001 percent and gives up after 20 tries, `XIRR` within 0.000001 percent after 100 tries, and `RATE` converges to within 0.0000001 in 20 iterations; each returns `#NUM!` when it gives up, and the guess defaults to 10 percent. The technique is a Workbook Forge profile. Both engines use Newton's method on the rate with the analytic slope, starting from the guess. A try is one Newton step. A step that would put the rate at or below -100 percent moves it halfway to -100 percent instead and still counts as a try. The result is the rate after a step that moved it by no more than the documented accuracy (0.0000001 for `IRR` and `RATE`, 0.00000001 for `XIRR`). A guess at or below -100 percent, a zero slope and a value that is not finite return `#NUM!`. An explicitly empty guess returns `#VALUE!`. `RATE` also returns `#NUM!` when the search settles within 0.0000001 of -100 percent, because the annuity equation can hold at -100 percent itself and that is not a rate.

What this means in practice: where a cash-flow series has one rate and Excel finds it, the results should agree to the documented accuracy, not to the last digit. Where there are several rates, or the search wanders, Excel may return a different rate, or a rate where Workbook Forge returns `#NUM!`, or the reverse. Fixtures therefore round iterative results to the documented accuracy, and their expected values come from Microsoft's examples, closed forms, or roots computed separately in 60-digit decimal arithmetic. Microsoft's `XIRR` example prints 0.373362535; its equation solves to 0.3733625335, inside the documented accuracy, and the evaluator returns the latter.

Python and Rust implement the same steps in the same order. `IRR`, `NPV` and the cost side of `MIRR` use only addition, multiplication and division, so the engines agree bit for bit. `XNPV`, `XIRR`, `RATE` and the root in `MIRR` use the platform's power, exponential and logarithm functions, which can differ in the last digit between the Python engine, native Rust and the WebAssembly build.

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
on next open. A scalar formula reference to a blank cell is cached as numeric zero.
An explicit empty string remains a string cache.
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
[agent contract](agent-protocol.md) imposes a smaller limit of 100. Imported
agent edits permit value/formula changes to existing cells and to absent cells
inside Excel's grid (inserted on export); style edits, grouped/table results,
preserved-only cells, and merged non-anchor cells fail before mutation. Rust preservation-aware edits
accept existing-cell content edits and absent-cell value/formula insertion
within the session grid bound, and refuse insertion into merged non-anchor
cells or outside that bound (`out_of_bounds`), imported style/layout/validation
changes, and grouped/table result edits. Structure edits remain refused.
Supported fresh-generation styles do not imply unrestricted imported-style
editing.

Macros and external data are never executed or fetched by either SDK. Excel may
recalculate formulas and update workbook-defined external links when a person opens
the output, depending on Excel settings. Macro-enabled `.xlsm` and legacy `.xls`
remain outside the supported workbook profile. Dialog sheets, macro sheets and
Strict OOXML are refused. Chartsheets are listed and preserved byte-for-byte;
they are not modeled as worksheets. Structured table references, grouped/table
formula calculation, worksheet spill placement and 1904-date formula calculation
remain outside the supported workbook profile. In-cell arrays and Compatibility
Version 3 are described in [in-cell arrays](in-cell-arrays.md). Unsupported
formula text can be retained for inspection and preservation; calculation
depending on it is refused.

Array results such as FILTER, SORT and UNIQUE are supported by the shape-preserving
formula API. That does not imply that the workbook adapters can place or edit
worksheet spill cells. Input bindings remain explicit application metadata and
must be supplied when importing XLSX; Excel defined names do not establish those
bindings automatically.
