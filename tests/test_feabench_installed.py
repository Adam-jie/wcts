"""Host-side fixture and collection guards; no model or benchmark gold access."""
from pathlib import Path

import pytest

from scripts.verify_feabench_installed import (baseline_suites, prepare_fixtures,
                                               safe_path, suite_pairing)


def test_fixture_preparation_copies_only_identical_data_and_preserves_existing(tmp_path):
    source = tmp_path / "base/tests/fixtures"
    target = tmp_path / "site/tests/fixtures"
    (source / "optimizer").mkdir(parents=True)
    (source / "optimizer/nation.csv.gz").write_bytes(b"frozen fixture")
    first = prepare_fixtures(source, target)
    second = prepare_fixtures(source, target)
    assert first["bytes_match"] and first["file_count"] == 1
    assert first["source_sha256"] == second["target_sha256"]
    assert first["target_preexisted"] is False and second["target_preexisted"] is True
    assert (source / "optimizer/nation.csv.gz").read_bytes() == b"frozen fixture"


def test_fixture_preparation_rejects_test_code_before_creating_destination(tmp_path):
    source, target = tmp_path / "source", tmp_path / "site/tests/fixtures"
    source.mkdir()
    (source / "test_escape.py").write_text("raise RuntimeError('not data')")
    with pytest.raises(ValueError, match="data only"):
        prepare_fixtures(source, target)
    assert not target.exists()


def test_conflicting_existing_fixture_is_never_overwritten(tmp_path):
    source, target = tmp_path / "source", tmp_path / "site/tests/fixtures"
    source.mkdir()
    target.mkdir(parents=True)
    (source / "data.csv").write_bytes(b"original")
    (target / "data.csv").write_bytes(b"modified")
    with pytest.raises(ValueError, match="differs"):
        prepare_fixtures(source, target)
    assert (target / "data.csv").read_bytes() == b"modified"


def test_path_guard_rejects_escape_and_root_itself(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        safe_path(tmp_path / "../escape", tmp_path, exists=False)
    with pytest.raises(ValueError, match="escapes"):
        safe_path(tmp_path, tmp_path)


def public_suite(ids):
    return {"case_ids": ids, "total": len(ids), "collection_errors": 0,
            "parse_error": "", "binding_error": "", "timeout": False,
            "output_limit_exceeded": False, "successful": False,
            "binding": {"success": True, "imported": True, "escaped_modules": []}}


def test_pairing_allows_genuine_failing_baseline_but_rejects_missing_or_duplicate_ids():
    baseline = public_suite(["test_a", "test_b"])
    assert suite_pairing(baseline, public_suite(["test_b", "test_a"]))["case_ids_match"]
    assert not suite_pairing(baseline, public_suite(["test_a"]))["case_ids_match"]
    assert not suite_pairing(public_suite(["test_a", "test_a"]), baseline)["baseline_valid"]
    assert not suite_pairing(public_suite([]), public_suite([]))["case_ids_match"]


def test_baseline_binding_cannot_pair_an_installed_or_other_source(tmp_path):
    baseline = public_suite(["test_a"])
    baseline["binding"].update(package="sqlglot", project=str(tmp_path / "other"),
                               source=str(tmp_path / "other"), before_modules={}, modules={})
    assert not suite_pairing(baseline, baseline, tmp_path / "baseline")["baseline_valid"]


def test_baseline_report_shape_retains_failing_public_suite():
    suite = public_suite(["test_a"])
    assert baseline_suites({"generated_baseline": suite}) == {"generated": suite}
    with pytest.raises(ValueError, match="no supported"):
        baseline_suites({"status": "completed"})
