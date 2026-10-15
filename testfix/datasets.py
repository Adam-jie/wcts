"""Pinned upstream dataset; reference solutions never enter task/agent objects."""
from __future__ import annotations

import ast
import gzip
import hashlib
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HUMANEVAL_COMMIT = "6d43fb980f9fee3c892a914eda09951f772ad10d"
HUMANEVAL_SHA256 = "b796127e635a67f93fb35c04f4cb03cf06f38c8072ee7cee8833d7bee06979ef"
SOURCE = f"https://raw.githubusercontent.com/openai/human-eval/{HUMANEVAL_COMMIT}"
DEMO_IDS = ("HumanEval/0", "HumanEval/1", "HumanEval/2", "HumanEval/3", "HumanEval/4")


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    requirements: str
    entry_point: str
    initial_code: str
    regression_cases: list[dict]
    kind: str = "humaneval"
    source: str = "https://github.com/openai/human-eval"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_humaneval(destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    for remote, local in [("data/HumanEval.jsonl.gz", "HumanEval.jsonl.gz"), ("LICENSE", "LICENSE")]:
        url = f"{SOURCE}/{remote}"
        with urllib.request.urlopen(url, timeout=45) as response:
            data = response.read(8_000_000)
        target = destination / local
        if local == "HumanEval.jsonl.gz" and hashlib.sha256(data).hexdigest() != HUMANEVAL_SHA256:
            raise ValueError("Pinned HumanEval download hash mismatch")
        target.write_bytes(data)
    rows = read_rows(destination)
    manifest = {"dataset": "HumanEval", "commit": HUMANEVAL_COMMIT, "source": SOURCE,
                "sha256": sha256(destination / "HumanEval.jsonl.gz"), "records": len(rows), "license": "MIT"}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def read_rows(destination: Path) -> dict[str, dict]:
    with gzip.open(destination / "HumanEval.jsonl.gz", "rt", encoding="utf-8") as stream:
        return {row["task_id"]: row for line in stream if (row := json.loads(line))}


def verify_dataset(destination: Path) -> None:
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    if manifest["sha256"] != sha256(destination / "HumanEval.jsonl.gz"):
        raise ValueError("Dataset integrity check failed: re-import the pinned upstream dataset")


def stub(prompt: str, entry_point: str) -> str:
    tree = ast.parse(prompt)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == entry_point:
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                node.body = [node.body[0], ast.Raise(ast.Call(ast.Name("NotImplementedError", ast.Load()), [ast.Constant("missing feature")], []))]
            else:
                node.body = [ast.Raise(ast.Call(ast.Name("NotImplementedError", ast.Load()), [ast.Constant("missing feature")], []))]
    return ast.unparse(ast.fix_missing_locations(tree)) + "\n"


# Independently authored smoke cases, not extracted from official acceptance tests.
REGRESSION = {
    "HumanEval/0": [{"args": [[1.0, 3.0], 0.5], "expected": False}],
    "HumanEval/1": [{"args": ["()"], "expected": ["()"]}],
    "HumanEval/2": [{"args": [3.0], "expected": 0.0}],
    "HumanEval/3": [{"args": [[1, 2]], "expected": False}],
    "HumanEval/4": [{"args": [[2.0, 2.0]], "expected": 0.0}],
    "HumanEval/10": [{"args": [""], "expected": ""}, {"args": ["a"], "expected": "a"}, {"args": ["aba"], "expected": "aba"}],
    "HumanEval/13": [{"args": [3, 5], "expected": 1}, {"args": [7, 11], "expected": 1}],
    "HumanEval/20": [{"args": [[3.0, 1.0]], "expected": [1.0, 3.0]}],
}

# Curated missing-behavior injection, authored from public specifications. These
# deliberately incomplete implementations preserve the above smoke behavior.
PARTIAL = {
    "HumanEval/0": "    return len(numbers) > 1 and abs(numbers[0] - numbers[1]) < threshold\n",
    "HumanEval/1": "    return [paren_string.replace(' ', '')]\n",
    "HumanEval/2": "    return 0.0\n",
    "HumanEval/3": "    return sum(operations) < 0\n",
    "HumanEval/4": "    return 0.0\n",
    "HumanEval/10": "    if is_palindrome(string):\n        return string\n    return string + string[::-1]\n",
    "HumanEval/13": "    return 1\n",
    "HumanEval/20": "    return (min(numbers[0], numbers[1]), max(numbers[0], numbers[1]))\n",
}


def load_tasks(destination: Path, ids: list[str] | None = None) -> list[Task]:
    verify_dataset(destination)
    rows = read_rows(destination)
    wanted = list(DEMO_IDS) if ids is None else ids
    tasks = []
    for task_id in wanted:
        row = rows.get(task_id)
        if row is None:
            raise ValueError(f"Unknown task: {task_id}")
        initial = row["prompt"] + PARTIAL[task_id] if task_id in PARTIAL else stub(row["prompt"], row["entry_point"])
        tasks.append(Task(task_id, row["entry_point"], row["prompt"], row["entry_point"],
                          initial, REGRESSION.get(task_id, [])))
    return tasks


def acceptance_code(destination: Path, task_id: str) -> str:
    """Only the evaluator calls this, never a model provider."""
    verify_dataset(destination)
    return read_rows(destination)[task_id]["test"]

