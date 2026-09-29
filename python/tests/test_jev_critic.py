"""The Jev critic's handling of its key, its requests and its scores.

Nothing here reaches the service or reads a real key. Requests go to a
stand-in opener, and every key is a made-up value.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "jev_critic.py"
WRAPPER = ROOT / "tools" / "with-typesafe-key.sh"
_spec = importlib.util.spec_from_file_location("jev_critic", TOOL)
critic = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(critic)

LABELS = ["excel_canonical_behavior", "artificial_local_profile", "syntax_or_spill_boundary"]
SETTINGS = ("TYPESAFE_API_KEY", "TYPESAFE_ENV_FILE", "TYPESAFE_KEY_COMMAND")


@pytest.fixture
def empty(tmp_path, monkeypatch):
    """A directory with no key anywhere in reach."""
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(critic, "ROOT", tmp_path / "repository")
    (tmp_path / "repository").mkdir()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


# Finding the key


def test_the_environment_comes_first(empty, monkeypatch):
    (empty / ".env").write_text("TYPESAFE_API_KEY=dummy-file\n")
    monkeypatch.setenv("TYPESAFE_API_KEY", "dummy-environment")
    assert critic.resolve_api_key() == "dummy-environment"


def test_the_order_of_the_files_and_the_command(empty, monkeypatch, tmp_path):
    named = tmp_path / "named.env"
    named.write_text("TYPESAFE_API_KEY=dummy-named\n")
    monkeypatch.setenv("TYPESAFE_ENV_FILE", str(named))
    monkeypatch.setenv("TYPESAFE_KEY_COMMAND", "printf dummy-command")
    (critic.ROOT / ".env").write_text("TYPESAFE_API_KEY=dummy-root\n")
    (empty / ".env").write_text("TYPESAFE_API_KEY=dummy-here\n")

    assert critic.resolve_api_key() == "dummy-here"
    (empty / ".env").unlink()
    assert critic.resolve_api_key() == "dummy-root"
    (critic.ROOT / ".env").unlink()
    assert critic.resolve_api_key() == "dummy-named"
    named.unlink()
    assert critic.resolve_api_key() == "dummy-command"


def test_a_file_with_no_key_is_passed_over(empty, monkeypatch, tmp_path):
    (empty / ".env").write_text("OTHER=1\nTYPESAFE_API_KEY=\n")
    named = tmp_path / "named.env"
    named.write_text("TYPESAFE_API_KEY=dummy-named\n")
    monkeypatch.setenv("TYPESAFE_ENV_FILE", str(named))
    assert critic.resolve_api_key() == "dummy-named"


@pytest.mark.parametrize(
    "line",
    [
        "TYPESAFE_API_KEY=dummy-123",
        'TYPESAFE_API_KEY="dummy-123"',
        "TYPESAFE_API_KEY='dummy-123'",
        "TYPESAFE_API_KEY=dummy-123\r",
        "TYPESAFE_API_KEY=dummy-123   ",
        "TYPESAFE_API_KEY=dummy-123 # the test key",
        'TYPESAFE_API_KEY="dummy-123" # the test key',
        "export TYPESAFE_API_KEY=dummy-123",
        "  TYPESAFE_API_KEY = dummy-123",
    ],
)
def test_a_line_of_a_key_file_as_it_is_usually_written(empty, line):
    (empty / ".env").write_bytes(b"# keys\nOTHER_TYPESAFE_API_KEY=wrong\n" + line.encode() + b"\n")
    assert critic.resolve_api_key() == "dummy-123"


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        ("printf 'dummy-123\\nrotated: 2026'", "one line"),
        ("printf 'dummy 123'", "one line"),
        ("printf 'locked: dummy-123'; exit 3", "failed"),
        ("sleep 30", "failed"),
    ],
)
def test_a_key_command_that_cannot_be_trusted_is_refused(empty, monkeypatch, command, reason):
    monkeypatch.setenv("TYPESAFE_KEY_COMMAND", command)
    monkeypatch.setattr(critic, "KEY_COMMAND_SECONDS", 0.5)
    with pytest.raises(critic.CriticError) as refused:
        critic.resolve_api_key()
    assert reason in str(refused.value)
    assert "dummy" not in str(refused.value)


def test_no_key_anywhere_is_said_plainly(empty):
    with pytest.raises(critic.CriticError, match="not found"):
        critic.resolve_api_key()


@pytest.mark.parametrize("key", ["dummy-123\nrotated", "dummy 123", "dummy-é", "dummy-☃"])
def test_a_key_given_by_the_caller_is_checked_and_not_shown(key):
    with pytest.raises(critic.CriticError) as refused:
        critic.JevClient(api_key=key)
    assert "dummy" not in str(refused.value)


# The wrapper


def _wrapped(directory, environment, *command):
    clean = {name: value for name, value in os.environ.items() if name not in SETTINGS}
    clean.update(environment, WORKBOOK_PYTHON=sys.executable)
    return subprocess.run(
        ["bash", str(WRAPPER), *command], cwd=directory, env=clean, capture_output=True, text=True, timeout=60
    )


SHOW = (sys.executable, "-c", "import os; print(os.environ['TYPESAFE_API_KEY'], os.getcwd())")


def test_the_wrapper_passes_the_key_and_keeps_the_directory(tmp_path):
    (tmp_path / ".env").write_text('export TYPESAFE_API_KEY="dummy-here" # the test key\n')
    done = _wrapped(tmp_path, {}, *SHOW)
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["dummy-here", str(tmp_path.resolve())]


def test_the_wrapper_refuses_what_the_tool_refuses(tmp_path):
    done = _wrapped(tmp_path, {"TYPESAFE_KEY_COMMAND": "printf 'locked: dummy-123'; exit 3"}, *SHOW)
    assert done.returncode == 2
    assert done.stdout == ""
    assert "TYPESAFE_KEY_COMMAND failed" in done.stderr and "dummy" not in done.stderr


def test_the_wrapper_returns_the_code_of_its_command(tmp_path):
    done = _wrapped(tmp_path, {"TYPESAFE_API_KEY": "dummy-123"}, sys.executable, "-c", "raise SystemExit(7)")
    assert done.returncode == 7


def test_the_wrapper_with_no_command_explains_itself(tmp_path):
    done = _wrapped(tmp_path, {"TYPESAFE_API_KEY": "dummy-123"})
    assert done.returncode == 2 and "usage" in done.stderr


# Requests


class Answer:
    def __init__(self, body):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


class Service:
    """Stands in for the opener. Each step is an answer to give or an error to raise."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests = []
        self.sleeps = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return Answer(step)

    def client(self):
        return critic.JevClient(api_key="dummy-123", opener=self, sleep=self.sleeps.append)


