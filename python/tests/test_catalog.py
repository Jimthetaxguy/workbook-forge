import json
from pathlib import Path

from workbook_forge.catalog import (
    PERMISSIVE_LICENSES,
    function_status,
    implementation_patterns,
    inventory_metadata,
    list_functions,
    lookup_function,
)


def test_official_inventory_is_versioned_and_source_linked():
    metadata = inventory_metadata()
    assert metadata["profile"] == "excel-microsoft-365-desktop"
    assert metadata["refresh_required"] is True
    assert len(list_functions()) == 521
    assert lookup_function("xlookup")["category"] == "Lookup and reference"


def test_catalog_presence_is_not_evaluator_support():
    assert function_status("SUM", "python") == "conformance-tested"
    assert function_status("VLOOKUP", "rust") == "conformance-tested"
    assert function_status("TEXTJOIN", "python") == "conformance-tested"
    assert function_status("IFNA", "rust") == "conformance-tested"
    assert function_status("XMATCH", "python") == "conformance-tested"
    assert function_status("XLOOKUP", "rust") == "conformance-tested"
    assert function_status("CONCAT", "python") == "conformance-tested"
    assert function_status("COUNTBLANK", "rust") == "conformance-tested"
    assert function_status("MINIFS", "python") == "conformance-tested"
    assert function_status("MAXIFS", "rust") == "conformance-tested"
    assert function_status("TEXTBEFORE", "python") == "conformance-tested"
    assert function_status("TEXTAFTER", "rust") == "conformance-tested"
    assert function_status("ISNA", "python") == "conformance-tested"
    assert function_status("ISERR", "rust") == "conformance-tested"
    assert function_status("ISERROR", "python") == "conformance-tested"
    assert function_status("NA", "rust") == "conformance-tested"
    assert function_status("VSTACK", "rust") == "catalogued"


def test_filter_glossary_records_bounded_implementation_and_sources():
    assert function_status("FILTER", "python") == "conformance-tested"
    assert function_status("FILTER", "rust") == "conformance-tested"
    spec = lookup_function("FILTER")["semantic_spec"]
    assert "ms-filter" in spec["source_refs"]
    assert "ms-array-formula-examples" in spec["source_refs"]
    assert "one column" in spec["semantics"]["shape_profile"]
    assert "blank and empty text" in spec["semantics"]["include_coercion_profile"]
    assert "array fallback shape are Formula Atlas profiles" in spec["semantics"]["empty_result_profile"]
    assert "100000 cells" in spec["semantics"]["allocation_profile"]
    assert "No direct Excel spot-checks" in spec["semantics"]["compatibility_limit"]


def test_conditional_aggregation_glossary_has_behavior_and_provenance():
    entry = lookup_function("countifs")
    assert function_status("COUNTIFS", "python") == "conformance-tested"
    assert function_status("AVERAGEIFS", "rust") == "conformance-tested"
    spec = entry["semantic_spec"]
    assert spec["returns"] == {"kind": "number", "shape": "scalar"}
    assert spec["semantics"]["criteria"]["text_comparison"] == "case-insensitive"
    assert spec["semantics"]["criteria"]["wildcards"]["~"] == "escapes *, ?, or ~"
    assert "ms-count-criteria" in spec["source_refs"]


def test_date_semantics_record_excel_serial_boundaries():
    date = lookup_function("DATE")["semantic_spec"]
    assert "0 through 1899 are offset by 1900" in date["semantics"]["year_rules"]
    assert "serial 60" in date["semantics"]["serial_60"]
    assert lookup_function("DAY")["semantic_spec"]["semantics"]["serial_zero"].startswith(
        "returns 31"
    )


def test_ifs_and_switch_glossary_records_profile_choices():
    ifs = lookup_function("IFS")["semantic_spec"]
    switch = lookup_function("SWITCH")["semantic_spec"]
    assert ifs["arguments"][1]["empty_allowed"] is True
    assert "not specified by Microsoft" in ifs["semantics"]["evaluation_profile"]
    assert switch["arguments"][2]["empty_allowed"] is True
    assert switch["arguments"][-1]["empty_allowed"] is True
    assert "ASCII text compares without case" in switch["semantics"]["comparison_profile"]
    assert "non-ASCII text compares exactly" in switch["semantics"]["comparison_profile"]
    assert "not been spot-checked in Excel" in switch["semantics"]["evaluation_profile"]


