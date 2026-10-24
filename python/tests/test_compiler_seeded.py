"""Seeded scalar arithmetic/metamorphic routes; not an Excel oracle."""

from dataclasses import replace
import json
import os
from pathlib import Path
import random
import shutil
import subprocess

import pytest

from workbook_forge import source_graph as g
from workbook_forge.source_contract import SourceDiagnostic
from workbook_forge.toolkit import WorkbookModel
from workbook_forge.xlsx import export_xlsx, import_xlsx

ROOT = Path(__file__).resolve().parents[2]


def seeded_case(seed, edited=False):
    rng = random.Random(seed)
    defaults = {f"A{i}": float(rng.randrange(1, 6)) for i in range(1, 5)}
    original = dict(defaults)
    if edited:
        defaults.update(A1=defaults["A1"] + 3, A2=-1.0, A3=defaults["A3"] + 2)
    formulas = {}
    expected = {g.sheet_prefix("Data") + a: v for a, v in defaults.items()}
    values = {}
    for row in range(1, 25):
        previous = "B" + str(row - 1)
        value = values.get(previous, 0.0)
        family = rng.randrange(7)
        if row == 1:
            formula = "='Data'!$A$1*2"
            result = defaults["A1"] * (3 if edited else 2)
        elif row == 24:
            formula = "=SUM('Data'!A1:A4)+AVERAGE('Data'!A1:A4)"
            result = sum(defaults.values()) * 1.25
        elif family == 0:
            formula = f"={previous}/2"
            result = value / 2
        elif family == 1:
            formula = f"={previous}+'Data'!A4"
            result = value + defaults["A4"]
        elif family == 2:
            formula = "=SUM('Data'!A1:A4)"
            result = sum(defaults.values())
        elif family == 3:
            formula = "=AVERAGE('Data'!A1:A4)"
            result = sum(defaults.values()) / 4
        elif family == 4:
            formula = f"=IF('Data'!A2<0,{previous}+1,IFERROR(1/0,5))"
            result = value + 1 if defaults["A2"] < 0 else 5.0
        elif family == 5:
            formula = f"=IFERROR({previous}/('Data'!A1-'Data'!A1),{previous}+2)"
            result = value + 2
        else:
            formula = f"={previous}-'Data'!A4"
            result = value - defaults["A4"]
        formulas["B" + str(row)] = formula
        values["B" + str(row)] = float(result)
    formulas.update(C1="=1/0", C2="=IFERROR(C1,7)", C3="=IF(TRUE,3,C1)")
    expected.update({g.sheet_prefix("Calc") + a: v for a, v in values.items()})
    expected.update({"'Calc'!C1": {"error": "#DIV/0!"}, "'Calc'!C2": 7.0, "'Calc'!C3": 3.0})
    doc = {
        "schema_version": 1,
        "revision": 0,
        "sheets": [
            {"id": "data", "name": "Data", "cells": {a: {"value": v} for a, v in original.items()}},
            {
                "id": "calc",
                "name": "Calc",
                "cells": {a: {"formula": f} for a, f in formulas.items()},
            },
        ],
        "outputs": {},
        "inputs": {},
    }
    return doc, expected, original


@pytest.fixture(scope="module")
def seed_writer():
    if not shutil.which("rustc"):
        pytest.skip("Rust toolchain unavailable; seeded routes not executed")
    subprocess.run(
        [
            "cargo",
            "build",
            "--manifest-path",
            str(ROOT / "rust/Cargo.toml"),
            "--locked",
            "--offline",
            "--release",
            "--example",
            "compiler_roundtrip",
        ],
        env={**os.environ, "CARGO_TARGET_DIR": str(ROOT / "rust/target")},
        check=True,
        capture_output=True,
        timeout=180,
    )
    return ROOT / "rust/target/release/examples/compiler_roundtrip"


