"""Contracts for replaying a saved Codex generation session."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from testfix.session import CAPTURED_MODEL, SessionProvider, _fingerprint


DEVELOPER_CODE = "def f(x):\n    return x + 1\n"
DEVELOPER_SHA256 = hashlib.sha256(DEVELOPER_CODE.encode("utf-8")).hexdigest()
ROLES = {
    "requirements": {"requirements": ["Preserve the public interface"]},
    "tester": {
        "cases": [{"args": [1], "expected": 2}],
        "rationale": "public edge",
        "steps": ["call f"],
        "coverage": ["increment"],
        "assumptions": ["x is an integer"],
    },
    "diagnosis": {"diagnosis": "f does not increment"},
    "developer": {"code": DEVELOPER_CODE, "reason": "implement the specification"},
    "reviewer": {
        "approved": True,
        "reasons": ["execution evidence passed"],
        "reviewed_code_sha256": DEVELOPER_SHA256,
    },
}


def captured_output(roles, task_ids=("Fake/1",)):
    tasks = {}
    for task_id in task_ids:
        task = copy.deepcopy(roles)
        tester = task["tester"]
        task["steps"] = tester.pop("steps")
        task["coverage"] = tester.pop("coverage")
        if "assumptions" in tester:
            task["assumptions"] = tester.pop("assumptions")
        tasks[task_id] = task
    return {"model": CAPTURED_MODEL, "tasks": tasks}


def make_bundle(tmp_path, **updates):
    output = tmp_path / "capture.json"
    output.write_text(json.dumps(captured_output(ROLES)), encoding="utf-8")
    prompt = tmp_path / "prompt.json"
    prompt.write_text(json.dumps({"task_id": "Fake/1", "request": {"specification": "Increment x."}}), encoding="utf-8")
    additional = tmp_path / "additional.py"
    additional.write_text("def check(candidate):\n    assert candidate(1) == 2\n", encoding="utf-8")
    specification = "Increment x."
    initial_code = "def f(x):\n    pass\n"
    entry_point = "f"
    bundle = {
        "version": "testfix.codex-session.v1",
        "model": CAPTURED_MODEL,
        "generated_at": "2026-10-14T08:00:00+00:00",
        "task_ids": ["Fake/1"],
        "roles": {"Fake/1": copy.deepcopy(ROLES)},
        "generation_records": [{
            "agent_id": "codex-agent-1",
            "model": CAPTURED_MODEL,
            "roles": list(ROLES),
            "generated_at": "2026-10-14T08:00:00+00:00",
            "prompt_file": prompt.name,
            "prompt_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
            "output_file": output.name,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "generation_duration_s": 3.25,
        }],
        "public_tasks": {"Fake/1": {
            "specification": specification,
            "initial_code": initial_code,
            "entry_point": entry_point,
            "fingerprint": _fingerprint(specification, initial_code, entry_point),
        }},
        "additional_suite_paths": {"Fake/1": {
            "path": additional.name,
            "sha256": hashlib.sha256(additional.read_bytes()).hexdigest(),
        }},
    }
    bundle.update(updates)
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return path, bundle


def context(code="def f(x):\n    pass\n"):
    return {"task_id": "Fake/1", "specification": "Increment x.", "entry_point": "f", "code": code}


def rewrite_capture(path, bundle, roles):
    capture_path = path.parent / "capture.json"
    capture_path.write_text(json.dumps(captured_output(roles)), encoding="utf-8")
    bundle["roles"] = {task_id: copy.deepcopy(roles) for task_id in bundle["task_ids"]}
    bundle["generation_records"][0]["sha256"] = hashlib.sha256(capture_path.read_bytes()).hexdigest()
    path.write_text(json.dumps(bundle), encoding="utf-8")


def test_returns_deep_copies_and_records_truthful_replay_metadata(tmp_path):
    path, _ = make_bundle(tmp_path)
    provider = SessionProvider(path)
    result = provider.invoke("requirements", context())
    result["requirements"].append("mutated")
    assert provider._roles["Fake/1"]["requirements"] == ROLES["requirements"]
    call = provider.calls[0]
    assert call == {
        "role": "requirements",
        "task_id": "Fake/1",
        "provider": "captured_codex_session",
        "source": "captured_codex_session",
        "model": CAPTURED_MODEL,
        "agent_id": "codex-agent-1",
        "captured_at": "2026-10-14T08:00:00+00:00",
        "evaluation_replay": True,
        "duration_s": call["duration_s"],
        "generation_duration_s": 3.25,
        "usage": {},
    }
    assert call["duration_s"] >= 0
    metadata = provider.metadata()
    assert metadata["execution_mode"] == "captured_codex_session"
    assert metadata["api_backed"] is False
    assert metadata["generation_time_in_evaluation"] is False
    assert "output_file" not in metadata["generation_records"][0]
    assert "cloud identity" in metadata["source_trust"]
    assert provider.additional_suite_paths["Fake/1"] == str((tmp_path / "additional.py").resolve())
    assert metadata["generation_records"][0]["prompt_file"] == "prompt.json"
    assert metadata["generation_records"][0]["prompt_sha256"] == hashlib.sha256(
        (tmp_path / "prompt.json").read_bytes()
    ).hexdigest()
    assert metadata["additional_suites"] == [{
        "task_id": "Fake/1",
        "path": "additional.py",
        "sha256": hashlib.sha256((tmp_path / "additional.py").read_bytes()).hexdigest(),
    }]


def test_tester_metadata_is_deterministically_bound_and_merged(tmp_path):
    path, _ = make_bundle(tmp_path)
    provider = SessionProvider(path)
    tester = provider.invoke("tester", context())
    assert tester == ROLES["tester"]
    assert tester["steps"] == ["call f"]
    assert tester["coverage"] == ["increment"]
    assert tester["assumptions"] == ["x is an integer"]


def test_accepts_actual_artifact_shape_with_optional_model_and_assumptions(tmp_path):
    path, _ = make_bundle(tmp_path)
    provider = SessionProvider(path)
    assert provider.invoke("tester", context())["assumptions"] == ["x is an integer"]

    path, bundle = make_bundle(tmp_path)
    capture_path = tmp_path / "capture.json"
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    del capture["model"]
    capture_path.write_text(json.dumps(capture), encoding="utf-8")
    bundle["generation_records"][0]["sha256"] = hashlib.sha256(capture_path.read_bytes()).hexdigest()
    path.write_text(json.dumps(bundle), encoding="utf-8")
    assert SessionProvider(path).invoke("tester", context())["assumptions"] == ["x is an integer"]


def test_rejects_captured_output_model_mismatch(tmp_path):
    path, bundle = make_bundle(tmp_path)
    capture_path = tmp_path / "capture.json"
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    capture["model"] = "gpt-other"
    capture_path.write_text(json.dumps(capture), encoding="utf-8")
    bundle["generation_records"][0]["sha256"] = hashlib.sha256(capture_path.read_bytes()).hexdigest()
    path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="output_file model"):
        SessionProvider(path)


@pytest.mark.parametrize("mutation, message", [
    ({"version": "wrong"}, "version"),
    ({"model": "gpt-other"}, "model"),
    ({"task_ids": ["Fake/1", "Fake/1"]}, "Duplicate task"),
    ({"roles": {"Fake/1": {key: value for key, value in ROLES.items() if key != "reviewer"}}}, "five captured roles"),
])
def test_rejects_invalid_bundle_shape(tmp_path, mutation, message):
    path, _ = make_bundle(tmp_path, **mutation)
    with pytest.raises(ValueError, match=message):
        SessionProvider(path)


def test_rejects_unknown_and_duplicate_invocations_including_repair_retry(tmp_path):
    path, _ = make_bundle(tmp_path)
    provider = SessionProvider(path)
    with pytest.raises(ValueError, match="Unknown task"):
        provider.invoke("tester", {**context(), "task_id": "Fake/unknown"})
    first = provider.invoke("developer", context())
    assert first["code"].endswith("x + 1\n")
    with pytest.raises(ValueError, match="repair retries require a new live generation"):
        provider.invoke("developer", context(first["code"]))


@pytest.mark.parametrize("protected", ["canonical_solution", "acceptance", "acceptance_code"])
def test_protected_oracle_fields_are_rejected_recursively(tmp_path, protected):
    path, _ = make_bundle(tmp_path)
    provider = SessionProvider(path)
    with pytest.raises(ValueError, match="Protected data"):
        provider.invoke("tester", {**context(), "nested": [{protected: "secret"}]})
    assert provider.calls == []


def test_rejects_output_path_escape_and_tampered_capture(tmp_path):
    outside = tmp_path.parent / "secret-output.txt"
    outside.write_text("do not read", encoding="utf-8")
    path, bundle = make_bundle(tmp_path)
    bundle["generation_records"][0]["output_file"] = "../secret-output.txt"
    bundle["generation_records"][0]["sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="bundle directory"):
        SessionProvider(path)

    path, bundle = make_bundle(tmp_path)
    (tmp_path / "capture.json").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        SessionProvider(path)


def test_rejects_bundle_role_tampering_despite_unchanged_capture_hash(tmp_path):
    path, bundle = make_bundle(tmp_path)
    bundle["roles"]["Fake/1"]["developer"]["code"] = "def f(x):\n    return 999\n"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match=r"Captured developer output does not match bundle\.roles"):
        SessionProvider(path)


def test_rejects_record_missing_output_for_another_bundled_task(tmp_path):
    path, bundle = make_bundle(tmp_path)
    bundle["task_ids"] = ["Fake/1", "Fake/2"]
    bundle["roles"]["Fake/2"] = copy.deepcopy(ROLES)
    bundle["public_tasks"]["Fake/2"] = copy.deepcopy(bundle["public_tasks"]["Fake/1"])
    path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="tasks must match all bundled task ids"):
        SessionProvider(path)


def test_rejects_platform_independent_output_path_escape(tmp_path):
    path, bundle = make_bundle(tmp_path)
    bundle["generation_records"][0]["output_file"] = "..\\secret-output.txt"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="bundle directory"):
        SessionProvider(path)


def test_rejects_tampered_or_missing_prompt_artifact(tmp_path):
    path, _ = make_bundle(tmp_path)
    (tmp_path / "prompt.json").write_text(json.dumps({"task_id": "tampered"}), encoding="utf-8")
    with pytest.raises(ValueError, match="prompt SHA-256 mismatch"):
        SessionProvider(path)

    path, _ = make_bundle(tmp_path)
    (tmp_path / "prompt.json").unlink()
    with pytest.raises(ValueError, match="bundle directory"):
        SessionProvider(path)


def test_rejects_prompt_oracle_and_path_escape(tmp_path):
    path, bundle = make_bundle(tmp_path)
    prompt = tmp_path / "prompt.json"
    prompt.write_text(json.dumps({"nested": [{"acceptance_code": "secret"}]}), encoding="utf-8")
    bundle["generation_records"][0]["prompt_sha256"] = hashlib.sha256(prompt.read_bytes()).hexdigest()
    path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="Protected data"):
        SessionProvider(path)

    outside = tmp_path.parent / "outside-prompt.json"
    outside.write_text(json.dumps({"task_id": "Fake/1"}), encoding="utf-8")
    path, bundle = make_bundle(tmp_path)
    bundle["generation_records"][0]["prompt_file"] = "../outside-prompt.json"
    bundle["generation_records"][0]["prompt_sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="bundle directory"):
        SessionProvider(path)


def test_rejects_tampered_missing_or_partial_additional_suites(tmp_path):
    path, _ = make_bundle(tmp_path)
    (tmp_path / "additional.py").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Additional suite SHA-256 mismatch"):
        SessionProvider(path)

    path, _ = make_bundle(tmp_path)
    (tmp_path / "additional.py").unlink()
    with pytest.raises(ValueError, match="bundle directory"):
        SessionProvider(path)

    path, bundle = make_bundle(tmp_path)
    bundle["additional_suite_paths"] = {}
    path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="match all bundled task ids exactly"):
        SessionProvider(path)


def test_rejects_additional_suite_path_escape(tmp_path):
    outside = tmp_path.parent / "outside-suite.py"
    outside.write_text("def check(candidate):\n    pass\n", encoding="utf-8")
    path, bundle = make_bundle(tmp_path)
    bundle["additional_suite_paths"]["Fake/1"] = {
        "path": "../outside-suite.py",
        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
    }
    path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="bundle directory"):
        SessionProvider(path)


def test_public_fingerprint_locks_spec_and_initial_code_but_allows_candidate_code(tmp_path):
    path, _ = make_bundle(tmp_path)
    with pytest.raises(ValueError, match="specification mismatch"):
        SessionProvider(path).invoke("requirements", {**context(), "specification": "changed"})
    with pytest.raises(ValueError, match="initial_code mismatch"):
        SessionProvider(path).invoke("tester", context("def f(x):\n    return 9\n"))
    provider = SessionProvider(path)
    assert provider.invoke("developer", context("def f(x):\n    return x\n"))["reason"]
    assert provider.invoke("reviewer", context("def f(x):\n    return x + 1\n"))["approved"] is True


def test_invalid_declared_public_fingerprint_is_rejected(tmp_path):
    path, bundle = make_bundle(tmp_path)
    bundle["public_tasks"]["Fake/1"]["fingerprint"] = "0" * 64
    path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        SessionProvider(path)


@pytest.mark.parametrize("public_tasks, message", [
    ({}, "match all bundled task ids exactly"),
    ({"Fake/1": {"specification": "Increment x."}}, "exactly four baseline fields"),
])
def test_public_tasks_rejects_empty_or_incomplete_baselines(tmp_path, public_tasks, message):
    path, bundle = make_bundle(tmp_path)
    bundle["public_tasks"] = public_tasks
    path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        SessionProvider(path)


@pytest.mark.parametrize("reviewed_hash, message", [
    (None, "must be a SHA-256 hex digest"),
    ("not-a-hash", "must be a SHA-256 hex digest"),
    ("0" * 64, "is stale"),
])
def test_reviewer_hash_must_exist_and_match_captured_developer_code(tmp_path, reviewed_hash, message):
    path, bundle = make_bundle(tmp_path)
    roles = copy.deepcopy(ROLES)
    if reviewed_hash is None:
        del roles["reviewer"]["reviewed_code_sha256"]
    else:
        roles["reviewer"]["reviewed_code_sha256"] = reviewed_hash
    rewrite_capture(path, bundle, roles)
    with pytest.raises(ValueError, match=message):
        SessionProvider(path)


@pytest.mark.parametrize("oracle_key", ["test", "hidden_test", "hidden_tests"])
def test_native_hidden_test_fields_are_rejected_even_with_matching_prompt_hash(tmp_path, oracle_key):
    path, bundle = make_bundle(tmp_path)
    prompt = tmp_path / "prompt.json"
    prompt.write_text(json.dumps({"task_id": "Fake/1", "upstream_row": {oracle_key: "def check(candidate): hidden assertion"}}), encoding="utf-8")
    bundle["generation_records"][0]["prompt_sha256"] = hashlib.sha256(prompt.read_bytes()).hexdigest()
    path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="Protected data"):
        SessionProvider(path)


@pytest.mark.parametrize("oracle_key", ["test", "hidden_test", "hidden_tests"])
def test_native_hidden_test_fields_cannot_enter_runtime_role_context(tmp_path, oracle_key):
    path, _ = make_bundle(tmp_path)
    with pytest.raises(ValueError, match="Protected data"):
        SessionProvider(path).invoke("tester", {**context(), "nested": [{oracle_key: "private evaluator"}]})
