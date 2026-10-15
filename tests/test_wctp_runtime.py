"""Integration checks execute the supplied WCTP files, never a fake graph engine."""
from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path
import shutil
import sys
from threading import Barrier, Lock

import pytest

from testfix.wctp_runtime import (
    SOURCE_SHA256, WCTPExecutionError, WCTPGraphRunner, WCTPSourceError,
    resolve_wctp_root, run_wctp_batch,
)


@pytest.fixture
def source_root():
    root = resolve_wctp_root()
    if not (root / "src/engine/graph_executor.py").is_file():
        pytest.skip("External supplied WCTP source is unavailable; set TESTFIX_WCTP_ROOT")
    return root


@pytest.fixture
def copied_source(source_root, tmp_path):
    root = tmp_path / "supplied-wctp"
    for relative in SOURCE_SHA256:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative, destination)
    return root


def test_real_wctp_batch_has_bounded_overlapping_callbacks_and_provenance(source_root):
    barrier = Barrier(2)

    def worker(index):
        def action():
            barrier.wait(timeout=5)
            return {"value": index}
        return action

    result = run_wctp_batch({f"task{i}": worker(i) for i in range(6)},
                            source_root=source_root, max_workers=2)
    assert result == {f"task{i}": {"value": i} for i in range(6)}
    metadata = result.metadata
    assert metadata["engine"] == "wctp.GraphExecutor.execute"
    assert metadata["max_workers"] == metadata["max_observed_concurrency"] == 2
    assert metadata["source_copied"] is False
    assert metadata["source_sha256"] == SOURCE_SHA256
    for relative, expected in metadata["source_sha256"].items():
        assert hashlib.sha256((source_root / relative).read_bytes()).hexdigest() == expected
    spans = {span["step_id"]: span for span in metadata["action_spans"] if span["step_id"].startswith("task")}
    assert len(spans) == 6
    assert all(span["thread_name"].startswith("ThreadPoolExecutor") for span in spans.values())
    assert all(span["started_at"] < span["finished_at"] for span in spans.values())
    assert any(
        max(left["started_monotonic_seconds"], right["started_monotonic_seconds"])
        < min(left["finished_monotonic_seconds"], right["finished_monotonic_seconds"])
        for key, left in spans.items() for other, right in spans.items() if key != other
    )
    assert metadata["executed_steps"][0] == "TestfixBatchStart"
    assert metadata["executed_steps"][-1] == "TestfixBatchEnd"


def test_loading_does_not_pollute_sys_path_src_or_import_device_stack(source_root):
    before_path = list(sys.path)
    before_src = {key: value for key, value in sys.modules.items() if key == "src" or key.startswith("src.")}
    runner = WCTPGraphRunner(source_root)
    assert list(sys.path) == before_path
    assert {key: value for key, value in sys.modules.items() if key == "src" or key.startswith("src.")} == before_src
    assert not any(key.startswith(runner.namespace + ".src.devices") for key in sys.modules)
    engine = runner.core["engine.graph_executor"]
    assert Path(engine.GraphExecutor.execute.__code__.co_filename) == source_root / "src/engine/graph_executor.py"
    assert engine.GraphExecutor.execute.__module__ == runner.namespace + ".src.engine.graph_executor"


def test_real_wctp_fork_join_waits_for_both_predecessors(source_root):
    done = []
    lock = Lock()
    barrier = Barrier(2)

    def branch(name):
        def action():
            barrier.wait(timeout=5)
            with lock:
                done.append(name)
            return name
        return action

    def join():
        assert sorted(done) == ["left", "right"]
        return "joined"

    result = WCTPGraphRunner(source_root, max_workers=2).run_graph(
        {"start": None, "left": branch("left"), "right": branch("right"), "join": join, "end": None},
        {"start": [("left", None), ("right", None)],
         "left": [("join", None)], "right": [("join", None)], "join": [("end", None)]},
        entry="start", exit="end",
    )
    assert result["join"] == "joined"
    assert result.metadata["initial_in_degree"]["join"] == 2


def test_real_wctp_critical_conditional_false_is_a_branch_not_error(source_root):
    selected = []
    result = WCTPGraphRunner(source_root).run_graph(
        {"start": None, "condition": lambda: False,
         "yes": lambda: selected.append("yes"), "no": lambda: selected.append("no"),
         "join": lambda: "joined", "end": None},
        {"start": [("condition", None)], "condition": [("yes", "True"), ("no", "False")],
         "yes": [("join", None)], "no": [("join", None)], "join": [("end", None)]},
        entry="start", exit="end", step_kinds={"condition": "conditional"},
    )
    assert selected == ["no"]
    assert result["condition"] is False
    assert "yes" not in result
    assert result["join"] == "joined"
    assert result.metadata["executed_steps"].count("join") == 1


