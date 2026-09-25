import json
import math
from pathlib import Path

import pytest

from formula_atlas import ArrayValue, ErrorValue, evaluate, evaluate_result


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        ("=2+3*4", 14),
        ("=(2+3)*4", 20),
        ("=-2^2", 4),
        ("=2^3^2", 64),
        ('="a"&"b"', "ab"),
        ("=5>=5", True),
        ("=1<>2", True),
        ("=SUM(A1:A3)", 30),
        ("=AVERAGE(A1:A3)", 15),
        ("=COUNT(A1:A3)", 2),
        ("=COUNTA(A1:A3)", 3),
        ("=MIN(A1:A3)", 10),
        ("=MAX(A1:A3)", 20),
        ("=IF(A1>2,\"yes\",1/0)", "yes"),
        ("=IFERROR(1/0,\"safe\")", "safe"),
        ("=AND(TRUE,1)", True),
        ("=OR(FALSE,TRUE)", True),
        ("=NOT(FALSE)", True),
        ("=ABS(-3)", 3),
        ("=ROUND(12.345,2)", 12.35),
        ('=LEFT("atlas",2)', "at"),
        ('=RIGHT("atlas",2)', "as"),
        ('=MID("atlas",2,3)', "tla"),
        ('=LEN("atlas")', 5),
        ('=CONCAT("a","b")', "ab"),
        ("=DATE(2024,2,29)", 45351),
        ("=DAY(DATE(2024,2,29))", 29),
        ("=YEAR(DATE(2024,2,29))", 2024),
        ("=MATCH(20,A1:A3,0)", 2),
        ("=XMATCH(20,A1:A3)", 2),
        ("=INDEX(A1:B2,2,2)", "b"),
        ("=VLOOKUP(10,A1:B2,2,FALSE)", "a"),
        ("=XLOOKUP(20,A1:A3,B1:B3)", "b"),
    ],
)
def test_supported_formulas(formula, expected):
    cells = {
        "A1": 10,
        "A2": 20,
        "A3": "x",
        "B1": "a",
        "B2": "b",
    }
    assert evaluate(formula, cells) == expected


def _fixture_value(value):
    if isinstance(value, dict) and isinstance(value.get("error"), str):
        return ErrorValue(value["error"], "shared fixture error cell")
    return value


def test_shared_golden_fixture():
    fixture = Path(__file__).parents[2] / "fixtures" / "formula-cases.jsonl"
    for line in fixture.read_text().splitlines():
        case = json.loads(line)
        cells = {
            address: _fixture_value(value)
            for address, value in case.get("cells", {}).items()
        }
        expected = case["expected"]
        value = (
            evaluate_result(case["formula"], cells)
            if "array" in expected
            else evaluate(case["formula"], cells)
        )
        if "array" in expected:
            target = expected["array"]
            assert isinstance(value, ArrayValue), case["id"]
            assert value.shape == (len(target), len(target[0])), case["id"]
            for actual_row, expected_row in zip(value.rows, target, strict=True):
                for actual_cell, expected_cell in zip(actual_row, expected_row, strict=True):
                    if isinstance(expected_cell, (int, float)) and not isinstance(expected_cell, bool):
                        assert (
                            isinstance(actual_cell, (int, float))
                            and not isinstance(actual_cell, bool)
                            and math.isclose(
                                actual_cell,
                                expected_cell,
                                rel_tol=1e-12,
                                abs_tol=1e-12,
                            )
                        ), case["id"]
                    elif isinstance(expected_cell, dict) and isinstance(expected_cell.get("error"), str):
                        assert isinstance(actual_cell, ErrorValue) and actual_cell.code == expected_cell["error"], case["id"]
                    else:
                        assert actual_cell == expected_cell, case["id"]
        elif "exact_number" in expected:
            target = int(expected["exact_number"])
            assert (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value == target
            ), case["id"]
        elif "number" in expected:
            assert (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isclose(value, expected["number"], rel_tol=1e-12, abs_tol=1e-12)
            ), case["id"]
        elif "text" in expected:
            assert value == expected["text"], case["id"]
        elif "bool" in expected:
            assert value is expected["bool"], case["id"]
        elif expected.get("blank") is True:
            assert value is None, case["id"]
        elif "error" in expected:
            assert isinstance(value, ErrorValue) and value.code == expected["error"], case["id"]
        else:
            raise AssertionError(f"unknown fixture result shape for {case['id']}")


