"""Project transports must keep hidden data and secrets out of model calls."""
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import threading
import time

import httpx
import pytest

from testfix.config import Settings
from testfix import project_models as module
from testfix.project_models import ProjectModelError, ProjectModelProvider


SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}},
          "required": ["summary"], "additionalProperties": False}
_HTTP_CLIENT = httpx.Client


def provider(tmp_path, name="openai", **kwargs):
    settings = Settings(root=tmp_path, base_url="https://model.example/v1", api_key="private-project-credential",
                        agentarts_url="https://runtime.example/invocations", agentarts_key="private-runtime-credential")
    kwargs.setdefault("http_transport", module.request_http_inline)
    return ProjectModelProvider(name, settings, artifact_dir=tmp_path / "calls", **kwargs)


def mock_http(monkeypatch, handler):
    clients = []
    def client(**kwargs):
        clients.append(kwargs)
        return _HTTP_CLIENT(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(module.httpx, "Client", client)
    return clients


def response(summary="public", model="gpt-5.6-sol"):
    return httpx.Response(200, json={"model": model, "choices": [{"message": {"content": json.dumps({"summary": summary})}}],
                                     "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "secret": "ignore"}})


def mock_cli(monkeypatch, handler):
    """Stub Popen's stdio lifecycle, including a successful reap after timeout."""
    class Process:
        pid = 987654
        returncode = None
        stdin = stdout = stderr = None
        def __init__(self, command, **kwargs):
            self.command, self.kwargs, self.timed_out = command, kwargs, False
        def communicate(self, input=None, timeout=None):
            if self.timed_out:
                self.returncode = -9
                return "", ""
            try:
                completed = handler(self.command, **self.kwargs, input=input, timeout=timeout)
            except subprocess.TimeoutExpired:
                self.timed_out = True
                raise
            self.returncode = completed.returncode
            return completed.stdout, completed.stderr
        def kill(self):
            self.returncode = -9
    monkeypatch.setattr(module.subprocess, "Popen", Process)
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""))
    if module.os.name != "nt":
        monkeypatch.setattr(module.os, "killpg", lambda *args: None)


@pytest.mark.parametrize("profile", ["flaskr_project", "python_package"])
@pytest.mark.parametrize("skill_id", ["plan-workflow", "explore-repository", "ground-code", "analyze-requirements",
                                     "generate-tests", "diagnose-results", "repair-code", "review-patch"])
def test_all_project_llm_skills_apply_body_contract_and_schema(tmp_path, monkeypatch, skill_id, profile):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return response()
    clients = mock_http(monkeypatch, handler)
    model = provider(tmp_path, profile=profile)
    assert model.invoke(skill_id, {"source_index": ["flaskr/auth.py"]}, SCHEMA) == {"summary": "public"}
    payload = requests[0]
    prompt = payload["messages"][0]["content"]
    assert skill_id in prompt and f"Profile: {profile}" in prompt
    assert "Machine-readable Skill contract" in prompt and "Required output JSON Schema" in prompt
    assert payload["model"] == "gpt-5.6-sol" and payload["response_format"]["json_schema"]["strict"] is True
    assert clients[0]["trust_env"] is False and clients[0]["follow_redirects"] is False
    assert model.calls[0]["usage"] == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    assert model.calls[0]["actual_model"] == "gpt-5.6-sol"
    directory = Path(model.calls[0]["artifact_dir"])
    assert json.loads((directory / "result.json").read_text(encoding="utf-8")) == {"summary": "public"}
    assert "private-project-credential" not in json.dumps(model.calls)


@pytest.mark.parametrize("key", ["hidden_tests", "Hidden-Acceptance", "reference_solution", "canonical_solution",
                                  "independent_acceptance", "acceptance_code"])
def test_protected_context_rejected_recursively_before_transmission(tmp_path, monkeypatch, key):
    clients = mock_http(monkeypatch, lambda _: pytest.fail("Protected data was transmitted"))
    model = provider(tmp_path)
    with pytest.raises(ValueError, match="Protected"):
        model.invoke("repair-code", {"public": [{"nested": {key: "sealed-marker"}}]}, SCHEMA)
    assert clients == [] and model.calls == []


def test_cli_uses_snapshot_stdin_no_source_cwd_or_secret_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-secret")
    monkeypatch.setenv("CUSTOM_TOKEN", "another-secret")
    seen = []
    def run(command, **kwargs):
        seen.append((command, kwargs))
        directory = Path(kwargs["cwd"])
        assert directory != tmp_path and directory.exists()
        assert sorted(path.name for path in directory.iterdir()) == ["schema.json"]
        assert "OPENAI_API_KEY" not in kwargs["env"] and "CUSTOM_TOKEN" not in kwargs["env"]
        assert "public-marker" in kwargs["input"] and command[-1] == "-"
        assert "--ignore-user-config" in command and "--ignore-rules" in command
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert command[command.index("--model") + 1] == "gpt-5.6-sol"
        assert "shell_tool" in command and "--no-daemon" in command
        Path(command[command.index("--output-last-message") + 1]).write_text('{"summary":"done"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, '{"type":"thread.started"}\n', "")
    mock_cli(monkeypatch, run)
    model = provider(tmp_path, "codex_cli", codex_executable="codex-stub")
    assert model.invoke("plan-workflow", {"public": "public-marker"}, SCHEMA, timeout_s=3, token_cap=200) == {"summary": "done"}
    assert not Path(seen[0][1]["cwd"]).exists()
    assert seen[0][1]["timeout"] == 3
    assert model.calls[0]["usage"] is None and model.calls[0]["actual_model"] is None
    assert model.calls[0]["model_evidence"] == "requested_cli_flag_only"
    assert model.metadata()["token_cap_enforcement"] == "prompt_only"
    assert model.metadata()["cli_controls"]["os_read_isolation_claimed"] is False


@pytest.mark.parametrize("failure,code", [("timeout", "timeout"), ("exit", "cli_exit"), ("missing", "missing_output"),
                                           ("tool", "unexpected_tool"), ("wrong_model", "model_mismatch")])
def test_cli_failures_have_no_fake_fallback(tmp_path, monkeypatch, failure, code):
    def run(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"], stderr="private-project-credential")
        if failure == "exit":
            return subprocess.CompletedProcess(command, 1, "", "private-project-credential")
        if failure != "missing":
            Path(command[command.index("--output-last-message") + 1]).write_text('{"summary":"done"}', encoding="utf-8")
        event = ({"type": "item.completed", "item": {"type": "command_execution"}} if failure == "tool"
                 else {"model": "gpt-6-sol"} if failure == "wrong_model" else {})
        return subprocess.CompletedProcess(command, 0, json.dumps(event), "")
    mock_cli(monkeypatch, run)
    model = provider(tmp_path, "codex_cli", codex_executable="codex-stub")
    with pytest.raises(ProjectModelError) as error:
        model.invoke("repair-code", {"public": "snapshot"}, SCHEMA)
    assert error.value.code == code
    assert model.calls[0]["status"] == "failed"
    assert "private-project-credential" not in json.dumps(model.calls)


@pytest.mark.parametrize("content", ['```json\n{"summary":"x"}\n```', 'preamble {"summary":"x"}',
                                     '{"summary":"x"} trailing', '{"summary":"a","summary":"b"}',
                                     '{"summary":NaN}', '[]', '{"summary":3}', '{"summary":"x","extra":1}'])
def test_invalid_model_json_and_schema_are_rejected(tmp_path, monkeypatch, content):
    mock_http(monkeypatch, lambda _: httpx.Response(200, json={"choices": [{"message": {"content": content}}]}))
    model = provider(tmp_path)
    with pytest.raises(ProjectModelError):
        model.invoke("review-patch", {}, SCHEMA)
    assert model.calls[0]["status"] == "failed"


def test_http_error_does_not_log_remote_body_url_or_secret(tmp_path, monkeypatch):
    mock_http(monkeypatch, lambda _: httpx.Response(401, text="private-project-credential leaked body"))
    model = provider(tmp_path)
    with pytest.raises(ProjectModelError, match="HTTP 401") as error:
        model.invoke("review-patch", {}, SCHEMA)
    saved = json.dumps(model.calls) + str(error.value)
    assert "private-project-credential" not in saved and "leaked body" not in saved


def test_http_transport_exception_never_exposes_credentials(tmp_path, monkeypatch):
    def fail(_):
        raise httpx.ConnectError("https://private-project-credential@example.com")
    mock_http(monkeypatch, fail)
    model = provider(tmp_path)
    with pytest.raises(ProjectModelError) as error:
        model.invoke("review-patch", {}, SCHEMA)
    assert error.value.code == "transport_error"
    assert "private-project-credential" not in str(error.value) + json.dumps(model.calls)


def test_secret_echo_is_redacted_and_wrong_actual_model_rejected(tmp_path, monkeypatch):
    mock_http(monkeypatch, lambda _: response("private-project-credential"))
    model = provider(tmp_path)
    assert model.invoke("review-patch", {}, SCHEMA)["summary"] == "[REDACTED]"
    assert "private-project-credential" not in json.dumps(model.calls)
    mock_http(monkeypatch, lambda _: response(model="gpt-6-sol"))
    with pytest.raises(ProjectModelError) as error:
        provider(tmp_path).invoke("review-patch", {}, SCHEMA)
    assert error.value.code == "model_mismatch"


@pytest.mark.parametrize("profile", ["flaskr_project", "python_package"])
def test_agentarts_carries_full_project_skill_contract(tmp_path, monkeypatch, profile):
    seen = []
    def handler(request):
        payload = json.loads(request.content)
        seen.append(payload)
        return httpx.Response(200, json={"contract_version": module.PROJECT_CONTRACT, "result": {"summary": "ok"}, "model": "gpt-5.6-sol"})
    mock_http(monkeypatch, handler)
    model = provider(tmp_path, "agentarts", profile=profile)
    assert model.invoke("ground-code", {"sources": {}}, SCHEMA) == {"summary": "ok"}
    assert seen[0]["skill_id"] == "ground-code" and "instructions" in seen[0] and seen[0]["output_schema"] == SCHEMA
    assert seen[0]["profile"] == profile
    assert model.calls[0]["request"]["profile"] == profile


def test_ollama_passes_schema_budget_and_measured_usage(tmp_path, monkeypatch):
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"model": "local-model", "message": {"content": '{"summary":"ok"}'},
                                       "prompt_eval_count": 14, "eval_count": 9})
    mock_http(monkeypatch, handler)
    model = provider(tmp_path, "ollama", model="local-model")
    model.invoke("ground-code", {}, SCHEMA, token_cap=33)
    assert seen[0]["format"] == SCHEMA and seen[0]["options"]["num_predict"] == 33
    assert model.calls[0]["usage"] == {"prompt_tokens": 14, "completion_tokens": 9}


def test_changed_trusted_skill_is_rejected_before_transport(tmp_path, monkeypatch):
    model = provider(tmp_path)
    monkeypatch.setattr(model.skill_catalog, "verify_snapshot", lambda _: (_ for _ in ()).throw(ValueError("changed skill")))
    clients = mock_http(monkeypatch, lambda _: pytest.fail("Stale prompt transmitted"))
    with pytest.raises(ValueError, match="changed skill"):
        model.invoke("plan-workflow", {}, SCHEMA)
    assert clients == [] and model.calls == []


def test_cli_provider_usage_is_measured_without_claiming_billing_audit(tmp_path, monkeypatch):
    def run(command, **kwargs):
        Path(command[command.index("--output-last-message") + 1]).write_text('{"summary":"ok"}', encoding="utf-8")
        event = {"type": "turn.completed", "usage": {"input_tokens": 25, "cached_input_tokens": 12,
                                                       "output_tokens": 9, "reasoning_output_tokens": 3}}
        return subprocess.CompletedProcess(command, 0, json.dumps(event), "")
    mock_cli(monkeypatch, run)
    model = provider(tmp_path, "codex_cli", codex_executable="codex-stub")
    model.invoke("plan-workflow", {}, SCHEMA)
    assert model.calls[0]["usage"] == {"input_tokens": 25, "cached_input_tokens": 12,
                                       "output_tokens": 9, "reasoning_output_tokens": 3, "total_tokens": 34}
    assert model.calls[0]["usage_source"] == "cli_turn_completed_provider_reported"
    assert "not a billing audit" in model.calls[0]["usage_note"]


def test_cli_completed_generation_can_report_cleanup_timeout(tmp_path, monkeypatch):
    def run(command, **kwargs):
        Path(command[command.index("--output-last-message") + 1]).write_text('{"summary":"ok"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, -9, '{"type":"turn.completed","usage":{"input_tokens":20,"output_tokens":5}}', ""), True
    monkeypatch.setattr(module, "_run_cli_process", run)
    model = provider(tmp_path, "codex_cli", codex_executable="codex-stub")
    assert model.invoke("plan-workflow", {}, SCHEMA) == {"summary": "ok"}
    assert model.calls[0]["cleanup_timeout"] is True and model.calls[0]["usage"]["total_tokens"] == 25


def test_cli_timeout_kills_its_process_tree_and_reaps(monkeypatch, tmp_path):
    killed, reaped = [], []
    class Process:
        pid = 876543
        returncode = -9
        def __init__(self, command, **kwargs):
            assert kwargs.get("creationflags") or kwargs.get("start_new_session")
        def communicate(self, input=None, timeout=None):
            if input is not None:
                raise subprocess.TimeoutExpired(["stub"], timeout, output=b'{"type":"turn.started"}\n')
            reaped.append(True)
            return '{"type":"turn.started"}\n', ""
        def kill(self):
            killed.append("direct")
    monkeypatch.setattr(module.subprocess, "Popen", Process)
    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs: killed.append(command))
    if module.os.name != "nt":
        monkeypatch.setattr(module.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    completed, deadline_hit = module._run_cli_process(["stub"], prompt="public", directory=str(tmp_path), timeout_s=0.1)
    assert deadline_hit is True and reaped == [True]
    if module.os.name == "nt":
        assert killed[0] == ["taskkill", "/PID", "876543", "/T", "/F"]
    else:
        assert killed[0][0] == 876543


def test_provider_status_caches_safe_login_and_never_probes_model(tmp_path, monkeypatch):
    module._STATUS_CACHE.clear()
    calls = []
    monkeypatch.setattr(module.shutil, "which", lambda _: "codex-stub")
    def status(executable):
        calls.append(executable)
        return {"available": True, "logged_in": True, "authentication": "chatgpt_oauth"}
    monkeypatch.setattr(module, "codex_cli_status", status)
    for _ in range(2):
        statuses = module.project_provider_status(Settings(root=tmp_path))
        assert statuses[0]["id"] == "codex_cli" and statuses[0]["configured"] is True
    assert calls == ["codex-stub"]
    module._STATUS_CACHE.clear()


def test_deterministic_verifier_cannot_be_satisfied_by_a_model(tmp_path):
    model = provider(tmp_path)
    with pytest.raises(ValueError, match="Deterministic"):
        model.invoke("verify-delivery", {}, SCHEMA)
    assert model.calls == []


@contextmanager
def local_model_server(mode, body=None):
    """Actual sockets keep the worker/deadline regression independent of mocks."""
    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            received.append({"headers": dict(self.headers), "body": json.loads(raw)})
            try:
                content = body if body is not None else b'{"answer":"public"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                if mode != "oversize_stream":
                    self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                if mode == "trickle":
                    for byte in content:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.04)  # Every read is active; total transfer exceeds deadline.
                elif mode == "oversize_stream":
                    self.wfile.write(b"x" * 2048)
                else:
                    self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_real_http_worker_has_absolute_deadline_for_continuous_trickle():
    # Allow a cold Python/httpx worker to start under whole-suite CPU load.
    # The 40-second active stream still exceeds this absolute five-second cap.
    with local_model_server("trickle", b'{"answer":"' + b"a" * 1000 + b'"}') as (url, received):
        started = time.monotonic()
        with pytest.raises(ProjectModelError) as error:
            module.request_http_subprocess(url, {"Authorization": "Bearer pipe-only-secret"}, {}, 5)
        duration = time.monotonic() - started
        assert error.value.code == "timeout"
        assert duration < 8  # Includes process-tree cleanup, far below stream completion.
        assert received and received[0]["headers"]["Authorization"] == "Bearer pipe-only-secret"


@pytest.mark.parametrize("mode", ["normal", "oversize_stream"])
def test_real_http_worker_bounds_declared_and_streamed_response_size(mode):
    with local_model_server(mode, b'{"answer":"' + b"a" * 2048 + b'"}') as (url, _):
        envelope = module.request_http_subprocess(url, {}, {}, 5, response_limit_bytes=1024)
    assert envelope == {"ok": False, "code": "response_too_large"}


def test_real_http_worker_keeps_credentials_out_of_artifact_files(tmp_path):
    content = json.dumps({"model": "gpt-5.6-sol", "choices": [{"message": {
        "content": '{"summary":"pipe-only-secret"}'}}]}).encode()
    with local_model_server("normal", content) as (url, received):
        model = ProjectModelProvider("openai", Settings(root=tmp_path, base_url=url, api_key="pipe-only-secret"),
                                     artifact_dir=tmp_path / "artifacts")
        assert model.invoke("plan-workflow", {}, SCHEMA, timeout_s=5) == {"summary": "[REDACTED]"}
    assert received[0]["headers"]["Authorization"] == "Bearer pipe-only-secret"
    assert model.metadata()["http_deadline_enforcement"] == "worker_process"
    assert all(b"pipe-only-secret" not in path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())


def test_http_worker_uses_pipe_only_request_and_hard_killtree_helper(tmp_path, monkeypatch):
    seen = []
    def process(command, *, prompt, directory, timeout_s):
        seen.append((command, prompt, directory, timeout_s))
        assert "pipe-only-secret" not in " ".join(command)
        assert list(Path(directory).iterdir()) == []
        assert "pipe-only-secret" in prompt
        return subprocess.CompletedProcess(command, -9, '{"ok":true,"body":{}}', "secret exception"), True
    monkeypatch.setattr(module, "_run_cli_process", process)
    with pytest.raises(ProjectModelError) as error:
        module.request_http_subprocess("http://localhost", {"Authorization": "Bearer pipe-only-secret"}, {}, 0.5)
    assert error.value.code == "timeout"
    assert seen[0][0][1:3] == ["-I", "-B"] and seen[0][3] == 0.5
    assert not Path(seen[0][2]).exists()
    assert "pipe-only-secret" not in str(error.value)


def test_http_worker_never_relays_stderr_or_unexpected_envelope_errors(monkeypatch):
    monkeypatch.setattr(module, "_run_cli_process", lambda command, **kwargs: (
        subprocess.CompletedProcess(command, 1, "", "secret exception with token"), False))
    with pytest.raises(ProjectModelError) as error:
        module.request_http_subprocess("http://localhost", {}, {}, 0.5)
    assert error.value.code == "http_worker_exit"
    assert "token" not in str(error.value)


def test_real_http_worker_uses_utf8_pipe_for_unicode_response():
    body = {"message": "中文与表情🧪"}
    with local_model_server("normal", json.dumps(body, ensure_ascii=False).encode("utf-8")) as (url, _):
        envelope = module.request_http_subprocess(url, {}, {}, 5)
    assert envelope == {"ok": True, "status": 200, "body": body}


@contextmanager
def concurrent_model_server():
    """A local model fixture that can finish only when both requests overlap."""
    received, lock, overlap = [], threading.Lock(), threading.Barrier(2)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            public = json.loads(payload['messages'][1]['content'])
            with lock:
                record = {'marker': public['marker'], 'start': time.monotonic()}
                received.append(record)
            overlap.wait(timeout=8)
            body = json.dumps({'model': 'wrong-model' if public.get('fail') else 'gpt-5.6-sol',
                'choices': [{'message': {'content': json.dumps({'summary': public['marker']})}}],
                'usage': {'total_tokens': public['tokens']}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            with lock:
                record['end'] = time.monotonic()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize('one_fails', [False, True])
def test_concurrent_http_workers_overlap_and_preserve_per_call_evidence(tmp_path, one_fails):
    finished = threading.Barrier(2)
    with concurrent_model_server() as (url, received):
        model = ProjectModelProvider('openai', Settings(root=tmp_path, base_url=url, api_key='pipe-only-secret'),
            artifact_dir=tmp_path / 'parallel-calls')
        def call(marker, tokens, fail):
            result = error = trace = None
            try:
                result, trace = model.invoke_traced('ground-code', {'marker': marker, 'tokens': tokens, 'fail': fail},
                    SCHEMA, timeout_s=10)
            except ProjectModelError as failure:
                error, trace = failure.code, model.last_trace
            finished.wait(timeout=5)
            # Reading after another thread finishes must still name this call.
            assert model.last_trace == trace
            return marker, result, error, trace
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(call, 'left', 11, False), pool.submit(call, 'right', 29, one_fails)]
            outcomes = [future.result(timeout=15) for future in futures]
    assert len(received) == 2
    assert max(item['start'] for item in received) < min(item['end'] for item in received)
    traces = model.snapshot_calls()
    assert len(traces) == 2
    assert len({trace['artifact_dir'] for trace in traces}) == 2
    assert len({trace['invocation_id'] for trace in traces}) == 2
    for marker, result, error, trace in outcomes:
        assert trace['request']['context']['marker'] == marker
        assert trace['usage']['total_tokens'] == (11 if marker == 'left' else 29)
        failed = marker == 'right' and one_fails
        assert error == ('model_mismatch' if failed else None)
        assert result == (None if failed else {'summary': marker})
        assert trace['status'] == ('failed' if failed else 'completed')
        directory = Path(trace['artifact_dir'])
        assert json.loads((directory / 'trace.json').read_text(encoding='utf-8')) == trace
        assert json.loads((directory / 'result.json').read_text(encoding='utf-8')) == result
        assert json.loads((directory / 'request.json').read_text(encoding='utf-8'))['context']['marker'] == marker
    assert model.last_trace is None  # The coordinator thread never made a call.
    assert model.metadata()['actual_models'] == (['gpt-5.6-sol', 'wrong-model'] if one_fails else ['gpt-5.6-sol'])
    traces[0]['request']['context']['marker'] = 'changed-snapshot'
    assert all(call['request']['context']['marker'] != 'changed-snapshot' for call in model.snapshot_calls())


def test_concurrent_cli_owns_independent_temporary_directories_and_usage(tmp_path, monkeypatch):
    entered, directories = threading.Barrier(2), []
    def run(command, **kwargs):
        public = json.loads(kwargs['input'].split('# Public task snapshot\n', 1)[1])
        directories.append(Path(kwargs['cwd']))
        entered.wait(timeout=5)
        Path(command[command.index('--output-last-message') + 1]).write_text(
            json.dumps({'summary': public['marker']}), encoding='utf-8')
        event = {'type': 'turn.completed', 'usage': {'input_tokens': public['tokens'], 'output_tokens': 3}}
        return subprocess.CompletedProcess(command, 0, json.dumps(event), '')
    mock_cli(monkeypatch, run)
    model = provider(tmp_path, 'codex_cli', codex_executable='codex-stub')
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(model.invoke_traced, 'ground-code', {'marker': marker, 'tokens': tokens}, SCHEMA)
                   for marker, tokens in [('first', 17), ('second', 31)]]
        results = [future.result(timeout=10) for future in futures]
    assert len(set(directories)) == 2 and all(not directory.exists() for directory in directories)
    for result, trace in results:
        assert result['summary'] == trace['request']['context']['marker']
        assert trace['usage']['total_tokens'] == trace['request']['context']['tokens'] + 3
        assert trace['cleanup_timeout'] is False
    assert len({trace['artifact_dir'] for _, trace in results}) == 2


def test_trace_validation_failure_clears_stale_thread_trace(tmp_path, monkeypatch):
    mock_http(monkeypatch, lambda _: response())
    model = provider(tmp_path)
    model.invoke('ground-code', {}, SCHEMA)
    assert model.last_trace['status'] == 'completed'
    with pytest.raises(ValueError, match='Protected'):
        model.invoke('ground-code', {'hidden_tests': 'not-transmitted'}, SCHEMA)
    assert model.last_trace is None and len(model.snapshot_calls()) == 1
