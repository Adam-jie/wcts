"""Seal current genuine generation outputs and immutable engineering evidence."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


BASE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name):
    return json.loads((BASE / name).read_text(encoding="utf-8"))


def main():
    source = load("source.json")
    contract = load("requirements.json")
    for name, expected in source["files"].items():
        assert sha(BASE / "baseline" / name) == expected, f"Upstream changed: {name}"
        if name not in contract["allowed_changes"]:
            assert sha(BASE / "candidate" / name) == expected, f"Unauthorized change: {name}"
    original_seal = load("pre-development-integrity.json")["test_file_hashes"]
    for name, expected in original_seal.items():
        if name.startswith("acceptance_tests/"):
            assert sha(BASE / name) == expected, f"Independent acceptance changed: {name}"
    for name, expected in load("pre-evaluation-integrity.json")["generated_tests"].items():
        assert sha(BASE / name) == expected, f"Generated tests changed during repair: {name}"
    candidate_files = {path.relative_to(BASE / "candidate").as_posix(): sha(path)
                       for path in sorted((BASE / "candidate/flaskr").rglob("*"))
                       if path.is_file() and "__pycache__" not in path.parts}
    digest = hashlib.sha256(json.dumps(candidate_files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    developer = load("developer-output.json")
    review = load("reviewer-output.json")
    assert developer["candidate_files"] == candidate_files and developer["candidate_sha256"] == digest
    assert review["approved"] is True and review["candidate_sha256"] == digest
    protected = {}
    for folder in ("baseline", "candidate", "generated_tests", "acceptance_tests"):
        for path in sorted((BASE / folder).rglob("*")):
            if path.is_file() and not {"__pycache__", ".pytest_cache", "instance"}.intersection(path.parts):
                protected[path.relative_to(BASE).as_posix()] = sha(path)
    tester = load("tester-output.json")
    records = []
    for role, prompt, output, artifacts in (
        ("tester", "tester-import-fix-input.json", "tester-output.json", {p.relative_to(BASE).as_posix(): sha(p) for p in (BASE / "generated_tests").glob("*.py")}),
        ("developer", "developer-fix-input.json", "developer-output.json", {item["path"]: item["sha256"] for item in developer["changed_files"]}),
        ("reviewer", "reviewer-input.json", "reviewer-output.json", {"reviewer-output.json": sha(BASE / "reviewer-output.json")}),
    ):
        record = {"role": role, "model": "gpt-5.6-sol", "prompt_path": prompt, "prompt_sha256": sha(BASE / prompt),
                  "output_path": output, "output_sha256": sha(BASE / output), "artifact_hashes": artifacts,
                  "agent": "/root/flaskr_" + {"tester": "test_designer", "developer": "developer", "reviewer": "reviewer"}[role],
                  "captured_at": load(output).get("captured_at")}
        if role != "tester": record["candidate_sha256"] = digest
        records.append(record)
    manifest = {"schema": "testfix.project.v1", "id": "Project/flaskr",
                "title": "Flaskr 完整博客：详情、搜索与分页补全", "source": source,
                "baseline": "baseline", "candidate": "candidate", "python": "../../.venv-flaskr/Scripts/python.exe",
                "generated_tests": "generated_tests/test_features.py", "acceptance_tests": "acceptance_tests/test_acceptance.py",
                "requirements": [item["id"] + ": " + item["description"] for item in contract["requirements"]],
                "test_steps": tester["test_steps"],
                "requirement_coverage": [{"requirement": key, "tests": tests} for key, tests in tester["requirement_coverage"].items()],
                "test_assumptions": tester["test_assumptions"] + contract["clarifications"],
                "protected_files": protected, "candidate_files": candidate_files, "generation_records": records,
                "review_path": "reviewer-output.json", "review_sha256": sha(BASE / "reviewer-output.json"),
                "prior_attempts": [{"path": name, "sha256": sha(BASE / name)} for name in
                                   ["tester-input.json", "tester-output-v1.json", "tester-fix-input.json", "tester-output-v2.json", "developer-input.json", "developer-output-v1.json",
                                    "reviewer-input-v1.json", "reviewer-output-v1.json", "boundary-probes.json"]],
                "sealed_at": datetime.now(timezone.utc).isoformat()}
    (BASE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Candidate tree:", digest)
    print("Protected files:", len(protected))


if __name__ == "__main__":
    main()
