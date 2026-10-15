"""Controller boundary fixtures; no model, Flask server, or business test is run.

CP-SAT, source copies, generated-test freezing, scoped patch application, review
hashes, capability artifacts and the final archive are exercised on temporary
miniature files. Tool/model results below are explicitly synthetic unit inputs.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid
import zipfile

import pytest

from testfix.config import Settings
from testfix.project_models import ProjectModelError, _validate_schema, reject_protected_context
from testfix.project_tools import files_under
from testfix import project_workflow as workflow


CORE = list(workflow.CAPABILITY_NAMES)
PRIVATE_MARKER = "PRIVATE_ACCEPTANCE_SOURCE_MUST_NOT_REACH_MODELS"
WHEEL_CHECK = "application and static asset start from built wheel"
RESTART_CHECK = "database and changed article survive process restart"
HTTP_CHECKS = [
    WHEEL_CHECK,
    "registration form loads",
    "registration persists and redirects to login",
    "login establishes real cookie session",
    *[f"create article {number} via HTTP" for number in range(13)],
    "create unrelated article", "SQLite contains all created articles",
    "index limits display to five articles", "filtered page two renders five matching articles",
    "last filtered page contains remaining three articles", "public detail is linked from title and displays body",
    "author updates article through HTTP", "updated content is rendered",
    "identical non-ASCII literal search Écho", "identical non-ASCII literal search Σ",
    "logged-out readers retain public detail access", "logged-out edit is redirected to login",
    "different user cannot edit article", "different user cannot delete article",
    "missing detail returns 404", "invalid page returns 400", "out-of-range filtered page returns 404",
    "very large positive page returns 404", "SQL-looking query remains literal text",
    "search input escapes HTML", RESTART_CHECK, "stored account survives restart",
    "author delete persists and removes detail",
]


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")
    return path


class BoundaryProvider:
    """Injected SDK-shaped provider with real schema/public-context validation."""

    def __init__(self):
        self.calls = []
        self.invocations = []
        self.repair_attempts = 0
        self.review_approved = True
        self.omit_core = False
        self.stale_second_review = False
        self.failure_skill = None
        self.reported_tokens = 10
        self.first_review_hash = None

    def metadata(self):
        return {"id": "injected-unit-boundary-fixture", "real_model_executed": False}

    def invoke(self, skill, context, schema, *, timeout_s, token_cap):
        assert schema == workflow.SCHEMAS[skill]
        assert timeout_s > 0 and token_cap > 0
        reject_protected_context(context)
        assert PRIVATE_MARKER not in json.dumps(context, ensure_ascii=False)
        self.invocations.append({"skill": skill, "context": deepcopy(context), "token_cap": token_cap})
        trace = {"skill_id": skill, "provider": "injected-unit-boundary-fixture",
                 "usage": {"total_tokens": self.reported_tokens}, "status": "completed"}
        if skill == self.failure_skill:
            trace.update(status="failed", error={"code": "transport_error"})
            self.calls.append(trace)
            raise ProjectModelError("transport_error", "Synthetic boundary transport failure")
        ids = context["requirement_ids"]
        if skill == "plan-workflow":
            output = {"goals": [{"requirement_id": value, "goal": "Mini fixture goal"} for value in ids],
                      "skill_ids": CORE[:-1] if self.omit_core else CORE.copy(), "rationale": "Unit fixture"}
        elif skill == "explore-repository":
            output = {"candidates": [{"path": "flaskr/app.py", "query": "VALUE", "reason": "Actual mini source"}],
                      "exhausted": True}
        elif skill == "ground-code":
            output = {"locations": [{"requirement_id": value, "path": "flaskr/app.py", "symbol": "VALUE",
                                      "evidence": "VALUE exists in provided sources", "status": "located"} for value in ids]}
        elif skill == "analyze-requirements":
            output = {"requirements": [{"id": value, "behavior": "Return updated mini value", "state": "missing",
                                          "evidence": "Public fixture source", "criterion": "Public fixture assertion"}
                                         for value in ids], "assumptions": []}
        elif skill == "generate-tests":
            output = {"files": [{"path": "test_value.py", "source": "from flaskr.app import VALUE\ndef test_value():\n    assert VALUE >= 1\n"}],
                      "steps": ["Collect the generated fixture assertion"],
                      "coverage": [{"requirement_id": value, "tests": ["test_value.py::test_value"]} for value in ids],
                      "assumptions": ["Synthetic tool outcomes are used only by this control-flow test"]}
        elif skill == "diagnose-results":
            output = {"failures": [{"requirement_id": value, "cause": "Mini value needs a scoped change",
                                      "evidence": "Supplied synthetic assertion failure", "target_paths": ["flaskr/app.py"]}
                                     for value in ids], "summary": "Fixture diagnosis"}
        elif skill == "repair-code":
            self.repair_attempts += 1
            output = {"baseline_sha256": context["candidate_sha256"],
                      "files": [{"path": "flaskr/app.py", "source": f"VALUE = {self.repair_attempts}\n"}],
                      "reason": "Scoped fixture patch"}
        elif skill == "review-patch":
            candidate_hash = context["candidate_sha256"]
            review_hash = self.first_review_hash if self.stale_second_review and self.first_review_hash else candidate_hash
            self.first_review_hash = self.first_review_hash or candidate_hash
            output = {"approved": self.review_approved, "candidate_sha256": review_hash,
                      "findings": [] if self.review_approved else [{"path": "flaskr/app.py", "severity": "high",
                                                                    "problem": "Synthetic review rejection", "fix": "Repair again"}],
                      "summary": "Unit boundary review, no business code executed"}
        else:
            raise AssertionError("Deterministic verifier must never be sent to a model")
        _validate_schema(output, schema)
        trace["result"] = deepcopy(output)
        self.calls.append(trace)
        return deepcopy(output)


class BoundaryTools:
    """Synthetic suite delivery at the trusted tool boundary, with real files/seals."""

    def __init__(self):
        self.calls = []
        self.results = []
        self.workspaces = []
        self.first_acceptance_fails = False
        self.failed_gate = None
        self.missing_gate = None
        self.mutate_tree = None
        self.public_failure = None
        self.baseline_failure = None

    def suite(self, workspace, operation, name, *, status, project):
        path = workspace.directory / "synthetic-tool-fixtures" / f"{len(self.calls)}-{operation}-{name}.stdout.txt"
        write(path, "Synthetic unit-boundary evidence; no business process or test was executed.\n")
        identity = {"regression": "original::test_original", "generated": "generated::test_value",
                    "independent": "private::test_value"}[name]
        passed = status == "passed"
        return {"total": 1, "passed": int(passed), "cases": [{"id": identity, "status": status}],
                "case_ids": [identity], "duplicate_case_ids": [], "collection_errors": 0,
                "runnable": True, "successful": passed, "timeout": False,
                "exit_code": 0 if passed else 1, "duration_s": 0, "command": [], "cwd": str(project),
                "stdout_path": str(path), "stderr_path": str(path), "junit_path": "", "parse_error": "",
                "binding_error": "", "binding": {"schema": "testfix.pytest-binding.v1", "success": True,
                    "project": str(project.resolve()), "modules": {"flaskr": str((project / "flaskr/__init__.py").resolve())},
                    "escaped_modules": [], "error": ""}}

    def __call__(self, settings, workspace, operation, *, timeout_s, round_number=1):
        assert timeout_s > 0
        workspace.assert_integrity()
        self.workspaces.append(workspace)
        self.calls.append((operation, round_number, workspace.candidate_hash()))
        result = {"operation": operation, "candidate_sha256": workspace.candidate_hash()}
        if operation in {"baseline_public", "candidate_public"}:
            target = workspace.baseline if operation == "baseline_public" else workspace.candidate
            result["regression"] = self.suite(workspace, operation, "regression", status="passed", project=target)
            result["generated"] = self.suite(workspace, operation, "generated",
                                               status="failure" if operation == "baseline_public" else "passed", project=target)
            if operation == "baseline_public" and self.baseline_failure:
                if self.baseline_failure == "skipped":
                    result["generated"]["cases"][0]["status"] = "skipped"
                else:
                    result["generated"][self.baseline_failure] = "Synthetic collection/binding failure"
            if operation == "candidate_public" and self.public_failure:
                suite = result[self.public_failure]
                suite.update(successful=False, passed=0, exit_code=1)
                suite["cases"][0]["status"] = "failure"
        elif operation == "verify":
            result["baseline"] = self.suite(workspace, operation, "independent", status="failure", project=workspace.baseline)
            acceptance_passes = not (self.first_acceptance_fails and round_number == 1) and self.failed_gate != "acceptance"
            result["acceptance"] = self.suite(workspace, operation, "independent",
                                                status="passed" if acceptance_passes else "failure", project=workspace.candidate)
            # This opaque wheel-shaped fixture is never installed or executed.
            wheel = workspace.directory / "synthetic-tool-fixtures" / "unit-fixture.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("flaskr/__init__.py", "# synthetic unit fixture\n")
                archive.writestr("flaskr/templates/blog/detail.html", "<p>unit fixture</p>\n")
            checks = [{"name": name, "passed": True} for name in HTTP_CHECKS if name != self.missing_gate]
            result["delivery"] = {
                "build": {"command": [], "exit_code": 0, "passed": self.failed_gate != "build", "wheel": str(wheel),
                          "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                          "members": ["flaskr/__init__.py", "flaskr/templates/blog/detail.html"]},
                "http": {"passed": len(checks), "total": len(checks), "successful": self.failed_gate != "http",
                         "checks": checks, "requests": [], "command": [], "tested_artifact": "built wheel"},
                "duration_s": 0,
            }
            if self.missing_gate == "build_record":
                del result["delivery"]["build"]
        else:
            raise AssertionError("Unknown tool operation")
        if self.mutate_tree and operation == "candidate_public":
            target = {"baseline": workspace.baseline / "flaskr/app.py",
                      "original": workspace.candidate / "tests/test_original.py",
                      "generated": workspace.generated / "test_value.py",
                      "hidden": workspace.acceptance / "test_private.py",
                      "candidate": workspace.candidate / "flaskr/app.py"}[self.mutate_tree]
            write(target, "# mutated outside the patch boundary\n")
        self.results.append(deepcopy(result))
        return result


@pytest.fixture
def harness(tmp_path, monkeypatch):
    source = tmp_path / "registered/source"
    write(source / "flaskr/__init__.py", '"""Miniature unit fixture, never served."""\n')
    write(source / "flaskr/app.py", "VALUE = 0\n")
    write(source / "tests/test_original.py", "def test_original():\n    assert True\n")
    write(source / "pyproject.toml", '[project]\nname = "unit-control-flow-fixture"\nversion = "0.0.0"\n')
    acceptance = tmp_path / "registered/acceptance"
    write(acceptance / "test_private.py", f"# {PRIVATE_MARKER}\nfrom flaskr.app import VALUE\ndef test_value():\n    assert VALUE >= 1\n")
    interpreter = write(tmp_path / "registered/python.exe", "not an executable; this test never launches it\n")
    project = {"id": "Project/unit-control-flow", "title": "Temporary controller fixture", "adapter": "flaskr",
               "source": {"origin": "synthetic-unit-fixture", "files": files_under(source)},
               "baseline": str(source), "acceptance": str(acceptance), "python": str(interpreter),
               "requirements": [{"id": "MINI-VALUE", "description": "Update only the mini fixture value"}],
               "clarifications": ["Boundary test only; no inference or business execution"],
               "allowed_changes": ["flaskr/app.py"]}
    settings = Settings(root=tmp_path)
    provider, tools = BoundaryProvider(), BoundaryTools()
    run = {"id": uuid.uuid4().hex, "project_id": project["id"], "provider": "injected-unit-boundary-fixture",
           "model": "unit-boundary-fixture", "max_rounds": 2}
    monkeypatch.setattr(workflow, "get_project", lambda current_settings, project_id: project)
    return SimpleNamespace(settings=settings, project=project, provider=provider, tools=tools, run=run,
                           execute=lambda **kwargs: workflow.execute_project_run(run, settings, provider=provider,
                                                                                  tool_runner=tools, **kwargs))


def assert_not_accepted(result):
    assert result["status"] != "completed"
    assert result["summary"]["accepted"] == 0
    assert result["tasks"][0]["status"] != "accepted"
    assert not result.get("download")


def test_required_http_gate_names_match_trusted_delivery_contract():
    assert len(HTTP_CHECKS) == len(set(HTTP_CHECKS)) == 40
    assert set(HTTP_CHECKS) == workflow.REQUIRED_HTTP_CHECKS


def test_nine_capability_control_loop_uses_real_ilp_and_real_scoped_artifacts(harness):
    checkpoints = []
    result = harness.execute(persist=lambda value: checkpoints.append(deepcopy(value)))
    assert result["status"] == "completed", result.get("error")
    assert result["summary"]["accepted"] == 1
    assert result["plan"]["solver"]["name"] == "OR-Tools CP-SAT"
    assert result["plan"]["solver_status"] in {"OPTIMAL", "FEASIBLE"}
    assert [item["skill_id"] for item in result["plan"]["selected"]] == CORE
    assert [item["skill"] for item in harness.provider.invocations] == CORE[:-1]
    assert all(item["status"] == "completed" for item in result["workflow"]["capabilities"])
    assert [call[0] for call in harness.tools.calls] == ["baseline_public", "candidate_public", "verify"]
    workspace = harness.tools.workspaces[-1]
    workspace.assert_integrity()
    assert (workspace.candidate / "flaskr/app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (workspace.baseline / "flaskr/app.py").read_text(encoding="utf-8") == "VALUE = 0\n"
    assert workspace.generated_seal == files_under(workspace.generated)
    assert result["tasks"][0]["review"]["candidate_sha256"] == workspace.candidate_hash()
    for capability in result["workflow"]["capabilities"]:
        artifact = capability["artifact"]
        path = harness.settings.root / artifact["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["skill_id"] == capability["skill_id"]
        assert document["source_revision"] == result["provenance"]["source_revision"]
    archive = harness.settings.root / result["download"]["path"]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == result["download"]["sha256"]
    with zipfile.ZipFile(archive) as package:
        assert package.read("project/flaskr/app.py") == b"VALUE = 1\n"
        assert len([name for name in package.namelist() if name.startswith("evidence/")]) == 9
        receipt = json.loads(package.read('provenance/acceptance-report.json'))
        assert receipt['tasks'][0]['candidate_sha256'] == result['tasks'][0]['candidate_sha256']
        assert receipt['tasks'][0]['verdict']['satisfies_tested_requirements'] is True
        assert 'python -m pytest validation/independent' in package.read('REPRODUCE.md').decode('utf-8')
    saved = json.loads((harness.settings.artifacts / result["id"] / "report.json").read_text(encoding="utf-8"))
    assert saved["status"] == "completed" and saved["summary"]["accepted"] == 1
    assert checkpoints[-1]["status"] == "completed"
    assert result["provenance"]["provider"]["real_model_executed"] is False


@pytest.mark.parametrize("gate", ["acceptance", "build", "http"])
def test_failed_required_gate_cannot_produce_completed_download(harness, gate):
    harness.tools.failed_gate = gate
    result = harness.execute()
    assert_not_accepted(result)
    assert result["status"] == "rejected"
    assert result["tasks"][0]["attempts"] == 2


@pytest.mark.parametrize("gate", ["build_record", RESTART_CHECK, WHEEL_CHECK])
def test_missing_required_gate_is_rejected_even_when_aggregate_says_success(harness, gate):
    harness.tools.missing_gate = gate
    assert_not_accepted(harness.execute())
    assert any(call[0] == "verify" for call in harness.tools.calls)


def test_independent_failure_retries_with_new_patch_hash_and_new_review(harness):
    harness.tools.first_acceptance_fails = True
    result = harness.execute()
    assert result["status"] == "completed", result.get("error")
    reviews = [item for item in harness.provider.invocations if item["skill"] == "review-patch"]
    assert len(reviews) == 2
    assert reviews[0]["context"]["candidate_sha256"] != reviews[1]["context"]["candidate_sha256"]
    verify_calls = [call for call in harness.tools.calls if call[0] == "verify"]
    assert [call[1] for call in verify_calls] == [1, 2]
    assert [call[2] for call in verify_calls] == [item["context"]["candidate_sha256"] for item in reviews]
    second_diagnosis = [item for item in harness.provider.invocations if item["skill"] == "diagnose-results"][1]
    summary = second_diagnosis["context"]["public_test_results"]["independent_result_summary"]
    assert set(summary) == {"passed", "total", "successful"}
    assert summary["successful"] is False
    review_directory = harness.settings.artifacts / result["id"] / "capabilities/review-patch"
    assert {path.name for path in review_directory.glob("*.json")} == {"attempt-1.json", "attempt-2.json"}


def test_second_round_stale_review_hash_cannot_reuse_previous_approval(harness):
    harness.tools.first_acceptance_fails = True
    harness.provider.stale_second_review = True
    result = harness.execute()
    assert_not_accepted(result)
    assert result["status"] == "failed"
    assert "Review is not bound" in result["error"]
    assert len([call for call in harness.tools.calls if call[0] == "verify"]) == 1


def test_denied_review_prevents_all_independent_verification(harness):
    harness.provider.review_approved = False
    result = harness.execute()
    assert_not_accepted(result)
    assert result["status"] == "rejected"
    assert all(call[0] != "verify" for call in harness.tools.calls)
    assert sum(item["skill"] == "review-patch" for item in harness.provider.invocations) == 2


@pytest.mark.parametrize("public_suite", ["regression", "generated"])
def test_public_test_failure_never_reaches_review_or_independent_verification(harness, public_suite):
    harness.tools.public_failure = public_suite
    result = harness.execute()
    assert_not_accepted(result)
    assert result["status"] == "rejected"
    assert sum(call[0] == "candidate_public" for call in harness.tools.calls) == 2
    assert not any(item["skill"] == "review-patch" for item in harness.provider.invocations)
    assert all(call[0] != "verify" for call in harness.tools.calls)


@pytest.mark.parametrize("failure", ["parse_error", "binding_error", "skipped"])
def test_invalid_baseline_evidence_is_not_a_functional_missing_feature(harness, failure):
    harness.tools.baseline_failure = failure
    result = harness.execute()
    assert_not_accepted(result)
    assert result["status"] == "failed"
    assert "baseline assertion failure" in result["error"]
    assert not any(item["skill"] == "repair-code" for item in harness.provider.invocations)


def test_model_planner_cannot_remove_final_gate(harness):
    harness.provider.omit_core = True
    result = harness.execute()
    assert_not_accepted(result)
    assert "omitted or reordered" in result["error"]
    assert [item["skill"] for item in harness.provider.invocations] == ["plan-workflow", "plan-workflow"]
    assert "validation_feedback" in harness.provider.invocations[-1]["context"]


def test_missing_exploration_file_is_rejected_and_corrected_by_next_model_call(harness):
    original = harness.provider.invoke
    rejected = False

    def invoke(skill, context, schema, **kwargs):
        nonlocal rejected
        output = original(skill, context, schema, **kwargs)
        if skill == 'explore-repository' and not rejected:
            rejected = True
            output['candidates'][0]['path'] = 'flaskr/templates/proposed-but-missing.html'
        return output

    harness.provider.invoke = invoke
    result = harness.execute()
    assert result['status'] == 'completed', result.get('error')
    calls = [item for item in harness.provider.invocations if item['skill'] == 'explore-repository']
    assert len(calls) == 2
    assert 'Required project file is missing' in calls[1]['context']['validation_feedback']['error']
    capability = next(item for item in result['workflow']['capabilities'] if item['skill_id'] == 'explore-repository')
    assert capability['attempt'] == 2 and len(capability['validation_failures']) == 1
    assert result['workflow']['budget']['calls'] == len(harness.provider.calls) == 9


@pytest.mark.parametrize('rejected_skill',['generate-tests','repair-code'])
def test_file_preflight_rejection_has_no_write_and_is_corrected_within_plan(harness,rejected_skill):
    original=harness.provider.invoke
    rejected=False

    def invoke(skill,context,schema,**kwargs):
        nonlocal rejected
        output=original(skill,context,schema,**kwargs)
        if skill==rejected_skill and not rejected:
            rejected=True
            if skill=='generate-tests':
                output['files'][0]['path']='generated_tests/test_value.py'
            else:
                output['files'][0]['source']='def invalid(:\n'
        return output

    harness.provider.invoke=invoke
    result=harness.execute()
    assert result['status']=='completed',result.get('error')
    calls=[item for item in harness.provider.invocations if item['skill']==rejected_skill]
    assert len(calls)==2
    assert calls[0]['context']['candidate_sha256']==calls[1]['context']['candidate_sha256']
    assert calls[1]['context']['validation_feedback']['error']
    capability=next(item for item in result['workflow']['capabilities'] if item['skill_id']==rejected_skill)
    assert capability['attempt']==2 and len(capability['validation_failures'])==1
    assert result['workflow']['budget']['calls']==9


def test_real_ilp_infeasibility_stops_before_any_provider_or_tool_call(harness):
    harness.run["budget"] = {**workflow.DEFAULT_BUDGET, "wall_seconds": 60}
    result = harness.execute()
    assert_not_accepted(result)
    assert result["status"] == "budget_exhausted"
    assert result["plan"]["solver_status"] == "INFEASIBLE"
    assert result["plan"]["stages"] == []
    assert harness.provider.invocations == [] and harness.tools.calls == []


def test_integer_wall_budget_also_reserves_public_suite_process_time(harness):
    # Model/verification allowances alone fit 4200 seconds; adding the
    # baseline and two candidate suite deadlines makes the plan infeasible.
    harness.run['budget'] = {**workflow.DEFAULT_BUDGET, 'wall_seconds':4200}
    result = harness.execute()
    assert result['status']=='budget_exhausted'
    assert result['plan']['solver_status']=='INFEASIBLE'
    assert harness.provider.invocations == [] and harness.tools.calls == []


def test_archive_creation_past_wall_deadline_cannot_publish_accepted_download(harness,monkeypatch):
    import time
    original_clock=time.monotonic
    original_write=workflow.zipfile.ZipFile.write
    offset=[0]

    def write_past_deadline(archive,*args,**kwargs):
        result=original_write(archive,*args,**kwargs)
        offset[0]=workflow.DEFAULT_BUDGET['wall_seconds']+1
        return result

    monkeypatch.setattr(workflow.time,'monotonic',lambda:original_clock()+offset[0])
    monkeypatch.setattr(workflow.zipfile.ZipFile,'write',write_past_deadline)
    result=harness.execute()
    assert result['status']=='budget_exhausted'
    assert_not_accepted(result)
    saved=json.loads((harness.settings.artifacts/result['id']/'report.json').read_text(encoding='utf-8'))
    assert saved['status']=='budget_exhausted' and saved['summary']['accepted']==0 and 'download' not in saved


def test_insufficient_conservative_request_reservation_stops_before_invoke(harness):
    harness.project["clarifications"] = ["large public context " * 6000]
    harness.run["budget"] = {**workflow.DEFAULT_BUDGET, "token_limit": 50_000}
    result = harness.execute()
    assert_not_accepted(result)
    assert result["status"] == "budget_exhausted"
    assert harness.provider.invocations == [] and harness.tools.calls == []


def test_budget_reservation_refuses_an_unplanned_large_request():
    budget=workflow.ExecutionBudget(workflow.DEFAULT_BUDGET)
    with pytest.raises(workflow.BudgetExceeded,match='reservation'):
        budget.reserve({'public_context':'x'*workflow.DEFAULT_BUDGET['token_limit']},1000)


def test_reported_usage_above_budget_stops_subsequent_model_calls(harness):
    harness.provider.reported_tokens = workflow.DEFAULT_BUDGET["token_limit"] + 1
    result = harness.execute()
    assert_not_accepted(result)
    assert result["status"] == "budget_exhausted"
    assert len(harness.provider.invocations) == 1 and harness.tools.calls == []


@pytest.mark.parametrize("tree", ["baseline", "original", "generated", "hidden", "candidate"])
def test_protected_tree_mutation_is_rejected_before_approval_or_download(harness, tree):
    harness.tools.mutate_tree = tree
    result = harness.execute()
    assert_not_accepted(result)
    assert result["status"] == "failed"
    assert "modified" in result["error"] or "changed" in result["error"]
    assert not any(item["skill"] == "review-patch" for item in harness.provider.invocations)


def test_provider_transport_error_persists_failed_trace_without_fallback(harness):
    harness.provider.failure_skill = "ground-code"
    result = harness.execute()
    assert_not_accepted(result)
    assert result["status"] == "failed"
    assert "transport_error" in result["error"]
    assert [item["skill"] for item in harness.provider.invocations] == CORE[:3]
    assert result["model_calls"][-1]["status"] == "failed"
    capability = next(item for item in result["workflow"]["capabilities"] if item["skill_id"] == "ground-code")
    assert capability["status"] == "failed"
    assert harness.tools.calls == []
