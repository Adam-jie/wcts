"""Run sealed public suites against the exact SQLGlot wheel installed by delivery.

This host-side native preexperiment is supplementary evidence, never an official
container benchmark score. No model, reference solution, or private test is read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = ROOT / "artifacts/fea-pilot-20261015"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.verify_feabench_delivery import runtime_hashes, tree_hashes
from testfix.package_tools import run_package_suite


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_path(path: Path, parent: Path, *, exists=True) -> Path:
    """Reject links before resolving; resolving alone hides redirected parents."""
    path = path.absolute()
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError("Linked experiment paths are forbidden")
    result = path.resolve()
    if not result.is_relative_to(parent.resolve()) or result == parent.resolve():
        raise ValueError("Path escapes its explicit experiment root")
    if exists and not result.exists():
        raise ValueError("Required experiment path does not exist")
    return result


def fixture_hashes(root: Path) -> dict:
    if not root.is_dir():
        raise ValueError("Baseline fixture directory is missing")
    result = {}
    for path in sorted(root.rglob("*")):
        safe_path(path, root)
        if path.is_file():
            if path.suffix.lower() in {".py", ".pyc", ".pyo", ".pyd"}:
                raise ValueError("Fixture preparation may copy data only, never Python code")
            result[path.relative_to(root).as_posix()] = digest(path)
    if not result:
        raise ValueError("Baseline fixture directory is empty")
    return result


def prepare_fixtures(source: Path, target: Path) -> dict:
    """Prepare the upstream relative data paths without copying test code."""
    before = fixture_hashes(source)
    for relative, checksum in before.items():
        destination = safe_path(target / relative, target.parent.parent, exists=False)
        if destination.exists() and (not destination.is_file() or digest(destination) != checksum):
            raise ValueError("Existing installed fixture differs from frozen baseline")
    existed = target.exists()
    if existed and fixture_hashes(target) != before:
        raise ValueError("Existing installed fixtures contain missing or extra files")
    for relative in before:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copyfile(source / relative, destination)
    after = fixture_hashes(target)
    if after != before or fixture_hashes(source) != before:
        raise ValueError("Fixture byte verification failed")
    return {"source": str(source), "target": str(target), "target_preexisted": existed,
            "source_sha256": before, "target_sha256": after, "bytes_match": True,
            "file_count": len(before), "python_test_code_copied": False}


def check_success(check: dict) -> bool:
    return (check.get("exit_code") == 0 and check.get("timeout") is False
            and check.get("output_limit_exceeded") is False)


def validate_delivery(delivery: Path, candidate: Path, instance_id: str) -> tuple[dict, Path, dict]:
    report_path = safe_path(delivery / "delivery-report.json", delivery)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (report.get("schema") != "testfix.feabench-native-delivery.v1"
            or report.get("instance_id") != instance_id or report.get("candidate_unchanged") is not True):
        raise ValueError("Delivery report identity or integrity is invalid")
    candidate_runtime, candidate_tree = runtime_hashes(candidate), tree_hashes(candidate)
    if (not candidate_runtime or report.get("candidate_runtime_sha256") != candidate_runtime
            or report.get("candidate_tree_sha256") != candidate_tree):
        raise ValueError("Delivery report does not bind the supplied candidate bytes")
    checks = report.get("checks", {})
    for name in ("compile", "build_wheel", "create_venv", "install_wheel"):
        if not check_success(checks.get(name, {})):
            raise ValueError("Delivery prerequisite failed: " + name)
    wheel_info = report.get("wheel", {})
    wheel = safe_path(Path(wheel_info.get("path", "")), delivery / "wheels")
    if wheel.suffix != ".whl" or digest(wheel) != wheel_info.get("sha256"):
        raise ValueError("Delivery wheel SHA256 does not match")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Wheel contains duplicate members")
        for member in archive.infolist():
            name = member.filename
            if (name.startswith(("/", "\\")) or "\\" in name
                    or any(part in {"..", ""} for part in Path(name).parts)
                    or stat.S_ISLNK(member.external_attr >> 16)):
                raise ValueError("Wheel contains unsafe members")
        wheel_runtime = {name: hashlib.sha256(archive.read(name)).hexdigest()
                         for name in names if name.startswith("sqlglot/") and name.endswith(".py")}
    manifest_path = safe_path(delivery / "wheel-runtime-hashes.json", delivery)
    if (wheel_info.get("runtime_bytes_match_candidate") is not True
            or wheel_runtime != candidate_runtime
            or json.loads(manifest_path.read_text(encoding="utf-8")) != candidate_runtime):
        raise ValueError("Wheel modules differ from candidate or sealed wheel manifest")
    sites = [*delivery.glob("installed-venv*/Lib/site-packages"),
             *delivery.glob("installed-venv*/lib/python*/site-packages")]
    if len(sites) != 1:
        raise ValueError("Delivery must identify exactly one installed environment")
    site = safe_path(sites[0], delivery)
    for module in (site / "sqlglot").rglob("*"):
        safe_path(module, site)
    if runtime_hashes(site) != candidate_runtime:
        raise ValueError("Installed SQLGlot bytes differ from the reviewed wheel")
    install = checks["install_wheel"].get("command", [])
    if (not install or not Path(install[0]).resolve().is_relative_to(site.parent.parent.resolve())
            or Path(install[-1]).resolve() != wheel):
        raise ValueError("Install receipt does not bind this environment and wheel")
    receipt = {"report_path": str(report_path), "report_sha256": digest(report_path),
               "wheel": str(wheel), "wheel_sha256": digest(wheel),
               "installed_site": str(site), "candidate_wheel_installed_bytes_match": True,
               "delivery_overall_success": report.get("success") is True}
    return receipt, site, candidate_runtime


def suite_pairing(baseline: dict, installed: dict, baseline_root: Path | None = None) -> dict:
    """A pairing requires real nonempty, unique collection identities and binding."""
    ids = baseline.get("case_ids", [])
    candidate_ids = installed.get("case_ids", [])
    binding = baseline.get("binding", {})
    valid_baseline = (isinstance(ids, list) and bool(ids) and len(ids) == len(set(ids))
                      and baseline.get("total") == len(ids)
                      and baseline.get("collection_errors") == 0
                      and not baseline.get("parse_error") and not baseline.get("binding_error")
                      and binding.get("success") is True and binding.get("imported") is True
                      and not binding.get("escaped_modules")
                      and baseline.get("timeout") is False
                      and baseline.get("output_limit_exceeded") is False)
    if baseline_root is not None:
        groups = [binding.get("before_modules"), binding.get("modules")]
        valid_baseline = (valid_baseline and binding.get("package") == "sqlglot"
                          and Path(binding.get("project", "")).resolve() == baseline_root.resolve()
                          and Path(binding.get("source", "")).resolve() == baseline_root.resolve()
                          and all(isinstance(group, dict) and "sqlglot" in group
                                  and all(Path(path).resolve().is_relative_to(baseline_root.resolve())
                                          for path in group.values()) for group in groups))
    matched = bool(valid_baseline and len(candidate_ids) == len(set(candidate_ids))
                   and set(ids) == set(candidate_ids))
    return {"provided": True, "baseline_valid": bool(valid_baseline),
            "case_ids_match": matched, "baseline_total": len(ids),
            "installed_total": len(candidate_ids), "baseline_successful": baseline.get("successful") is True,
            "baseline_case_ids": ids, "installed_case_ids": candidate_ids}


def baseline_suites(value: dict) -> dict:
    """Accept the public driver report or an explicit two-suite baseline record."""
    suites = {}
    for name, aliases in {"generated": ("generated_baseline", "baseline_generated", "generated"),
                          "regression": ("baseline_regression", "regression")}.items():
        for key in aliases:
            item = value.get(key)
            if isinstance(item, dict) and "case_ids" in item:
                suites[name] = item
                break
    if not suites and "case_ids" in value:
        suites["generated"] = value
    if not suites:
        raise ValueError("Baseline results contain no supported public suite record")
    return suites


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("case", "candidate", "delivery", "generated-tests", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--baseline-results", type=Path)
    args = parser.parse_args()
    output = safe_path(args.output, ARTIFACT, exists=False)
    if any(output.is_relative_to(ARTIFACT / name) for name in ("public", "evaluator-only")):
        raise ValueError("Output cannot write into public or private benchmark inputs")
    # Reject overlap before creating even a report directory in any input tree.
    for supplied in (args.candidate, args.delivery, args.generated_tests):
        source = supplied.resolve()
        if output.is_relative_to(source) or source.is_relative_to(output):
            raise ValueError("Output must be separate from every input tree")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "testfix.feabench-installed-suites.v1", "success": False,
              "official_container_score": False, "model_calls": 0, "suites": {},
              "pairing": {}, "integrity": {}}
    seals = {}
    file_seals = {}
    try:
        case_path = safe_path(args.case, ARTIFACT / "public")
        case = json.loads(case_path.read_text(encoding="utf-8"))
        file_seals["case"] = (case_path, digest(case_path))
        if case.get("schema") != "testfix.feabench-pilot-case.v1":
            raise ValueError("Unsupported public case schema")
        baseline = safe_path(Path(case["baseline_root"]), ARTIFACT / "public")
        candidate = safe_path(args.candidate, ARTIFACT)
        delivery = safe_path(args.delivery, ARTIFACT)
        generated = safe_path(args.generated_tests, ARTIFACT)
        for protected in (baseline, candidate, delivery, generated):
            if output.is_relative_to(protected) or protected.is_relative_to(output):
                raise ValueError("Output must be separate from every input tree")
        if (not generated.is_dir() or not list(generated.glob("test_*.py"))
                or not (baseline / "tests").is_dir()):
            raise ValueError("Generated and original suites must be nonempty directories")
        for name, path in (("candidate", candidate), ("generated", generated), ("baseline", baseline)):
            seals[name] = (path, tree_hashes(path))
        report.update(instance_id=case["instance_id"], case_path=str(case_path),
                      case_sha256=digest(case_path), generated_seal=seals["generated"][1],
                      candidate_tree_sha256=seals["candidate"][1])
        receipt, site, expected_runtime = validate_delivery(delivery, candidate, case["instance_id"])
        report["delivery_binding"] = receipt
        for name, path in (("delivery_report", Path(receipt["report_path"])),
                           ("wheel", Path(receipt["wheel"]))):
            file_seals[name] = (path, digest(path))
        seals["installed_runtime"] = (site, expected_runtime)
        report["candidate_runtime_sha256"] = expected_runtime
        report["fixtures"] = prepare_fixtures(baseline / "tests/fixtures", site / "tests/fixtures")
        seals["installed_fixtures"] = (site / "tests/fixtures", report["fixtures"]["target_sha256"])
        config = {"source_root": ".", "package": "sqlglot"}
        for name, tests in (("generated", generated), ("regression", baseline / "tests")):
            report["suites"][name] = run_package_suite(Path(case["python"]), site, tests, output,
                                                       "installed-" + name, config)
        if args.baseline_results:
            baseline_path = safe_path(args.baseline_results, ARTIFACT)
            value = json.loads(baseline_path.read_text(encoding="utf-8"))
            file_seals["baseline_results"] = (baseline_path, digest(baseline_path))
            if "instance_id" in value and value["instance_id"] != case["instance_id"]:
                raise ValueError("Baseline results refer to another benchmark instance")
            generated_hashes = value.get("generated_tests", {}).get("sha256")
            if generated_hashes is not None and generated_hashes != seals["generated"][1]:
                raise ValueError("Baseline generated tests do not match the frozen suite")
            report["baseline_results"] = {"path": str(baseline_path), "sha256": digest(baseline_path)}
            for name, suite in baseline_suites(value).items():
                report["pairing"][name] = suite_pairing(suite, report["suites"][name], baseline)
        else:
            report["pairing"] = {name: {"provided": False, "case_ids_match": None}
                                 for name in report["suites"]}
        report["success"] = (all(suite["successful"] for suite in report["suites"].values())
                             and all(not pair.get("provided") or pair.get("case_ids_match")
                                     for pair in report["pairing"].values()))
    except Exception as error:
        report["error"] = type(error).__name__ + ": " + str(error)
    finally:
        for name, (path, before) in file_seals.items():
            report["integrity"][name + "_unchanged"] = path.is_file() and digest(path) == before
        for name, (path, before) in seals.items():
            try:
                after = runtime_hashes(path) if name == "installed_runtime" else tree_hashes(path)
                report["integrity"][name + "_unchanged"] = after == before
            except Exception as error:
                report["integrity"][name + "_unchanged"] = False
                report["integrity"][name + "_error"] = type(error).__name__ + ": " + str(error)
        report["success"] = report["success"] and bool(seals) and all(
            value is True for key, value in report["integrity"].items() if key.endswith("_unchanged"))
        (output / "installed-suites-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"success": report["success"], "report": str(output / "installed-suites-report.json")},
                     ensure_ascii=False))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
