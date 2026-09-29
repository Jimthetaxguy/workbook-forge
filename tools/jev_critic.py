#!/usr/bin/env python3
"""tools/jev_critic.py — TypeSafe Jev System One critic for Workbook Forge.

Zero third-party runtime dependencies (Python standard library only).
Evaluates formula behavior profiles, intake layout topology, and autoresearch scope.

Usage:
  python3 tools/jev_critic.py audit-profile \
      --formula "SORT" --fixture '{"formula": "=SORT(A1:B3)", "result": {"error": "#VALUE!"}}'
  python3 tools/jev_critic.py classify-layout \
      --window-json '[[{"val":"Date","bold":true},{"val":"Amount","bold":true}],[{"val":"2025-01-01"},{"val":100}]]'
  python3 tools/jev_critic.py eval-benchmark \
      --benchmark-file catalog/behavior_critic_benchmarks.json --gate-threshold 0.15
  python3 tools/jev_critic.py run -- <command...>

Exit codes: 0 done or gate passed, 1 gate failed, 2 the tool could not do its work.
`run` exits with the code of the command it ran. When it cannot run the command it
exits with 125 (no key), 126 (the command cannot be run) or 127 (no such command).
"""
from __future__ import annotations

import argparse
import http.client
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "jev-latest"
DEFAULT_URL = "https://api.typesafe.ai/v1/systemone"
KEY_NAME = "TYPESAFE_API_KEY"
KEY_COMMAND_SECONDS = 5
# Answers that may come right on a second try. Anything else is final.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
# The longest wait the service may ask for with Retry-After, in seconds.
LONGEST_WAIT = 30.0
# The largest multi-class Brier score: all weight on one wrong label.
WORST_BRIER = 2.0


class CriticError(RuntimeError):
    """A failure that is reported in one line, never with the key in it."""

    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


def _checked_key(value: str, source: str) -> str:
    """Return the key, or an empty string when the source holds none."""
    key = value.strip()
    if any(not "!" <= character <= "~" for character in key):
        # The value is not shown: part of it may be the key.
        raise CriticError(f"the key from {source} must be one line of plain characters with no spaces")
    return key


def _key_from_env_file(path: Path) -> str:
    """Read KEY=value from a .env file. The last definition in the file counts.

    A value may be bare, or in single or double quotes, and may be followed
    by a comment. `export` may come first. Nothing is expanded, so a value
    that refers to another variable is refused, not used as it stands.
    """
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise CriticError(f"{path} could not be read: {type(error).__name__}") from None
    found = ""
    for number, line in enumerate(lines, start=1):
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, separator, value = line.partition("=")
        if not separator or name.strip() != KEY_NAME:
            continue
        where = f"{path} line {number}"
        value = value.strip()
        if value[:1] in ("'", '"'):
            closing = value.find(value[0], 1)
            if closing < 0:
                raise CriticError(f"{where}: the quote is not closed")
            rest = value[closing + 1:].strip()
            if rest and not rest.startswith("#"):
                raise CriticError(f"{where}: there is text after the closing quote")
            value = value[1:closing]
        else:
            value = value.split(" #", 1)[0].split("\t#", 1)[0]
        if "$" in value or "\\" in value or "`" in value:
            raise CriticError(f"{where}: the value needs a shell to read it, and none is used")
        found = _checked_key(value, where)
    return found


def _key_from_command(command: str) -> str:
    try:
        output = subprocess.check_output(
            command, shell=True, stderr=subprocess.DEVNULL, timeout=KEY_COMMAND_SECONDS
        )
    except (subprocess.SubprocessError, OSError) as error:
        # The command and its output may hold the key, so neither is shown.
        raise CriticError(f"TYPESAFE_KEY_COMMAND failed: {type(error).__name__}") from None
    try:
        return _checked_key(output.decode("utf-8"), "TYPESAFE_KEY_COMMAND")
    except UnicodeDecodeError:
        raise CriticError("TYPESAFE_KEY_COMMAND did not print text") from None


