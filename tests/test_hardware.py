"""Explicit local fixture/loopback tests, never claims about real MCU firmware."""
from contextlib import contextmanager
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from testfix.config import Settings
from testfix import hardware
from testfix.hardware import (Assertion, HardwareRunRequest, HTTPDevice, SerialDevice,
                              SSHDevice, SSHAction, _assert, _device_key, _ssh,
                              create_hardware_router, run_hardware)


def payload(kind="http", base_url="http://127.0.0.1:1"):
    contract = [{"kind": "json_equals", "path": ["relay"], "expected": "on"}]
    if kind == "serial":
        contract = [{"kind": "text_equals", "expected": "PING\n"}]
        device = {"kind": "serial", "port": "loop://"}
        action = {"kind": "serial", "write_text": "PING\n", "read_until": "\n"}
    else:
        device = {"kind": "http", "base_url": base_url}
        action = {"kind": "http", "path": "/status", "method": "GET"}
    action.update(id="read-relay", function_id="relay", description="Read the user-defined relay state",
                  timeout_s=3, assertions=contract)
    return {"device": device, "requirements": [{"id": "relay", "description": "Read relay state",
                                                 "expected": "Relay reports on", "category": "communication" if kind == "serial" else "functional"}],
            "allowed_actions": [action], "plan": {"steps": [{"id": "s1", "function_id": "relay",
                                                               "action_id": "read-relay", "assertions": copy.deepcopy(contract)}]}}


@contextmanager
def local_device():
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            seen.append(self.path)
            if self.path == "/redirect":
                self.send_response(302); self.send_header("Location", "/must-not-follow"); self.end_headers(); return
            if self.path == "/slow":
                time.sleep(3)
            if self.path == "/large":
                data = b"x" * 70000
            elif self.path == "/echo-auth":
                authorization = self.headers.get("Authorization")
                data = json.dumps({"relay": "on", "echo": authorization,
                                   authorization.split(" ", 1)[1]: "fixture"}).encode()
            elif self.path == "/fail":
                data = b'{"relay":"off"}'
            else:
                data = b'{"relay":"on"}'
            try:
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield f"http://127.0.0.1:{server.server_port}", seen
    finally: server.shutdown(); server.server_close(); thread.join(timeout=2)


@pytest.mark.parametrize("patch", [
    {"device": {"kind": "http", "base_url": "http://user:secret@127.0.0.1"}},
    {"device": {"kind": "http", "base_url": "http://example.com"}},
    {"device": {"kind": "http", "base_url": "http://0.0.0.0:8080"}},
    {"device": {"kind": "http", "base_url": "http://224.0.0.1:8080"}},
    {"device": {"kind": "serial", "port": "socket://127.0.0.1:1"}},
    {"device": {"kind": "serial", "port": "C:/secret.txt"}},
])
def test_rejects_undeclared_or_unsafe_endpoints(patch):
    value = payload(); value.update(patch)
    with pytest.raises(ValidationError): HardwareRunRequest.model_validate(value)


@pytest.mark.parametrize("path", ["//other-host/a", "https://example.com/a", "/x\r\nHost:y", "/a\\b"])
def test_http_actions_cannot_change_origin(path):
    value = payload(); value["allowed_actions"][0]["path"] = path
    with pytest.raises(ValidationError): HardwareRunRequest.model_validate(value)


def test_model_cannot_relax_or_invent_the_user_assertion_contract():
    value = payload(); value["plan"]["steps"][0]["assertions"][0]["expected"] = "off"
    with pytest.raises(ValidationError, match="preserve"): HardwareRunRequest.model_validate(value)
    value = payload(); value["plan"]["steps"][0]["action_id"] = "run-anything"
    with pytest.raises(ValidationError, match="declared"): HardwareRunRequest.model_validate(value)
    value = payload()
    value["allowed_actions"][0]["assertions"][0]["expected"] = {"v": True}
    value["plan"]["steps"][0]["assertions"][0]["expected"] = {"v": 1}
    with pytest.raises(ValidationError, match="preserve"): HardwareRunRequest.model_validate(value)


