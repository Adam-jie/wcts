"""Freeze a metadata-only, deterministic FEA-Bench Lite pilot.

This collector downloads text/JSON only. It does not install packages, execute
upstream code, retrieve PR diffs, or invoke a model. Gold and test patches belong
in a separate evaluator-only directory and are deliberately outside its scope.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request


BENCHMARK_REVISION = "fb3c11274796f6057bf447410540fcf2fa1d90b1"
DATASET_REVISION = "55ee2f78126a3ecdeac6f595fa1ba6ae5c600bad"
SEED_PREFIX = "testfix-fea-pilot-v1:"
DATASET = "microsoft/FEA-Bench"
SOURCE_FILES = (
    "README.md", "LICENSE", "instances_lite.json", "feabench/get_dataset.py",
    "feabench/collect/utils.py", "feabench/collect/make_instructions.py",
    "swe-bench.diff",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(url, headers={"User-Agent": "TestFix-FEA-Pilot/1"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read(), dict(response.headers)


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def select_ids(ids: list[str], count: int = 10) -> list[dict[str, object]]:
    """Order all Lite IDs by a declared hash; take the first from each repo."""
    if len(ids) != len(set(ids)):
        raise ValueError("Lite IDs contain duplicates")
    ordered = sorted(ids, key=lambda item: sha256((SEED_PREFIX + item).encode("utf-8")))
    seen: set[str] = set()
    selected = []
    for instance_id in ordered:
        repo_id, pull_number = instance_id.rsplit("-", 1)
        repo = repo_id.replace("__", "/")
        if repo in seen:
            continue
        seen.add(repo)
        selected.append({
            "rank": len(selected) + 1,
            "instance_id": instance_id,
            "repo": repo,
            "pull_number": int(pull_number),
            "selection_sha256": sha256((SEED_PREFIX + instance_id).encode("utf-8")),
            "pull_request_url": f"https://github.com/{repo}/pull/{pull_number}",
        })
        if len(selected) == count:
            break
    if len(selected) != count:
        raise ValueError("Insufficient distinct repositories")
    return selected


def collect(output: Path) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    sources = []
    for name in SOURCE_FILES:
        url = f"https://raw.githubusercontent.com/microsoft/FEA-Bench/{BENCHMARK_REVISION}/{name}"
        content, _ = fetch(url)
        path = output / "upstream" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        sources.append({"url": url, "path": path.relative_to(output).as_posix(),
                        "sha256": sha256(content), "bytes": len(content)})

    ids = json.loads((output / "upstream/instances_lite.json").read_bytes())
    if len(ids) != 200:
        raise ValueError(f"Expected 200 Lite IDs, found {len(ids)}")
    selected = select_ids(ids)
    selection = {
        "schema_version": 1,
        "frozen_before_model_execution": True,
        "selection_rule": "Sort the 200 official Lite instance IDs ascending by SHA256(seed_prefix + instance_id); scan in order and retain the first ID per distinct repo until 10 are selected.",
        "seed_prefix": SEED_PREFIX,
        "environment_filter": "none",
        "replacement_policy": "No replacement based on environment, model outcome, or test success. Environment failures remain selected and are reported separately.",
        "official_repository_revision": BENCHMARK_REVISION,
        "official_dataset_revision": DATASET_REVISION,
        "lite_ids_sha256": sources[2]["sha256"],
        "selected": selected,
    }
    frozen_path = output / "pilot-selection.json"
    if frozen_path.exists() and json.loads(frozen_path.read_bytes()) != selection:
        raise ValueError("Existing frozen selection differs; refuse to overwrite it")
    save_json(frozen_path, selection)

    dataset_url = f"https://huggingface.co/api/datasets/{DATASET}/revision/{DATASET_REVISION}"
    dataset_info, _ = fetch(dataset_url)
    if json.loads(dataset_info).get("sha") != DATASET_REVISION:
        raise ValueError("Hugging Face revision mismatch")
    (output / "dataset-revision.json").write_bytes(dataset_info)

    def page(offset: int) -> tuple[list[dict], dict]:
        query = urllib.parse.urlencode({"dataset": DATASET, "config": "default",
                                       "split": "test", "offset": offset, "length": 100})
        url = f"https://datasets-server.huggingface.co/rows?{query}"
        content, headers = fetch(url)
        revision = {key.lower(): value for key, value in headers.items()}.get("x-revision")
        if revision != DATASET_REVISION:
            raise ValueError(f"Viewer revision mismatch at offset {offset}: {revision}")
        data = json.loads(content)
        if data["num_rows_total"] != 1401:
            raise ValueError("Unexpected full dataset size")
        path = output / "metadata-pages" / f"rows-{offset:04d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return [row["row"] for row in data["rows"]], {
            "url": url, "path": path.relative_to(output).as_posix(),
            "x_revision": revision, "sha256": sha256(content), "bytes": len(content),
        }

    with ThreadPoolExecutor(max_workers=3) as pool:
        pages = list(pool.map(page, range(0, 1401, 100)))
    rows = [row for values, _ in pages for row in values]
    by_id = {row["instance_id"]: row for row in rows}
    if len(rows) != 1401 or len(by_id) != 1401:
        raise ValueError("Full metadata row count or uniqueness mismatch")
    metadata = []
    for chosen in selected:
        item = by_id[chosen["instance_id"]]
        if (item["repo"], item["pull_number"]) != (chosen["repo"], chosen["pull_number"]):
            raise ValueError("Selected ID disagrees with official metadata")
        if {"patch", "test_patch", "problem_statement"} & item.keys():
            raise ValueError("Unexpected non-metadata fields in published dataset")
        metadata.append({**chosen, **item})
    save_json(output / "pilot-metadata.json", metadata)
    save_json(output / "first-task-metadata.json", {
        **metadata[0], "metadata_source_revision": DATASET_REVISION,
        "source_repository_revision": BENCHMARK_REVISION,
    })
    manifest = {
        "schema_version": 1,
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET, "split": "test", "full_metadata_count": len(rows),
        "lite_count": len(ids), "pilot_count": len(metadata),
        "official_repository_revision": BENCHMARK_REVISION,
        "official_dataset_revision": DATASET_REVISION,
        "model_invocations": 0, "third_party_code_executed": False,
        "gold_patch_collected": False, "test_patch_collected": False,
        "selection_file": "pilot-selection.json",
        "selection_sha256": sha256(frozen_path.read_bytes()),
        "pilot_metadata_sha256": sha256((output / "pilot-metadata.json").read_bytes()),
        "sources": sources + [receipt for _, receipt in pages],
        "reconstruction": {
            "official_entrypoint": "python -m feabench.get_dataset",
            "needs": ["public PR request text", "PR diff split using official is_test_file policy", "new component definitions reconstructed with official AST comparison", "base repository at metadata base_commit"],
            "evaluator_swebench_revision": "a0536ee6f9fd5ff88acf17a36a384bf3da3d93d6",
            "harness_patch": "upstream/swe-bench.diff",
            "official_execution": "Docker-based swebench.harness.run_evaluation; native smoke tests must be reported separately, not as official benchmark scores.",
            "isolation": "Gold source patches and test patches must remain evaluator-only and outside the model worktree/prompt.",
        },
    }
    save_json(output / "collection-manifest.json", manifest)
    return {key: manifest[key] for key in (
        "dataset", "full_metadata_count", "lite_count", "pilot_count", "selection_sha256",
        "official_repository_revision", "official_dataset_revision")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path("artifacts/fea-pilot-20261015/collection"))
    args = parser.parse_args()
    print(json.dumps(collect(args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
