"""Intake: typed Workbook model from .xlsx."""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

from workbook_forge.intake import (
    extract_dependencies,
    intake_workbook,
    intake_workbook_model,
    main,
)
from workbook_forge.model import (
    Binding,
    BindingConstraints,
    Cell,
    Formula,
    ModelError,
    Sheet,
    UnsupportedVersionError,
    Workbook,
)

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PKG = "http://schemas.openxmlformats.org/package/2006/content-types"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def write_intake_fixture(path: Path) -> Path:
    """Synthetic .xlsx: cached and uncached formulas, refs, format, empty cell."""
    parts = {
        "[Content_Types].xml": f"""<?xml version="1.0"?><Types xmlns="{PKG}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>""".encode(),
        "_rels/.rels": f"""<?xml version="1.0"?><Relationships xmlns="{PKG_REL}"><Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>""".encode(),
        "xl/workbook.xml": f"""<?xml version="1.0"?><workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/><sheet name="Sheet2" sheetId="2" r:id="rId2"/></sheets></workbook>""".encode(),
        "xl/_rels/workbook.xml.rels": f"""<?xml version="1.0"?><Relationships xmlns="{PKG_REL}"><Relationship Id="rId1" Type="{REL}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="{REL}/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="{REL}/styles" Target="styles.xml"/></Relationships>""".encode(),
        "xl/styles.xml": f"""<?xml version="1.0"?><styleSheet xmlns="{MAIN}"><numFmts count="1"><numFmt numFmtId="164" formatCode="0.00%"/></numFmts><fonts count="1"><font/></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="1"><border/></borders><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" applyNumberFormat="1"/></cellXfs></styleSheet>""".encode(),
        "xl/worksheets/sheet1.xml": f"""<?xml version="1.0"?><worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1"><v>10</v></c><c r="B1"><f>A1*2</f><v>20</v></c><c r="C1"/><c r="D1" s="1"><v>0.5</v></c><c r="E1"><f>Sheet2!A1</f><v>5</v></c><c r="F1"><f>A1/0</f></c></row></sheetData></worksheet>""".encode(),
        "xl/worksheets/sheet2.xml": f"""<?xml version="1.0"?><worksheet xmlns="{MAIN}"><sheetData><row r="1"><c r="A1"><v>5</v></c></row></sheetData></worksheet>""".encode(),
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return path


def test_core_types_are_importable_source_of_truth():
    assert Cell.__module__ == "workbook_forge.model"
    assert Formula.__module__ == "workbook_forge.model"
    assert Sheet.__module__ == "workbook_forge.model"
    assert Workbook.__module__ == "workbook_forge.model"


def test_intake_formula_cross_sheet_format_and_empty(tmp_path):
    path = write_intake_fixture(tmp_path / "intake.xlsx")
    wb = intake_workbook_model(path)
    assert isinstance(wb, Workbook)
    assert wb.schema_version == 1
    assert wb.model_version == 1
    assert wb.source_path == str(path.resolve())
    assert {s.name for s in wb.sheets} == {"Sheet1", "Sheet2"}

    sheet1 = wb.sheet("Sheet1")
    assert sheet1 is not None

    # Formula cell
    b1 = sheet1.cells["B1"]
    assert b1.formula is not None
    assert b1.formula.expression == "=A1*2"
    assert "Sheet1!A1" in b1.formula.dependencies
    assert b1.value is None
    assert b1.formula.cached_value == 20.0

    # Cross-sheet reference
    e1 = sheet1.cells["E1"]
    assert e1.formula is not None
    assert e1.formula.expression == "=Sheet2!A1"
    assert e1.formula.dependencies == ("Sheet2!A1",)
    assert e1.value is None
    assert e1.formula.cached_value == 5.0

    # Formula without an imported cache keeps that absence explicit.
    f1 = sheet1.cells["F1"]
    assert f1.formula is not None
    assert f1.value is None
    assert f1.formula.cached_value is None
    assert f1.data_type == "unknown"

    # Number-formatted cell
    d1 = sheet1.cells["D1"]
    assert d1.value == 0.5
    assert d1.number_format == "0.00%"
    assert d1.data_type == "number"

    # Empty cell present in the map
    c1 = sheet1.cells["C1"]
    assert c1.value is None
    assert c1.formula is None
    assert c1.data_type == "blank"

    summary = wb.to_summary_dict()
    assert summary["model_version"] == 1
    assert summary["sheet_count"] == 2
    assert summary["formula_count"] == 3
    assert summary["dependency_graph_size"] >= 2


def test_extract_dependencies_handles_sheet_bang():
    deps = extract_dependencies("=Sheet2!A1+B2", "Sheet1")
    assert deps == ("Sheet2!A1", "Sheet1!B2")


def test_intake_workbook_emits_canonical_json_bytes(tmp_path):
    path = write_intake_fixture(tmp_path / "canonical.xlsx")
    encoded = intake_workbook(path)
    assert isinstance(encoded, bytes)
    payload = json.loads(encoded)
    assert payload["schema_version"] == 1
    assert payload["model_version"] == 1
    assert payload["sheets"][0]["cells"]["B1"]["formula"]["cached_value"] == 20.0
    hydrated = Workbook.from_bytes(encoded)
    assert hydrated.to_bytes() == encoded


def test_cli_prints_canonical_json_by_default(tmp_path):
    path = write_intake_fixture(tmp_path / "cli.xlsx")
    # module entry point
    proc = subprocess.run(
        [sys.executable, "-m", "workbook_forge.intake", str(path)],
        cwd=Path(__file__).resolve().parents[1],
        env={**dict(**__import__("os").environ), "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["schema_version"] == 1
    assert payload["model_version"] == 1
    assert "sheets" in payload
    assert payload["sheets"][0]["cells"]["B1"]["formula"]["cached_value"] == 20.0


def test_cli_can_print_versioned_summary(tmp_path):
    path = write_intake_fixture(tmp_path / "summary.xlsx")
    proc = subprocess.run(
        [sys.executable, "-m", "workbook_forge.intake", "--summary", str(path)],
        cwd=Path(__file__).resolve().parents[1],
        env={**dict(**__import__("os").environ), "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["schema_version"] == 1
    assert payload["model_version"] == 1
    assert payload["sheet_count"] == 2
    assert payload["formula_count"] == 3
    assert "dependency_graph_size" in payload


def test_canonical_fixture_matches_schema_and_round_trips_stably():
    root = Path(__file__).resolve().parents[2]
    schema = json.loads((root / "schemas/workbook-model-v1.schema.json").read_text())
    fixture_path = root / "tests/fixtures/canonical/workbook-v1.json"
    fixture_bytes = fixture_path.read_bytes()
    payload = json.loads(fixture_bytes)

    import jsonschema

    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(payload, schema)

    workbook = Workbook.from_bytes(fixture_bytes)
    encoded = workbook.to_bytes()
    rehydrated = Workbook.from_bytes(encoded)
    assert encoded == rehydrated.to_bytes()
    assert json.loads(encoded) == payload
    results = rehydrated.sheet("Results")
    assert results is not None
    formula_with_cache = results.cells["A1"]
    assert formula_with_cache.value is None
    assert formula_with_cache.formula is not None
    assert formula_with_cache.formula.cached_value == 20.0
    formula_without_cache = results.cells["B1"]
    assert formula_without_cache.value is None
    assert formula_without_cache.formula is not None
    assert formula_without_cache.formula.cached_value is None
    assert "cached_value" not in payload["sheets"][1]["cells"]["B1"]["formula"]
    assert rehydrated.bindings == (
        Binding(
            direction="input",
            name="amount",
            sheet="Inputs",
            address="A1",
            value_type="number",
            required=True,
            constraints=BindingConstraints(minimum=0.0, maximum=1000.0),
        ),
        Binding(
            direction="output",
            name="doubled",
            sheet="Results",
            address="A1",
            value_type="number",
            required=True,
        ),
    )
    inputs = rehydrated.sheet("Inputs")
    assert inputs.cells["A2"].value is None
    assert inputs.cells["A3"].value is True
    assert inputs.cells["B1"].value == "assumption note"
    assert inputs.cells["C1"].value == {
        "error": "#N/A",
        "message": "source cell contained an error",
    }


def test_model_reader_rejects_unsupported_versions_and_unknown_fields():
    root = Path(__file__).resolve().parents[2]
    fixture = json.loads((root / "tests/fixtures/canonical/workbook-v1.json").read_bytes())
    for field in ("schema_version", "model_version"):
        for version in (2, -1):
            changed = {**fixture, field: version}
            try:
                Workbook.from_bytes(json.dumps(changed).encode())
            except UnsupportedVersionError as error:
                assert f"unsupported {field} {version}" in str(error)
            else:
                raise AssertionError(f"reader accepted unsupported {field} {version}")

    changed = json.loads(json.dumps(fixture))
    changed["sheets"][0]["cells"]["A1"]["unexpected"] = True
    try:
        Workbook.from_bytes(json.dumps(changed).encode())
    except ModelError as error:
        assert "unsupported field(s): unexpected" in str(error)
    else:
        raise AssertionError("reader silently accepted an unknown cell field")


def test_model_reader_rejects_missing_versions_and_mismatched_cell_address():
    root = Path(__file__).resolve().parents[2]
    fixture = json.loads((root / "tests/fixtures/canonical/workbook-v1.json").read_bytes())
    missing_version = {key: value for key, value in fixture.items() if key != "model_version"}
    try:
        Workbook.from_bytes(json.dumps(missing_version).encode())
    except ModelError as error:
        assert "missing required field(s): model_version" in str(error)
    else:
        raise AssertionError("reader defaulted a missing model_version")

    changed = json.loads(json.dumps(fixture))
    changed["sheets"][0]["cells"]["A1"]["address"] = "B1"
    try:
        Workbook.from_bytes(json.dumps(changed).encode())
    except ModelError as error:
        assert "must match its cell-map key" in str(error)
    else:
        raise AssertionError("reader accepted a mismatched cell-map address")


def test_model_reader_rejects_invalid_binding_constraints():
    root = Path(__file__).resolve().parents[2]
    fixture = json.loads((root / "tests/fixtures/canonical/workbook-v1.json").read_bytes())
    fixture["bindings"][0]["constraints"] = {"min": 10, "max": 2}
    try:
        Workbook.from_bytes(json.dumps(fixture).encode())
    except ModelError as error:
        assert "constraints.min must not exceed max" in str(error)
    else:
        raise AssertionError("reader accepted an inverted binding range")
