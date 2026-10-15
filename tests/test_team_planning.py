"""Work graphs cannot weaken scope, coverage, dependency or solver budgets."""
from copy import deepcopy

import pytest

from testfix.team_planning import (
    DECLARED_TASK_SECONDS, DECLARED_TASK_TOKEN_CAP, TEAM_PLAN_SCHEMA,
    TeamPlanError, graph_metrics, priority_ready, solve_team_plan, validate_team_plan,
)


def item(identity, skill="repair-code", requirements=("R1",), depends=(), paths=("src/main.py",)):
    return {"id": identity, "title": "Implement the registered requirement", "skill_id": skill,
            "requirement_ids": list(requirements), "depends_on": list(depends), "paths": list(paths)}


def plan(*items, requirements=("R1",)):
    return {"request_assessment": {"supported": True, "matched_requirement_ids": list(requirements),
                                    "uncovered_requests": [], "questions": []},
            "work_items": list(items) or [item("repair")]}


def validate(value, requirements=("R1",), allowed=("src/main.py",), public=("src/main.py", "README.md")):
    return validate_team_plan(value, requirements, allowed, public)


def solve(value, **overrides):
    return solve_team_plan(value, wall_seconds=overrides.pop("wall_seconds", 1000),
                           token_limit=overrides.pop("token_limit", 100_000), **overrides)


def test_schema_objects_are_strict_and_all_fields_required():
    def walk(schema):
        # Supported by the strict model output-schema subset; duplicate values
        # are nevertheless rejected by validate_team_plan's host-side checks.
        assert "uniqueItems" not in schema
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == set(schema["properties"])
            for child in schema["properties"].values():
                walk(child)
        if schema.get("type") == "array":
            walk(schema["items"])
    walk(TEAM_PLAN_SCHEMA)


def test_validated_plan_is_detached_and_accepts_public_source_mapping():
    original = plan(item("explore", "explore-repository", paths=("README.md",)),
                    item("repair", depends=("explore",)))
    result = validate(original, public={"src/main.py": "private content is not copied", "README.md": "docs"})
    assert result == original and result is not original
    result["work_items"][0]["paths"].append("another.py")
    assert original["work_items"][0]["paths"] == ["README.md"]


@pytest.mark.parametrize("field,value", [("supported", False), ("uncovered_requests", ["Add an unsupported API"]),
                                         ("questions", ["Which authentication protocol?"])])
def test_unhandled_user_request_cannot_silently_become_a_partial_plan(field, value):
    candidate = plan()
    candidate["request_assessment"][field] = value
    with pytest.raises(TeamPlanError, match="unsupported or unresolved"):
        validate(candidate)
    with pytest.raises(TeamPlanError, match="unsupported or unresolved"):
        solve(candidate)


@pytest.mark.parametrize("matched", [[], ["R1", "R2"], ["other"], ["R1", "R1"]])
def test_matched_ids_must_exactly_cover_registered_requirements(matched):
    candidate = plan()
    candidate["request_assessment"]["matched_requirement_ids"] = matched
    with pytest.raises(TeamPlanError):
        validate(candidate)


def test_exploration_cannot_count_as_repair_coverage():
    candidate = plan(item("explore", "explore-repository", requirements=("R1", "R2")),
                     item("repair", requirements=("R1",)), requirements=("R1", "R2"))
    with pytest.raises(TeamPlanError, match="missing repair-code coverage.*R2"):
        validate(candidate, requirements=("R1", "R2"))


def test_unknown_item_requirement_is_not_ignored():
    candidate = plan(item("repair", requirements=("R1", "unregistered")))
    with pytest.raises(TeamPlanError, match="Unknown requirement IDs"):
        validate(candidate)


@pytest.mark.parametrize("candidate,reason", [
    (plan(item("one", depends=("missing",))), "Unknown dependency"),
    (plan(item("one", depends=("one",))), "cycle"),
    (plan(item("one", depends=("two",)), item("two", depends=("one",))), "cycle"),
    (plan(item("duplicate"), item("duplicate")), "Duplicate work item"),
    (plan(item("../escape")), "id must match"),
    (plan(item("repair", skill="execute-shell")), "unsupported skill_id"),
])
def test_illegal_task_graphs_fail_before_solver(candidate, reason):
    with pytest.raises(TeamPlanError, match=reason):
        validate(candidate)
    with pytest.raises(TeamPlanError, match=reason):
        solve(candidate)


