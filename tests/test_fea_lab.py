"""Public-evidence boundaries and fixed-library probe validation."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from testfix import fea_lab
from testfix.config import Settings


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("patch", [
    {"operation": "__import__"}, {"table": "tbl; DROP TABLE x"},
    {"columns": ["*;print(1)"]}, {"clauses": ["a DESC;print(1)"]},
    {"clauses": ["__import__('os')"]}, {"columns": []},
    {"columns": ["a"] * 6}, {"source": "evaluator-only"},
    {"operation": "sql"}, {"table": "../secret"},
])
def test_probe_rejects_execution_paths_and_unbounded_input(patch):
    with pytest.raises(ValidationError):
        fea_lab.FEAProbeRequest(**patch)


def test_probe_normalizes_constrained_clauses():
    value = fea_lab.FEAProbeRequest(columns=["orders.total"], table="orders", clauses=["orders.total desc"])
    assert value.clauses == ["orders.total DESC"]


def test_evidence_reader_rejects_path_escape(tmp_path):
    with pytest.raises(ValueError, match="escaped"):
        fea_lab._safe(tmp_path, tmp_path.parent / "secret.env", exists=False)


@pytest.fixture
def real_evidence():
    if not (ROOT / fea_lab.PILOT / fea_lab.FINAL / "retest-summary.json").is_file():
        pytest.skip("Fixed local experiment is not distributed with public source")
    return Settings(root=ROOT)


def test_projection_reads_only_public_model_artifacts(real_evidence, monkeypatch):
    observed = []
    original = fea_lab._read

    def read(root, path, limit=4_000_000):
        observed.append(Path(path).as_posix())
        return original(root, path, limit)

    monkeypatch.setattr(fea_lab, "_read", read)
    value = fea_lab.demo(real_evidence)
    assert [(s["passed"], s["total"]) for s in value["generated_tests"]["stages"]] == [(1, 9), (2, 9), (9, 9)]
    assert len(value["generated_tests"]["cases"]) == 9
    assert value["generated_tests"]["frozen_sha256"] == fea_lab.TEST_SHA
    assert all(not any(part in path for part in ("evaluator-only", "/model_calls/", "testpatch", "gold")) for path in observed)
    text = json.dumps(value, ensure_ascii=False)
    assert '"actual_identity_verified": false' in text
    assert all(key not in value for key in ("model_calls", "request", "raw_trace", "evaluation_command"))
    assert value["stages"][-1]["llm"] is False
    assert value["integrity"]["verified"] is True


def test_projection_rejects_candidate_tampering(real_evidence, monkeypatch):
    original = fea_lab._tree

    def changed(root, source, *, runtime=False):
        value = original(root, source, runtime=runtime)
        if source.as_posix().endswith(fea_lab.FINAL + "/workspace/candidate") and not runtime:
            value["sqlglot/parser.py"] = "0" * 64
        return value

    monkeypatch.setattr(fea_lab, "_tree", changed)
    with pytest.raises(ValueError, match="candidate bytes changed"):
        fea_lab.demo(real_evidence)


def test_projection_rejects_review_receipt_tampering(real_evidence, monkeypatch):
    original = fea_lab._json

    def changed(root, path):
        value = original(root, path)
        if path.name == "review-patch-2.json":
            value["candidate_sha256"] = "0" * 64
        return value

    monkeypatch.setattr(fea_lab, "_json", changed)
    with pytest.raises(ValueError, match="review receipt"):
        fea_lab.demo(real_evidence)


def test_suite_projection_never_exposes_commands_or_origins():
    value = fea_lab._suite({"passed": 1, "total": 1, "successful": True,
                            "command": ["SECRET"], "cases": [{"id": "test_a", "status": "passed"}],
                            "binding": {"success": True, "origin": "SECRET"}}, "generated", "Tests")
    assert value["binding_verified"] is True
    assert "SECRET" not in json.dumps(value)
