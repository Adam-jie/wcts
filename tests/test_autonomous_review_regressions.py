"""Independent controller regressions; all reports and executors are local fixtures."""
from copy import deepcopy
import json
import os

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from testfix import api
from testfix.config import Settings
from testfix.reports import now, save_run
from test_project_workflow import harness  # shared synthetic controller fixture


def external_report(settings, identifier="c" * 32, status="running"):
    report = {"id": identifier, "created_at": now(), "status": status,
              "execution_owner_pid": os.getpid(), "provider": "codex_cli",
              "runner": "project_process", "kind": "autonomous_project",
              "events": [], "tasks": [], "summary": {"total": 1, "accepted": 0}}
    save_run(settings.artifacts / identifier, report)
    return report


@pytest.fixture
def store(tmp_path):
    value = api.RunStore(Settings(root=tmp_path))
    yield value
    value.pool.shutdown(wait=True)


def test_external_completion_refreshes_snapshot_and_releases_submission(store, monkeypatch):
    report = external_report(store.settings)
    observer = api.RunStore(store.settings)
    try:
        assert observer.snapshot(report["id"])["status"] == "running"
        report.update(status="completed", summary={"total": 1, "accepted": 1})
        save_run(store.settings.artifacts / report["id"], report)
        assert observer.snapshot(report["id"])["summary"]["accepted"] == 1
        dispatched = []
        monkeypatch.setattr(observer.pool, "submit", lambda *args: dispatched.append(args))
        created = observer.submit_project(api.ProjectRunRequest())
        assert created["status"] == "queued" and len(dispatched) == 1
    finally:
        observer.pool.shutdown(wait=True)


def test_external_owner_death_is_rechecked_after_initial_load(store, monkeypatch):
    alive = [True]
    monkeypatch.setattr(api, "_owner_alive", lambda identifier: alive[0])
    report = external_report(store.settings)
    observer = api.RunStore(store.settings)
    try:
        assert observer.snapshot(report["id"])["status"] == "running"
        alive[0] = False
        assert observer.snapshot(report["id"])["status"] == "interrupted"
        saved = json.loads((store.settings.artifacts / report["id"] / "report.json").read_text(encoding="utf-8"))
        assert saved["status"] == "interrupted"
    finally:
        observer.pool.shutdown(wait=True)


@pytest.mark.parametrize("kind", ["project", "function"])
def test_submission_discovers_a_new_external_run_before_dispatch(store, monkeypatch, kind):
    external_report(store.settings)
    dispatched = []
    monkeypatch.setattr(store.pool, "submit", lambda *args: dispatched.append(args))
    with pytest.raises(HTTPException) as failure:
        if kind == "project":
            store.submit_project(api.ProjectRunRequest())
        else:
            store.submit(api.RunRequest(task_ids=["unit-only"]))
    assert failure.value.status_code == 409 and not dispatched


def test_list_discovers_reports_created_after_app_start_without_touching_root(tmp_path):
    (tmp_path / "web").mkdir()
    application = api.create_app(Settings(root=tmp_path))
    try:
        report = external_report(application.state.store.settings, status="completed")
        with TestClient(application) as client:
            values = client.get("/api/runs").json()["runs"]
        assert [(value["id"], value["status"]) for value in values] == [(report["id"], "completed")]
    finally:
        application.state.store.pool.shutdown(wait=True)


def test_same_process_peer_store_does_not_claim_the_local_executor(store, monkeypatch):
    dispatched = []
    monkeypatch.setattr(store.pool, "submit", lambda *args: dispatched.append(args))
    identifier = store.submit_project(api.ProjectRunRequest())["id"]
    observer = api.RunStore(store.settings)
    try:
        # Both stores have the same PID. Only the original store owns its future.
        report = deepcopy(dispatched[0][1])
        report["status"] = "completed"
        save_run(store.settings.artifacts / identifier, report)
        assert observer.snapshot(identifier)["status"] == "completed"
        assert store.snapshot(identifier)["status"] == "queued"
    finally:
        observer.pool.shutdown(wait=True)


def test_owner_final_report_wins_when_published_before_death_check(store, monkeypatch):
    report = external_report(store.settings)

    def exited_after_publishing(identifier):
        report["status"] = "completed"
        save_run(store.settings.artifacts / report["id"], report)
        return False

    monkeypatch.setattr(api, "_owner_alive", exited_after_publishing)
    assert store.snapshot(report["id"])["status"] == "completed"
    saved = json.loads((store.settings.artifacts / report["id"] / "report.json").read_text(encoding="utf-8"))
    assert saved["status"] == "completed" and "error" not in saved


def test_feedback_correction_consumes_real_attempt_budget_and_plan_allowance(harness, monkeypatch):
    from testfix.project_workflow import OUTPUT_CAPS

    original = harness.provider.invoke
    rejected = [False]

    def model_response(skill, context, schema, **limits):
        output = original(skill, context, schema, **limits)
        if skill == "explore-repository" and not rejected[0]:
            rejected[0] = True
            output["candidates"][0]["path"] = "flaskr/missing-proposed-file.py"
            harness.provider.calls[-1]["result"] = deepcopy(output)
        return output

    monkeypatch.setattr(harness.provider, "invoke", model_response)
    result = harness.execute()
    assert result["status"] == "completed"
    calls = [call for call in harness.provider.invocations if call["skill"] == "explore-repository"]
    stage = next(stage for stage in result["plan"]["selected"] if stage["skill_id"] == "explore-repository")
    capability = next(capability for capability in result["workflow"]["capabilities"] if capability["skill_id"] == "explore-repository")
    assert len(calls) == capability["attempt"] == stage["max_invocations"] == 2
    assert "validation_feedback" in calls[1]["context"]
    assert calls[0]["token_cap"] == calls[1]["token_cap"] == stage["output_token_cap_per_call"] == OUTPUT_CAPS["explore-repository"]
    assert stage["duration_ms"] == 2 * 120 * 1000
    assert result["workflow"]["budget"]["calls"] == len(result["model_calls"]) == 9
    assert result["workflow"]["budget"]["reported_tokens"] == 9 * harness.provider.reported_tokens
    assert capability["artifact"]["path"].endswith("attempt-2.json")
