"""Engine versus generated-runtime equivalence matrix for source profile 6.

Nobody had measured whether the generated profile-6 runtimes agree with the
engines. This module holds the case table, runs every case through each
implementation and classifies the outcome. It decides nothing.

One row per case; the formula under test is always in ``Calc!D1`` and every
other cell is an authored value. The columns:

- ``python_engine``: ``workbook_forge.evaluate_result``, the formula API.
- ``python_session``: the same Python engine behind a toolkit session
  (``WorkbookModel(backend="python").calculate()``), the cell layer.
- ``rust_engine``: the Rust engine through the native bridge
  (``WorkbookModel(backend="rust").calculate()``).
- ``generated_python`` and ``generated_rust``: the profile-6 program that the
  source compiler generates from the exported toolkit workbook
  (``source_graph.compile_workbook``), run through
  ``source_process.run_validated_source`` by ``source_graph.execute``.

Two documented rules separate the formula API from the cell layer, so a case
may name a different formula-API expectation. A blank formula result is
cached as numeric zero at a cell (docs/behavior-profiles.md, "Calculation and
edits"). An engine refusal, which the formula API can only report as a
``#VALUE!`` error value carrying the refusal message, becomes an
``unsupported_formula`` diagnostic in a session. A workbook carrying such a
diagnostic cannot be exported, so refused modes are generated through the
compiler's Program API instead of ``compile_workbook``; ``generated_route``
records which path a row took.

Expected values are hand-derived and each case names its basis: independent
arithmetic, a Microsoft example or statement, or a Workbook Forge profile
named in the docs. None comes from either engine or from a generated runtime.

Each row answers two questions separately. ``agreement``: do the
implementations agree with each other (under the two layer rules)?
``matches_expectation``: do they all give the hand-derived value? The
``classification`` follows: ``agreement`` or ``refusal by design`` when both
hold; ``semantic difference`` or ``test error`` when the implementations
disagree; ``shared deviation`` when they agree with each other but not with
the expectation. A disagreement is never hidden by loosening a comparison.
It is pinned in ``DIVERGENCES`` with the observed values and one
classification, and an unpinned or changed one is reported ``unclassified``.

Usage::

    python tools/source_equivalence_probe.py --output results.json
    python tools/source_equivalence_probe.py --output out.json --function SUM --no-generated-rust
"""
import argparse
from collections import Counter
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

from workbook_forge import ErrorValue, evaluate_result
from workbook_forge import source_graph as g
from workbook_forge.source_contract import SourceDiagnostic
from workbook_forge.toolkit import WorkbookModel
from workbook_forge.workbook import UnsupportedWorkbook
from workbook_forge.xlsx import export_xlsx

SHEET = "Calc"
TARGET = "D1"
SOURCE_PROFILE = 6
UNAVAILABLE = "unavailable"
CELL_COLUMNS = ("python_session", "rust_engine", "generated_python", "generated_rust")
COLUMNS = ("python_engine", *CELL_COLUMNS)
CLASSIFICATIONS = (
    "agreement", "refusal by design", "semantic difference", "test error", "shared deviation"
)
# How the formula API renders a cell-layer value: a blank before the cell
# caches it as zero, and a refusal before the session types it.
FORMULA_API_FORMS = {"0.0": "blank", "refused:unsupported_formula": "#VALUE!"}


@dataclass(frozen=True)
class Refused:
    """An implementation declined the formula with a typed code instead of a value."""

    code: str


class _Same:
    """The formula API is expected to agree with the cell layer."""

    def __repr__(self):
        return "SAME"


SAME = _Same()
REFUSED = Refused("unsupported_formula")


@dataclass(frozen=True, eq=False)
class Case:
    function: str
    mode: str
    formula: str
    cells: dict
    expected: object
    basis: str
    formula_api: object = SAME

    @property
    def expected_formula_api(self):
        return self.expected if self.formula_api is SAME else self.formula_api


@dataclass(frozen=True)
class Divergence:
    """A pinned disagreement: the observed rendering per column and why it stands."""

    classification: str
    observed: dict
    note: str


def error(code):
    return {"error": code}


