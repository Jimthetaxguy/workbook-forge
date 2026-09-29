"""The review tools decide which findings and verdicts are trusted, so they are tested too."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools" / "review"


def _load(name: str):
    sys.path.insert(0, str(TOOLS))
    try:
        spec = importlib.util.spec_from_file_location(f"review_{name}", TOOLS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(TOOLS))


packets = _load("build_packet")
repro = _load("run_repro")
ledger = _load("ledger")
canaries = _load("canaries")


def _git(directory: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(directory), "-c", "user.name=test", "-c", "user.email=test@example.invalid", *arguments],
        check=True, capture_output=True,
    )


@pytest.fixture
def checkout(tmp_path):
    source = tmp_path / "source"
    (source / "docs/specs").mkdir(parents=True)
    (source / "schemas").mkdir()
    (source / "python/workbook_forge").mkdir(parents=True)
    (source / "docs/specs/red-flags.md").write_text("---\nstatus: active\nauthor: someone\n---\n# Spec\nRule one.\n")
    (source / "docs/run-history.md").write_text("# History\nEverything was verified.\n")
    (source / "schemas/workbook-model.v1.schema.json").write_text("{}\n")
    (source / "python/workbook_forge/model.py").write_text("LIMIT = 10\n\n\ndef within(value):\n    return value <= LIMIT\n")
    (source / ".gitignore").write_text("__pycache__/\n")
    _git(source, "init", "-q")
    _git(source, "add", ".gitignore", "docs", "schemas", "python")
    _git(source, "commit", "-q", "-m", "initial")
    return source


def _finding(**changes) -> dict:
    finding = {
        "id": "contract-01",
        "lens": "contract",
        "base": "main",
        "severity": "high",
        "claim": "Hydration accepts a workbook whose model_version is a string.",
        "expectation": "E3",
        "location": {"file": "python/workbook_forge/model.py", "line_start": 4, "line_end": 5},
        "evidence": {"command": "python -c \"print(1)\"", "output": "1\n", "exit_code": 0},
        "attacks_attempted": ["passed a string version", "passed a float version"],
        "status": "OPEN",
        "contexts": {"finder": "reviewer-contract"},
    }
    finding.update(changes)
    return finding


# Packets


def test_stage_one_packet_holds_the_contract_and_not_the_code(checkout, tmp_path):
    packet = tmp_path / "packet"
    manifest = packets.build(checkout, "contract", 1, packet)
    assert sorted(manifest["files"]) == ["docs/specs/red-flags.md", "schemas/workbook-model.v1.schema.json"]
    assert not (packet / "python").exists()
    assert not (packet / "docs/run-history.md").exists()
    assert packets.verify(packet, "contract", 1) == []


def test_status_blocks_are_removed_when_a_document_is_copied(checkout, tmp_path):
    packet = tmp_path / "packet"
    packets.build(checkout, "contract", 1, packet)
    assert (packet / "docs/specs/red-flags.md").read_text() == "# Spec\nRule one.\n"


def test_stage_two_adds_the_code(checkout, tmp_path):
    packet = tmp_path / "packet"
    manifest = packets.build(checkout, "contract", 2, packet)
    assert "python/workbook_forge/model.py" in manifest["files"]


def test_the_map_beside_the_packet_names_the_commit_and_the_packet_does_not(checkout, tmp_path):
    packet = tmp_path / "packet"
    packets.build(checkout, "contract", 1, packet)
    assert len(json.loads((tmp_path / "packet.map.json").read_text())["tip"]) == 40
    assert "tip" not in json.loads((packet / "manifest.json").read_text())


@pytest.mark.parametrize(
    ("path", "content", "reason"),
    [
        (".git/HEAD", "ref: refs/heads/main\n", "version-control data"),
        ("docs/run-history.md", "# History\n", "outside what lens"),
        ("docs/specs/extra.md", "---\nstatus: accepted\n---\n# Extra\n", "status block"),
    ],
)
def test_verify_reports_what_does_not_belong(checkout, tmp_path, path, content, reason):
    packet = tmp_path / "packet"
    packets.build(checkout, "contract", 1, packet)
    (packet / path).parent.mkdir(parents=True, exist_ok=True)
    (packet / path).write_text(content)
    problems = packets.verify(packet, "contract", 1)
    assert len(problems) == 1 and reason in problems[0]


def test_verify_refuses_links(checkout, tmp_path):
    packet = tmp_path / "packet"
    packets.build(checkout, "contract", 1, packet)
    (packet / "schemas/link.json").symlink_to(checkout / "docs/run-history.md")
    assert any("links are not allowed" in problem for problem in packets.verify(packet, "contract", 1))


def test_check_shows_a_file_the_reviewer_changed(checkout, tmp_path):
    packet = tmp_path / "packet"
    packets.build(checkout, "contract", 2, packet)
    assert packets.check(packet) == []
    (packet / "python/workbook_forge/model.py").write_text("LIMIT = 11\n")
    assert packets.check(packet) == ["python/workbook_forge/model.py: changed"]


def test_a_star_does_not_cross_directories():
    assert packets.matches("rust/src/lib.rs", ["rust/src/*.rs"])
    assert not packets.matches("rust/src/nested/lib.rs", ["rust/src/*.rs"])
    assert packets.matches("receipts/a/b.json", ["receipts/**"])


def test_an_unknown_lens_is_an_error(checkout, tmp_path):
    with pytest.raises(packets.PacketError):
        packets.build(checkout, "praise", 1, tmp_path / "packet")


# Reproductions


@pytest.mark.parametrize(
    "command",
    [
        "rm -r build",
        "git log",
        "curl https://example.invalid",
        "/usr/bin/osascript -e 'tell application \"Finder\" to beep'",
        "python tools/excel_oracle.py --excel",
        "cat ../secrets",
        "cat /etc/passwd",
        "open -a Calculator",
    ],
)
def test_commands_that_reach_outside_are_refused(tmp_path, command):
    record = repro.run(command, tmp_path, timeout=5, memory_mb=256)
    assert record["refused"] and record["exit_code"] is None


def test_a_plain_command_runs_and_its_output_is_kept(tmp_path):
    record = repro.run(f"{sys.executable} -c \"print(open.__name__)\"", tmp_path, timeout=20, memory_mb=512)
    assert record["refused"] is None
    assert record["exit_code"] == 0 and record["output"] == "open\n"


def test_a_command_that_hangs_is_stopped(tmp_path):
    record = repro.run(f"{sys.executable} -c \"import time; time.sleep(60)\"", tmp_path, timeout=1, memory_mb=512)
    assert record["timed_out"] and record["exit_code"] != 0
    assert record["seconds"] < 10


def test_a_command_that_takes_too_much_memory_is_stopped(tmp_path):
    hungry = f"{sys.executable} -c \"import time; block = bytearray(400 * 1024 * 1024); time.sleep(30)\""
    record = repro.run(hungry, tmp_path, timeout=20, memory_mb=100)
    assert record["memory_exceeded"] and not record["timed_out"]


# Findings


def test_a_complete_finding_is_accepted(tmp_path):
    assert ledger.validate([_finding()], tmp_path) == []


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"strengths": "well structured"}, "Additional properties"),
        ({"attacks_attempted": []}, "should be non-empty"),
        ({"evidence": {"command": "python -c 1", "output": ""}}, "should be non-empty"),
        ({"evidence": {"command": "git log", "output": "x"}}, "uses 'git'"),
        ({"evidence": {"command": "python t.py --excel", "output": "x"}}, "mentions Excel"),
        ({"location": {"file": "/etc/passwd", "line_start": 1, "line_end": 1}}, "inside the packet"),
        ({"location": {"file": "../x.py", "line_start": 1, "line_end": 1}}, "inside the packet"),
        ({"location": {"file": "a.py", "line_start": 9, "line_end": 2}}, "ends before it starts"),
        ({"claim": "Fix it like this:\n--- a/model.py\n+++ b/model.py\n@@ -1 +1 @@\n"}, "contains a patch"),
        ({"status": "CONFIRMED"}, "coordinator's own reproduction"),
        ({"status": "REFUTED"}, "needs the reason"),
        ({"contexts": {"finder": "one", "fixer": "one"}}, "different contexts"),
    ],
)
def test_a_finding_that_cannot_be_checked_is_rejected(tmp_path, changes, reason):
    problems = ledger.validate([_finding(**changes)], tmp_path)
    assert problems and any(reason in problem for problem in problems), problems


def test_an_id_may_be_used_once(tmp_path):
    assert ledger.validate([_finding(), _finding()], tmp_path) == ["contract-01: id is used twice"]


def test_merge_keeps_one_copy_of_a_defect_and_orders_by_severity(tmp_path):
    first = _finding()
    same_defect = _finding(id="boundary-01", lens="boundary")
    worse = _finding(id="parity-01", lens="parity", severity="critical", claim="Python and Rust disagree on a blank operand.")
    (tmp_path / "a.jsonl").write_text(json.dumps(first) + "\n" + json.dumps(worse) + "\n")
    (tmp_path / "b.jsonl").write_text(json.dumps(same_defect) + "\n")
    assert [item["id"] for item in ledger.merge(tmp_path)] == ["parity-01", "contract-01"]


def test_attestation_is_a_hash_and_depends_on_content(tmp_path):
    (tmp_path / "rules.md").write_text("build fast\n")
    first = ledger.attest([tmp_path / "rules.md"])
    (tmp_path / "rules.md").write_text("review hard\n")
    assert len(first) == 64 and first != ledger.attest([tmp_path / "rules.md"])


# Planted defects

DEFECT = {
    "id": "limit-off-by-one",
    "file": "python/workbook_forge/model.py",
    "find": "value <= LIMIT",
    "replace": "value < LIMIT",
    "why": "The limit itself must be accepted.",
}


def test_a_defect_must_match_exactly_once():
    with pytest.raises(canaries.DefectError):
        canaries.apply("a = 1\na = 1\n", {**DEFECT, "find": "a = 1"})
    with pytest.raises(canaries.DefectError):
        canaries.apply("a = 1\n", {**DEFECT, "find": "b = 2"})


def test_apply_reports_the_line_it_changed():
    changed, line = canaries.apply("LIMIT = 10\n\n\ndef within(value):\n    return value <= LIMIT\n", DEFECT)
    assert line == 5 and "value < LIMIT" in changed


def test_planting_changes_the_packet_and_records_it_outside(checkout, tmp_path):
    packet = tmp_path / "packet"
    packets.build(checkout, "contract", 2, packet)
    results = [{**DEFECT, "survived": True}, {**DEFECT, "id": "caught", "survived": False}]
    planted = canaries.plant(packet, results, limit=3)
    assert planted == [{"id": "limit-off-by-one", "file": DEFECT["file"], "line": 5, "lens": "contract"}]
    assert "value < LIMIT" in (packet / DEFECT["file"]).read_text()
    assert packets.check(packet) == []
    assert not list(packet.rglob("*planted*"))
    assert json.loads((tmp_path / "packet.planted.json").read_text()) == planted


def test_a_reviewer_who_finds_nothing_planted_is_not_trusted():
    planted = [{"id": "limit-off-by-one", "file": DEFECT["file"], "line": 5, "lens": "contract"}]
    elsewhere = _finding(location={"file": DEFECT["file"], "line_start": 40, "line_end": 41})
    report = canaries.score([elsewhere], planted)
    assert report["contract"] == {
        "planted": 1, "found": 0, "missed": ["limit-off-by-one"], "clean_verdicts_trusted": False,
    }
    assert canaries.score([_finding()], planted)["contract"]["clean_verdicts_trusted"]


def test_running_defects_leaves_the_checkout_as_it_was(checkout):
    (checkout / "python/tests").mkdir()
    (checkout / "python/tests/test_limit.py").write_text(
        "import sys\nsys.path.insert(0, 'python/workbook_forge')\nimport model\n\n\n"
        "def test_below():\n    assert model.within(3)\n"
    )
    _git(checkout, "add", "python/tests/test_limit.py")
    _git(checkout, "commit", "-q", "-m", "test")
    before = (checkout / DEFECT["file"]).read_text()
    results = canaries.run(checkout, [DEFECT], Path(sys.executable))
    assert results[0]["survived"] and results[0]["caught_by"] == []
    assert (checkout / DEFECT["file"]).read_text() == before


# Weaknesses found when these tools were themselves reviewed


@pytest.mark.parametrize(
    "command",
    [
        "python3 tools/excel_oracle.py --run-excel",
        "python3 tools/excel_oracle.py --run-excel --live-scenario",
        "python3 tools/canonical_excel_receipt.py --fixture f.json",
        'cat "$HOME/code/repo/CONTEXT.md"',
        "cd ~ && cat code/repo/CONTEXT.md",
        "cat `echo /etc/passwd`",
        "cat $(echo /etc/passwd)",
        "cat ..",
        "python3.13 -c 'unterminated",
    ],
)
def test_more_commands_that_reach_outside_are_refused(tmp_path, command):
    assert repro.refusal(command, tmp_path) is not None


@pytest.mark.parametrize(
    "command",
    [
        'grep -n -A9 "Scalar::Error { code, .. } => Value::Error" rust/src/model.rs',
        "PYTHONPATH=python python3.13 -B _scratch/check.py",
        "PYTHONPATH=python python3.13 -m pytest -q -o pythonpath=_scratch/variant_01 python/tests/test_x.py",
        "python3.13 -c \"print('a..b')\"",
    ],
)
def test_ordinary_review_commands_are_allowed(tmp_path, command):
    assert repro.refusal(command, tmp_path) is None


def test_a_finding_that_cites_a_whole_file_earns_no_planted_defect():
    planted = [{"id": "limit-off-by-one", "file": DEFECT["file"], "line": 5, "lens": "contract"}]
    whole_file = _finding(location={"file": DEFECT["file"], "line_start": 1, "line_end": 99999})
    report = canaries.score([whole_file], planted)
    assert report["contract"]["found"] == 0
    assert not report["contract"]["clean_verdicts_trusted"]


def test_a_refuted_finding_earns_no_planted_defect():
    planted = [{"id": "limit-off-by-one", "file": DEFECT["file"], "line": 5, "lens": "contract"}]
    refuted = _finding(status="REFUTED", refutation="does not reproduce")
    assert canaries.score([refuted], planted)["contract"]["found"] == 0


def test_check_notices_a_rewritten_manifest(checkout, tmp_path):
    packet = tmp_path / "packet"
    packets.build(checkout, "contract", 2, packet)
    target = packet / "python/workbook_forge/model.py"
    target.write_text("LIMIT = 11\n")
    manifest = json.loads((packet / "manifest.json").read_text())
    manifest["files"]["python/workbook_forge/model.py"] = packets.digest(target.read_bytes())
    (packet / "manifest.json").write_text(json.dumps(manifest))
    assert packets.check(packet) == ["manifest.json: changed since the packet was built"]


def test_check_notices_added_and_removed_files(checkout, tmp_path):
    packet = tmp_path / "packet"
    packets.build(checkout, "contract", 2, packet)
    (packet / "notes.md").write_text("extra\n")
    (packet / "_scratch").mkdir()
    (packet / "_scratch/experiment.py").write_text("print(1)\n")
    (packet / "schemas/workbook-model.v1.schema.json").unlink()
    assert packets.check(packet) == [
        "notes.md: added",
        "schemas/workbook-model.v1.schema.json: removed",
    ]


def test_planting_keeps_the_manifest_record_current(checkout, tmp_path):
    packet = tmp_path / "packet"
    packets.build(checkout, "contract", 2, packet)
    canaries.plant(packet, [{**DEFECT, "survived": True}], limit=3)
    assert packets.check(packet) == []
