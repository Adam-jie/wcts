"""Exercise the integer planner with OR-Tools, including impossible budgets."""
from __future__ import annotations

from copy import deepcopy
from itertools import product
import random

import pytest

from testfix.scheduling import InvalidConfiguration, solve_plan
import testfix.scheduling as scheduling
from testfix.skills import SkillCatalog


PROJECT_ORDER = [
    "plan-workflow", "explore-repository", "ground-code", "analyze-requirements",
    "generate-tests", "diagnose-results", "repair-code", "review-patch", "verify-delivery",
]


def option(skill_id: str, strategy: str = "baseline", *, duration: int = 10, tokens: int = 20) -> dict:
    return {
        "skill_id": skill_id, "id": strategy, "duration_ms": duration, "token_cap": tokens,
        "resources": {"model_slots": int(skill_id != "verify-delivery"),
                      "cpu_slots": int(skill_id == "verify-delivery"),
                      "candidate_write": int(skill_id == "repair-code")},
        "evidence": {"basis": "Explicit test fixture bounds; not runtime measurements"},
    }


def options() -> list[dict]:
    return [option(skill_id) for skill_id in PROJECT_ORDER]


def budget(**overrides) -> dict:
    return {
        "max_makespan_ms": 1000, "max_tokens": 1000,
        "resources": {"model_slots": 1, "cpu_slots": 1, "candidate_write": 1},
        **overrides,
    }


def test_real_integer_solver_selects_every_stage_and_preserves_dependencies():
    result = solve_plan(list(reversed(options())), budget())
    assert result["status"] == "OPTIMAL"
    assert result["solver"]["name"] == "OR-Tools CP-SAT"
    assert result["solver"]["optimality_proven"] is True
    assert result["execution_verified"] is False
    assert result["validation"]["gate_status"] == "execution_evidence_required"
    assert [stage["id"] for stage in result["stages"]] == PROJECT_ORDER
    assert result["totals"] == {"duration_ms": 90, "token_cap": 180, "makespan_ms": 90,
                                "objective_value": 270}
    for i, stage in enumerate(result["stages"]):
        assert type(stage["start_ms"]) is int and type(stage["end_ms"]) is int
        assert stage["end_ms"] - stage["start_ms"] == stage["duration_ms"] == 10
        assert stage["start_ms"] == (result["stages"][i - 1]["end_ms"] if i else 0)
    assert result["solver"]["model_counts"]["exactly_one"] == 9
    assert result["solver"]["model_counts"]["cumulative"] == 3
    assert result["solver"]["model_counts"]["no_overlap"] == 1


@pytest.mark.parametrize("overrides", [{"max_tokens": 179}, {"max_makespan_ms": 89},
                                        {"resources": {"model_slots": 0, "cpu_slots": 1,
                                                       "candidate_write": 1}},
                                        {"resources": {"model_slots": 1, "cpu_slots": 1,
                                                       "candidate_write": 0}}])
def test_legal_but_impossible_budgets_are_solver_infeasible(overrides: dict):
    result = solve_plan(options(), budget(**overrides))
    assert result["status"] == "INFEASIBLE"
    assert result["stages"] == []
    assert result["totals"] is None
    assert result["solver"]["optimality_proven"] is False
    assert result["solver"]["objective_value"] is None


def test_capacity_conflict_removes_only_illegal_resource_choice():
    choices = options()
    excessive = option("repair-code", "over-capacity", duration=1, tokens=0)
    excessive["resources"]["model_slots"] = 2
    choices.append(excessive)
    result = solve_plan(choices, budget())
    repair = next(stage for stage in result["stages"] if stage["id"] == "repair-code")
    assert result["status"] == "OPTIMAL"
    assert repair["strategy"] == "baseline"
    # Independently check the cumulative capacities at every scheduled boundary.
    for time in {stage["start_ms"] for stage in result["stages"]}:
        active = [stage for stage in result["stages"] if stage["start_ms"] <= time < stage["end_ms"]]
        for resource, capacity in budget()["resources"].items():
            assert sum(stage["resources"][resource] for stage in active) <= capacity