# Fruit lookup vectors from fixtures/xlookup-source-cases.v1.json, shared with
# python/tests/test_source_graph_v6.py so the XLOOKUP rows read against the same data.
FRUIT = {
    "A1": "Pear", "A2": "pear", "A3": None, "A4": "Apple", "B1": 10, "B2": 20, "B3": 30, "B4": 40
}
MIXED = {"A1": 1, "A2": "7", "A3": None, "A4": True, "A5": 2.5}
ZERO = {"A1": 0}
SUM_REMARK = (
    "Microsoft WorksheetFunction.Sum remarks: numbers, logical values and numeric text typed "
    "directly are counted; in an array or reference only numbers count and empty cells, "
    "logical values and text are ignored; text that cannot be read as a number is an error"
)
AVERAGE_RANGE = (
    "Microsoft AVERAGE remarks: in a range or reference, text, logical values and empty cells "
    "are ignored and cells holding zero are included"
)
AVERAGE_DIRECT = (
    "Microsoft's two references disagree on direct arguments: WorksheetFunction.Average says "
    "logical values and numeric text typed directly are counted, the Excel AVERAGE page says "
    "they are not; the expectation follows the counted rule, which the compiler manifest "
    "marks provisional, and an Excel observation would settle it"
)
AVERAGE_TEXT_ERROR = (
    "Microsoft AVERAGE remarks: text that cannot be translated into a number causes an error"
)
# Unary plus is the only row that still cites this phrase. The engines do not
# match that report, so the row is a shared deviation. A row the engines match
# names a Workbook Forge profile instead and keeps the unverified note.
REPORTED = "reported Excel behavior, not stated on the Microsoft page; unverified here"
WOULD_SETTLE = "an Excel observation would settle the expectation"
IF_OMITTED_FALSE = (
    "Workbook Forge IF omitted-argument profile (catalog/formulas.json, IF semantics "
    "default): an omitted value_if_false returns FALSE; Microsoft's IF page only marks "
    "the argument optional; " + WOULD_SETTLE
)
IF_TEXT_NOT_LOGICAL = (
    "Workbook Forge logical-test profile: nonempty text is not a logical value, so "
    "logical_test is #VALUE!; " + WOULD_SETTLE
)
XLOOKUP_TEXT_MATCH = (
    "Workbook Forge XLOOKUP exact-match profile (docs/source-graph-v6.md): lookup text "
    "compares without regard to case; that page records these expected values as a local "
    "implementation profile, not an Excel observation; " + WOULD_SETTLE
)
SORT_ORDER = (
    "Excel default sort order places numbers before text before logical values, so the "
    "comparison operators rank them in that order and never coerce across types"
)
XLOOKUP_MODES = (
    "Microsoft documents match_mode -1, 0, 1, 2 and search_mode 1, -1, 2, -2; a value outside "
    "those lists is an invalid argument, and the exact Excel error code is unverified"
)
PROFILE_COMPARISON = (
    "Workbook Forge binary comparison profile (CONTEXT.md), not an Excel observation"
)
SIGNED_ZERO = (
    "IEEE 754 binary64 arithmetic; Excel displays every zero without a sign, so the sign is "
    "unobservable there"
)

