"""Supervisor state machine: explicit evidence gates and bounded repair loop."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Callable

from .config import Settings
from .policy import PolicyError, validate_candidate
from .providers import ModelProvider
from .reports import now, save_run
from .runner import Runner
from .tasks import hidden_tests, select_tasks


def validated_cases(response: dict) -> list[dict]:
    cases = response.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 30:
        raise ValueError("Tester must return 1-30 input/expected cases")
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("args"), list) or ("expected" in case) == ("raises" in case):
            raise ValueError("Invalid generated test schema")
        if "raises" in case and case["raises"] not in {"ValueError", "TypeError", "IndexError", "ZeroDivisionError"}:
            raise ValueError("Unsupported expected exception")
        if not isinstance(case.get("kwargs", {}), dict) or any(not isinstance(key, str) or key.startswith("_") for key in case.get("kwargs", {})):
            raise ValueError("Invalid test keyword arguments")
        tolerance = case.get("tolerance", 1e-8)
        if not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool) or not 0 <= tolerance <= 1e-5:
            raise ValueError("Tolerance must be between 0 and 1e-5")
    serialized = json.dumps(cases, allow_nan=False)
    if len(serialized) > 30_000:
        raise ValueError("Generated tests exceed 30 KB")
    return json.loads(serialized)


def regression_breaks(before: dict, after: dict) -> int:
    def passed_indices(value):
        if "passed_indices" in value:
            return set(value["passed_indices"])
        if value["passed"] == 0:
            return set()
        failures = {item["index"] for item in value.get("failures", []) if "index" in item}
        return set(range(value["total"])) - failures
    return len(passed_indices(before) - passed_indices(after))


def execute_run(run: dict, settings: Settings, persist: Callable[[dict], None] | None = None,
                provider: ModelProvider | None = None, runner: Runner | None = None,
                extra_suites: dict[str, str] | None = None) -> dict:
    started = time.monotonic()
    directory = settings.artifacts / run["id"]

    def checkpoint():
        run["updated_at"] = now()
        save_run(directory, run)
        if persist:
            persist(run)

    def event(task_id, agent, stage, message):
        if run.get("provenance", {}).get("execution_mode") == "captured_codex_session":
            captured_messages = {
                "analyze": "读取本次 GPT-5.6 会话已保存的公开需求分析",
                "generate": "读取本次 GPT-5.6 会话已保存的自动化测试与测试步骤",
                "diagnose": "读取本次 GPT-5.6 会话已保存的缺口诊断；失败证据已在本地复现",
                "patch": "读取本次 GPT-5.6 会话已保存的候选补丁；执行修改权限检查",
                "review": "读取已保存的 GPT-5.6 独立评审并核对候选代码哈希",
            }
            message = captured_messages.get(stage, message)
        run["events"].append({"time": now(), "task_id": task_id, "agent": agent, "stage": stage, "message": message})
        checkpoint()

    run.update(status="running", execution_owner_pid=os.getpid(), events=[], tasks=[], summary={"total": len(run["task_ids"]), "accepted": 0, "regressions": 0, "duration_s": 0})
    run["provenance"] = {"prompt_contract": "testfix.role.v1", "model": settings.model if run["provider"] == "openai" else settings.ollama_model if run["provider"] == "ollama" else run["provider"],
                         "replay_is_not_model_evaluation": run["provider"] == "replay", "acceptance_unit": "whole task suite",
                         "regression_policy": "each previously passing case remains passing", "max_rounds": run["max_rounds"]}
    if (settings.dataset / "manifest.json").exists():
        run["provenance"]["dataset"] = json.loads((settings.dataset / "manifest.json").read_text(encoding="utf-8"))
    if provider and hasattr(provider, "metadata"):
        run["provenance"].update(provider.metadata())
    checkpoint()
    try:
        tasks = select_tasks(settings, run["task_ids"])
        provider = provider or ModelProvider(run["provider"], settings)
        if hasattr(provider, "metadata"):
            run["provenance"].update(provider.metadata())
        runner = runner or Runner(run["runner"], settings.docker_image)
        for task in tasks:
            task_started = time.monotonic()
            result = {"id": task.id, "title": task.title, "status": "running", "attempts": 0, "requirements": [],
                      "source": task.source, "kind": task.kind, "history": []}
            run["tasks"].append(result)
            code = task.initial_code
            base = {"task_id": task.id, "entry_point": task.entry_point, "specification": task.requirements, "code": code}
            try:
                event(task.id, "requirements", "analyze", "对照公开需求分析现有实现和需要保持的接口")
                plan = provider.invoke("requirements", base)
                if not isinstance(plan.get("requirements"), list) or not plan["requirements"] or any(not isinstance(x, str) for x in plan["requirements"]):
                    raise ValueError("Requirements agent returned invalid requirements")
                result["requirements"] = plan["requirements"]
                hidden = hidden_tests(settings, task)
                locked_hash = hashlib.sha256(hidden.encode()).hexdigest()
                result["acceptance_sha256"] = locked_hash
                result["baseline"] = runner.evaluate(code, task.initial_code, task.entry_point, acceptance=hidden)
                extra = (extra_suites or {}).get(task.id)
                if extra is not None:
                    result["extended_sha256"] = hashlib.sha256(extra.encode()).hexdigest()
                    result["extended_baseline"] = runner.evaluate(code, task.initial_code, task.entry_point, acceptance=extra)
                regression_before = runner.evaluate(code, task.initial_code, task.entry_point, cases=task.regression_cases)
                result["regression_baseline"] = regression_before
                event(task.id, "tester", "generate", "生成需求导出的测试数据；独立验收保持在评测端")
                tests = provider.invoke("tester", {**base, "requirements": result["requirements"]})
                cases = validated_cases(tests)
                result["test_cases"] = cases
                result["test_rationale"] = tests.get("rationale", "")
                result["test_steps"] = tests.get("steps", [])
                result["requirement_coverage"] = tests.get("coverage", [])
                result["test_assumptions"] = tests.get("assumptions", [])
                result["generated_baseline"] = runner.evaluate(code, task.initial_code, task.entry_point, cases=cases)
                feedback = result["generated_baseline"]
                event(task.id, "diagnosis", "diagnose", f"新增测试暴露 {feedback['total'] - feedback['passed']} 个失败；记录功能缺口")
                diagnosis = provider.invoke("diagnosis", {**base, "requirements": result["requirements"], "cases": cases, "test_result": feedback})
                if not isinstance(diagnosis.get("diagnosis"), str):
                    raise ValueError("Diagnosis agent returned invalid diagnosis")
                result["diagnosis"] = diagnosis["diagnosis"]
                for attempt in range(1, run["max_rounds"] + 1):
                    result["attempts"] = attempt
                    event(task.id, "developer", "patch", f"生成第 {attempt} 轮补丁；只允许目标实现和新增辅助函数")
                    proposal = provider.invoke("developer", {**base, "code": code, "requirements": result["requirements"],
                                                              "diagnosis": result["diagnosis"], "cases": cases, "test_result": feedback})
                    candidate = proposal.get("code")
                    if not isinstance(candidate, str):
                        raise ValueError("Developer must return complete module source")
                    try:
                        validate_candidate(candidate, task.initial_code, task.entry_point)
                    except PolicyError as exc:
                        feedback = {"error": str(exc), "stage": "patch_policy"}
                        result["history"].append({"attempt": attempt, "policy_error": str(exc)})
                        event(task.id, "verifier", "rejected", str(exc))
                        continue
                    code = candidate
                    result["patch"] = code
                    result["diff"] = "".join(difflib.unified_diff(task.initial_code.splitlines(keepends=True), code.splitlines(keepends=True),
                                                                 fromfile="a/candidate.py", tofile="b/candidate.py"))
                    event(task.id, "verifier", "verify", "执行生成测试、固定独立验收和原有回归")
                    generated = runner.evaluate(code, task.initial_code, task.entry_point, cases=cases)
                    independent = runner.evaluate(code, task.initial_code, task.entry_point, acceptance=hidden)
                    regression = runner.evaluate(code, task.initial_code, task.entry_point, cases=task.regression_cases)
                    extended = runner.evaluate(code, task.initial_code, task.entry_point, acceptance=extra) if extra is not None else None
                    if hashlib.sha256(hidden_tests(settings, task).encode()).hexdigest() != locked_hash:
                        raise ValueError("Independent acceptance integrity changed")
                    result.update(generated=generated, acceptance=independent, regression=regression)
                    if extended is not None:
                        result["extended"] = extended
                    # Full upstream assertions and canonical solutions never cross this boundary.
                    review_context = {**base, "code": code, "requirements": result["requirements"], "cases": cases,
                                      "generated": generated, "regression": regression,
                                      "independent_suite_passed": independent["total"] > 0 and independent["passed"] == independent["total"]}
                    if extended is not None:
                        review_context["extended_suite_passed"] = extended["total"] > 0 and extended["passed"] == extended["total"]
                    event(task.id, "reviewer", "review", "依据候选代码与验证证据进行独立评审")
                    review = provider.invoke("reviewer", review_context)
                    if type(review.get("approved")) is not bool or not isinstance(review.get("reasons"), list):
                        raise ValueError("Reviewer returned invalid schema")
                    if "reviewed_code_sha256" in review:
                        reviewed_hash = review["reviewed_code_sha256"]
                        if not isinstance(reviewed_hash, str) or reviewed_hash.casefold() != hashlib.sha256(code.encode()).hexdigest():
                            raise ValueError("Reviewer approval is bound to a different candidate revision")
                    if run["provenance"].get("execution_mode") == "captured_codex_session" and "reviewed_code_sha256" not in review:
                        raise ValueError("Captured session reviewer must bind approval to candidate SHA-256")
                    result["review"] = review
                    regression_break = regression_breaks(regression_before, regression)
                    accepted = (generated["total"] > 0 and generated["passed"] == generated["total"]
                                and independent["total"] > 0 and independent["passed"] == independent["total"]
                                and (extended is None or extended["total"] > 0 and extended["passed"] == extended["total"])
                                and regression_break == 0 and review["approved"])
                    result["verdict"] = {"loads_and_is_callable": generated.get("runnable", False),
                                         "generated_tests_pass": generated["total"] > 0 and generated["passed"] == generated["total"],
                                         "independent_acceptance_pass": independent["total"] > 0 and independent["passed"] == independent["total"],
                                         "additional_properties_pass": extended is None or extended["total"] > 0 and extended["passed"] == extended["total"],
                                         "regression_preserved": regression_break == 0,
                                         "satisfies_tested_requirements": accepted}
                    result["history"].append({"attempt": attempt, "generated": generated, "acceptance": independent,
                                              "regression": regression, "extended": extended, "review": review, "accepted": accepted})
                    if accepted:
                        result["status"] = "accepted"
                        break
                    feedback = {"generated": generated, "independent_suite_passed": independent["passed"] == independent["total"],
                                "regression": regression, "review": review}
                    event(task.id, "supervisor", "retry", "证据门禁未全部通过；在轮数预算内继续修复")
                if result["status"] == "running":
                    result["status"] = "rejected"
                result["regression_breaks"] = regression_breaks(regression_before, result.get("regression", regression_before))
                event(task.id, "supervisor", "complete", "独立验收和回归全部通过" if result["status"] == "accepted" else "补丁未通过全部门禁")
            except Exception as exc:
                result.update(status="error", error=safe_error(exc, settings))
                event(task.id, "supervisor", "error", result["error"])
            result["duration_s"] = round(time.monotonic() - task_started, 3)
            if result.get("patch"):
                delivery = directory / task.id.replace("/", "-")
                delivery.mkdir(exist_ok=True)
                (delivery / "candidate.py").write_text(result["patch"], encoding="utf-8")
                (delivery / "patch.diff").write_text(result["diff"], encoding="utf-8")
            run["summary"].update(accepted=sum(item["status"] == "accepted" for item in run["tasks"]),
                                  regressions=sum(item.get("regression_breaks", 0) for item in run["tasks"]),
                                  duration_s=round(time.monotonic() - started, 3))
            checkpoint()
        run["model_calls"] = provider.calls
        run["status"] = "completed" if all(item["status"] == "accepted" for item in run["tasks"]) else "failed"
    except Exception as exc:
        run.update(status="failed", error=safe_error(exc, settings))
    run["summary"]["duration_s"] = round(time.monotonic() - started, 3)
    run["finished_at"] = now()
    checkpoint()
    return run


def safe_error(exc: Exception, settings: Settings) -> str:
    message = f"{type(exc).__name__}: {str(exc)[:1000]}"
    for secret in (settings.api_key, settings.agentarts_key):
        if secret:
            message = message.replace(secret, "[REDACTED]")
    # Transport exception URLs can embed userinfo/query credentials.
    if isinstance(exc, (ConnectionError, TimeoutError)) or type(exc).__module__.startswith("httpx"):
        return f"{type(exc).__name__}: model transport failed; check service connectivity"
    return message

