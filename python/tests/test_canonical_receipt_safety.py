"""Receipt output guards use disposable synthetic paths; deletion is forbidden."""
import pytest

from workbook_forge import canonical_roundtrip as receipt


@pytest.mark.parametrize("kind", ["empty", "populated", "previous_receipt", "file", "symlink", "dangling_symlink"])
def test_existing_output_is_refused_without_deletion_or_oracle(tmp_path, monkeypatch, kind):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("existing outputs must be refused before deletion or oracle access")
    monkeypatch.setattr(receipt.shutil, "rmtree", forbidden)
    monkeypatch.setattr(receipt, "_oracle", forbidden)
    output = tmp_path / "output"
    target = tmp_path / "synthetic_target"
    if kind == "file":
        output.write_text("keep file")
    elif kind in {"symlink", "dangling_symlink"}:
        if kind == "symlink":
            target.mkdir()
            (target / "sentinel").write_text("keep target")
        output.symlink_to(target, target_is_directory=True)
    else:
        output.mkdir()
        if kind == "populated":
            (output / "sentinel").write_text("keep directory")
        elif kind == "previous_receipt":
            (output / "receipt.json").write_text('{"prior": true}')
    with pytest.raises(FileExistsError, match="must be new"):
        receipt.build_canonical_receipt(tmp_path/"not-read.json", output, run_excel=True)
    if kind == "file":
        assert output.read_text() == "keep file"
    elif kind == "populated":
        assert (output/"sentinel").read_text() == "keep directory"
    elif kind == "previous_receipt":
        assert (output / "receipt.json").read_text() == '{"prior": true}'
    elif kind == "symlink":
        assert output.is_symlink() and (target/"sentinel").read_text() == "keep target"
    elif kind == "dangling_symlink":
        assert output.is_symlink() and not target.exists()
    else:
        assert output.is_dir() and list(output.iterdir()) == []


def test_bad_fixture_does_not_create_an_output_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(receipt, "_oracle", lambda: pytest.fail("oracle must not run"))
    fixture = tmp_path / "invalid.json"
    fixture.write_text("not JSON")
    output = tmp_path / "new-output"
    with pytest.raises(ValueError):
        receipt.build_canonical_receipt(fixture, output)
    assert not output.exists()
