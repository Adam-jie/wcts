"""Transport, local API and original framework integration contracts."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass

import httpx
import pytest
from fastapi.testclient import TestClient

import testfix.api as api_module
import testfix.providers as providers_module
from testfix.agentarts_runtime import handle_invocation
from testfix.api import create_app
from testfix.config import ROOT, Settings
from testfix.providers import ModelProvider, parse_object
from testfix.runner import Runner
from testfix.wctp_bridge import WCTPEventBridge


def mock_transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(providers_module.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_compatible_api_sends_public_context_and_reads_json(monkeypatch):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"requirements":["preserve interface"]}'}}],
                                        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "arbitrary": "ignored"}})
    mock_transport(monkeypatch, handler)
    provider = ModelProvider("openai", Settings(base_url="https://model.example/v1", model="test-model", api_key="test-secret"))
    assert provider.invoke("requirements", {"task_id": "demo", "specification": "public"})["requirements"] == ["preserve interface"]
    assert str(seen[0].url) == "https://model.example/v1/chat/completions"
    assert json.loads(seen[0].content)["model"] == "test-model"
    assert provider.calls[0]["usage"] == {"prompt_tokens": 12, "completion_tokens": 8}
    assert "test-secret" not in json.dumps(provider.calls)


def test_agentarts_uses_documented_session_and_our_business_contract(monkeypatch):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"contract_version": "testfix.role.v1", "result": {"approved": True, "reasons": []}})
    mock_transport(monkeypatch, handler)
    provider = ModelProvider("agentarts", Settings(agentarts_url="https://gateway.example/runtimes/testfix/invocations", agentarts_key="test-key"))
    assert provider.invoke("reviewer", {"task_id": "demo"})["approved"] is True
    assert seen[0].headers["Authorization"] == "Bearer test-key"
    assert len(seen[0].headers["X-Hw-Agentarts-Session-Id"]) <= 64
    assert json.loads(seen[0].content) == {"contract_version": "testfix.role.v1", "role": "reviewer", "context": {"task_id": "demo"}}


@pytest.mark.parametrize("provider_name", ["openai", "agentarts"])
def test_http_failure_never_silently_uses_replay(monkeypatch, provider_name):
    mock_transport(monkeypatch, lambda request: httpx.Response(401, text="test-secret should not be logged"))
    provider = ModelProvider(provider_name, Settings(base_url="https://model.example/v1", model="m", api_key="test-secret",
                                                   agentarts_url="https://gateway.example/invocations", agentarts_key="test-secret"))
    with pytest.raises(RuntimeError, match="HTTP 401") as error:
        provider.invoke("developer", {"task_id": "HumanEval/2"})
    assert "test-secret" not in str(error.value)
    assert not provider.calls


def test_agentarts_malformed_contract_is_rejected(monkeypatch):
    mock_transport(monkeypatch, lambda request: httpx.Response(200, json={"output": "some unrelated agent result"}))
    provider = ModelProvider("agentarts", Settings(agentarts_url="https://gateway.example/invocations", agentarts_key="test"))
    with pytest.raises(ValueError, match="contract"):
        provider.invoke("requirements", {"task_id": "demo"})


def test_runtime_handler_forwards_role_to_real_transport(monkeypatch):
    mock_transport(monkeypatch, lambda request: httpx.Response(200, json={"choices": [{"message": {"content": '{"diagnosis":"missing boundary"}'}}]}))
    result = handle_invocation({"contract_version": "testfix.role.v1", "role": "diagnosis", "context": {"task_id": "demo"}},
                               Settings(base_url="https://model.example/v1", model="m", api_key="test"), "openai")
    assert result["result"] == {"diagnosis": "missing boundary"}


@pytest.mark.parametrize("context", [{"task_id": "demo", "canonical_solution": "secret"}, {"task_id": "demo", "acceptance_code": "hidden"}])
def test_runtime_handler_refuses_oracle_data(context):
    with pytest.raises(ValueError, match="Protected data"):
        handle_invocation({"contract_version": "testfix.role.v1", "role": "tester", "context": context})


def test_runtime_handler_refuses_replay_and_recursion():
    for provider_name in ("replay", "agentarts"):
        with pytest.raises(ValueError, match="real upstream"):
            handle_invocation({"contract_version": "testfix.role.v1", "role": "tester", "context": {"task_id": "demo"}}, Settings(), provider_name)


def test_wctp_branch_false_is_control_not_failed_test():
    events = []
    @dataclass
    class Result:
        status: bool = False
        duration: float = 0.02
        outputs: dict | None = None
        error: str = ""
    bridge = WCTPEventBridge(events.append, {"branch": "Conditional", "step": "Normal"})
    bridge("started", "branch")
    bridge("finished", "branch", Result())
    bridge("finished", "step", Result())
    bridge("finished", "unknown", Result())
    bridge("loop_iteration", "branch", {"current": 1, "total": 3})
    assert [event.get("outcome") for event in events[1:4]] == ["control", "failed", "unclassified"]
    assert events[-1]["progress"] == {"current": 1, "total": 3}


def test_expected_exception_is_executed_and_wrong_exception_fails():
    code = "def f(x):\n    raise ValueError('invalid')\n"
    result = Runner().evaluate(code, code, "f", cases=[{"args": [1], "raises": "ValueError"}, {"args": [2], "raises": "TypeError"}])
    assert result["passed_indices"] == [0]
    assert result["passed"] == 1


@pytest.mark.parametrize("bad", ["[1,2]", "{} extra", "no JSON"])
def test_model_payload_parser_rejects_ambiguous_results(bad):
    with pytest.raises(ValueError):
        parse_object(bad)


def test_local_api_static_mime_credentials_and_csrf(tmp_path):
    # Keep artifact writes in disposable storage, use real static web files.
    settings = Settings(root=ROOT, base_url="https://model.example/v1", model="m", api_key="never-show-this")
    app = create_app(settings)
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert "never-show-this" not in health.text
        assert client.get("/static/app.js").headers["content-type"].startswith("text/javascript")
        assert client.get("/static/style.css").headers["content-type"].startswith("text/css")
        assert client.get("/", headers={"host": "attacker.example"}).status_code == 403
        assert client.post("/api/runs", headers={"origin": "https://attacker.example"}, json={"task_ids": ["HumanEval/0"]}).status_code == 403
        assert client.post("/api/runs", json={"task_ids": ["HumanEval/0", "HumanEval/0"]}).status_code == 400
        assert client.post("/api/runs", json={"task_ids": ["HumanEval/0"], "provider": "ollama"}).status_code == 400
        assert client.get("/api/runs/not-found").status_code == 404
    app.state.store.pool.shutdown(wait=True)


def test_api_rejects_overlapping_runs(monkeypatch, tmp_path):
    settings = Settings(root=tmp_path)
    (tmp_path / "web").mkdir()
    release = threading.Event()
    started = threading.Event()
    def deferred(run, settings, persist):
        run["status"] = "running"
        persist(run)
        started.set()
        release.wait(timeout=5)
    monkeypatch.setattr(api_module, "select_tasks", lambda *args: [])
    monkeypatch.setattr(api_module, "execute_run", deferred)
    app = create_app(settings)
    try:
        with TestClient(app) as client:
            assert client.post("/api/runs", json={"task_ids": ["HumanEval/0"]}).status_code == 202
            assert started.wait(timeout=1)
            assert client.post("/api/runs", json={"task_ids": ["HumanEval/1"]}).status_code == 409
    finally:
        release.set()
        app.state.store.pool.shutdown(wait=True)

