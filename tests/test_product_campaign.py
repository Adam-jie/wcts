"""Runner invariants; these fixtures do not certify a product or physical device."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from testfix import product_campaign as campaign
from testfix.product_campaign import (
    CampaignContract, CampaignNotExecuted, MINIMUM_PRODUCT_DIMENSIONS, Observation,
    run_campaign, thaw_json,
)


DIGEST = "a" * 64


def contract(rounds=3, full=False):
    dimensions = MINIMUM_PRODUCT_DIMENSIONS if full else ["functional"]
    return {
        "name": "Frozen product fixture", "product_type": "web", "candidate_hash": DIGEST,
        "rounds": rounds, "seed": 17, "max_duration_s": 120.0,
        "cases": [{"id": dimension, "dimension": dimension, "critical": True, "timeout_s": 10.0,
                   "rows": [{"id": "normal", "input": {"values": [1, 2]}, "expected": {"ok": True}},
                            {"id": "boundary", "input": {"values": []}, "expected": {"ok": False}}]}
                  for dimension in dimensions],
        "required_dimensions": [{"id": dimension, "required_case_ids": [dimension],
                                 "min_rounds": 2, "min_data_rows": 2} for dimension in dimensions],
    }


def expected(attempt):
    return thaw_json(attempt.expected)


def run(value, executor, tmp_path, **options):
    return run_campaign(value, executor, output_dir=tmp_path, **options)


def test_cartesian_schedule_seed_coverage_and_independent_durable_receipts(tmp_path):
    value = contract()
    first = run(value, expected, tmp_path, candidate_probe=lambda: DIGEST)
    second = run(value, expected, tmp_path, candidate_probe=lambda: DIGEST)
    assert first["schedule_hash"] == second["schedule_hash"]
    assert first["run_id"] != second["run_id"]
    assert first["counts"]["passed"] == 6
    assert first["coverage"] == {
        "attempts": {"executed": 6, "planned": 6, "ratio": 1.0},
        "unique_cases": {"executed": 1, "planned": 1, "ratio": 1.0},
        "unique_data_rows": {"executed": 2, "planned": 2, "ratio": 1.0},
    }
    for index in range(1, 4):
        assert {item["data_id"] for item in first["attempts"] if item["round_index"] == index} == {"normal", "boundary"}
    evidence = Path(first["evidence_dir"])
    assert json.loads((evidence / "contract.json").read_text(encoding="utf-8"))["cases"] == value["cases"]
    assert json.loads((evidence / "report.json").read_text(encoding="utf-8")) == first
    assert [json.loads(line) for line in (evidence / "attempts.jsonl").read_text(encoding="utf-8").splitlines()] == first["attempts"]


def test_failure_in_first_round_survives_later_passes_and_marks_flaky(tmp_path):
    def callback(attempt):
        if attempt.round_index == 1 and attempt.data_id == "normal":
            return {"ok": False}
        return expected(attempt)
    report = run(contract(), callback, tmp_path, candidate_probe=lambda: DIGEST)
    assert report["counts"]["failed"] == 1
    assert report["counts"]["passed"] == 5
    assert report["first_failure_round"] == {"functional": 1}
    assert report["flaky_data_rows"] == [{"case_id": "functional", "data_id": "normal", "statuses": ["failed", "passed", "passed"]}]
    assert report["critical_nonpass_attempts"] == 1
    assert not report["tested_scope_passed"] and not report["product_accepted"]


@pytest.mark.parametrize("wanted,actual", [(True, 1), (1, True), (1, 1.0), ({"x": [False]}, {"x": [0]})])
def test_oracle_comparison_does_not_equate_json_types(tmp_path, wanted, actual):
    value = contract(1)
    value["cases"][0]["rows"] = [{"id": "type", "input": None, "expected": wanted}]
    report = run(value, lambda _: actual, tmp_path)
    assert report["counts"]["failed"] == 1
    assert report["counts"]["passed"] == 0


def test_callback_cannot_mutate_snapshot_or_original_oracle(tmp_path):
    value = contract()
    original = copy.deepcopy(value)
    snapshots = []
    def callback(attempt):
        snapshots.append(attempt)
        with pytest.raises(TypeError):
            attempt.input_data["replacement"] = "bad"
        with pytest.raises(TypeError):
            attempt.expected["ok"] = not attempt.expected["ok"]
        with pytest.raises(AttributeError):
            attempt.input_data["values"].append(3)
        # A malicious closure still cannot mutate the detached owned contract.
        value["cases"][0]["rows"][0]["expected"]["ok"] = "weakened"
        return expected(attempt)
    report = run(value, callback, tmp_path)
    assert report["tested_scope_passed"]
    assert report["contract_intact"] and report["contract_hash"] == report["contract_hash_after"]
    assert len({id(item.input_data) for item in snapshots}) == 6
    sealed = json.loads((Path(report["evidence_dir"]) / "contract.json").read_text(encoding="utf-8"))
    assert sealed["cases"] == original["cases"]


def test_all_selected_checks_passing_is_distinct_from_product_acceptance(tmp_path):
    report = run(contract(), expected, tmp_path, candidate_probe=lambda: DIGEST)
    assert report["tested_scope_passed"] and not report["product_accepted"]
    dimensions = {item["id"]: item for item in report["dimensions"]}
    assert dimensions["functional"]["status"] == "passed"
    assert dimensions["security"]["status"] == "not_evaluated"
    assert len(dimensions) == len(MINIMUM_PRODUCT_DIMENSIONS)


def test_product_gate_requires_candidate_probe_and_every_fixed_dimension(tmp_path):
    value = contract(full=True)
    unbound = run(value, expected, tmp_path)
    bound = run(value, expected, tmp_path, candidate_probe=lambda: DIGEST)
    assert unbound["tested_scope_passed"] and not unbound["product_accepted"]
    assert "candidate_identity_not_verified" in unbound["acceptance_blockers"]
    assert bound["product_accepted"]
    assert all(item["status"] == "passed" for item in bound["dimensions"])


def test_dimension_minima_cannot_be_satisfied_by_a_single_labeled_probe(tmp_path):
    value = contract(rounds=1)
    value["required_dimensions"] = [{"id": "functional", "required_case_ids": ["functional", "missing"],
                                     "min_rounds": 2, "min_data_rows": 3, "min_duration_s": 10.0}]
    report = run(value, expected, tmp_path, candidate_probe=lambda: DIGEST)
    dimension = report["dimensions"][0]
    assert dimension["status"] == "not_evaluated"
    assert dimension["missing_case_ids"] == ["missing"]
    assert {"insufficient_planned_rounds", "insufficient_data_or_rounds:functional", "insufficient_execution_duration"} <= set(dimension["reasons"])
    assert not report["product_accepted"]


@pytest.mark.parametrize("when,expected_calls,first_status", [(1, 0, "not_executed"), (2, 1, "invalidated")])
def test_candidate_change_aborts_without_mixing_revisions(tmp_path, when, expected_calls, first_status):
    probes = 0
    executions = []
    def probe():
        nonlocal probes
        probes += 1
        return "b" * 64 if probes >= when else DIGEST
    def callback(attempt):
        executions.append(attempt)
        return expected(attempt)
    report = run(contract(), callback, tmp_path, candidate_probe=probe)
    assert len(executions) == expected_calls
    assert report["attempts"][0]["status"] == first_status
    assert all(item["status"] == "not_executed" for item in report["attempts"][1:])
    assert report["candidate_identity"] == "changed" and not report["product_accepted"]


def test_adapter_failures_timeout_and_unsupported_remain_distinct(tmp_path):
    value = contract(1)
    value["cases"][0]["rows"] = [{"id": name, "input": None, "expected": None}
                                  for name in ["exception", "timeout", "unsupported"]]
    def callback(attempt):
        if attempt.data_id == "exception":
            raise RuntimeError("secret-must-not-appear")
        if attempt.data_id == "timeout":
            raise TimeoutError()
        raise CampaignNotExecuted()
    report = run(value, callback, tmp_path, candidate_probe=lambda: DIGEST)
    assert report["counts"] == {"passed": 0, "failed": 0, "error": 1, "timeout": 1, "not_executed": 1, "invalidated": 0}
    assert "secret-must-not-appear" not in json.dumps(report)
    assert report["coverage"]["attempts"]["executed"] == 2


def test_attempt_timeout_and_wall_budget_prevent_further_dispatch(tmp_path, monkeypatch):
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr(campaign.time, "monotonic", lambda: clock.value)
    value = contract()
    value["max_duration_s"] = 0.5
    calls = []
    def callback(attempt):
        calls.append(attempt)
        assert attempt.deadline_monotonic == 0.5
        clock.value = 0.6
        return expected(attempt)
    report = run(value, callback, tmp_path)
    assert len(calls) == 1
    assert report["counts"]["timeout"] == 1 and report["counts"]["not_executed"] == 5
    assert report["budget_exceeded"]
    assert "campaign_budget_exceeded" in report["acceptance_blockers"]


def test_post_execution_identity_probe_is_included_in_wall_budget(tmp_path, monkeypatch):
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr(campaign.time, "monotonic", lambda: clock.value)
    value = contract(1)
    value["max_duration_s"] = 0.5
    probes = 0
    def probe():
        nonlocal probes
        probes += 1
        if probes == 2:
            clock.value = 0.6
        return DIGEST
    report = run(value, expected, tmp_path, candidate_probe=probe)
    assert report["attempts"][0]["status"] == "timeout"
    assert report["attempts"][0]["reason"] == "campaign_deadline_exceeded"
    assert report["budget_exceeded"] and not report["product_accepted"]


def test_unreached_candidate_probe_does_not_claim_identity_verification(tmp_path, monkeypatch):
    ticks = iter([0.0] + [1.0] * 100)
    monkeypatch.setattr(campaign.time, "monotonic", lambda: next(ticks))
    value = contract(1)
    value["max_duration_s"] = 0.5
    probe_calls = []
    report = run(value, expected, tmp_path, candidate_probe=lambda: probe_calls.append(True) or DIGEST)
    assert not probe_calls
    assert report["candidate_identity"] == "not_verified"
    assert report["counts"]["not_executed"] == 2


def test_late_adapter_result_is_timeout_even_when_it_returns_expected_json(tmp_path, monkeypatch):
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr(campaign.time, "monotonic", lambda: clock.value)
    calls = 0
    def callback(attempt):
        nonlocal calls
        calls += 1
        clock.value += 11.0 if calls == 1 else 0.1
        return expected(attempt)
    report = run(contract(), callback, tmp_path, candidate_probe=lambda: DIGEST)
    assert report["counts"]["timeout"] == 1 and report["counts"]["passed"] == 5
    assert report["attempts"][0]["reason"] == "attempt_deadline_exceeded"
    assert not report["budget_exceeded"] and not report["tested_scope_passed"]


def test_duration_quantiles_use_attempt_durations_and_gate_active_time(tmp_path, monkeypatch):
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr(campaign.time, "monotonic", lambda: clock.value)
    value = contract(4)
    value["cases"][0]["rows"] = [value["cases"][0]["rows"][0]]
    value["required_dimensions"][0].update(min_data_rows=1, min_duration_s=20.0)
    def callback(attempt):
        clock.value += attempt.round_index
        return Observation(expected(attempt), {"fixture": True})
    report = run(value, callback, tmp_path, candidate_probe=lambda: DIGEST)
    assert report["duration_metrics_s"] == {"p50": 2.5, "p95": 3.85}
    assert report["dimensions"][0]["execution_duration_s"] == 10.0
    assert report["dimensions"][0]["status"] == "not_evaluated"


@pytest.mark.parametrize("actual", [float("nan"), {1: "bad-json-key"}, object()])
def test_adapter_cannot_return_non_json_or_self_certify_a_pass(tmp_path, actual):
    report = run(contract(1), lambda _: actual, tmp_path)
    assert report["counts"]["error"] == 2
    assert not report["tested_scope_passed"]


def test_evidence_paths_never_overwrite_an_existing_run(tmp_path, monkeypatch):
    monkeypatch.setattr(campaign.uuid, "uuid4", lambda: SimpleNamespace(hex="f" * 32))
    first = run(contract(1), expected, tmp_path)
    saved_path = Path(first["evidence_dir"]) / "report.json"
    saved = saved_path.read_bytes()
    with pytest.raises(FileExistsError):
        run(contract(1), lambda _: "bad", tmp_path)
    assert saved_path.read_bytes() == saved


def test_contract_rejects_ambiguous_ids_and_unbounded_plans():
    value = contract()
    value["cases"].append(copy.deepcopy(value["cases"][0]))
    with pytest.raises(ValidationError):
        CampaignContract.model_validate(value)
    value = contract(100)
    value["cases"][0]["rows"] = [{"id": f"row{index}", "input": None, "expected": None} for index in range(101)]
    with pytest.raises(ValidationError):
        CampaignContract.model_validate(value)


def test_adapters_cannot_override_runner_verdict_with_success_metadata(tmp_path):
    report = run(contract(1), lambda _: Observation({"passed": True}, {"verdict": "passed"}), tmp_path)
    assert report["counts"]["failed"] == 2
    assert not report["tested_scope_passed"]
