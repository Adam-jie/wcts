"""Validate bounded model-proposed work graphs and schedule them with CP-SAT.

Plans are data, never commands. The solver uses declared resource allowances,
not learned performance measurements. Out-degree priority selects among ready
tasks; CP-SAT independently checks dependency, capacity and file-write bounds.
Planned intervals do not assert that the caller implements concurrent execution.
The nine-capability workflow and its delivery gates remain a separate authority.
"""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import PurePosixPath, PureWindowsPath
import re
from typing import Any

import ortools
from ortools.sat.python import cp_model


MAX_WORK_ITEMS = 4
DECLARED_TASK_SECONDS = 180
DECLARED_TASK_TOKEN_CAP = 24_000
_INTEGER_LIMIT = 2 ** 50
_SAFE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SKILLS = {"explore-repository", "repair-code"}
PRIORITY_POLICY = "out_degree_then_critical_path_then_descendants_then_id"
_METRIC_FIELDS = {"in_degree", "out_degree", "descendant_count", "critical_path_declared_seconds"}


def _object(properties: dict) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def _strings(*, nonempty: bool = False) -> dict:
    # Strict structured-output providers do not universally accept uniqueItems.
    # Host validation below always enforces uniqueness before any execution.
    return {"type": "array", "items": {"type": "string", "minLength": 1},
            **({"minItems": 1} if nonempty else {})}


TEAM_PLAN_SCHEMA = _object({
    "request_assessment": _object({
        "supported": {"type": "boolean"},
        "matched_requirement_ids": _strings(nonempty=True),
        "uncovered_requests": _strings(),
        "questions": _strings(),
    }),
    "work_items": {"type": "array", "minItems": 1, "maxItems": MAX_WORK_ITEMS,
                   "items": _object({
                       "id": {"type": "string", "pattern": _SAFE_ID.pattern},
                       "title": {"type": "string", "minLength": 1, "maxLength": 2000},
                       "skill_id": {"type": "string", "enum": sorted(_SKILLS)},
                       "requirement_ids": _strings(nonempty=True),
                       "depends_on": _strings(),
                       "paths": _strings(nonempty=True),
                   })},
})


class TeamPlanError(ValueError):
    """An unsupported request or invalid graph cannot become executable work."""


def _exact(value: Any, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise TeamPlanError(f"{label} must contain exactly {sorted(keys)}")
    return value


def _text(value: Any, label: str, *, maximum: int = 2000) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > maximum or any(ord(char) < 32 for char in value)):
        raise TeamPlanError(f"{label} must be a nonempty trimmed string without control characters")
    return value


