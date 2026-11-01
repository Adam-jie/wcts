"""Deadline-supervised local HTTP adapter for frozen product campaigns."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import time

from .package_tools import _run_bounded
from .product_campaign import AttemptInput, Observation, thaw_json
from .product_http_worker import validate_request


def source_digest(root: Path, relative_files: list[str]) -> str:
    """Bind explicitly selected disk files; not a claim about the running binary."""
    if not relative_files or len(relative_files) != len(set(relative_files)):
        raise ValueError("Provide unique source paths")
    root = root.resolve()
    digest = hashlib.sha256()
    for name in sorted(relative_files):
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Source probe path escaped the repository or is missing")
        # Length framing prevents ambiguities between paths and file bytes.
        encoded = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        for block in (encoded, content):
            digest.update(len(block).to_bytes(8, "big"))
            digest.update(block)
    return digest.hexdigest()


class LocalHTTPAdapter:
    def __init__(self, base_url: str):
        validate_request(base_url, {"path": "/api/health"})
        self.base_url = base_url

    def __call__(self, attempt: AttemptInput) -> Observation:
        request = thaw_json(attempt.input_data)
        validate_request(self.base_url, request)
        remaining = min(attempt.timeout_s, attempt.deadline_monotonic - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("HTTP attempt budget exhausted")
        worker = Path(__file__).with_name("product_http_worker.py").resolve()
        payload = json.dumps({"base_url": self.base_url, "request": request, "timeout_s": remaining}, allow_nan=False)
        result = _run_bounded([sys.executable, "-I", "-B", "-X", "utf8", str(worker)],
                              cwd=worker.parent, timeout_s=remaining, stdin=payload)
        if result["timeout"]:
            raise TimeoutError("HTTP worker deadline exceeded")
        if result["exit_code"] != 0 or result["output_limit_exceeded"]:
            raise RuntimeError("HTTP worker failed")
        output = json.loads(result["stdout"])
        if output.get("ok") is not True:
            if output.get("error") == "timeout":
                raise TimeoutError("HTTP request timed out")
            raise RuntimeError("HTTP request failed: " + str(output.get("error", "unknown")))
        return Observation(output["actual"], output["metadata"])