def test_result_api_preserves_array_shape_and_scalar_api_stays_scalar():
    result = evaluate_result("=SEQUENCE(2,3,10,10)")
    assert isinstance(result, ArrayValue)
    assert result.shape == (2, 3)
    assert result.rows == ((10.0, 20.0, 30.0), (40.0, 50.0, 60.0))
    legacy = evaluate("=SEQUENCE(2,3,10,10)")
    assert isinstance(legacy, ErrorValue) and legacy.code == "#VALUE!"
    assert evaluate("=SUM(SEQUENCE(3,2))") == 21.0


def test_result_api_materializes_a_range_without_exposing_private_range_type():
    result = evaluate_result("=A1:B2", {"A1": 1, "B1": 2, "A2": 3, "B2": 4})
    assert isinstance(result, ArrayValue)
    assert result.shape == (2, 2)
    assert result.rows == ((1, 2), (3, 4))
    assert isinstance(evaluate("=A1:B2"), ErrorValue)


@pytest.mark.parametrize(
    ("formula", "code"),
    [
        ("=BOGUS(1)", "#NAME?"),
        ("=A1+", "#VALUE!"),
        ("=1/0", "#DIV/0!"),
        ("=INDEX(A1:A2,3)", "#REF!"),
        ("=SUM(A1:A2", "#VALUE!"),
    ],
)
def test_errors_are_explicit(formula, code):
    value = evaluate(formula, {"A1": 1, "A2": 2})
    assert isinstance(value, ErrorValue)
    assert value.code == code


def test_workday_date_rejects_python_integers_outside_binary64_domain():
    value = evaluate("=WORKDAY(A1,1)", {"A1": 10**400})
    assert isinstance(value, ErrorValue)
    assert value.code == "#NUM!"


def test_combinatorics_rejects_python_integers_outside_exact_argument_domain():
    value = evaluate("=COMBIN(A1,0)", {"A1": 9_007_199_254_740_993})
    assert isinstance(value, ErrorValue)
    assert value.code == "#NUM!"


def test_combinatorics_rounds_exact_integer_results_once_to_binary64():
    assert evaluate("=COMBIN(54,23)") == float(math.comb(54, 23))
    assert evaluate("=COMBIN(60,19)") == float(math.comb(60, 19))
    assert evaluate("=COMBIN(1029,514)") == float(math.comb(1029, 514))
    assert evaluate("=FACT(170)") == float(math.factorial(170))


def test_sheet_qualified_cells_and_mapping_keys():
    assert evaluate("=Data!A1+2", {"Data!A1": 40}) == 42
    assert evaluate("=A1+2", {"Data!A1": 40}, sheet_name="Data") == 42
    assert evaluate("=A1+2", {"A1": 40}, sheet_name="Data") == 42


def test_absolute_references_and_case_insensitive_function_names():
    assert evaluate("=sUm($a$1:A2)", {"A1": 3, "A2": 4}) == 7


def test_empty_cells_in_aggregate_ranges_are_blank():
    assert evaluate("=COUNT(A1:A3)", {"A1": 5}) == 1
    assert evaluate("=COUNTA(A1:A3)", {"A1": 5}) == 1


