"""Registration must preserve v1 seals while admitting a sealed v2 supplement.

All fixtures are local metadata and tiny source files. No interpreter is started,
no dependency is installed, and no model or upstream network call is required.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import register_richcli
from testfix.project_tools import files_under


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, *args: str) -> dict:
    return {"name": name, "args": list(args), "expected_exit": 0,
            "stdout_contains": [], "stdout_not_contains": []}


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path
    directory = root / "experiments/rich-cli"
    for name, text in {
        "baseline/src/rich_cli/__init__.py": "",
        "baseline/src/rich_cli/__main__.py": "def run():\n    return 1\n",
        "baseline/tests/test_main.py": "def test_original():\n    assert True\n",
        "acceptance_tests/test_acceptance.py": "def test_acceptance():\n    assert True\n",
        "regression_tests/test_original_behavior.py": "def test_old_behavior():\n    assert True\n",
    }.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    base_checks = [check("base-version", "--version")]
    write_json(directory / "delivery-checks.json", base_checks)
    source = {"repository": "https://github.com/example/rich-cli", "commit": "a" * 40,
              "license": "MIT", "files": files_under(directory / "baseline")}
    write_json(directory / "source.json", source)
    write_json(directory / "requirements.json", {
        "requirements": [{"id": "R1", "description": "Preserve literal CSV data."}],
        "clarifications": ["Original public clarification"],
        "allowed_changes": ["src/rich_cli/__main__.py"],
    })
    write_json(directory / "pre-model-seal.json", {
        "source": source["files"],
        "acceptance": files_under(directory / "acceptance_tests"),
        "behavior_regression": files_under(directory / "regression_tests"),
        "delivery_checks_sha256": digest(directory / "delivery-checks.json"),
    })
    python = root / ".venv-richcli/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"stub interpreter: registration must never execute me")
    registry = root / "data/projects/registered/rich-cli.json"
    monkeypatch.setattr(register_richcli, "ROOT", root)

    def add_v2():
        regression = directory / "behavior_regression_v2"
        regression.mkdir()
        for path in (directory / "regression_tests").glob("*.py"):
            (regression / path.name).write_bytes(path.read_bytes())
        (regression / "test_csv_head_tail_compatibility.py").write_text(
            "def test_head_precedes_tail():\n    assert True\n", encoding="utf-8")
        additional_checks = [check("csv-head-tail-compatibility", "--csv", "--head", "1", "--tail", "1", "-")]
        write_json(directory / "delivery-checks-v2.json", additional_checks)
        supplement = {"schema": "testfix.rich-cli.compatibility.v2",
                      "regression_files": files_under(regression),
                      "delivery_checks_sha256": digest(directory / "delivery-checks-v2.json"),
                      "public_clarifications": ["CSV keeps its original head-before-tail precedence."]}
        write_json(directory / "compatibility-v2-seal.json", supplement)
        return supplement, additional_checks

    def read_project():
        return json.loads(registry.read_text(encoding="utf-8"))

    def reject_without_overwriting():
        write_json(registry, {"existing_registration": "must survive rejected metadata"})
        previous = registry.read_bytes()
        with pytest.raises(ValueError):
            register_richcli.register()
        assert registry.read_bytes() == previous

    return SimpleNamespace(root=root, directory=directory, registry=registry,
                           source=source, base_checks=base_checks, add_v2=add_v2,
                           read_project=read_project, reject=reject_without_overwriting)


@pytest.mark.parametrize("wrapped_checks", [False, True])
def test_original_registration_remains_sealed_and_does_not_need_v2(case, wrapped_checks):
    if wrapped_checks:
        checks_file = case.directory / "delivery-checks.json"
        write_json(checks_file, {"checks": case.base_checks})
        seal_file = case.directory / "pre-model-seal.json"
        seal = json.loads(seal_file.read_text(encoding="utf-8"))
        seal["delivery_checks_sha256"] = digest(checks_file)
        write_json(seal_file, seal)
    seal_bytes = (case.directory / "pre-model-seal.json").read_bytes()
    result = register_richcli.register()
    project = case.read_project()
    assert result["seal_verified"] is True
    assert result["model_calls"] == 0
    assert result["evaluation_revision"] == project["evaluation_revision"] == "original-frozen-v1"
    assert project["package_config"]["checks"] == case.base_checks
    assert project["package_config"]["regression"] == str(case.directory / "regression_tests")
    assert project["clarifications"] == ["Original public clarification"]
    assert (case.directory / "pre-model-seal.json").read_bytes() == seal_bytes


def test_sealed_v2_selects_fixed_regression_tree_and_appends_public_metadata(case):
    supplement, extra_checks = case.add_v2()
    before = {name: (case.directory / name).read_bytes() for name in
              ["pre-model-seal.json", "source.json", "requirements.json", "delivery-checks.json"]}
    result = register_richcli.register()
    project = case.read_project()
    assert result["evaluation_revision"] == project["evaluation_revision"] == "head-tail-compatibility-v2"
    assert result["seal_verified"] is True and result["model_calls"] == 0
    assert project["source"] == case.source
    assert project["acceptance"] == str(case.directory / "acceptance_tests")
    assert project["package_config"]["regression"] == str(case.directory / "behavior_regression_v2")
    assert project["package_config"]["checks"] == [*case.base_checks, *extra_checks]
    assert project["clarifications"] == ["Original public clarification", *supplement["public_clarifications"]]
    for name, data in before.items():
        assert (case.directory / name).read_bytes() == data


@pytest.mark.parametrize("enable_v2", [False, True])
@pytest.mark.parametrize("relative", [
    "baseline/src/rich_cli/__main__.py",
    "acceptance_tests/test_acceptance.py",
    "regression_tests/test_original_behavior.py",
    "delivery-checks.json",
])
def test_every_original_v1_seal_is_required_even_with_valid_v2(case, enable_v2, relative):
    if enable_v2:
        case.add_v2()
    path = case.directory / relative
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    case.reject()


@pytest.mark.parametrize("change", ["changed_file", "extra_file", "missing_file", "changed_checks"])
def test_v2_changes_reject_registration_before_replacing_existing_registry(case, change):
    case.add_v2()
    regression = case.directory / "behavior_regression_v2"
    if change == "changed_file":
        (regression / "test_csv_head_tail_compatibility.py").write_text("assert False\n", encoding="utf-8")
    elif change == "extra_file":
        (regression / "unsealed_test.py").write_text("assert True\n", encoding="utf-8")
    elif change == "missing_file":
        (regression / "test_csv_head_tail_compatibility.py").unlink()
    else:
        path = case.directory / "delivery-checks-v2.json"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    case.reject()


@pytest.mark.parametrize("field,value", [
    ("schema", "wrong-schema"),
    ("regression_files", {}),
    ("regression_files", None),
    ("delivery_checks_sha256", "0" * 64),
])
def test_invalid_v2_seal_metadata_fails_closed(case, field, value):
    supplement, _ = case.add_v2()
    supplement[field] = value
    write_json(case.directory / "compatibility-v2-seal.json", supplement)
    case.reject()


@pytest.mark.parametrize("supplement", [None, [], "not an object", 12])
def test_v2_root_must_be_an_object_with_a_clear_validation_error(case, supplement):
    case.add_v2()
    write_json(case.directory / "compatibility-v2-seal.json", supplement)
    case.reject()


@pytest.mark.parametrize("clarifications", [None, "must not expand into characters", {}, [1], [None]])
def test_v2_public_clarifications_must_be_a_list_of_strings(case, clarifications):
    supplement, _ = case.add_v2()
    supplement["public_clarifications"] = clarifications
    write_json(case.directory / "compatibility-v2-seal.json", supplement)
    case.reject()


@pytest.mark.parametrize("extra_checks", [None, {}, {"checks": []}, "not a check list", ["not a check"], [{}],
                                           [check("base-version", "--version")],
                                           [{**check("bad-command", "--version"), "command": ["arbitrary-command"]}],
                                           [{**check("bad-args", "--version"), "args": "--version"}]])
def test_even_sha_matching_v2_extra_checks_must_pass_package_config_validation(case, extra_checks):
    supplement, _ = case.add_v2()
    path = case.directory / "delivery-checks-v2.json"
    write_json(path, extra_checks)
    supplement["delivery_checks_sha256"] = digest(path)
    write_json(case.directory / "compatibility-v2-seal.json", supplement)
    case.reject()


def test_v2_does_not_make_absent_original_seal_optional(case):
    case.add_v2()
    (case.directory / "pre-model-seal.json").unlink()
    with pytest.raises(FileNotFoundError):
        register_richcli.register()
    assert not case.registry.exists()