CASES = [
    # SUM
    Case("SUM", "range_skips_text_logical_blank", "=SUM(A1:A5)", MIXED, 3.5,
         "1 + 2.5 = 3.5; " + SUM_REMARK),
    Case("SUM", "direct_numeric_text_and_logical", '=SUM(A1,"7",TRUE)', MIXED, 9.0,
         "1 + 7 + 1 = 9; " + SUM_REMARK),
    Case("SUM", "cell_reference_text_is_skipped", "=SUM(A2)", MIXED, 0.0,
         "a referenced cell holding the text 7 is ignored; " + SUM_REMARK),
    Case("SUM", "direct_non_numeric_text", '=SUM("abc",1)', MIXED, error("#VALUE!"),
         SUM_REMARK),
    Case("SUM", "error_in_range_propagates", "=SUM(A1:A3)", {"A1": 1, "A2": error("#N/A"), "A3": 2},
         error("#N/A"), "Microsoft general rule: an error value in an argument is the result"),
    Case("SUM", "blank_only_range", "=SUM(A3:A4)", {"A3": None, "A4": None}, 0.0,
         "nothing to add is 0"),
    # IF
    Case("IF", "lazy_untaken_error_branch", "=IF(A1>0,A1*2,1/0)", {"A1": 3}, 6.0,
         "3 > 0 so 3 * 2 = 6; the untaken 1/0 is never evaluated"),
    Case("IF", "taken_error_branch", "=IF(A1>0,1/0,5)", {"A1": 3}, error("#DIV/0!"),
         "the taken branch divides by zero"),
    Case("IF", "two_arguments_false_default", "=IF(A1>5,1)", {"A1": 3}, False,
         "with value_if_false omitted a false test returns FALSE (" + IF_OMITTED_FALSE + ")"),
    Case("IF", "blank_condition_is_false", "=IF(A3,1,2)", {"A3": None}, 2.0,
         "a blank test is FALSE, so the third argument is returned"),
    Case("IF", "numeric_condition_nonzero_is_true", "=IF(A1,1,2)", {"A1": 3}, 1.0,
         "a nonzero number is TRUE"),
    Case("IF", "text_condition_is_value_error", '=IF("abc",1,2)', {}, error("#VALUE!"),
         "text has no truth value, so logical_test is #VALUE! (" + IF_TEXT_NOT_LOGICAL + ")"),
    Case("IF", "numeric_text_cell_condition_is_value_error", "=IF(A2,1,2)", MIXED,
         error("#VALUE!"), "a cell holding the text 7 is text, not a logical value ("
         + IF_TEXT_NOT_LOGICAL + ")"),
    Case("IF", "error_condition_propagates", "=IF(1/0,1,2)", {}, error("#DIV/0!"),
         "an error in logical_test is the result"),
    Case("IF", "blank_cell_branch_result", "=IF(A1>0,A3,1)", {"A1": 3, "A3": None}, 0.0,
         "the taken branch is a blank cell; a cell caches a blank formula result as 0 "
         "(docs/behavior-profiles.md) while the formula API reports the blank",
         formula_api=None),
    # IFERROR
    Case("IFERROR", "div0_takes_fallback", '=IFERROR(A1/A2,"fallback")', {"A1": 6, "A2": 0},
         "fallback", "6/0 is #DIV/0!, so the fallback text is returned"),
    Case("IFERROR", "clean_value_keeps_value", '=IFERROR(A1/A5,"fallback")', {"A1": 6, "A5": 3},
         2.0, "6 / 3 = 2 is not an error"),
    Case("IFERROR", "fallback_not_evaluated_on_clean", "=IFERROR(A1/A5,1/0)", {"A1": 6, "A5": 3},
         2.0, "6 / 3 = 2; the fallback 1/0 is never evaluated"),
    Case("IFERROR", "fallback_error_propagates", "=IFERROR(1/0,1/0)", {}, error("#DIV/0!"),
         "the fallback itself divides by zero"),
    Case("IFERROR", "blank_value_is_empty_text", "=IFERROR(A3,9)", {"A3": None}, "",
         'Microsoft IFERROR: an empty cell in value is treated as the empty string ""'),
    Case("IFERROR", "blank_fallback_is_empty_text", "=IFERROR(1/0,A3)", {"A3": None}, "",
         'Microsoft IFERROR: an empty cell in value_if_error is treated as the empty string ""'),
    Case("IFERROR", "error_cell_takes_fallback", "=IFERROR(A2,A1)", {"A1": 6, "A2": error("#N/A")},
         6.0, "the value cell holds #N/A, so the fallback cell 6 is returned"),
    # AVERAGE
    Case("AVERAGE", "range_skips_text_logical_blank_keeps_zero", "=AVERAGE(A1:A5)",
         {"A1": 4, "A2": 0, "A3": "8", "A4": True, "A5": None}, 2.0,
         "(4 + 0) / 2 = 2; " + AVERAGE_RANGE),
    Case("AVERAGE", "microsoft_example_range_plus_direct", "=AVERAGE(A2:A6,5)",
         {"A2": 10, "A3": 7, "A4": 9, "A5": 27, "A6": 2}, 10.0,
         "Microsoft AVERAGE example: (10 + 7 + 9 + 27 + 2 + 5) / 6 = 10"),
    Case("AVERAGE", "direct_numeric_text_counted", '=AVERAGE("8",4)', {}, 6.0,
         "(8 + 4) / 2 = 6 under the counted rule; " + AVERAGE_DIRECT),
    Case("AVERAGE", "direct_logical_counted", "=AVERAGE(TRUE,3)", {}, 2.0,
         "(1 + 3) / 2 = 2 under the counted rule; " + AVERAGE_DIRECT),
    Case("AVERAGE", "direct_non_numeric_text", '=AVERAGE("abc",3)', {}, error("#VALUE!"),
         AVERAGE_TEXT_ERROR),
    Case("AVERAGE", "direct_logical_text_is_not_numeric", '=AVERAGE("TRUE",3)', {},
         error("#VALUE!"), "the text TRUE is not a text representation of a number; "
         + AVERAGE_TEXT_ERROR),
    Case("AVERAGE", "cell_reference_text_skipped", "=AVERAGE(A3,4)", {"A3": "8"}, 4.0,
         "the referenced text 8 is ignored, so 4 / 1 = 4; " + AVERAGE_RANGE),
    Case("AVERAGE", "blank_only_is_div0", "=AVERAGE(A3:A4)", {"A3": None, "A4": None},
         error("#DIV/0!"), "no numeric value to average divides by a zero count"),
    Case("AVERAGE", "error_in_range_propagates", "=AVERAGE(A1:A3)",
         {"A1": 1, "A2": error("#NUM!"), "A3": 2}, error("#NUM!"),
         "Microsoft general rule: an error value in an argument is the result"),
    # XLOOKUP
    Case("XLOOKUP", "exact_forward", '=XLOOKUP("PEAR",A1:A4,B1:B4)', FRUIT, 10.0,
         "the first match of PEAR without regard to case is row 1 (" + XLOOKUP_TEXT_MATCH + ")"),
    Case("XLOOKUP", "exact_reverse", '=XLOOKUP("pear",A1:A4,B1:B4,,,-1)', FRUIT, 20.0,
         "search_mode -1 searches from the last item, so the row 2 duplicate wins"),
    Case("XLOOKUP", "if_not_found_taken", '=XLOOKUP("missing",A1:A4,B1:B4,"not found")', FRUIT,
         "not found", "no match, so if_not_found is returned"),
    Case("XLOOKUP", "if_not_found_not_taken_lazy", '=XLOOKUP("Pear",A1:A4,B1:B4,1/0)', FRUIT,
         10.0, "row 1 matches, so the 1/0 fallback is never evaluated"),
    Case("XLOOKUP", "if_not_found_blank_cell", '=XLOOKUP("missing",A1:A4,B1:B4,A3)', FRUIT,
         0.0, "no match, so the blank fallback cell is the result; a cell caches a blank "
         "formula result as 0 (docs/behavior-profiles.md) while the formula API reports the blank",
         formula_api=None),
    Case("XLOOKUP", "not_found_default_na", '=XLOOKUP("missing",A1:A4,B1:B4)', FRUIT,
         error("#N/A"), "Microsoft: with if_not_found omitted a miss returns #N/A"),
    Case("XLOOKUP", "blank_lookup_matches_blank_cell", "=XLOOKUP(,A1:A4,B1:B4)", FRUIT, 30.0,
         "Microsoft: an omitted lookup_value finds the blank cells in lookup_array; row 3"),
    Case("XLOOKUP", "numeric_lookup_returns_text", "=XLOOKUP(20,B1:B4,A1:A4)", FRUIT, "pear",
         "20 is row 2 of B1:B4, whose A cell holds pear"),
    Case("XLOOKUP", "numeric_text_does_not_match_number", '=XLOOKUP("10",B1:B4,A1:A4)', FRUIT,
         error("#N/A"), "the text 10 is not the number 10; " + SORT_ORDER),
    Case("XLOOKUP", "logical_lookup_matches_logical", "=XLOOKUP(TRUE,A1:A4,B1:B4)",
         {**FRUIT, "A4": True}, 40.0, "TRUE in row 4 matches the logical lookup value"),
    Case("XLOOKUP", "number_does_not_match_logical", "=XLOOKUP(1,A1:A4,B1:B4)",
         {**FRUIT, "A4": True}, error("#N/A"), "the number 1 is not the logical TRUE; "
         + SORT_ORDER),
    Case("XLOOKUP", "row_vectors", '=XLOOKUP("b",A1:C1,A2:C2)',
         {"A1": "a", "B1": "b", "C1": "c", "A2": 1, "B2": 2, "C2": 3}, 2.0,
         "b is column 2 of the row vector A1:C1, whose A2:C2 cell holds 2"),
    Case("XLOOKUP", "single_cell_arrays", '=XLOOKUP("Pear",A1,B1)', FRUIT, 10.0,
         "a single cell is a one-by-one array; Pear matches A1, so B1 holds the result 10"),
    Case("XLOOKUP", "match_mode_numeric_text_zero", '=XLOOKUP("Pear",A1:A4,B1:B4,,"0")', FRUIT,
         10.0, "the text 0 reads as the number 0, the exact match mode; row 1 holds 10"),
    Case("XLOOKUP", "error_in_scanned_lookup_array", '=XLOOKUP("Apple",C1:C3,B1:B3)',
         {**FRUIT, "C1": "Kiwi", "C2": error("#REF!"), "C3": "Apple"}, error("#REF!"),
         "Microsoft general rule: an error value in an argument is the result; the error "
         "precedes the match in search order"),
    Case("XLOOKUP", "error_after_match_not_reached", '=XLOOKUP("Pear",C1:C3,B1:B3)',
         {**FRUIT, "C1": "Pear", "C2": error("#REF!"), "C3": "x"}, 10.0,
         "Pear matches row 1 before the forward search reaches the error in row 2"),
    Case("XLOOKUP", "approximate_next_smaller_refused", '=XLOOKUP("x",A1:A4,B1:B4,,-1)', FRUIT,
         REFUSED, "Microsoft documents match_mode -1; docs/source-graph-v6.md: not implemented, "
         "refused rather than treated as exact", formula_api=error("#VALUE!")),
    Case("XLOOKUP", "approximate_next_larger_refused", '=XLOOKUP("x",A1:A4,B1:B4,,1)', FRUIT,
         REFUSED, "Microsoft documents match_mode 1; not implemented, refused",
         formula_api=error("#VALUE!")),
    Case("XLOOKUP", "wildcard_refused", '=XLOOKUP("P*",A1:A4,B1:B4,,2)', FRUIT, REFUSED,
         "Microsoft documents match_mode 2; not implemented, refused",
         formula_api=error("#VALUE!")),
    Case("XLOOKUP", "binary_ascending_refused", '=XLOOKUP("Pear",A1:A4,B1:B4,,0,2)', FRUIT,
         REFUSED, "Microsoft documents search_mode 2; not implemented, refused",
         formula_api=error("#VALUE!")),
    Case("XLOOKUP", "binary_descending_refused", '=XLOOKUP("Pear",A1:A4,B1:B4,,0,-2)', FRUIT,
         REFUSED, "Microsoft documents search_mode -2; not implemented, refused",
         formula_api=error("#VALUE!")),
    Case("XLOOKUP", "match_mode_fractional_is_invalid", '=XLOOKUP("Pear",A1:A4,B1:B4,,0.5)',
         FRUIT, error("#VALUE!"), XLOOKUP_MODES),
    Case("XLOOKUP", "search_mode_fractional_is_invalid", '=XLOOKUP("Pear",A1:A4,B1:B4,,0,1.5)',
         FRUIT, error("#VALUE!"), XLOOKUP_MODES),
    Case("XLOOKUP", "search_mode_zero_is_invalid", '=XLOOKUP("Pear",A1:A4,B1:B4,,0,0)', FRUIT,
         error("#VALUE!"), XLOOKUP_MODES),
    # Operators of the profile-6 dialect: + - * / unary signs and the six comparisons
    Case("OPERATORS", "arithmetic_precedence", "=1+2*3-8/4", {}, 5.0,
         "1 + 6 - 2 = 5"),
    Case("OPERATORS", "numeric_text_coerces", "=A2+1", MIXED, 8.0,
         "the text 7 reads as 7 in arithmetic; 7 + 1 = 8"),
    Case("OPERATORS", "logical_coerces_in_arithmetic", "=TRUE+1", {}, 2.0,
         "TRUE is 1 in arithmetic; 1 + 1 = 2"),
    Case("OPERATORS", "blank_is_zero_in_arithmetic", "=A3*3", MIXED, 0.0,
         "a blank is 0 in arithmetic; 0 * 3 = 0"),
    Case("OPERATORS", "non_numeric_text_arithmetic", '="abc"+1', {}, error("#VALUE!"),
         "Microsoft: text that cannot be converted to a number in arithmetic is #VALUE!"),
    Case("OPERATORS", "division_by_zero", "=A1/A3", MIXED, error("#DIV/0!"),
         "Microsoft: dividing by a zero or blank cell is #DIV/0!"),
    Case("OPERATORS", "unary_minus_on_numeric_text", "=-A2", MIXED, -7.0,
         "the text 7 reads as 7; its negation is -7"),
    Case("OPERATORS", "unary_minus_on_blank_is_negative_zero", "=-A3", MIXED, -0.0,
         "a blank is 0 and -(+0) is -0 in " + SIGNED_ZERO),
    Case("OPERATORS", "zero_times_negative_keeps_sign", "=A1*-1", ZERO, -0.0,
         "0 * -1 is -0 in " + SIGNED_ZERO),
    Case("OPERATORS", "unary_plus_leaves_text", "=+A2", MIXED, "7",
         "unary plus is the identity and does not coerce its operand, so the text 7 stays text "
         "(" + REPORTED + ")"),
    Case("OPERATORS", "overflow_is_num_error", "=1E308*10", {}, error("#NUM!"),
         "Microsoft documents 9.99999999999999E+307 as the largest number; the #NUM! code is "
         "a Workbook Forge profile (docs/behavior-profiles.md, General)"),
    Case("OPERATORS", "text_equality_ascii_case_insensitive", '="Pear"="PEAR"', {}, True,
         "the = operator compares ASCII text without regard to case (" + PROFILE_COMPARISON
         + "; Microsoft's EXACT page offers EXACT as the case-sensitive comparison; "
         + WOULD_SETTLE + ")"),
    Case("OPERATORS", "text_relational_order", '="apple"<"Banana"', {}, True,
         "case-insensitive text order: a precedes b"),
    Case("OPERATORS", "number_text_equality_is_false", '=1="1"', {}, False, SORT_ORDER),
    Case("OPERATORS", "number_text_inequality_is_true", '=1<>"1"', {}, True, SORT_ORDER),
    Case("OPERATORS", "number_ranks_below_text", '=1<"a"', {}, True, SORT_ORDER),
    Case("OPERATORS", "logical_ranks_above_number", "=TRUE>1", {}, True, SORT_ORDER),
    Case("OPERATORS", "logical_number_equality_is_false", "=TRUE=1", {}, False, SORT_ORDER),
    Case("OPERATORS", "blank_equals_zero", "=A3=0", MIXED, True,
         "a blank compared with a number is 0"),
    Case("OPERATORS", "blank_equals_empty_text", '=A3=""', MIXED, True,
         "a blank compared with text is the empty string"),
    Case("OPERATORS", "blank_equals_false", "=A3=FALSE", MIXED, True,
         "a blank compared with a logical value is FALSE"),
    Case("OPERATORS", "error_operands_left_first", '=(1/0)<("abc"+1)', {}, error("#DIV/0!"),
         "both operands are errors; the left one is reported (" + PROFILE_COMPARISON + ")"),
    Case("OPERATORS", "non_ascii_text_compares_exactly", '="é"="É"', {}, False,
         "non-ASCII text compares exactly, so the accented pair differs (" + PROFILE_COMPARISON
         + "; Excel's collation is unverified)"),
    Case("OPERATORS", "ascii_text_ranks_below_non_ascii", '="A"<"é"', {}, True,
         "with a non-ASCII operand the UTF-8 bytes compare, and A (0x41) precedes é (0xC3 0xA9) "
         "(" + PROFILE_COMPARISON + ")"),
]

