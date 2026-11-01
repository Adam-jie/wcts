"""Frozen, repeated product checks executed by application-owned adapters.

This module never executes a model-provided command. An adapter must enforce its
own I/O/process deadline: Python cannot forcibly interrupt an arbitrary callback.
The wall budget prevents further dispatch and marks late results as timeouts.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import re
import time
from types import MappingProxyType
from typing import Any, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


MAX_ATTEMPTS = 10_000
MINIMUM_PRODUCT_DIMENSIONS = (
    "functional", "regression", "data_integrity", "stability", "concurrency",
    "performance", "recovery", "security", "compatibility", "delivery",
)
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_STATUSES = ("passed", "failed", "error", "timeout", "not_executed", "invalidated")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _identifier(value: str) -> str:
    if not _ID.fullmatch(value):
        raise ValueError("Use a short ASCII identifier")
    return value


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DataVector(_FrozenModel):
    id: str
    input: JsonValue
    expected: JsonValue

    _id = field_validator("id")(_identifier)

    @model_validator(mode="after")
    def finite_bounded_json(self):
        if len(_canonical(self.model_dump()).encode("utf-8")) > 65_536:
            raise ValueError("Each data vector must fit in 64 KiB of JSON")
        return self


class ProductCase(_FrozenModel):
    id: str
    dimension: str
    critical: bool = True
    timeout_s: float = Field(default=10.0, ge=0.001, le=120)
    rows: list[DataVector] = Field(min_length=1, max_length=200)

    _ids = field_validator("id", "dimension")(_identifier)

    @model_validator(mode="after")
    def unique_rows(self):
        if len({row.id for row in self.rows}) != len(self.rows):
            raise ValueError("Data IDs must be unique within each case")
        return self


class DimensionRule(_FrozenModel):
    """An absent rule target remains untested rather than silently disappearing."""
    id: str
    required_case_ids: list[str] = Field(default_factory=list, max_length=200)
    min_rounds: int = Field(default=1, ge=1, le=100)
    min_data_rows: int = Field(default=1, ge=1, le=200)
    min_duration_s: float = Field(default=0.0, ge=0, le=3600)

    _id = field_validator("id")(_identifier)

    @field_validator("required_case_ids")
    @classmethod
    def identifiers(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Required case IDs must be unique")
        return [_identifier(value) for value in values]


class CampaignContract(_FrozenModel):
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=160)
    product_type: Literal["web", "software", "mcu", "mpu"]
    candidate_hash: str
    rounds: int = Field(ge=1, le=100)
    seed: int = Field(default=0, ge=0, le=2**63 - 1)
    max_duration_s: float = Field(default=120.0, ge=0.001, le=3600)
    cases: list[ProductCase] = Field(min_length=1, max_length=200)
    required_dimensions: list[DimensionRule] = Field(min_length=1, max_length=32)

    @field_validator("candidate_hash")
    @classmethod
    def sha256(cls, value):
        if not _SHA256.fullmatch(value):
            raise ValueError("candidate_hash must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def bounded_unique_plan(self):
        cases = {case.id: case for case in self.cases}
        if len(cases) != len(self.cases):
            raise ValueError("Case IDs must be unique")
        if len({rule.id for rule in self.required_dimensions}) != len(self.required_dimensions):
            raise ValueError("Dimension IDs must be unique")
        if self.rounds * sum(len(case.rows) for case in self.cases) > MAX_ATTEMPTS:
            raise ValueError(f"Campaigns are limited to {MAX_ATTEMPTS} planned attempts")
        for rule in self.required_dimensions:
            for case_id in rule.required_case_ids:
                if case_id in cases and cases[case_id].dimension != rule.id:
                    raise ValueError("A required case must belong to its declared dimension")
        return self


def freeze_json(value: Any) -> Any:
    """A detached recursively immutable JSON value for an adapter invocation."""
    if isinstance(value, dict):
        return MappingProxyType({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze_json(item) for item in value)
    return value


def thaw_json(value: Any) -> Any:
    """Turn an adapter snapshot back into JSON containers when an API needs them."""
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


def strict_equal(expected: Any, actual: Any) -> bool:
    """JSON equality with distinct bool/int/float types, including nested values."""
    expected = thaw_json(expected)
    actual = thaw_json(actual)
    if type(expected) is not type(actual):
        return False
    if isinstance(expected, dict):
        return expected.keys() == actual.keys() and all(strict_equal(expected[k], actual[k]) for k in expected)
    if isinstance(expected, list):
        return len(expected) == len(actual) and all(strict_equal(a, b) for a, b in zip(expected, actual))
    return expected == actual


def _validate_json(value: Any) -> None:
    if type(value) in (str, bool, int, type(None)):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _validate_json(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _validate_json(item)
        return
    raise ValueError("Adapter observations must contain finite JSON values with string object keys")


@dataclass(frozen=True)
class AttemptInput:
    round_index: int
    case_id: str
    data_id: str
    dimension: str
    critical: bool
    input_data: Any
    expected: Any
    timeout_s: float
    deadline_monotonic: float


@dataclass(frozen=True)
class Observation:
    actual: Any
    metadata: Any = None


class CampaignNotExecuted(Exception):
    """The adapter did not execute this check (for example an unsupported tool)."""


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    location = (len(ordered) - 1) * quantile
    low, high = math.floor(location), math.ceil(location)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (location - low), 6)


def _dimensions(contract: CampaignContract, receipts: list[dict]) -> list[dict]:
    cases = {case.id: case for case in contract.cases}
    output = []
    rules = list(contract.required_dimensions)
    declared = {rule.id for rule in rules}
    rules.extend(DimensionRule(id=dimension) for dimension in MINIMUM_PRODUCT_DIMENSIONS if dimension not in declared)
    for rule in rules:
        required = rule.required_case_ids or [case.id for case in contract.cases if case.dimension == rule.id]
        missing = [case_id for case_id in required if case_id not in cases]
        selected = [item for item in receipts if item["case_id"] in required]
        reasons = []
        execution_duration = sum(item["duration_s"] for item in selected if item["status"] != "not_executed")
        if not required:
            reasons.append("no_cases_declared")
        if missing:
            reasons.append("missing_required_cases")
        if contract.rounds < rule.min_rounds:
            reasons.append("insufficient_planned_rounds")
        if execution_duration < rule.min_duration_s:
            reasons.append("insufficient_execution_duration")
        for case_id in required:
            if case_id not in cases:
                continue
            complete_rows = set()
            for row in cases[case_id].rows:
                attempted_rounds = {item["round_index"] for item in selected
                                    if item["case_id"] == case_id and item["data_id"] == row.id
                                    and item["status"] != "not_executed"}
                if len(attempted_rounds) >= rule.min_rounds:
                    complete_rows.add(row.id)
            if len(complete_rows) < rule.min_data_rows:
                reasons.append(f"insufficient_data_or_rounds:{case_id}")
        failed = any(item["status"] in {"failed", "error", "timeout", "invalidated"} for item in selected)
        unexecuted = any(item["status"] == "not_executed" for item in selected)
        status = "failed" if failed else "not_evaluated" if reasons or unexecuted else "passed"
        output.append({"id": rule.id, "status": status, "required_case_ids": required,
                       "min_rounds": rule.min_rounds, "min_data_rows": rule.min_data_rows,
                       "min_duration_s": rule.min_duration_s, "execution_duration_s": round(execution_duration, 6),
                       "missing_case_ids": missing, "reasons": sorted(set(reasons))})
    return output


def run_campaign(
    contract: CampaignContract | dict,
    executor: Callable[[AttemptInput], Any],
    *,
    output_dir: str | Path,
    candidate_probe: Callable[[], str] | None = None,
) -> dict:
    """Run every planned round/case/data tuple once, without success-overwrite retries.

    ``candidate_probe`` must independently hash the actual candidate before/after
    each dispatch. Without it checks still run, but product acceptance is denied.
    Outputs use an exclusive, fresh run directory; existing evidence is untouched.
    """
    submitted = contract.model_dump(mode="json") if isinstance(contract, CampaignContract) else contract
    # Parse serialized JSON to detach all nested containers from the caller.
    owned = CampaignContract.model_validate(json.loads(_canonical(submitted)))
    payload = owned.model_dump(mode="json")
    contract_hash = _digest(payload)
    dataset_hash = _digest([{ "case_id": case.id, "rows": [{"id": row.id, "input": row.input}
                                            for row in case.rows]} for case in owned.cases])
    oracle_hash = _digest([{ "case_id": case.id, "rows": [{"id": row.id, "expected": row.expected}
                                           for row in case.rows]} for case in owned.cases])
    rng = random.Random(owned.seed)
    schedule = []
    for round_index in range(1, owned.rounds + 1):
        pairs = [(case.id, row.id) for case in owned.cases for row in case.rows]
        rng.shuffle(pairs)
        schedule.extend({"round_index": round_index, "case_id": case_id, "data_id": data_id}
                        for case_id, data_id in pairs)
    schedule_hash = _digest(schedule)
    run_id = uuid.uuid4().hex
    destination = Path(output_dir) / run_id
    destination.mkdir(parents=True, exist_ok=False)
    for filename, content in (("contract.json", payload), ("schedule.json", schedule)):
        with (destination / filename).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    start = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    campaign_deadline = start + owned.max_duration_s
    receipts = []
    cases = {case.id: case for case in owned.cases}
    rows = {(case.id, row.id): row for case in owned.cases for row in case.rows}
    identity = "not_verified"
    aborted_reason = None

    def check_candidate() -> bool:
        nonlocal identity, aborted_reason
        if candidate_probe is None:
            return True
        try:
            current = candidate_probe()
        except Exception:
            identity, aborted_reason = "probe_error", "candidate_probe_error"
            return False
        if type(current) is not str or current != owned.candidate_hash:
            identity, aborted_reason = "changed", "candidate_changed"
            return False
        identity = "verified"
        return True

    with (destination / "attempts.jsonl").open("x", encoding="utf-8") as stream:
        for sequence, item in enumerate(schedule, 1):
            case = cases[item["case_id"]]
            row = rows[(case.id, item["data_id"])]
            receipt = {"sequence": sequence, **item, "dimension": case.dimension, "critical": case.critical,
                       "status": "not_executed", "reason": None, "duration_s": 0.0,
                       "expected": json.loads(_canonical(row.expected)), "actual": None, "metadata": None}
            if aborted_reason:
                receipt["reason"] = aborted_reason
            elif time.monotonic() >= campaign_deadline:
                receipt["reason"] = "campaign_budget_exhausted"
            elif not check_candidate():
                receipt["reason"] = aborted_reason
            elif time.monotonic() >= campaign_deadline:
                receipt["reason"] = "campaign_budget_exhausted"
            else:
                attempt_start = time.monotonic()
                deadline = min(campaign_deadline, attempt_start + case.timeout_s)
                invocation = AttemptInput(
                    item["round_index"], case.id, row.id, case.dimension, case.critical,
                    freeze_json(json.loads(_canonical(row.input))),
                    freeze_json(json.loads(_canonical(row.expected))), case.timeout_s, deadline,
                )
                try:
                    observation = executor(invocation)
                    if not isinstance(observation, Observation):
                        observation = Observation(observation)
                    # Reject arbitrary objects/nonfinite numbers and detach mutable results.
                    actual = thaw_json(observation.actual)
                    metadata = thaw_json(observation.metadata)
                    _validate_json(actual)
                    _validate_json(metadata)
                    encoded = _canonical({"actual": actual, "metadata": metadata})
                    if len(encoded.encode("utf-8")) > 262_144:
                        raise ValueError("Adapter evidence exceeds 256 KiB")
                    saved = json.loads(encoded)
                    receipt.update(saved)
                    receipt["status"] = "passed" if strict_equal(row.expected, saved["actual"]) else "failed"
                    if receipt["status"] == "failed":
                        receipt["reason"] = "expected_actual_mismatch"
                except CampaignNotExecuted:
                    receipt["status"], receipt["reason"] = "not_executed", "adapter_not_executed"
                except TimeoutError:
                    receipt["status"], receipt["reason"] = "timeout", "adapter_timeout"
                except Exception as error:
                    receipt["status"], receipt["reason"] = "error", "adapter_exception"
                    receipt["metadata"] = {"exception_type": type(error).__name__}
                finished = time.monotonic()
                receipt["duration_s"] = round(finished - attempt_start, 6)
                if finished > deadline and receipt["status"] != "not_executed":
                    receipt["status"] = "timeout"
                    receipt["reason"] = "campaign_deadline_exceeded" if finished > campaign_deadline else "attempt_deadline_exceeded"
                if not check_candidate():
                    receipt["observed_status"] = receipt["status"]
                    receipt["status"], receipt["reason"] = "invalidated", aborted_reason
                elif time.monotonic() > campaign_deadline and receipt["status"] != "not_executed":
                    receipt["status"], receipt["reason"] = "timeout", "campaign_deadline_exceeded"
            receipts.append(receipt)
            stream.write(json.dumps(receipt, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()

    intact = _digest(owned.model_dump(mode="json")) == contract_hash
    counts = {status: sum(item["status"] == status for item in receipts) for status in _STATUSES}
    attempted = [item for item in receipts if item["status"] != "not_executed"]
    attempted_cases = {item["case_id"] for item in attempted}
    attempted_data = {(item["case_id"], item["data_id"]) for item in attempted}
    groups = defaultdict(list)
    for item in receipts:
        groups[(item["case_id"], item["data_id"])].append(item)
    flaky = [{"case_id": key[0], "data_id": key[1],
              "statuses": [item["status"] for item in values]}
             for key, values in groups.items()
             if any(item["status"] == "passed" for item in values)
             and any(item["status"] in {"failed", "error", "timeout"} for item in values)]
    first_failures = {}
    for item in receipts:
        if item["status"] in {"failed", "error", "timeout", "invalidated"}:
            first_failures.setdefault(item["case_id"], item["round_index"])
    dimensions = _dimensions(owned, receipts)
    elapsed = time.monotonic() - start
    budget_exceeded = elapsed > owned.max_duration_s
    reasons = []
    if identity != "verified":
        reasons.append("candidate_identity_not_verified")
    if not intact:
        reasons.append("contract_integrity_failed")
    if counts["passed"] != len(schedule):
        reasons.append("not_all_attempts_passed")
    if any(item["status"] != "passed" for item in dimensions):
        reasons.append("required_dimensions_not_passed")
    if budget_exceeded:
        reasons.append("campaign_budget_exceeded")
    durations = [item["duration_s"] for item in attempted]
    total_data = sum(len(case.rows) for case in owned.cases)
    critical_failures = sum(item["critical"] and item["status"] != "passed" for item in receipts)
    report = {
        "schema_version": 1, "run_id": run_id, "name": owned.name, "product_type": owned.product_type,
        "started_at": started_at, "duration_s": round(elapsed, 6), "budget_exceeded": budget_exceeded,
        "candidate_hash": owned.candidate_hash, "candidate_identity": identity,
        "contract_hash": contract_hash, "contract_hash_after": _digest(owned.model_dump(mode="json")),
        "contract_intact": intact, "dataset_hash": dataset_hash, "oracle_hash": oracle_hash,
        "schedule_hash": schedule_hash, "seed": owned.seed,
        "tested_scope_passed": counts["passed"] == len(schedule) and intact,
        "product_accepted": not reasons, "acceptance_blockers": reasons,
        "product_policy": {"id": "testfix-product-v1", "minimum_dimensions": list(MINIMUM_PRODUCT_DIMENSIONS)},
        "counts": counts, "critical_nonpass_attempts": critical_failures,
        "coverage": {
            "attempts": {"executed": len(attempted), "planned": len(schedule), "ratio": len(attempted) / len(schedule)},
            "unique_cases": {"executed": len(attempted_cases), "planned": len(owned.cases), "ratio": len(attempted_cases) / len(owned.cases)},
            "unique_data_rows": {"executed": len(attempted_data), "planned": total_data, "ratio": len(attempted_data) / total_data},
        },
        "rounds": [{"round_index": index, "counts": dict(Counter(item["status"] for item in receipts if item["round_index"] == index))}
                   for index in range(1, owned.rounds + 1)],
        "dimensions": dimensions, "first_failure_round": first_failures, "flaky_data_rows": flaky,
        "duration_metrics_s": {"p50": _percentile(durations, .5), "p95": _percentile(durations, .95)},
        "attempts": receipts, "evidence_dir": str(destination.resolve()),
        "execution_limit": "Adapters enforce I/O/process deadlines; this runner cannot forcibly interrupt arbitrary Python callbacks.",
        "identity_limit": "candidate_probe proves only the bytes/resources included by its adapter; running images, configuration and databases require explicit binding.",
    }
    with (destination / "report.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return report