def test_time_function_glossary_records_source_facts_and_profile_gaps():
    expected_sources = {
        "HOUR": "ms-hour",
        "MINUTE": "ms-minute",
        "SECOND": "ms-second",
        "TIME": "ms-time",
    }
    for name, source_id in expected_sources.items():
        entry = lookup_function(name)
        assert function_status(name, "python") == "conformance-tested"
        assert function_status(name, "rust") == "conformance-tested"
        assert source_id in entry["semantic_spec"]["source_refs"]

    hour = lookup_function("HOUR")["semantic_spec"]
    assert "0 through 23" in hour["semantics"]["result"]
    assert "not parsed here" in hour["semantics"]["text_time_parsing"]
    assert "not been spot-checked in Excel" in hour["semantics"]["whole_second_profile"]
    assert "unverified profile rule" in hour["semantics"]["serial_range"]

    minute = lookup_function("MINUTE")["semantic_spec"]
    second = lookup_function("SECOND")["semantic_spec"]
    assert "0 through 59" in minute["semantics"]["result"]
    assert "0 through 59" in second["semantics"]["result"]

    time = lookup_function("TIME")["semantic_spec"]
    assert time["semantics"]["component_range"].startswith("each coerced component")
    assert "wrap modulo one day" in time["semantics"]["overflow"]
    assert "not been spot-checked in Excel" in time["semantics"]["fractional_components"]


def test_week_function_glossary_records_selectors_and_date_profiles():
    expected_sources = {
        "WEEKDAY": "ms-weekday",
        "WEEKNUM": "ms-weeknum",
        "ISOWEEKNUM": "ms-isoweeknum",
    }
    for name, source_id in expected_sources.items():
        entry = lookup_function(name)
        assert function_status(name, "python") == "conformance-tested"
        assert function_status(name, "rust") == "conformance-tested"
        assert source_id in entry["semantic_spec"]["source_refs"]

    weekday = lookup_function("WEEKDAY")["semantic_spec"]
    assert weekday["semantics"]["valid_return_types"] == [1, 2, 3, 11, 12, 13, 14, 15, 16, 17]
    assert "before March 1, 1900" in weekday["semantics"]["historical_weekday"]
    assert "not been spot-checked in Excel" in weekday["semantics"]["return_type_fractional_profile"]
    assert "Serial 60" in weekday["semantics"]["serial_60"]

    weeknum = lookup_function("WEEKNUM")["semantic_spec"]
    assert weeknum["semantics"]["valid_return_types"] == [1, 2, 11, 12, 13, 14, 15, 16, 17, 21]
    assert "first Thursday" in weeknum["semantics"]["system_2"]
    assert "Serial 60" in weeknum["semantics"]["serial_60_and_1900_boundary"]

    iso = lookup_function("ISOWEEKNUM")["semantic_spec"]
    assert "first Thursday" in iso["semantics"]["week_rule"]
    assert "not been spot-checked in Excel" in iso["semantics"]["serial_60_and_1900_boundary"]


def test_business_day_glossary_records_source_facts_and_unverified_profiles():
    for name, source_id in {
        "WORKDAY": "ms-workday",
        "NETWORKDAYS": "ms-networkdays",
    }.items():
        entry = lookup_function(name)
        assert function_status(name, "python") == "conformance-tested"
        assert function_status(name, "rust") == "conformance-tested"
        assert source_id in entry["semantic_spec"]["source_refs"]

    workday = lookup_function("WORKDAY")["semantic_spec"]
    assert "truncation of noninteger days" in workday["semantics"]["offset"]
    assert "has not been spot-checked in Excel" in workday["semantics"]["zero_offset_profile"]
    assert "array constants are outside" in workday["semantics"]["holidays"]
    assert "non-finite date expression returns #NUM!" in workday["semantics"]["nonfinite_date_profile"]
    assert "binary64 numeric domain" in workday["semantics"]["nonfinite_date_profile"]

    networkdays = lookup_function("NETWORKDAYS")["semantic_spec"]
    assert "inclusively" in networkdays["semantics"]["interval"]
    assert "does not specify reversed-range sign" in networkdays["semantics"]["reversed_range_profile"]
    assert "not been spot-checked in Excel" in networkdays["semantics"]["holiday_cells_profile"]
    assert "non-finite date expression returns #NUM!" in networkdays["semantics"]["nonfinite_date_profile"]


def test_custom_weekend_glossary_separates_documented_facts_and_profiles():
    for name, source_id in {
        "WORKDAY.INTL": "ms-workday-intl",
        "NETWORKDAYS.INTL": "ms-networkdays-intl",
    }.items():
        entry = lookup_function(name)
        assert function_status(name, "python") == "conformance-tested"
        assert function_status(name, "rust") == "conformance-tested"
        assert entry["implementation"]["arity"] == {"min": 2, "max": 4}
        assert source_id in entry["semantic_spec"]["source_refs"]
        assert "not been spot-checked in Excel" in entry["semantic_spec"]["semantics"]["nonfinite_date_profile"]

    networkdays = lookup_function("NETWORKDAYS.INTL")["semantic_spec"]
    assert "1-7" in networkdays["semantics"]["weekend_codes"]
    assert "11-17" in networkdays["semantics"]["weekend_codes"]
    assert "negative counts" in networkdays["semantics"]["interval"]
    assert "all-one mask returns zero" in networkdays["semantics"]["weekend_mask"]
    assert "Microsoft does not specify" in networkdays["semantics"]["invalid_numeric_weekend_profile"]
    assert "does not specify this holiday case" in networkdays["semantics"]["holiday_range_profile"]
    assert "prose says 22" in networkdays["semantics"]["microsoft_example_note"]

    workday = lookup_function("WORKDAY.INTL")["semantic_spec"]
    assert "truncation of noninteger days" in workday["semantics"]["offset"]
    assert "all-one mask is invalid" in workday["semantics"]["weekend_mask"]
    assert "does not state its exact error code" in workday["semantics"]["all_weekend_error_profile"]
    assert "array constants are unsupported" in workday["semantics"]["holidays"]