# Pinned disagreements. Key: (function, mode). Every other row must agree with
# itself and its expectation; a new disagreement is reported as unclassified
# until it is examined and added here with the observed renderings and one
# classification. Columns absent from an installation are not compared.
DIVERGENCES = {
    ("XLOOKUP", "single_cell_arrays"): Divergence(
        "semantic difference",
        {"python_engine": "#VALUE!", "python_session": "#VALUE!", "rust_engine": "10.0",
         "generated_python": "10.0", "generated_rust": "10.0"},
        "The Python engine dereferences a single-cell lookup_array to a scalar and reports "
        "'XLOOKUP lookup_array must be a cell range' (#VALUE!); the Rust engine and both "
        "generated runtimes accept a single cell as a one-cell vector, as "
        "docs/source-graph-v6.md documents for the source dialect.",
    ),
    ("XLOOKUP", "match_mode_fractional_is_invalid"): Divergence(
        "semantic difference",
        {"python_engine": "#VALUE!", "python_session": "refused:unsupported_formula",
         "rust_engine": "#VALUE!", "generated_python": "#VALUE!", "generated_rust": "#VALUE!"},
        "The Python engine tests match_mode != 0 before checking that it is an integer, so "
        "0.5 is an unsupported mode and the session refuses the cell; the Rust engine "
        "(mode_argument) and both generated runtimes (lookup_mode) reject a fractional mode as "
        "an invalid argument, #VALUE!, before the unsupported-mode check.",
    ),
    ("XLOOKUP", "search_mode_fractional_is_invalid"): Divergence(
        "semantic difference",
        {"python_engine": "#VALUE!", "python_session": "refused:unsupported_formula",
         "rust_engine": "#VALUE!", "generated_python": "#VALUE!", "generated_rust": "#VALUE!"},
        "Same ordering difference as match_mode: the Python engine treats 1.5 as an "
        "unsupported search mode and the session refuses; the Rust engine and both generated "
        "runtimes return #VALUE! for a non-integer mode.",
    ),
    ("XLOOKUP", "search_mode_zero_is_invalid"): Divergence(
        "shared deviation",
        {"python_engine": "#VALUE!", "python_session": "refused:unsupported_formula",
         "rust_engine": "refused:unsupported_formula",
         "generated_python": "refused:unsupported_formula",
         "generated_rust": "refused:unsupported_formula"},
        "Every implementation treats search_mode 0, which Microsoft does not list, as an "
        "unimplemented mode and refuses the cell (unsupported_formula) instead of returning "
        "an invalid-argument error value; the Python formula API shows the refusal as #VALUE!.",
    ),
    ("OPERATORS", "zero_times_negative_keeps_sign"): Divergence(
        "semantic difference",
        {"python_engine": "0.0", "python_session": "0.0", "rust_engine": "-0.0",
         "generated_python": "-0.0", "generated_rust": "-0.0"},
        "The Python engine's number coercion returns integer-valued operands as exact "
        "integers, and 0 * -1 in integer arithmetic has no sign; the Rust engine and both "
        "generated runtimes multiply binary64 values and keep IEEE's negative zero. Excel "
        "displays every zero unsigned, so this cannot change a displayed result.",
    ),
    ("OPERATORS", "unary_plus_leaves_text"): Divergence(
        "shared deviation",
        {"python_engine": "7.0", "python_session": "7.0", "rust_engine": "7.0",
         "generated_python": "7.0", "generated_rust": "7.0"},
        "Every implementation coerces the operand of unary plus to a number, so the text 7 "
        "becomes 7.0; Excel is reported to return the operand unchanged. An Excel observation "
        "would settle the expectation.",
    ),
}