def test_solver_choice_matches_exhaustive_integer_objective_under_hard_budgets():
    choices = []
    pairs = []
    for i, skill_id in enumerate(PROJECT_ORDER):
        pair = [option(skill_id, "quick", duration=2 + i, tokens=20),
                option(skill_id, "frugal", duration=12 + i, tokens=2)]
        pairs.append(pair)
        choices.extend(pair)
    limits = budget(max_makespan_ms=120, max_tokens=90,
                    objective_weights={"duration": 3, "tokens": 2})
    feasible = [combination for combination in product(*pairs)
                if sum(item["duration_ms"] for item in combination) <= limits["max_makespan_ms"]
                and sum(item["token_cap"] for item in combination) <= limits["max_tokens"]]
    optimum = min(sum(3 * item["duration_ms"] + 2 * item["token_cap"] for item in combination)
                  for combination in feasible)
    result = solve_plan(choices, limits)
    assert result["status"] == "OPTIMAL"
    assert result["totals"]["objective_value"] == optimum
    assert result["totals"]["token_cap"] <= 90
    assert result["totals"]["makespan_ms"] <= 120
    assert {stage["strategy"] for stage in result["stages"]} == {"quick", "frugal"}


def test_objective_weights_can_choose_different_legal_strategies():
    choices = options() + [option("ground-code", "quick-expensive", duration=1, tokens=100)]
    fast = solve_plan(choices, budget(objective_weights={"duration": 100, "tokens": 1}))
    frugal = solve_plan(choices, budget(objective_weights={"duration": 1, "tokens": 100}))
    assert fast["stages"][2]["strategy"] == "quick-expensive"
    assert frugal["stages"][2]["strategy"] == "baseline"


@pytest.mark.parametrize("missing", PROJECT_ORDER)
def test_core_and_validation_skills_cannot_be_deleted_to_fit_budget(missing: str):
    with pytest.raises(InvalidConfiguration, match="invalid_configuration"):
        solve_plan([item for item in options() if item["skill_id"] != missing], budget())


@pytest.mark.parametrize("mutate", [
    lambda choices, limits: choices[0].update(duration_ms=0),
    lambda choices, limits: choices[0].update(duration_ms=1.5),
    lambda choices, limits: choices[0].update(token_cap=True),
    lambda choices, limits: choices[0].update(enabled=False),
    lambda choices, limits: choices[0]["evidence"].update(basis=""),
    lambda choices, limits: choices[0]["resources"].update(model_slots=0),
    lambda choices, limits: choices[0]["resources"].update(candidate_write=1),
    lambda choices, limits: choices[6]["resources"].update(candidate_write=0),
    lambda choices, limits: choices[-1]["resources"].update(cpu_slots=0),
    lambda choices, limits: choices.append(deepcopy(choices[0])),
    lambda choices, limits: limits.update(max_tokens=-1),
    lambda choices, limits: limits.update(objective_weights={"duration": 0, "tokens": 0}),
    lambda choices, limits: limits.update(solver_time_limit_seconds=float("nan")),
    lambda choices, limits: limits.update(solver_time_limit_seconds=10 ** 1000),
    lambda choices, limits: limits.update(required_gates=[]),
])
def test_invalid_configuration_is_not_reported_as_solver_infeasibility(mutate):
    choices, limits = options(), budget()
    mutate(choices, limits)
    with pytest.raises(InvalidConfiguration) as failure:
        solve_plan(choices, limits)
    assert failure.value.code == "invalid_configuration"


def test_pure_python_profile_requires_its_complete_six_stage_core():
    choices = [item for item in options() if item["skill_id"] in PROJECT_ORDER[3:]]
    result = solve_plan(choices, budget(), profile="pure_python")
    assert result["status"] == "OPTIMAL"
    assert [stage["id"] for stage in result["stages"]] == PROJECT_ORDER[3:]
    assert "independent_acceptance" in result["validation"]["required_gates"]


