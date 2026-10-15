"""Meaningful failure-path checks for full-engineering delivery evidence."""
import hashlib
import json
import subprocess
import sys

import pytest

from testfix.config import Settings
from testfix.project_delivery import verify_delivery


def setup_delivery(tmp_path):
    (tmp_path / "candidate").mkdir()
    (tmp_path / "python.exe").write_bytes(b"mock executable")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"id": "Project/flaskr", "candidate": "candidate", "python": "python.exe"}), encoding="utf-8")
    return path, Settings(root=tmp_path)


def test_build_timeout_is_saved_as_failed_delivery(tmp_path, monkeypatch):
    path, settings = setup_delivery(tmp_path)
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(subprocess, "run", timeout)
    result = verify_delivery(path, settings, {"id": "timeout"})
    assert result["build"]["passed"] is False
    assert result["http"]["successful"] is False
    assert result["execution_error"] is True
    saved = json.loads((settings.artifacts / "timeout/delivery/result.json").read_text(encoding="utf-8"))
    assert saved["error"].startswith("TimeoutExpired:")


def test_success_exit_without_wheel_is_rejected(tmp_path, monkeypatch):
    path, settings = setup_delivery(tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", ""))
    result = verify_delivery(path, settings, {"id": "missing-wheel"})
    assert result["build"]["passed"] is False
    assert result["http"]["total"] == 0 and result["http"]["successful"] is False


def test_retry_delivery_uses_independent_database_and_preserves_previous_evidence(tmp_path,monkeypatch):
    path,settings=setup_delivery(tmp_path)
    databases=[]
    def stop_after_recording(command,**kwargs):
        directory=Path(command[command.index('--outdir')+1]).parent
        database=directory/'http.sqlite'
        assert not database.exists(), 'A retry reused the preceding verification database'
        database.write_bytes(b'previous attempt database contents')
        databases.append(database)
        return subprocess.CompletedProcess(command,1,'controlled build stop','')
    from pathlib import Path
    monkeypatch.setattr(subprocess,'run',stop_after_recording)
    for attempt in ('verify-1','verify-2'):
        result=verify_delivery(path,settings,{'id':'same-run','delivery_attempt':attempt})
        assert not result['build']['passed']
    assert databases[0]!=databases[1]
    assert all(path.read_bytes()==b'previous attempt database contents' for path in databases)
    assert all((path.parent/'result.json').is_file() for path in databases)


def test_cli_delivery_exception_cannot_leave_accepted_report(tmp_path, monkeypatch):
    from testfix import cli, project_eval, project_delivery, reports
    settings = Settings(root=tmp_path)
    marker = tmp_path / "sealed.txt"
    marker.write_text("same", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"protected_files": {"sealed.txt": hashlib.sha256(marker.read_bytes()).hexdigest()}}), encoding="utf-8")
    task = {"id": "Project/flaskr", "title": "Flaskr", "status": "awaiting_delivery", "duration_s": 1,
            "verdict": {"test_suites_pass": True}}
    run = {"id": "abc", "status": "running", "provider": "codex_session", "runner": "project_process",
           "summary": {"total": 1, "accepted": 0, "regressions": 0}, "tasks": [task]}
    saved = []
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(project_eval, "evaluate_project", lambda *args, **kwargs: run)
    def failure(*args):
        raise OSError("build startup failed")
    monkeypatch.setattr(project_delivery, "verify_delivery", failure)
    monkeypatch.setattr(reports, "save_run", lambda directory, value: saved.append(value))
    monkeypatch.setattr(sys, "argv", ["testfix", "evaluate-project", str(manifest)])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 1
    assert saved[-1]["status"] == "failed" and saved[-1]["summary"]["accepted"] == 0
    assert saved[-1]["tasks"][0]["status"] == "rejected"
    assert saved[-1]["tasks"][0]["verdict"]["satisfies_tested_requirements"] is False
