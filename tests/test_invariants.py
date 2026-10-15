"""Independent checks of evidence boundaries, patch scope and regression gates."""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import time

import pytest

import testfix.orchestrator as orchestration
import testfix.runner as execution
from testfix.config import Settings
from testfix.datasets import Task
from testfix.orchestrator import execute_run, validated_cases
from testfix.policy import PolicyError, validate_candidate
from testfix.runner import Runner


def run_request(task_id="HumanEval/2", rounds=2):
    return {
        "id": "invariant-run",
        "task_ids": [task_id],
        "provider": "replay",
        "runner": "process",
        "max_rounds": rounds,
    }


class RecordingProvider:
    def __init__(self, candidates, cases):
        self.candidates = iter(candidates)
        self.cases = cases
        self.seen = []
        self.calls = []

    def invoke(self, role, context):
        self.seen.append((role, copy.deepcopy(context)))
        self.calls.append({"role": role, "provider": "test-double", "usage": {}})
        if role == "requirements":
            return {"requirements": ["Satisfy the public specification and preserve existing behavior"]}
        if role == "tester":
            return {"cases": copy.deepcopy(self.cases), "rationale": "Public specification examples"}
        if role == "diagnosis":
            return {"diagnosis": "The initial function lacks required behavior"}
        if role == "developer":
            return {"code": next(self.candidates), "reason": "Fixture candidate"}
        return {"approved": True, "reasons": ["Test-double review; execution gates remain authoritative"]}


@pytest.fixture
def synthetic_dataset(tmp_path):
    """Use recognizable sentinels so any oracle crossing is immediately visible."""
    destination = tmp_path / "data" / "humaneval"
    destination.mkdir(parents=True)
    acceptance_marker = "HIDDEN_ACCEPTANCE_SENTINEL_651f"
    reference_marker = "CANONICAL_REFERENCE_SENTINEL_184b"
    row = {
        "task_id": "HumanEval/2",
        "entry_point": "truncate_number",
        "prompt": "def truncate_number(number: float) -> float:\n    \"\"\"Return the decimal remainder of a positive number.\"\"\"\n",
        "test": f"# {acceptance_marker}\ndef check(candidate):\n    assert candidate(4.25) == 0.25\n",
        "canonical_solution": f"# {reference_marker}\n    return number - int(number)\n",
    }
    dataset = destination / "HumanEval.jsonl.gz"
    with gzip.open(dataset, "wt", encoding="utf-8") as stream:
        stream.write(json.dumps(row) + "\n")
    digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
    (destination / "manifest.json").write_text(json.dumps({"sha256": digest}), encoding="utf-8")
    return Settings(root=tmp_path), acceptance_marker, reference_marker, digest


@pytest.mark.parametrize("mutation", [
    "math.isclose = lambda a, b, **kwargs: True",
    "math.pi += 1",
    "del math.sqrt",
])
def test_candidate_cannot_mutate_imported_judge_dependencies(mutation):
    initial = "def f(x):\n    return x\n"
    candidate = "import math\ndef f(x):\n    " + mutation + "\n    return 999\n"
    with pytest.raises(PolicyError):
        validate_candidate(candidate, initial, "f")


def test_wrong_numeric_answer_cannot_poison_worker_comparator():
    initial = "def f(x):\n    return x\n"
    candidate = "import math\ndef f(x):\n    math.isclose = lambda a, b, **kwargs: True\n    return 999\n"
    try:
        result = Runner().evaluate(candidate, initial, "f", cases=[{"args": [1], "expected": 2}])
    except PolicyError:
        return  # Rejecting the candidate before execution satisfies the boundary.
    assert result["passed"] == 0


def test_wrong_answer_cannot_poison_shared_acceptance_module():
    initial = "def f(x):\n    return x\n"
    candidate = "import math\ndef f(x):\n    math.isclose = lambda a, b, **kwargs: True\n    return 999\n"
    acceptance = "import math\ndef check(candidate):\n    observed = candidate(1)\n    assert math.isclose(observed, 2)\n"
    try:
        result = Runner().evaluate(candidate, initial, "f", acceptance=acceptance)
    except PolicyError:
        return
    assert result["passed"] == 0


@pytest.mark.parametrize("binding", [
    "from math import floor as helper\n",
    "import math as helper\n",
    "from math import floor as f\n",
])
def test_import_alias_cannot_replace_locked_function(binding):
    initial = "def helper(x):\n    return x + 1\ndef f(x):\n    return helper(x)\n"
    with pytest.raises(PolicyError):
        validate_candidate(initial + binding, initial, "f")


