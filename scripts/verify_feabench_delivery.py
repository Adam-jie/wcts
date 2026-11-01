"""Build a candidate copy and exercise the installed SQLGlot wheel, without LLM calls."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid
import zipfile


ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = ROOT / "artifacts/fea-pilot-20261015"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from testfix.package_tools import _run_bounded


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_hashes(root):
    return {p.relative_to(root).as_posix(): digest(p)
            for p in sorted((root / "sqlglot").rglob("*.py"))}


def tree_hashes(root):
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in {".git", "__pycache__", ".pytest_cache"} for part in relative.parts) or path.suffix == ".pyc":
            continue
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Candidate contains a symlink or junction")
        if path.is_file():
            result[relative.as_posix()] = digest(path)
    return result


def call(argv, cwd, output, name, timeout=180):
    result = _run_bounded([str(v) for v in argv], cwd=cwd, timeout_s=timeout)
    (output / (name + ".stdout.txt")).write_text(result["stdout"], encoding="utf-8")
    (output / (name + ".stderr.txt")).write_text(result["stderr"], encoding="utf-8")
    return {"command": [str(v) for v in argv], "cwd": str(cwd), "exit_code": result["exit_code"],
            "timeout": result["timeout"], "duration_s": result["duration_s"],
            "output_limit_exceeded": result["output_limit_exceeded"],
            "stdout_path": str(output / (name + ".stdout.txt")),
            "stderr_path": str(output / (name + ".stderr.txt"))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    case = json.loads(args.case.read_text(encoding="utf-8"))
    candidate, output = args.candidate.resolve(), args.output.resolve()
    if not candidate.is_relative_to(ROOT) or not output.is_relative_to(ARTIFACT):
        raise ValueError("Delivery paths must remain within the experiment workspace")
    output.mkdir(parents=True, exist_ok=False)
    source_hashes = runtime_hashes(candidate)
    candidate_tree = tree_hashes(candidate)
    smoke_path = ARTIFACT / "delivery-smoke-contract.json"
    smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
    report = {"schema": "testfix.feabench-native-delivery.v1", "success": False,
              "instance_id": case["instance_id"], "candidate_runtime_sha256": source_hashes,
              "candidate_tree_sha256": candidate_tree,
              "smoke_contract_sha256": digest(smoke_path), "official_container_score": False,
              "checks": {}}
    try:
        python = str(case["python"])
        build_root = output / "build-source"
        shutil.copytree(candidate, build_root, ignore=shutil.ignore_patterns("__pycache__", ".git", "*.pyc"))
        # Compile a separate tree so the reviewed candidate remains byte-for-byte intact.
        report["checks"]["compile"] = call([python, "-I", "-B", "-X", "utf8", "-m", "compileall", "-q", "sqlglot"],
                                              build_root, output, "compile")
        wheels = output / "wheels"
        wheels.mkdir()
        report["checks"]["build_wheel"] = call([python, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                                                   "--wheel-dir", str(wheels), "."], build_root, output, "build-wheel")
        wheel_files = list(wheels.glob("*.whl"))
        if report["checks"]["build_wheel"]["exit_code"] != 0 or len(wheel_files) != 1:
            raise RuntimeError("Candidate wheel build did not produce exactly one wheel")
        wheel = wheel_files[0]
        report["wheel"] = {"path": str(wheel), "sha256": digest(wheel)}
        with zipfile.ZipFile(wheel) as archive:
            wheel_runtime = {name: hashlib.sha256(archive.read(name)).hexdigest()
                             for name in archive.namelist() if name.startswith("sqlglot/") and name.endswith(".py")}
        report["wheel"]["runtime_bytes_match_candidate"] = wheel_runtime == source_hashes
        if wheel_runtime != source_hashes:
            raise RuntimeError("Wheel runtime modules do not match the reviewed candidate bytes")
        runtime_manifest = output / "wheel-runtime-hashes.json"
        runtime_manifest.write_text(json.dumps(wheel_runtime, indent=2), encoding="utf-8")
        installed = output / ("installed-venv-" + uuid.uuid4().hex[:8])
        report["checks"]["create_venv"] = call([python, "-m", "venv", str(installed)], output, output, "create-venv")
        installed_python = installed / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        if report["checks"]["create_venv"]["exit_code"] != 0:
            raise RuntimeError("Installed-wheel interpreter could not be prepared")
        report["checks"]["install_wheel"] = call([str(installed_python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
                                                     output, output, "install-wheel")
        probe = output / "installed-probe.py"
        probe.write_text('''import hashlib, json, pathlib, sys
import sqlglot
from sqlglot import select
origin = pathlib.Path(sqlglot.__file__).resolve()
prefix = pathlib.Path(sys.prefix).resolve()
binding = origin.is_relative_to(prefix) and "site-packages" in origin.parts
expected_modules = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
cases = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["cases"]
results = []
for item in cases:
    try:
        query = select("a").from_("tbl")
        if item["name"] == "new_sort_by": query = query.sort_by("a")
        elif item["name"] == "new_cluster_by": query = query.cluster_by("a")
        elif item["name"] == "preserve_order_by": query = query.order_by("a DESC")
        else: raise ValueError("Unregistered smoke case")
        value = query.sql()
        results.append({"name": item["name"], "expected": item["expected_sql"], "actual": value, "passed": value == item["expected_sql"]})
    except Exception as error:
        results.append({"name": item["name"], "passed": False, "error": type(error).__name__ + ": " + str(error)})
modules = {}
for name, module in sorted(sys.modules.items()):
    if name != "sqlglot" and not name.startswith("sqlglot."): continue
    raw_path = getattr(module, "__file__", None)
    if not raw_path:
        binding = False
        modules[name] = {"matched": False, "reason": "missing module file"}
        continue
    path = pathlib.Path(raw_path).resolve()
    location_ok = path.is_relative_to(prefix) and "site-packages" in path.parts
    key = "/".join(path.parts[path.parts.index("site-packages") + 1:]) if location_ok else ""
    checksum = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    matched = location_ok and key in expected_modules and checksum == expected_modules[key]
    binding = binding and matched
    modules[name] = {"path": str(path), "sha256": checksum, "matched": matched}
report = {"source_binding": binding, "origin": str(origin), "prefix": str(prefix), "modules": modules, "cases": results}
print(json.dumps(report, ensure_ascii=False))
sys.exit(0 if binding and all(item["passed"] for item in results) else 1)
''', encoding="utf-8")
        report["checks"]["installed_api"] = call([str(installed_python), "-I", "-B", "-X", "utf8", str(probe), str(smoke_path), str(runtime_manifest)],
                                                     output, output, "installed-api")
        raw = json.loads(Path(report["checks"]["installed_api"]["stdout_path"]).read_text(encoding="utf-8"))
        report["installed_api"] = raw
        report["success"] = (all(check["exit_code"] == 0 and not check["timeout"] and not check["output_limit_exceeded"] for check in report["checks"].values())
                             and raw["source_binding"] and all(item["passed"] for item in raw["cases"]))
    except Exception as error:
        report["error"] = type(error).__name__ + ": " + str(error)
    finally:
        report["candidate_unchanged"] = tree_hashes(candidate) == candidate_tree
        report["success"] = report["success"] and report["candidate_unchanged"]
        (output / "delivery-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"success": report["success"], "report": str(output / "delivery-report.json")}, ensure_ascii=False))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