def test_blank_direct_reference_is_distinct_from_numeric_zero():
    assert evaluate("=A1") is None
    assert evaluate("=A1+2") == 2
    assert evaluate("=COUNT(A1)") == 0
    assert evaluate("=COUNTA(A1)") == 0
    assert evaluate("=COUNT(A1:A2)", {"A2": 0}) == 1
    assert evaluate("=COUNTA(A1:A2)", {"A2": 0}) == 1


def test_qualified_sheet_ranges_and_lookup_vector_validation():
    assert evaluate("=SUM(Data!A1:A2)", {"Data!A1": 2, "Data!A2": 3}) == 5
    value = evaluate("=MATCH(2,A1:B2,0)", {"A1": 1, "B1": 2, "A2": 3, "B2": 4})
    assert isinstance(value, ErrorValue) and value.code == "#VALUE!"


def test_non_aggregate_functions_reject_ranges_explicitly():
    value = evaluate("=LEFT(A1:A2,1)", {"A1": "ab", "A2": "cd"})
    assert isinstance(value, ErrorValue) and value.code == "#VALUE!"


def test_ifna_is_lazy_and_catches_only_na_errors():
    assert evaluate('=IFNA(42,1/0)') == 42
    assert evaluate('=IFNA(#N/A,"fallback")') == "fallback"
    value = evaluate('=IFNA(1/0,"fallback")')
    assert isinstance(value, ErrorValue) and value.code == "#DIV/0!"
    value = evaluate('=IFNA(1,"fallback",3)')
    assert isinstance(value, ErrorValue) and value.code == "#VALUE!"


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        ("=ISBLANK(A1)", True),
        ('=ISBLANK("")', False),
        ("=ISNUMBER(3.5)", True),
        ("=ISNUMBER(TRUE)", False),
        ("=ISNUMBER(#N/A)", False),
        ('=ISTEXT("x")', True),
        ("=ISTEXT(A1)", False),
        ("=ISLOGICAL(FALSE)", True),
        ("=ISLOGICAL(0)", False),
    ],
)
def test_excel_type_predicates(formula, expected):
    assert evaluate(formula) is expected


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        ('=UPPER("MiXeD")', "MIXED"),
        ('=LOWER("MiXeD")', "mixed"),
        ('=TRIM("  alpha   beta  ")', "alpha beta"),
        ('=SUBSTITUTE("banana","an","X")', "bXXa"),
        ('=SUBSTITUTE("banana","an","X",2)', "banXa"),
        ('=FIND("na","banana")', 3),
        ('=FIND("an","banana",3)', 4),
        ('=SEARCH("*","a*b")', 2),
        ('=SEARCH("AN","banana")', 2),
        ('=SEARCH("na","banana",4)', 5),
        ('=TEXTJOIN(",",TRUE,A1:A3)', "a"),
        ('=TEXTJOIN(",",FALSE,A1:A3)', "a,,"),
        ('=TEXTJOIN("-",TRUE,"a","","b")', "a-b"),
    ],
)
def test_text_and_search_functions(formula, expected):
    assert evaluate(formula, {"A1": "a", "A3": ""}) == expected


@pytest.mark.parametrize(
    ("formula", "code"),
    [
        ('=IFNA("x")', "#VALUE!"),
        ('=UPPER(A1:A2)', "#VALUE!"),
        ('=SUBSTITUTE("abc","a","x",0)', "#VALUE!"),
        ('=SUBSTITUTE("abc","","x")', "#VALUE!"),
        ('=FIND("z","abc")', "#VALUE!"),
        ('=FIND("A","banana")', "#VALUE!"),
        ('=FIND("a","abc",0)', "#VALUE!"),
        ('=SEARCH("a","abc",5)', "#VALUE!"),
        ('=TEXTJOIN(",",TRUE)', "#VALUE!"),
        ('=TEXTJOIN(",","yes",A1)', "#VALUE!"),
    ],
)
def test_text_and_ifna_functions_return_explicit_errors(formula, code):
    value = evaluate(formula, {"A1": "a", "A2": "b"})
    assert isinstance(value, ErrorValue)
    assert value.code == code


