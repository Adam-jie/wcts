"""Read-only evaluation payloads, scrubbed child env, bounded execution."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from .policy import validate_acceptance_code, validate_candidate


def validate_result(result: dict, expected_total: int) -> dict:
    if not isinstance(result, dict) or type(result.get("total")) is not int or result["total"] != expected_total:
        raise ValueError("Invalid worker total")
    if type(result.get("passed")) is not int or not 0 <= result["passed"] <= expected_total:
        raise ValueError("Invalid worker passed count")
    indices = result.get("passed_indices")
    if (not isinstance(indices, list) or any(type(index) is not int or not 0 <= index < expected_total for index in indices)
            or len(set(indices)) != len(indices) or len(indices) != result["passed"]):
        raise ValueError("Invalid worker passed indices")
    if not isinstance(result.get("failures"), list) or any(not isinstance(item, dict) for item in result["failures"]):
        raise ValueError("Invalid worker failure evidence")
    failed_indices = [item["index"] for item in result["failures"] if "index" in item]
    if (any(type(index) is not int or not 0 <= index < expected_total for index in failed_indices)
            or len(set(failed_indices)) != len(failed_indices) or set(indices) & set(failed_indices)
            or result["passed"] == expected_total and result["failures"]):
        raise ValueError("Contradictory worker failure evidence")
    return result


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=4).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class Runner:
    def __init__(self, mode="process", docker_image="python:3.13-slim", timeout=8.0):
        if mode not in {"process", "docker"}:
            raise ValueError("Unsupported runner")
        self.mode, self.docker_image, self.timeout = mode, docker_image, timeout
        if mode == "docker" and not docker_available():
            raise ValueError("Docker daemon is unavailable; use the restricted development runner or start Docker")

    def evaluate(self, code, initial_code, entry_point, *, cases=None, acceptance=None):
        validate_candidate(code, initial_code, entry_point)
        if acceptance is not None:
            validate_acceptance_code(acceptance)
        total = 1 if acceptance is not None else len(cases or [])
        if not total:
            return {"passed": 0, "total": 0, "passed_indices": [], "failures": [], "duration_s": 0, "runner": self.mode}
        started = time.monotonic()
        container = f"testfix-{uuid.uuid4().hex}"
        with tempfile.TemporaryDirectory(prefix="testfix-eval-") as temporary:
            directory = Path(temporary)
            worker = directory / "worker.py"
            shutil.copyfile(Path(__file__).with_name("worker.py"), worker)
            shutil.copyfile(Path(__file__).with_name("resource_limits.py"), directory / "resource_limits.py")
            payload = directory / "payload.json"
            payload.write_text(json.dumps({"code": code, "entry_point": entry_point, "cases": cases or [], "acceptance": acceptance}), encoding="utf-8")
            env = {key: value for key, value in os.environ.items() if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "COMSPEC"}}
            if self.mode == "docker":
                command = ["docker", "run", "--rm", "--name", container, "--network=none", "--read-only", "--cap-drop=ALL",
                           "--security-opt=no-new-privileges", "--pids-limit=32", "--memory=512m", "--cpus=1", "--user=65534:65534",
                           "--mount", f"type=bind,source={directory},target=/work,readonly", self.docker_image,
                           "python", "-I", "-S", "/work/worker.py", "/work/payload.json"]
            else:
                command = [sys.executable, "-I", "-S", str(worker), str(payload)]
            process = subprocess.Popen(command, cwd=directory, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            try:
                stdout, stderr = process.communicate(timeout=self.timeout)
                if process.returncode:
                    result = {"passed": 0, "total": total, "failures": [{"error": "Worker failed", "detail": stderr.decode("utf-8", errors="replace")[-1500:]}]}
                else:
                    result = validate_result(json.loads(stdout.decode("utf-8")), total)
            except subprocess.TimeoutExpired:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=5)
                else:
                    process.kill()
                process.communicate(timeout=5)
                result = {"passed": 0, "total": total, "failures": [{"error": "Execution timeout"}]}
            finally:
                if self.mode == "docker":
                    subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=5)
        return {**result, "duration_s": round(time.monotonic() - started, 3), "runner": self.mode}