def test_task_count_is_bounded_and_nonempty():
    candidate = plan()
    candidate["work_items"] = []
    with pytest.raises(TeamPlanError, match="1–4"):
        validate(candidate)
    with pytest.raises(TeamPlanError, match="1–4"):
        validate(plan(*(item(f"task{i}") for i in range(5))))


def test_repair_cannot_write_even_public_but_unapproved_files():
    with pytest.raises(TeamPlanError, match="exceed allowed_changes"):
        validate(plan(item("repair", paths=("README.md",))))


def test_exploration_cannot_read_a_private_or_nonexistent_path():
    candidate = plan(item("explore", "explore-repository", paths=("hidden/acceptance.py",)), item("repair"))
    with pytest.raises(TeamPlanError, match="exceed public_files"):
        validate(candidate)


@pytest.mark.parametrize("path", ["../src/main.py", "/src/main.py", "C:/src/main.py", "src\\main.py",
                                  "src/./main.py", "src//main.py", "src/main.py:stream", "src/../main.py"])
def test_paths_are_not_normalized_into_an_unexpected_authority(path):
    with pytest.raises(TeamPlanError, match="repository-relative POSIX"):
        validate(plan(item("repair", paths=(path,))), allowed=(path,))


@pytest.mark.parametrize("mutate", [
    lambda candidate: candidate.update({"command": "untrusted"}),
    lambda candidate: candidate["work_items"][0].update({"shell": "untrusted"}),
    lambda candidate: candidate["request_assessment"].pop("questions"),
    lambda candidate: candidate["request_assessment"].update({"supported": 1}),
    lambda candidate: candidate["work_items"][0].update({"paths": []}),
])
def test_strict_runtime_validation_does_not_depend_on_model_obeying_schema(mutate):
    candidate = plan()
    mutate(candidate)
    with pytest.raises(TeamPlanError):
        validate(candidate)


def test_real_solver_respects_precedence_and_declares_its_assumptions():
    candidate = validate(plan(item("repair", depends=("explore",)),
                              item("explore", "explore-repository", paths=("README.md",))))
    result = solve(candidate)
    assert result["status"] == "OPTIMAL"
    assert result["order"] == ["explore", "repair"]
    assert result["solver"]["name"] == "OR-Tools CP-SAT"
    assert result["solver"]["constraints"]["precedence"] == 1
    assert result["execution_verified"] is False
    assert result["estimates"]["basis"] == "declared_allowances_not_measured_performance"
    assert result["estimates"]["total_tokens"] == 2 * DECLARED_TASK_TOKEN_CAP
    assert result["estimates"]["makespan_seconds"] == 2 * DECLARED_TASK_SECONDS
    assert result["stages"][1]["start_seconds"] >= result["stages"][0]["end_seconds"]


def test_capacity_two_can_overlap_reads_but_dependency_waits_for_both():
    candidate = validate(plan(item("read-a", "explore-repository"),
                              item("read-b", "explore-repository", paths=("README.md",)),
                              item("repair", depends=("read-a", "read-b"))))
    parallel = solve(candidate, model_slots=2, wall_seconds=360)
    assert parallel["status"] == "OPTIMAL"
    assert parallel["estimates"]["makespan_seconds"] == 360
    stages = {stage["id"]: stage for stage in parallel["stages"]}
    assert stages["read-a"]["start_seconds"] == stages["read-b"]["start_seconds"] == 0
    assert stages["repair"]["start_seconds"] == 180
    serial = solve(candidate, model_slots=1, wall_seconds=360)
    assert serial["status"] == "INFEASIBLE"
    assert serial["order"] == [] and serial["stages"] == []
    assert "concurrency_is_not_asserted" in parallel["execution_policy"]


def test_disjoint_repair_paths_can_overlap_with_real_model_capacity():
    candidate = validate(plan(item("first"), item("second", paths=("src/second.py",))),
                         allowed=("src/main.py", "src/second.py"))
    result = solve(candidate, model_slots=2, wall_seconds=180)
    assert result["status"] == "OPTIMAL"
    assert result["estimates"]["makespan_seconds"] == 180
    first, second = result["stages"]
    assert first["start_seconds"] == second["start_seconds"] == 0
    assert result["solver"]["constraints"]["candidate_write_conflicts"] == []
    impossible = solve(candidate, model_slots=1, wall_seconds=180)
    assert impossible["status"] == "INFEASIBLE"
    assert impossible["order"] == [] and impossible["stages"] == []