def case_id(case):
    return f"{case.function}-{case.mode}"


def render(value):
    """One comparable, JSON-friendly spelling per value kind; -0.0 keeps its sign."""
    if isinstance(value, Refused):
        return f"refused:{value.code}"
    if isinstance(value, ErrorValue):
        return value.code
    if isinstance(value, dict):
        return value["error"]
    if value is None:
        return "blank"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(float(value))
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise TypeError(f"unrenderable result {value!r}")


def rust_engine_available():
    return importlib.util.find_spec("workbook_forge._native") is not None


def rustc_available():
    return shutil.which("rustc") is not None


def python_engine(case):
    cells = {
        address: ErrorValue(value["error"]) if isinstance(value, dict) else value
        for address, value in case.cells.items()
    }
    return evaluate_result(case.formula, cells, SHEET)


def _model(case, backend):
    model = WorkbookModel(sheets=[SHEET], backend=backend)
    for address, value in case.cells.items():
        model.set_value(SHEET, address, value)
    model.set_formula(SHEET, TARGET, case.formula)
    return model


def session(case, backend):
    report = _model(case, backend).calculate()
    for item in report["diagnostics"]:
        if item.get("sheet") == SHEET and item.get("address") == TARGET:
            return Refused(item["code"])
    if report["diagnostics"]:
        raise AssertionError(f"diagnostic outside {TARGET}: {report['diagnostics']}")
    return report["values"][f"{SHEET}!{TARGET}"]


