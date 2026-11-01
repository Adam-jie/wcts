"""Application-owned, one-request HTTP worker. No redirects, proxies or commands."""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import math
import sys
import time
from urllib.parse import urlencode, urlsplit

MAX_BODY = 131_072


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON fields")
        result[key] = value
    return result


def _text(value, limit=2048):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Invalid bounded HTTP text")
    return value


def validate_request(base_url, request):
    """This adapter is deliberately limited to read-only local service probes."""
    base = urlsplit(_text(base_url))
    if (base.scheme != "http" or base.username or base.password or base.path not in ("", "/")
            or base.query or base.fragment or not base.hostname or not base.port):
        raise ValueError("Use an explicit loopback HTTP address and port")
    if not ipaddress.ip_address(base.hostname).is_loopback:
        raise ValueError("Only loopback IP addresses are supported")
    if not isinstance(request, dict) or set(request) - {"method", "path", "query", "headers", "select", "select_headers", "body_contains"}:
        raise ValueError("Unknown HTTP probe field")
    method = request.get("method", "GET")
    path = _text(request.get("path"))
    parts = urlsplit(path)
    if (not path.startswith("/") or path.startswith("//") or parts.scheme or parts.netloc
            or parts.query or parts.fragment or "\\" in path or "%" in path):
        raise ValueError("Use a relative URL path without encoded routing")
    if method not in ("GET", "HEAD") and not (method == "POST" and path == "/api/health"):
        raise ValueError("This read-only adapter forbids the requested method")
    query = request.get("query", [])
    if not isinstance(query, list) or len(query) > 20:
        raise ValueError("Invalid query pairs")
    for pair in query:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("Invalid query pair")
        for value in pair:
            _text(value, 256)
    headers = request.get("headers", {})
    if not isinstance(headers, dict) or len(headers) > 2:
        raise ValueError("Invalid request headers")
    for name, value in headers.items():
        if name.lower() not in {"accept", "origin"}:
            raise ValueError("Only Accept and Origin headers are supported")
        _text(value, 1024)
    selectors = request.get("select", [])
    if not isinstance(selectors, list) or len(selectors) > 24:
        raise ValueError("Too many JSON selections")
    for selector in selectors:
        if not isinstance(selector, dict) or set(selector) != {"path", "op"} or selector["op"] not in {"value", "type", "length"}:
            raise ValueError("Invalid JSON selection")
        route = selector["path"]
        if not isinstance(route, list) or len(route) > 8:
            raise ValueError("Invalid JSON path")
        for key in route:
            if type(key) is int and 0 <= key <= 10000:
                continue
            _text(key, 128)
    selected_headers = request.get("select_headers", [])
    if not isinstance(selected_headers, list) or len(selected_headers) > 16:
        raise ValueError("Too many selected headers")
    for name in selected_headers:
        _text(name, 128)
        if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in name):
            raise ValueError("Use lowercase header names")
    if "body_contains" in request:
        _text(request["body_contains"], 256)
    return base


def _select(document, selector):
    current = document
    for key in selector["path"]:
        if isinstance(current, dict) and isinstance(key, str) and key in current:
            current = current[key]
        elif isinstance(current, list) and type(key) is int and key < len(current):
            current = current[key]
        else:
            return {"present": False}
    op = selector["op"]
    if op == "type":
        value = {str: "string", bool: "boolean", int: "number", float: "number", dict: "object", list: "array", type(None): "null"}[type(current)]
    elif op == "length":
        if type(current) not in {str, list, dict}:
            return {"present": True, "error": "no_length"}
        value = len(current)
    else:
        value = current
    return {"present": True, "value": value}


def execute(base_url, request, timeout_s):
    base = validate_request(base_url, request)
    if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not 0 < timeout_s <= 120:
        raise ValueError("Invalid HTTP deadline")
    deadline = time.monotonic() + timeout_s
    path = request["path"]
    if request.get("query"):
        # JSON stores pairs as lists; urllib requires tuple pairs.
        path += "?" + urlencode([tuple(pair) for pair in request["query"]])
    connection = http.client.HTTPConnection(base.hostname, base.port, timeout=timeout_s)
    started = time.monotonic()
    try:
        connection.request(request.get("method", "GET"), path, headers=request.get("headers", {}))
        response = connection.getresponse()
        chunks, size = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("HTTP deadline")
            if connection.sock:
                connection.sock.settimeout(remaining)
            chunk = response.read(min(16384, MAX_BODY + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_BODY:
                raise ValueError("HTTP response exceeds body limit")
        body = b"".join(chunks)
        try:
            document = json.loads(body, object_pairs_hook=_unique,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
            json_valid = True
        except (ValueError, UnicodeDecodeError):
            document, json_valid = None, False
        actual = {"status_code": response.status, "json_valid": json_valid}
        if "select" in request:
            actual["json_select"] = [_select(document, selector) if json_valid else {"present": False} for selector in request["select"]]
        if "select_headers" in request:
            actual["headers"] = {name: response.getheader(name) for name in request["select_headers"]}
        if "body_contains" in request:
            actual["body_contains"] = request["body_contains"] in body.decode("utf-8", errors="replace")
        return {"actual": actual, "metadata": {"http_status": response.status, "response_bytes": len(body),
                "response_sha256": hashlib.sha256(body).hexdigest(), "request_duration_s": round(time.monotonic() - started, 6)}}
    finally:
        connection.close()


def main():
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError("HTTP worker input too large")
        payload = json.loads(raw)
        result = execute(payload["base_url"], payload["request"], payload["timeout_s"])
        print(json.dumps({"ok": True, **result}, ensure_ascii=True, allow_nan=False))
    except TimeoutError:
        print(json.dumps({"ok": False, "error": "timeout"}))
    except Exception as exc:
        # Response bodies and exception messages may contain secrets. Store only type.
        print(json.dumps({"ok": False, "error": type(exc).__name__}))


if __name__ == "__main__":
    main()
