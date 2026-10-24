"""Independent-review regressions: representability, artifact closure, signed zero."""

import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import pytest

from workbook_forge import source_cli as cli, source_graph as g
from workbook_forge.source_contract import SourceDiagnostic
from workbook_forge.toolkit import WorkbookModel
from workbook_forge.xlsx import export_xlsx, import_xlsx

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
WRITER = Path(__file__).resolve().parents[2] / "rust/target/debug/examples/compiler_roundtrip"


def workbook(path, formula="=A1*2"):
    model = WorkbookModel(sheets=["Calc"])
    model.set_value("Calc", "A1", 0)
    model.set_formula("Calc", "B1", formula)
    export_xlsx(model, path)
    return model


@pytest.mark.parametrize(
    "mutation",
    [
        "locale-numfmt",
        "patterned-fill",
        "font-disabled",
        "fill-disabled",
        "format-disabled",
        "alignment-disabled",
        "style-parent",
    ],
)
def test_unrepresentable_style_is_refused_before_bundle(tmp_path, mutation, capsys):
    original = tmp_path / "original.xlsx"
    model = workbook(original)
    model.set_style(
        "Calc", "A1", bold=True, fill_color="FF0000", number_format="0.00", horizontal="center"
    )
    styled = tmp_path / "styled.xlsx"
    export_xlsx(model, styled)
    with zipfile.ZipFile(styled) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    styles = ET.fromstring(parts["xl/styles.xml"])
    xf = styles.findall(NS + "cellXfs/" + NS + "xf")[1]
    if mutation == "locale-numfmt":
        xf.set("numFmtId", "5")
    elif mutation == "patterned-fill":
        styles.findall(NS + "fills/" + NS + "fill/" + NS + "patternFill")[-1].set(
            "patternType", "gray125"
        )
    elif mutation == "style-parent":
        xf.set("xfId", "1")
    else:
        xf.set(
            {
                "font-disabled": "applyFont",
                "fill-disabled": "applyFill",
                "format-disabled": "applyNumberFormat",
                "alignment-disabled": "applyAlignment",
            }[mutation],
            "0",
        )
    parts["xl/styles.xml"] = ET.tostring(styles)
    changed = tmp_path / "changed.xlsx"
    with zipfile.ZipFile(changed, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    bundle = tmp_path / "bundle"
    assert cli.main(["compile", str(changed), str(bundle), "--language", "python"]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "unsupported_fidelity"
    assert not bundle.exists()


def test_real_manifest_budget_is_closed_under_cli_emit(tmp_path):
    original = tmp_path / "input.xlsx"
    doc = workbook(tmp_path / "seed.xlsx").to_dict()
    for i in range(2, 10002):
        doc["sheets"][0]["cells"]["A" + str(i)] = {"value": float(i)}
    export_xlsx(WorkbookModel(document=doc), original)
    bundle = tmp_path / "bundle"
    assert cli.main(["compile", str(original), str(bundle), "--language", "python"]) == 0
    assert (bundle / "manifest.json").stat().st_size <= g.MAX_SOURCE_BYTES
    original.unlink()
    assert (
        cli.main(
            [
                "emit",
                str(bundle / "program.py"),
                str(bundle / "manifest.json"),
                str(tmp_path / "output.xlsx"),
                "--language",
                "python",
            ]
        )
        == 0
    )


def test_oversized_manifest_refused_before_bundle_publication(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        g,
        "compile_workbook",
        lambda *a, **kw: ("source", {"padding": "x" * (g.MAX_SOURCE_BYTES + 1)}),
    )
    bundle = tmp_path / "bundle"
    assert cli.main(["compile", "unused.xlsx", str(bundle), "--language", "python"]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "resource_limit"
    assert not bundle.exists()


def test_library_producer_and_consumer_enforce_manifest_budget(tmp_path, monkeypatch):
    original = tmp_path / "input.xlsx"
    workbook(original)
    source, manifest = g.compile_workbook(original, "python", 5)
    manifest["padding"] = "x" * (g.MAX_SOURCE_BYTES + 1)
    with pytest.raises(SourceDiagnostic, match="manifest") as error:
        g.lower(source, "python", manifest, {})
    assert error.value.code == "resource_limit"
    real = g.source_model

    def bloated(*args):
        model = real(*args)
        model["sheets"][0]["cells"]["A1"]["style"] = {
            "number_format": "x" * (g.MAX_SOURCE_BYTES + 1)
        }
        return model

    monkeypatch.setattr(g, "source_model", bloated)
    with pytest.raises(SourceDiagnostic, match="manifest") as error:
        g.compile_workbook(original, "python", 5)
    assert error.value.code == "resource_limit"


@pytest.mark.parametrize(
    "origin,target",
    [("python", "python"), ("python", "rust"), ("rust", "python"), ("rust", "rust")],
)
def test_signed_zero_edited_routes_preserve_sign_and_sum_identity(tmp_path, origin, target):
    original = tmp_path / "original.xlsx"
    model = workbook(tmp_path / "seed.xlsx", "=0*-1")
    model.set_formula("Calc", "C1", "=SUM(0*-1)")
    model.set_formula("Calc", "D1", "=-A1")
    model.set_formula("Calc", "E1", "=-(-A1)")
    export_xlsx(model, original)
    source, manifest = g.compile_workbook(original, origin, 5)
    # Consume a deliberately edited artifact, not the original package.
    edited = source.replace("num(1.0)", "num(2.0)")
    assert edited != source
    source = edited
    original.unlink()
    path = tmp_path / ("program.rs" if target == "rust" else "program.py")
    path.write_text(g.translate(source, origin, target))
    actual = g.emit(path, target, manifest, {}, tmp_path / "output.xlsx", WRITER)
    assert g.typed(actual["'Calc'!B1"]) == "n:-0.0"
    assert g.typed(actual["'Calc'!C1"]) == "n:0.0"
    assert g.typed(actual["'Calc'!D1"]) == "n:-0.0"
    assert g.typed(actual["'Calc'!E1"]) == "n:0.0"
    imported = import_xlsx(tmp_path / "output.xlsx").to_dict()["sheets"][0]["cells"]
    assert math.copysign(1, imported["B1"]["cached_value"]) == -1
    assert math.copysign(1, imported["C1"]["cached_value"]) == 1
    assert g.typed(False) != g.typed(0.0) != g.typed(-0.0)
    assert g.typed(None) != g.typed(0.0)


@pytest.mark.parametrize("version", [2, 3, 4, 5])
@pytest.mark.parametrize("language", ["rust", "python"])
def test_sum_identity_and_unary_zero_in_each_profile(tmp_path, version, language):
    original = tmp_path / "original.xlsx"
    model = workbook(tmp_path / "seed.xlsx", "=SUM(0*-1)")
    model.set_formula("Calc", "C1", "=-A1")
    export_xlsx(model, original)
    source, _ = g.compile_workbook(original, language, version)
    path = tmp_path / ("program.rs" if language == "rust" else "program.py")
    path.write_text(source)
    result = g.execute(path, language, {}, tmp_path / "program")
    assert g.typed(result["B1" if version == 2 else "'Calc'!B1"]) == "n:0.0"
    assert g.typed(result["C1" if version == 2 else "'Calc'!C1"]) == "n:-0.0"


def test_writer_sign_corruption_is_refused_without_numeric_tolerance(tmp_path, monkeypatch):
    original = tmp_path / "original.xlsx"
    workbook(original, "=0*-1")
    source, manifest = g.compile_workbook(original, "python", 5)
    path = tmp_path / "program.py"
    path.write_text(source)
    real_export = g.export_xlsx

    def wrong_sign(model, target):
        real_export(model, target)
        with zipfile.ZipFile(target) as archive:
            parts = {name: archive.read(name) for name in archive.namelist()}
        sheet = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
        sheet.find(f".//{NS}c[@r='B1']/{NS}v").text = "0"
        parts["xl/worksheets/sheet1.xml"] = ET.tostring(sheet)
        with zipfile.ZipFile(target, "w") as archive:
            for name, data in parts.items():
                archive.writestr(name, data)

    monkeypatch.setattr(g, "export_xlsx", wrong_sign)
    output = tmp_path / "must-not-publish.xlsx"
    with pytest.raises(SourceDiagnostic) as error:
        g.emit(path, "python", manifest, {}, output)
    assert error.value.code == "backend_mismatch"
    assert not output.exists()
