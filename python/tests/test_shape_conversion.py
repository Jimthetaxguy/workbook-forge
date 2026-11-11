"""Shape conversion edges between the canonical and toolkit workbook shapes.

Every expected document and Conversion list comes from
fixtures/shape-conversion-cases.v1.json, written by hand from spec 09 step 0.
The conversions never judge a document themselves: the toolkit reader judges
a toolkit document and the canonical hydrator judges a canonical one.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import math
from pathlib import Path
import re
import shutil

import jsonschema
import pytest

from workbook_forge import python_engine
from workbook_forge.canonical_xlsx import CanonicalPackageError
from workbook_forge.convert import ConversionError, to_canonical, to_toolkit
from workbook_forge.intake import intake_workbook_model
from workbook_forge.model import Workbook, hydrate
from workbook_forge.workbook import WorkbookError
from workbook_forge.xlsx import import_xlsx

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads(
    (ROOT / "fixtures" / "shape-conversion-cases.v1.json").read_text(encoding="utf-8")
)
OPERATING_SCENARIO = ROOT / "fixtures" / "operating-scenario.workbook.json"
SCHEMA = json.loads(
    (ROOT / "schemas" / "workbook-model.v1.schema.json").read_text(encoding="utf-8")
)


def _params(cases: list[dict]) -> list:
    return [pytest.param(case, id=case["name"]) for case in cases]


def _refusals(direction: str) -> list:
    return _params([case for case in CASES["refusals"] if case["direction"] == direction])


def _text(document: dict) -> str:
    # Python reads 20 == 20.0 and True == 1 as equal; the JSON text does not,
    # so this is how the number representation of a shape is pinned.
    return json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _scenario() -> Workbook:
    return hydrate(OPERATING_SCENARIO.read_bytes())


def _all_number(workbook: Workbook) -> dict[str, str]:
    return {name: "number" for name in workbook.bindings.inputs}


def _toolkit_document(cells: dict, **extra) -> dict:
    return {
        "schema_version": 1,
        "sheets": [{"id": "s", "name": "S", "cells": cells}],
        "inputs": {},
        "outputs": {},
        **extra,
    }


# --- to_toolkit -------------------------------------------------------------


@pytest.mark.parametrize("case", _params(CASES["canonical_cases"]))
def test_to_toolkit_matches_fixture_cases(case):
    workbook = hydrate(json.dumps(case["canonical"]))
    document, conversion = to_toolkit(workbook, input_kinds=case["input_kinds"])
    assert _text(document) == _text(case["toolkit"])
    assert conversion.to_dict() == case["conversion"]


@pytest.mark.parametrize("case", _refusals("to_toolkit"))
def test_to_toolkit_refusals(case):
    workbook = hydrate(json.dumps(case["document"]))
    with pytest.raises(ConversionError) as info:
        to_toolkit(workbook, input_kinds=case["input_kinds"])
    assert info.value.code == case["code"]
    assert str(info.value) == f"{info.value.code}: {info.value.message}"


def test_to_toolkit_refuses_text_the_toolkit_cannot_hold():
    # The toolkit caps cell text at 32767 UTF-16 units; the canonical reader
    # has no such cap, so the conversion must hand the refusal on unchanged.
    document = {
        "schema_version": 1,
        "model_version": 1,
        "metadata": {},
        "sheets": [
            {
                "name": "S",
                "cells": {"A1": {"address": "A1", "value": "x" * 32_768, "data_type": "text"}},
            }
        ],
    }
    with pytest.raises(ConversionError) as info:
        to_toolkit(hydrate(json.dumps(document)))
    assert info.value.code == "resource_limit"


@pytest.mark.parametrize(
    "workbook, input_kinds",
    [
        pytest.param(hydrate(json.dumps(case["canonical"])), case["input_kinds"], id=case["name"])
        for case in CASES["canonical_cases"]
    ]
    + [pytest.param(_scenario(), _all_number(_scenario()), id="operating-scenario")],
)
def test_to_toolkit_output_is_accepted_by_the_python_engine(workbook, input_kinds):
    document, _conversion = to_toolkit(workbook, input_kinds=input_kinds)
    session = python_engine.Session(json.dumps(document))
    assert json.loads(session.snapshot()) == document


# --- to_canonical -----------------------------------------------------------


@pytest.mark.parametrize("case", _params(CASES["toolkit_cases"]))
def test_to_canonical_matches_fixture_cases(case):
    workbook, conversion = to_canonical(case["toolkit"])
    assert _text(workbook.to_dict()) == _text(case["canonical"])
    assert conversion.to_dict() == case["conversion"]


@pytest.mark.parametrize("case", _refusals("to_canonical"))
def test_to_canonical_refusals(case):
    with pytest.raises(ConversionError) as info:
        to_canonical(case["document"])
    assert info.value.code == case["code"]
    assert str(info.value) == f"{info.value.code}: {info.value.message}"


@pytest.mark.parametrize("case", _params(CASES["toolkit_cases"]))
def test_to_canonical_output_validates_against_the_v1_schema(case):
    workbook, _conversion = to_canonical(case["toolkit"])
    jsonschema.validate(json.loads(workbook.to_json()), SCHEMA)


def test_review_focus_na_bang_is_spelled_na():
    # The toolkit accepts both spellings of the error; the canonical reader
    # accepts one. The conversion respells it and lists nothing for it.
    workbook, conversion = to_canonical(_toolkit_document({"A1": {"value": {"error": "#N/A!"}}}))
    cell = workbook.sheets[0].cells["A1"]
    assert cell.value == {"error": "#N/A", "message": None}
    assert cell.data_type == "error"
    assert conversion.dropped == ("revision", "sheets[0].id")


def test_review_focus_unnormalised_toolkit_document_lists_required():
    # Drop lists are relative to the normalised toolkit form: `required` is
    # dropped even though this document never wrote it.
    document = _toolkit_document(
        {"B2": {"value": 2.0}, "A1": {"value": 1.0}},
        inputs={"x": {"sheet": "S", "address": "A1", "kind": "number"}},
    )
    workbook, conversion = to_canonical(document)
    assert "bindings.inputs.x.required" in conversion.dropped
    assert "revision" in conversion.dropped
    assert list(workbook.sheets[0].cells) == ["A1", "B2"]
    assert workbook.sheets[0].dimensions == (1, 2, 1, 2)


def test_to_canonical_without_an_envelope_marks_only_cached_values_imported():
    document = _toolkit_document(
        {
            "A1": {"value": 1.0},
            "A2": {"value": None, "formula": "=A1", "cached_value": 1.0},
            "A3": {"value": None, "formula": "=A1"},
            # A null cache is no cache: the toolkit reader drops it, so the
            # cell is not marked imported and nothing is listed for it.
            "A4": {"value": None, "formula": "=A1", "cached_value": None},
            "A5": {"value": 2.0, "cached_value": None},
        }
    )
    workbook, conversion = to_canonical(document)
    assert workbook.sheets[0].cells["A4"].provenance is None
    assert workbook.sheets[0].cells["A5"].value == 2
    listed = conversion.dropped + conversion.added
    assert not any(path.endswith(("A4.provenance", "A5.cached_value")) for path in listed)
    # A toolkit document does not say whether it was authored or imported, so
    # no workbook provenance is invented for it.
    assert workbook.provenance is None
    assert "provenance" not in conversion.added
    assert "model_version" in conversion.added
    cells = workbook.sheets[0].cells
    assert cells["A1"].provenance is None
    assert cells["A2"].provenance.to_dict() == {"origin": "imported"}
    assert cells["A2"].value == 1 and cells["A2"].formula.result is None
    assert cells["A3"].provenance is None and cells["A3"].value is None
    assert "sheets[0].cells.A2.provenance" in conversion.added
    assert "sheets[0].cells.A3.provenance" not in conversion.added
    assert "metadata.source" not in conversion.added


def test_to_canonical_lists_a_cached_value_on_a_non_formula_cell_as_dropped():
    # import_xlsx moves a blocked cell's value into cached_value; a cell without
    # a formula has no canonical home for it.
    document = _toolkit_document(
        {"A1": {"value": None, "cached_value": 5.0, "blocked_reason": "grouped formula region"}}
    )
    workbook, conversion = to_canonical(document)
    assert workbook.sheets[0].cells["A1"].value is None
    assert conversion.dropped == (
        "revision",
        "sheets[0].cells.A1.blocked_reason",
        "sheets[0].cells.A1.cached_value",
        "sheets[0].id",
    )


def test_to_canonical_derives_dependencies_with_the_canonical_analyzer():
    document = _toolkit_document(
        {
            "A1": {"value": None, "formula": "=SUM(B1:B2)+Other!A1"},
            "A2": {"value": None, "formula": "=NOSUCHFUNCTION(("},
        }
    )
    document["sheets"].append({"id": "o", "name": "Other", "cells": {}})
    workbook, conversion = to_canonical(document)
    cells = workbook.sheets[0].cells
    assert cells["A1"].formula.dependencies == ("Other!A1", "S!B1", "S!B2")
    # A formula the analyzer refuses gets no dependencies and is not refused
    # here; the refusal travels as a canonical diagnostic on that cell, so it
    # is never mistaken for a formula with no references.
    assert cells["A2"].formula.dependencies == ()
    assert [item.to_dict() for item in workbook.diagnostics] == [
        {
            "code": "parse_error",
            "classification": "parse_error",
            "message": "formula could not be parsed",
            "sheet": "S",
            "address": "A2",
        }
    ]
    assert "diagnostics[0]" in conversion.added
    assert workbook.sheets[1].dimensions is None


def test_to_canonical_with_an_envelope_marks_only_baseline_cells_imported(tmp_path):
    # The baseline says which cells the import held and with what; a cell
    # added or changed since is authored, whatever the envelope's sheet parts.
    copy = tmp_path / "op_merged_cells.xlsx"
    shutil.copyfile(CORPUS / "op_merged_cells.xlsx", copy)
    model = import_xlsx(copy)
    imported = model.to_dict()["sheets"][0]
    merged_addresses = {"A1", "B1", "C1", "D1", "A4", "A5", "A6"}
    edited = next(
        address
        for address, cell in imported["cells"].items()
        if "formula" not in cell and address not in merged_addresses
    )
    model.set_value("Report", edited, "changed after import")
    model.set_value("Report", "Z99", 42.0)
    model.set_formula("Report", "Z100", "=Z99*2")
    document = model.to_dict()
    document["sheets"].append(
        {"id": "later", "name": "Later", "cells": {"A1": {"value": 1.0}}, "column_widths": {}}
    )
    workbook, conversion = to_canonical(document, model.envelope)
    assert workbook.provenance.to_dict() == {
        "origin": "imported",
        "source": model.envelope.source.workbook_part,
    }
    report = workbook.sheet("Report").cells
    authored = {edited, "Z99", "Z100"}
    for address, cell in report.items():
        expected = (
            {"origin": "authored"}
            if address in authored
            else {"origin": "imported", "source": "xl/worksheets/sheet1.xml"}
        )
        assert cell.provenance.to_dict() == expected, address
        assert f"sheets[0].cells.{address}.provenance" in conversion.added
    assert workbook.sheet("Later").cells["A1"].provenance.to_dict() == {"origin": "authored"}
    assert "sheets[1].cells.A1.provenance" in conversion.added


def test_to_canonical_integer_rule_is_inclusive_and_keeps_negative_zero_a_float():
    document = _toolkit_document(
        {
            "A1": {"value": -0.0},
            "A2": {"value": float(2**53)},
            "A3": {"value": float(2**53 + 2)},
            "A4": {"value": 3.0},
            "A5": {"value": True},
        }
    )
    workbook, _conversion = to_canonical(document)
    values = {address: cell.value for address, cell in workbook.sheets[0].cells.items()}
    assert isinstance(values["A1"], float) and math.copysign(1.0, values["A1"]) < 0
    assert values["A2"] == 2**53 and isinstance(values["A2"], int)
    assert isinstance(values["A3"], float)
    assert values["A4"] == 3 and isinstance(values["A4"], int)
    assert values["A5"] is True
    document_back, _conversion = to_toolkit(workbook)
    back = document_back["sheets"][0]["cells"]
    assert math.copysign(1.0, back["A1"]["value"]) < 0
    assert back["A4"]["value"] == 3.0 and isinstance(back["A4"]["value"], float)
    # bool is an int in Python; it must stay a boolean in the toolkit shape.
    assert back["A5"]["value"] is True


@pytest.mark.parametrize(
    "field, code",
    [("model_version", "unsupported_model_version"), ("schema_version", "unsupported_schema_version")],
)
def test_to_toolkit_refuses_an_unsupported_version_with_the_model_code(field, code):
    workbook = dataclasses.replace(hydrate(OPERATING_SCENARIO.read_bytes()), **{field: 2})
    with pytest.raises(ConversionError) as info:
        to_toolkit(workbook, input_kinds=_all_number(workbook))
    assert info.value.code == code


# --- round trips ------------------------------------------------------------

_SEGMENT = re.compile(r"([^.\[\]]+)(?:\[(\d+)\])?")


def _without(document: dict, paths: set[str]) -> dict:
    """The document with every listed path deleted.

    A ``diagnostics[i]`` path deletes ``diagnostics``; a missing path is
    ignored. An object left empty is removed afterwards, on both sides of a
    comparison: the normalised toolkit form writes ``column_widths: {}`` where
    an unnormalised document omits it, and a ``style`` emptied by a deletion
    is not written back. Neither shape gives an empty object a meaning.
    """
    result = copy.deepcopy(document)
    for path in paths:
        _delete(result, path)
    return _prune(result)


def _delete(node, path: str) -> None:
    if path.startswith("diagnostics["):
        node.pop("diagnostics", None)
        return
    if path.startswith("bindings.") and "inputs" in node:
        # Paths use the canonical grammar; the toolkit shape keeps its
        # bindings at the root.
        path = path.removeprefix("bindings.")
    segments = _SEGMENT.findall(path)
    for name, index in segments[:-1]:
        if not isinstance(node, dict) or name not in node:
            return
        node = node[name]
        if index:
            if not isinstance(node, list) or int(index) >= len(node):
                return
            node = node[int(index)]
    name, index = segments[-1]
    assert not index, path
    if isinstance(node, dict):
        node.pop(name, None)


def _prune(node):
    if isinstance(node, dict):
        pruned = {key: _prune(value) for key, value in node.items()}
        return {key: value for key, value in pruned.items() if value != {}}
    if isinstance(node, list):
        return [_prune(item) for item in node]
    return node


def _listed(*conversions) -> set[str]:
    return {path for conversion in conversions for path in conversion.dropped + conversion.added}


@pytest.mark.parametrize(
    "workbook, input_kinds",
    [
        pytest.param(hydrate(json.dumps(case["canonical"])), case["input_kinds"], id=case["name"])
        for case in CASES["canonical_cases"]
    ]
    + [pytest.param(_scenario(), _all_number(_scenario()), id="operating-scenario")],
)
def test_canonical_round_trip_restores_everything_not_listed(workbook, input_kinds):
    original = workbook.to_dict()
    document, forward = to_toolkit(workbook, input_kinds=input_kinds)
    back, backward = to_canonical(document)
    listed = _listed(forward, backward)
    assert _without(original, listed) == _without(back.to_dict(), listed)


@pytest.mark.parametrize("case", _params(CASES["toolkit_cases"]))
def test_toolkit_round_trip_restores_everything_not_listed(case):
    original = case["toolkit"]
    workbook, forward = to_canonical(original)
    # The kinds the document already carries are the only typing the round
    # trip may use; binding typing is otherwise not mechanical.
    kinds = {name: binding["kind"] for name, binding in original.get("inputs", {}).items()}
    back, backward = to_toolkit(workbook, input_kinds=kinds)
    listed = _listed(forward, backward)
    assert _one_spelling(_without(original, listed)) == _without(back, listed)


def _one_spelling(node):
    # The toolkit accepts two spellings the canonical shape writes one way:
    # #N/A! for #N/A, and a formula with or without its leading "=" (imported
    # formulas are raw OOXML text, which has none). A round trip comes back in
    # the canonical spelling. Both are representation changes, never listed.
    if isinstance(node, dict):
        if node.get("error") == "#N/A!":
            return {**node, "error": "#N/A"}
        spelled = {key: _one_spelling(value) for key, value in node.items()}
        formula = spelled.get("formula")
        if isinstance(formula, str) and formula and not formula.startswith("="):
            spelled["formula"] = "=" + formula
        return spelled
    if isinstance(node, list):
        return [_one_spelling(item) for item in node]
    return node


# --- the corpus -------------------------------------------------------------

CORPUS = ROOT / "fixtures" / "corpus"
# Toolkit imports the canonical reader refuses: a blocked formula cell carries
# an empty expression, which the canonical shape cannot hold.
_CONVERSION_REFUSALS = {
    "hb_data_table_formula.xlsx",
    "hb_shared_formulas.xlsx",
}
# Toolkit imports that canonical intake refuses. The conversion refusals above
# are included; the rest convert and then fail intake.
_CANONICAL_INTAKE_REFUSALS = {
    "hb_compat_version_3.xlsx",
    "hb_data_table_formula.xlsx",
    "hb_dynamic_array_cached.xlsx",
    "hb_shared_formulas.xlsx",
    "op_1904_dates.xlsx",
    "op_array_formulas.xlsx",
    "xw_1904_dates.xlsx",
    "xw_array_formula.xlsx",
    "xw_dynamic_arrays.xlsx",
}
# Corpus workbooks that pass both the toolkit importer and canonical intake.
_CANONICAL_INTAKE_AGREED = {
    "hb_3d_references.xlsx",
    "hb_absolute_targets.xlsx",
    "hb_bom_part.xlsx",
    "hb_connections_query.xlsx",
    "hb_custom_xml.xlsx",
    "hb_defined_names.xlsx",
    "hb_dropdown_formulas.xlsx",
    "hb_dropdown_rules.xlsx",
    "hb_embedded_ole.xlsx",
    "hb_errors_as_values.xlsx",
    "hb_escaped_control_chars.xlsx",
    "hb_excel_like_markup.xlsx",
    "hb_external_link.xlsx",
    "hb_far_off_cell.xlsx",
    "hb_flatten_has_functions.xlsx",
    "hb_form_controls.xlsx",
    "hb_google_sheets_like.xlsx",
    "hb_huge_shared_strings.xlsx",
    "hb_inline_and_shared_strings.xlsx",
    "hb_inline_strings_rich.xlsx",
    "hb_layout_protection.xlsx",
    "hb_libreoffice_like.xlsx",
    "hb_long_formula.xlsx",
    "hb_many_sheets.xlsx",
    "hb_many_styles.xlsx",
    "hb_minimal_no_optional_parts.xlsx",
    "hb_numbers_like.xlsx",
    "hb_odd_part_name.xlsx",
    "hb_pivot_cache.xlsx",
    "hb_pivot_calculated_field.xlsx",
    "hb_pivot_fields.xlsx",
    "hb_prefixed_spreadsheetml.xlsx",
    "hb_r1c1_looking_names.xlsx",
    "hb_rich_text_phonetic.xlsx",
    "hb_slicers.xlsx",
    "hb_sparklines.xlsx",
    "hb_stale_dimension.xlsx",
    "hb_table_totals_calculated.xlsx",
    "hb_text_columns.xlsx",
    "hb_text_edge_cases.xlsx",
    "hb_threaded_comments.xlsx",
    "hb_timelines.xlsx",
    "hb_volatile_formulas.xlsx",
    "hb_wide_sheet.xlsx",
    "op_budget_shared_strings.xlsx",
    "op_charts.xlsx",
    "op_comments_notes.xlsx",
    "op_conditional_formatting.xlsx",
    "op_defined_names.xlsx",
    "op_dropdowns.xlsx",
    "op_empty_sheets.xlsx",
    "op_errors_booleans.xlsx",
    "op_freeze_filter_print.xlsx",
    "op_hidden_sheets.xlsx",
    "op_hyperlinks.xlsx",
    "op_large_60k.xlsx",
    "op_merged_cells.xlsx",
    "op_rich_text.xlsx",
    "op_sheet_name_quoting.xlsx",
    "op_sparse_far_cell.xlsx",
    "op_styles_number_formats.xlsx",
    "op_table_structured_refs.xlsx",
    "op_text_cells.xlsx",
    "xw_cached_values.xlsx",
    "xw_chart_image.xlsx",
    "xw_chartsheet.xlsx",
    "xw_comments.xlsx",
    "xw_conditional_ext.xlsx",
    "xw_dropdowns.xlsx",
    "xw_hyperlinks_names.xlsx",
    "xw_inline_strings.xlsx",
    "xw_table_totals.xlsx",
    "xw_text_formats.xlsx",
}
# The path grammar of the plan's shared definitions: every listed path must
# be one of these and nothing else.
_PATH = re.compile(
    r"^(?:model_version|revision|provenance|metadata\..+|diagnostics\[\d+\]"
    r"|bindings\.inputs\.[^.]+(?:\.(?:kind|required|min|max|choices))?"
    r"|sheets\[\d+\]\.(?:id|dimensions|column_widths\.[A-Z]{1,3}"
    r"|cells\.[A-Z]{1,3}[1-9][0-9]*\.(?:data_type|provenance|cached_value|blocked_reason"
    r"|style\.[a-z_]+|formula\.(?:dependencies|result))))$"
)


def _accepted_imports():
    """Every corpus workbook import_xlsx accepts, with its model; refusals are skipped."""
    for path in sorted(CORPUS.glob("*.xlsx")):
        try:
            model = import_xlsx(path)
        except WorkbookError:
            # UnsupportedWorkbook is a WorkbookError: both are the importer's refusals.
            continue
        yield path, model


def test_every_importable_corpus_workbook_converts_and_round_trips():
    converted: list[str] = []
    refused: dict[str, str] = {}
    for path, model in _accepted_imports():
        envelope = model.envelope
        document = model.to_dict()
        try:
            workbook, forward = to_canonical(document, envelope)
        except ConversionError as error:
            # The toolkit holds a shared-formula follower as an empty formula
            # with a blocked_reason; the canonical shape has no such cell and
            # its own importer refuses the same workbooks (unsupported_formula).
            # The refusal is the canonical reader's, named, never a crash.
            refused[path.name] = str(error)
            continue
        converted.append(path.name)
        assert hydrate(workbook.to_json()) == workbook, path.name
        jsonschema.validate(json.loads(workbook.to_json()), SCHEMA)
        for conversion in (forward.dropped, forward.added):
            assert list(conversion) == sorted(set(conversion)), (path.name, conversion)
            for item in conversion:
                assert _PATH.fullmatch(item), (path.name, item)
        assert {"model_version", "provenance", "metadata.source"} <= set(forward.added), path.name
        assert workbook.provenance.to_dict() == {
            "origin": "imported",
            "source": envelope.source.workbook_part,
        }, path.name
        assert workbook.metadata["source"] == {
            "fingerprint": envelope.source.fingerprint,
            "parts": list(envelope.source.parts),
        }, path.name
        for index, sheet in enumerate(workbook.sheets):
            part = envelope.source.sheet_parts[sheet.name]
            for address, cell in sheet.cells.items():
                assert cell.provenance.to_dict() == {"origin": "imported", "source": part}, (
                    path.name,
                    sheet.name,
                    address,
                )
                assert f"sheets[{index}].cells.{address}.provenance" in forward.added
        back, backward = to_toolkit(workbook, input_kinds={})
        listed = _listed(forward, backward)
        assert _one_spelling(_without(document, listed)) == _without(back, listed), path.name
    for name, message in refused.items():
        assert message.startswith("invalid_model: ") and "formula.expression" in message, (
            name,
            message,
        )
    assert set(refused) == _CONVERSION_REFUSALS, refused
    # The converted workbooks are the intake agreement plus the intake
    # refusals whose documents still convert.
    assert set(converted) == _CANONICAL_INTAKE_AGREED | (
        _CANONICAL_INTAKE_REFUSALS - _CONVERSION_REFUSALS
    ), converted


def test_converted_import_agrees_with_canonical_intake_cell_for_cell():
    agreed = []
    refused = []
    for path, model in _accepted_imports():
        try:
            intake = intake_workbook_model(path)
        except (WorkbookError, CanonicalPackageError):
            refused.append(path.name)
            continue
        agreed.append(path.name)
        converted, _conversion = to_canonical(model.to_dict(), model.envelope)
        # Metadata is not compared: defined names are the contract's, not step 0's.
        assert converted.provenance == intake.provenance, path.name
        assert [sheet.name for sheet in converted.sheets] == [
            sheet.name for sheet in intake.sheets
        ], path.name
        for left, right in zip(converted.sheets, intake.sheets):
            where = (path.name, left.name)
            assert left.dimensions == right.dimensions, where
            assert set(left.cells) == set(right.cells), where
            for address in sorted(left.cells):
                assert _comparable(left.cells[address]) == _comparable(right.cells[address]), (
                    *where,
                    address,
                )
    assert set(refused) == _CANONICAL_INTAKE_REFUSALS, refused
    assert set(agreed) == _CANONICAL_INTAKE_AGREED, agreed


def _comparable(cell) -> tuple:
    expression = None if cell.formula is None else cell.formula.expression
    return (cell.value, cell.data_type, cell.number_format, expression, cell.provenance)
