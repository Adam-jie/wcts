"""Package controller boundaries using a temporary synthetic repo and fake I/O.

No real package case, private evaluation, model, build, or CLI is executed.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid
import zipfile

import pytest

from testfix.config import Settings
from testfix.package_tools import _merge_regression, run_package_suite
from testfix.project_eval import _mapping_sha256
from testfix.project_models import _validate_schema
from testfix.project_tools import ProjectWorkspace, files_under
from testfix import project_workflow as workflow


PRIVATE = "SYNTHETIC_PRIVATE_ACCEPTANCE_SOURCE"
CLI_EXPECTATION = "SYNTHETIC_PRIVATE_CLI_EXPECTATION"
TARGET = "src/demo_pkg/__main__.py"
NAMES = ["synthetic stdin CLI", "synthetic export CLI"]


def write(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


class PackageProvider:
    def __init__(self):
        self.calls, self.contexts, self.reviews = [], [], []
        self.repairs = 0

    def metadata(self):
        return {"id": "synthetic-package-controller", "real_model_executed": False}

    def invoke(self, skill, context, schema, *, timeout_s, token_cap):
        assert timeout_s > 0 and token_cap > 0
        serialized = json.dumps(context, ensure_ascii=False)
        assert PRIVATE not in serialized and CLI_EXPECTATION not in serialized
        assert context["execution_profile"] == "python_package"
        self.contexts.append(deepcopy(context))
        ids = context["requirement_ids"]
        if skill == "plan-workflow":
            result = {"goals": [{"requirement_id": item, "goal": "Synthetic package behavior"} for item in ids],
                      "skill_ids": list(workflow.CAPABILITY_NAMES), "rationale": "Synthetic unit input"}
        elif skill == "explore-repository":
            result = {"candidates": [{"path": TARGET, "query": "VALUE", "reason": "Provided synthetic source"}], "exhausted": True}
        elif skill == "ground-code":
            result = {"locations": [{"requirement_id": item, "path": TARGET, "symbol": "VALUE", "evidence": "Present in public source", "status": "located"} for item in ids]}
        elif skill == "analyze-requirements":
            result = {"requirements": [{"id": item, "behavior": "Update synthetic value", "state": "missing", "evidence": "Provided source", "criterion": "Synthetic assertion"} for item in ids], "assumptions": []}
        elif skill == "generate-tests":
            result = {"files": [{"path": "test_generated.py", "source": "def test_generated():\n    assert True\n"}],
                      "steps": ["Synthetic controller fixture; no suite is actually executed"],
                      "coverage": [{"requirement_id": item, "tests": ["test_generated"]} for item in ids], "assumptions": []}
        elif skill == "diagnose-results":
            result = {"failures": [{"requirement_id": item, "cause": "Synthetic value", "evidence": "Supplied synthetic failure", "target_paths": [TARGET]} for item in ids], "summary": "Synthetic diagnosis"}
        elif skill == "repair-code":
            self.repairs += 1
            result = {"baseline_sha256": context["candidate_sha256"], "files": [{"path": TARGET, "source": f"VALUE = {self.repairs}\n"}], "reason": "Synthetic scoped change"}
        elif skill == "review-patch":
            self.reviews.append(context["candidate_sha256"])
            result = {"approved": True, "candidate_sha256": context["candidate_sha256"], "findings": [], "summary": "Synthetic review"}
        else:
            raise AssertionError("Deterministic verification cannot invoke a model")
        _validate_schema(result, schema)
        self.calls.append({"skill_id": skill, "provider": "synthetic-package-controller", "status": "completed", "usage": {"total_tokens": 17}, "result": deepcopy(result)})
        return result


class PackageTools:
    def __init__(self):
        self.calls, self.workspaces = [], []
        self.bad_delivery, self.mutation = None, None
        self.first_independent_failure = False

    def suite(self, workspace, name, passed=True, private=False):
        log = write(workspace.directory / "synthetic-evidence" / f"{len(self.calls)}-{name}.txt", PRIVATE if private else "Synthetic unit evidence; no suite executed")
        return {"total": 1, "passed": int(passed), "cases": [{"id": name + "::case", "status": "passed" if passed else "failure"}],
                "case_ids": [name + "::case"], "duplicate_case_ids": [], "collection_errors": 0, "successful": passed,
                "runnable": True, "timeout": False, "exit_code": 0 if passed else 1, "parse_error": "", "binding_error": "",
                "duration_s": 0, "stdout_path": str(log), "stderr_path": str(log)}

    def __call__(self, settings, workspace, operation, *, timeout_s, round_number=1):
        assert timeout_s > 0
        workspace.assert_integrity()
        self.calls.append((operation, round_number))
        self.workspaces.append(workspace)
        result = {"operation": operation, "candidate_sha256": workspace.candidate_hash()}
        if operation in {"baseline_public", "candidate_public"}:
            upstream = result["upstream_regression"] = self.suite(workspace, "upstream")
            behavior = result["behavior_regression"] = self.suite(workspace, "behavior")
            result["regression"] = _merge_regression(upstream, behavior, workspace.directory / "synthetic-evidence", f"{len(self.calls)}-merged")
            result["generated"] = self.suite(workspace, "generated", operation != "baseline_public")
        else:
            result["baseline"] = self.suite(workspace, "independent", False, private=True)
            result["acceptance"] = self.suite(workspace, "independent", not (self.first_independent_failure and round_number == 1), private=True)
            checks = [{"name": name, "passed": True} for name in NAMES]
            delivery = {"build": {"passed": True}, "cli": {"successful": True, "passed": len(NAMES), "total": len(NAMES), "checks": checks}}
            if self.bad_delivery == "missing":
                checks.pop()
            elif self.bad_delivery == "duplicate":
                checks[1]["name"] = checks[0]["name"]
            elif self.bad_delivery == "renamed":
                checks[1]["name"] = "an unregistered optimistic check"
            elif self.bad_delivery == "failed":
                checks[0]["passed"] = False
            elif self.bad_delivery == "build":
                delivery["build"]["passed"] = False
            elif self.bad_delivery == "http":
                delivery["http"] = delivery.pop("cli")
            result["delivery"] = delivery
        if operation == "candidate_public" and self.mutation:
            targets = {"original": workspace.candidate / "tests/test_original.py", "behavior": workspace.regression / "test_behavior.py",
                       "acceptance": workspace.acceptance / "test_private.py", "generated": workspace.generated / "test_generated.py",
                       "metadata": workspace.candidate / "README.md"}
            write(targets[self.mutation], "# changed outside the authorized patch tool\n")
        return result


@pytest.fixture
def package_harness(tmp_path, monkeypatch):
    source = tmp_path / "registered/source"
    write(source / TARGET, "VALUE = 0\n")
    write(source / "src/demo_pkg/__init__.py", "# synthetic source package\n")
    write(source / "tests/test_original.py", "def test_original():\n    assert True\n")
    write(source / "README.md", "Synthetic package metadata\n")
    write(source / "pyproject.toml", '[project]\nname="demo-pkg"\nversion="0.0.0"\n')
    write(source / "src/demo_pkg/py.typed", "")
    private = write(tmp_path / "registered/acceptance/test_private.py", f"# {PRIVATE}\ndef test_private():\n    assert True\n")
    regression = write(tmp_path / "registered/regression/test_behavior.py", "def test_behavior():\n    assert True\n")
    python = write(tmp_path / "registered/python.exe", "synthetic non-executable interpreter\n")
    config = {"package": "demo_pkg", "source_root": "src", "regression": str(regression.parent), "cli_module": "demo_pkg",
              "distribution": "demo-pkg", "checks": [{"name": name, "args": [], "expected_exit": 0, "stdout_contains": [CLI_EXPECTATION], "stdout_not_contains": []} for name in NAMES]}
    project = {"id": "Project/synthetic-package", "title": "Synthetic package controller", "adapter": "python_package",
               "source": {"files": files_under(source)}, "baseline": str(source), "acceptance": str(private.parent), "python": str(python),
               "package_config": config, "public_runtime": {"package": "demo_pkg", "source_root": "src"},
               "requirements": [{"id": "PKG-VALUE", "description": "Update the synthetic value"}], "clarifications": [], "allowed_changes": [TARGET]}
    settings, provider, tools = Settings(root=tmp_path), PackageProvider(), PackageTools()
    run = {"id": uuid.uuid4().hex, "project_id": project["id"], "provider": "synthetic-package-controller", "model": "synthetic",
           "max_rounds": 1, "budget": {**workflow.DEFAULT_BUDGET, "wall_seconds": 7200}}
    monkeypatch.setattr(workflow, "get_project", lambda current, identifier: project)
    return SimpleNamespace(settings=settings, project=project, provider=provider, tools=tools, run=run,
                           execute=lambda: workflow.execute_project_run(run, settings, provider=provider, tool_runner=tools))


def test_package_profile_records_nine_capabilities_and_cli_evidence(package_harness):
    fixture = package_harness
    result = fixture.execute()
    assert result["status"] == "completed" and result["summary"]["accepted"] == 1
    assert result["execution_profile"] == "python_package"
    assert [item["skill_id"] for item in result["workflow"]["capabilities"]] == list(workflow.CAPABILITY_NAMES)
    assert all(item["status"] == "completed" for item in result["workflow"]["capabilities"])
    task = result["tasks"][0]
    assert task["delivery_kind"] == "cli" and task["verdict"]["application_runs_via_cli"] is True
    assert "application_runs_over_http" not in task["verdict"]
    assert task["upstream_regression"]["successful"] and task["behavior_regression"]["successful"]
    workspace = fixture.tools.workspaces[-1]
    assert task["candidate_sha256"] == _mapping_sha256(files_under(workspace.candidate))
    archive = fixture.settings.root / result["download"]["path"]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == result["download"]["sha256"]
    with zipfile.ZipFile(archive) as output:
        assert "validation/behavior-regression/test_behavior.py" in output.namelist()


@pytest.mark.parametrize("gate", ["missing", "duplicate", "renamed", "failed", "build", "http"])
def test_package_delivery_cannot_use_incomplete_cli_checks_or_http(package_harness, gate):
    package_harness.tools.bad_delivery = gate
    result = package_harness.execute()
    assert result["status"] != "completed" and result["summary"]["accepted"] == 0 and "download" not in result


@pytest.mark.parametrize("tree", ["original", "behavior", "acceptance", "generated", "metadata"])
def test_package_protected_tree_mutation_blocks_review_and_delivery(package_harness, tree):
    package_harness.tools.mutation = tree
    result = package_harness.execute()
    assert result["status"] == "failed" and result["summary"]["accepted"] == 0 and not package_harness.provider.reviews
    assert "download" not in result


def test_package_hash_covers_metadata_and_non_python_package_files(package_harness):
    fixture = package_harness
    workspace = ProjectWorkspace(fixture.settings, fixture.run["id"], fixture.project)
    initial = workspace.candidate_hash()
    write(workspace.candidate / "src/demo_pkg/py.typed", "changed type marker\n")
    assert workspace.candidate_hash() != initial
    with pytest.raises(ValueError, match="outside the patch tool"):
        workspace.assert_integrity()


def test_package_independent_failure_requires_new_patch_and_review_without_private_context(package_harness):
    fixture = package_harness
    fixture.run["max_rounds"] = 2
    fixture.tools.first_independent_failure = True
    result = fixture.execute()
    assert result["status"] == "completed" and len(fixture.provider.reviews) == 2
    assert fixture.provider.reviews[0] != fixture.provider.reviews[1] == result["tasks"][0]["candidate_sha256"]
    assert ("verify", 1) in fixture.tools.calls and ("verify", 2) in fixture.tools.calls
    contexts = json.dumps(fixture.provider.contexts)
    assert PRIVATE not in contexts and CLI_EXPECTATION not in contexts


def test_package_archive_deadline_cannot_publish_an_accepted_zip(package_harness, monkeypatch):
    fixture = package_harness
    original_clock, original_write = workflow.time.monotonic, workflow.zipfile.ZipFile.write
    offset = [0]

    def past_deadline(archive, *args, **kwargs):
        offset[0] = 7201
        return original_write(archive, *args, **kwargs)

    monkeypatch.setattr(workflow.time, "monotonic", lambda: original_clock() + offset[0])
    monkeypatch.setattr(workflow.zipfile.ZipFile, "write", past_deadline)
    result = fixture.execute()
    assert result["status"] == "budget_exhausted" and result["summary"]["accepted"] == 0 and "download" not in result
    assert not (fixture.settings.artifacts / result["id"] / "completed-project.zip").exists()


@pytest.mark.parametrize("change", ["delete", "function_name", "file_name", "parameter_id"])
def test_package_collection_identity_still_rejects_deleted_or_renamed_cases(tmp_path, change):
    # An ancestor configuration previously made JUnit classnames include the
    # baseline/candidate directory. Root normalization must preserve the actual
    # relative test file, function and parameter identities within each suite.
    write(tmp_path / "pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["."]\n')
    source = ('import pytest\nimport demo_pkg\n'
              'def test_anchor():\n    assert demo_pkg.VALUE == 1\n'
              '@pytest.mark.parametrize("value", [1, 2], ids=["first", "second"])\n'
              'def test_subject(value):\n    assert value in (1, 2)\n')
    projects = [tmp_path / "workspace" / name for name in ("baseline", "candidate")]
    for project in projects:
        write(project / "src/demo_pkg/__init__.py", "VALUE = 1\n")
        write(project / "tests/test_contract.py", source)
    candidate_test = projects[1] / "tests/test_contract.py"
    if change == "delete":
        candidate_test.write_text(source.split("@pytest.mark.parametrize")[0], encoding="utf-8")
    elif change == "function_name":
        candidate_test.write_text(source.replace("def test_subject(", "def test_replacement("), encoding="utf-8")
    elif change == "file_name":
        candidate_test.rename(candidate_test.with_name("test_replacement.py"))
    else:
        candidate_test.write_text(source.replace('ids=["first", "second"]', 'ids=["renamed", "second"]'), encoding="utf-8")
    suites = [run_package_suite(Path(sys.executable), project, project / "tests", tmp_path / "evidence", label,
                                {"source_root": "src", "package": "demo_pkg"})
              for project, label in zip(projects, ("baseline", "candidate"))]
    assert all(suite["successful"] for suite in suites)
    assert suites[0]["case_ids"] != suites[1]["case_ids"]
    assert workflow._paired_cases_match(*suites) is False
    assert workflow._regression_preserved(*suites) is False


@pytest.mark.parametrize("suite_name", ["generated", "regression"])
def test_package_passing_identity_mismatch_fails_without_business_repair(package_harness, suite_name):
    fixture = package_harness
    fixture.run["max_rounds"] = 2
    original_tools = fixture.tools

    def mismatch(*args, **kwargs):
        result = original_tools(*args, **kwargs)
        if args[2] == "candidate_public":
            suite = result[suite_name]
            suite["cases"][0]["id"] += "-different-collected-case"
            suite["case_ids"][0] += "-different-collected-case"
        return result

    result = workflow.execute_project_run(fixture.run, fixture.settings, provider=fixture.provider, tool_runner=mismatch)
    assert result["status"] == "failed" and result["summary"]["accepted"] == 0
    assert "Public test identities changed" in result["error"]
    assert result["tasks"][0]["generated"]["successful"] and result["tasks"][0]["regression"]["successful"]
    assert fixture.provider.repairs == 1 and not fixture.provider.reviews
    assert "download" not in result
    assert not (fixture.settings.artifacts / result["id"] / "completed-project.zip").exists()