def generated_programs(case, directory, languages):
    """Generate, run and read back Calc!D1 for each language; also the route taken."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "source.xlsx"
    try:
        export_xlsx(_model(case, "python"), source)
        route = "compile_workbook"
    except UnsupportedWorkbook:
        # The engine refused the formula, so the toolkit will not export the
        # workbook; build the same program through the compiler's Program API.
        route = "program_api"
    prefix = g.sheet_prefix(SHEET)
    results = {}
    for language in languages:
        try:
            if route == "compile_workbook":
                code, _ = g.compile_workbook(source, language, version=SOURCE_PROFILE)
            else:
                node = g.from_excel(g.parse_expression(case.formula), SHEET, version=SOURCE_PROFILE)
                defaults = {
                    prefix + address: g.scalar(value) for address, value in case.cells.items()
                }
                program = g.Program(
                    SHEET, defaults, {prefix + TARGET: node}, (SHEET,), SOURCE_PROFILE
                )
                code = g.generate(program, language)
            path = directory / ("program.py" if language == "python" else "program.rs")
            path.write_text(code, encoding="utf-8")
            values = g.execute(path, language, {}, directory / f"binary-{language}")
        except SourceDiagnostic as diagnostic:
            results[language] = Refused(diagnostic.code)
        else:
            results[language] = values[prefix + TARGET]
    return results, route


def implementations_agree(row):
    """Cell-layer columns agree, the formula API shows that value or its layer form, and
    the generated programs came from the route the session's verdict calls for."""
    if row["generated_route"] != row["expected_route"]:
        return False
    values = {row[column] for column in CELL_COLUMNS if row[column] != UNAVAILABLE}
    if len(values) != 1:
        return False
    value = values.pop()
    return row["python_engine"] in {value, FORMULA_API_FORMS.get(value)}


