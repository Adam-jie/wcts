"""A smoke-pass must not silently make a product-validation command green."""
import json

import pytest

from scripts import run_product_campaign as cli


@pytest.mark.parametrize("scope_passed,accepted,flags,exit_code", [
    (True, False, [], 1),
    (True, False, ["--scope-only"], 0),
    (True, True, [], 0),
    (False, False, ["--scope-only"], 1),
])
def test_cli_defaults_to_full_product_gate(tmp_path, monkeypatch, scope_passed, accepted, flags, exit_code):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    (tmp_path / "app.py").write_text("version = 1", encoding="utf-8")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"source_files": ["app.py"], "contract": {}}), encoding="utf-8")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    report = {"run_id": "framework-fixture", "tested_scope_passed": scope_passed, "product_accepted": accepted,
              "counts": {}, "coverage": {}, "duration_s": 0.0, "acceptance_blockers": [], "evidence_dir": str(evidence)}
    monkeypatch.setattr(cli, "run_campaign", lambda *args, **kwargs: report)
    assert cli.main(["--profile", str(profile), "--output", str(tmp_path / "output"), *flags]) == exit_code
    context = json.loads((evidence / "adapter-context.json").read_text(encoding="utf-8"))
    assert context["exit_gate"] == ("tested_scope_passed" if flags else "product_accepted")
    assert context["source_snapshot_verified"] is True