def test_textjoin_limit_and_error_propagation():
    value = evaluate('=TEXTJOIN("",TRUE,A1)', {"A1": "x" * 32768})
    assert isinstance(value, ErrorValue) and value.code == "#VALUE!"
    value = evaluate('=TEXTJOIN(",",TRUE,#N/A)')
    assert isinstance(value, ErrorValue) and value.code == "#N/A"


def test_string_builders_check_utf16_limit_before_join_or_replace(monkeypatch):
    monkeypatch.setattr("formula_atlas.MAX_TEXT_LENGTH_UNITS", 8)
    cases = [
        ('=A1&A1', {"A1": "12345"}),
        ('=CONCAT(A1:A2)', {"A1": "12345", "A2": "67890"}),
        ('=TEXTJOIN(",",FALSE,A1:A2)', {"A1": "12345", "A2": "67890"}),
        ('=SUBSTITUTE(A1,"a",B1)', {"A1": "aaaa", "B1": "123"}),
    ]
    for formula, cells in cases:
        value = evaluate(formula, cells)
        assert isinstance(value, ErrorValue) and value.code == "#VALUE!", formula


def test_formula_text_limit_counts_utf16_code_units():
    formula = '=\"' + "😀" * 16_384 + '\"'
    value = evaluate(formula)
    assert isinstance(value, ErrorValue) and value.code == "#VALUE!"


def test_xlookup_exact_forward_reverse_and_lazy_fallback():
    cells = {"A1": 7, "A2": 4, "A3": 7, "B1": "first", "B2": "middle", "B3": "last"}
    assert evaluate('=XLOOKUP(7,A1:A3,B1:B3)', cells) == "first"
    assert evaluate('=XLOOKUP(7,A1:A3,B1:B3,"missing",0,-1)', cells) == "last"
    assert evaluate('=XLOOKUP("ALPHA",C1:C2,D1:D2)', {"C1": "alpha", "C2": "beta", "D1": 1, "D2": 2}) == 1
    assert evaluate('=XLOOKUP(7,A1:A3,B1:B3,1/0)', cells) == "first"
    value = evaluate('=XLOOKUP(99,A1:A3,B1:B3,1/0)', cells)
    assert isinstance(value, ErrorValue) and value.code == "#DIV/0!"
    value = evaluate("=XLOOKUP(99,A1:A3,B1:B3)", cells)
    assert isinstance(value, ErrorValue) and value.code == "#N/A"


def test_xlookup_and_xmatch_validate_vectors_and_exact_modes():
    cases = [
        ("=XLOOKUP(1,A1:B2,C1:C4)", "#VALUE!"),
        ("=XLOOKUP(1,A1:A2,B1:B3)", "#VALUE!"),
        ("=XLOOKUP(1,A1:A2,B1:B2,\"missing\",1)", "#VALUE!"),
        ("=XLOOKUP(1,A1:A2,B1:B2,\"missing\",0,2)", "#VALUE!"),
        ("=XLOOKUP(A1:A2,A1:A2,B1:B2)", "#VALUE!"),
        ("=XLOOKUP(99,A1:A2,B1:B2,C1:C2)", "#VALUE!"),
        ("=XLOOKUP(1,A1:A2,B1:B2)", "#N/A"),
        ("=XMATCH(1,A1:B2)", "#VALUE!"),
        ("=XMATCH(A1:A2,A1:A2)", "#VALUE!"),
        ("=XMATCH(1,A1:A2,1)", "#VALUE!"),
        ("=XMATCH(1,A1:A2,0,0)", "#VALUE!"),
        ("=XMATCH(1,A1:A2)", "#N/A"),
    ]
    cells = {"A1": 2, "A2": 3, "B1": 4, "B2": 5, "C1": 6, "C2": 7, "C3": 8, "C4": 9}
    for formula, code in cases:
        value = evaluate(formula, cells)
        assert isinstance(value, ErrorValue) and value.code == code, formula


