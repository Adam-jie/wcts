from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from testfix.config import Settings
import testfix.project_eval as project_eval
from testfix.project_eval import (_cases, _functional_failure, _mapping_sha256, _paired_cases_match,
                                  _run_suite, evaluate_project)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_junit_preserves_identity_and_rejects_collection_error(tmp_path):
    xml = tmp_path / "result.xml"
    xml.write_text('<testsuites><testsuite errors="1"><testcase classname="test_a" name="same"/>'
                   '<testcase classname="test_b" name="same"><failure/></testcase></testsuite></testsuites>', encoding="utf-8")
    cases, errors = _cases(xml)
    assert cases == [{"id": "test_a::same", "status": "passed"}, {"id": "test_b::same", "status": "failure"}]
    assert errors == 1


def test_candidate_mapping_hash_is_canonical():
    assert _mapping_sha256({"b.py": "2", "a.py": "1"}) == _mapping_sha256({"a.py": "1", "b.py": "2"})


def make_manifest(tmp_path: Path) -> Path:
    for repo in (tmp_path / "base", tmp_path / "candidate"):
        (repo / "flaskr").mkdir(parents=True)
        (repo / "tests").mkdir()
        (repo / "flaskr" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (repo / "flaskr" / "template.html").write_text("<p>old</p>\n", encoding="utf-8")
        (repo / "tests" / "test_old.py").write_text("def test_old(): assert True\n", encoding="utf-8")
    (tmp_path / "generated.py").write_text("def test_new(): assert True\n", encoding="utf-8")
    (tmp_path / "acceptance.py").write_text("def test_accept(): assert True\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text("# sealed fixture support\n", encoding="utf-8")
    changed = tmp_path / "candidate/flaskr/template.html"
    changed.write_bytes(b"<p>new</p>\n")
    candidate_files = {name: digest(tmp_path / "candidate" / name) for name in ("flaskr/app.py", "flaskr/template.html")}
    review = {"approved": True, "role": "reviewer", "model": "gpt-5.6-sol",
              "candidate_sha256": _mapping_sha256(candidate_files)}
    (tmp_path / "review.json").write_text(json.dumps(review), encoding="utf-8")
    tester_output = {"role": "requirements_and_tester", "model": "gpt-5.6-sol"}
    developer_output = {"role": "diagnosis_and_developer", "model": "gpt-5.6-sol",
                        "changed_files": [{"path": "candidate/flaskr/template.html",
                                           "source": changed.read_text(encoding="utf-8"), "sha256": digest(changed)}]}
    for role, output in (("tester", tester_output), ("developer", developer_output)):
        (tmp_path / f"{role}-prompt.json").write_text(json.dumps({"role": role}), encoding="utf-8")
        (tmp_path / f"{role}-output.json").write_text(json.dumps(output), encoding="utf-8")
    (tmp_path / "reviewer-prompt.json").write_text(json.dumps({"role": "reviewer"}), encoding="utf-8")
    protected = {}
    for path in [tmp_path / "generated.py", tmp_path / "acceptance.py",
                 tmp_path / "conftest.py", tmp_path / "base/flaskr/app.py", tmp_path / "base/flaskr/template.html",
                 tmp_path / "base/tests/test_old.py", tmp_path / "candidate/flaskr/app.py",
                 tmp_path / "candidate/flaskr/template.html", tmp_path / "candidate/tests/test_old.py"]:
        protected[path.relative_to(tmp_path).as_posix()] = digest(path)
    manifest = {"schema": "testfix.project.v1", "id": "demo", "title": "demo", "source": {},
                "baseline": "base", "candidate": "candidate", "python": "python.exe",
                "generated_tests": "generated.py", "acceptance_tests": "acceptance.py",
                "protected_files": protected, "candidate_files": candidate_files,
                "generation_records": [],
                "review_path": "review.json", "review_sha256": digest(tmp_path / "review.json")}
    tester_artifacts = {name: digest(tmp_path / name) for name in ("generated.py", "acceptance.py", "conftest.py")}
    role_data = {
        "tester": ("tester-output.json", tester_artifacts, {}),
        "developer": ("developer-output.json", {"candidate/flaskr/template.html": digest(changed)},
                      {"candidate_sha256": _mapping_sha256(candidate_files)}),
        "reviewer": ("review.json", {"review.json": digest(tmp_path / "review.json")},
                     {"candidate_sha256": _mapping_sha256(candidate_files)}),
    }
    for role, (output_name, artifacts, extra) in role_data.items():
        prompt_name = f"{role}-prompt.json"
        manifest["generation_records"].append({"role": role, "model": "gpt-5.6-sol",
            "prompt_path": prompt_name, "prompt_sha256": digest(tmp_path / prompt_name),
            "output_path": output_name, "output_sha256": digest(tmp_path / output_name),
            "artifact_hashes": artifacts, **extra})
    path = tmp_path / "manifest.json"
    shutil.copy2(sys.executable, tmp_path / "python.exe")
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_hash_tampering_is_rejected_before_execution(tmp_path):
    manifest = make_manifest(tmp_path)
    (tmp_path / "acceptance.py").write_text("def test_changed(): pass\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        evaluate_project(manifest, Settings(root=tmp_path), "tamper")


def test_candidate_files_must_cover_all_runtime_source(tmp_path):
    manifest = make_manifest(tmp_path)
    extra = tmp_path / "candidate/flaskr/style.css"
    extra.write_text("body {}\n", encoding="utf-8")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["protected_files"]["candidate/flaskr/style.css"] = digest(extra)
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="cover every"):
        evaluate_project(manifest, Settings(root=tmp_path), "coverage")


def test_relative_path_escape_is_rejected(tmp_path):
    manifest = make_manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["generated_tests"] = "../outside.py"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="escapes"):
        evaluate_project(manifest, Settings(root=tmp_path), "escape")


def test_generated_suite_directory_support_is_sealed(tmp_path):
    manifest = make_manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    del data["protected_files"]["conftest.py"]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="does not seal"):
        evaluate_project(manifest, Settings(root=tmp_path), "support")