def test_functional_requirement_cannot_pass_with_connection_only():
    value = payload(); contract = [{"kind": "status_code", "expected": 200}]
    value["allowed_actions"][0]["assertions"] = contract
    value["plan"]["steps"][0]["assertions"] = contract
    with pytest.raises(ValidationError, match="behavior contract"): HardwareRunRequest.model_validate(value)
    value["requirements"][0]["category"] = "communication"
    assert HardwareRunRequest.model_validate(value)


def test_native_serial_echo_is_communication_not_firmware_function():
    value = payload("serial")
    value["device"]["port"] = "COM3"
    value["requirements"][0]["category"] = "functional"
    with pytest.raises(ValidationError, match="behavior contract"):
        HardwareRunRequest.model_validate(value)
    value["allowed_actions"][0]["write_text"] = "READ_STATE\n"
    HardwareRunRequest.model_validate(value)


def test_requires_every_function_and_unique_ids():
    value = payload(); value["requirements"].append({"id": "uncovered", "description": "Other", "expected": "Works"})
    with pytest.raises(ValidationError, match="Uncovered"): HardwareRunRequest.model_validate(value)
    value = payload(); value["plan"]["steps"] *= 2
    with pytest.raises(ValidationError, match="unique"): HardwareRunRequest.model_validate(value)


def test_invalid_json_cannot_satisfy_expected_null():
    assertion = Assertion(kind="json_equals", expected=None)
    assert not _assert(assertion, {"json": None, "json_valid": False})["passed"]
    assert _assert(assertion, {"json": None, "json_valid": True})["passed"]
    assert not _assert(Assertion(kind="json_equals", expected=True), {"json": 1, "json_valid": True})["passed"]
    assert not _assert(Assertion(kind="json_equals", expected={"v": [True]}),
                       {"json": {"v": [1]}, "json_valid": True})["passed"]


@pytest.mark.parametrize("expected", [float("nan"), float("inf"), {"v": float("nan")}])
def test_nonfinite_json_contracts_rejected(expected):
    with pytest.raises(ValidationError): Assertion(kind="json_equals", expected=expected)


def test_nonstandard_device_json_is_not_accepted_as_json():
    assert not hardware._response(b'{"v":NaN}')["json_valid"]
    assert not hardware._response(b'{"v":1e999}')["json_valid"]
    assert not hardware._response(b'{"v":"bad","v":"ok"}')["json_valid"]


def test_report_write_failure_releases_device_lock(tmp_path, monkeypatch):
    request = HardwareRunRequest.model_validate(payload())
    monkeypatch.setattr(hardware, "_execute", lambda *args: {"communication_passed": True, "json": {"relay": "on"}, "json_valid": True})
    original = Path.write_text
    def fail_report(path, *args, **kwargs):
        if path.name == "report.json": raise OSError("Simulated disk failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", fail_report)
    with pytest.raises(OSError, match="disk failure"):
        run_hardware(Settings(root=tmp_path), request)
    lock = hardware._LOCKS[_device_key(request.device)]
    assert lock.acquire(blocking=False)
    lock.release()


