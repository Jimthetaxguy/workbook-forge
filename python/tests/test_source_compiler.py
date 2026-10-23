"""Four actual executable-source routes; source edits survive without XML/cache."""
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

import pytest

from test_compiler_benchmark import compiler_binary, tool  # noqa: F401
from workbook_forge.toolkit import WorkbookModel
from workbook_forge.xlsx import export_xlsx, import_xlsx


@pytest.mark.parametrize("origin,destination", [("rust", "rust"), ("python", "python"), ("rust", "python"), ("python", "rust")])
def test_executable_source_roundtrip_without_original(tmp_path, compiler_binary, origin, destination):  # noqa: F811
    compiler = tool("source_compiler")
    model = WorkbookModel(sheets=["Calc"])
    model.set_value("Calc", "A1", 2)
    model.set_formula("Calc", "B1", "=A1*2+3")
    model.set_style("Calc", "A1", number_format="0.00", bold=True)
    source = tmp_path / "original.xlsx"
    export_xlsx(model, source)
    code, manifest = compiler.compile_workbook(source, origin)
    assert all("formula" not in cell and "cached_value" not in cell
               for cell in manifest["model"]["sheets"][0]["cells"].values())
    assert "<worksheet" not in json.dumps(manifest)
    assert "xlsx" not in code and "workbook_forge" not in code
    # Supported edit in real source code, not a workbook or an embedded formula.
    edited = code.replace("2.0", "5.0")
    assert edited != code
    source.rename(source.with_suffix(".unavailable"))
    first = tmp_path / ("first.rs" if origin == "rust" else "first.py")
    first.write_text(edited)
    assert compiler.execute(first, origin, 2, tmp_path / "first_binary") == 13
    assert compiler.execute(first, origin, 7, tmp_path / "first_binary_2") == 38
    translated = compiler.translate(first.read_text(), origin, destination)
    second = tmp_path / ("second.rs" if destination == "rust" else "second.py")
    second.write_text(translated)
    assert compiler.execute(second, destination, 7, tmp_path / "second_binary") == 38
    document = compiler.lower(second.read_text(), destination, manifest, 7)
    output = tmp_path / "output.xlsx"
    if destination == "rust":
        ir = tmp_path / "lowered.json"
        ir.write_text(json.dumps(document))
        subprocess.run([str(compiler_binary), "emit-fresh", str(ir), str(output)], check=True, capture_output=True, timeout=60)
    else:
        export_xlsx(WorkbookModel(document=document), output)
    reopened = import_xlsx(output).to_dict()
    cells = reopened["sheets"][0]["cells"]
    assert cells["A1"]["value"] == 7
    assert cells["B1"]["cached_value"] == 38
    assert "5.0" in cells["B1"]["formula"]
    assert cells["A1"]["style"] == manifest["model"]["sheets"][0]["cells"]["A1"]["style"]


@pytest.mark.parametrize("text", ["a1 / 0", "sum(a1)", "a1 ** 2", "__import__('os')", "a1 # spill", "True", "a2+1", "1e1000", "(" * 40 + "a1" + ")" * 40 + " ** 2"])
def test_source_subset_refuses_unrepresentable_edits(text):
    compiler = tool("source_compiler")
    with pytest.raises(compiler.SourceDiagnostic):
        compiler.expression(text)


def test_scaffold_edits_and_manifest_formula_restoration_are_refused():
    compiler = tool("source_compiler")
    source = compiler.generate(compiler.expression("a1+2"), "python")
    with pytest.raises(compiler.SourceDiagnostic, match="scaffold"):
        compiler.translate(source.replace("import sys", "import os"), "python", "rust")
    with pytest.raises(compiler.SourceDiagnostic, match="version"):
        compiler.lower(source, "python", {"source_subset_version": 2}, 1)
    model = WorkbookModel(sheets=["Calc"])
    model.set_value("Calc", "A1", 2)
    model.set_formula("Calc", "B1", "=A1+9")
    with pytest.raises(compiler.SourceDiagnostic, match="restore"):
        compiler.lower(source, "python", {"source_subset_version": 1, "model": model.to_dict()}, 1)


