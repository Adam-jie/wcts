"""Additional properties are real acceptance gates, independent of model tests."""
from __future__ import annotations

import copy
import json

import testfix.orchestrator as orchestration
from testfix.config import Settings
from testfix.datasets import Task
from testfix.orchestrator import execute_run
from testfix.reports import markdown
from testfix.runner import Runner


def test_extra_suite_can_reject_patch_despite_original_suite_passing(monkeypatch, tmp_path):
    task = Task("Synthetic/properties", "f", "Return the input", "f", "def f(x):\n    return 0\n", [{"args": [0], "expected": 0}])
    monkeypatch.setattr(orchestration, "select_tasks", lambda *args: [task])
    monkeypatch.setattr(orchestration, "hidden_tests", lambda *args: "def check(candidate):\n    assert candidate(1)==1\n")
    private_marker = "EVALUATOR_ONLY_PROPERTY_SENTINEL"
    extra = f"# {private_marker}\ndef check(candidate):\n    assert candidate(2)==2\n"
    class Provider:
        def __init__(self):
            self.calls = []
            self.seen = []
        def invoke(self, role, context):
            self.seen.append(copy.deepcopy(context))
            return {"requirements": ["Return input"], "cases": [{"args": [1], "expected": 1}], "diagnosis": "missing function",
                    "code": "def f(x):\n    return 0 if x==0 else 1\n", "approved": True, "reasons": []}
    provider = Provider()
    run = {"id": "extra-check", "task_ids": [task.id], "provider": "replay", "runner": "process", "max_rounds": 1}
    result = execute_run(run, Settings(root=tmp_path), provider=provider, extra_suites={task.id: extra})
    evidence = result["tasks"][0]
    assert evidence["acceptance"]["passed"] == 1
    assert evidence["generated"]["passed"] == 1
    assert evidence["extended"]["passed"] == 0
    assert evidence["status"] == "rejected"
    assert evidence["verdict"]["loads_and_is_callable"] is True
    assert evidence["verdict"]["satisfies_tested_requirements"] is False
    assert private_marker not in json.dumps(provider.seen)
    assert private_marker not in json.dumps(result)


def test_executed_property_metrics_and_unicode_round_trip():
    code = "def f(x):\n    return x\n"
    extra = "TESTFIX_METRICS = {'checks':0}\ndef check(candidate):\n    for value in ['中文', 'a', '']:\n        assert candidate(value)==value\n        TESTFIX_METRICS['checks'] += 1\n"
    result = Runner().evaluate(code, code, "f", acceptance=extra)
    assert result["metrics"]["checks"] == 3
    assert result["runnable"] is True
    assert result["passed"] == result["total"] == 1
    wrong = "def f(x):\n    return '错误'\n"
    failure = Runner().evaluate(wrong, code, "f", cases=[{"args": ["中文"], "expected": "中文"}])
    assert failure["passed"] == 0
    assert "错误" in failure["failures"][0]["actual"]


def test_session_report_distinguishes_generation_and_evaluation():
    run = {"id": "captured", "provider": "codex_session", "runner": "process", "status": "completed", "tasks": [],
           "provenance": {"model": "gpt-5.6-sol"}, "summary": {"total": 0, "accepted": 0, "regressions": 0, "duration_s": 1}}
    report = markdown(run)
    assert "本次 Codex 会话生成" in report
    assert "未直接调用 API" in report
    assert "仅为本地执行阶段" in report
    assert "以下结果仅证明工程流程" not in report


def test_captured_review_of_other_candidate_is_rejected(monkeypatch, tmp_path):
    task = Task("Synthetic/review-hash", "f", "Return input", "f", "def f(x):\n    return 0\n", [{"args": [0], "expected": 0}])
    monkeypatch.setattr(orchestration, "select_tasks", lambda *args: [task])
    monkeypatch.setattr(orchestration, "hidden_tests", lambda *args: "def check(candidate):\n    assert candidate(1)==1\n")
    class Provider:
        calls = []
        def invoke(self, role, context):
            return {"requirements": ["Return input"], "cases": [{"args": [1], "expected": 1}], "diagnosis": "missing behavior",
                    "code": "def f(x):\n    return x\n", "approved": True, "reasons": [], "reviewed_code_sha256": "0" * 64}
    result = execute_run({"id": "stale-review", "task_ids": [task.id], "provider": "replay", "runner": "process", "max_rounds": 1},
                         Settings(root=tmp_path), provider=Provider())
    assert result["tasks"][0]["status"] == "error"
    assert result["summary"]["accepted"] == 0
    assert "different candidate revision" in result["tasks"][0]["error"]

