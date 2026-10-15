"""Isolated interpreter entry; invoked by runner, never imports project config."""
from __future__ import annotations

import builtins
import contextlib
import copy
import io
import json
import math
import sys
from pathlib import Path

SAFE = ("abs all any bool dict enumerate filter float frozenset int isinstance len list map max min next ord chr "
        "pow range reversed round set slice sorted str sum tuple zip Exception ValueError TypeError IndexError "
        "ZeroDivisionError NotImplementedError").split()


def restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name not in {"typing", "math"}:
        raise ImportError("Restricted import")
    return builtins.__import__(name, globals, locals, fromlist, level)


def equivalent(actual, expected, tolerance):
    if isinstance(actual, (int, float)) and not isinstance(actual, bool) and isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance)
    if isinstance(actual, (list, tuple)) and isinstance(expected, (list, tuple)):
        return len(actual) == len(expected) and all(equivalent(a, e, tolerance) for a, e in zip(actual, expected))
    return type(actual) is type(expected) and actual == expected


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    # -I excludes the script directory. Load only our trusted copied helper, then
    # remove that search entry before compiling the candidate namespace.
    sys.path.insert(0, str(Path(__file__).parent))
    from resource_limits import apply_limits
    sys.path.pop(0)
    resource_guard = apply_limits(memory_bytes=512 * 1024 * 1024, cpu_seconds=5)
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    namespace = {"__builtins__": {name: getattr(builtins, name) for name in SAFE}}
    namespace["__builtins__"]["__import__"] = restricted_import
    sink = io.StringIO()
    failures = []
    passed = 0
    passed_indices = []
    runnable = False
    metrics = {}
    cases = payload.get("cases", [])
    total = 1 if payload.get("acceptance") is not None else len(cases)
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            exec(compile(payload["code"], "candidate.py", "exec"), namespace)
            candidate = namespace[payload["entry_point"]]
            runnable = callable(candidate)
            if payload.get("acceptance") is not None:
                judge = {}  # Trusted upstream tests execute in a separate namespace.
                exec(compile(payload["acceptance"], "acceptance.py", "exec"), judge)
                judge["check"](candidate)
                metrics = {key: value for key, value in judge.get("TESTFIX_METRICS", {}).items()
                           if isinstance(key, str) and type(value) is int and value >= 0}
                passed = 1
                passed_indices = [0]
            else:
                for index, case in enumerate(cases):
                    try:
                        actual = candidate(*copy.deepcopy(case["args"]), **copy.deepcopy(case.get("kwargs", {})))
                        if "raises" not in case and equivalent(actual, case["expected"], case.get("tolerance", 1e-8)):
                            passed += 1
                            passed_indices.append(index)
                        else:
                            failures.append({"index": index, "expected": case.get("expected", case.get("raises")), "actual": repr(actual)[:500], "error": "assertion mismatch"})
                    except Exception as exc:
                        if case.get("raises") == type(exc).__name__:
                            passed += 1
                            passed_indices.append(index)
                        else:
                            failures.append({"index": index, "error": f"{type(exc).__name__}: {str(exc)[:500]}"})
    except BaseException as exc:
        # No hidden assertion values are returned, only a generic failure category.
        failures.append({"error": f"{type(exc).__name__}: {str(exc)[:500]}" if payload.get("acceptance") is None else type(exc).__name__})
    result = {"passed": passed, "total": total, "passed_indices": passed_indices, "failures": failures,
              "runnable": runnable, "metrics": metrics}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