def _status(code):
    return urllib.error.HTTPError("https://service.invalid", code, "reason", {}, io.BytesIO(b"Bearer dummy-123"))


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_an_answer_that_will_not_change_is_asked_for_once(code):
    service = Service(_status(code), {"answers": {}})
    with pytest.raises(critic.CriticError, match=str(code)):
        service.client().ask({}, {})
    assert len(service.requests) == 1 and service.sleeps == []


def test_payment_required_is_asked_for_once():
    service = Service(_status(402), {"answers": {}})
    with pytest.raises(critic.CriticError, match="402"):
        service.client().ask({}, {})
    assert len(service.requests) == 1


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_an_answer_that_may_change_is_asked_for_again(code):
    service = Service(_status(code), _status(code), {"answers": {"q": 1}})
    assert service.client().ask({}, {}) == {"answers": {"q": 1}}
    assert len(service.requests) == 3 and service.sleeps == [1.5, 3.0]


def test_giving_up_does_not_wait_after_the_last_attempt():
    service = Service(*[_status(503)] * 4)
    with pytest.raises(critic.CriticError, match="after 4 attempts"):
        service.client().ask({}, {})
    assert len(service.requests) == 4 and service.sleeps == [1.5, 3.0, 4.5]


def test_a_request_that_timed_out_is_not_sent_again():
    for error in (TimeoutError("timed out"), urllib.error.URLError(TimeoutError("timed out"))):
        service = Service(error, {"answers": {}})
        with pytest.raises(critic.CriticError):
            service.client().ask({}, {})
        assert len(service.requests) == 1