def test_shared_repair_file_retains_mutual_exclusion_across_multi_path_tasks():
    candidate = validate(plan(item("first", paths=("src/main.py", "src/second.py")),
                              item("second", paths=("src/second.py",))),
                         allowed=("src/main.py", "src/second.py"))
    result = solve(candidate, model_slots=2, wall_seconds=360)
    assert result["status"] == "OPTIMAL"
    assert result["estimates"]["makespan_seconds"] == 360
    first, second = result["stages"]
    assert (first["end_seconds"] <= second["start_seconds"]
            or second["end_seconds"] <= first["start_seconds"])
    assert result["solver"]["constraints"]["candidate_write_conflicts"] == [
        {"tasks": ["first", "second"], "paths": ["src/second.py"]}]
    impossible = solve(candidate, model_slots=2, wall_seconds=180)
    assert impossible["status"] == "INFEASIBLE"
    assert impossible["order"] == [] and impossible["stages"] == []


def test_exploration_does_not_take_a_candidate_write_lock():
    candidate = validate(plan(item("read", "explore-repository"), item("repair")))
    result = solve(candidate, model_slots=2, wall_seconds=180)
    assert result["status"] == "OPTIMAL"
    assert {stage["start_seconds"] for stage in result["stages"]} == {0}
    assert result["solver"]["constraints"]["candidate_write_conflicts"] == []


def test_graph_metrics_count_distinct_descendants_in_a_diamond_and_include_self_in_path():
    candidate = plan(item("root", "explore-repository"),
                     item("left", "explore-repository", depends=("root",)),
                     item("right", "explore-repository", depends=("root",)),
                     item("join", depends=("left", "right")))
    metrics = graph_metrics(candidate, task_seconds=10)
    assert metrics == {
        "root": {"in_degree": 0, "out_degree": 2, "descendant_count": 3,
                 "critical_path_declared_seconds": 30},
        "left": {"in_degree": 1, "out_degree": 1, "descendant_count": 1,
                 "critical_path_declared_seconds": 20},
        "right": {"in_degree": 1, "out_degree": 1, "descendant_count": 1,
                  "critical_path_declared_seconds": 20},
        "join": {"in_degree": 2, "out_degree": 0, "descendant_count": 0,
                 "critical_path_declared_seconds": 10},
    }
    result = solve(candidate, model_slots=2, wall_seconds=30, task_seconds=10)
    assert result["status"] == "OPTIMAL"
    assert result["order"] == ["root", "left", "right", "join"]
    assert result["graph_metrics"] == metrics
    assert result["estimates"]["makespan_seconds"] == 30
    stages = {stage["id"]: stage for stage in result["stages"]}
    assert stages["join"]["start_seconds"] >= max(stages[name]["end_seconds"] for name in ("left", "right"))


def test_direct_out_degree_alone_breaks_equal_critical_path_and_descendant_tie():
    # The two roots reach the same nodes and have equal longest paths. Only the
    # direct edge z-fork -> leaf raises z-fork's out-degree above a-chain's.
    candidate = plan(item("a-chain", "explore-repository"),
                     item("z-fork", "explore-repository"),
                     item("middle", "explore-repository", depends=("a-chain", "z-fork")),
                     item("leaf", depends=("middle", "z-fork")))
    metrics = graph_metrics(candidate)
    assert metrics["a-chain"]["descendant_count"] == metrics["z-fork"]["descendant_count"] == 2
    assert metrics["a-chain"]["critical_path_declared_seconds"] == metrics["z-fork"]["critical_path_declared_seconds"]
    assert priority_ready(["a-chain", "z-fork"], candidate) == ["z-fork", "a-chain"]
    without_direct_edge = deepcopy(candidate)
    without_direct_edge["work_items"][-1]["depends_on"] = ["middle"]
    assert priority_ready(["a-chain", "z-fork"], without_direct_edge) == ["a-chain", "z-fork"]
    assert solve(candidate)["order"] == ["z-fork", "a-chain", "middle", "leaf"]