def test_real_wctp_loop_reentry_resets_local_waiting_and_exits(source_root):
    calls = []
    result = WCTPGraphRunner(source_root).run_graph(
        {"start": None, "loop": None, "body": lambda: calls.append("body"), "end": None},
        {"start": [("loop", None)], "loop": [("body", "True"), ("end", "False")],
         "body": [("loop", None)]},
        entry="start", exit="end", step_kinds={"loop": "loop"}, loop_totals={"loop": 3},
    )
    assert calls == ["body"] * 3
    assert Counter(result.metadata["executed_steps"]) == {"start": 1, "loop": 4, "body": 3, "end": 1}
    assert result.metadata["back_edges"] == [["body", "loop"]]
    assert result["loop"] is False


def test_callback_failure_is_not_success_and_keeps_real_receipt(source_root):
    def fail():
        raise RuntimeError("intentional host failure")

    with pytest.raises(WCTPExecutionError, match="intentional host failure") as caught:
        run_wctp_batch({"failed": fail}, source_root=source_root)
    assert "failed" not in caught.value.results
    assert caught.value.metadata["source_sha256"] == SOURCE_SHA256
    assert any(event.get("error", "").endswith("intentional host failure")
               for event in caught.value.metadata["events"])


def test_failure_drains_started_chunk_without_starting_queued_chunks(source_root):
    calls = []

    def fail():
        calls.append("failed")
        raise RuntimeError("stop later chunks")

    with pytest.raises(WCTPExecutionError, match="stop later chunks"):
        run_wctp_batch({"failed": fail, "started": lambda: calls.append("started"),
                        "queued": lambda: calls.append("queued")}, source_root=source_root, max_workers=2)
    assert sorted(calls) == ["failed", "started"]


def test_disconnected_cycle_is_not_executed_as_a_valid_graph(source_root):
    with pytest.raises(ValueError, match="unreachable"):
        WCTPGraphRunner(source_root).run_graph(
            {"start": None, "end": None, "hiddenA": None, "hiddenB": None},
            {"start": [("end", None)], "hiddenA": [("hiddenB", None)], "hiddenB": [("hiddenA", None)]},
            entry="start", exit="end")


def test_changed_dependency_is_rejected_before_import(copied_source):
    path = copied_source / "src/engine/graph_executor.py"
    path.write_bytes(path.read_bytes() + b"\n# modified dependency\n")
    with pytest.raises(WCTPSourceError, match="SHA-256 mismatch"):
        WCTPGraphRunner(copied_source)


def test_changed_dependency_after_initial_import_is_rejected_before_action(copied_source):
    runner = WCTPGraphRunner(copied_source)
    path = copied_source / "src/tcm/case_parser.py"
    path.write_bytes(path.read_bytes() + b"\n# changed after import\n")
    invoked = []
    with pytest.raises(WCTPSourceError, match="SHA-256 mismatch"):
        runner.run_graph({"start": lambda: invoked.append(True), "end": None},
                         {"start": [("end", None)]}, entry="start", exit="end")
    assert invoked == []


def test_missing_dependency_fails_without_replay_fallback(tmp_path):
    with pytest.raises(WCTPSourceError, match="dependency is missing"):
        run_wctp_batch({"task": lambda: "not-run"}, source_root=tmp_path / "missing")


@pytest.mark.parametrize("workers", [0, -1, 33, True, 1.5])
def test_invalid_worker_count_is_rejected_before_external_import(workers):
    with pytest.raises(ValueError, match="max_workers"):
        WCTPGraphRunner("unused-root", max_workers=workers)


def test_configured_source_root_is_respected(monkeypatch, tmp_path):
    monkeypatch.setenv("TESTFIX_WCTP_ROOT", str(tmp_path))
    assert resolve_wctp_root() == tmp_path.resolve()


def test_serial_limit_still_runs_real_wctp(source_root):
    result = run_wctp_batch({"first": lambda: False, "second": lambda: 2},
                            source_root=source_root, max_workers=1)
    assert result == {"first": False, "second": 2}
    assert result.metadata["max_observed_concurrency"] == 1
