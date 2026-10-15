"""Real report writes with only filesystem publication faults injected."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time

import pytest

from testfix import reports


def run(status):
    return {"id": "fixture-report", "provider": "replay", "runner": "process",
            "status": status, "tasks": [], "summary": {"accepted": 0, "total": 1}}


@pytest.mark.parametrize("filename", ["report.json", "report.md"])
def test_save_run_retries_a_transient_sharing_denial(tmp_path, monkeypatch, filename):
    reports.save_run(tmp_path, run("old"))
    target = tmp_path / filename
    published = target.read_bytes()
    replace = Path.replace
    attempts, delays = [], []

    def sharing_denial(source, destination):
        if Path(destination) == target:
            attempts.append(source)
            if len(attempts) == 1:
                assert target.read_bytes() == published
                raise PermissionError("synthetic Windows sharing denial")
        return replace(source, destination)

    monkeypatch.setattr(Path, "replace", sharing_denial)
    monkeypatch.setattr(time, "sleep", delays.append)
    reports.save_run(tmp_path, run("new"))
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert len(delays) == 1 and 0 < delays[0] <= 1
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["status"] == "new"
    assert "状态：new" in (tmp_path / "report.md").read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("filename", ["report.json", "report.md"])
def test_save_run_propagates_permanent_denial_and_preserves_published_target(tmp_path, monkeypatch, filename):
    reports.save_run(tmp_path, run("old"))
    target = tmp_path / filename
    published = target.read_bytes()
    replace = Path.replace
    attempts, delays = [], []
    failure = PermissionError("permanent publication denial")

    def permanent_denial(source, destination):
        if Path(destination) == target:
            attempts.append(source)
            raise failure
        return replace(source, destination)

    monkeypatch.setattr(Path, "replace", permanent_denial)
    monkeypatch.setattr(time, "sleep", delays.append)
    with pytest.raises(PermissionError) as caught:
        reports.save_run(tmp_path, run("new"))
    assert caught.value is failure and target.read_bytes() == published
    assert 1 < len(attempts) <= 10
    assert len(delays) == len(attempts) - 1 and sum(delays) <= 2
    assert not list(tmp_path.glob("*.tmp"))


def test_render_failure_does_not_publish_part_of_an_invalid_report(tmp_path):
    reports.save_run(tmp_path, run("old"))
    published = {name: (tmp_path / name).read_bytes() for name in ("report.json", "report.md")}
    invalid = run("new")
    del invalid["provider"]
    with pytest.raises(KeyError):
        reports.save_run(tmp_path, invalid)
    assert {name: (tmp_path / name).read_bytes() for name in published} == published


def test_concurrent_report_writers_use_distinct_temporary_files(tmp_path, monkeypatch):
    replace = Path.replace
    barrier = threading.Barrier(4)
    sources = set()
    lock = threading.Lock()

    def concurrent_publication(source, destination):
        with lock:
            first_attempt = source not in sources
            sources.add(source)
        if Path(destination).name == "report.json" and first_attempt:
            barrier.wait(timeout=10)
        return replace(source, destination)

    monkeypatch.setattr(Path, "replace", concurrent_publication)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(reports.save_run, tmp_path, run(f"candidate-{number}")) for number in range(4)]
        for future in futures:
            future.result(timeout=15)
    assert len(sources) == 8
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["status"].startswith("candidate-")
    assert "状态：candidate-" in (tmp_path / "report.md").read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))


def test_package_report_distinguishes_upstream_and_added_regression_and_real_cli():
    value={**run('completed'),'provider':'codex_cli','runner':'project_process',
           'kind':'autonomous_project','execution_profile':'python_package','tasks':[
               {'id':'Project/package','title':'Fixture package','status':'accepted',
                'upstream_regression':{'passed':1,'total':1},
                'behavior_regression':{'passed':13,'total':13},
                'delivery':{'build':{'passed':True},'cli':{'passed':16,'total':16}}}]}
    text=reports.markdown(value)
    assert '上游原有测试：1/1' in text and '冻结的既有行为回归：13/13' in text
    assert '真实 CLI' in text and '真实 HTTP' not in text and 'Flaskr' not in text
    assert '| --- |' in text