def matches_expectation(row):
    wanted = {"python_engine": row["expected_formula_api"]}
    wanted.update({column: row["expected"] for column in CELL_COLUMNS})
    return all(row[column] in {UNAVAILABLE, want} for column, want in wanted.items())


def classify(row, case):
    agree = implementations_agree(row)
    matched = matches_expectation(row)
    row["agreement"] = agree
    row["matches_expectation"] = matched
    if agree and matched:
        refused = isinstance(case.expected, Refused)
        row["classification"] = "refusal by design" if refused else "agreement"
        row["note"] = ""
        return row
    allowed = {"shared deviation"} if agree else {"semantic difference", "test error"}
    divergence = DIVERGENCES.get((case.function, case.mode))
    pinned = (
        divergence is not None
        and divergence.classification in allowed
        and all(row[column] in {UNAVAILABLE, observed}
                for column, observed in divergence.observed.items())
    )
    if pinned:
        row["classification"] = divergence.classification
        row["note"] = divergence.note
    else:
        observed = {column: row[column] for column in COLUMNS}
        observed["generated_route"] = row["generated_route"]
        row["classification"] = "unclassified"
        row["note"] = "not pinned in DIVERGENCES: " + json.dumps(observed, ensure_ascii=False)
    return row


def run_case(case, directory, *, rust_engine=True, generated_rust=True):
    python_session = session(case, "python")
    row = {
        "function": case.function,
        "mode": case.mode,
        "formula": case.formula,
        "expected": render(case.expected),
        "expected_formula_api": render(case.expected_formula_api),
        "basis": case.basis,
        "python_engine": render(python_engine(case)),
        "python_session": render(python_session),
        "rust_engine": render(session(case, "rust")) if rust_engine else UNAVAILABLE,
    }
    languages = ("python", "rust") if generated_rust else ("python",)
    programs, route = generated_programs(case, directory, languages)
    row["generated_python"] = render(programs["python"])
    row["generated_rust"] = render(programs["rust"]) if generated_rust else UNAVAILABLE
    # Export refuses a workbook whose Python session reported a diagnostic, and
    # only then; any other route switch is a change in export that must show.
    row["expected_route"] = (
        "program_api" if isinstance(python_session, Refused) else "compile_workbook"
    )
    row["generated_route"] = route
    return classify(row, case)