def test_xmatch_reverse_returns_one_based_index():
    assert evaluate('=XMATCH("x",A1:A3,0,-1)', {"A1": "x", "A2": "y", "A3": "x"}) == 3


@pytest.mark.parametrize(
    "formula",
    [
        "=XMATCH(1/0,A1:A2)",
        '=XLOOKUP(1/0,A1:A2,B1:B2,"fallback")',
    ],
)
def test_lookup_functions_propagate_lookup_value_errors(formula):
    value = evaluate(formula, {"A1": 1, "A2": 2, "B1": "a", "B2": "b"})
    assert isinstance(value, ErrorValue)
    assert value.code == "#DIV/0!"


def test_textjoin_rejects_more_than_254_total_arguments():
    in_limit = '=TEXTJOIN("",TRUE,' + ",".join('"x"' for _ in range(252)) + ")"
    over_limit = '=TEXTJOIN("",TRUE,' + ",".join('"x"' for _ in range(253)) + ")"
    assert evaluate(in_limit) == "x" * 252
    value = evaluate(over_limit)
    assert isinstance(value, ErrorValue) and value.code == "#VALUE!"


def test_parser_and_evaluator_bound_nesting_without_rejecting_flat_chains():
    nested_64 = "=ABS(" + "ABS(" * 63 + "1" + ")" * 64
    nested_65 = "=ABS(" + "ABS(" * 64 + "1" + ")" * 65
    assert evaluate(nested_64) == 1
    assert evaluate(nested_65).code == "#VALUE!"

    grouped_over_profile = "(" * 97 + "1" + ")" * 97
    assert evaluate(grouped_over_profile).code == "#VALUE!"

    long_sum = "=1" + "+1" * 1_000
    assert evaluate(long_sum) == 1_001
    assert evaluate("=" + "+" * 4_096 + "1") == 1
    assert evaluate("=1" + "%" * 1_000) == 0


def test_formula_length_limit_is_checked_before_tokenizing():
    result = evaluate("1" * 8_193)
    assert isinstance(result, ErrorValue) and result.code == "#VALUE!"


def test_wildcard_matching_stops_at_the_formula_work_budget():
    pattern = "a*" * 2_000
    result = evaluate(f'=COUNTIF(A1,"{pattern}")', {"A1": "a" * 2_000})
    assert isinstance(result, ErrorValue) and result.code == "#VALUE!"


def test_excel_1900_date_serial_compatibility():
    assert evaluate("=DATE(1900,1,1)") == 1
    assert evaluate("=DATE(1900,2,28)") == 59
    assert evaluate("=DATE(1900,2,29)") == 60
    assert evaluate("=DATE(1900,3,1)") == 61
    assert evaluate("=DATE(1900,1,60)") == 60
    assert evaluate("=DAY(59)") == 28
    assert evaluate("=DAY(60)") == 29
    assert evaluate("=DAY(61)") == 1
    assert evaluate("=YEAR(59)") == 1900
    assert evaluate("=YEAR(60)") == 1900
    assert evaluate("=YEAR(61)") == 1900
    assert evaluate("=DATE(0,1,1)") == 1


def test_countif_supports_operators_wildcards_and_blanks():
    cells = {
        "A1": "alpha", "A2": "beta", "A3": "alphabet", "A4": "",
        "A6": "a*", "A7": "a?", "B1": 10, "B2": 20, "B3": 30,
        "B4": 40, "B5": "skip", "B6": 60, "B7": False,
    }
    assert evaluate('=COUNTIF(B1:B7,">=30")', cells) == 3
    assert evaluate('=COUNTIF(A1:A7,"a*")', cells) == 4
    assert evaluate('=COUNTIF(A1:A7,"a?")', cells) == 2
    assert evaluate('=COUNTIF(A1:A7,"a~*")', cells) == 1
    assert evaluate('=COUNTIF(A1:A7,"~?")', cells) == 0
    assert evaluate('=COUNTIF(A1:A7,"")', cells) == 2
    assert evaluate('=COUNTIF(A1:A7,"<>")', cells) == 5
    assert evaluate('=COUNTIF(A1:A7,"ALPHA")', cells) == 1


