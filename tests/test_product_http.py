"""Real socket checks for the trusted HTTP campaign boundary."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from testfix.product_campaign import AttemptInput
from testfix.product_http import LocalHTTPAdapter, source_digest
from testfix.product_http_worker import execute, validate_request


@pytest.fixture
def service():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/drip":
                try:
                    for byte in b"HTTP/1.1 200 OK\r\nX-Slow: aaaaaaaaaaaaaaaaaaaaaaaa\r\n\r\n{}":
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.03)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            if self.path == "/slow":
                time.sleep(1)
            self.send_response(302 if self.path == "/redirect" else 200)
            self.send_header("Location", "http://192.0.2.1/never-follow")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            body = (json.dumps({"query": parse_qs(urlsplit(self.path).query, keep_blank_values=True)}, ensure_ascii=True).encode()
                    if self.path.startswith("/query?") else b"x" * 140000 if self.path == "/large" else b'{"x":false,"x":true}'
                    if self.path == "/duplicate" else json.dumps({"flag": True, "value": None, "items": [1, 2]}).encode())
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def attempt(request, seconds=3.0):
    return AttemptInput(1, "case", "data", "functional", True, request, {"this": "must not be read"}, seconds, time.monotonic() + seconds)


def test_real_http_projection_preserves_types_and_missing(service):
    request = {"path": "/", "select": [{"path": ["flag"], "op": "value"},
                {"path": ["value"], "op": "value"}, {"path": ["missing"], "op": "value"},
                {"path": ["items"], "op": "length"}], "select_headers": ["x-content-type-options"]}
    observation = LocalHTTPAdapter(service)(attempt(request))
    assert observation.actual == {"status_code": 200, "json_valid": True,
            "json_select": [{"present": True, "value": True}, {"present": True, "value": None},
                            {"present": False}, {"present": True, "value": 2}],
            "headers": {"x-content-type-options": "nosniff"}}
    assert observation.metadata["response_bytes"] > 0


def test_redirect_is_observed_and_never_followed(service):
    result = execute(service, {"path": "/redirect"}, 1)
    assert result["actual"]["status_code"] == 302


def test_json_list_query_pairs_roundtrip_unicode_empty_and_reserved_characters(service):
    request = {"path": "/query", "query": [["text", "中文 空格 & +"], ["empty", ""], ["limit", "0"]],
               "select": [{"path": ["query"], "op": "value"}]}
    actual = LocalHTTPAdapter(service)(attempt(request)).actual
    assert actual["json_select"] == [{"present": True, "value": {"text": ["中文 空格 & +"], "empty": [""], "limit": ["0"]}}]


@pytest.mark.parametrize("base", ["http://example.com:80", "http://192.0.2.1:80", "http://localhost:80",
    "http://user:pass@127.0.0.1:80", "http://127.0.0.1:80/api", "https://127.0.0.1:80"])
def test_rejects_external_dns_credentials_or_nonlocal_base(base):
    with pytest.raises(ValueError):
        LocalHTTPAdapter(base)


@pytest.mark.parametrize("probe", [{"path": "//example.com"}, {"path": "/%61pi/health", "method": "POST"},
    {"path": "/api/project-runs", "method": "POST"}, {"path": "/", "headers": {"Authorization": "secret"}},
    {"path": "/", "headers": {"Origin": "test\r\nInjected: yes"}}, {"path": "/", "select": [{"path": [-1], "op": "value"}]}])
def test_rejects_mutations_routing_and_header_injection(probe):
    with pytest.raises(ValueError):
        validate_request("http://127.0.0.1:8765", probe)


def test_response_limit_is_enforced(service):
    with pytest.raises(ValueError, match="body limit"):
        execute(service, {"path": "/large"}, 2)


@pytest.mark.parametrize("path", ["/slow", "/drip"])
def test_owned_worker_has_total_timeout_before_response_headers(service, path):
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        LocalHTTPAdapter(service)(attempt({"path": path}, 0.2))
    assert time.monotonic() - started < 3  # includes process termination overhead


def test_duplicate_json_fields_are_invalid_instead_of_last_value_wins(service):
    actual = execute(service, {"path": "/duplicate", "select": [{"path": ["x"], "op": "value"}]}, 1)["actual"]
    assert actual["json_valid"] is False
    assert actual["json_select"] == [{"present": False}]


def test_source_probe_detects_modification_and_rejects_escape(tmp_path):
    source = tmp_path / "module.py"
    source.write_text("value = 1", encoding="utf-8")
    before = source_digest(tmp_path, ["module.py"])
    source.write_text("value = 2", encoding="utf-8")
    assert source_digest(tmp_path, ["module.py"]) != before
    with pytest.raises(ValueError):
        source_digest(tmp_path, ["../elsewhere.py"])
