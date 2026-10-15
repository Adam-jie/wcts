"""Integer selection and scheduling of mandatory Skills using OR-Tools CP-SAT.

The inputs are caller-declared duration upper bounds and token caps. A solver
optimum is an optimum of those declarations, not a measured runtime guarantee.
This module never calls a model, executes business code, or passes delivery gates.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

import ortools
from ortools.sat.python import cp_model

from .skills import SkillCatalog, SkillValidationError


PLAN_SCHEMA = "testfix.integer-plan.v1"
RESOURCE_NAMES = ("model_slots", "cpu_slots", "candidate_write")
# Keep aggregate domains and coefficients safely inside CP-SAT's signed int64.
_INTEGER_LIMIT = 2 ** 60
_OPTION_FIELDS = {"skill_id", "id", "duration_ms", "token_cap", "resources", "evidence"}
_BUDGET_REQUIRED = {"max_makespan_ms", "max_tokens", "resources"}
_BUDGET_OPTIONAL = {"objective_weights", "solver_time_limit_seconds", "random_seed"}


class InvalidConfiguration(ValueError):
    """Malformed or catalog-illegal input, distinct from a proved infeasible plan."""

    code = "invalid_configuration"

    def __init__(self, message: str):
        super().__init__(f"{self.code}: {message}")


class SolverModelError(RuntimeError):
    """The integer model failed solver validation; no substitute plan is returned."""


def _integer(value: Any, label: str, *, minimum: int = 0, maximum: int = _INTEGER_LIMIT) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise InvalidConfiguration(f"{label} must be an integer in [{minimum}, {maximum}]")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise InvalidConfiguration(f"{label} must be a nonempty trimmed string")
    return value


def _exact_object(value: Any, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise InvalidConfiguration(f"{label} must contain exactly {sorted(keys)}")
    return value


def _resources(value: Any, label: str) -> dict:
    resources = _exact_object(value, set(RESOURCE_NAMES), label)
    return {name: _integer(resources[name], f"{label}.{name}", maximum=1 if name == "candidate_write"
                           else _INTEGER_LIMIT) for name in RESOURCE_NAMES}


def _validate_budget(budget: dict) -> dict:
    if not isinstance(budget, dict) or not _BUDGET_REQUIRED <= set(budget) or \
            set(budget) - _BUDGET_REQUIRED - _BUDGET_OPTIONAL:
        raise InvalidConfiguration(f"budget requires {sorted(_BUDGET_REQUIRED)} and permits only "
                                   f"{sorted(_BUDGET_OPTIONAL)} as additional fields")
    weights = _exact_object(budget.get("objective_weights", {"duration": 1, "tokens": 1}),
                            {"duration", "tokens"}, "budget.objective_weights")
    weights = {name: _integer(value, f"objective_weights.{name}") for name, value in weights.items()}
    if not any(weights.values()):
        raise InvalidConfiguration("At least one objective weight must be positive")
    raw_time_limit = budget.get("solver_time_limit_seconds", 10.0)
    try:
        time_limit = float(raw_time_limit) if type(raw_time_limit) in {int, float} else float("nan")
    except (OverflowError, ValueError):
        time_limit = float("nan")
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise InvalidConfiguration("solver_time_limit_seconds must be a finite positive number")
    return {
        "max_makespan_ms": _integer(budget["max_makespan_ms"], "max_makespan_ms", minimum=1),
        "max_tokens": _integer(budget["max_tokens"], "max_tokens"),
        "resources": _resources(budget["resources"], "budget.resources"),
        "objective_weights": weights, "solver_time_limit_seconds": float(time_limit),
        "random_seed": _integer(budget.get("random_seed", 0), "random_seed", maximum=2 ** 31 - 1),
    }


def _validate_options(options: list[dict], profile: str, catalog: SkillCatalog) -> tuple[dict, dict]:
    if not isinstance(options, list):
        raise InvalidConfiguration("options must be a list of strategy objects")
    definitions = catalog.catalog["profiles"]
    if not isinstance(profile, str) or profile not in definitions:
        raise InvalidConfiguration(f"Unknown profile {profile!r}")
    order = definitions[profile]["required_skills"]
    grouped: dict[str, list[dict]] = {}
    seen = set()
    for position, raw in enumerate(options):
        item = _exact_object(raw, _OPTION_FIELDS, f"options[{position}]")
        skill_id = _string(item["skill_id"], "option.skill_id")
        strategy_id = _string(item["id"], "option.id")
        if skill_id not in order:
            raise InvalidConfiguration(f"{skill_id!r} is not a required Skill in profile {profile}")
        if (skill_id, strategy_id) in seen:
            raise InvalidConfiguration(f"Duplicate strategy {strategy_id!r} for {skill_id}")
        seen.add((skill_id, strategy_id))
        evidence = _exact_object(item["evidence"], {"basis"}, "option.evidence")
        _string(evidence["basis"], "option.evidence.basis")
        resources = _resources(item["resources"], "option.resources")
        contract = catalog.get_contract(skill_id)
        if contract["kind"] == "llm" and resources["model_slots"] < 1:
            raise InvalidConfiguration(f"LLM Skill {skill_id} requires at least one model_slots unit")
        if contract["kind"] == "deterministic" and resources["cpu_slots"] < 1:
            raise InvalidConfiguration(f"Deterministic Skill {skill_id} requires at least one cpu_slots unit")
        writes_candidate = "candidate_sources" in contract["writes"]
        if resources["candidate_write"] != int(writes_candidate):
            raise InvalidConfiguration(f"{skill_id}.candidate_write must match the catalog's candidate_sources writes")
        grouped.setdefault(skill_id, []).append({
            "skill_id": skill_id, "id": strategy_id,
            "duration_ms": _integer(item["duration_ms"], "option.duration_ms", minimum=1),
            "token_cap": _integer(item["token_cap"], "option.token_cap"),
            "resources": resources, "evidence": deepcopy(evidence),
        })
    # This boundary rejects omitted mandatory Skills and retains all fixed gates.
    try:
        validation = catalog.validate_plan(profile, [skill_id for skill_id in order if skill_id in grouped])
    except SkillValidationError as error:
        raise InvalidConfiguration(str(error)) from error
    return grouped, validation


def _guard_aggregate_integers(grouped: dict, weights: dict) -> None:
    # Include every optional coefficient, not just one selected option, because
    # the solver's model validator checks aggregate linear expression domains.
    totals = {
        "duration": sum(item["duration_ms"] for choices in grouped.values() for item in choices),
        "tokens": sum(item["token_cap"] for choices in grouped.values() for item in choices),
        "objective": sum(weights["duration"] * item["duration_ms"] + weights["tokens"] * item["token_cap"]
                         for choices in grouped.values() for item in choices),
    }
    totals.update({resource: sum(item["resources"][resource] for choices in grouped.values() for item in choices)
                   for resource in RESOURCE_NAMES})
    for name, total in totals.items():
        if total > _INTEGER_LIMIT:
            raise InvalidConfiguration(f"Aggregate {name} exceeds the safe solver integer range")


def solve_plan(options: list[dict], budget: dict, profile: str = "flaskr_project",
               catalog: SkillCatalog | None = None) -> dict:
    """Select one external strategy per mandatory Skill and solve its integer plan.

    ``InvalidConfiguration`` means malformed input or a catalog violation.
    Valid but impossible bounds return ``INFEASIBLE``; ``UNKNOWN`` contains no
    plan. Only ``OPTIMAL`` or ``FEASIBLE`` expose solver-backed stage values.
    The existing profile is a single round in fixed serial order with no idle
    gaps. Resource names describe logical capacities, not an OS sandbox.
    """
    if catalog is not None and not isinstance(catalog, SkillCatalog):
        raise InvalidConfiguration("catalog must be a loaded SkillCatalog")
    library = catalog if catalog is not None else SkillCatalog()
    limits = _validate_budget(budget)
    grouped, validation = _validate_options(options, profile, library)
    weights = limits["objective_weights"]
    _guard_aggregate_integers(grouped, weights)
    order = validation["skill_ids"]
    horizon = min(limits["max_makespan_ms"], sum(max(item["duration_ms"] for item in grouped[skill_id])
                                               for skill_id in order))
    model = cp_model.CpModel()
    starts, ends, selected = {}, {}, {}
    intervals_by_resource: dict[str, list] = {name: [] for name in RESOURCE_NAMES}
    demands_by_resource: dict[str, list[int]] = {name: [] for name in RESOURCE_NAMES}
    token_terms, objective_terms = [], []
    for skill_id in order:
        starts[skill_id] = model.new_int_var(0, horizon, f"start:{skill_id}")
        ends[skill_id] = model.new_int_var(0, horizon, f"end:{skill_id}")
        choices = []
        duration_terms = []
        for item in grouped[skill_id]:
            key = (skill_id, item["id"])
            variable = model.new_bool_var(f"select:{skill_id}:{item['id']}")
            selected[key] = variable
            choices.append(variable)
            duration_terms.append(item["duration_ms"] * variable)
            token_terms.append(item["token_cap"] * variable)
            objective_terms.append((weights["duration"] * item["duration_ms"] + weights["tokens"] * item["token_cap"])
                                   * variable)
            interval = model.new_optional_interval_var(starts[skill_id], item["duration_ms"], ends[skill_id],
                                                       variable, f"interval:{skill_id}:{item['id']}")
            for resource in RESOURCE_NAMES:
                if item["resources"][resource]:
                    intervals_by_resource[resource].append(interval)
                    demands_by_resource[resource].append(item["resources"][resource])
        model.add_exactly_one(choices)
        model.add(ends[skill_id] == starts[skill_id] + sum(duration_terms))
        for prerequisite in library.get_contract(skill_id)["prerequisites"]:
            # The catalog boundary already checked all prerequisite IDs/order.
            model.add(starts[skill_id] >= ends[prerequisite])
    model.add(starts[order[0]] == 0)
    for previous, following in zip(order, order[1:]):
        model.add(starts[following] == ends[previous])
    model.add(sum(token_terms) <= limits["max_tokens"])
    makespan = model.new_int_var(0, horizon, "makespan")
    model.add_max_equality(makespan, list(ends.values()))
    model.add(makespan <= limits["max_makespan_ms"])
    for resource in RESOURCE_NAMES:
        model.add_cumulative(intervals_by_resource[resource], demands_by_resource[resource],
                             limits["resources"][resource])
    # The candidate write lock is binary and may never overlap between writers.
    model.add_no_overlap(intervals_by_resource["candidate_write"])
    model.minimize(sum(objective_terms))
    invalid_model = model.validate()
    if invalid_model:
        raise SolverModelError(f"CP-SAT model validation failed: {invalid_model}")
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = limits["solver_time_limit_seconds"]
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = limits["random_seed"]
    status_code = solver.solve(model)
    status = solver.status_name(status_code)
    if status_code == cp_model.MODEL_INVALID:
        raise SolverModelError("CP-SAT returned MODEL_INVALID; no plan is available")
    if status not in {"OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"}:
        raise SolverModelError(f"Unexpected CP-SAT status {status!r}")
    has_solution = status_code in {cp_model.OPTIMAL, cp_model.FEASIBLE}
    result = {
        "schema_version": PLAN_SCHEMA, "profile": profile, "status": status,
        "stages": [], "totals": None, "validation": validation,
        "budget": deepcopy(limits), "execution_verified": False,
        "solver": {
            "name": "OR-Tools CP-SAT", "version": ortools.__version__,
            "status": status, "optimality_proven": status_code == cp_model.OPTIMAL,
            "wall_time_seconds": solver.wall_time, "branches": solver.num_branches,
            "conflicts": solver.num_conflicts,
            "objective_value": solver.objective_value if has_solution else None,
            "best_objective_bound": solver.best_objective_bound if has_solution else None,
            "model_counts": {"exactly_one": len(order), "optional_intervals": len(selected),
                             "cumulative": len(RESOURCE_NAMES), "no_overlap": 1},
            "parameters": {"max_time_in_seconds": limits["solver_time_limit_seconds"],
                           "num_search_workers": 1, "random_seed": limits["random_seed"]},
        },
        "assumptions": {
            "cost_basis": "caller_declared_duration_upper_bounds_and_token_caps",
            "objective": "weighted_declared_duration_ms_plus_token_cap",
            "runtime_optimality_claimed": False, "measured_runtime_guarantee": False,
            "schedule": "single_round_fixed_serial_order_without_idle_gaps",
            "resource_scope": "logical_capacity_only",
            "delivery_gates": "mandatory_execution_evidence_still_required",
        },
    }
    if not has_solution:
        return result
    for skill_id in order:
        chosen = next(item for item in grouped[skill_id] if solver.value(selected[(skill_id, item["id"])]))
        result["stages"].append({
            "id": skill_id, "strategy": chosen["id"],
            "start_ms": solver.value(starts[skill_id]), "end_ms": solver.value(ends[skill_id]),
            "duration_ms": chosen["duration_ms"], "token_cap": chosen["token_cap"],
            "resources": deepcopy(chosen["resources"]), "evidence": deepcopy(chosen["evidence"]),
        })
    # A solver-backed strategy selection must still pass the Skill contract.
    result["validation"] = library.validate_plan(profile, [stage["id"] for stage in result["stages"]])
    total_duration = sum(stage["duration_ms"] for stage in result["stages"])
    total_tokens = sum(stage["token_cap"] for stage in result["stages"])
    result["totals"] = {
        "duration_ms": total_duration, "token_cap": total_tokens, "makespan_ms": solver.value(makespan),
        "objective_value": weights["duration"] * total_duration + weights["tokens"] * total_tokens,
    }
    return result
