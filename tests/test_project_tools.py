"""Boundary tests for registered tools; no model or application code is run."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

import pytest

from testfix.config import Settings
from testfix import project_delivery, project_eval, project_tool_worker, project_tools
from testfix.project_tools import ProjectWorkspace, confined, files_under, public_sources, relative_path, run_project_tool


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def registered(tmp_path):
    baseline = tmp_path / "registered/baseline"
    write(baseline / "flaskr/__init__.py", '"""Public application package."""\n')
    write(baseline / "flaskr/app.py", "VALUE = 1\n\ndef original():\n    return VALUE\n")
    write(baseline / "flaskr/templates/index.html", "<p>Original</p>\n")
    write(baseline / "tests/test_original.py", "def test_original():\n    assert True\n")
    write(baseline / "README.md", "Public project requirements.\n")
    acceptance = tmp_path / "registered/acceptance"
    write(acceptance / "test_private_acceptance.py", "def test_private_contract():\n    assert True\n")
    interpreter = write(tmp_path / "registered/python.exe", "not executed by these tests\n")
    project = {
        "id": "Project/unit-fixture",
        "baseline": str(baseline),
        "acceptance": str(acceptance),
        "python": str(interpreter),
        "source": {"files": files_under(baseline)},
        "allowed_changes": ["flaskr/app.py", "flaskr/__init__.py", "flaskr/templates/index.html"],
    }
    return Settings(root=tmp_path), project


@pytest.fixture
def workspace(registered):
    settings, project = registered
    return ProjectWorkspace(settings, "a" * 32, project)


def freeze_generated(workspace):
    return workspace.write_tests({"files": [{"path": "test_new_feature.py", "source": "def test_new_feature():\n    assert True\n"}]})


def test_invalid_second_generated_file_does_not_write_or_freeze_first(workspace):
    with pytest.raises(ValueError,match='own directory'):
        workspace.write_tests({'files':[
            {'path':'test_valid.py','source':'def test_valid():\n    assert True\n'},
            {'path':'generated_tests/conftest.py','source':'import pytest\n'}]})
    assert list(workspace.generated.iterdir())==[]
    assert workspace.generated_seal is None


def test_preflight_validation_does_not_write_or_freeze_candidate_or_tests(workspace):
    before=workspace.candidate_hash()
    workspace.write_tests({'files':[{'path':'test_valid.py','source':'def test_valid():\n    assert True\n'}]},dry_run=True)
    workspace.apply_patch({'baseline_sha256':before,'files':[{'path':'flaskr/app.py','source':'VALUE = 2\n'}]},dry_run=True)
    assert list(workspace.generated.iterdir())==[] and workspace.generated_seal is None
    assert workspace.candidate_hash()==before


@pytest.mark.parametrize("path", [
    "", "../secret.py", "flaskr/../../secret.py", "/tmp/secret.py", "C:/secret.py",
    "C:secret.py", "flaskr\\app.py", "flaskr//app.py", "./flaskr/app.py", "flaskr/./app.py",
    "flaskr/app.py/", ".env", "flaskr/.env.local", ".venv/site.py", "flaskr/__pycache__/app.pyc",
    "node_modules/pkg/index.py", "flaskr/build/output.py",
])
def test_relative_paths_reject_escape_and_nonpublic_inputs(path):
    with pytest.raises(ValueError):
        relative_path(path)


def test_confined_paths_and_public_snapshot_keep_hidden_inputs_out(workspace):
    assert relative_path("flaskr/templates/index.html") == "flaskr/templates/index.html"
    assert confined(workspace.candidate, "flaskr/app.py", exists=True).is_file()
    with pytest.raises(ValueError, match="missing"):
        confined(workspace.candidate, "flaskr/missing.py", exists=True)
    write(workspace.candidate / ".env", "LOCAL_FIXTURE_KEY=not-a-secret\n")
    write(workspace.candidate / ".venv/ignored.py", "raise AssertionError('not public')\n")
    # Excluded caches/environment files are outside the reproducible source seal.
    snapshot = workspace.snapshot()
    assert snapshot["sources"]["flaskr/app.py"].startswith("VALUE = 1")
    assert "tests/test_original.py" in snapshot["sources"]
    assert all(".env" not in name and ".venv" not in name and "private" not in name for name in snapshot["sources"])
    assert "independent_acceptance" in snapshot["scope_manifest"]["frozen"]
    assert set(snapshot["scope_manifest"]["allowed_changes"]) == workspace.allowed
    with pytest.raises(ValueError, match="byte budget"):
        public_sources(workspace.candidate, byte_limit=1)


@pytest.mark.parametrize("outside", [False, True], ids=["internal-link", "escaping-link"])
def test_real_symlink_is_rejected_even_if_its_target_is_inside_root(tmp_path, outside):
    root = tmp_path / "project"
    target = write((tmp_path / "outside.py") if outside else (root / "real.py"), "VALUE = 1\n")
    root.mkdir(exist_ok=True)
    link = root / "linked.py"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as error:
        pytest.skip("Host cannot create test symlinks: " + str(error))
    with pytest.raises(ValueError, match="outside|Symlink|symlink"):
        confined(root, "linked.py", exists=True)
    with pytest.raises(ValueError, match="symlink"):
        files_under(root)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction boundary")
def test_real_windows_junction_cannot_redirect_project_reads(tmp_path):
    import _winapi
    if not hasattr(_winapi, "CreateJunction"):
        pytest.skip("This Python does not expose the local junction primitive")
    root = tmp_path / "project"
    root.mkdir()
    target = write(tmp_path / "sibling-target/app.py", "VALUE = 1\n").parent
    junction = root / "redirected"
    _winapi.CreateJunction(str(target), str(junction))
    assert junction.is_junction()
    with pytest.raises(ValueError, match="outside|junction"):
        confined(root, "redirected/app.py", exists=True)
    with pytest.raises(ValueError, match="junction"):
        files_under(root)


def test_source_pinning_rejects_changes_before_snapshot_copy(registered):
    settings, project = registered
    write(Path(project["baseline"]) / "flaskr/app.py", "VALUE = 999\n")
    with pytest.raises(ValueError, match="Pinned source snapshot changed"):
        ProjectWorkspace(settings, "b" * 32, project)


@pytest.mark.parametrize("field", ["baseline", "acceptance"])
def test_registered_source_and_hidden_suite_cannot_point_outside_workspace(registered, field):
    settings, project = registered
    project = copy.deepcopy(project)
    project[field] = str(settings.root.parent / "outside-never-opened")
    with pytest.raises(ValueError, match="within the local workspace"):
        ProjectWorkspace(settings, "d" * 32, project)


def test_snapshot_never_overwrites_a_prior_run_and_checks_registered_scope(registered, workspace):
    settings, project = registered
    with pytest.raises(ValueError, match="already exists"):
        ProjectWorkspace(settings, "a" * 32, project)
    with pytest.raises(ValueError, match="Run ID"):
        ProjectWorkspace(settings, "../unregistered", project)
    project = copy.deepcopy(project)
    project["allowed_changes"].append("tests/test_original.py")
    with pytest.raises(ValueError, match="runtime package"):
        ProjectWorkspace(settings, "c" * 32, project)


@pytest.mark.parametrize("which", ["baseline", "original", "generated", "hidden", "candidate", "extra_candidate"])
def test_execution_seals_detect_mutation_of_each_protected_tree(workspace, which):
    freeze_generated(workspace)
    targets = {
        "baseline": workspace.baseline / "flaskr/app.py",
        "original": workspace.candidate / "tests/test_original.py",
        "generated": workspace.generated / "test_new_feature.py",
        "hidden": workspace.acceptance / "test_private_acceptance.py",
        "candidate": workspace.candidate / "flaskr/app.py",
        "extra_candidate": workspace.candidate / "flaskr/unapproved.py",
    }
    write(targets[which], "# modified outside the registered write tool\n")
    with pytest.raises(ValueError, match="modified|changed"):
        workspace.assert_integrity()
    with pytest.raises(ValueError, match="modified|changed"):
        workspace.snapshot()


def test_generated_tests_freeze_after_first_write_and_cannot_replace_originals(workspace):
    seal = freeze_generated(workspace)
    assert seal == {"test_new_feature.py": digest(workspace.generated / "test_new_feature.py")}
    with pytest.raises(ValueError, match="already frozen"):
        workspace.write_tests({"files": [{"path": "test_new_feature.py", "source": "def test_changed(): pass\n"}]})
    assert files_under(workspace.generated) == seal
    assert (workspace.candidate / "tests/test_original.py").read_text(encoding="utf-8") == "def test_original():\n    assert True\n"


@pytest.mark.parametrize("name,source", [
    ("../test_escape.py", "def test_x(): pass\n"),
    ("tests/test_original.py", "def test_x(): pass\n"),
    ("test_bad.py", "import subprocess\n"),
    ("test_bad.py", "from socket import socket\n"),
    ("test_bad.py", "eval('1 + 1')\n"),
    ("test_bad.py", "import os\nos.system('should-never-run')\n"),
])
def test_generated_test_proposals_reject_escape_paths_and_execution_requests(workspace, name, source):
    with pytest.raises(ValueError):
        workspace.write_tests({"files": [{"path": name, "source": source}]})
    assert workspace.generated_seal is None
    assert list(workspace.generated.iterdir()) == []
    workspace.assert_integrity()


def test_patch_is_a_whole_file_write_bound_to_the_current_candidate(workspace):
    freeze_generated(workspace)
    old_hash = workspace.candidate_hash()
    baseline = (workspace.baseline / "flaskr/app.py").read_bytes()
    replacement = 'VALUE = 2\n\ndef feature():\n    return "补全完成"\n'
    result = workspace.apply_patch({"baseline_sha256": old_hash, "files": [{"path": "flaskr/app.py", "source": replacement}]})
    assert (workspace.candidate / "flaskr/app.py").read_bytes() == replacement.encode("utf-8")
    assert (workspace.baseline / "flaskr/app.py").read_bytes() == baseline
    assert result["candidate_sha256"] == workspace.candidate_hash() != old_hash
    assert result["changed_files"] == ["flaskr/app.py"]
    assert result["hashes"] == {"flaskr/app.py": digest(workspace.candidate / "flaskr/app.py")}
    workspace.assert_integrity()
    with pytest.raises(ValueError, match="stale candidate"):
        workspace.apply_patch({"baseline_sha256": old_hash, "files": [{"path": "flaskr/app.py", "source": "VALUE = 3\n"}]})
    assert (workspace.candidate / "flaskr/app.py").read_bytes() == replacement.encode("utf-8")


@pytest.mark.parametrize("forbidden", ["tests/test_original.py", "../outside.py", "flaskr/unapproved.py", "flaskr/.env"])
def test_patch_rejects_unallowed_paths_before_any_file_is_written(workspace, forbidden):
    before = files_under(workspace.candidate)
    with pytest.raises(ValueError, match="unauthorized"):
        workspace.apply_patch({"baseline_sha256": workspace.candidate_hash(), "files": [
            {"path": "flaskr/app.py", "source": "VALUE = 2\n"},
            {"path": forbidden, "source": "VALUE = 3\n"},
        ]})
    assert files_under(workspace.candidate) == before
    workspace.assert_integrity()


def test_patch_validates_all_syntax_before_first_write(workspace):
    before = files_under(workspace.candidate)
    with pytest.raises(SyntaxError):
        workspace.apply_patch({"baseline_sha256": workspace.candidate_hash(), "files": [
            {"path": "flaskr/app.py", "source": "VALUE = 2\n"},
            {"path": "flaskr/__init__.py", "source": "def broken(\n"},
        ]})
    assert files_under(workspace.candidate) == before


def fake_tool_process(workspace, monkeypatch, *, evidence=None, mutate=None, exit_code=0, missing=False):
    """Fake only the OS launch; production code still builds its request/logs."""
    captured = {}
    process = SimpleNamespace(pid=24680, returncode=exit_code)

    def wait(timeout):
        captured["timeout"] = timeout
        return exit_code

    process.wait = wait

    def launch(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        request = json.loads(Path(command[2]).read_text(encoding="utf-8"))
        captured["request"] = request
        if mutate:
            mutate()
        if not missing:
            result = {"operation": request["operation"], "candidate_sha256": workspace.candidate_hash(), "regression": {"passed": 1, "total": 1}}
            result.update(evidence or {})
            Path(request["output"]).write_text(json.dumps(result), encoding="utf-8")
        return process

    monkeypatch.setattr(project_tools.subprocess, "Popen", launch)
    return captured, process


def test_registered_tool_launch_uses_fixed_worker_and_strips_environment_credentials(workspace, monkeypatch):
    freeze_generated(workspace)
    monkeypatch.setenv("OPENAI_API_KEY", "unit-fixture-not-a-real-key")
    monkeypatch.setenv("TESTFIX_LLM_API_KEY", "unit-fixture-not-a-real-key")
    captured, _ = fake_tool_process(workspace, monkeypatch)
    result = run_project_tool(workspace.settings, workspace, "candidate_public", timeout_s=7, round_number=2)
    command, kwargs, request = captured["command"], captured["kwargs"], captured["request"]
    assert command[:2] == [sys.executable, str(Path(project_tool_worker.__file__).resolve())]
    assert len(command) == 3 and Path(command[2]).is_relative_to(workspace.directory / "tool_calls")
    assert kwargs["cwd"] == workspace.settings.root
    assert not kwargs.get("shell", False)
    assert "OPENAI_API_KEY" not in kwargs["env"] and "TESTFIX_LLM_API_KEY" not in kwargs["env"]
    assert request["operation"] == "candidate_public"
    assert request["candidate"] == str(workspace.candidate)
    assert request["generated"] == str(workspace.generated)
    assert captured["timeout"] == 7
    assert result["candidate_sha256"] == workspace.candidate_hash()
    assert result["duration_s"] >= 0
    assert (workspace.settings.root / result["artifact"]).is_file()


def test_arbitrary_model_command_is_rejected_before_any_launch(workspace, monkeypatch):
    def forbidden_launch(*args, **kwargs):
        pytest.fail("A model-supplied shell request reached process execution")
    monkeypatch.setattr(project_tools.subprocess, "Popen", forbidden_launch)
    with pytest.raises(ValueError, match="Unknown fixed project tool"):
        run_project_tool(workspace.settings, workspace, "python -c print('untrusted')", timeout_s=1)
    assert not (workspace.directory / "tool_calls").exists()


@pytest.mark.parametrize("evidence", [{"candidate_sha256": "stale-version"}, {"operation": "verify"}])
def test_tool_results_must_match_both_operation_and_candidate(workspace, monkeypatch, evidence):
    fake_tool_process(workspace, monkeypatch, evidence=evidence)
    with pytest.raises(ValueError, match="not bound"):
        run_project_tool(workspace.settings, workspace, "candidate_public", timeout_s=1)


@pytest.mark.parametrize("missing,exit_code", [(True, 0), (False, 17)])
def test_tool_failure_or_missing_evidence_cannot_be_reported_as_success(workspace, monkeypatch, missing, exit_code):
    fake_tool_process(workspace, monkeypatch, missing=missing, exit_code=exit_code)
    with pytest.raises(RuntimeError, match="Project tool failed"):
        run_project_tool(workspace.settings, workspace, "candidate_public", timeout_s=1)


def test_mutation_during_a_tool_call_is_rejected_even_with_matching_runtime_hash(workspace, monkeypatch):
    freeze_generated(workspace)
    fake_tool_process(workspace, monkeypatch, mutate=lambda: write(workspace.generated / "test_new_feature.py", "def test_forged(): pass\n"))
    with pytest.raises(ValueError, match="Frozen generated tests modified"):
        run_project_tool(workspace.settings, workspace, "candidate_public", timeout_s=1)


def test_wall_timeout_kills_the_launched_tree_and_returns_no_success(workspace, monkeypatch):
    process = SimpleNamespace(pid=24680, returncode=None)
    waited, killed = [], []
    def wait(timeout):
        waited.append(timeout)
        raise subprocess.TimeoutExpired("fixed-worker", timeout)
    process.wait = wait
    monkeypatch.setattr(project_tools.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(project_tools, "_kill_tree", lambda item: killed.append(item))
    with pytest.raises(TimeoutError, match="wall-time budget"):
        run_project_tool(workspace.settings, workspace, "candidate_public", timeout_s=.05)
    assert waited == [.05]
    assert killed == [process]


def test_tree_termination_targets_the_worker_descendants_and_waits(monkeypatch):
    calls, waits = [], []
    process = SimpleNamespace(pid=24680, poll=lambda: None, wait=lambda timeout: waits.append(timeout))
    if os.name == "nt":
        monkeypatch.setattr(project_tools.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)))
    else:
        monkeypatch.setattr(project_tools.os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    project_tools._kill_tree(process)
    if os.name == "nt":
        command, kwargs = calls[0]
        assert command == ["taskkill", "/PID", "24680", "/T", "/F"]
        assert kwargs["timeout"] > 0
    else:
        import signal
        assert calls == [(24680, signal.SIGKILL)]
    assert waits and waits[0] > 0


def worker_request(workspace, **extra):
    directory = workspace.directory / "tool_calls/worker-fixture"
    directory.mkdir(parents=True)
    request = {"operation": "candidate_public", "root": str(workspace.settings.root), "run_id": workspace.directory.name,
               "baseline": str(workspace.baseline), "candidate": str(workspace.candidate), "generated": str(workspace.generated),
               "acceptance": str(workspace.acceptance), "python": workspace.project["python"], "output": str(directory / "result.json")}
    request.update(extra)
    path = write(directory / "request.json", json.dumps(request))
    return path, request


def patch_worker_runtime(monkeypatch, request_path):
    events = []
    def limits(**kwargs):
        events.append(("limits", kwargs))
        return SimpleNamespace(backend="unit-test-guard", memory_bytes=123, cpu_seconds=4)
    def suite(python, target, tests, directory, name):
        events.append(("suite", Path(python), Path(target), Path(tests), name))
        return {"passed": 1, "total": 1, "exit_code": 0}
    monkeypatch.setattr(project_tool_worker, "apply_limits", limits)
    monkeypatch.setattr(project_eval, "_run_suite", suite)
    monkeypatch.setattr(sys, "argv", ["project_tool_worker.py", str(request_path)])
    return events


@pytest.mark.parametrize("operation,target_name", [("baseline_public", "baseline"), ("candidate_public", "candidate")])
def test_worker_public_operation_ignores_injected_shell_and_never_reads_hidden_suite(workspace, monkeypatch, operation, target_name):
    freeze_generated(workspace)
    path, request = worker_request(workspace, operation=operation, command=["should-never-run"], shell="should-never-run", cwd="model-request")
    events = patch_worker_runtime(monkeypatch, path)
    monkeypatch.setattr(project_delivery, "verify_delivery", lambda *args: pytest.fail("Public testing invoked delivery/hidden validation"))
    assert project_tool_worker.main() == 0
    assert events[0][0] == "limits"
    suites = [event for event in events if event[0] == "suite"]
    target = getattr(workspace, target_name)
    assert len(suites) == 2
    assert {event[3] for event in suites} == {target / "tests", workspace.generated}
    assert all(event[2] == target for event in suites)
    assert workspace.acceptance not in {event[3] for event in suites}
    result = json.loads(Path(request["output"]).read_text(encoding="utf-8"))
    assert result["candidate_sha256"] == workspace.candidate_hash()
    assert result["limits"]["backend"] == "unit-test-guard"


def test_worker_verify_uses_fixed_hidden_paths_and_a_host_built_delivery_manifest(workspace, monkeypatch):
    path, request = worker_request(workspace, operation="verify", command="model-shell-is-data")
    events = patch_worker_runtime(monkeypatch, path)
    manifests = []
    def delivery(manifest_path, settings, run):
        manifests.append((json.loads(manifest_path.read_text(encoding="utf-8")), settings, run))
        return {"build": {"passed": 1, "total": 1}}
    monkeypatch.setattr(project_delivery, "verify_delivery", delivery)
    assert project_tool_worker.main() == 0
    suites = [event for event in events if event[0] == "suite"]
    assert [event[2] for event in suites] == [workspace.baseline, workspace.candidate]
    assert all(event[3] == workspace.acceptance for event in suites)
    manifest, settings, run = manifests[0]
    assert manifest == {"id": "Project/flaskr", "candidate": str(workspace.candidate), "python": workspace.project["python"]}
    assert settings.root == workspace.settings.root and run["id"] == workspace.directory.name
    assert json.loads(Path(request["output"]).read_text(encoding="utf-8"))["operation"] == "verify"


@pytest.mark.parametrize("field", ["candidate", "acceptance", "python", "output"])
def test_worker_rejects_paths_outside_registered_root_before_running_any_suite(workspace, monkeypatch, field):
    path, _ = worker_request(workspace, **{field: str(workspace.settings.root.parent / "outside-never-opened")})
    events = patch_worker_runtime(monkeypatch, path)
    with pytest.raises(ValueError, match="Tool paths must stay"):
        project_tool_worker.main()
    assert [event[0] for event in events] == ["limits"]


def test_worker_request_file_itself_must_be_in_artifacts(workspace, monkeypatch):
    path, request = worker_request(workspace)
    unregistered = write(workspace.settings.root / "not-artifacts/request.json", json.dumps(request))
    events = patch_worker_runtime(monkeypatch, unregistered)
    with pytest.raises(ValueError, match="Tool paths must stay"):
        project_tool_worker.main()
    assert [event[0] for event in events] == ["limits"]


def test_worker_refuses_an_unknown_operation_without_executing_a_suite(workspace, monkeypatch):
    path, request = worker_request(workspace, operation="arbitrary-model-command")
    events = patch_worker_runtime(monkeypatch, path)
    with pytest.raises(ValueError, match="Unknown fixed operation"):
        project_tool_worker.main()
    assert [event[0] for event in events] == ["limits"]
    assert not Path(request["output"]).exists()
