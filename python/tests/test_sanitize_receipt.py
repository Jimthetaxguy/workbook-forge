"""tools/sanitize_receipt.py: receipts lose their machine paths, workbooks keep their bytes.

The paths in these tests are synthetic. ``/Users/example-user`` names no
account on any machine; it stands for the kind of path an Excel run leaves in
a receipt.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import sanitize_receipt  # noqa: E402

COMMITTED = ROOT / "receipts" / "canonical-operating-scenario"
COMMITTED_WORKBOOK = COMMITTED / "operating-scenario.exported.xlsx"
FAKE_HOME = "/Users/example-user"
FAKE_RUN = f"{FAKE_HOME}/Desktop/excel-run"

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tiny_xlsx(path: Path, *, abs_path: str | None = None, cell_value: int = 1) -> Path:
    """A one-cell package. ``abs_path`` plants Excel's saving-folder record."""
    planted = ""
    if abs_path is not None:
        planted = (
            '<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
            '<mc:Choice Requires="x15ac" xmlns:x15ac="http://schemas.microsoft.com/office/spreadsheetml/2010/11/ac">'
            f'<x15ac:absPath url="{abs_path}"/></mc:Choice></mc:AlternateContent>'
        )
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{_MAIN_NS}" xmlns:r="{_REL_NS}">{planted}'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>"
        ),
        "xl/worksheets/sheet1.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="{_MAIN_NS}"><sheetData><row r="1"><c r="A1"><v>{cell_value}</v></c></row></sheetData></worksheet>'
        ),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return path


@pytest.fixture
def synthetic(tmp_path):
    """A repository root with a receipt that holds every path shape the tool rewrites."""
    root = tmp_path / "repo"
    run = root / "receipts" / "run"
    run.mkdir(parents=True)
    workbook = tiny_xlsx(run / "roundtrip.xlsx")
    outside = tmp_path / "elsewhere" / "contract.json"
    outside.parent.mkdir()
    outside.write_text('{"id": "synthetic"}\n', encoding="utf-8")
    receipt = {
        "schema_version": 1,
        "status": "observed",
        "working_copy": f"{FAKE_RUN}/roundtrip.xlsx",
        "source_workbook": str(workbook),
        "contract": str(outside),
        "reason": f"Excel saved {FAKE_RUN}/roundtrip.xlsx after a full rebuild.",
        "notes": [
            "~/Documents/excel-run/notes.txt",
            "C:\\Users\\example-user\\excel-run\\roundtrip.xlsx",
            f"file://{FAKE_RUN}/roundtrip.applescript",
        ],
        "by_path": {f"{FAKE_RUN}/roundtrip.xlsx": {"sha256": "0" * 64}},
        "desktop_command": "python3 tools/canonical_excel_receipt.py --output-dir receipts/run --excel",
        "automation": "/usr/bin/osascript",
        "edit": "20 \u2192 25",
        "saved_workbook_sha256": _sha(workbook),
    }
    receipt_path = run / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return root, receipt_path, workbook, outside, receipt