def test_numeric_domain_and_manifest_bindings_are_guarded():
    compiler = tool("source_compiler")
    for value in [True, float("inf"), float("nan"), 10 ** 10000]:
        assert not compiler.numeric_domain(value)
    assert compiler.numeric_domain(-1e15)
    model = WorkbookModel(sheets=["Calc"])
    model.set_value("Calc", "A1", 2)
    model.set_value("Calc", "B1", None)
    code = compiler.generate(compiler.expression("a1+2"), "python")
    with pytest.raises(compiler.SourceDiagnostic, match="bindings"):
        compiler.lower(code, "python", {"source_subset_version": 1, "model": model.to_dict(),
                       "input": {"sheet": "Calc", "address": "A2"}, "output": {"sheet": "Calc", "address": "B1"}}, 7)


@pytest.mark.parametrize("metadata", ["opaque", "italic", "row_height"])
def test_source_bundle_refuses_uncaptured_metadata(tmp_path, metadata):
    compiler = tool("source_compiler")
    model = WorkbookModel(sheets=["Calc"])
    model.set_value("Calc", "A1", 2)
    model.set_formula("Calc", "B1", "=A1+2")
    base = tmp_path / "base.xlsx"
    export_xlsx(model, base)
    with zipfile.ZipFile(base) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    if metadata == "opaque":
        parts["custom/metadata.xml"] = b"<metadata/>"
    elif metadata == "italic":
        root = ET.fromstring(parts["xl/styles.xml"])
        ET.SubElement(root.find(f"{{{compiler.MAIN}}}fonts/{{{compiler.MAIN}}}font"), f"{{{compiler.MAIN}}}i")
        parts["xl/styles.xml"] = ET.tostring(root)
    else:
        root = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
        root.find(f"{{{compiler.MAIN}}}sheetData/{{{compiler.MAIN}}}row").set("ht", "30")
        parts["xl/worksheets/sheet1.xml"] = ET.tostring(root)
    source = tmp_path / "metadata.xlsx"
    with zipfile.ZipFile(source, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    with pytest.raises(compiler.SourceDiagnostic) as error:
        compiler.compile_workbook(source, "python")
    assert error.value.code == "unsupported_fidelity"


def test_standalone_cli_compiles_translates_executes_and_emits(tmp_path, compiler_binary):  # noqa: F811
    compiler = tool("source_compiler")
    model = WorkbookModel(sheets=["Calc"])
    model.set_value("Calc", "A1", 2)
    model.set_formula("Calc", "B1", "=A1*2+3")
    source = tmp_path / "source.xlsx"
    export_xlsx(model, source)
    command = [sys.executable, compiler.__file__]
    bundle = tmp_path / "bundle"
    subprocess.run([*command, "compile", str(source), str(bundle), "--language", "python"], check=True)
    program = bundle / "program.py"
    program.write_text(program.read_text().replace("2.0", "5.0"))
    source.rename(source.with_suffix(".unavailable"))
    target = tmp_path / "translated.rs"
    subprocess.run([*command, "translate", str(program), str(target), "--origin", "python", "--destination", "rust"], check=True)
    output = tmp_path / "output.xlsx"
    subprocess.run([*command, "emit", str(target), str(bundle / "manifest.json"), str(output), "--language", "rust", "--input", "7", "--rust-binary", str(compiler_binary)], check=True, capture_output=True)
    assert import_xlsx(output).to_dict()["sheets"][0]["cells"]["B1"]["cached_value"] == 38
    program.write_text(program.read_text().replace("import sys", "import os"))
    refused = tmp_path / "refused.rs"
    result = subprocess.run([*command, "translate", str(program), str(refused), "--origin", "python", "--destination", "rust"], capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)["code"] == "unsupported_source"
    assert not refused.exists()