def test_sumif_and_averageif_defaults_and_optional_ranges():
    cells = {
        "A1": "east", "A2": "west", "A3": "east", "A4": "east",
        "A5": "west", "A6": "east", "A7": "west",
        "B1": 10, "B2": 20, "B3": 30, "B4": 40, "B5": "skip", "B6": 60, "B7": False,
    }
    assert evaluate('=SUMIF(A1:A7,"east",B1:B7)', cells) == 140
    assert evaluate('=SUMIF(B1:B7,">=30")', cells) == 130
    assert evaluate('=AVERAGEIF(A1:A7,"east",B1:B7)', cells) == 35
    assert evaluate('=AVERAGEIF(A1:A7,"west",B1:B7)', cells) == 20
    value = evaluate('=AVERAGEIF(A1:A7,"west",A1:A7)', cells)
    assert isinstance(value, ErrorValue) and value.code == "#DIV/0!"


def test_countifs_sumifs_and_averageifs_share_multi_criteria():
    cells = {
        "A1": "east", "A2": "west", "A3": "east", "A4": "east",
        "A5": "west", "A6": "east", "A7": "west",
        "B1": 10, "B2": 20, "B3": 30, "B4": 40, "B5": "skip", "B6": 60, "B7": False,
    }
    assert evaluate('=COUNTIFS(A1:A7,"east",B1:B7,">=30")', cells) == 3
    assert evaluate('=SUMIFS(B1:B7,A1:A7,"east",B1:B7,">20")', cells) == 130
    assert evaluate('=AVERAGEIFS(B1:B7,A1:A7,"east",B1:B7,">20")', cells) == 130 / 3
    value = evaluate('=AVERAGEIFS(A1:A7,A1:A7,"east")', cells)
    assert isinstance(value, ErrorValue) and value.code == "#DIV/0!"


def test_countblank_and_ifs_handle_error_cells_by_range_role():
    cells = {
        "A1": "skip",
        "A2": "match",
        "B1": ErrorValue("#N/A"),
        "B2": 7,
    }
    assert evaluate("=COUNTBLANK(A1:B2)", cells) == 0
    assert evaluate('=MINIFS(B1:B2,A1:A2,"match")', cells) == 7
    value = evaluate('=MINIFS(B1:B2,A1:A2,"skip")', cells)
    assert isinstance(value, ErrorValue) and value.code == "#N/A"


@pytest.mark.parametrize(
    ("formula", "code"),
    [
        ('=COUNTIF(A1:A2,">>5")', "#VALUE!"),
        ('=COUNTIF(A1:A2,A1:A2)', "#VALUE!"),
        ('=SUMIF(A1:A2,"*",B1:B3)', "#VALUE!"),
        ('=AVERAGEIF(A1:A2,"*",B1:B3)', "#VALUE!"),
        ('=COUNTIFS(A1:A2,"*",B1:B3,">0")', "#VALUE!"),
        ('=SUMIFS(B1:B3,A1:A2,"*")', "#VALUE!"),
        ('=AVERAGEIFS(B1:B3,A1:A2,"*")', "#VALUE!"),
        ('=COUNTIFS(A1:A2,"x",B1:B2)', "#VALUE!"),
        ('=SUMIFS(B1:B2,A1:A2)', "#VALUE!"),
    ],
)
def test_criteria_functions_reject_malformed_criteria_and_ranges(formula, code):
    value = evaluate(formula, {"A1": "x", "A2": "y", "B1": 1, "B2": 2, "B3": 3})
    assert isinstance(value, ErrorValue)
    assert value.code == code


