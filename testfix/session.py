"""Replay a previously captured Codex generation session during evaluation.

The bundle is a local evidence artifact.  Its hashes protect the captured
files from accidental or subsequent modification; they do not attest which
remote service or model produced those files.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import time
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from threading import Lock
from typing import Any


BUNDLE_VERSION = "testfix.codex-session.v1"
CAPTURED_MODEL = "gpt-5.6-sol"
ROLES = ("requirements", "tester", "diagnosis", "developer", "reviewer")
_PROTECTED_CONTEXT_KEYS = {"canonical_solution", "acceptance", "acceptance_code", "test", "hidden_test", "hidden_tests"}
_INITIAL_CODE_ROLES = {"requirements", "tester", "diagnosis"}


def _require_iso8601(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be an ISO timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{field} must be an ISO timestamp") from None
    return value


def _require_local_relative_path(value: Any, base: Path, field: str, *, must_exist: bool) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty relative path")
    relative = Path(value)
    # Check the source spelling as well as the resolved destination.  A path
    # containing '..' is rejected even if normalization happens to put it back
    # inside the bundle directory.
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (relative.is_absolute() or relative.drive or posix.is_absolute()
            or windows.is_absolute() or windows.drive
            or ".." in posix.parts or ".." in windows.parts):
        raise ValueError(f"{field} must stay inside the bundle directory")
    try:
        resolved = (base / relative).resolve(strict=must_exist)
        resolved.relative_to(base)
    except (FileNotFoundError, OSError, ValueError):
        raise ValueError(f"{field} must stay inside the bundle directory") from None
    if must_exist and not resolved.is_file():
        raise ValueError(f"{field} must name a captured output file")
    return resolved


def _reject_protected_data(value: Any, seen: set[int] | None = None) -> None:
    if seen is None:
        seen = set()
    if isinstance(value, dict):
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        for key, nested in value.items():
            if isinstance(key, str) and key.casefold() in _PROTECTED_CONTEXT_KEYS:
                raise ValueError(f"Protected data '{key}' is not allowed in session context")
            _reject_protected_data(nested, seen)
    elif isinstance(value, (list, tuple, set, frozenset)):
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        for nested in value:
            _reject_protected_data(nested, seen)


def _fingerprint(specification: str, initial_code: str, entry_point: str) -> str:
    public = {
        "entry_point": entry_point,
        "initial_code": initial_code,
        "specification": specification,
    }
    encoded = json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SessionProvider:
    """Provider backed by saved outputs from one earlier live Codex session."""

    def __init__(self, bundle_path: str | Path):
        self.bundle_path = Path(bundle_path).resolve(strict=True)
        if not self.bundle_path.is_file():
            raise ValueError("Session bundle must be a JSON file")
        self._base = self.bundle_path.parent.resolve()
        try:
            bundle = json.loads(self.bundle_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Session bundle must be valid UTF-8 JSON") from exc
        if not isinstance(bundle, dict):
            raise ValueError("Session bundle must be a JSON object")
        if bundle.get("version") != BUNDLE_VERSION:
            raise ValueError(f"Unsupported session bundle version; expected {BUNDLE_VERSION}")
        if bundle.get("model") != CAPTURED_MODEL:
            raise ValueError(f"Captured session model must be {CAPTURED_MODEL}")
        self.model = CAPTURED_MODEL
        self.generated_at = _require_iso8601(bundle.get("generated_at"), "generated_at")

        task_ids = bundle.get("task_ids")
        if (not isinstance(task_ids, list) or not task_ids
                or any(not isinstance(task_id, str) or not task_id for task_id in task_ids)):
            raise ValueError("task_ids must be a non-empty list of strings")
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("Duplicate task id in session bundle")
        self.task_ids = tuple(task_ids)

        role_payloads = bundle.get("roles")
        if not isinstance(role_payloads, dict) or set(role_payloads) != set(task_ids):
            raise ValueError("Session roles must match task_ids exactly")
        self._roles: dict[str, dict[str, dict]] = {}
        for task_id in task_ids:
            task_roles = role_payloads[task_id]
            if not isinstance(task_roles, dict) or set(task_roles) != set(ROLES):
                raise ValueError(f"Task {task_id!r} must contain all five captured roles exactly once")
            if any(not isinstance(task_roles[role], dict) for role in ROLES):
                raise ValueError(f"Task {task_id!r} role outputs must be JSON objects")
            self._roles[task_id] = copy.deepcopy(task_roles)

        self._public_tasks = self._validate_public_tasks(bundle.get("public_tasks", {}))
        self._observed_public: dict[str, dict[str, str]] = {}
        self._record_for_role, safe_records = self._validate_records(bundle.get("generation_records"))
        self._validate_reviewer_hashes()
        self._safe_records = safe_records
        self.additional_suite_paths, self._safe_additional_suites = self._validate_additional_suites(
            bundle.get("additional_suite_paths", {})
        )
        self.calls: list[dict] = []
        self._used: set[tuple[str, str]] = set()
        self._lock = Lock()

    def _validate_public_tasks(self, value: Any) -> dict[str, dict[str, str]]:
        if not isinstance(value, dict) or set(value) != set(self.task_ids):
            raise ValueError("public_tasks must match all bundled task ids exactly")
        result: dict[str, dict[str, str]] = {}
        required = {"specification", "initial_code", "entry_point", "fingerprint"}
        for task_id, public in value.items():
            if not isinstance(public, dict) or set(public) != required:
                raise ValueError(f"Public task {task_id!r} requires exactly four baseline fields")
            if any(not isinstance(public[field], str) or not public[field] for field in required):
                raise ValueError(f"Public task {task_id!r} baseline fields must be non-empty strings")
            expected = _fingerprint(public["specification"], public["initial_code"], public["entry_point"])
            if public["fingerprint"] != expected:
                raise ValueError(f"Public task fingerprint mismatch for {task_id!r}")
            result[task_id] = copy.deepcopy(public)
        return result

    def _validate_records(self, value: Any) -> tuple[dict[str, dict], list[dict]]:
        if not isinstance(value, list) or not value:
            raise ValueError("generation_records must be a non-empty list")
        by_role: dict[str, dict] = {}
        safe_records: list[dict] = []
        for index, record in enumerate(value):
            prefix = f"generation_records[{index}]"
            if not isinstance(record, dict):
                raise ValueError(f"{prefix} must be an object")
            agent_id = record.get("agent_id")
            if not isinstance(agent_id, str) or not agent_id:
                raise ValueError(f"{prefix}.agent_id must be a non-empty string")
            if record.get("model") != CAPTURED_MODEL:
                raise ValueError(f"{prefix}.model must be {CAPTURED_MODEL}")
            captured_at = _require_iso8601(record.get("generated_at"), f"{prefix}.generated_at")
            roles = record.get("roles")
            if (not isinstance(roles, list) or not roles
                    or any(role not in ROLES for role in roles) or len(set(roles)) != len(roles)):
                raise ValueError(f"{prefix}.roles must contain unique known roles")
            for role in roles:
                if role in by_role:
                    raise ValueError(f"Role {role!r} is attributed to multiple generation records")
                by_role[role] = record
            prompt = _require_local_relative_path(
                record.get("prompt_file"), self._base, f"{prefix}.prompt_file", must_exist=True
            )
            prompt_digest = record.get("prompt_sha256")
            if (not isinstance(prompt_digest, str) or len(prompt_digest) != 64
                    or any(character not in "0123456789abcdefABCDEF" for character in prompt_digest)):
                raise ValueError(f"{prefix}.prompt_sha256 must be a SHA-256 hex digest")
            observed_prompt_digest = hashlib.sha256(prompt.read_bytes()).hexdigest()
            if observed_prompt_digest != prompt_digest.casefold():
                raise ValueError(f"Captured prompt SHA-256 mismatch for {record['prompt_file']!r}")
            try:
                prompt_content = json.loads(prompt.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"{prefix}.prompt_file must contain valid UTF-8 JSON") from exc
            _reject_protected_data(prompt_content)
            output = _require_local_relative_path(record.get("output_file"), self._base, f"{prefix}.output_file", must_exist=True)
            digest = record.get("sha256")
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(character not in "0123456789abcdefABCDEF" for character in digest)):
                raise ValueError(f"{prefix}.sha256 must be a SHA-256 hex digest")
            observed = hashlib.sha256(output.read_bytes()).hexdigest()
            if observed != digest.casefold():
                raise ValueError(f"Captured output SHA-256 mismatch for {record['output_file']!r}")
            self._validate_record_output(output, roles, prefix)
            duration = record.get("generation_duration_s")
            if (duration is not None
                    and (isinstance(duration, bool) or not isinstance(duration, (int, float))
                         or not math.isfinite(duration) or duration < 0)):
                raise ValueError(f"{prefix}.generation_duration_s must be a non-negative number")
            safe = {
                "agent_id": agent_id,
                "model": CAPTURED_MODEL,
                "roles": list(roles),
                "generated_at": captured_at,
                "prompt_file": Path(record["prompt_file"]).as_posix(),
                "prompt_sha256": observed_prompt_digest,
                "sha256": observed,
            }
            if duration is not None:
                safe["generation_duration_s"] = duration
            safe_records.append(safe)
        if set(by_role) != set(ROLES):
            raise ValueError("generation_records must attribute all five roles")
        return by_role, safe_records

    def _validate_record_output(self, output: Path, roles: list[str], prefix: str) -> None:
        try:
            captured = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{prefix}.output_file must contain valid UTF-8 JSON") from exc
        if (not isinstance(captured, dict) or set(captured) not in ({"tasks"}, {"tasks", "model"})
                or not isinstance(captured["tasks"], dict)):
            raise ValueError(f"{prefix}.output_file must contain tasks and an optional model")
        if "model" in captured and captured["model"] != CAPTURED_MODEL:
            raise ValueError(f"{prefix}.output_file model must be {CAPTURED_MODEL}")
        captured_tasks = captured["tasks"]
        if set(captured_tasks) != set(self.task_ids):
            raise ValueError(f"{prefix}.output_file tasks must match all bundled task ids")
        for task_id in self.task_ids:
            task_output = captured_tasks[task_id]
            expected_fields = set(roles)
            if "tester" in roles:
                expected_fields.update({"steps", "coverage"})
            if (not isinstance(task_output, dict)
                    or set(task_output) not in (expected_fields, expected_fields | {"assumptions"})):
                raise ValueError(
                    f"{prefix}.output_file task {task_id!r} must contain exactly its declared roles and metadata"
                )
            if "assumptions" in task_output and "tester" not in roles:
                raise ValueError("Captured assumptions metadata is only valid for tester output")
            for role in roles:
                role_output = task_output[role]
                if not isinstance(role_output, dict):
                    raise ValueError(f"Captured {role} output for {task_id!r} must be an object")
                normalized = copy.deepcopy(role_output)
                if role == "tester":
                    if any(field in normalized for field in ("steps", "coverage", "assumptions")):
                        raise ValueError("Captured tester steps, coverage and assumptions must be task metadata")
                    for field in ("steps", "coverage"):
                        if not isinstance(task_output[field], list):
                            raise ValueError(f"Captured tester {field} for {task_id!r} must be a list")
                        normalized[field] = copy.deepcopy(task_output[field])
                    if "assumptions" in task_output:
                        assumptions = task_output["assumptions"]
                        if (not isinstance(assumptions, list)
                                or any(not isinstance(item, str) for item in assumptions)):
                            raise ValueError(f"Captured tester assumptions for {task_id!r} must be a list of strings")
                        normalized["assumptions"] = copy.deepcopy(assumptions)
                if normalized != self._roles[task_id][role]:
                    raise ValueError(f"Captured {role} output does not match bundle.roles for {task_id!r}")

    def _validate_reviewer_hashes(self) -> None:
        for task_id in self.task_ids:
            developer_code = self._roles[task_id]["developer"].get("code")
            if not isinstance(developer_code, str):
                raise ValueError(f"Captured developer code for {task_id!r} must be a string")
            reviewed_hash = self._roles[task_id]["reviewer"].get("reviewed_code_sha256")
            if (not isinstance(reviewed_hash, str) or len(reviewed_hash) != 64
                    or any(character not in "0123456789abcdefABCDEF" for character in reviewed_hash)):
                raise ValueError(f"Captured reviewer hash for {task_id!r} must be a SHA-256 hex digest")
            expected = hashlib.sha256(developer_code.encode("utf-8")).hexdigest()
            if reviewed_hash.casefold() != expected:
                raise ValueError(f"Captured reviewer hash is stale for developer code in {task_id!r}")

    def _validate_additional_suites(self, value: Any) -> tuple[dict[str, str], list[dict]]:
        if not isinstance(value, dict) or set(value) != set(self.task_ids):
            raise ValueError("additional_suite_paths must match all bundled task ids exactly")
        result: dict[str, str] = {}
        safe: list[dict] = []
        for task_id, suite in value.items():
            if not isinstance(suite, dict) or set(suite) != {"path", "sha256"}:
                raise ValueError(f"Additional suite for {task_id!r} requires exactly path and sha256")
            relative = suite["path"]
            digest = suite["sha256"]
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(character not in "0123456789abcdefABCDEF" for character in digest)):
                raise ValueError(f"Additional suite SHA-256 for {task_id!r} must be a hex digest")
            path = _require_local_relative_path(
                relative, self._base, f"additional_suite_paths.{task_id}.path", must_exist=True
            )
            observed = hashlib.sha256(path.read_bytes()).hexdigest()
            if observed != digest.casefold():
                raise ValueError(f"Additional suite SHA-256 mismatch for {task_id!r}")
            result[task_id] = str(path)
            safe.append({"task_id": task_id, "path": Path(relative).as_posix(), "sha256": observed})
        return result, safe

    def _validate_public_context(self, role: str, context: dict) -> None:
        task_id = context["task_id"]
        baseline = self._public_tasks.get(task_id, {})
        observed = self._observed_public.get(task_id)
        for field in ("specification", "entry_point"):
            if field in baseline and context.get(field) != baseline[field]:
                raise ValueError(f"Public task {field} mismatch for {task_id!r}")
            if observed is not None and field in observed and context.get(field) != observed[field]:
                raise ValueError(f"Public task {field} changed for {task_id!r}")
        if role in _INITIAL_CODE_ROLES:
            if "initial_code" in baseline and context.get("code") != baseline["initial_code"]:
                raise ValueError(f"Public task initial_code mismatch for {task_id!r}")
            if observed is not None and "initial_code" in observed and context.get("code") != observed["initial_code"]:
                raise ValueError(f"Public task initial_code changed for {task_id!r}")
        if observed is None:
            candidate = {}
            if isinstance(context.get("specification"), str):
                candidate["specification"] = context["specification"]
            if isinstance(context.get("entry_point"), str):
                candidate["entry_point"] = context["entry_point"]
            if role in _INITIAL_CODE_ROLES and isinstance(context.get("code"), str):
                candidate["initial_code"] = context["code"]
            self._observed_public[task_id] = candidate

    def invoke(self, role: str, context: dict) -> dict:
        started = time.monotonic()
        if role not in ROLES:
            raise ValueError("Unknown agent role")
        if not isinstance(context, dict):
            raise ValueError("Session context must be an object")
        _reject_protected_data(context)
        task_id = context.get("task_id")
        if task_id not in self._roles:
            raise ValueError(f"Unknown task id for captured session: {task_id!r}")
        self._validate_public_context(role, context)
        key = (task_id, role)
        with self._lock:
            if key in self._used:
                if role in {"developer", "reviewer"}:
                    raise ValueError(f"Captured {role} output is single-use; repair retries require a new live generation")
                raise ValueError(f"Captured {role} output for {task_id!r} was already consumed")
            result = copy.deepcopy(self._roles[task_id][role])
            self._used.add(key)
            record = self._record_for_role[role]
            call = {
                "role": role,
                "task_id": task_id,
                "provider": "captured_codex_session",
                "source": "captured_codex_session",
                "model": CAPTURED_MODEL,
                "agent_id": record["agent_id"],
                "captured_at": record["generated_at"],
                "evaluation_replay": True,
                "duration_s": round(time.monotonic() - started, 6),
                "usage": {},
            }
            if "generation_duration_s" in record:
                call["generation_duration_s"] = record["generation_duration_s"]
            self.calls.append(call)
        return result

    def metadata(self) -> dict:
        return {
            "model": CAPTURED_MODEL,
            "execution_mode": "captured_codex_session",
            "api_backed": False,
            "generation_records": copy.deepcopy(self._safe_records),
            "additional_suites": copy.deepcopy(self._safe_additional_suites),
            "generation_time_in_evaluation": False,
            "source_trust": (
                "Trusted local capture declaration only: SHA-256 verifies the saved artifacts, "
                "but does not cryptographically prove model or cloud identity."
            ),
        }