def resolve_api_key() -> str:
    """Resolve the key from the environment, a .env file, or a command.

    In order: TYPESAFE_API_KEY; .env in the current directory; .env in the
    repository root; the file named by TYPESAFE_ENV_FILE; the one line
    printed by the shell command in TYPESAFE_KEY_COMMAND. A file with no
    key in it is passed over. Nothing about one machine is written here.
    """
    key = _checked_key(os.environ.get(KEY_NAME, ""), KEY_NAME)
    if key:
        return key

    candidates = [Path.cwd() / ".env", ROOT / ".env"]
    if os.environ.get("TYPESAFE_ENV_FILE"):
        named = Path(os.environ["TYPESAFE_ENV_FILE"])
        if not named.is_file():
            raise CriticError("the file named by TYPESAFE_ENV_FILE does not exist")
        candidates.append(named)
    for candidate in candidates:
        if candidate.is_file():
            key = _key_from_env_file(candidate)
            if key:
                return key

    command = os.environ.get("TYPESAFE_KEY_COMMAND")
    if command:
        key = _key_from_command(command)
        if key:
            return key

    raise CriticError(
        f"{KEY_NAME} not found. Set it, put it in a .env file, or set TYPESAFE_KEY_COMMAND"
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: following one would send the key to another address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class JevClient:
    """Client for TypeSafe Jev System One REST API using pure Python standard library."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_URL,
        model: str = DEFAULT_MODEL,
        opener: Optional[Callable[..., Any]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.api_key = _checked_key(api_key, "the caller") if api_key else resolve_api_key()
        if not self.api_key:
            raise CriticError("the key is empty")
        self.base_url = base_url
        self.model = model
        self._open = opener or urllib.request.build_opener(_NoRedirect).open
        self._sleep = sleep

    def _without_key(self, text: object) -> str:
        return str(text).replace(self.api_key, "[key]")

    def ask(self, state: Any, questions: Dict[str, Any], retries: int = 4, timeout: float = 60.0) -> Dict[str, Any]:
        payload = json.dumps({"model": self.model, "state": state, "questions": questions}).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "WorkbookForge-JevCritic/1.0",
        }
        req = urllib.request.Request(self.base_url, data=payload, headers=headers, method="POST")
        if retries < 1:
            raise CriticError("at least one attempt is needed")
        failure = "no attempt was made"

        # Nothing the service or a proxy says is repeated in a message: it
        # may hold the key in a form that cannot be recognised and removed.
        for attempt in range(1, retries + 1):
            wait = 1.5 * attempt
            try:
                with self._open(req, timeout=timeout) as resp:
                    body = resp.read()
            except urllib.error.HTTPError as error:
                if error.code == 402:
                    raise CriticError("TypeSafe API: Payment Required (402)") from None
                failure = f"the service answered HTTP {int(error.code)}"
                if error.code not in RETRYABLE_STATUS:
                    raise CriticError(failure) from None
                asked = str(error.headers.get("Retry-After", "")) if error.headers else ""
                if asked.isdigit():
                    wait = min(float(asked), LONGEST_WAIT)
            except urllib.error.URLError as error:
                failure = f"the service could not be reached: {type(error.reason).__name__}"
                if not isinstance(error.reason, ConnectionRefusedError):
                    # Anything else may have happened after the request was
                    # received, so the request is not sent again.
                    raise CriticError(failure) from None
            except (OSError, http.client.HTTPException) as error:
                raise CriticError(f"the answer could not be read: {type(error).__name__}") from None
            else:
                try:
                    answer = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, ValueError, RecursionError):
                    raise CriticError("the service answered with something other than JSON") from None
                if not isinstance(answer, dict):
                    raise CriticError("the service answered with JSON that is not an object")
                return answer
            if attempt < retries:
                self._sleep(wait)

        raise CriticError(f"{failure} (after {retries} attempts)")

    def answer_to_show(self, response: Dict[str, Any], question: str) -> str:
        """The service's answer as text to print, or an error when it holds none."""
        answers = response.get("answers")
        if not isinstance(answers, dict) or not isinstance(answers.get(question), dict):
            raise CriticError("the service gave no answer to the question")
        return self._without_key(json.dumps(response, indent=2))


def audit_formula_profile(client: JevClient, formula_name: str, proposed_fixture: Dict[str, Any]) -> Dict[str, Any]:
    state = {
        "formula": formula_name,
        "fixture": proposed_fixture,
        "target": "Microsoft Excel 365 Desktop calculation engine",
    }
    questions = {
        "behavior_provenance": {
            "type": "choice",
            "instructions": f"Does this proposed behavior for {formula_name} match authentic Excel 365 Desktop behavior or an artificial engine profile?",
            "criteria": {
                "excel_canonical_behavior": {
                    "what": "Exact match with documented Microsoft Excel 365 calculation results or verified Excel behavior.",
                    "examples": ["SUM('1', 2) coerces numeric text in arguments to numbers", "='a'>1 returns TRUE"],
                    "not_for": "Local non-Excel approximations, standard IEEE float mismatch, or language exceptions."
                },
                "artificial_local_profile": {
                    "what": "A behavioral choice introduced by local engine design that conflicts with Excel Desktop.",
                    "examples": ["='a'>1 returning #VALUE!", "=0.1+0.2=0.3 returning FALSE", "LEN('😀') returning 1"],
                    "not_for": "Documented Excel error behaviors."
                },
                "syntax_or_spill_boundary": {
                    "what": "An unsupported boundary error returned because dynamic array spill placement or formula-cell context is unmodeled.",
                    "examples": ["Spill reference A1# unsupported", "Range in strictly scalar parameter returning local #VALUE!"],
                    "not_for": "Standard scalar functions returning normal calculation results."
                }
            }
        }
    }
    return client.ask(state, questions)


def classify_table_layout(client: JevClient, grid_window: List[Any]) -> Dict[str, Any]:
    state = {"grid_window": grid_window}
    questions = {
        "region_role": {
            "type": "choice",
            "instructions": "Classify the primary structural role of this tabular grid snippet.",
            "criteria": {
                "table_header_row": {
                    "what": "Row of distinct field names labeling the data columns beneath them.",
                    "examples": ["['Date', 'Transaction', 'Category', 'Amount']"],
                    "not_for": "Worksheet titles, KPI summary cards, or data rows."
                },
                "data_record_block": {
                    "what": "Homogeneous tabular rows representing business data records.",
                    "examples": ["['2025-01-01', 'Deposit', 'Revenue', 5000.00]"],
                    "not_for": "Summary rows with sums, averages, or header labels."
                },
                "aggregation_summary_row": {
                    "what": "Aggregation row summing or averaging prior rows.",
                    "examples": ["['Total', '', '', 124500.00]"],
                    "not_for": "Individual transaction lines."
                },
                "metadata_title_banner": {
                    "what": "Top-level metadata, sheet banner, report title, or standalone parameters.",
                    "examples": ["['Q4 2025 Financial Statement']"],
                    "not_for": "Multi-column grid headers."
                }
            }
        }
    }
    return client.ask(state, questions)


def _read_answer(response: Dict[str, Any], question: str, labels: List[str]) -> tuple[Any, Dict[str, float], str]:
    """Return the choice, a probability for every label, and what is wrong with the answer."""
    answers = response.get("answers")
    answer = answers.get(question) if isinstance(answers, dict) else None
    if not isinstance(answer, dict):
        return None, {}, "no answer to the question"
    choice = answer.get("choice")
    if choice not in labels:
        return choice, {}, "the choice is not one of the labels"
    given = answer.get("probabilities")
    if not isinstance(given, dict) or not given:
        return choice, {}, "no probabilities"
    if set(given) - set(labels):
        return choice, {}, "a probability is given for an unknown label"
    probabilities = {}
    for label in labels:
        value = given.get(label, 0.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
            return choice, {}, "a probability is not a number from 0 to 1"
        probabilities[label] = float(value)
    if abs(sum(probabilities.values()) - 1.0) > 0.01:
        return choice, {}, "the probabilities do not add up to 1"
    if probabilities[choice] < max(probabilities.values()):
        return choice, {}, "the choice is not the most probable label"
    return choice, probabilities, ""


def _gate_threshold(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number") from None
    if not 0.0 <= value <= WORST_BRIER:
        raise argparse.ArgumentTypeError(f"must be from 0 to {WORST_BRIER:g}")
    return value


def run_benchmark_eval(client: JevClient, benchmark_path: Path, gate_threshold: float = 0.15) -> Dict[str, Any]:
    try:
        cases = json.loads(benchmark_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as error:
        raise CriticError(f"{benchmark_path} could not be read as JSON: {type(error).__name__}") from None
    if not isinstance(cases, list) or not cases:
        raise CriticError(f"{benchmark_path} holds no cases")
    if not any(isinstance(case, dict) and case.get("is_false_control") for case in cases):
        raise CriticError(f"{benchmark_path} holds no control cases")
    if not math.isfinite(gate_threshold) or not 0.0 <= gate_threshold <= WORST_BRIER:
        raise CriticError(f"the gate threshold must be from 0 to {WORST_BRIER:g}")

    prepared = []
    for case in cases:
        try:
            cid, truth, state, questions = case["id"], case["ground_truth"], case["state"], case["questions"]
            question = next(iter(questions))
            labels = list(questions[question]["criteria"])
        except (KeyError, TypeError, StopIteration):
            raise CriticError(f"{benchmark_path} has a case that is not laid out as expected") from None
        if truth not in labels:
            raise CriticError(f"case {cid}: the ground truth is not one of its labels")
        prepared.append((cid, truth, state, questions, question, labels, bool(case.get("is_false_control", False))))

    results = []
    for cid, truth, state, questions, question, labels, is_control in prepared:
        choice, probabilities, malformed = _read_answer(client.ask(state, questions), question, labels)
        if malformed:
            # An answer that cannot be scored counts as the worst answer.
            case_brier = WORST_BRIER
        else:
            # Multi-class Brier score over every label: sum((p_k - y_k)^2).
            case_brier = sum((probabilities[label] - (1.0 if label == truth else 0.0)) ** 2 for label in labels)
        results.append({
            "id": cid,
            "correct": not malformed and choice == truth,
            "predicted": choice,
            "truth": truth,
            "case_brier": case_brier,
            "malformed": malformed,
            "is_false_control": is_control,
        })

    n = len(results)
    mean_brier = sum(r["case_brier"] for r in results) / n
    accuracy = sum(1 for r in results if r["correct"]) / n
    controls = [r for r in results if r["is_false_control"]]
    control_acc = sum(1 for r in controls if r["correct"]) / len(controls)
    # One control taken for real behaviour is the failure the gate exists to catch.
    passed = (mean_brier <= gate_threshold) and all(r["correct"] for r in controls)

    return {
        "total_cases": n,
        "accuracy": accuracy,
        "mean_brier_score": mean_brier,
        "gate_threshold": gate_threshold,
        "false_control_cases": len(controls),
        "false_control_rejection_rate": control_acc,
        "malformed_answers": sum(1 for r in results if r["malformed"]),
        "gate_passed": passed,
        "details": results
    }


def _json_argument(text: str, what: str) -> Any:
    try:
        return json.loads(text if text.lstrip()[:1] in ("{", "[") else Path(text).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as error:
        raise CriticError(f"{what} is neither JSON nor a file of JSON: {type(error).__name__}") from None


def run_command(command: List[str]) -> None:
    """Replace this process with `command`, with the key in its environment.

    The key goes into the environment and nowhere else: not into the
    command's arguments, where other users of the machine could read it.
    """
    if command[:1] == ["--"]:
        command = command[1:]
    if not command or not command[0]:
        raise CriticError("run needs a command, for example: run -- python3 tools/jev_critic.py --help", 125)
    environment = dict(os.environ)
    try:
        environment[KEY_NAME] = resolve_api_key()
    except CriticError as error:
        raise CriticError(str(error), 125) from None
    try:
        os.execvpe(command[0], command, environment)
    except FileNotFoundError:
        raise CriticError(f"{command[0]}: no such command", 127) from None
    except (OSError, ValueError) as error:
        raise CriticError(f"{command[0]} cannot be run: {type(error).__name__}", 126) from None


def _main(arguments: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="TypeSafe Jev System One critic for Workbook Forge.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    audit_p = sub.add_parser("audit-profile")
    audit_p.add_argument("--formula", required=True)
    audit_p.add_argument("--fixture", required=True, help="JSON string or file path")

    layout_p = sub.add_parser("classify-layout")
    layout_p.add_argument("--window-json", required=True, help="JSON string of 2D row array")

    eval_p = sub.add_parser("eval-benchmark")
    eval_p.add_argument("--benchmark-file", default=str(ROOT / "catalog" / "behavior_critic_benchmarks.json"))
    eval_p.add_argument("--gate-threshold", type=_gate_threshold, default=0.15)

    run_p = sub.add_parser("run", help="run a command with the key in its environment")
    run_p.add_argument("command", nargs=argparse.REMAINDER)

    args = parser.parse_args(arguments)

    if args.cmd == "run":
        run_command(args.command)
        return 0
    if args.cmd == "audit-profile":
        fixture_data = _json_argument(args.fixture, "--fixture")
        client = JevClient()
        print(client.answer_to_show(audit_formula_profile(client, args.formula, fixture_data), "behavior_provenance"))
        return 0
    if args.cmd == "classify-layout":
        window = _json_argument(args.window_json, "--window-json")
        client = JevClient()
        print(client.answer_to_show(classify_table_layout(client, window), "region_role"))
        return 0
    client = JevClient()
    res = run_benchmark_eval(client, Path(args.benchmark_file), args.gate_threshold)
    print(client._without_key(json.dumps(res, indent=2)))
    return 0 if res["gate_passed"] else 1


def main() -> None:
    try:
        sys.exit(_main())
    except CriticError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(error.code)


if __name__ == "__main__":
    main()
