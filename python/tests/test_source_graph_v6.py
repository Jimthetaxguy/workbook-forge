"""XLOOKUP's six positional arguments survive compiled Rust/Python source."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from workbook_forge import source_graph as g
from workbook_forge.source_contract import SourceDiagnostic
from workbook_forge.toolkit import WorkbookModel
from workbook_forge.xlsx import export_xlsx, import_xlsx
from test_compiler_benchmark import compiler_binary  # noqa: F401,F811

ROOT = Path(__file__).resolve().parents[2]
CASE = json.loads((ROOT / "fixtures/xlookup-source-cases.v1.json").read_text())


def make_workbook(path):
    model = WorkbookModel(sheets=[CASE["sheet"]])
    for address, value in CASE["values"].items():
        model.set_value(CASE["sheet"], address, value)
    for address, formula in CASE["formulas"].items():
        model.set_formula(CASE["sheet"], address, formula)
    export_xlsx(model, path)
    return model


def assert_formula_values(values):
    for address, expected in CASE["expected"].items():
        key = g.sheet_prefix(CASE["sheet"]) + address
        assert g.typed(values[key]) == g.typed(expected), (address, values[key], expected)


@pytest.mark.parametrize(
    "origin,destination",
    [("python", "python"), ("python", "rust"), ("rust", "python"), ("rust", "rust")],
)
def test_xlookup_six_slots_execute_translate_and_export(
    tmp_path, compiler_binary, origin, destination  # noqa: F811
):
    source = tmp_path / "source.xlsx"
    make_workbook(source)
    code, manifest = g.compile_workbook(source, origin, version=6)
    assert manifest["source_subset_version"] == 6
    program = g.import_source(code, origin)
    call = program.formulas[g.sheet_prefix(CASE["sheet"]) + "D2"]
    assert call.kind == "xlookup"
    assert [argument.kind for argument in call.args] == [
        "text_hex", "range_ref", "range_ref", "missing", "missing", "neg"
    ]
    full_call = program.formulas[g.sheet_prefix(CASE["sheet"]) + "D10"]
    assert full_call.kind == "xlookup"
    assert [argument.kind for argument in full_call.args] == [
        "text_hex", "range_ref", "range_ref", "text_hex", "num", "neg"
    ]

    first = tmp_path / ("first.py" if origin == "python" else "first.rs")
    first.write_text(code)
    assert_formula_values(g.execute(first, origin, {}, tmp_path / "first-bin"))
    changed = g.execute(
        first, origin, {g.sheet_prefix(CASE["sheet"]) + "A1": "Apple"}, tmp_path / "changed-bin"
    )
    assert changed[g.sheet_prefix(CASE["sheet"]) + "D1"] == 20

    translated = g.translate(code, origin, destination)
    second = tmp_path / ("second.py" if destination == "python" else "second.rs")
    second.write_text(translated)
    assert_formula_values(g.execute(second, destination, {}, tmp_path / "second-bin"))

    if origin == destination:
        output = tmp_path / "output.xlsx"
        g.emit(second, destination, manifest, {}, output, compiler_binary)
        reopened = import_xlsx(output).to_dict()["sheets"][0]["cells"]
        assert reopened["D2"]["cached_value"] == 20
        roundtrip = g.parse_expression(reopened["D2"]["formula"])
        assert roundtrip.value.upper() == "XLOOKUP"
        assert [roundtrip.children[index].kind for index in (3, 4)] == ["missing", "missing"]
        assert roundtrip.children[5].kind == "unary" and roundtrip.children[5].value == "-"
        assert reopened["D5"]["cached_value"] == {"error": "#N/A"}


@pytest.mark.parametrize("language", ["python", "rust"])
@pytest.mark.parametrize(
    "formula,code",
    [
        ('=XLOOKUP("x",A1:A2,B1:B3)', "unsupported_shape"),
        ('=XLOOKUP("x",A1:B2,C1:D2)', "unsupported_shape"),
        ('=XLOOKUP("x",A1:A2,B1:B2,,1)', "unsupported_formula"),
        ('=XLOOKUP("x",A1:A2,B1:B2,,,2)', "unsupported_formula"),
    ],
)
def test_xlookup_shape_and_unimplemented_modes_fail_explicitly(
    tmp_path, language, formula, code
):
    node = g.from_excel(g.parse_expression(formula), "Calc", version=6)
    program = g.Program("Calc", {"'Calc'!A1": g.scalar("x"), "'Calc'!B1": g.scalar(1)},
                        {"'Calc'!D1": node}, ("Calc",), 6)
    if code == "unsupported_shape":
        with pytest.raises(SourceDiagnostic) as error:
            g.generate(program, language)
        assert error.value.code == code
        return

    source = tmp_path / ("program.py" if language == "python" else "program.rs")
    source.write_text(g.generate(program, language))
    with pytest.raises(SourceDiagnostic) as error:
        g.execute(source, language, {}, tmp_path / "binary")
    assert error.value.code == code


def test_xlookup_requires_profile_six_and_missing_slots_are_functional():
    formula = g.parse_expression('=XLOOKUP(,A1:A2,B1:B2,,0,-1)')
    with pytest.raises(SourceDiagnostic, match="profile v6"):
        g.from_excel(formula, "Calc", version=5)
    node = g.from_excel(formula, "Calc", version=6)
    assert [argument.kind for argument in node.args] == [
        "missing", "range_ref", "range_ref", "missing", "num", "neg"
    ]


def test_packaged_compiler_defaults_to_graph_v6_for_xlookup(tmp_path):
    source = tmp_path / "source.xlsx"
    make_workbook(source)
    bundle = tmp_path / "bundle"
    subprocess.run(
        [sys.executable, "-m", "workbook_forge.source_cli", "compile", str(source), str(bundle), "--language", "python"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads((bundle / "manifest.json").read_text())["source_subset_version"] == 6
    assert (bundle / "program.py").read_text().startswith(g.template("python", 6))
