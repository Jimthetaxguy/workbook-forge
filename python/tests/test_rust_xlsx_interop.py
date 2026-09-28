"""Real file interchange between independent Python and standalone Rust adapters.

The Rust example is compiled and run as a process; the optional Python native
bridge is not involved. These tests prove adapter interoperability, not Excel
Desktop acceptance. Rust is required when running this source-tree suite.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile
from xml.etree import ElementTree as ET

import pytest

from workbook_forge.toolkit import operating_scenario
from workbook_forge.workbook import Workbook
from workbook_forge.xlsx import export_xlsx, import_xlsx

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def rust_xlsx():
    cargo = shutil.which("cargo")
    if cargo is None:
        pytest.skip("Rust toolchain is unavailable; standalone adapter interop was not run")
    target = ROOT / "rust" / "target"
    environment = dict(os.environ, CARGO_TARGET_DIR=str(target))
    result = subprocess.run(
        [cargo, "build", "--manifest-path", str(ROOT / "rust/Cargo.toml"), "--locked", "--offline", "--example", "xlsx_scenario"],
        env=environment, text=True, capture_output=True, timeout=180,
    )
    assert result.returncode == 0, result.stderr
    binary = target / "debug" / "examples" / ("xlsx_scenario.exe" if os.name == "nt" else "xlsx_scenario")

    def run(*arguments, success=True):
        completed = subprocess.run(
            [str(binary), *map(str, arguments)], text=True, capture_output=True,
            timeout=30,
        )
        if success:
            assert completed.returncode == 0, completed.stderr
            return json.loads(completed.stdout)
        assert completed.returncode != 0, completed.stdout
        return completed

    return run


def _bindings():
    document = operating_scenario(backend="python").to_dict()
    return {"inputs": document["inputs"], "outputs": document["outputs"], "backend": "python"}


def _outputs(model, price):
    report = model.calculate()
    assert report["diagnostics"] == []
    assert report["outputs"]["revenue"] == price * 370
    assert report["outputs"]["profit"] == (price - 8) * 370 - 3000
    if price > 8:
        assert report["outputs"]["break_even_units"] == pytest.approx(1000 / (price - 8))
    else:
        assert report["outputs"]["break_even_units"] == {"error": "#N/A"}
    return report["outputs"]


def _parts(path):
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_python_xlsx_rust_edit_python_reimport_preserves_source_and_parts(rust_xlsx, tmp_path):
    generated = export_xlsx(operating_scenario(backend="python"), tmp_path / "python-generated.xlsx")
    original = _parts(generated)
    original["custom/opaque.xml"] = b'<opaque xmlns="urn:workbook-forge:interop">preserve &amp; exact bytes</opaque>'
    source = tmp_path / "python-with-opaque.xlsx"
    with zipfile.ZipFile(source, "w") as archive:
        for name, data in original.items():
            archive.writestr(name, data)
    source_bytes = source.read_bytes()
    destination = tmp_path / "rust-edited.xlsx"
    rust_xlsx("edit", source, destination, "25")
    assert source.read_bytes() == source_bytes
    restored = import_xlsx(destination, **_bindings())
    _outputs(restored, 25)
    after = _parts(destination)
    assert after.keys() == original.keys()
    for name in original:
        if name not in {"xl/workbook.xml", "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml"}:
            assert after[name] == original[name], name
    with Workbook.open(destination) as workbook:
        assert workbook.get("Assumptions", "B1").value == 25
        assert workbook.get("Forecast", "F2").value == 9250
        assert workbook.get("Forecast", "F2").formula == "SUM(C2:C4)"
    # The Python adapter must accept Rust's preserved validation and styles.
    python_again = export_xlsx(restored, tmp_path / "python-again.xlsx")
    _outputs(import_xlsx(python_again, **_bindings()), 25)


def test_rust_generated_xlsx_python_edit_rust_reimport(rust_xlsx, tmp_path):
    source = tmp_path / "rust-generated.xlsx"
    rust_xlsx("generate", source)
    source_bytes = source.read_bytes()
    model = import_xlsx(source, **_bindings())
    _outputs(model, 20)
    model.set_inputs({"unit_price": 25})
    destination = export_xlsx(model, tmp_path / "python-edited.xlsx")
    observed = rust_xlsx("inspect", destination)
    assert observed["report"]["diagnostics"] == []
    assert observed["report"]["outputs"] == _outputs(model, 25)
    assert source.read_bytes() == source_bytes
    # A second Rust edit exercises imported Python validation and source styles.
    final = tmp_path / "rust-again.xlsx"
    rust_xlsx("edit", destination, final, "30")
    _outputs(import_xlsx(final, **_bindings()), 30)


def test_rust_input_validation_and_existing_output_refuse_without_mutation(rust_xlsx, tmp_path):
    source = export_xlsx(operating_scenario(backend="python"), tmp_path / "input.xlsx")
    original = source.read_bytes()
    rejected = tmp_path / "invalid.xlsx"
    rust_xlsx("edit", source, rejected, "-1", success=False)
    assert not rejected.exists()
    existing = tmp_path / "existing.xlsx"
    existing.write_bytes(b"existing destination is never overwritten")
    rust_xlsx("edit", source, existing, "25", success=False)
    assert existing.read_bytes() == b"existing destination is never overwritten"
    rust_xlsx("edit", source, source, "25", success=False)
    assert source.read_bytes() == original


def test_rust_scalar_error_cache_is_read_as_error_in_python(rust_xlsx, tmp_path):
    source = export_xlsx(operating_scenario(backend="python"), tmp_path / "source.xlsx")
    destination = tmp_path / "no-margin.xlsx"
    rust_xlsx("edit", source, destination, "8")
    restored = import_xlsx(destination, **_bindings())
    _outputs(restored, 8)
    cells = restored.to_dict()["sheets"][1]["cells"]
    assert cells["F4"]["cached_value"] == {"error": "#N/A"}
    assert cells["F4"]["value"] is None


@pytest.mark.parametrize("storage", ["inline", "shared"])
def test_rich_text_excludes_phonetic_annotations_in_both_adapters(rust_xlsx, tmp_path, storage):
    generated = export_xlsx(operating_scenario(backend="python"), tmp_path / "generated.xlsx")
    parts = _parts(generated)
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    relation = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    rich_text = '<r><t>東</t></r><r><t>京</t></r><rPh sb="0" eb="2"><t>とうきょう</t></rPh>'
    old = b'<c r="A1" s="0" t="inlineStr"><is><t xml:space="preserve">Unit price</t></is></c>'
    if storage == "inline":
        new = f'<c r="A1" s="0" t="inlineStr"><is>{rich_text}</is></c>'.encode()
    else:
        new = b'<c r="A1" s="0" t="s"><v>0</v></c>'
        parts["xl/sharedStrings.xml"] = f'<sst xmlns="{main}" count="1" uniqueCount="1"><si>{rich_text}</si></sst>'.encode()
        parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
            b"</Types>", b'<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>',
        )
        parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace(
            b"</Relationships>", f'<Relationship Id="richText" Type="{relation}/sharedStrings" Target="sharedStrings.xml"/></Relationships>'.encode(),
        )
    assert parts["xl/worksheets/sheet1.xml"].count(old) == 1
    parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(old, new)
    source = tmp_path / "rich-text.xlsx"
    with zipfile.ZipFile(source, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    python = import_xlsx(source, **_bindings())
    assert python.to_dict()["sheets"][0]["cells"]["A1"]["value"] == "東京"
    observed = rust_xlsx("inspect", source)
    assert observed["model"]["sheets"][0]["cells"]["A1"]["value"] == "東京"
    destination = tmp_path / "rich-edited.xlsx"
    rust_xlsx("edit", source, destination, "25")
    _outputs(import_xlsx(destination, **_bindings()), 25)
    if storage == "shared":
        assert _parts(destination)["xl/sharedStrings.xml"] == parts["xl/sharedStrings.xml"]
    else:
        assert rich_text.encode() in _parts(destination)["xl/worksheets/sheet1.xml"]


def test_extension_cell_like_xml_cannot_redirect_a_real_cell_edit(rust_xlsx, tmp_path):
    generated = export_xlsx(operating_scenario(backend="python"), tmp_path / "generated.xlsx")
    parts = _parts(generated)
    # Extension payloads are opaque. A same-namespace c element outside
    # sheetData/row must never replace the authoritative worksheet cell node.
    extension = b'<extLst><ext uri="urn:workbook-forge:opaque-cell"><c r="B1"><v>777</v></c></ext></extLst>'
    parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(b"</worksheet>", extension + b"</worksheet>")
    source = tmp_path / "extension.xlsx"
    with zipfile.ZipFile(source, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    observed = rust_xlsx("inspect", source)
    assert observed["model"]["sheets"][0]["cells"]["B1"]["value"] == 20
    destination = tmp_path / "edited.xlsx"
    rust_xlsx("edit", source, destination, "25")
    saved = _parts(destination)["xl/worksheets/sheet1.xml"]
    root = ET.fromstring(saved)
    namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    actual = root.find('s:sheetData/s:row/s:c[@r="B1"]/s:v', namespace)
    assert actual is not None and float(actual.text) == 25
    # Python deliberately refuses duplicate c elements even in extensions;
    # scoped XML and the standalone Rust decoder verify this broader profile.
    observed = rust_xlsx("inspect", destination)
    assert observed["report"]["outputs"]["revenue"] == 9250
    assert extension in saved