@pytest.mark.parametrize("seed", [7, 31, 2026])
@pytest.mark.parametrize(
    "origin,target",
    [("python", "python"), ("python", "rust"), ("rust", "python"), ("rust", "rust")],
)
def test_seeded_edited_and_metamorphic_routes_without_original(
    tmp_path, seed_writer, seed, origin, target
):
    doc, _, defaults = seeded_case(seed)
    _, expected, _ = seeded_case(seed, True)
    original = tmp_path / "original.xlsx"
    export_xlsx(WorkbookModel(document=doc), original)
    source, manifest = g.compile_workbook(original, origin, 5)
    expression = "mul(reference(e, \"'Data'!$A$1\"), num(2.0))"
    assert source.count(expression) == 1
    source = source.replace(expression, expression.replace("2.0", "3.0"))
    literal = (
        json.dumps("'Data'!A3")
        + (": " if origin == "python" else ".to_string(), ")
        + f"num({defaults['A3']!r})"
    )
    assert source.count(literal) == 1
    source = source.replace(
        literal, literal.replace(f"num({defaults['A3']!r})", f"num({defaults['A3'] + 2!r})")
    )
    first = tmp_path / ("first.py" if origin == "python" else "first.rs")
    first.write_text(source)
    original.rename(tmp_path / "unavailable-original.xlsx")
    overrides = {"'Data'!A1": defaults["A1"] + 3, "'Data'!A2": -1.0}

    def typed(values):
        return {a: g.typed(v) for a, v in values.items()}

    assert typed(g.execute(first, origin, overrides, tmp_path / "first-binary")) == typed(expected)
    # Re-read the actual edited artifact, translate it, then apply x+0 only to a
    # known-positive numeric result; this law does not assume signed-zero identity.
    translated = g.translate(first.read_text(), origin, target)
    program = g.import_source(translated, target)
    formulas = dict(program.formulas)
    formulas["'Calc'!B24"] = g.Node("add", args=(formulas["'Calc'!B24"], g.Node("num", 0.0)))
    assert expected["'Calc'!B24"] > 0
    second = tmp_path / ("second.py" if target == "python" else "second.rs")
    second.write_text(g.generate(replace(program, formulas=formulas), target))
    output = tmp_path / "output.xlsx"
    assert typed(g.emit(second, target, manifest, overrides, output, seed_writer)) == typed(
        expected
    )
    observed = import_xlsx(output).to_dict()
    actual = {
        g.sheet_prefix(sheet["name"]) + a: c.get("cached_value")
        if c.get("formula") is not None
        else c.get("value")
        for sheet in observed["sheets"]
        for a, c in sheet["cells"].items()
    }
    assert typed(actual) == typed(expected)
    assert observed["sheets"][1]["cells"]["B24"]["formula"].endswith("+0.0)")


def program(formula, defaults=None):
    return g.Program("Calc", defaults or {}, {"'Calc'!B1": formula}, ("Calc",), 5)


def test_validation_range_expansion_reuse_is_scoped_and_contextual(monkeypatch):
    original = g.range_keys
    calls = []

    def counted(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(g, "range_keys", counted)
    repeated = g.Node("sum_values", args=tuple(g.Node("range_ref", "A1:A2") for _ in range(10)))
    p = g.Program("Left", {}, {"'Left'!B1": repeated, "'Right'!B1": repeated}, ("Left", "Right"), 5)
    result = g.validate(p)
    assert result["'Left'!B1"] == ["'Left'!A1", "'Left'!A2"]
    assert result["'Right'!B1"] == ["'Right'!A1", "'Right'!A2"]
    assert len(calls) == 2
    g.validate(p)
    assert len(calls) == 4  # No cache escapes one complete validation.


@pytest.mark.parametrize(
    "case", ["arguments", "expression_depth", "dependency_depth", "range_expansion", "cycle"]
)
def test_bounded_profile_boundaries(case):
    if case == "arguments":
        g.validate(program(g.Node("sum_values", args=(g.Node("num", 1.0),) * 255)))
        p = program(g.Node("sum_values", args=(g.Node("num", 1.0),) * 256))
    elif case == "expression_depth":
        node = g.Node("num", 1.0)
        for _ in range(31):
            node = g.Node("add", args=(node, g.Node("num", 1.0)))
        g.validate(program(node))
        p = program(g.Node("add", args=(node, g.Node("num", 1.0))))
    elif case == "dependency_depth":
        formulas = {
            f"'Calc'!B{i}": g.Node("reference", "A1" if i == 1 else "B" + str(i - 1))
            for i in range(1, 97)
        }
        g.validate(g.Program("Calc", {"'Calc'!A1": g.Node("num", 1.0)}, formulas, ("Calc",), 5))
        formulas["'Calc'!B97"] = g.Node("reference", "B96")
        p = g.Program("Calc", {"'Calc'!A1": g.Node("num", 1.0)}, formulas, ("Calc",), 5)
    elif case == "range_expansion":
        node = g.Node("range_ref", "A1:A500")
        assert (
            len(g.validate(program(g.Node("sum_values", args=(node,) * 200)))["'Calc'!B1"]) == 500
        )
        p = program(g.Node("sum_values", args=(node,) * 201))
    else:
        p = program(g.Node("reference", "B1"))
    with pytest.raises(SourceDiagnostic) as error:
        g.validate(p)
    assert error.value.code == ("cycle" if case == "cycle" else "resource_limit")


@pytest.mark.parametrize("language", ["python", "rust"])
def test_near_dependency_limit_executes_both_runtimes(tmp_path, language):
    formulas = {
        f"'Calc'!B{i}": g.Node("reference", "A1" if i == 96 else "B" + str(i + 1))
        for i in range(1, 97)
    }
    p = g.Program("Calc", {"'Calc'!A1": g.Node("num", 1.0)}, formulas, ("Calc",), 5)
    path = tmp_path / ("program.py" if language == "python" else "program.rs")
    path.write_text(g.generate(p, language))
    actual = g.execute(path, language, {}, tmp_path / "binary")
    assert len(actual) == 97 and all(g.typed(value) == "n:1.0" for value in actual.values())


def test_source_byte_budget_component_boundary(tmp_path):
    source = tmp_path / "source.txt"
    source.write_bytes(b" " * g.MAX_SOURCE_BYTES)
    assert len(g.read_source(source)) == g.MAX_SOURCE_BYTES
    with source.open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(SourceDiagnostic) as error:
        g.read_source(source)
    assert error.value.code == "resource_limit"
