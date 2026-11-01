"""Prepare and judge a fixed FEA-Bench case without exposing evaluator patches.

This is a native Windows preexperiment, not the official container benchmark.
Only public/input.json and public/base are inputs to the repair agent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from testfix.project_eval import _mapping_sha256
from testfix.project_tools import files_under
PILOT = ROOT / "artifacts" / "fea-pilot-20261015"
CASE = PILOT / "evaluator-only" / "sqlglot-415"
PUBLIC = PILOT / "public" / "sqlglot-415"
INSTANCE = "tobymao__sqlglot-415"
BASE = "7f40ddd9b41f3dabdcbc3105cf5f06faacd82d75"
TARGET = "tests/test_build.py::TestBuild::test_build"
DEPENDENCIES = ["pytest==7.4.4", "pip==25.3", "setuptools==80.9.0", "wheel==0.45.1", "numpy==1.23.5",
                "pandas==1.4.4", "duckdb==0.5.1", "python-dateutil==2.9.0.post0", "colorama==0.4.6",
                "exceptiongroup==1.3.1", "iniconfig==2.1.0", "packaging==26.3", "pluggy==1.6.0",
                "pytz==2026.5", "six==1.17.0", "tomli==2.5.0", "typing-extensions==4.16.0"]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download(url: str, path: Path, limit: int = 12_000_000) -> None:
    if path.exists():
        return
    request = urllib.request.Request(url, headers={"User-Agent": "TestFix-native-FEA-pilot/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        value = response.read(limit + 1)
    if len(value) > limit:
        raise ValueError("Download exceeds bounded size")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def clean_env(workspace: Path | None = None) -> dict[str, str]:
    keep = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "COMSPEC", "PATHEXT"}
    result = {key: value for key, value in os.environ.items() if key.upper() in keep}
    result.update(PYTHONNOUSERSITE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1",
                  PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    if workspace:
        result["PYTHONPATH"] = str(workspace)
    return result


def command(args: list[str], cwd: Path, log: Path, timeout: int = 180) -> subprocess.CompletedProcess:
    start = time.monotonic()
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(args, cwd=cwd, env=clean_env(cwd), capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        log.write_bytes((exc.stdout or b"") + b"\nTIMEOUT\n" + (exc.stderr or b""))
        raise RuntimeError(f"Timed out; private log: {log}") from exc
    log.write_bytes(result.stdout + b"\n--- STDERR ---\n" + result.stderr)
    write_json(log.with_suffix(log.suffix + ".meta.json"), {
        "args": args, "cwd": str(cwd), "exit_code": result.returncode,
        "duration_seconds": round(time.monotonic() - start, 3),
    })
    return result


def make_copy(source: Path, target: Path) -> None:
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite {target}")
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "*.egg-info", "build", "dist"))
    result = command(["git", "init", "--quiet"], target, target.parent / (target.name + "-git-init.log"))
    if result.returncode:
        raise RuntimeError("git init failed")


def apply_patch(source: Path, patch: Path, log: Path) -> None:
    result = command(["git", "apply", "--check", str(patch)], source, log.with_suffix(".check.log"))
    if result.returncode:
        reverse = command(["git", "apply", "--reverse", "--check", str(patch)], source, log.with_suffix(".reverse-check.log"))
        if reverse.returncode == 0:
            return
        raise RuntimeError(f"Patch check failed; private log: {log}")
    result = command(["git", "apply", str(patch)], source, log)
    if result.returncode:
        raise RuntimeError(f"Patch application failed; private log: {log}")


def tests(python: Path, source: Path, selected: list[str], output: Path, label: str) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    before_files = files_under(source)
    expected_origin = (source / "sqlglot" / "__init__.py").resolve()
    junit = output / (label + ".xml")
    log = output / (label + ".log")
    binding = output / (label + "-binding.json")
    result = command([str(python), "-B", str(ROOT / "testfix" / "package_pytest_worker.py"),
                      "--project", str(source), "--source-root", ".", "--package", "sqlglot", "--binding-json", str(binding),
                      "--", "-q", "-p", "no:cacheprovider", "--junitxml=" + str(junit), *selected], source, log)
    record = json.loads(binding.read_text(encoding="utf-8")) if binding.exists() else {}
    modules = {**record.get("before_modules", {}), **record.get("modules", {})}
    binding_valid = bool(record.get("imported") and record.get("success") and not record.get("escaped_modules"))
    for location in modules.values():
        module_path = Path(location).resolve()
        if not module_path.is_relative_to(source.resolve()):
            binding_valid = False
            continue
        relative = module_path.relative_to(source.resolve()).as_posix()
        if not module_path.is_file() or before_files.get(relative) != digest(module_path):
            binding_valid = False
    binding_valid = binding_valid and modules.get("sqlglot") == str(expected_origin)
    source_unchanged = before_files == files_under(source)
    cases = list(ET.parse(junit).getroot().iter("testcase")) if junit.exists() else []
    failed = sum(case.find("failure") is not None for case in cases)
    errors = sum(case.find("error") is not None for case in cases)
    skipped = sum(case.find("skipped") is not None for case in cases)
    # pytest can select the enclosing TestFix pyproject as rootdir. Its JUnit
    # class names then include this evaluation copy's path. Strip only that
    # exact source prefix so frozen test identities compare across copies.
    prefix = ".".join(source.resolve().relative_to(ROOT).parts) + "."
    def case_id(case: ET.Element) -> str:
        return case.get("classname", "").removeprefix(prefix) + "::" + case.get("name", "")
    case_ids = sorted(case_id(case) for case in cases)
    outcomes = {case_id(case):
                ("failed" if case.find("failure") is not None else "error" if case.find("error") is not None else
                 "skipped" if case.find("skipped") is not None else "passed") for case in cases}
    unique_ids = len(set(case_ids)) == len(case_ids)
    return {"exit_code": result.returncode, "total": len(cases), "passed": len(cases) - failed - errors - skipped,
            "failed": failed, "errors": errors, "skipped": skipped, "log": str(log), "junit": str(junit),
            "all_passed": bool(cases) and result.returncode == 0 and failed == errors == skipped == 0
                          and binding_valid and source_unchanged and unique_ids,
            "import_binding_valid": binding_valid, "import_origin": str(expected_origin),
            "binding_json": str(binding), "imported_module_count": len(modules), "source_tree_unchanged": source_unchanged,
            "test_case_ids_sha256": _mapping_sha256({value: value for value in case_ids}),
            "test_case_outcomes_sha256": _mapping_sha256(outcomes)}


def prepare(args: argparse.Namespace) -> dict:
    case_dir = Path(args.case_dir).resolve()
    public = PUBLIC
    metadata = json.loads((PILOT / "collection" / "first-task-metadata.json").read_text(encoding="utf-8"))
    request = json.loads((PILOT / "collection" / "first-task-request.json").read_text(encoding="utf-8"))
    if metadata["instance_id"] != INSTANCE or metadata["base_commit"] != BASE:
        raise ValueError("Fixed task differs from preregistered metadata")
    case_dir.mkdir(parents=True, exist_ok=True)
    public.mkdir(parents=True, exist_ok=True)
    archive = case_dir / "base.zip"
    diff = case_dir / "pull.diff"
    patch = case_dir / "pull.patch"
    download(f"https://codeload.github.com/tobymao/sqlglot/zip/{BASE}", archive)
    download("https://github.com/tobymao/sqlglot/pull/415.diff", diff)
    download("https://github.com/tobymao/sqlglot/pull/415.patch", patch)
    base = public / "base"
    if not base.exists():
        base.mkdir()
        with zipfile.ZipFile(archive) as zipped:
            prefix = zipped.namelist()[0].split("/", 1)[0] + "/"
            for info in zipped.infolist():
                relative = info.filename.removeprefix(prefix)
                if not relative:
                    continue
                dest = (base / relative).resolve()
                if not dest.is_relative_to(base.resolve()):
                    raise ValueError("Unsafe archive member")
                if info.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zipped.read(info))
    pieces = re.split(r"(?=^diff --git )", diff.read_text(encoding="utf-8"), flags=re.M)
    gold, acceptance = [], []
    for piece in pieces:
        if not piece.startswith("diff --git "):
            continue
        name = piece.splitlines()[0].split(" b/", 1)[1]
        (acceptance if name.startswith("tests/") else gold).append(piece)
    if not gold or not acceptance:
        raise ValueError("Missing production or acceptance patch")
    for name, pieces in [("gold.patch", gold), ("tests.patch", acceptance)]:
        expected = "".join(pieces).encode("utf-8")
        dest = case_dir / name
        if dest.exists() and dest.read_bytes() != expected:
            raise RuntimeError("Refusing to overwrite a changed independent patch")
        if not dest.exists():
            dest.write_bytes(expected)
    package_python = case_dir / "venv" / "Scripts" / "python.exe"
    if not package_python.exists():
        result = command([args.interpreter, "-m", "venv", "--without-pip", str(case_dir / "venv")], case_dir, case_dir / "venv-create.log")
        if result.returncode:
            raise RuntimeError("Unable to create isolated case environment")
    result = command(["uv", "pip", "install", "--python", str(package_python), "--only-binary=:all:", *DEPENDENCIES], case_dir, case_dir / "dependency-install.log")
    if result.returncode:
        raise RuntimeError("Unable to provision pinned preexperiment dependencies")
    frozen = command(["uv", "pip", "freeze", "--python", str(package_python)], case_dir, case_dir / "dependency-freeze.log")
    if frozen.returncode:
        raise RuntimeError("Unable to record dependency environment")
    (case_dir / "requirements.lock.txt").write_bytes(frozen.stdout)
    allowed = sorted(p.relative_to(base).as_posix() for p in (base / "sqlglot").rglob("*.py"))
    base_file_hashes = files_under(base)
    sealed = {"instance_id": INSTANCE, "base_commit": BASE, "baseline": str(base),
        "python": str(package_python), "target": TARGET, "allowed_paths": allowed,
        "baseline_hashes": base_file_hashes, "sources": {"base_archive": digest(archive), "pull_diff": digest(diff), "pull_patch": digest(patch),
        "gold_patch": digest(case_dir / "gold.patch"), "tests_patch": digest(case_dir / "tests.patch")}}
    if (case_dir / "case.json").exists():
        existing = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
        if existing != sealed:
            raise RuntimeError("Refusing to reseal changed baseline, source patch or case settings")
    else:
        write_json(case_dir / "case.json", sealed)
    baseline = case_dir / "baseline-original"
    if not baseline.exists():
        make_copy(base, baseline)
    baseline_official = case_dir / "baseline-official"
    if not baseline_official.exists():
        make_copy(base, baseline_official)
    apply_patch(baseline_official, case_dir / "tests.patch", case_dir / "baseline-apply-tests.log")
    gold_source = case_dir / "gold-source"
    if not gold_source.exists():
        make_copy(base, gold_source)
    apply_patch(gold_source, case_dir / "gold.patch", case_dir / "gold-apply-production.log")
    gold_official = case_dir / "gold-official"
    if not gold_official.exists():
        make_copy(gold_source, gold_official)
    apply_patch(gold_official, case_dir / "tests.patch", case_dir / "gold-apply-tests.log")
    run_index = 1
    while (case_dir / "preflight" / ("run-%03d" % run_index)).exists():
        run_index += 1
    output = case_dir / "preflight" / ("run-%03d" % run_index)
    results = {"environment": "native-windows-preexperiment", "official_benchmark_score": False,
        "baseline_regression": tests(package_python, baseline, ["tests"], output, "baseline-regression"),
        "baseline_official": tests(package_python, baseline_official, [TARGET], output, "baseline-official"),
        "gold_regression": tests(package_python, gold_source, ["tests"], output, "gold-regression"),
        "gold_official": tests(package_python, gold_official, [TARGET], output, "gold-official")}
    results["target_stable"] = (results["baseline_official"]["total"] == results["gold_official"]["total"] == 1
                                and results["baseline_official"]["test_case_ids_sha256"] == results["gold_official"]["test_case_ids_sha256"])
    results["regression_case_ids_match"] = (results["baseline_regression"]["test_case_ids_sha256"] == results["gold_regression"]["test_case_ids_sha256"])
    results["ready"] = (results["baseline_regression"]["all_passed"] and not results["baseline_official"]["all_passed"]
                        and results["baseline_official"]["failed"] == 1 and results["baseline_official"]["errors"] == 0
                        and results["baseline_official"]["skipped"] == 0 and results["baseline_official"]["import_binding_valid"]
                        and results["baseline_official"]["source_tree_unchanged"] and results["gold_regression"]["all_passed"]
                        and results["gold_official"]["all_passed"] and results["target_stable"] and results["regression_case_ids_match"])
    write_json(case_dir / "preflight.json", results)
    public_input = {"schema": "testfix.feabench-pilot-case.v1", "instance_id": INSTANCE, "repo": "tobymao/sqlglot",
        "repo_url": "https://github.com/tobymao/sqlglot", "base_commit": BASE, "title": request["title"],
        "requirement": {"id": "sqlglot-sort-cluster-builders", "description": request["body"]},
        "problem_statement": request["title"] + "\n\n" + request["body"], "public_api": [], "interface_hints": [],
        "setting": "PR-text-only native Windows preexperiment; not official FEA Standard score",
        "baseline_root": str(base), "baseline_dir": str(base), "python": str(package_python),
        "package_interpreter": str(package_python), "allowed_paths": allowed, "context_paths": [],
        "protected_paths": ["tests/**", "setup.py", "setup.cfg", "pyproject.toml", "README.md", ".git/**"],
        "regression": {"tests": ["tests"]}, "baseline_regression_tests": ["tests"],
        "evaluation": {"command": [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-X", "utf8", str(Path(__file__).resolve()), "evaluate", "--case-dir", str(case_dir), "--candidate", "{candidate}", "--output", "{output}"],
                       "interpreter": str(package_python), "package_interpreter": str(package_python), "timeout_seconds": 240}}
    write_json(public / "input.json", public_input)
    (public / "README.md").write_text("# Fixed FEA-Bench native pilot\n\nTask: `tobymao__sqlglot-415`, base `" + BASE + "`.\n\nOnly PR title/body and original source are agent inputs. No reference patch, acceptance patch, new-component AST or acceptance feedback is provided. All existing `sqlglot/**/*.py` are editable; original repository tests are protected. This Windows/venv run is a native preexperiment, not the official Docker-based FEA-Bench score.\n", encoding="utf-8")
    summary = {"instance_id": INSTANCE, "ready": results["ready"], "public_input": str(public / "input.json"),
        "baseline": str(base), "environment": results["environment"],
        **{name: {key: value for key, value in result.items() if key not in {"log", "junit"}} for name, result in results.items() if isinstance(result, dict)}}
    write_json(public / "preflight-summary.json", summary)
    return summary


def evaluate(args: argparse.Namespace) -> dict:
    case_dir, candidate, output = Path(args.case_dir).resolve(), Path(args.candidate).resolve(), Path(args.output).resolve()
    case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    preflight = json.loads((case_dir / "preflight.json").read_text(encoding="utf-8"))
    if not preflight["ready"]:
        raise RuntimeError("Reference preflight is not ready")
    baseline = Path(case["baseline"])
    if files_under(baseline) != case["baseline_hashes"]:
        raise RuntimeError("Sealed baseline has changed")
    for filename, key in [("gold.patch", "gold_patch"), ("tests.patch", "tests_patch")]:
        if digest(case_dir / filename) != case["sources"][key]:
            raise RuntimeError("Sealed independent patch has changed")
    output.mkdir(parents=True, exist_ok=True)
    changes, violations = [], []
    candidate_files = files_under(candidate)
    candidate_tree_sha256 = _mapping_sha256(candidate_files)
    for name, expected in case["baseline_hashes"].items():
        file = candidate / name
        if not file.is_file():
            violations.append("Missing original file: " + name)
        elif digest(file) != expected:
            (changes if name in case["allowed_paths"] else violations).append(name)
    for file in candidate.rglob("*"):
        name = file.relative_to(candidate).as_posix()
        if file.is_symlink() or not file.resolve().is_relative_to(candidate):
            violations.append("Symlink or escaping path: " + name)
            continue
        if not file.is_file():
            continue
        if any(part in {".git", "__pycache__", ".pytest_cache"} for part in file.relative_to(candidate).parts):
            continue
        if name not in case["baseline_hashes"]:
            violations.append("Unexpected added file: " + name)
    result = {"instance_id": INSTANCE, "environment": "native-windows-preexperiment", "official_benchmark_score": False,
        "baseline_official": preflight["baseline_official"], "baseline_regression": preflight["baseline_regression"],
        "target_stable": preflight["target_stable"], "changes": changes, "protected_file_violations": violations, "resolved": False,
        "candidate_tree_sha256": candidate_tree_sha256}
    if not violations:
        source = output / "candidate-original"
        make_copy(candidate, source)
        official = output / "candidate-official"
        make_copy(candidate, official)
        apply_patch(official, case_dir / "tests.patch", output / "apply-acceptance.log")
        python = Path(case["python"])
        result["candidate_regression"] = tests(python, source, ["tests"], output, "candidate-regression")
        result["candidate_official"] = tests(python, official, [case["target"]], output, "candidate-official")
        result["regression_case_ids_match"] = (result["baseline_regression"]["test_case_ids_sha256"] == result["candidate_regression"]["test_case_ids_sha256"])
        result["candidate_target_ids_match"] = (result["baseline_official"]["test_case_ids_sha256"] == result["candidate_official"]["test_case_ids_sha256"])
        result["resolved"] = (bool(changes) and result["candidate_regression"]["all_passed"] and result["candidate_official"]["all_passed"]
                              and result["regression_case_ids_match"] and result["candidate_target_ids_match"])
    write_json(output / "evaluation.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--case-dir", default=str(CASE))
    prepare_parser.add_argument("--interpreter", default="D:/ProgramData/anaconda3/envs/env_model/python.exe")
    evaluate_parser = sub.add_parser("evaluate")
    evaluate_parser.add_argument("--case-dir", default=str(CASE))
    evaluate_parser.add_argument("--candidate", "--candidate-dir", required=True)
    evaluate_parser.add_argument("--output", "--output-dir", required=True)
    args = parser.parse_args()
    result = prepare(args) if args.action == "prepare" else evaluate(args)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