def test_paths_leave_the_receipt_and_the_workbook_bytes_do_not_change(synthetic, tmp_path):
    root, receipt_path, workbook, outside, original = synthetic
    out = tmp_path / "published"
    result = sanitize_receipt.sanitize(receipt_path, workbook, out, root=root)

    assert sorted(path.name for path in out.iterdir()) == ["receipt.json", "roundtrip.xlsx", "sanitized.json"]
    assert _sha(out / "roundtrip.xlsx") == _sha(workbook) == result.workbook_sha256
    written = (out / "receipt.json").read_bytes()
    manifest_bytes = (out / "sanitized.json").read_bytes()
    for leak in (b"example-user", b"/Users/", b"~/", b"C:\\\\", b"file://", str(outside).encode()):
        assert leak not in written, leak
        assert leak not in manifest_bytes, leak

    document = json.loads(written)
    assert document["working_copy"] == "roundtrip.xlsx"
    assert document["source_workbook"] == "receipts/run/roundtrip.xlsx"
    assert document["contract"] == "sha256:" + _sha(outside)
    assert document["reason"] == "Excel saved roundtrip.xlsx after a full rebuild."
    assert document["notes"] == ["notes.txt", "roundtrip.xlsx", "roundtrip.applescript"]
    assert document["by_path"] == {"roundtrip.xlsx": {"sha256": "0" * 64}}
    for untouched in ("desktop_command", "automation", "edit", "saved_workbook_sha256", "status"):
        assert document[untouched] == original[untouched]

    manifest = json.loads(manifest_bytes)
    assert manifest["kind"] == "sanitized-receipt"
    assert manifest["receipt_sha256"] == hashlib.sha256(written).hexdigest() == result.receipt_sha256
    assert manifest["source_receipt_sha256"] == _sha(receipt_path) == result.source_receipt_sha256
    assert manifest["source_receipt_sha256"] != manifest["receipt_sha256"]
    assert manifest["workbook_sha256"] == _sha(workbook)
    assert manifest["workbook_bytes_unchanged"] is True
    assert manifest["workbook_warnings"] == [] and manifest["receipt_warnings"] == []
    by_location = {item["location"]: item for item in manifest["changes"]}
    assert set(by_location) == {
        "working_copy", "source_workbook", "contract", "reason", "notes[0]", "notes[1]", "notes[2]", "by_path[key]",
    }
    assert by_location["working_copy"]["kind"] == "basename"
    assert by_location["source_workbook"]["kind"] == "relative"
    assert by_location["contract"]["kind"] == "fingerprint"
    assert all(set(item) == {"location", "kind", "replacement"} for item in manifest["changes"])
    assert {change.original for change in result.changes} >= {f"{FAKE_RUN}/roundtrip.xlsx", str(workbook)}


@pytest.mark.parametrize("kind", ["empty", "populated", "file", "symlink"])
def test_existing_output_is_refused_before_anything_is_written(synthetic, tmp_path, monkeypatch, kind):
    root, receipt_path, workbook, _outside, _original = synthetic

    def forbidden(*_args, **_kwargs):
        raise AssertionError("an existing output must be refused before any copy or write")

    monkeypatch.setattr(sanitize_receipt.shutil, "copyfile", forbidden)
    out = tmp_path / "existing"
    target = tmp_path / "target"
    if kind == "file":
        out.write_text("keep file", encoding="utf-8")
    elif kind == "symlink":
        target.mkdir()
        (target / "sentinel").write_text("keep target", encoding="utf-8")
        out.symlink_to(target, target_is_directory=True)
    else:
        out.mkdir()
        if kind == "populated":
            (out / "sentinel").write_text("keep directory", encoding="utf-8")
    with pytest.raises(sanitize_receipt.SanitizeError, match="must be new"):
        sanitize_receipt.sanitize(receipt_path, workbook, out, root=root)
    if kind == "file":
        assert out.read_text(encoding="utf-8") == "keep file"
    elif kind == "populated":
        assert (out / "sentinel").read_text(encoding="utf-8") == "keep directory"
    elif kind == "symlink":
        assert out.is_symlink() and (target / "sentinel").read_text(encoding="utf-8") == "keep target"
    else:
        assert out.is_dir() and list(out.iterdir()) == []


def test_the_repository_root_is_refused_as_an_output(synthetic):
    root, receipt_path, workbook, _outside, _original = synthetic
    with pytest.raises(sanitize_receipt.SanitizeError, match="must be new"):
        sanitize_receipt.sanitize(receipt_path, workbook, ROOT, root=root)
    with pytest.raises(sanitize_receipt.SanitizeError, match="must be new"):
        sanitize_receipt.sanitize(receipt_path, workbook, ROOT.parent, root=root)