def test_critical_path_precedes_descendant_count_then_id_and_input_order_is_irrelevant():
    metrics = {
        "a": {"in_degree": 0, "out_degree": 1, "descendant_count": 2,
              "critical_path_declared_seconds": 20},
        "b": {"in_degree": 0, "out_degree": 1, "descendant_count": 3,
              "critical_path_declared_seconds": 10},
        "c": {"in_degree": 0, "out_degree": 1, "descendant_count": 1,
              "critical_path_declared_seconds": 20},
        "d": {"in_degree": 0, "out_degree": 1, "descendant_count": 2,
              "critical_path_declared_seconds": 20},
    }
    assert priority_ready(["b", "d", "c", "a"], metrics) == ["a", "d", "c", "b"]
    assert priority_ready({"a", "b", "c", "d"}, metrics) == ["a", "d", "c", "b"]
    result = solve(plan(item("z"), item("a")))
    assert priority_ready(["z", "a"], result) == ["a", "z"]
    assert result["priority"]["keys"]["a"] == result["stages"][0]["priority_key"]


@pytest.mark.parametrize("identities", [["unknown"], ["repair", "repair"], "repair"])
def test_priority_ready_rejects_unknown_duplicate_or_non_collection_ids(identities):
    with pytest.raises(TeamPlanError):
        priority_ready(identities, plan())


@pytest.mark.parametrize("metrics", [None, {"repair": {}}, {"repair": {
    "in_degree": 0, "out_degree": True, "descendant_count": 0,
    "critical_path_declared_seconds": 180}}])
def test_priority_ready_rejects_invalid_metrics(metrics):
    with pytest.raises(TeamPlanError):
        priority_ready(["repair"], metrics)


def test_metrics_and_ready_priority_do_not_authorize_infeasible_work():
    candidate = plan(item("z", depends=("a",)), item("a"))
    result = solve(candidate, wall_seconds=180, model_slots=4)
    assert result["status"] == "INFEASIBLE"
    assert result["graph_metrics"]["a"]["out_degree"] == 1
    assert result["order"] == result["stages"] == []


def test_valid_task_ids_do_not_collide_with_plan_and_result_field_names():
    candidate = plan(item("work_items"), item("graph_metrics"),
                     item("schema_version"), item("request_assessment"))
    metrics = graph_metrics(candidate)
    expected = ["graph_metrics", "request_assessment", "schema_version", "work_items"]
    assert priority_ready(set(metrics), metrics) == expected
    assert priority_ready(set(metrics), candidate) == expected
    assert priority_ready(set(metrics), solve(candidate)) == expected


@pytest.mark.parametrize("overrides", [{"token_limit": DECLARED_TASK_TOKEN_CAP - 1},
                                        {"wall_seconds": DECLARED_TASK_SECONDS - 1},
                                        {"wall_seconds": 0}, {"model_slots": 0}])
def test_impossible_bounds_return_no_execution_order(overrides):
    result = solve(validate(plan()), **overrides)
    assert result["status"] == "INFEASIBLE"
    assert result["order"] == [] and result["stages"] == []
    assert result["estimates"]["makespan_seconds"] is None


def test_exhausted_solver_time_returns_unknown_without_a_fallback_greedy_order():
    result = solve(validate(plan()), solver_time_limit_seconds=1e-9)
    assert result["status"] == "UNKNOWN"
    assert result["order"] == [] and result["stages"] == []


@pytest.mark.parametrize("overrides", [{"wall_seconds": 1.5}, {"token_limit": True},
                                        {"model_slots": -1}, {"task_seconds": 0},
                                        {"task_tokens": 0}, {"solver_time_limit_seconds": float("nan")},
                                        {"task_tokens": 2**50}, {"solver_time_limit_seconds": 2**10000}])
def test_integer_budget_fields_and_aggregate_domains_fail_closed(overrides):
    candidate = validate(plan(item("a"), item("b")))
    with pytest.raises(TeamPlanError):
        solve(candidate, **overrides)


def test_solver_does_not_mutate_the_validated_task_graph():
    candidate = validate(plan(item("repair", depends=("explore",)),
                              item("explore", "explore-repository")))
    sealed = deepcopy(candidate)
    solve(candidate)
    assert candidate == sealed
