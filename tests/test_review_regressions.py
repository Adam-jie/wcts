"""Reproductions for the final fresh-review findings."""
from __future__ import annotations

import json

import pytest

import testfix.orchestrator as orchestration
from testfix.config import Settings
from testfix.datasets import Task
from testfix.orchestrator import execute_run
from testfix.policy import PolicyError, validate_acceptance_code, validate_candidate
from testfix.runner import Runner
from testfix.tasks import import_repository


@pytest.mark.parametrize("judge", ["", "   \n", "def check(candidate):\n    pass\n", "def other(candidate):\n    assert candidate(1)==1\n"])
def test_repository_admission_rejects_missing_or_vacuous_judge(tmp_path, judge):
    (tmp_path / "source.py").write_text("def f(x):\n    return 0\n", encoding="utf-8")
    (tmp_path / "acceptance.py").write_text(judge, encoding="utf-8")
    manifest = {"id": "Repo/empty-judge", "title": "f", "source_file": "source.py", "acceptance_file": "acceptance.py",
                "entry_point": "f", "requirements": "Return x", "regression_cases": [{"args": [0], "expected": 0}]}
    path = tmp_path / "task.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="Independent acceptance"):
        import_repository(path, Settings(root=tmp_path))
    assert not (tmp_path / "data" / "repositories" / "empty-judge").exists()


def test_runner_empty_acceptance_is_not_zero_of_zero_success():
    source = "def f(x):\n    return x\n"
    with pytest.raises(ValueError, match="nonempty"):
        Runner().evaluate(source, source, "f", acceptance="")


def test_delegated_assertions_are_supported():
    validate_acceptance_code("def expect(candidate):\n    assert candidate(1)==1\ndef check(candidate):\n    expect(candidate)\n")


@pytest.mark.parametrize("binding", ["from math import ceil as floor", "import math as floor", "from math import floor\nfrom math import ceil as floor"])
def test_original_helper_dependency_binding_is_locked(binding):
    initial = "from math import floor\ndef helper(x):\n    return floor(x)\ndef f(x):\n    return helper(x)\n"
    candidate = binding + "\ndef helper(x):\n    return floor(x)\ndef f(x):\n    return int(x) if x >= 0 else helper(x)\n"
    with pytest.raises(PolicyError, match="import binding"):
        validate_candidate(candidate, initial, "f")


def test_zero_evidence_cannot_pass_supervisor_gate(monkeypatch, tmp_path):
    task = Task("Synthetic/empty", "f", "Return x", "f", "def f(x):\n    return 0\n", [{"args": [0], "expected": 0}])
    monkeypatch.setattr(orchestration, "select_tasks", lambda *args: [task])
    monkeypatch.setattr(orchestration, "hidden_tests", lambda *args: "def check(candidate):\n    assert candidate(2)==2\n")
    class Provider:
        calls = []
        def invoke(self, role, context):
            return {"requirements": ["Return x"], "cases": [{"args": [1], "expected": 1}], "diagnosis": "missing behavior",
                    "code": "def f(x):\n    return 0 if x==0 else 1\n", "approved": True, "reasons": []}
    class ZeroEvidenceRunner:
        def evaluate(self, code, initial, entry_point, *, cases=None, acceptance=None):
            if acceptance is not None:
                return {"passed": 0, "total": 0, "passed_indices": [], "failures": []}
            return {"passed": 1, "total": 1, "passed_indices": [0], "failures": []}
    result = execute_run({"id": "zero-evidence", "task_ids": [task.id], "provider": "replay", "runner": "process", "max_rounds": 1},
                         Settings(root=tmp_path), provider=Provider(), runner=ZeroEvidenceRunner())
    assert result["tasks"][0]["status"] == "rejected"
    assert result["summary"]["accepted"] == 0


@pytest.mark.parametrize("binding", ["def sum(values):\n    return 0\n", "from math import floor as sum\n", "from typing import List as list\n"])
def test_new_module_bindings_cannot_shadow_helper_builtins(binding):
    initial = "def helper(values):\n    return sum(list(values))\ndef f(values):\n    return helper(values)\n"
    with pytest.raises(PolicyError, match="shadow safe builtins"):
        validate_candidate(initial + binding, initial, "f")