def test_router_validation_does_not_reflect_credentials(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI(); app.include_router(create_hardware_router(Settings(root=tmp_path)))
    client = TestClient(app)
    value = payload(); value["device"]["headers"] = {"Authorization": "Bearer NEVER_ECHO_THIS"}
    value["plan"]["steps"][0]["action_id"] = "undeclared"
    response = client.post("/api/hardware/validate-plan", json=value)
    assert response.status_code == 422
    assert "NEVER_ECHO_THIS" not in response.text and "Authorization" not in response.text
    assert all("input" not in error for error in response.json()["detail"])


def test_real_local_http_success_and_failure_receipts(tmp_path):
    settings = Settings(root=tmp_path)
    with local_device() as (url, _):
        value = payload(base_url=url)
        report = run_hardware(settings, HardwareRunRequest.model_validate(value))
        assert report["success"] and report["validation"]["passed"] == 1
        assert report["is_simulation"] and not report["functional_verified"] and not report["model_used"]
        assert report["steps"][0]["actual"]["json"] == {"relay": "on"}
        assert json.loads((tmp_path / report["report_path"]).read_text(encoding="utf-8"))["run_id"] == report["run_id"]
        value["allowed_actions"][0]["path"] = "/fail"
        failed = run_hardware(settings, HardwareRunRequest.model_validate(value))
        assert not failed["success"]
        assert failed["steps"][0]["assertions"][0]["actual"] == "off"
        assert Path(tmp_path / failed["report_path"]).is_file()


def test_authorization_values_not_exposed_in_report(tmp_path):
    with local_device() as (url, _):
        value = payload(base_url=url)
        value["device"]["headers"] = {"Authorization": "Bearer TEST_SECRET_123"}
        value["allowed_actions"][0]["path"] = "/echo-auth"
        report = run_hardware(Settings(root=tmp_path), HardwareRunRequest.model_validate(value))
        assert report["success"]
        serialized = json.dumps(report)
        assert "TEST_SECRET_123" not in serialized and "Authorization" not in serialized
        assert "[REDACTED]" in serialized


def test_redirect_not_followed_and_large_response_fails(tmp_path):
    with local_device() as (url, seen):
        value = payload(base_url=url)
        value["allowed_actions"][0]["path"] = "/redirect"
        report = run_hardware(Settings(root=tmp_path), HardwareRunRequest.model_validate(value))
        assert not report["success"]
        assert report["steps"][0]["actual"]["status_code"] == 302
        assert seen == ["/redirect"]
        value["allowed_actions"][0]["path"] = "/large"
        report = run_hardware(Settings(root=tmp_path), HardwareRunRequest.model_validate(value))
        assert not report["success"] and "byte budget" in report["steps"][0]["actual"]["error"]


def test_serial_loopback_runs_real_pyserial_but_never_verifies_firmware(tmp_path):
    pytest.importorskip("serial")
    report = run_hardware(Settings(root=tmp_path), HardwareRunRequest.model_validate(payload("serial")))
    assert report["success"] and report["is_simulation"]
    assert not report["functional_verified"] and not report["model_used"]
    actual = report["steps"][0]["actual"]
    assert actual["text"] == "PING\n" and actual["dtr"] is False and actual["rts"] is False


def test_binary_serial_contract(tmp_path):
    pytest.importorskip("serial")
    value = payload("serial"); action = value["allowed_actions"][0]
    action.pop("write_text"); action.pop("read_until")
    action.update(write_hex="00 ff 10", read_until_hex="10")
    contract = [{"kind": "hex_equals", "expected": "00ff10"}]
    action["assertions"] = contract; value["plan"]["steps"][0]["assertions"] = contract
    report = run_hardware(Settings(root=tmp_path), HardwareRunRequest.model_validate(value))
    assert report["success"] and report["steps"][0]["actual"]["hex"] == "00ff10"


def test_action_deadline_is_enforced_outside_adapter_process(tmp_path):
    with local_device() as (url, _):
        value = payload(base_url=url); value["allowed_actions"][0].update(path="/slow", timeout_s=1)
        started = time.monotonic()
        report = run_hardware(Settings(root=tmp_path), HardwareRunRequest.model_validate(value))
        assert time.monotonic() - started < 5
        assert not report["success"] and report["steps"][0]["actual"]["timeout"]


def test_device_lock_covers_same_ip_across_http_and_ssh():
    http = HTTPDevice(kind="http", base_url="http://192.168.1.2:8080")
    ssh = SSHDevice(kind="ssh", host="192.168.1.2", username="user", private_key_path="key", known_hosts_path="known")
    assert _device_key(http) == _device_key(ssh)


def test_ssh_unknown_hosts_rejected_and_client_closed(tmp_path, monkeypatch):
    key, known = tmp_path / "key", tmp_path / "known"
    key.write_text("not a real key"); known.write_text("")
    calls = {}
    class FakeClient:
        def load_host_keys(self, path): calls["known"] = path
        def set_missing_host_key_policy(self, value): calls["policy"] = value
        def connect(self, host, **options):
            calls["connect"] = options
            raise ValueError("Unknown host refused")
        def close(self): calls["closed"] = True
    class RejectPolicy: pass
    monkeypatch.setitem(sys.modules, "paramiko", SimpleNamespace(SSHClient=FakeClient, RejectPolicy=RejectPolicy))
    device = SSHDevice(kind="ssh", host="192.168.1.2", username="user", private_key_path=str(key), known_hosts_path=str(known))
    action = SSHAction(kind="ssh", id="status", function_id="status", description="Read status", command="status")
    with pytest.raises(ValueError, match="Unknown"): _ssh(device, action)
    assert isinstance(calls["policy"], RejectPolicy)
    assert calls["connect"]["allow_agent"] is False and calls["connect"]["look_for_keys"] is False
    assert calls["closed"] is True


@contextmanager
def local_ssh_device(tmp_path):
    """Real ephemeral SSH server, explicitly a local simulator, not an MPU."""
    paramiko = pytest.importorskip("paramiko")
    host_key, user_key = paramiko.RSAKey.generate(2048), paramiko.RSAKey.generate(2048)
    private = tmp_path / "ephemeral-test-key"
    user_key.write_private_key_file(str(private))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0)); listener.listen(4); listener.settimeout(.2)
    port = listener.getsockname()[1]
    known, unknown = tmp_path / "known-hosts", tmp_path / "empty-hosts"
    keys = paramiko.HostKeys(); keys.add(f"[127.0.0.1]:{port}", host_key.get_name(), host_key); keys.save(str(known))
    unknown.write_text("")
    stopped, commands, active = threading.Event(), [], []
    class Server(paramiko.ServerInterface):
        def get_allowed_auths(self, username): return "publickey"
        def check_auth_publickey(self, username, key):
            return paramiko.AUTH_SUCCESSFUL if username == "tester" and key == user_key else paramiko.AUTH_FAILED
        def check_channel_request(self, kind, channel_id):
            return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
        def check_channel_exec_request(self, channel, command):
            commands.append(command.decode())
            def respond():
                time.sleep(.03)
                try:
                    channel.sendall(b'{"relay":"on"}\n')
                    channel.send_exit_status(0); channel.shutdown_write()
                except (OSError, EOFError): pass
            threading.Thread(target=respond, daemon=True).start()
            return True
    def serve():
        while not stopped.is_set():
            try: connection, _ = listener.accept()
            except socket.timeout: continue
            except OSError: break
            transport = paramiko.Transport(connection); active.append(transport)
            try:
                transport.add_server_key(host_key); transport.start_server(server=Server())
                while transport.is_active() and not stopped.wait(.05): pass
            except (OSError, EOFError, paramiko.SSHException): pass
            finally: transport.close()
    worker = threading.Thread(target=serve, daemon=True); worker.start()
    try: yield port, private, known, unknown, commands
    finally:
        stopped.set(); listener.close()
        for transport in active: transport.close()
        worker.join(timeout=2)
        private.unlink(missing_ok=True)


