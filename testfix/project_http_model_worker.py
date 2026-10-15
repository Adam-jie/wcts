"""One bounded HTTP request, with all request credentials carried over stdin.

Run as a standalone isolated Python script. Never write request data or remote
exception text to disk, stderr, or error responses. The parent owns the hard
wall deadline and kills this process tree when that deadline expires.
"""
from __future__ import annotations

import json
import math
import sys


MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def _failure(code: str, status: int | None = None) -> dict:
    result = {"ok": False, "code": code}
    if status is not None:
        result["status"] = status
    return result


def request_http(url: str, headers: dict, payload: dict, timeout_s: float,
                 response_limit_bytes: int = MAX_RESPONSE_BYTES) -> dict:
    """Inactivity timeout plus streamed size bound; the parent adds hard timeout."""
    import httpx

    if (not isinstance(url, str) or not isinstance(headers, dict) or not isinstance(payload, dict)
            or type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0
            or type(response_limit_bytes) is not int or not 1 <= response_limit_bytes <= MAX_RESPONSE_BYTES):
        return _failure("invalid_request")
    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=False, trust_env=False) as client:
            with client.stream("POST", url, headers={"Accept-Encoding": "identity", **headers}, json=payload) as response:
                if response.status_code >= 300:
                    return _failure("http_error", response.status_code)
                declared_length = response.headers.get("Content-Length")
                if declared_length and declared_length.isdigit() and int(declared_length) > response_limit_bytes:
                    return _failure("response_too_large")
                if response.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
                    # Reject compression rather than allocate an unbounded decoded chunk.
                    return _failure("invalid_json")
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    if len(content) + len(chunk) > response_limit_bytes:
                        return _failure("response_too_large")
                    content.extend(chunk)
                try:
                    body = json.loads(content)
                    if not isinstance(body, dict):
                        return _failure("invalid_json")
                    # JSON can parse exponent overflow as infinity. Never emit it.
                    json.dumps(body, allow_nan=False)
                except (UnicodeError, ValueError, OverflowError):
                    return _failure("invalid_json")
                return {"ok": True, "status": response.status_code, "body": body}
    except httpx.TimeoutException:
        return _failure("timeout")
    except Exception:
        return _failure("transport_error")


def main() -> None:
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            result = _failure("request_too_large")
        else:
            request = json.loads(raw)
            result = request_http(**request) if isinstance(request, dict) else _failure("invalid_request")
    except Exception:
        result = _failure("worker_error")
    # Only this fixed envelope or a bounded JSON success body reaches stdout.
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