def test_postfix_percent_operator_precedence_and_errors():
    assert evaluate("=50%") == 0.5
    assert evaluate("=50%*2") == 1
    assert evaluate("=50%^2") == 0.25
    assert evaluate("=-50%") == -0.5
    assert evaluate("=50%%") == 0.005
    value = evaluate("=#N/A%")
    assert isinstance(value, ErrorValue) and value.code == "#N/A"


def test_match_and_exact_vlookup_are_case_insensitive_and_support_wildcards():
    cells = {
        "A1": "Alpha", "A2": "beta", "A3": "literal*",
        "B1": 11, "B2": 22, "B3": 33,
    }
    assert evaluate('=MATCH("ALPHA",A1:A3,0)', cells) == 1
    assert evaluate('=MATCH("b?ta",A1:A3,0)', cells) == 2
    assert evaluate('=MATCH("literal~*",A1:A3,0)', cells) == 3
    assert evaluate('=VLOOKUP("ALPHA",A1:B3,2,FALSE)', cells) == 11
    assert evaluate('=VLOOKUP("b?ta",A1:B3,2,FALSE)', cells) == 22
    assert evaluate('=VLOOKUP("literal~*",A1:B3,2,FALSE)', cells) == 33


def test_quoted_worksheet_names_and_qualified_ranges():
    cells = {
        "Sales Data!A1": 10,
        "Sales Data!A2": 20,
        "O'Brien!A1": 5,
    }
    assert evaluate("='Sales Data'!A1+2", cells) == 12
    assert evaluate("=SUM('Sales Data'!A1:A2)", cells) == 30
    assert evaluate("='O''Brien'!A1+1", cells) == 6
    assert evaluate("=A1+1", cells, sheet_name="O'Brien") == 6


def test_index_area_num_is_explicit_for_single_area_references():
    cells = {"A1": "a", "A2": "b"}
    assert evaluate("=INDEX(A1:A2,2,1,1)", cells) == "b"
    value = evaluate("=INDEX(A1:A2,1,1,2)", cells)
    assert isinstance(value, ErrorValue) and value.code == "#REF!"
    value = evaluate("=INDEX(A1:A2,1,1,0)", cells)
    assert isinstance(value, ErrorValue) and value.code == "#VALUE!"


def test_sort_key_errors_follow_source_order_for_direct_nonfinite_inputs():
    mixed_before_nonfinite = evaluate_result(
        "=SORT(A1:A3)", {"A1": 1, "A2": "x", "A3": math.inf}
    )
    assert isinstance(mixed_before_nonfinite, ErrorValue)
    assert mixed_before_nonfinite.code == "#VALUE!"

    nonfinite_before_mixed = evaluate_result(
        "=SORT(A1:A3)", {"A1": math.inf, "A2": "x", "A3": 1}
    )
    assert isinstance(nonfinite_before_mixed, ErrorValue)
    assert nonfinite_before_mixed.code == "#NUM!"


def test_sort_and_unique_numeric_payloads_use_binary64():
    cells = {"A1": 9007199254740993, "A2": 9007199254740992}
    sorted_result = evaluate_result("=SORT(A1:A2)", cells)
    assert isinstance(sorted_result, ArrayValue)
    assert sorted_result.rows == (
        (9007199254740992.0,),
        (9007199254740992.0,),
    )

    unique_result = evaluate_result("=UNIQUE(A1:A2)", cells)
    assert isinstance(unique_result, ArrayValue)
    assert unique_result.rows == ((9007199254740992.0,),)

    nonfinite_payload = evaluate_result(
        "=SORT(A1:B2)",
        {"A1": 1, "B1": math.inf, "A2": 2, "B2": "x"},
    )
    assert isinstance(nonfinite_payload, ErrorValue)
    assert nonfinite_payload.code == "#NUM!"
