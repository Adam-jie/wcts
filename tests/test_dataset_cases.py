"""Offline full-repository preparation boundaries; no model or repository code runs."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from testfix.dataset_cases import PreparationLimits, prepare_dataset_case


def write(path: Path, value: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="")
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def fixture(tmp_path):
    source = tmp_path / "upstream"
    app = write(source / "src/app.py", "def temperature():\n    return 25\n")
    write(source / "src/optional.py", "# PRIVATE_REFERENCE_IMPLEMENTATION\nVALUE = 2\n")
    write(source / "tests/test_old.py", "# Frozen upstream regression\n")
    write(source / "requirements.lock", "# Frozen dependencies\n")
    write(source / "README.md", "Public upstream documentation.\n")
    write(source / ".env", "SECRET_LOCAL_VALUE=NEVER_COPY_THIS\n")
    write(source / ".env.example", "SERVICE_URL=http://example.invalid\n")
    write(source / "node_modules/pkg/index.js", "PRIVATE_DEPENDENCY_CACHE\n")
    write(source / ".git/config", "PRIVATE_GIT_CONFIG\n")
    write(source / "evaluator-only/gold.patch", "PRIVATE_GRADER_ANSWER\n")
    spec = {
        "schema": "testfix.dataset-case.v1", "id": "web-temperature-delete-01",
        "title": "Restore a temperature API", "track": "controlled_defect_repair", "domain": "web",
        "source": {"repository": "https://github.com/example/full-application", "commit": "a" * 40, "license": "MIT"},
        "requirements": [{"id": "TEMP", "description": "Read the temperature", "expected": "Return 25"}],
        "allowed_changes": ["src/app.py", "src/optional.py"],
        "mutations": [{"kind": "replace_text", "path": "src/app.py", "expected_sha256": sha(app),
                       "old": "return 25", "new": "return -99"},
                      {"kind": "remove_file", "path": "src/optional.py", "expected_sha256": sha(source / "src/optional.py")}],
    }
    return source, spec, tmp_path / "prepared"


def test_three_copies_are_isolated_and_agent_input_has_no_injection_or_reference(fixture):
    source, spec, output = fixture
    original = (source / "src/app.py").read_bytes()
    result = prepare_dataset_case(spec, source, output)
    pristine = output / "evaluator-only/pristine"
    agent = output / "agent-input/project"
    candidate = output / "candidate"
    assert (source / "src/app.py").read_bytes() == (pristine / "src/app.py").read_bytes() == original
    assert (agent / "src/app.py").read_bytes() == (candidate / "src/app.py").read_bytes()
    assert "return -99" in (agent / "src/app.py").read_text(encoding="utf-8")
    assert not (agent / "src/optional.py").exists() and not (candidate / "src/optional.py").exists()
    assert (pristine / "src/optional.py").exists() and (source / "src/optional.py").exists()
    assert sha(agent / "tests/test_old.py") == sha(source / "tests/test_old.py")
    public = json.loads((output / "agent-input/task.json").read_text(encoding="utf-8"))
    assert set(public) == {"schema", "id", "title", "track", "domain", "source", "requirements",
                           "allowed_changes", "project_sha256", "status", "preparation_only"}
    public_bytes = b"".join(path.read_bytes() for path in (output / "agent-input").rglob("*") if path.is_file())
    assert not any(value.encode() in public_bytes for value in ["PRIVATE_REFERENCE_IMPLEMENTATION", "PRIVATE_GRADER_ANSWER",
                   "NEVER_COPY_THIS", "PRIVATE_GIT_CONFIG", "PRIVATE_DEPENDENCY_CACHE", "expected_sha256", "pristine_root"])
    assert (agent / ".env.example").exists()
    for root in (pristine, agent, candidate):
        assert not (root / ".env").exists() and not (root / "evaluator-only").exists()
    private = json.loads((output / "evaluator-only/preparation.json").read_text(encoding="utf-8"))
    assert private["mutations"][0]["old"] == "return 25"
    assert private["pristine_health"] == "unverified"
    assert result["status"] == "preflight_pending" and result["preparation_only"] is True
    assert result["snapshots"]["agent_input"]["sha256"] == result["snapshots"]["initial_candidate"]["sha256"]
    assert result["source"]["revision_verification"] == "declared_not_checked"
    (candidate / "src/app.py").write_text("# candidate-only change\n", encoding="utf-8")
    assert (source / "src/app.py").read_bytes() == (pristine / "src/app.py").read_bytes() == original
    assert "return -99" in (agent / "src/app.py").read_text(encoding="utf-8")


def test_natural_language_existing_project_has_no_injection_or_claimed_execution(fixture):
    source, spec, output = fixture
    spec.update(track="requirement_implementation", domain="mpu")
    spec.pop("mutations")
    spec["allowed_changes"].append("src/new_feature.py")
    result = prepare_dataset_case(spec, source, output)
    assert result["snapshots"]["pristine"]["sha256"] == result["snapshots"]["agent_input"]["sha256"]
    assert result["preflight"] == {"status": "pending", "executed": False}
    assert not (source / "src/new_feature.py").exists()
    assert (output / "agent-input/project/src/optional.py").exists()
    bad = deepcopy(spec)
    bad["mutations"] = [{"kind": "remove_file", "path": "src/app.py", "expected_sha256": sha(source / "src/app.py")}]
    with pytest.raises(ValueError, match="cannot include mutations"):
        prepare_dataset_case(bad, source, output.parent / "bad-natural")


@pytest.mark.parametrize("path", ["tests/test_old.py", "src/test_service.py", "package-lock.json", "pnpm-lock.yaml",
                                  "requirements.lock", "conftest.py", "src/widget.spec.ts", "acceptance_tests/test_api.py",
                                  "testsuites/test_io.c", "west.yml", ".gitmodules"])
def test_frozen_tests_dependencies_and_acceptance_cannot_be_patch_targets(fixture, path):
    source, spec, output = fixture
    write(source / path, "FROZEN\n")
    spec["allowed_changes"] = [path]
    spec["mutations"] = [{"kind": "remove_file", "path": path, "expected_sha256": sha(source / path)}]
    with pytest.raises(ValueError, match="protected"):
        prepare_dataset_case(spec, source, output)
    assert not output.exists()


def test_injection_requires_hash_and_exact_unique_text_before_creating_output(fixture):
    source, spec, output = fixture
    bad = deepcopy(spec)
    bad["mutations"][0]["expected_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        prepare_dataset_case(bad, source, output)
    assert not output.exists()
    app = source / "src/app.py"
    write(app, "VALUE = 25\nVALUE = 25\n")
    spec["mutations"] = [{"kind": "replace_text", "path": "src/app.py", "expected_sha256": sha(app), "old": "VALUE = 25", "new": "VALUE = 0"}]
    with pytest.raises(ValueError, match="exactly once"):
        prepare_dataset_case(spec, source, output)
    assert not output.exists() and app.read_text(encoding="utf-8").count("VALUE = 25") == 2


@pytest.mark.parametrize("path", ["../escape.py", "src/../../escape.py", "C:/escape.py", "C:escape.py", "src\\app.py", "src//app.py", "src/app.py:stream"])
def test_nonportable_or_escaping_paths_are_rejected_before_writes(fixture, path):
    source, spec, output = fixture
    spec["allowed_changes"] = [path]
    with pytest.raises(ValueError, match="path"):
        prepare_dataset_case(spec, source, output)
    assert not output.exists()


def test_unknown_fields_cannot_sneak_evaluator_data_into_public_contract(fixture):
    source, spec, output = fixture
    spec["requirements"][0]["gold_patch"] = "PRIVATE_ANSWER"
    with pytest.raises(ValueError, match="fields"):
        prepare_dataset_case(spec, source, output)
    assert not output.exists()


def test_templates_are_kept_only_when_credentials_are_placeholders_and_exclusions_require_review(fixture):
    source, spec, output = fixture
    write(source / ".env.example", "OPENAI_API_KEY=your-api-key\nDB_PASSWORD=${DB_PASSWORD}\n")
    write(source / "service/.env.example", "API_KEY=REAL_PRIVATE_VALUE_DO_NOT_COPY\n")
    result = prepare_dataset_case(spec, source, output)
    assert (output / "agent-input/project/.env.example").exists()
    assert not (output / "agent-input/project/service/.env.example").exists()
    private = json.loads((output / "evaluator-only/preparation.json").read_text(encoding="utf-8"))
    assert "service/.env.example" in private["excluded_paths"] and private["excluded_paths_review_required"] is True
    assert "review_excluded_source_paths" in result["review_required"]


def test_dynamic_route_names_are_literal_paths_and_case_aliases_cannot_expand_patch_scope(fixture):
    source, spec, output = fixture
    route = write(source / "src/routes/[slug].tsx", "export const page = 'public';\n")
    spec["allowed_changes"].append("src/routes/[slug].tsx")
    prepare_dataset_case(spec, source, output)
    assert sha(output / "agent-input/project/src/routes/[slug].tsx") == sha(route)
    spec["allowed_changes"][-1] = "SRC/routes/new.tsx"
    with pytest.raises(ValueError, match="spelling"):
        prepare_dataset_case(spec, source, output.parent / "case-alias")


def test_embedded_env_and_emulator_target_modules_are_preserved_in_all_snapshots(fixture):
    source, spec, output = fixture
    write(source / "env/common.c", "int env_load(void) { return 0; }\n")
    write(source / "target/arm/cpu.c", "int cpu_run(void) { return 0; }\n")
    prepare_dataset_case(spec, source, output)
    for tree in ("evaluator-only/pristine", "agent-input/project", "candidate"):
        assert sha(output / tree / "env/common.c") == sha(source / "env/common.c")
        assert sha(output / tree / "target/arm/cpu.c") == sha(source / "target/arm/cpu.c")


def test_output_never_overwrites_or_nests_in_source_and_limits_are_enforced(fixture):
    source, spec, output = fixture
    with pytest.raises(ValueError, match="overlap"):
        prepare_dataset_case(spec, source, source / "new-case")
    assert not (source / "new-case").exists()
    write(output / "keep.txt", "USER_OWNED\n")
    with pytest.raises(ValueError, match="exists"):
        prepare_dataset_case(spec, source, output)
    assert (output / "keep.txt").read_text(encoding="utf-8") == "USER_OWNED\n"
    with pytest.raises(ValueError, match="limit"):
        prepare_dataset_case(spec, source, output.parent / "too-large", limits=PreparationLimits(max_files=1))
    assert not (output.parent / "too-large").exists()


def test_hardlinked_files_are_rejected_without_following_or_copying(fixture):
    source, spec, output = fixture
    os.link(source / "src/app.py", source / "src/alias.py")
    with pytest.raises(ValueError, match="link"):
        prepare_dataset_case(spec, source, output)
    assert not output.exists()


def test_cli_prepares_without_running_project_and_preserves_source(fixture):
    source, spec, output = fixture
    write(source / "never_execute.py", "raise RuntimeError('REPOSITORY_CODE_MUST_NOT_RUN')\n")
    spec_path = write(output.parent / "case.json", json.dumps(spec))
    script = Path(__file__).resolve().parents[1] / "scripts/prepare_dataset_case.py"
    result = subprocess.run([sys.executable, "-X", "utf8", str(script), "--spec", str(spec_path),
                             "--source", str(source), "--output", str(output)], capture_output=True, text=True,
                            encoding="utf-8", timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "preflight_pending"
    assert (source / "never_execute.py").read_text(encoding="utf-8").startswith("raise RuntimeError")
