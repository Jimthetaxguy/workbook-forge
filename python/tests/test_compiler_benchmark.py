"""Cross-runtime IR transfer consumes edited artifacts, with explicit limits."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

from workbook_forge.toolkit import WorkbookModel
from workbook_forge.xlsx import export_snapshot, import_xlsx
from workbook_forge.workbook import WorkbookError

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def compiler_binary():
    """Build the release compiler_roundtrip example.

    Does not skip when rustc is missing. Callers that skip must do so before
    requesting this fixture.
    """
    target = ROOT / "rust/target"
    subprocess.run(
        [
            "cargo",
            "build",
            "--release",
            "--locked",
            "--offline",
            "--manifest-path",
            str(ROOT / "rust/Cargo.toml"),
            "--example",
            "compiler_roundtrip",
        ],
        env={**os.environ, "CARGO_TARGET_DIR": str(target)},
        check=True,
        timeout=180,
    )
    return target / "release/examples/compiler_roundtrip"


def tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_four_routes_receipt_records_source_translation_not_implemented_and_excel_recalculation_not_run(
    tmp_path, compiler_binary
):
    receipt = tool("compiler_benchmark").run(compiler_binary, tmp_path / "run", repeats=1)
    assert len(receipt["routes"]) == 4
    assert all(r["source_translation"] == "not_implemented" for r in receipt["routes"])
    assert all(r["excel_recalculation"] == "not_run" for r in receipt["routes"])


@pytest.mark.parametrize("backend", ["python", "rust"])
@pytest.mark.parametrize("mutation", ["remove_cell", "style", "guard", "version"])
def test_transferred_ir_cannot_weaken_preservation(tmp_path, compiler_binary, backend, mutation):
    benchmark = tool("compiler_benchmark")
    source = tmp_path / "source.xlsx"
    benchmark.fixture(source)
    document = benchmark.decode(compiler_binary, backend, source)
    if mutation == "remove_cell":
        del document["sheets"][0]["cells"]["A1"]
    elif mutation == "style":
        document["sheets"][0]["cells"]["A1"]["style"]["bold"] = False
    elif mutation == "guard":
        document["sheets"][0]["cells"]["A1"]["blocked_reason"] = "changed guard"
    else:
        document["schema_version"] = 999
    ir = tmp_path / "model.json"
    ir.write_text(json.dumps(document))
    output = tmp_path / "output.xlsx"
    with pytest.raises((ValueError, WorkbookError, subprocess.CalledProcessError)):
        benchmark.emit(compiler_binary, backend, source, ir, output)
    assert not output.exists()
    assert benchmark.decode(compiler_binary, backend, source)["sheets"][0]["cells"]["A1"]["value"] == 2


def test_snapshot_export_is_detached_and_requires_baseline(tmp_path):
    benchmark = tool("compiler_benchmark")
    source = tmp_path / "source.xlsx"
    benchmark.fixture(source)
    imported = import_xlsx(source)
    before = imported.to_dict()
    document = copy.deepcopy(before)
    document["sheets"][0]["cells"]["A1"]["value"] = 8
    export_snapshot(imported, document, tmp_path / "out.xlsx")
    assert imported.to_dict() == before
    assert document["sheets"][0]["cells"]["A1"]["value"] == 8
    with pytest.raises(WorkbookError, match="baseline"):
        export_snapshot(WorkbookModel(), document, tmp_path / "invalid.xlsx")


@pytest.mark.parametrize("front,back", [("rust", "python"), ("python", "rust")])
def test_cross_runtime_calculation_needs_no_original_package(tmp_path, compiler_binary, front, back):
    benchmark = tool("compiler_benchmark")
    source = tmp_path / "source.xlsx"
    benchmark.fixture(source)
    document = benchmark.decode(compiler_binary, front, source)
    document["sheets"][0]["cells"]["A1"]["value"] = 11
    document["sheets"][1]["cells"]["B1"]["formula"] = "=Data!$A$1*3"
    for sheet in document["sheets"]:
        for cell in sheet["cells"].values():
            cell.pop("cached_value", None)
    ir = tmp_path / "artifact.json"
    ir.write_text(json.dumps(document))
    # Destination only receives the edited artifact, without formula caches.
    source.rename(source.with_suffix(".unavailable"))
    if back == "rust":
        report = benchmark.rust(compiler_binary, "calculate", str(ir))
    else:
        report = WorkbookModel(document=json.loads(ir.read_text())).calculate()
    assert not report["diagnostics"]
    assert report["values"]["Calc!B1"] == 33
    assert report["values"]["Calc!B2"] == 47


def test_mapping_has_all_inventory_rows_and_honest_stages(compiler_binary):
    actual = tool("compiler_coverage").build(compiler_binary)
    assert actual == json.loads((ROOT / "catalog/compiler-coverage.v1.json").read_text())
    inventory = json.loads((ROOT / "catalog/function_inventory.json").read_text())
    assert {r["name"] for r in actual["functions"]} == {r["name"] for r in inventory["functions"]}
    for row in actual["functions"]:
        for stages in row["languages"].values():
            assert stages["translated_source"] == ("graph_source_subset_v6" if row["name"] in {"SUM","IF","IFERROR","AVERAGE","XLOOKUP"} else "not_implemented")
            assert stages["unsupported_now"] == (stages["executable"] == "planned")
            assert stages["excel_observed"] == "not_established_by_this_mapping"
