"""Independent, manifest-driven evaluation of a complete Python project.

The evaluated repositories are trusted local source code.  They execute in a
normal subprocess, not in a security sandbox.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from .reports import now, save_run


SCHEMA = "testfix.project.v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping_sha256(mapping: dict[str, str]) -> str:
    """Hash canonical UTF-8 JSON: sorted keys, no insignificant whitespace."""
    payload = json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _inside(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes settings.root") from exc
    return resolved


def _manifest_path(base: Path, value: object, root: Path, label: str, *, directory: bool = False) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty path")
    raw = Path(value)
    path = _inside(raw if raw.is_absolute() else base / raw, root, label)
    if directory and not path.is_dir():
        raise ValueError(f"{label} is not a directory")
    if not directory and not path.is_file():
        raise ValueError(f"{label} is not a file")
    return path


def _validate_hashes(base: Path, values: object, root: Path, label: str) -> dict[str, str]:
    if not isinstance(values, dict) or not values:
        raise ValueError(f"{label} must be a non-empty path-to-SHA256 object")
    result: dict[str, str] = {}
    for name, expected in values.items():
        if not isinstance(name, str) or not isinstance(expected, str) or len(expected) != 64:
            raise ValueError(f"invalid {label} entry")
        if Path(name).is_absolute():
            raise ValueError(f"{label} paths must be relative")
        path = _manifest_path(base, name, root, f"{label}.{name}")
        actual = _sha256(path)
        if actual.casefold() != expected.casefold():
            raise ValueError(f"{label} hash mismatch: {name}")
        result[name] = actual
    return result


def _runtime_files(repository: Path) -> set[str]:
    source = repository / "flaskr"
    if not source.is_dir():
        raise ValueError("candidate/flaskr is missing")
    return {
        path.relative_to(repository).as_posix()
        for path in source.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def _cases(xml_path: Path) -> tuple[list[dict], int]:
    try:
        root = ET.parse(xml_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError("pytest did not produce valid JUnit XML") from exc
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    errors = sum(int(suite.attrib.get("errors", "0")) for suite in suites)
    records = []
    for case in root.iter("testcase"):
        status = "passed"
        for child in case:
            if child.tag in {"failure", "error", "skipped"}:
                status = child.tag
                break
        records.append({"id": f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}", "status": status})
    return records, errors


def _environment() -> dict[str, str]:
    keep = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "TEMP", "TMP", "LANG", "LC_ALL"}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_suite(python: Path, cwd: Path, tests: Path, artifact_dir: Path, name: str) -> dict:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    xml_path = artifact_dir / f"{name}-{token}.xml"
    temp_path = artifact_dir / f"{name}-{token}-tmp"
    stdout_path = artifact_dir / f"{name}-{token}.stdout.txt"
    stderr_path = artifact_dir / f"{name}-{token}.stderr.txt"
    binding_path = artifact_dir / f"{name}-{token}.binding.json"
    worker = Path(__file__).with_name("project_pytest_worker.py").resolve()
    command = [str(python), str(worker), "--project", str(cwd), "--binding-json", str(binding_path), "--",
               "-q", "-p", "no:cacheprovider", "--basetemp", str(temp_path),
               "--junitxml", str(xml_path), str(tests)]
    started = time.monotonic()
    timed_out = False
    try:
        process = subprocess.run(command, cwd=cwd, env=_environment(), capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=60, check=False)
        exit_code, stdout, stderr = process.returncode, process.stdout, process.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out, exit_code = True, None
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", "replace")
    duration = round(time.monotonic() - started, 3)
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    records: list[dict] = []
    collection_errors = 0
    parse_error = ""
    if xml_path.is_file():
        try:
            records, collection_errors = _cases(xml_path)
        except ValueError as exc:
            parse_error = str(exc)
    else:
        parse_error = "JUnit XML missing"
    binding: dict = {}
    binding_error = ""
    try:
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        module_paths = binding.get("modules")
        if (binding.get("schema") != "testfix.pytest-binding.v1" or binding.get("success") is not True
                or Path(str(binding.get("project", ""))).resolve() != cwd.resolve()
                or not isinstance(module_paths, dict) or "flaskr" not in module_paths
                or any(not _inside(Path(path), cwd, "binding module") for path in module_paths.values())):
            binding_error = "invalid pytest source binding evidence"
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        binding_error = "pytest source binding evidence missing or invalid"
    passed = sum(case["status"] == "passed" for case in records)
    duplicate_case_ids = sorted({case["id"] for case in records if sum(other["id"] == case["id"] for other in records) > 1})
    successful = (not timed_out and exit_code == 0 and not parse_error and not binding_error and collection_errors == 0
                  and not duplicate_case_ids and len(records) > 0 and passed == len(records))
    return {"total": len(records), "passed": passed, "cases": records, "case_ids": [x["id"] for x in records],
            "collection_errors": collection_errors, "runnable": not timed_out and not parse_error,
            "duplicate_case_ids": duplicate_case_ids,
            "binding_path": str(binding_path), "binding": binding, "binding_error": binding_error,
            "successful": successful, "timeout": timed_out, "exit_code": exit_code, "duration_s": duration,
            "command": command, "cwd": str(cwd), "stdout_path": str(stdout_path), "stderr_path": str(stderr_path),
            "junit_path": str(xml_path), "parse_error": parse_error}


def _diff(baseline: Path, candidate: Path, files: set[str]) -> str:
    chunks = []
    for name in sorted(files):
        before_path, after_path = baseline / name, candidate / name
        before = before_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if before_path.is_file() else []
        after = after_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if after_path.is_file() else []
        chunks.extend(difflib.unified_diff(before, after, fromfile=f"a/{name}", tofile=f"b/{name}"))
    return "".join(chunks)


def _functional_failure(suite: dict) -> bool:
    """True only for a collected assertion failure, never infrastructure/skip noise."""
    return (not suite["timeout"] and not suite["parse_error"] and not suite.get("binding_error")
            and suite["collection_errors"] == 0
            and not suite["duplicate_case_ids"] and suite["total"] > 0
            and any(case["status"] == "failure" for case in suite["cases"]))


def _paired_cases_match(before: dict, after: dict) -> bool:
    """Bind a baseline result to the exact same uniquely collected cases."""
    return (not before["duplicate_case_ids"] and not after["duplicate_case_ids"]
            and before["total"] > 0 and before["case_ids"] == after["case_ids"])


def evaluate_project(manifest_path: Path, settings, run_id: str | None = None) -> dict:
    """Validate a sealed manifest, execute six pytest suites, and save a report."""
    started = time.monotonic()
    created_at = now()
    root = settings.root.resolve()
    manifest_path = _inside(Path(manifest_path), root, "manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA:
        raise ValueError(f"manifest schema must be {SCHEMA}")
    if not isinstance(manifest.get("source"), dict):
        raise ValueError("source must be an object")
    for field in ("id", "title"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise ValueError(f"{field} must be a non-empty string")
    for field in ("requirements", "test_steps", "requirement_coverage", "test_assumptions"):
        if field in manifest and not isinstance(manifest[field], list):
            raise ValueError(f"{field} must be a list")
    base = manifest_path.parent
    baseline = _manifest_path(base, manifest.get("baseline"), root, "baseline", directory=True)
    candidate = _manifest_path(base, manifest.get("candidate"), root, "candidate", directory=True)
    if baseline == candidate:
        raise ValueError("baseline and candidate must be different directories")
    generated_tests = _manifest_path(base, manifest.get("generated_tests"), root, "generated_tests")
    acceptance_tests = _manifest_path(base, manifest.get("acceptance_tests"), root, "acceptance_tests")
    if not generated_tests.read_text(encoding="utf-8").strip() or not acceptance_tests.read_text(encoding="utf-8").strip():
        raise ValueError("generated and acceptance tests must be non-empty")
    python_raw = manifest.get("python")
    if not isinstance(python_raw, str) or not python_raw:
        raise ValueError("python must be a path")
    python = Path(python_raw)
    python = _inside(python if python.is_absolute() else base / python, root, "python")
    if not python.is_file():
        raise ValueError("python executable does not exist")

    protected = _validate_hashes(base, manifest.get("protected_files"), root, "protected_files")
    protected_paths = {_inside(base / name, root, f"protected_files.{name}") for name in protected}
    required_protected = {generated_tests, acceptance_tests}
    for suite_path in (generated_tests, acceptance_tests):
        required_protected.update(path.resolve() for path in suite_path.parent.glob("*.py") if path.is_file())
    for repository in (baseline, candidate):
        for folder in (repository / "flaskr", repository / "tests"):
            if folder.is_dir():
                required_protected.update(path.resolve() for path in folder.rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    if not required_protected <= protected_paths:
        missing = sorted(str(path.relative_to(root)) for path in required_protected - protected_paths)
        raise ValueError(f"protected_files does not seal source/tests: {missing}")
    candidate_hashes = _validate_hashes(candidate, manifest.get("candidate_files"), root, "candidate_files")
    expected_sources = _runtime_files(candidate)
    if set(candidate_hashes) != expected_sources:
        raise ValueError("candidate_files must cover every candidate flaskr runtime resource exactly")
    candidate_digest = _mapping_sha256(candidate_hashes)
    original_baseline = baseline / "tests"
    original_candidate = candidate / "tests"
    if not original_baseline.exists() or not original_candidate.exists():
        raise ValueError("both repositories must contain original tests")
    for tests_dir in (original_baseline, original_candidate):
        test_files = list(tests_dir.rglob("test*.py"))
        if not test_files or not any(path.read_text(encoding="utf-8").strip() for path in test_files):
            raise ValueError("original tests must be non-empty")
    def test_tree(repository: Path) -> dict[str, str]:
        return {path.relative_to(repository / "tests").as_posix(): _sha256(path)
                for path in (repository / "tests").rglob("*")
                if path.is_file() and "__pycache__" not in path.parts}
    if test_tree(baseline) != test_tree(candidate):
        raise ValueError("baseline and candidate original test trees must be byte-identical")
    generations = manifest.get("generation_records")
    if not isinstance(generations, list) or len(generations) != 3:
        raise ValueError("generation_records must contain exactly tester, developer, and reviewer")
    checked_generations = []
    by_role: dict[str, tuple[dict, dict, dict[str, str]]] = {}
    output_roles = {"tester": "requirements_and_tester", "developer": "diagnosis_and_developer", "reviewer": "reviewer"}
    for index, record in enumerate(generations):
        if not isinstance(record, dict) or record.get("role") not in output_roles or record.get("model") != "gpt-5.6-sol":
            raise ValueError("generation record requires a valid role and model gpt-5.6-sol")
        role = record["role"]
        if role in by_role:
            raise ValueError(f"duplicate active generation role: {role}")
        prompt = _manifest_path(base, record.get("prompt_path"), root, f"generation_records[{index}].prompt")
        output = _manifest_path(base, record.get("output_path"), root, f"generation_records[{index}].output")
        if _sha256(prompt) != str(record.get("prompt_sha256", "")).casefold() or _sha256(output) != str(record.get("output_sha256", "")).casefold():
            raise ValueError("generation record hash mismatch")
        try:
            output_data = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("generation output must be JSON") from exc
        if output_data.get("role") != output_roles[role] or output_data.get("model") != "gpt-5.6-sol":
            raise ValueError("generation output role/model mismatch")
        artifacts = _validate_hashes(base, record.get("artifact_hashes"), root, f"generation_records[{index}].artifact_hashes")
        by_role[role] = (record, output_data, artifacts)
        checked_generations.append({**record, "prompt_path": str(prompt), "output_path": str(output)})
    if set(by_role) != set(output_roles):
        raise ValueError("generation records must cover all three active roles")

    tester_artifacts = {_inside(base / name, root, "tester artifact") for name in by_role["tester"][2]}
    expected_test_artifacts = {path.resolve() for path in generated_tests.parent.glob("*.py") if path.is_file()}
    if tester_artifacts != expected_test_artifacts:
        raise ValueError("tester artifact_hashes must exactly cover generated test directory Python files")
    changed_runtime = {name for name in expected_sources
                       if not (baseline / name).is_file() or _sha256(baseline / name) != candidate_hashes[name]}
    developer_record, developer_output, developer_artifacts = by_role["developer"]
    if developer_record.get("candidate_sha256", "").casefold() != candidate_digest:
        raise ValueError("developer record is bound to a different candidate")
    developer_artifact_paths = {_inside(base / name, root, "developer artifact") for name in developer_artifacts}
    if not {candidate / name for name in changed_runtime} <= developer_artifact_paths:
        raise ValueError("developer artifacts do not cover every changed runtime file")
    changed_files = developer_output.get("changed_files")
    if not isinstance(changed_files, list):
        raise ValueError("developer output changed_files must be a list")
    described: set[str] = set()
    for item in changed_files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("source"), str):
            raise ValueError("invalid developer changed_files entry")
        path = _inside(base / item["path"], root, "developer changed file")
        try:
            name = path.relative_to(candidate).as_posix()
        except ValueError as exc:
            raise ValueError("developer changed file is outside candidate") from exc
        source_bytes = item["source"].encode("utf-8")
        if path.read_bytes() != source_bytes or hashlib.sha256(source_bytes).hexdigest() != str(item.get("sha256", "")).casefold():
            raise ValueError("developer changed_files source/hash mismatch")
        described.add(name)
    if described != changed_runtime:
        raise ValueError("developer changed_files must exactly describe changed runtime files")
    review_path = _manifest_path(base, manifest.get("review_path"), root, "review_path")
    if _sha256(review_path) != str(manifest.get("review_sha256", "")).casefold():
        raise ValueError("review hash mismatch")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if review.get("approved") is not True or review.get("role") != "reviewer" or review.get("model") != "gpt-5.6-sol":
        raise ValueError("review must be an approved gpt-5.6-sol reviewer record")
    if review.get("candidate_sha256", "").casefold() != candidate_digest:
        raise ValueError("review is bound to a different candidate mapping")
    reviewer_record, reviewer_output, _ = by_role["reviewer"]
    if reviewer_record.get("candidate_sha256", "").casefold() != candidate_digest or reviewer_output != review:
        raise ValueError("reviewer generation record is not bound to the active review")

    identifier = run_id or uuid.uuid4().hex
    if not isinstance(identifier, str) or not identifier or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in identifier):
        raise ValueError("invalid run_id")
    artifact_dir = settings.artifacts / identifier
    suites = {
        "regression_baseline": _run_suite(python, baseline, original_baseline, artifact_dir, "baseline-original"),
        "generated_baseline": _run_suite(python, baseline, generated_tests, artifact_dir, "baseline-generated"),
        "baseline": _run_suite(python, baseline, acceptance_tests, artifact_dir, "baseline-acceptance"),
        "regression": _run_suite(python, candidate, original_candidate, artifact_dir, "candidate-original"),
        "generated": _run_suite(python, candidate, generated_tests, artifact_dir, "candidate-generated"),
        "acceptance": _run_suite(python, candidate, acceptance_tests, artifact_dir, "candidate-acceptance"),
    }
    before_ids = {case["id"] for case in suites["regression_baseline"]["cases"] if case["status"] == "passed"}
    after_ids = {case["id"] for case in suites["regression"]["cases"] if case["status"] == "passed"}
    identities_match = {
        "regression": _paired_cases_match(suites["regression_baseline"], suites["regression"]),
        "generated": _paired_cases_match(suites["generated_baseline"], suites["generated"]),
        "acceptance": _paired_cases_match(suites["baseline"], suites["acceptance"]),
    }
    regression_preserved = (identities_match["regression"] and suites["regression_baseline"]["successful"]
                            and suites["regression"]["successful"] and before_ids == after_ids)
    baseline_missing = _functional_failure(suites["baseline"]) and _functional_failure(suites["generated_baseline"])
    test_suites_pass = (baseline_missing and all(identities_match.values()) and regression_preserved
                        and suites["generated"]["successful"] and suites["acceptance"]["successful"])
    task = {"id": manifest.get("id"), "title": manifest.get("title"), "kind": "project", "source": manifest.get("source"),
            "requirements": manifest.get("requirements", []), "test_steps": manifest.get("test_steps", []),
            "requirement_coverage": manifest.get("requirement_coverage", []), "test_assumptions": manifest.get("test_assumptions", []),
            **suites, "status": "awaiting_delivery" if test_suites_pass else "rejected", "review": review,
            "diff": _diff(baseline, candidate, _runtime_files(baseline) | expected_sources),
            "regression_breaks": len(before_ids - after_ids),
            "verdict": {"baseline_demonstrates_missing_requirement": baseline_missing,
                        "generated_tests_pass": suites["generated"]["successful"],
                        "independent_acceptance_pass": suites["acceptance"]["successful"],
                        "regression_preserved": regression_preserved, "review_bound_to_candidate": True,
                        "paired_case_identities_match": identities_match,
                        "test_suites_pass": test_suites_pass,
                        "satisfies_tested_requirements": False},
            "duration_s": round(time.monotonic() - started, 3)}
    run = {"schema": SCHEMA, "id": identifier, "status": "running", "provider": "codex_session",
           "runner": "project_process", "created_at": created_at, "updated_at": now(), "finished_at": None, "tasks": [task],
           "summary": {"total": 1, "accepted": 0, "regressions": task["regression_breaks"], "duration_s": task["duration_s"]},
           "provenance": {"execution_mode": "captured_codex_session", "model": "gpt-5.6-sol",
                          "manifest_path": str(manifest_path), "manifest_sha256": _sha256(manifest_path),
                          "candidate_mapping_sha256": candidate_digest, "protected_files": protected,
                          "generation_records": checked_generations,
                          "api_backed": False, "generation_time_in_evaluation": False,
                          "prior_attempts": manifest.get("prior_attempts", []),
                          "execution_warning": "trusted local project source executed by host subprocess; not a sandbox"}}
    save_run(artifact_dir, run)
    return run