def test_a_service_that_cannot_be_reached_is_tried_again():
    service = Service(urllib.error.URLError(ConnectionRefusedError("refused")), {"answers": {}})
    assert service.client().ask({}, {}) == {"answers": {}}


@pytest.mark.parametrize("body", [b"<html>busy</html>", b"[1, 2]", b"\xff\xfe"])
def test_an_answer_that_is_not_a_json_object_is_refused_once(body):
    service = Service(body, {"answers": {}})
    with pytest.raises(critic.CriticError, match="JSON"):
        service.client().ask({}, {})
    assert len(service.requests) == 1


def test_a_failure_that_names_the_key_does_not_show_it():
    service = Service(urllib.error.URLError("proxy refused Bearer dummy-123"), *[_status(503)] * 3)
    with pytest.raises(critic.CriticError) as failed:
        service.client().ask({}, {}, retries=1)
    assert "dummy-123" not in str(failed.value)


def test_a_redirect_is_refused_and_not_followed():
    request = critic.urllib.request.Request("https://service.invalid", data=b"{}", method="POST")
    assert critic._NoRedirect().redirect_request(request, None, 302, "Found", {}, "http://elsewhere.invalid") is None


# Scores


def _case(identifier, truth, control):
    return {
        "id": identifier,
        "is_false_control": control,
        "ground_truth": truth,
        "state": {"formula": "LEN"},
        "questions": {"behavior_provenance": {"type": "choice", "criteria": {label: {} for label in LABELS}}},
    }


def _benchmark(tmp_path, cases):
    path = tmp_path / "benchmark.json"
    path.write_text(json.dumps(cases))
    return path


def _answer(choice, probabilities):
    return {"answers": {"behavior_provenance": {"choice": choice, "probabilities": probabilities}}}


PAIR = [_case("real", LABELS[0], False), _case("control", LABELS[1], True)]
RIGHT = [_answer(LABELS[0], {LABELS[0]: 1.0}), _answer(LABELS[1], {LABELS[1]: 0.9, LABELS[0]: 0.1})]


def test_right_answers_pass_and_score_by_hand(tmp_path):
    report = critic.run_benchmark_eval(Service(*RIGHT).client(), _benchmark(tmp_path, PAIR))
    # First case 0. Second case (0.9-1)^2 + (0.1-0)^2 + 0 = 0.02. Mean 0.01.
    assert report["mean_brier_score"] == pytest.approx(0.01)
    assert report["accuracy"] == 1.0 and report["gate_passed"] is True


def test_a_wrong_answer_is_scored_against_every_label(tmp_path):
    wrong = _answer(LABELS[2], {LABELS[2]: 1.0})
    report = critic.run_benchmark_eval(Service(wrong, RIGHT[1]).client(), _benchmark(tmp_path, PAIR))
    # The truth has no probability given: (0-1)^2 + (1-0)^2 = 2.
    assert report["details"][0]["case_brier"] == pytest.approx(2.0)
    assert report["mean_brier_score"] == pytest.approx(1.01)
    assert report["gate_passed"] is False


@pytest.mark.parametrize(
    "answer",
    [
        {},
        {"answers": []},
        {"answers": {"behavior_provenance": None}},
        {"error": "overloaded"},
        _answer(LABELS[0], {}),
        _answer(LABELS[0], None),
        _answer("bogus_label", {LABELS[0]: 1.0}),
        _answer(LABELS[0], {"bogus_label": 1.0}),
        _answer(LABELS[0], {LABELS[0]: "1.0"}),
        _answer(LABELS[0], {LABELS[0]: True}),
        _answer(LABELS[0], {LABELS[0]: float("nan")}),
        _answer(LABELS[0], {LABELS[0]: 1.5, LABELS[1]: -0.5}),
        _answer(LABELS[0], {LABELS[0]: 0.4}),
    ],
)
def test_an_answer_that_cannot_be_scored_counts_as_the_worst_answer(tmp_path, answer):
    report = critic.run_benchmark_eval(Service(answer, RIGHT[1]).client(), _benchmark(tmp_path, PAIR))
    first = report["details"][0]
    assert first["case_brier"] == 2.0 and first["correct"] is False and first["malformed"]
    assert report["malformed_answers"] == 1 and report["gate_passed"] is False