def test_support_catalog_points_to_checked_in_schema():
    schema = Path(__file__).parents[2] / "catalog" / "formula-support.schema.json"
    assert schema.is_file()


def test_open_source_pattern_catalog_enforces_license_and_provenance_policy():
    root = Path(__file__).parents[2]
    patterns = implementation_patterns()
    schema = json.loads((root / "catalog" / "open-source-patterns.schema.json").read_text())
    assert set(patterns["eligibility_policy"]["allowed_licenses"]) == PERMISSIVE_LICENSES
    assert set(schema["$defs"]["permissiveLicense"]["enum"]) == PERMISSIVE_LICENSES
    assert schema["properties"]["eligibility_policy"]["properties"]["code_copied"] == {"const": False}
    source_ids = {source["id"] for source in patterns["sources"]}
    assert len(source_ids) == len(patterns["sources"])
    for source in patterns["sources"]:
        assert source["licenses"]
        assert set(source["licenses"]) <= PERMISSIVE_LICENSES
        assert source["license_evidence_url"].startswith("https://")
    for pattern in patterns["patterns"]:
        assert set(pattern["source_refs"]) <= source_ids
        assert pattern["code_copied"] is False


def test_blank_and_conditional_extrema_glossary_records_edge_behavior():
    blank = lookup_function("COUNTBLANK")["semantic_spec"]
    assert "empty text" in blank["semantics"]["blank_values"]
    assert "zero" in blank["semantics"]["excluded_values"]

    minimum = lookup_function("MINIFS")["semantic_spec"]
    assert minimum["semantics"]["criteria_pair_limit"] == {
        "required_pairs": 1,
        "additional_pairs": 126,
        "total_pairs": 127,
        "maximum_arguments": 255,
    }
    assert minimum["semantics"]["no_numeric_matches"] == "returns 0"
    assert "ms-minifs" in minimum["source_refs"]


def test_text_delimiter_glossary_records_optional_argument_semantics():
    before = lookup_function("TEXTBEFORE")["semantic_spec"]
    after = lookup_function("TEXTAFTER")["semantic_spec"]
    assert before["arguments"][-1]["default"] == "#N/A"
    assert before["semantics"]["match_mode"]["default"] == 0
    assert before["semantics"]["instance_num"]["negative"] == "searches from the end"
    assert after["semantics"]["empty_delimiter"]["positive_instance"] == "returns the entire text"
    assert "ms-textbefore-textafter" in before["source_refs"]


def test_error_predicate_glossary_distinguishes_na_from_other_errors():
    isna = lookup_function("ISNA")["semantic_spec"]
    iserr = lookup_function("ISERR")["semantic_spec"]
    iserror = lookup_function("ISERROR")["semantic_spec"]
    na = lookup_function("NA")["semantic_spec"]
    assert isna["semantics"]["matches"] == "#N/A only"
    assert iserr["semantics"]["excludes"] == "#N/A"
    assert iserror["semantics"]["matches"] == "all supported Excel error values"
    assert na["returns"] == {"kind": "error", "shape": "scalar"}


def test_day_count_glossary_separates_documented_rules_from_profiles():
    days360 = lookup_function("DAYS360")
    yearfrac = lookup_function("YEARFRAC")
    for entry, source_id in [(days360, "ms-days360"), (yearfrac, "ms-yearfrac")]:
        assert function_status(entry["name"], "python") == "conformance-tested"
        assert function_status(entry["name"], "rust") == "conformance-tested"
        assert entry["implementation"]["arity"] == {"min": 2, "max": 3}
        assert source_id in entry["semantic_spec"]["source_refs"]

    days_semantics = days360["semantic_spec"]["semantics"]
    assert "negative day count" in days_semantics["direction"]
    assert "numeric text" in days_semantics["method_profile"]
    assert "blank cell" in days_semantics["method_profile"]
    assert "Formula errors propagate" in days_semantics["method_profile"]
    assert "not been spot-checked" in days_semantics["method_profile"]
    assert "truncated toward zero" in days_semantics["fractional_serial_profile"]

    year_semantics = yearfrac["semantic_spec"]["semantics"]
    assert year_semantics["basis_mapping"]["1"] == "Actual/Actual"
    assert "truncated to integers" in year_semantics["argument_truncation"]
    assert "does not specify this multi-year algorithm" in year_semantics["actual_actual_profile"]
    assert "does not specify the sign behavior" in year_semantics["signed_reverse_profile"]
    assert "may return an incorrect result" in year_semantics["february_warning"]