def _string_list(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise TeamPlanError(f"{label} must be a list" + (" with at least one item" if nonempty else ""))
    for item in value:
        _text(item, label)
    if len(set(value)) != len(value):
        raise TeamPlanError(f"{label} must not contain duplicate values")
    return value


def _path(value: Any, label: str) -> str:
    value = _text(value, label)
    if ("\\" in value or ":" in value or PurePosixPath(value).is_absolute()
            or PureWindowsPath(value).drive
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise TeamPlanError(f"{label} must be a normalized repository-relative POSIX file path")
    return value


def _authority_set(value: Any, label: str, *, paths: bool = False) -> set[str]:
    # Mapping inputs are useful for a public path -> source snapshot; only keys
    # are read and no source content is copied into the solver result.
    if not isinstance(value, (list, tuple, set, frozenset, dict)):
        raise TeamPlanError(f"{label} must be a collection of registered strings")
    items = list(value)
    for item in items:
        (_path if paths else _text)(item, label)
    if len(set(items)) != len(items):
        raise TeamPlanError(f"{label} contains duplicate registered values")
    return set(items)


def _graph(plan: Any) -> dict:
    _exact(plan, {"request_assessment", "work_items"}, "team plan")
    assessment = _exact(plan["request_assessment"], {
        "supported", "matched_requirement_ids", "uncovered_requests", "questions"}, "request_assessment")
    if type(assessment["supported"]) is not bool:
        raise TeamPlanError("request_assessment.supported must be boolean")
    for name in ("matched_requirement_ids", "uncovered_requests", "questions"):
        _string_list(assessment[name], f"request_assessment.{name}",
                     nonempty=name == "matched_requirement_ids")
    if (not assessment["supported"] or assessment["uncovered_requests"] or assessment["questions"]):
        raise TeamPlanError("Request is unsupported or unresolved; uncovered requests/questions must be resolved "
                            f"before execution: {assessment['uncovered_requests']!r}; {assessment['questions']!r}")
    items = plan["work_items"]
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_WORK_ITEMS:
        raise TeamPlanError(f"work_items must contain 1–{MAX_WORK_ITEMS} tasks")
    by_id = {}
    for item in items:
        _exact(item, {"id", "title", "skill_id", "requirement_ids", "depends_on", "paths"}, "work item")
        identity = _text(item["id"], "work item id", maximum=64)
        if not _SAFE_ID.fullmatch(identity):
            raise TeamPlanError("Work item id must match [a-z][a-z0-9_-]{0,63}")
        if identity in by_id:
            raise TeamPlanError(f"Duplicate work item id: {identity}")
        _text(item["title"], f"{identity}.title")
        if not isinstance(item["skill_id"], str) or item["skill_id"] not in _SKILLS:
            raise TeamPlanError(f"{identity} has an unsupported skill_id")
        _string_list(item["requirement_ids"], f"{identity}.requirement_ids", nonempty=True)
        _string_list(item["depends_on"], f"{identity}.depends_on")
        for path in _string_list(item["paths"], f"{identity}.paths", nonempty=True):
            _path(path, f"{identity}.paths")
        by_id[identity] = item
    visiting, visited = set(), set()

    def visit(identity: str) -> None:
        if identity in visiting:
            raise TeamPlanError("Work item dependency cycle detected")
        if identity in visited:
            return
        visiting.add(identity)
        for dependency in by_id[identity]["depends_on"]:
            if dependency not in by_id:
                raise TeamPlanError(f"Unknown dependency {dependency!r} for {identity}")
            visit(dependency)
        visiting.remove(identity)
        visited.add(identity)

    for identity in by_id:
        visit(identity)
    return by_id


def _coverage(plan: dict, registered: set[str]) -> None:
    if not registered:
        raise TeamPlanError("At least one registered requirement is required")
    matched = set(plan["request_assessment"]["matched_requirement_ids"])
    if matched != registered:
        raise TeamPlanError("matched_requirement_ids must exactly cover registered requirements; "
                            f"missing={sorted(registered - matched)}, unknown={sorted(matched - registered)}")
    repairs = set()
    for item in plan["work_items"]:
        referenced = set(item["requirement_ids"])
        if referenced - registered:
            raise TeamPlanError(f"Unknown requirement IDs in {item['id']}: {sorted(referenced - registered)}")
        if item["skill_id"] == "repair-code":
            repairs.update(referenced)
    if registered - repairs:
        raise TeamPlanError(f"Requirements missing repair-code coverage: {sorted(registered - repairs)}")


def validate_team_plan(plan: dict, requirement_ids, allowed_changes, public_files) -> dict:
    """Return a detached plan after checking against caller-owned authorities.

    The LLM's assessment is checked for explicit unsupported/unresolved requests;
    semantic completeness still requires the existing requirements analysis and
    frozen acceptance tests. Neither this validator nor the solver changes the
    registered requirements, writable scope or mandatory delivery gates.
    """
    _graph(plan)
    registered = _authority_set(requirement_ids, "requirement_ids")
    allowed = _authority_set(allowed_changes, "allowed_changes", paths=True)
    public = _authority_set(public_files, "public_files", paths=True)
    _coverage(plan, registered)
    for item in plan["work_items"]:
        paths = set(item["paths"])
        scope = allowed if item["skill_id"] == "repair-code" else public
        if paths - scope:
            label = "allowed_changes" if item["skill_id"] == "repair-code" else "public_files"
            raise TeamPlanError(f"{item['id']} paths exceed {label}: {sorted(paths - scope)}")
    return deepcopy(plan)


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= _INTEGER_LIMIT:
        raise TeamPlanError(f"{label} must be an integer in [{minimum}, {_INTEGER_LIMIT}]")
    return value


def graph_metrics(plan: dict, *, task_seconds: int = DECLARED_TASK_SECONDS) -> dict[str, dict]:
    """Measure a bounded DAG without inferring measured runtime or speedup.

    Critical-path allowance includes the task itself. Descendants are distinct
    reachable tasks, so a diamond's joined node is counted once, not per path.
    """
    by_id = _graph(plan)
    task_seconds = _integer(task_seconds, "task_seconds", minimum=1)
    if len(by_id) * task_seconds > _INTEGER_LIMIT:
        raise TeamPlanError("Aggregate task allowances exceed the safe solver integer range")
    successors = {identity: set() for identity in by_id}
    for identity, item in by_id.items():
        for dependency in item["depends_on"]:
            successors[dependency].add(identity)
    descendants, critical_paths = {}, {}

    def visit(identity: str) -> None:
        if identity in descendants:
            return
        reachable = set()
        longest = 0
        for child in sorted(successors[identity]):
            visit(child)
            reachable.add(child)
            reachable.update(descendants[child])
            longest = max(longest, critical_paths[child])
        descendants[identity] = reachable
        critical_paths[identity] = task_seconds + longest

    for identity in sorted(by_id):
        visit(identity)
    return {
        identity: {"in_degree": len(by_id[identity]["depends_on"]),
                   "out_degree": len(successors[identity]),
                   "descendant_count": len(descendants[identity]),
                   "critical_path_declared_seconds": critical_paths[identity]}
        for identity in sorted(by_id)
    }


def _priority_key(identity: str, metrics: dict) -> tuple[int, int, int, str]:
    values = metrics[identity]
    return (-values["out_degree"], -values["critical_path_declared_seconds"],
            -values["descendant_count"], identity)


def priority_ready(ids, plan_or_metrics: dict) -> list[str]:
    """Sort caller-established ready IDs; this function does not establish readiness.

    Accept a raw plan, a metrics mapping or ``solve_team_plan``'s result. Higher
    direct out-degree wins, then longer declared critical path, more distinct
    descendants, and ascending ID. Runtime must still wait for actual successful
    predecessors and enforce live model capacity and file-conflict locks.
    """
    if not isinstance(plan_or_metrics, dict):
        raise TeamPlanError("plan_or_metrics must be a plan or graph metrics mapping")
    if (set(plan_or_metrics) == {"request_assessment", "work_items"}
            and isinstance(plan_or_metrics["work_items"], list)):
        metrics = graph_metrics(plan_or_metrics)
    elif plan_or_metrics.get("schema_version") == "testfix.team-integer-plan.v1":
        metrics = plan_or_metrics.get("graph_metrics")
    else:
        metrics = plan_or_metrics
    if not isinstance(metrics, dict):
        raise TeamPlanError("graph_metrics must be a task mapping")
    if not isinstance(ids, (list, tuple, set, frozenset)):
        raise TeamPlanError("ready task IDs must be a collection")
    identities = _string_list(list(ids), "ready task IDs")
    for identity in identities:
        if identity not in metrics:
            raise TeamPlanError(f"Unknown ready task ID: {identity}")
        values = metrics[identity]
        if not isinstance(values, dict) or not _METRIC_FIELDS.issubset(values):
            raise TeamPlanError(f"Invalid graph metrics for {identity}")
        for field in _METRIC_FIELDS:
            _integer(values[field], f"{identity}.{field}",
                     minimum=int(field == "critical_path_declared_seconds"))
    return sorted(identities, key=lambda identity: _priority_key(identity, metrics))


def _priority_topological_order(by_id: dict, metrics: dict) -> list[str]:
    remaining = {identity: set(item["depends_on"]) for identity, item in by_id.items()}
    ordered = []
    while remaining:
        ready = priority_ready([identity for identity, dependencies in remaining.items()
                                if not dependencies], metrics)
        if not ready:
            raise TeamPlanError("Work item dependency cycle detected")
        identity = ready[0]
        ordered.append(identity)
        del remaining[identity]
        for dependencies in remaining.values():
            dependencies.discard(identity)
    return ordered


def solve_team_plan(plan: dict, *, wall_seconds: int, token_limit: int,
                    model_slots: int = 1, task_seconds: int = DECLARED_TASK_SECONDS,
                    task_tokens: int = DECLARED_TASK_TOKEN_CAP,
                    solver_time_limit_seconds: float = 5.0) -> dict:
    """Solve a validated graph with precedence, capacity and per-file write locks.

    Call ``validate_team_plan`` with the registered authorities before calling
    this function. Structural, assessment and coverage checks are repeated here,
    but this function cannot independently establish filesystem permissions.
    ``INFEASIBLE`` and ``UNKNOWN`` never return executable stages or an order.
    """
    by_id = _graph(plan)
    _coverage(plan, set(plan["request_assessment"]["matched_requirement_ids"]))
    wall_seconds = _integer(wall_seconds, "wall_seconds")
    token_limit = _integer(token_limit, "token_limit")
    model_slots = _integer(model_slots, "model_slots")
    task_seconds = _integer(task_seconds, "task_seconds", minimum=1)
    task_tokens = _integer(task_tokens, "task_tokens", minimum=1)
    try:
        time_limit = float(solver_time_limit_seconds) if type(solver_time_limit_seconds) in (int, float) else math.nan
    except (OverflowError, ValueError):
        time_limit = math.nan
    if not math.isfinite(time_limit) or time_limit <= 0:
        raise TeamPlanError("solver_time_limit_seconds must be finite and positive")
    total_duration, total_tokens = len(by_id) * task_seconds, len(by_id) * task_tokens
    if max(total_duration, total_tokens) > _INTEGER_LIMIT:
        raise TeamPlanError("Aggregate task allowances exceed the safe solver integer range")
    horizon = min(wall_seconds, total_duration)
    model = cp_model.CpModel()
    metrics = graph_metrics(plan, task_seconds=task_seconds)
    starts, ends, intervals, task_intervals = {}, {}, [], {}
    for identity, item in by_id.items():
        starts[identity] = model.new_int_var(0, horizon, f"start:{identity}")
        ends[identity] = model.new_int_var(0, horizon, f"end:{identity}")
        interval = model.new_interval_var(starts[identity], task_seconds, ends[identity], f"task:{identity}")
        intervals.append(interval)
        task_intervals[identity] = interval
    for identity, item in by_id.items():
        for dependency in item["depends_on"]:
            model.add(starts[identity] >= ends[dependency])
    model.add_cumulative(intervals, [1] * len(intervals), model_slots)
    write_conflicts = []
    writers = sorted(identity for identity, item in by_id.items() if item["skill_id"] == "repair-code")
    for index, left in enumerate(writers):
        for right in writers[index + 1:]:
            shared_paths = sorted(set(by_id[left]["paths"]) & set(by_id[right]["paths"]))
            if shared_paths:
                model.add_no_overlap([task_intervals[left], task_intervals[right]])
                write_conflicts.append({"tasks": [left, right], "paths": shared_paths})
    # Express the fixed declaration as a solver constraint, including impossible
    # token allowances. It is not a post-hoc label for a greedy ordering.
    model.add(total_tokens <= token_limit)
    makespan = model.new_int_var(0, horizon, "makespan")
    model.add_max_equality(makespan, list(ends.values()))
    model.minimize(makespan)
    problem = model.validate()
    if problem:
        raise RuntimeError(f"CP-SAT team model is invalid: {problem}")
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    status_code = solver.solve(model)
    status = solver.status_name(status_code)
    if status not in {"OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"}:
        raise RuntimeError(f"Unexpected CP-SAT team status: {status}")
    executable = status in {"OPTIMAL", "FEASIBLE"}
    result = {
        "schema_version": "testfix.team-integer-plan.v1", "status": status,
        "order": [], "stages": [], "execution_verified": False,
        "graph_metrics": metrics,
        "priority": {"policy": PRIORITY_POLICY,
                     "sort_fields": ["out_degree_desc", "critical_path_declared_seconds_desc",
                                     "descendant_count_desc", "id_asc"],
                     "keys": {identity: list(_priority_key(identity, metrics)) for identity in sorted(by_id)},
                     "scope": "ready_tasks_only; precedence_and_live_resource_checks_still_required"},
        "budget": {"wall_seconds": wall_seconds, "token_limit": token_limit, "model_slots": model_slots},
        "estimates": {"basis": "declared_allowances_not_measured_performance",
                      "task_count": len(by_id), "declared_task_seconds": task_seconds,
                      "declared_task_token_cap": task_tokens, "total_tokens": total_tokens,
                      "total_serial_seconds": total_duration, "makespan_seconds": None,
                      "measured_runtime_guarantee": False},
        "solver": {"name": "OR-Tools CP-SAT", "version": ortools.__version__, "status": status,
                   "optimality_proven": status == "OPTIMAL", "wall_time_seconds": solver.wall_time,
                   "constraints": {"precedence": sum(len(item["depends_on"]) for item in by_id.values()),
                                   "model_cumulative": 1, "candidate_write_conflicts": write_conflicts,
                                   "token_budget": 1}},
        "execution_policy": "priority_ready_dispatch_with_dependency_and_file_locks; concurrency_is_not_asserted",
        "delivery_gates": "existing_nine_capability_workflow_and_frozen_acceptance_required",
    }
    if not executable:
        return result
    for identity in _priority_topological_order(by_id, metrics):
        item = by_id[identity]
        result["stages"].append({
            **deepcopy(item), "start_seconds": solver.value(starts[identity]),
            "end_seconds": solver.value(ends[identity]), "duration_seconds": task_seconds,
            "token_cap": task_tokens,
            "graph_metrics": deepcopy(metrics[identity]),
            "priority_key": list(_priority_key(identity, metrics)),
            "resources": {"model_slots": 1, "candidate_write": int(item["skill_id"] == "repair-code")},
        })
    result["order"] = [item["id"] for item in result["stages"]]
    result["estimates"]["makespan_seconds"] = solver.value(makespan)
    return result