def test_a_control_answered_wrongly_fails_the_gate_whatever_the_score(tmp_path):
    fooled = _answer(LABELS[0], {LABELS[0]: 0.34, LABELS[1]: 0.33, LABELS[2]: 0.33})
    report = critic.run_benchmark_eval(Service(RIGHT[0], fooled).client(), _benchmark(tmp_path, PAIR), 2.0)
    assert report["mean_brier_score"] <= 2.0 and report["gate_passed"] is False


@pytest.mark.parametrize(
    ("cases", "reason"),
    [([], "holds no cases"), ({"id": "not a list"}, "holds no cases"), ([PAIR[0]], "holds no control cases")],
)
def test_a_benchmark_that_proves_nothing_is_an_error_and_asks_nothing(tmp_path, cases, reason):
    service = Service()
    with pytest.raises(critic.CriticError, match=reason):
        critic.run_benchmark_eval(service.client(), _benchmark(tmp_path, cases))
    assert service.requests == []


def test_a_ground_truth_outside_the_labels_is_an_error_before_any_request(tmp_path):
    service = Service()
    with pytest.raises(critic.CriticError, match="ground truth"):
        critic.run_benchmark_eval(service.client(), _benchmark(tmp_path, [PAIR[1], _case("bad", "bogus", False)]))
    assert service.requests == []


@pytest.mark.parametrize("threshold", ["inf", "nan", "-1", "2.5", "high"])
def test_a_threshold_outside_the_scale_is_refused(threshold, capsys):
    with pytest.raises(SystemExit) as stopped:
        critic._main(["eval-benchmark", "--gate-threshold", threshold])
    assert stopped.value.code == 2
    assert "gate-threshold" in capsys.readouterr().err


def _run_tool(directory, *arguments):
    clean = {name: value for name, value in os.environ.items() if name not in SETTINGS}
    return subprocess.run(
        [sys.executable, str(TOOL), *arguments], cwd=directory, env=clean, capture_output=True, text=True, timeout=60
    )


def test_a_failure_is_one_line_and_code_two(tmp_path):
    done = _run_tool(tmp_path, "eval-benchmark", "--benchmark-file", str(tmp_path / "absent.json"))
    assert done.returncode == 2 and done.stdout == ""
    assert done.stderr.startswith("error: ") and "Traceback" not in done.stderr
    assert len(done.stderr.strip().splitlines()) == 1


# The benchmark that ships


def test_the_shipped_benchmark_is_laid_out_in_pairs():
    cases = json.loads((ROOT / "catalog" / "behavior_critic_benchmarks.json").read_text())
    by_fixture = {}
    for case in cases:
        labels = list(next(iter(case["questions"].values()))["criteria"])
        assert case["ground_truth"] in labels, case["id"]
        fixture = case["state"]["fixture"]
        by_fixture.setdefault(fixture["formula"], []).append(case["is_false_control"])
    assert all(sorted(pair) == [False, True] for pair in by_fixture.values()), by_fixture


def test_the_blank_cell_case_holds_a_blank_cell_and_not_empty_text():
    # In Excel an empty cell counts as 0 in arithmetic. A cell holding
    # zero-length text does not: ="" + 10 is #VALUE!.
    cases = json.loads((ROOT / "catalog" / "behavior_critic_benchmarks.json").read_text())
    blank = [case for case in cases if "blank" in case["id"]]
    assert len(blank) == 2
    for case in blank:
        assert case["state"]["fixture"]["cell_context"] == {"A1": None}
        assert "+" in case["state"]["fixture"]["formula"] and case["state"]["formula"] != "SUM"