@pytest.mark.parametrize("name", ["receipt.json", "scenario-roundtrip.json", "red-flag-roundtrip.json"])
def test_committed_receipts_are_a_no_op(tmp_path, name):
    """The committed preflight package names no machine path, so nothing changes
    and the receipt bytes, hence its hash, are kept exactly."""
    out = tmp_path / "out"
    result = sanitize_receipt.sanitize(COMMITTED / name, COMMITTED_WORKBOOK, out)
    assert result.changes == []
    assert (out / name).read_bytes() == (COMMITTED / name).read_bytes()
    assert result.receipt_sha256 == result.source_receipt_sha256 == _sha(COMMITTED / name)
    assert _sha(out / COMMITTED_WORKBOOK.name) == _sha(COMMITTED_WORKBOOK) == result.workbook_sha256
    assert result.workbook_paths == [] and result.receipt_warnings == []
    manifest = json.loads((out / sanitize_receipt.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["changes"] == [] and manifest["receipt_sha256"] == _sha(COMMITTED / name)


def test_dry_run_reports_without_writing(synthetic, tmp_path):
    root, receipt_path, workbook, _outside, _original = synthetic
    out = tmp_path / "never-created"
    result = sanitize_receipt.sanitize(receipt_path, workbook, out, root=root, dry_run=True)
    assert result.changes and result.written is None
    assert not out.exists()
    without_out_dir = sanitize_receipt.sanitize(receipt_path, workbook, None, root=root, dry_run=True)
    assert [change.public() for change in without_out_dir.changes] == [change.public() for change in result.changes]
    with pytest.raises(sanitize_receipt.SanitizeError, match="--out-dir"):
        sanitize_receipt.sanitize(receipt_path, workbook, None, root=root)


def test_a_machine_path_inside_the_workbook_is_a_warning_never_an_edit(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    workbook = tiny_xlsx(root / "saved.xlsx", abs_path=f"{FAKE_RUN}/")
    receipt_path = root / "receipt.json"
    receipt_path.write_text(json.dumps({"status": "observed", "saved_workbook_sha256": _sha(workbook)}) + "\n")
    out = tmp_path / "out"
    result = sanitize_receipt.sanitize(receipt_path, workbook, out, root=root)
    assert result.workbook_paths == [("xl/workbook.xml", f"{FAKE_RUN}/")]
    assert result.workbook_warnings == ["xl/workbook.xml: machine-local path present; the workbook bytes were not altered"]
    assert _sha(out / "saved.xlsx") == _sha(workbook)
    manifest_bytes = (out / sanitize_receipt.MANIFEST_NAME).read_bytes()
    assert b"example-user" not in manifest_bytes
    assert json.loads(manifest_bytes)["workbook_warnings"] == result.workbook_warnings
    assert FAKE_RUN in sanitize_receipt.report(result, dry_run=False)


def test_a_workbook_the_receipt_does_not_name_is_a_warning(synthetic, tmp_path):
    root, receipt_path, _workbook, _outside, _original = synthetic
    other = tiny_xlsx(tmp_path / "other.xlsx", cell_value=2)
    assert _sha(other) != _original["saved_workbook_sha256"]
    result = sanitize_receipt.sanitize(receipt_path, other, tmp_path / "out", root=root)
    assert result.receipt_warnings == [
        "no workbook hash recorded in the receipt equals the workbook passed with --workbook",
    ]
    assert _sha(tmp_path / "out" / "other.xlsx") == _sha(other)


def test_command_line_exit_codes(synthetic, tmp_path, capsys):
    root, receipt_path, workbook, _outside, _original = synthetic
    out = tmp_path / "cli-out"
    argv = ["--receipt", str(receipt_path), "--workbook", str(workbook), "--out-dir", str(out), "--root", str(root)]
    assert sanitize_receipt.main(argv) == 0
    printed = capsys.readouterr().out
    assert f"rewrote working_copy: '{FAKE_RUN}/roundtrip.xlsx' -> 'roundtrip.xlsx' (basename)" in printed
    assert f"workbook roundtrip.xlsx sha256 {_sha(workbook)} (bytes unchanged)" in printed
    assert sanitize_receipt.main(argv) == 2
    assert "must be new" in capsys.readouterr().err
    assert sanitize_receipt.main(argv[:4] + ["--dry-run"]) == 0
    assert "dry run: nothing was written" in capsys.readouterr().out
