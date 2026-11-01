"""Fixed local GET-only light-load worker; never imports candidate/user code."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import math
from pathlib import Path
import sys
import threading
import time


def _http_worker():
    path = Path(__file__).with_name("product_http_worker.py").resolve()
    spec = importlib.util.spec_from_file_location("_testfix_owned_product_http_worker", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Application HTTP worker unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HTTP = _http_worker()
_FIELDS = {"request", "concurrency", "requests_per_worker", "expected_status", "latency_budget_s"}


def validate_load(base_url, value):
    if type(value) is not dict or set(value) != _FIELDS:
        raise ValueError("Provide exactly the fixed HTTP load fields")
    for name, low, high in (("concurrency", 1, 8), ("requests_per_worker", 1, 25), ("expected_status", 100, 599)):
        if type(value[name]) is not int or not low <= value[name] <= high:
            raise ValueError("Invalid bounded HTTP load integer")
    latency = value["latency_budget_s"]
    if type(latency) not in (int, float) or not math.isfinite(latency) or not .01 <= latency <= 10:
        raise ValueError("Invalid HTTP latency budget")
    HTTP.validate_request(base_url, value["request"])
    if value["request"].get("method", "GET") != "GET":
        raise ValueError("Load tests support GET only, including the health endpoint")
    encoded = json.dumps(value, ensure_ascii=True, allow_nan=False)
    if len(encoded.encode("utf-8")) > 60_000:
        raise ValueError("HTTP load input exceeds fixed size limit")


def _quantile(values, probability):
    if not values:
        return None
    ordered = sorted(values)
    point = (len(ordered) - 1) * probability
    lower, upper = math.floor(point), math.ceil(point)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (point - lower), 6)


def execute_load(base_url, value, timeout_s):
    validate_load(base_url, value)
    if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not 0 < timeout_s <= 120:
        raise ValueError("Invalid HTTP load deadline")
    concurrency = value["concurrency"]
    per_worker = value["requests_per_worker"]
    planned = concurrency * per_worker
    started = time.monotonic()
    deadline = started + timeout_s
    barrier = threading.Barrier(concurrency)
    lock = threading.Lock()
    active, peak = 0, 0
    receipts = []

    def worker(worker_index):
        nonlocal active, peak
        # Synchronize before counting any in-flight request; waiting at the
        # barrier must not manufacture an overlap observation.
        barrier.wait(timeout=max(.001, min(10.0, deadline - time.monotonic())))
        for request_index in range(1, per_worker + 1):
            remaining = deadline - time.monotonic()
            receipt = {"worker_index": worker_index, "request_index": request_index,
                       "status": "not_executed", "http_status": None,
                       "latency_s": None, "error_type": None}
            if remaining <= 0:
                with lock:
                    receipts.append(receipt)
                continue
            request_started = time.monotonic()
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                # Both the socket and complete one-response read carry a
                # deadline. The parent process owns the harder total deadline.
                response = HTTP.execute(base_url, value["request"], min(10.0, remaining))
                receipt["status"] = "completed"
                receipt["http_status"] = response["actual"]["status_code"]
                receipt["response_bytes"] = response["metadata"]["response_bytes"]
            except TimeoutError:
                receipt["status"], receipt["error_type"] = "timeout", "TimeoutError"
            except Exception as error:
                receipt["status"], receipt["error_type"] = "error", type(error).__name__
            finally:
                # Preserve the measured float for the gate; rounding a duration
                # just above the budget must not turn a failed check green.
                receipt["latency_s"] = time.monotonic() - request_started
                with lock:
                    active -= 1
                    receipts.append(receipt)

    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="testfix-http-load") as pool:
        futures = [pool.submit(worker, index) for index in range(1, concurrency + 1)]
        for future in futures:
            future.result()
    receipts.sort(key=lambda item: (item["worker_index"], item["request_index"]))
    completed = [item for item in receipts if item["status"] == "completed"]
    errors = [item for item in receipts if item["status"] in {"error", "timeout"}]
    skipped = [item for item in receipts if item["status"] == "not_executed"]
    statuses = dict(sorted(Counter(str(item["http_status"]) for item in completed).items()))
    success_latencies = [item["latency_s"] for item in completed]
    all_latencies = [item["latency_s"] for item in receipts if item["latency_s"] is not None]
    complete = len(completed) == planned and not errors and not skipped
    return {
        "actual": {
            "requests_planned": planned, "requests_completed": len(completed),
            "error_count": len(errors), "requests_not_executed": len(skipped),
            "status_counts": statuses,
            "expected_status_met": complete and statuses == {str(value["expected_status"]): planned},
            "latency_budget_met": complete and all(duration <= value["latency_budget_s"] for duration in success_latencies),
            "workers_overlapped": peak >= concurrency,
        },
        "metadata": {
            "concurrency": concurrency, "requests_per_worker": per_worker,
            "max_inflight": peak, "inflight_scope": "client adapter requests, not server execution threads or established sockets",
            "wall_duration_s": round(time.monotonic() - started, 6),
            "request_latency_s": {"p50": _quantile(all_latencies, .5), "p95": _quantile(all_latencies, .95)},
            "latency_budget_s": value["latency_budget_s"],
            "per_request_timeout_cap_s": 10.0,
            "requests": receipts,
            "scope_limit": "Client light-load transport/status/latency evidence only; does not prove business behavior, soak endurance or resource utilization.",
            "interrupted_batch_limit": "If the supervising process terminates this worker, per-request completion is unknown; only a failed/timeout batch receipt remains.",
        },
    }


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON fields")
        result[key] = value
    return result


def main():
    try:
        raw = sys.stdin.buffer.read(65_537)
        if len(raw) > 65_536:
            raise ValueError("HTTP load worker input too large")
        payload = json.loads(raw, object_pairs_hook=_unique,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
        if type(payload) is not dict or set(payload) != {"base_url", "request", "timeout_s"}:
            raise ValueError("Invalid HTTP load worker envelope")
        result = execute_load(payload["base_url"], payload["request"], payload["timeout_s"])
        print(json.dumps({"ok": True, **result}, ensure_ascii=True, allow_nan=False))
    except TimeoutError:
        print(json.dumps({"ok": False, "error": "timeout"}))
    except Exception as error:
        print(json.dumps({"ok": False, "error": type(error).__name__}))


if __name__ == "__main__":
    main()