@pytest.mark.parametrize("payload", [
    {"passed": 999, "total": 1, "passed_indices": [0], "failures": []},
    {"passed": -1, "total": 1, "passed_indices": [], "failures": []},
    {"passed": True, "total": 1, "passed_indices": [0], "failures": []},
    {"passed": 1, "total": 1, "passed_indices": [0], "failures": [{"index": 0, "error": "failed"}]},
])
def test_runner_rejects_fabricated_worker_counts(monkeypatch, payload):
    class ForgedWorker:
        returncode = 0

        def communicate(self, timeout=None):
            return json.dumps(payload).encode(), b""

    monkeypatch.setattr(execution.subprocess, "Popen", lambda *args, **kwargs: ForgedWorker())
    code = "def f(x):\n    return x\n"
    with pytest.raises(ValueError):
        Runner().evaluate(code, code, "f", cases=[{"args": [1], "expected": 1}])


def test_hidden_tests_and_reference_never_reach_model_or_reports(synthetic_dataset):
    settings, hidden_marker, reference_marker, original_digest = synthetic_dataset
    bad = "def truncate_number(number: float) -> float:\n    return 0.0\n"
    good = "def truncate_number(number: float) -> float:\n    return number - int(number)\n"
    provider = RecordingProvider([bad, good], [{"args": [4.25], "expected": 0.25}])
    result = execute_run(run_request(), settings, provider=provider)
    assert result["status"] == "completed"
    assert result["tasks"][0]["attempts"] == 2
    serialized_contexts = json.dumps(provider.seen)
    serialized_report = json.dumps(result)
    for marker in (hidden_marker, reference_marker):
        assert marker not in serialized_contexts
        assert marker not in serialized_report
        assert marker not in (settings.artifacts / result["id"] / "report.md").read_text(encoding="utf-8")
    dataset = settings.dataset / "HumanEval.jsonl.gz"
    assert hashlib.sha256(dataset.read_bytes()).hexdigest() == original_digest
    first_attempt = result["tasks"][0]["history"][0]
    assert first_attempt["acceptance"]["passed"] == 0
    assert first_attempt["acceptance"]["failures"] == [{"error": "AssertionError"}]


def test_every_previously_passing_regression_remains_passing(tmp_path, monkeypatch):
    """Equal aggregate scores must not hide a swapped pass/failure pair."""
    task = Task(
        "Synthetic/regression-swap", "f", "Return 999 for 2, preserve the existing answer 1 for 1.",
        "f", "def f(x):\n    return x\n",
        [{"args": [1], "expected": 1}, {"args": [2], "expected": 999}],
    )
    monkeypatch.setattr(orchestration, "select_tasks", lambda *args, **kwargs: [task])
    monkeypatch.setattr(orchestration, "hidden_tests", lambda *args: "def check(candidate):\n    assert candidate(2) == 999\n")
    provider = RecordingProvider(["def f(x):\n    return 999\n"], [{"args": [2], "expected": 999}])
    result = execute_run(run_request(task.id, rounds=1), Settings(root=tmp_path), provider=provider)
    task_result = result["tasks"][0]
    assert task_result["regression_baseline"]["passed"] == 1
    assert task_result["regression"]["passed"] == 1
    assert task_result["regression_breaks"] == 1
    assert task_result["status"] == "rejected"
    assert result["summary"]["accepted"] == 0


def test_policy_rejections_consume_budget_and_leave_dataset_unchanged(synthetic_dataset):
    settings, _, _, original_digest = synthetic_dataset
    forbidden = "import os\ndef truncate_number(number: float) -> float:\n    return 0.25\n"
    provider = RecordingProvider([forbidden] * 2, [{"args": [4.25], "expected": 0.25}])
    result = execute_run(run_request(rounds=2), settings, provider=provider)
    assert result["tasks"][0]["status"] == "rejected"
    assert result["tasks"][0]["attempts"] == 2
    assert sum(role == "developer" for role, _ in provider.seen) == 2
    assert not any(role == "reviewer" for role, _ in provider.seen)
    assert hashlib.sha256((settings.dataset / "HumanEval.jsonl.gz").read_bytes()).hexdigest() == original_digest


def test_nonterminating_candidate_is_stopped():
    code = "def f(x):\n    while True:\n        pass\n"
    started = time.monotonic()
    result = Runner(timeout=0.5).evaluate(code, code, "f", cases=[{"args": [1], "expected": 1}])
    assert result["passed"] == 0
    assert result["failures"][0]["error"] == "Execution timeout"
    assert time.monotonic() - started < 8


@pytest.mark.parametrize("case", [
    {"args": [1], "expected": 1, "tolerance": float("inf")},
    {"args": [1], "expected": 1, "tolerance": float("nan")},
    {"args": [1], "expected": float("nan")},
    {"args": [1], "expected": 1, "kwargs": {"__builtins__": {}}},
])
def test_generated_case_schema_rejects_nonfinite_and_private_inputs(case):
    with pytest.raises(ValueError):
        validated_cases({"cases": [case]})