def test_original_test_trees_must_be_identical(tmp_path):
    manifest = make_manifest(tmp_path)
    changed = tmp_path / "candidate/tests/test_old.py"
    changed.write_text("def test_old(): assert False\n", encoding="utf-8")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["protected_files"]["candidate/tests/test_old.py"] = digest(changed)
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="byte-identical"):
        evaluate_project(manifest, Settings(root=tmp_path), "tests-changed")


def test_absolute_python_must_remain_in_workspace(tmp_path):
    manifest = make_manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["python"] = sys.executable
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="python escapes"):
        evaluate_project(manifest, Settings(root=tmp_path), "python-escape")


def test_duplicate_junit_identity_cannot_pass(tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    python.write_bytes(b"placeholder")
    tests = tmp_path / "test_x.py"
    tests.write_text("def test_x(): pass\n", encoding="utf-8")
    def fake_run(command, **kwargs):
        xml_path = Path(command[command.index("--junitxml") + 1])
        binding_path = Path(command[command.index("--binding-json") + 1])
        xml_path.write_text('<testsuite><testcase classname="x" name="same"/><testcase classname="x" name="same"/></testsuite>', encoding="utf-8")
        binding_path.write_text(json.dumps({"schema": "testfix.pytest-binding.v1", "project": str(tmp_path.resolve()),
                                            "success": True, "modules": {"flaskr": str((tmp_path / "flaskr/__init__.py").resolve())},
                                            "escaped_modules": [], "error": ""}), encoding="utf-8")
        assert kwargs["env"]["PYTHONUTF8"] == "1"
        assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(project_eval.subprocess, "run", fake_run)
    result = _run_suite(python, tmp_path, tests, tmp_path / "artifacts", "duplicate")
    assert result["successful"] is False
    assert result["duplicate_case_ids"] == ["x::same"]


@pytest.mark.parametrize("binding_payload", [None, {"schema": "testfix.pytest-binding.v1", "success": False,
                                                     "project": "wrong", "modules": {}, "escaped_modules": ["flaskr"]}])
def test_missing_or_invalid_binding_evidence_cannot_pass(tmp_path, monkeypatch, binding_payload):
    python = tmp_path / "python.exe"
    python.write_bytes(b"placeholder")
    tests = tmp_path / "test_x.py"
    tests.write_text("def test_x(): pass\n", encoding="utf-8")
    def fake_run(command, **kwargs):
        Path(command[command.index("--junitxml") + 1]).write_text(
            '<testsuite><testcase classname="x" name="test_x"/></testsuite>', encoding="utf-8")
        if binding_payload is not None:
            Path(command[command.index("--binding-json") + 1]).write_text(json.dumps(binding_payload), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(project_eval.subprocess, "run", fake_run)
    result = _run_suite(python, tmp_path, tests, tmp_path / "artifacts", "missing-binding")
    assert result["successful"] is False
    assert result["binding_error"]


def test_worker_pins_baseline_before_external_fixture_inserts_candidate(tmp_path):
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    external = tmp_path / "external"
    for project, value in ((baseline, "baseline"), (candidate, "candidate")):
        (project / "flaskr").mkdir(parents=True)
        (project / "flaskr/__init__.py").write_text(f"VALUE = {value!r}\n", encoding="utf-8")
    external.mkdir()
    (external / "conftest.py").write_text(
        f"import sys\nsys.path.insert(0, {str(candidate)!r})\n", encoding="utf-8")
    test_file = external / "test_binding.py"
    test_file.write_text("import flaskr\ndef test_source(): assert flaskr.VALUE == 'baseline'\n", encoding="utf-8")
    result = _run_suite(Path(sys.executable), baseline, test_file, tmp_path / "artifacts", "real-binding")
    assert result["successful"] is True
    assert result["binding"]["modules"]["flaskr"].startswith(str(baseline.resolve()))


def test_skip_or_collection_error_is_not_proof_of_missing_feature():
    suite = {"timeout": False, "parse_error": "", "collection_errors": 0, "duplicate_case_ids": [],
             "total": 1, "cases": [{"id": "x::test", "status": "skipped"}]}
    assert _functional_failure(suite) is False
    suite["cases"][0]["status"] = "failure"
    assert _functional_failure(suite) is True
    suite["collection_errors"] = 1
    assert _functional_failure(suite) is False


@pytest.mark.parametrize("before_ids,after_ids", [
    (["tests::one"], ["tests::one", "tests::two"]),
    (["tests::one", "tests::two"], ["tests::one"]),
])
def test_paired_suites_reject_superset_and_subset_case_collections(before_ids, after_ids):
    def suite(ids):
        return {"case_ids": ids, "total": len(ids), "duplicate_case_ids": []}
    assert _paired_cases_match(suite(before_ids), suite(after_ids)) is False


@pytest.mark.parametrize("pair", ["generated", "acceptance"])
def test_new_requirement_suite_missing_a_candidate_case_is_rejected(pair):
    baseline = {"case_ids": [f"{pair}::one", f"{pair}::two"], "total": 2, "duplicate_case_ids": []}
    candidate = {"case_ids": [f"{pair}::one"], "total": 1, "duplicate_case_ids": []}
    assert _paired_cases_match(baseline, candidate) is False


def test_passing_test_stage_remains_awaiting_delivery_and_unscored(tmp_path, monkeypatch):
    manifest = make_manifest(tmp_path)
    def evidence(case_id, status):
        passed = int(status == "passed")
        return {"total": 1, "passed": passed, "cases": [{"id": case_id, "status": status}],
                "case_ids": [case_id], "collection_errors": 0, "duplicate_case_ids": [],
                "runnable": True, "successful": status == "passed", "timeout": False,
                "exit_code": 0 if status == "passed" else 1, "duration_s": 0.01, "command": [],
                "cwd": str(tmp_path), "stdout_path": "", "stderr_path": "", "junit_path": "", "parse_error": ""}
    results = {
        "baseline-original": evidence("old::test", "passed"),
        "candidate-original": evidence("old::test", "passed"),
        "baseline-generated": evidence("new::generated", "failure"),
        "candidate-generated": evidence("new::generated", "passed"),
        "baseline-acceptance": evidence("new::acceptance", "failure"),
        "candidate-acceptance": evidence("new::acceptance", "passed"),
    }
    monkeypatch.setattr(project_eval, "_run_suite", lambda python, cwd, tests, artifacts, name: results[name])
    run = evaluate_project(manifest, Settings(root=tmp_path), "provisional")
    assert run["status"] == "running"
    assert run["tasks"][0]["status"] == "awaiting_delivery"
    assert run["tasks"][0]["verdict"]["test_suites_pass"] is True
    assert run["tasks"][0]["verdict"]["satisfies_tested_requirements"] is False
    assert run["finished_at"] is None
    assert run["summary"]["accepted"] == 0
