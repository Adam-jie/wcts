"""Process-supervised loopback GET-only light-load campaign adapter."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

from .package_tools import _run_bounded
from .product_campaign import AttemptInput, Observation, thaw_json
from .product_http_load_worker import validate_load
from .product_http_worker import validate_request


class LocalHTTPLoadAdapter:
    def __init__(self, base_url: str):
        validate_request(base_url, {"path": "/", "method": "GET"})
        self.base_url = base_url

    def __call__(self, attempt: AttemptInput) -> Observation:
        # Expected outcomes belong exclusively to the independent runner.
        request = thaw_json(attempt.input_data)
        validate_load(self.base_url, request)
        remaining = min(attempt.timeout_s, attempt.deadline_monotonic - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("HTTP load attempt budget exhausted")
        worker = Path(__file__).with_name("product_http_load_worker.py").resolve()
        payload = json.dumps({"base_url": self.base_url, "request": request,
                              "timeout_s": remaining}, ensure_ascii=True, allow_nan=False)
        result = _run_bounded([sys.executable, "-I", "-B", "-X", "utf8", str(worker)],
                              cwd=worker.parent, timeout_s=remaining, stdin=payload)
        if result["timeout"]:
            raise TimeoutError("HTTP load worker deadline exceeded")
        if result["exit_code"] != 0 or result["output_limit_exceeded"]:
            raise RuntimeError("HTTP load worker failed")
        output = json.loads(result["stdout"])
        if output.get("ok") is not True:
            if output.get("error") == "timeout":
                raise TimeoutError("HTTP load request timed out")
            raise RuntimeError("HTTP load request failed: " + str(output.get("error", "unknown")))
        return Observation(output["actual"], output["metadata"])