def build_matrix(cases, *, rust_engine, generated_rust):
    with tempfile.TemporaryDirectory(prefix="forge-equivalence-") as directory:
        rows = [
            run_case(case, Path(directory) / str(index), rust_engine=rust_engine,
                     generated_rust=generated_rust)
            for index, case in enumerate(cases)
        ]
    summary = Counter(row["classification"] for row in rows)
    return {
        "record": "engine versus generated-runtime equivalence matrix",
        "source_profile": SOURCE_PROFILE,
        "functions": sorted({case.function for case in cases}),
        "columns": {
            "python_engine": "workbook_forge.evaluate_result (formula API)",
            "python_session": "Python engine through WorkbookModel(backend='python').calculate()",
            "rust_engine": "Rust engine through the native bridge, WorkbookModel(backend='rust')",
            "generated_python": "generated profile-6 Python program via run_validated_source",
            "generated_rust": "generated profile-6 Rust program compiled by rustc",
        },
        "rust_engine": "compared" if rust_engine else UNAVAILABLE,
        "generated_rust": "compared" if generated_rust else UNAVAILABLE,
        "cases": len(rows),
        "summary": {name: summary.get(name, 0) for name in (*CLASSIFICATIONS, "unclassified")},
        "divergences": [
            {"function": function, "mode": mode, "classification": item.classification,
             "observed": item.observed, "note": item.note}
            for (function, mode), item in DIVERGENCES.items()
        ],
        "rows": rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", type=Path, required=True, help="where to write the JSON matrix")
    parser.add_argument("--function", action="append",
                        help="limit the matrix to one function (repeatable)")
    parser.add_argument("--no-rust-engine", action="store_true",
                        help="skip the Rust engine column even when the native bridge is present")
    parser.add_argument("--no-generated-rust", action="store_true",
                        help="skip the generated Rust column even when rustc is on PATH")
    args = parser.parse_args(argv)
    cases = [case for case in CASES if not args.function or case.function in args.function]
    if not cases:
        parser.error("no case matches --function " + ", ".join(args.function))
    matrix = build_matrix(
        cases,
        rust_engine=rust_engine_available() and not args.no_rust_engine,
        generated_rust=rustc_available() and not args.no_generated_rust,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(matrix, indent=2, ensure_ascii=False) + "\n"
    args.output.write_text(encoded, encoding="utf-8")
    print(json.dumps({"cases": matrix["cases"], **matrix["summary"],
                      "rust_engine": matrix["rust_engine"],
                      "generated_rust": matrix["generated_rust"]}))
    return 1 if matrix["summary"]["unclassified"] else 0


if __name__ == "__main__":
    sys.exit(main())