def test_real_local_ssh_key_auth_and_known_host_rejection(tmp_path):
    with local_ssh_device(tmp_path) as (port, key, known, unknown, commands):
        value = payload()
        value["device"] = {"kind": "ssh", "host": "127.0.0.1", "port": port, "username": "tester",
                           "private_key_path": str(key), "known_hosts_path": str(known)}
        action = value["allowed_actions"][0]
        action.pop("path"); action.pop("method"); action.update(kind="ssh", command="status", timeout_s=5)
        action["assertions"].append({"kind": "exit_code", "expected": 0})
        value["plan"]["steps"][0]["assertions"] = copy.deepcopy(action["assertions"])
        report = run_hardware(Settings(root=tmp_path), HardwareRunRequest.model_validate(value))
        assert report["success"] and report["is_simulation"] and not report["functional_verified"]
        assert report["steps"][0]["actual"]["host_key_policy"] == "RejectPolicy"
        assert commands == ["status"]
        value["device"]["known_hosts_path"] = str(unknown)
        rejected = run_hardware(Settings(root=tmp_path), HardwareRunRequest.model_validate(value))
        assert not rejected["success"] and "known_hosts" in rejected["steps"][0]["actual"]["error"]
        assert commands == ["status"], "Unknown host must be rejected before the fixed command runs"
        assert str(key) not in json.dumps(report)
