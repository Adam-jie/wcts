"""New protocol adapter for WCTP, based on its callback contract, not copied code.

WCTP GraphExecutor calls event_callback(event, step_id, result). Conditional and
Loop False values describe branch control, not test failures. Source provenance
and the absent upstream code license are documented in THIRD_PARTY_NOTICES.md.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Callable


class WCTPEventBridge:
    def __init__(self, emit: Callable[[dict], None], node_types: dict[str, str] | None = None):
        self.emit = emit
        self.node_types = node_types or {}

    def __call__(self, event: str, step_id: str, result=None):
        if event not in {"started", "finished", "loop_iteration"}:
            return
        evidence = {"source": "wctp", "event": event, "step_id": step_id, "node_type": self.node_types.get(step_id, "Unknown")}
        if event == "finished":
            if is_dataclass(result):
                values = asdict(result)
            elif isinstance(result, dict):
                values = result
            else:
                values = {name: getattr(result, name, None) for name in ("status", "duration", "outputs", "error", "start_time", "end_time")}
            node_type = evidence["node_type"]
            error = values.get("error")
            status = values.get("status")
            # Unknown nodes need the WCTP owner's classification before judgement.
            outcome = "error" if error else "control" if node_type in {"Conditional", "Loop"} else "passed" if status is True else "failed" if node_type == "Normal" and status is False else "unclassified"
            evidence.update(outcome=outcome, duration_s=values.get("duration"), outputs=values.get("outputs"), error=error)
        elif event == "loop_iteration" and isinstance(result, dict):
            evidence["progress"] = {"current": result.get("current"), "total": result.get("total")}
        self.emit(evidence)

