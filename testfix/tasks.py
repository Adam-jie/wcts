"""Trusted task snapshots for single-module repository features and benchmarks."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .config import Settings
from .datasets import Task, acceptance_code, load_tasks
from .policy import validate_acceptance_code, validate_candidate


def repository_store(settings: Settings) -> Path:
    return settings.root / "data" / "repositories"


def import_repository(manifest_path: Path, settings: Settings) -> str:
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    task_id = manifest.get("id", "")
    if not re.fullmatch(r"Repo/[A-Za-z0-9_-]{1,64}", task_id):
        raise ValueError("Repository task id must be Repo/<letters-digits-dash-underscore>")
    source_path = (manifest_path.parent / manifest["source_file"]).resolve()
    acceptance_path = (manifest_path.parent / manifest["acceptance_file"]).resolve()
    if source_path == acceptance_path or source_path.suffix != ".py" or acceptance_path.suffix != ".py":
        raise ValueError("Separate Python source and independent acceptance files are required")
    code = source_path.read_text(encoding="utf-8")
    hidden = acceptance_path.read_text(encoding="utf-8")
    validate_acceptance_code(hidden)
    requirements = manifest["requirements"]
    if not isinstance(requirements, str) or not requirements.strip() or len(requirements) > 30_000:
        raise ValueError("Repository manifest requires bounded text requirements")
    entry = manifest["entry_point"]
    validate_candidate(code, code, entry)
    from .orchestrator import validated_cases
    cases = validated_cases({"cases": manifest["regression_cases"]})
    public = {"id": task_id, "title": manifest.get("title", entry), "requirements": requirements,
              "entry_point": entry, "initial_code": code, "regression_cases": cases,
              "kind": "repository", "source": str(source_path)}
    store = repository_store(settings) / task_id.split("/")[1]
    store.mkdir(parents=True, exist_ok=True)
    (store / "task.json").write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
    (store / "acceptance.py").write_text(hidden, encoding="utf-8")
    (store / "integrity.json").write_text(json.dumps({"task_sha256": hashlib.sha256((store / "task.json").read_bytes()).hexdigest(),
                                                     "acceptance_sha256": hashlib.sha256((store / "acceptance.py").read_bytes()).hexdigest()}), encoding="utf-8")
    return task_id


def load_repository_tasks(settings: Settings) -> list[Task]:
    tasks = []
    for path in sorted(repository_store(settings).glob("*/task.json")):
        verify_repository(path.parent)
        tasks.append(Task(**json.loads(path.read_text(encoding="utf-8"))))
    return tasks


def verify_repository(directory: Path) -> None:
    manifest = json.loads((directory / "integrity.json").read_text(encoding="utf-8"))
    for filename, key in [("task.json", "task_sha256"), ("acceptance.py", "acceptance_sha256")]:
        if hashlib.sha256((directory / filename).read_bytes()).hexdigest() != manifest[key]:
            raise ValueError("Repository task snapshot integrity check failed")


def catalog(settings: Settings) -> list[Task]:
    benchmark = load_tasks(settings.dataset) if (settings.dataset / "manifest.json").exists() else []
    return benchmark + load_repository_tasks(settings)


def select_tasks(settings: Settings, ids: list[str]) -> list[Task]:
    benchmark_ids = [task_id for task_id in ids if task_id.startswith("HumanEval/")]
    benchmark = load_tasks(settings.dataset, benchmark_ids) if benchmark_ids else []
    available = {task.id: task for task in benchmark + load_repository_tasks(settings)}
    if any(task_id not in available for task_id in ids):
        raise ValueError("Unknown task id; import repository manifest or HumanEval dataset first")
    return [available[task_id] for task_id in ids]


def hidden_tests(settings: Settings, task: Task) -> str:
    if task.kind == "humaneval":
        return acceptance_code(settings.dataset, task.id)
    directory = repository_store(settings) / task.id.split("/")[1]
    verify_repository(directory)
    return (directory / "acceptance.py").read_text(encoding="utf-8")

