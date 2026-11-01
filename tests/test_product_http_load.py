"""Real temporary sockets for bounded client load, never the user's 8765 service."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time

import pytest

from testfix.product_campaign import AttemptInput, freeze_json
from testfix.product_http_load import LocalHTTPLoadAdapter
from testfix.product_http_load_worker import execute_load, validate_load
from testfix import product_http_load_worker as load_worker


@pytest.fixture
def service():
    observed = {"active": 0, "peak": 0, "paths": []}
    lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            with lock:
                observed["active"] += 1
                observed["peak"] = max(observed["peak"], observed["active"])
                observed["paths"].append(self.path)
            try:
                if self.path in {"/overlap", "/latency"}:
                    time.sleep(.1)
                elif self.path == "/slow":
                    time.sleep(1.0)
                elif self.path == "/disconnect":
                    self.connection.close()
                    return
                self.send_response(503 if self.path == "/unavailable" else 302 if self.path == "/redirect" else 200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Location", "http://192.0.2.1/must-not-follow")
                body = b"x" * 140_000 if self.path == "/large" else b'{"ok":true}'
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            finally:
                with lock:
                    observed["active"] -= 1

        def do_POST(self):
            with lock:
                observed["paths"].append("UNEXPECTED POST")
            self.send_response(500)
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", observed
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def load(path="/", concurrency=2, requests_per_worker=2, expected_status=200, latency_budget_s=1.0):
    return {"request": {"method": "GET", "path": path}, "concurrency": concurrency,
            "requests_per_worker": requests_per_worker, "expected_status": expected_status,
            "latency_budget_s": latency_budget_s}


def attempt(value, seconds=4.0):
    class PoisonExpected:
        def __getattribute__(self, name):
            raise AssertionError("The adapter must not inspect its oracle")
    return AttemptInput(1, "load", "row", "concurrency", True, freeze_json(value),
                        PoisonExpected(), seconds, time.monotonic() + seconds)


def test_real_four_worker_overlap_counts_every_request_without_using_oracle(service):
    base, observed = service
    result = LocalHTTPLoadAdapter(base)(attempt(load("/overlap", 4, 2)))
    assert result.actual == {
        "requests_planned": 8, "requests_completed": 8, "error_count": 0,
        "requests_not_executed": 0, "status_counts": {"200": 8},
        "expected_status_met": True, "latency_budget_met": True, "workers_overlapped": True,
    }
    assert result.metadata["max_inflight"] == 4
    assert observed["peak"] == 4
    assert len(result.metadata["requests"]) == len(observed["paths"]) == 8
    assert result.metadata["request_latency_s"]["p95"] >= .09
    assert "client" in result.metadata["inflight_scope"]
    assert "soak" in result.metadata["scope_limit"]


def test_unexpected_http_status_is_completed_and_never_hidden_as_success(service):
    base, _ = service
    result = execute_load(base, load("/unavailable", 2, 2), 3.0)
    assert result["actual"]["requests_completed"] == 4
    assert result["actual"]["status_counts"] == {"503": 4}
    assert result["actual"]["error_count"] == 0
    assert result["actual"]["expected_status_met"] is False


def test_network_latency_above_frozen_budget_is_reported_false(service):
    base, _ = service
    result = LocalHTTPLoadAdapter(base)(attempt(load("/latency", 2, 1, latency_budget_s=.01)))
    assert result.actual["requests_completed"] == 2
    assert result.actual["latency_budget_met"] is False
    assert all(item["latency_s"] > .01 for item in result.metadata["requests"])


def test_latency_gate_does_not_round_a_small_overrun_into_a_pass(monkeypatch):
    clock = {"value": 0.0}
    monkeypatch.setattr(load_worker.time, "monotonic", lambda: clock["value"])
    def response(*_):
        clock["value"] = 1.0000004
        return {"actual": {"status_code": 200}, "metadata": {"response_bytes": 11}}
    monkeypatch.setattr(load_worker.HTTP, "execute", response)
    result = execute_load("http://127.0.0.1:1", load(concurrency=1, requests_per_worker=1), 3.0)
    assert result["actual"]["latency_budget_met"] is False
    assert result["metadata"]["requests"][0]["latency_s"] > 1.0


def test_transport_errors_are_individually_counted_without_automatic_retry(service):
    base, observed = service
    result = LocalHTTPLoadAdapter(base)(attempt(load("/disconnect", 2, 2)))
    assert result.actual["requests_planned"] == result.actual["error_count"] == 4
    assert result.actual["requests_completed"] == 0
    assert result.actual["status_counts"] == {}
    assert result.actual["expected_status_met"] is False
    assert result.actual["latency_budget_met"] is False
    assert len(observed["paths"]) == 4
    assert all(item["status"] == "error" for item in result.metadata["requests"])


def test_total_worker_timeout_is_enforced_before_slow_requests_finish(service):
    base, _ = service
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        LocalHTTPLoadAdapter(base)(attempt(load("/slow", 2, 3), seconds=.2))
    assert time.monotonic() - started < 3.0  # Includes Windows process-tree termination.


def test_reused_response_cap_and_redirect_rules_are_preserved(service):
    base, observed = service
    large = execute_load(base, load("/large", 1, 1), 3.0)
    redirect = execute_load(base, load("/redirect", 1, 1, expected_status=302), 3.0)
    assert large["actual"]["error_count"] == 1
    assert large["metadata"]["requests"][0]["error_type"] == "ValueError"
    assert redirect["actual"]["expected_status_met"] is True
    assert observed["paths"] == ["/large", "/redirect"]


def test_get_only_boundary_rejects_health_post_before_any_socket(service):
    base, observed = service
    request = load("/api/health")
    request["request"]["method"] = "POST"
    with pytest.raises(ValueError, match="GET only"):
        LocalHTTPLoadAdapter(base)(attempt(request))
    assert observed["paths"] == []


@pytest.mark.parametrize("base,value", [
    ("http://192.0.2.1:80", load()),
    ("http://example.com:80", load()),
    ("http://127.0.0.1:80", load(concurrency=True)),
    ("http://127.0.0.1:80", load(concurrency=9)),
    ("http://127.0.0.1:80", load(latency_budget_s=float("nan"))),
    ("http://127.0.0.1:80", {**load(), "command": "arbitrary"}),
    ("http://127.0.0.1:80", {**load(), "request": {"path": "//other-host"}}),
])
def test_unsafe_or_unbounded_inputs_are_rejected(base, value):
    with pytest.raises(ValueError):
        validate_load(base, value)
