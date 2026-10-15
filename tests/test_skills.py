"""Skill catalogs fail closed and never turn metadata checks into execution claims."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from testfix.skills import DEFAULT_CATALOG, SkillCatalog, SkillValidationError, load_catalog


ROLE_SKILLS = {
    "requirements": "analyze-requirements", "tester": "generate-tests", "diagnosis": "diagnose-results",
    "developer": "repair-code", "reviewer": "review-patch",
}
PURE_PLAN = list(ROLE_SKILLS.values()) + ["verify-delivery"]
PROJECT_PLAN = ["plan-workflow", "explore-repository", "ground-code"] + PURE_PLAN
PURE_GATES = ["generated_tests", "independent_acceptance", "regression_preservation", "candidate_policy", "review"]
PROJECT_GATES = ["paired_test_ids", "source_binding", "original_regression", "generated_tests", "independent_acceptance",
                 "protected_hashes", "review_hash", "wheel_build", "real_http", "sqlite_restart"]
PACKAGE_GATES = ["paired_test_ids", "source_binding", "original_regression", "generated_tests", "independent_acceptance",
                 "protected_hashes", "review_hash", "wheel_build", "real_cli"]
PREREQUISITES = {
    "plan-workflow": [], "explore-repository": ["plan-workflow"], "ground-code": ["explore-repository"],
    "analyze-requirements": [], "generate-tests": ["analyze-requirements"],
    "diagnose-results": ["generate-tests"], "repair-code": ["diagnose-results"],
    "review-patch": ["repair-code"], "verify-delivery": ["review-patch"],
}


def save_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture
def catalog_path(tmp_path: Path) -> Path:
    root = tmp_path / "library"
    root.mkdir()
    catalog = {
        "schema_version": "testfix.skill-catalog.v1", "version": "1.0.0",
        "skills": [{"id": skill_id, "path": f"{skill_id}/SKILL.md",
                    "contract": f"{skill_id}/references/contract.json"} for skill_id in PROJECT_PLAN],
        "roles": ROLE_SKILLS.copy(),
        "profiles": {profile: {"required_skills": plan.copy(),
                               "protected_resources": ["independent_acceptance", "regression_tests", "reference_solution"],
                               "required_gates": gates.copy()}
                     for profile, plan, gates in (("pure_python", PURE_PLAN, PURE_GATES),
                                                  ("flaskr_project", PROJECT_PLAN, PROJECT_GATES))},
    }
    save_json(root / "catalog.json", catalog)
    roles_by_skill = {skill_id: role for role, skill_id in ROLE_SKILLS.items()}
    for skill_id in PROJECT_PLAN:
        skill_root = root / skill_id
        (skill_root / "references").mkdir(parents=True)
        (skill_root / "SKILL.md").write_text(f"# {skill_id}\nUse public evidence. 中文说明。\n", encoding="utf-8")
        profiles = ["pure_python", "flaskr_project"] if skill_id in PURE_PLAN else ["flaskr_project"]
        contract = {
            "schema_version": "testfix.skill.v1", "id": skill_id, "version": "1.0.0",
            "kind": "llm" if skill_id in roles_by_skill else "deterministic",
            "role": roles_by_skill.get(skill_id, skill_id), "paper_capability": "test fixture capability",
            "profiles": profiles, "inputs": ["public_specification"], "outputs": ["skill_feedback"],
            "reads": ["public_specification", "candidate_sources"],
            "writes": ["candidate_sources"] if skill_id == "repair-code" else ["skill_feedback"],
            "constraints": [{"id": "logical-scope", "description": "Validate declared resource scopes",
                             "enforcement": "catalog_validator", "check": "validate_invocation"}],
            "prerequisites": PREREQUISITES[skill_id], "budgets": {"max_attempts": 3, "timeout_seconds": 120},
            "failure": {"codes": ["invalid_input"], "feedback_fields": ["reason"]},
            "optimization": {"mandatory": True, "resources": ["cpu"],
                             "estimated_seconds": None, "estimated_tokens": None},
        }
        save_json(skill_root / "references" / "contract.json", contract)
    return root / "catalog.json"


def edit_catalog(path: Path, edit) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    edit(value)
    save_json(path, value)


def edit_contract(path: Path, skill_id: str, edit) -> None:
    edit_catalog(path.parent / skill_id / "references" / "contract.json", edit)


def assert_error(code: str, operation) -> None:
    with pytest.raises(SkillValidationError) as error:
        operation()
    assert error.value.code == code


def test_fixture_load_render_and_metadata_validation(catalog_path: Path):
    library = load_catalog(catalog_path)
    assert library.skill_ids == tuple(PROJECT_PLAN)
    prompt = library.render_instructions("developer")
    assert "# repair-code" in prompt
    assert "中文说明" in prompt
    assert "Profile: pure_python" in prompt
    assert "independent_acceptance" in prompt
    assert "not an OS sandbox" in prompt
    assert "# generate-tests" not in prompt
    plan = library.validate_plan("pure_python", PURE_PLAN)
    assert plan["required_gates"] == PURE_GATES
    assert plan["gate_status"] == "execution_evidence_required"
    assert plan["execution_verified"] is False
    assert library.validate_plan("flaskr_project", PROJECT_PLAN)["execution_verified"] is False
    invocation = library.validate_invocation("repair-code", "pure_python", ["candidate_sources"],
                                            ["candidate_sources"], ["diagnose-results"], 3, 120)
    assert invocation["validation"] == "declared_request_only"
    assert invocation["execution_verified"] is False


def test_rendered_remote_prompt_contains_the_role_contract(catalog_path: Path):
    library = SkillCatalog(catalog_path)
    prompt = library.render_instructions("developer")
    rendered_contract = json.loads(prompt.rsplit("```json\n", 1)[1].split("```", 1)[0])
    assert rendered_contract["id"] == "repair-code"
    assert rendered_contract["budgets"] == {"max_attempts": 3, "timeout_seconds": 120}
    assert rendered_contract["prerequisites"] == ["diagnose-results"]
    assert rendered_contract["failure"]["feedback_fields"] == ["reason"]
    assert any(constraint["id"] == "logical-scope" and constraint["enforcement"] == "catalog_validator"
               for constraint in rendered_contract["constraints"])
    assert "neither this contract nor a validated plan proves execution" in prompt


@pytest.mark.parametrize("profile,gate", [("pure_python", "independent_acceptance"),
                                          ("pure_python", "regression_preservation"),
                                          ("flaskr_project", "wheel_build"),
                                          ("flaskr_project", "real_http"),
                                          ("flaskr_project", "sqlite_restart")])
def test_catalog_cannot_drop_required_delivery_gates(catalog_path: Path, profile: str, gate: str):
    edit_catalog(catalog_path, lambda value: value["profiles"][profile]["required_gates"].remove(gate))
    assert_error("missing_required_gate", lambda: SkillCatalog(catalog_path))


def test_catalog_may_add_required_gate_without_claiming_execution(catalog_path: Path):
    edit_catalog(catalog_path, lambda value: value["profiles"]["pure_python"]["required_gates"].append("extra_audit"))
    result = SkillCatalog(catalog_path).validate_plan("pure_python", PURE_PLAN)
    assert "extra_audit" in result["required_gates"]
    assert result["gate_status"] == "execution_evidence_required"
    assert result["execution_verified"] is False


@pytest.mark.parametrize("field,value", [("schema_version", "unknown.v9"), ("version", "2.0.0")])
def test_unknown_contract_schema_or_version_rejected(catalog_path: Path, field: str, value: str):
    edit_contract(catalog_path, "repair-code", lambda contract: contract.update({field: value}))
    assert_error("unknown_schema", lambda: SkillCatalog(catalog_path))


def test_unknown_catalog_schema_rejected(catalog_path: Path):
    edit_catalog(catalog_path, lambda value: value.update(schema_version="unknown"))
    assert_error("unknown_schema", lambda: SkillCatalog(catalog_path))


@pytest.mark.parametrize("removed", ["budgets", "reads", "constraints"])
def test_missing_contract_fields_rejected(catalog_path: Path, removed: str):
    edit_contract(catalog_path, "repair-code", lambda value: value.pop(removed))
    assert_error("invalid_schema", lambda: SkillCatalog(catalog_path))


def test_unknown_schema_fields_and_duplicate_json_keys_rejected(catalog_path: Path):
    edit_catalog(catalog_path, lambda value: value.update(allow_anything=True))
    assert_error("invalid_schema", lambda: SkillCatalog(catalog_path))
    catalog_path.write_text('{"schema_version":"a","schema_version":"b"}', encoding="utf-8")
    assert_error("invalid_schema", lambda: SkillCatalog(catalog_path))


@pytest.mark.parametrize("bad_path", ["../outside/SKILL.md", "/outside/SKILL.md", "C:/outside/SKILL.md",
                                     "repair-code\\SKILL.md", "review-patch/SKILL.md"])
def test_paths_cannot_escape_or_borrow_another_skill(catalog_path: Path, bad_path: str):
    edit_catalog(catalog_path, lambda value: value["skills"][0].update(path=bad_path))
    assert_error("unsafe_path", lambda: SkillCatalog(catalog_path))


def test_contract_path_cannot_escape(catalog_path: Path):
    edit_catalog(catalog_path, lambda value: value["skills"][0].update(contract="../contract.json"))
    assert_error("unsafe_path", lambda: SkillCatalog(catalog_path))


def test_symlink_escape_is_rejected(catalog_path: Path, tmp_path: Path):
    target = tmp_path / "outside.md"
    target.write_text("external instructions", encoding="utf-8")
    linked = catalog_path.parent / "plan-workflow" / "SKILL.md"
    linked.unlink()
    try:
        linked.symlink_to(target)
    except OSError as error:
        pytest.skip(f"OS does not allow symlink creation: {error}")
    assert_error("unsafe_path", lambda: SkillCatalog(catalog_path))


def test_resolved_escape_is_rejected_without_os_symlink_privileges(catalog_path: Path, tmp_path: Path, monkeypatch):
    target = tmp_path / "outside.md"
    target.write_text("external instructions", encoding="utf-8")
    linked = catalog_path.parent / "plan-workflow" / "SKILL.md"
    original_resolve = Path.resolve

    def resolve(path: Path, *args, **kwargs):
        return original_resolve(target if path == linked else path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert_error("unsafe_path", lambda: SkillCatalog(catalog_path))


def test_missing_file_and_mismatched_id_rejected(catalog_path: Path):
    body = catalog_path.parent / "repair-code" / "SKILL.md"
    body.unlink()
    assert_error("missing_file", lambda: SkillCatalog(catalog_path))
    body.write_text("Repair", encoding="utf-8")
    edit_contract(catalog_path, "repair-code", lambda value: value.update(id="different-id"))
    assert_error("id_mismatch", lambda: SkillCatalog(catalog_path))


def test_duplicate_skill_ids_rejected(catalog_path: Path):
    edit_catalog(catalog_path, lambda value: value["skills"].append(value["skills"][0].copy()))
    assert_error("duplicate_id", lambda: SkillCatalog(catalog_path))


@pytest.mark.parametrize("resource", ["independent_acceptance", "regression_tests", "reference_solution"])
def test_protected_writes_rejected_during_load_and_invocation(catalog_path: Path, resource: str):
    library = SkillCatalog(catalog_path)
    assert_error("protected_write", lambda: library.validate_invocation(
        "repair-code", "pure_python", [], [resource], ["diagnose-results"], 1, 30))
    edit_contract(catalog_path, "repair-code", lambda value: value.update(writes=[resource]))
    assert_error("protected_write", lambda: SkillCatalog(catalog_path))


def test_only_repair_can_write_candidate_sources(catalog_path: Path):
    library = SkillCatalog(catalog_path)
    assert_error("protected_write", lambda: library.validate_invocation(
        "generate-tests", "pure_python", [], ["candidate_sources"], ["analyze-requirements"], 1, 30))
    edit_contract(catalog_path, "generate-tests", lambda value: value.update(writes=["candidate_sources"]))
    assert_error("protected_write", lambda: SkillCatalog(catalog_path))


@pytest.mark.parametrize("resource", ["independent_acceptance", "reference_solution"])
def test_llm_cannot_read_hidden_evidence_but_verifier_can(catalog_path: Path, resource: str):
    library = SkillCatalog(catalog_path)
    assert_error("protected_read", lambda: library.validate_invocation(
        "repair-code", "pure_python", [resource], [], ["diagnose-results"], 1, 30))
    edit_contract(catalog_path, "repair-code", lambda value: value["reads"].append(resource))
    assert_error("protected_read", lambda: SkillCatalog(catalog_path))
    edit_contract(catalog_path, "repair-code", lambda value: value["reads"].remove(resource))
    edit_contract(catalog_path, "verify-delivery", lambda value: value["reads"].append(resource))
    library = SkillCatalog(catalog_path)
    result = library.validate_invocation("verify-delivery", "pure_python", [resource], [], ["review-patch"], 1, 30)
    assert result["execution_verified"] is False


def test_profile_additional_protected_resources_are_enforced(catalog_path: Path):
    edit_catalog(catalog_path, lambda value: value["profiles"]["pure_python"]["protected_resources"].append("skill_feedback"))
    assert_error("protected_write", lambda: SkillCatalog(catalog_path))


def test_roles_and_profiles_are_validated(catalog_path: Path):
    library = SkillCatalog(catalog_path)
    assert_error("unknown_role", lambda: library.render_instructions("superuser"))
    assert_error("unknown_profile", lambda: library.render_instructions("developer", "all_permissions"))
    assert_error("profile_mismatch", lambda: library.validate_plan("pure_python", PROJECT_PLAN))
    edit_catalog(catalog_path, lambda value: value["roles"].update(developer="missing"))
    assert_error("unknown_skill", lambda: SkillCatalog(catalog_path))


def test_contract_role_must_match_mapping(catalog_path: Path):
    edit_contract(catalog_path, "repair-code", lambda value: value.update(role="tester"))
    assert_error("role_mismatch", lambda: SkillCatalog(catalog_path))


def test_unknown_and_cyclic_dependencies_rejected(catalog_path: Path):
    edit_contract(catalog_path, "analyze-requirements", lambda value: value.update(prerequisites=["missing"]))
    assert_error("unknown_skill", lambda: SkillCatalog(catalog_path))
    edit_contract(catalog_path, "analyze-requirements", lambda value: value.update(prerequisites=["review-patch"]))
    assert_error("dependency_cycle", lambda: SkillCatalog(catalog_path))


def test_dependency_unavailable_in_profile_rejected(catalog_path: Path):
    edit_contract(catalog_path, "analyze-requirements", lambda value: value.update(prerequisites=["ground-code"]))
    assert_error("profile_mismatch", lambda: SkillCatalog(catalog_path))


def test_invocation_scopes_prerequisites_and_known_completed_ids(catalog_path: Path):
    library = SkillCatalog(catalog_path)
    assert_error("scope_violation", lambda: library.validate_invocation(
        "repair-code", "pure_python", ["secret_data"], [], ["diagnose-results"], 1, 30))
    assert_error("scope_violation", lambda: library.validate_invocation(
        "repair-code", "pure_python", [], ["unlisted_output"], ["diagnose-results"], 1, 30))
    assert_error("missing_prerequisite", lambda: library.validate_invocation(
        "repair-code", "pure_python", [], [], [], 1, 30))
    assert_error("unknown_skill", lambda: library.validate_invocation(
        "repair-code", "pure_python", [], [], ["diagnose-results", "missing"], 1, 30))


@pytest.mark.parametrize("attempt,timeout,code", [(4, 30, "budget_exceeded"), (1, 121, "budget_exceeded"),
                                                (0, 30, "invalid_schema"), (1, 0, "invalid_schema"),
                                                (True, 30, "invalid_schema"), (1, 1.5, "invalid_schema")])
def test_invocation_budget_bounds(catalog_path: Path, attempt, timeout, code):
    library = SkillCatalog(catalog_path)
    assert_error(code, lambda: library.validate_invocation("repair-code", "pure_python", [], [],
                                                         ["diagnose-results"], attempt, timeout))


def test_invalid_contract_budget_and_unmeasured_cost(catalog_path: Path):
    edit_contract(catalog_path, "repair-code", lambda value: value["budgets"].update(max_attempts=True))
    assert_error("invalid_schema", lambda: SkillCatalog(catalog_path))
    edit_contract(catalog_path, "repair-code", lambda value: value["budgets"].update(max_attempts=3))
    edit_contract(catalog_path, "repair-code", lambda value: value["optimization"].update(estimated_tokens=0))
    assert_error("invalid_schema", lambda: SkillCatalog(catalog_path))


def test_plan_requires_verifier_reviewer_known_unique_ordered_nodes(catalog_path: Path):
    library = SkillCatalog(catalog_path)
    assert_error("missing_required_skill", lambda: library.validate_plan("pure_python", PURE_PLAN[:-1]))
    assert_error("missing_required_skill", lambda: library.validate_plan("pure_python", PURE_PLAN[:-2]))
    assert_error("unknown_skill", lambda: library.validate_plan("pure_python", ["missing"] + PURE_PLAN))
    assert_error("invalid_schema", lambda: library.validate_plan("pure_python", PURE_PLAN + ["repair-code"]))
    assert_error("missing_prerequisite", lambda: library.validate_plan(
        "pure_python", ["generate-tests", "analyze-requirements"] + PURE_PLAN[2:]))


def test_profile_cannot_make_review_or_verifier_optional(catalog_path: Path):
    edit_catalog(catalog_path, lambda value: value["profiles"]["pure_python"].update(required_skills=PURE_PLAN[:-1]))
    assert_error("missing_delivery_skill", lambda: SkillCatalog(catalog_path))


def test_optional_optimization_cannot_remove_required_skill(catalog_path: Path):
    edit_contract(catalog_path, "verify-delivery", lambda value: value["optimization"].update(mandatory=False))
    library = SkillCatalog(catalog_path)
    assert_error("missing_required_skill", lambda: library.validate_plan("pure_python", PURE_PLAN[:-1]))


@pytest.mark.parametrize("relative", ["catalog.json", "repair-code/SKILL.md", "repair-code/references/contract.json"])
def test_snapshot_hashes_exact_bytes_and_detects_stale_files(catalog_path: Path, relative: str):
    library = SkillCatalog(catalog_path)
    snapshot = library.snapshot()
    loaded = catalog_path.parent / relative
    assert snapshot["files"][relative] == hashlib.sha256(loaded.read_bytes()).hexdigest()
    library.verify_snapshot(snapshot)
    loaded.write_bytes(loaded.read_bytes() + b"\n")
    assert library.snapshot() == snapshot
    assert_error("stale_snapshot", lambda: library.verify_snapshot(snapshot))


def test_snapshot_mismatch_missing_file_and_metadata_copies(catalog_path: Path):
    library = SkillCatalog(catalog_path)
    snapshot = library.snapshot()
    changed = library.snapshot()
    changed["files"]["catalog.json"] = "0" * 64
    assert_error("stale_snapshot", lambda: library.verify_snapshot(changed))
    assert library.snapshot() == snapshot
    contract = library.get_contract("repair-code")
    contract["writes"].append("secret_data")
    metadata = library.catalog
    metadata["profiles"]["pure_python"]["required_skills"].clear()
    assert "secret_data" not in library.get_contract("repair-code")["writes"]
    assert library.catalog["profiles"]["pure_python"]["required_skills"] == PURE_PLAN
    (catalog_path.parent / "repair-code" / "SKILL.md").unlink()
    assert_error("stale_snapshot", lambda: library.verify_snapshot(snapshot))


def test_bundled_default_library_loads_and_renders_existing_roles():
    library = load_catalog()
    assert set(library.skill_ids) == set(PROJECT_PLAN)
    for role, skill_id in ROLE_SKILLS.items():
        assert skill_id in library.render_instructions(role)
    for profile, plan in (("pure_python", PURE_PLAN), ("flaskr_project", PROJECT_PLAN)):
        assert library.validate_plan(profile, plan)["execution_verified"] is False
    library.verify_snapshot(library.snapshot())


@pytest.fixture
def package_catalog_path(tmp_path: Path) -> Path:
    root = tmp_path / "package-library"
    shutil.copytree(DEFAULT_CATALOG.parent, root)
    return root / "catalog.json"


def test_python_package_bundled_profile_has_nine_stages_cli_gates_and_versioned_assets():
    library = load_catalog()
    assert library.catalog["version"] == "1.2.0"
    validation = library.validate_plan("python_package", PROJECT_PLAN)
    assert validation["required_order"] == PROJECT_PLAN
    assert validation["required_gates"] == PACKAGE_GATES
    assert validation["execution_verified"] is False
    assert validation["gate_status"] == "execution_evidence_required"
    assert {"real_http", "sqlite_restart"}.isdisjoint(validation["required_gates"])
    assert library.validate_plan("flaskr_project", PROJECT_PLAN)["required_gates"] == PROJECT_GATES
    assert library.validate_plan("pure_python", PURE_PLAN)["required_gates"] == PURE_GATES
    snapshot = library.snapshot()
    assert snapshot["catalog_version"] == "1.2.0" and len(snapshot["files"]) == 19
    for skill_id in PROJECT_PLAN:
        contract = library.get_contract(skill_id)
        assert contract["version"] == "1.2.0"
        assert "python_package" in contract["profiles"]
        body = (library.root / skill_id / "SKILL.md").read_text(encoding="utf-8")
        assert 'contract-version: "1.2.0"' in body
        assert "python_package" in body
        assert any(item["id"] == "package.profile" for item in contract["constraints"])
    library.verify_snapshot(snapshot)


@pytest.mark.parametrize("role", ROLE_SKILLS)
def test_python_package_role_instructions_and_invocation_keep_scope_and_dependencies(role: str):
    library = load_catalog()
    skill_id = ROLE_SKILLS[role]
    prompt = library.render_instructions(role, "python_package")
    assert "Profile: python_package" in prompt and "real_cli" in prompt
    header = prompt.split("Gate labels describe", 1)[0]
    assert "real_http" not in header and "sqlite_restart" not in header
    contract = library.get_contract(skill_id)
    result = library.validate_invocation(skill_id, "python_package", contract["reads"], contract["writes"],
                                         contract["prerequisites"], 1, contract["budgets"]["timeout_seconds"])
    assert result["profile"] == "python_package" and result["execution_verified"] is False
    assert_error("protected_read", lambda: library.validate_invocation(
        skill_id, "python_package", ["independent_acceptance"], [], contract["prerequisites"], 1, 30))
    assert_error("protected_write", lambda: library.validate_invocation(
        skill_id, "python_package", [], ["regression_tests"], contract["prerequisites"], 1, 30))


@pytest.mark.parametrize("gate", PACKAGE_GATES)
def test_python_package_catalog_cannot_drop_any_required_delivery_gate(package_catalog_path: Path, gate: str):
    edit_catalog(package_catalog_path, lambda data: data["profiles"]["python_package"]["required_gates"].remove(gate))
    assert_error("missing_required_gate", lambda: SkillCatalog(package_catalog_path))


@pytest.mark.parametrize("missing", PROJECT_PLAN)
def test_python_package_catalog_cannot_delete_core_skill(package_catalog_path: Path, missing: str):
    edit_catalog(package_catalog_path,
                 lambda data: data["profiles"]["python_package"]["required_skills"].remove(missing))
    expected = "missing_delivery_skill" if missing in {"review-patch", "verify-delivery"} else "missing_core_skill"
    assert_error(expected, lambda: SkillCatalog(package_catalog_path))


def test_python_package_does_not_replace_flaskr_http_gates_with_cli(package_catalog_path: Path):
    edit_catalog(package_catalog_path,
                 lambda data: data["profiles"]["flaskr_project"].update(required_gates=PACKAGE_GATES.copy()))
    assert_error("missing_required_gate", lambda: SkillCatalog(package_catalog_path))


def test_python_package_snapshot_still_rejects_modified_contract(package_catalog_path: Path):
    library = SkillCatalog(package_catalog_path)
    snapshot = library.snapshot()
    path = package_catalog_path.parent / "verify-delivery/references/contract.json"
    path.write_bytes(path.read_bytes() + b"\n")
    assert_error("stale_snapshot", lambda: library.verify_snapshot(snapshot))


def test_catalog_v12_requires_package_profile(package_catalog_path: Path):
    edit_catalog(package_catalog_path, lambda data: data["profiles"].pop("python_package"))
    assert_error("invalid_schema", lambda: SkillCatalog(package_catalog_path))


def test_python_package_verifier_can_read_frozen_evidence_but_not_write_it():
    library = load_catalog()
    contract = library.get_contract("verify-delivery")
    result = library.validate_invocation("verify-delivery", "python_package", ["independent_acceptance"],
                                         ["verification_report"], ["review-patch"], 5, 240)
    assert result["validation"] == "declared_request_only" and result["execution_verified"] is False
    assert_error("protected_write", lambda: library.validate_invocation(
        "verify-delivery", "python_package", [], ["independent_acceptance"], ["review-patch"], 1, 30))
    assert_error("budget_exceeded", lambda: library.validate_invocation(
        "verify-delivery", "python_package", [], [], ["review-patch"], contract["budgets"]["max_attempts"] + 1, 30))


def test_legacy_catalog_cannot_declare_an_unavailable_package_contract(catalog_path: Path):
    edit_contract(catalog_path, "repair-code", lambda data: data["profiles"].append("python_package"))
    assert_error("unknown_profile", lambda: SkillCatalog(catalog_path))


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0"])
def test_legacy_two_profile_catalogs_and_contracts_remain_loadable(catalog_path: Path, version: str):
    edit_catalog(catalog_path, lambda data: data.update(version=version))
    for skill_id in PROJECT_PLAN:
        edit_contract(catalog_path, skill_id, lambda data: data.update(version=version))
    library = SkillCatalog(catalog_path)
    assert set(library.catalog["profiles"]) == {"pure_python", "flaskr_project"}
    assert library.validate_plan("flaskr_project", PROJECT_PLAN)["required_gates"] == PROJECT_GATES
    assert library.validate_plan("pure_python", PURE_PLAN)["required_gates"] == PURE_GATES
    assert_error("unknown_profile", lambda: library.validate_plan("python_package", PROJECT_PLAN))