def test_catalog_validates_both_input_and_selected_output_boundary():
    class RecordingCatalog(SkillCatalog):
        def __init__(self):
            super().__init__()
            self.calls = []

        def validate_plan(self, profile, skill_ids):
            self.calls.append((profile, list(skill_ids)))
            return super().validate_plan(profile, skill_ids)

    catalog = RecordingCatalog()
    choices, limits = options(), budget()
    before_choices, before_limits = deepcopy(choices), deepcopy(limits)
    solve_plan(choices, limits, catalog=catalog)
    assert catalog.calls == [("flaskr_project", PROJECT_ORDER), ("flaskr_project", PROJECT_ORDER)]
    assert choices == before_choices and limits == before_limits


def test_real_solver_unknown_never_returns_fabricated_stages():
    result = solve_plan(options(), budget(solver_time_limit_seconds=1e-9))
    assert result["status"] == "UNKNOWN"
    assert result["stages"] == [] and result["totals"] is None
    assert result["solver"]["objective_value"] is None


def test_real_solver_feasible_does_not_claim_proved_optimality(monkeypatch):
    real_solver = scheduling.cp_model.CpSolver

    class FirstSolutionSolver(real_solver):
        def solve(self, model, solution_callback=None):
            # Exercise a real interrupted CP-SAT search, without forging a status
            # or replacing its selected strategy/timing values with test data.
            self.parameters.cp_model_presolve = False
            self.parameters.linearization_level = 0
            self.parameters.stop_after_first_solution = True
            return super().solve(model, solution_callback)

    monkeypatch.setattr(scheduling.cp_model, "CpSolver", FirstSolutionSolver)
    rng = random.Random(45)
    choices = [option(skill_id, str(strategy), duration=rng.randint(1, 100), tokens=rng.randint(1, 100))
               for skill_id in PROJECT_ORDER for strategy in range(16)]
    result = solve_plan(choices, budget(max_makespan_ms=350, max_tokens=350))
    assert result["status"] == "FEASIBLE"
    assert result["solver"]["optimality_proven"] is False
    assert len(result["stages"]) == 9
    assert result["totals"]["makespan_ms"] <= 350
    assert result["totals"]["token_cap"] <= 350


def test_large_integer_objective_is_rejected_before_solver_overflow():
    with pytest.raises(InvalidConfiguration, match="integer"):
        solve_plan(options(), budget(objective_weights={"duration": 2 ** 60, "tokens": 1}))


def test_large_resource_aggregate_is_invalid_configuration():
    choices = options()
    for item in choices:
        item["resources"]["cpu_slots"] = 2 ** 60
    with pytest.raises(InvalidConfiguration, match="integer"):
        solve_plan(choices, budget())


def test_python_package_solves_real_nine_stage_integer_plan_with_cli_delivery():
    choices = options() + [option("repair-code", "quick-package", duration=1, tokens=1)]
    result = solve_plan(choices, budget(), profile="python_package")
    assert result["status"] == "OPTIMAL"
    assert result["profile"] == "python_package"
    assert [stage["id"] for stage in result["stages"]] == PROJECT_ORDER
    assert result["stages"][6]["strategy"] == "quick-package"
    assert result["totals"]["objective_value"] == 242
    assert "real_cli" in result["validation"]["required_gates"]
    assert {"real_http", "sqlite_restart"}.isdisjoint(result["validation"]["required_gates"])
    assert result["validation"]["execution_verified"] is False


def test_python_package_integer_budget_cannot_skip_cli_verifier():
    result = solve_plan(options(), budget(max_makespan_ms=89), profile="python_package")
    assert result["status"] == "INFEASIBLE" and result["stages"] == []
    with pytest.raises(InvalidConfiguration):
        solve_plan(options()[:-1], budget(max_makespan_ms=80), profile="python_package")
