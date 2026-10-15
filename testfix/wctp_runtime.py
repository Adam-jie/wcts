"""Reference the supplied WCTP graph engine without importing its device stack.

The original executor, graph model, registry and execution context are loaded
from verified local files. Only absolute ``src`` imports are relocated to a
private namespace; the context's annotation-only EnvBlueprint import is omitted
with postponed annotations. No source is copied into this package and no
``src`` package or search-path entry is installed globally.
"""
from __future__ import annotations

import __future__
import ast
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import os
import math
from pathlib import Path
import re
import sys
from threading import RLock, current_thread
import time
from types import ModuleType
from typing import Any


SOURCE_SHA256 = {
    "src/engine/graph_executor.py": "37159cfee123b110ed0f4a194bb640cdc7d5de1a8b5df5de5b54bf69df37130c",
    "src/engine/context.py": "a2d95ec9171e1a39893eaf67d37a18c898390f9e1fd68ac80694dbef68f5c455",
    "src/tcm/case_parser.py": "469ddec328daf157d030c46c5f494653fa10372a782be6bfc92fb4eeeec0c87c",
    "src/tcm/step_registry.py": "a85df5c2dc28259a818afb96fe60af17346256227afe58c11bc1e56958f254d3",
    "src/infra/config.py": "fcfc2662d5a485ce5417ef5dd03238c1d9c1d128ed49680c5f697826244dd947",
    "src/infra/exceptions.py": "75694c9339a819cdcbc6e7fd9b3eb01882abc91794d619cb562ccde304ef34fa",
    "src/infra/logkit.py": "9fe0dd1f49df84e24931f9dced4291ff064eb5575c979bf9d87cb0eadbe961d7",
}
_IMPORT_LOCK = RLock()
_SAFE_ID = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,127}$")
_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "自动化框架" / "wctp"


class WCTPSourceError(RuntimeError):
    """The external dependency is missing, changed, or cannot be imported."""


class WCTPExecutionError(RuntimeError):
    """Actual WCTP execution failed; completed action values remain inspectable."""

    def __init__(self, message: str, results: "WCTPBatchResult"):
        super().__init__(message)
        self.results = results
        self.metadata = results.metadata


class WCTPBatchResult(dict):
    """Task ID -> original callback value, with serializable engine provenance."""

    def __init__(self, values: Mapping[str, Any], metadata: dict):
        super().__init__(values)
        self.metadata = metadata


def resolve_wctp_root(source_root: str | Path | None = None) -> Path:
    value = source_root if source_root is not None else os.environ.get("TESTFIX_WCTP_ROOT")
    return Path(value).expanduser().resolve() if value else _DEFAULT_ROOT.resolve()


def _verified_sources(root: Path) -> dict[str, bytes]:
    sources = {}
    for relative, expected in SOURCE_SHA256.items():
        path = root / relative
        try:
            if not path.resolve().is_relative_to(root):
                raise WCTPSourceError(f"WCTP source escapes configured root: {relative}")
            data = path.read_bytes()
        except OSError as exc:
            raise WCTPSourceError(
                f"WCTP dependency is missing: {path}. Set TESTFIX_WCTP_ROOT to the supplied source root."
            ) from exc
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise WCTPSourceError(
                f"WCTP source SHA-256 mismatch for {relative}: expected {expected}, found {actual}"
            )
        sources[relative] = data
    return sources


class _PrivateImports(ast.NodeTransformer):
    def __init__(self, namespace: str, relative: str):
        self.namespace = namespace
        self.relative = relative

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if self.relative == "src/engine/context.py" and node.module == "src.infra.env":
            # EnvBlueprint occurs exclusively in postponed type annotations.
            # Importing env.py would eagerly import the unrelated device stack.
            return None
        if node.level == 0 and node.module and (node.module == "src" or node.module.startswith("src.")):
            node.module = self.namespace + "." + node.module
        return node


class _SourceLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, namespace: str, root: Path, sources: dict[str, bytes]):
        self.namespace, self.root, self.sources = namespace, root, sources

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(self.namespace + "."):
            return None
        relative = fullname[len(self.namespace) + 1:].replace(".", "/") + ".py"
        if relative in self.sources:
            return importlib.util.spec_from_loader(fullname, self, origin=str(self.root / relative))
        raise WCTPSourceError(f"WCTP import outside verified graph dependency closure: {fullname}")

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        relative = module.__name__[len(self.namespace) + 1:].replace(".", "/") + ".py"
        tree = ast.parse(self.sources[relative], filename=str(self.root / relative))
        tree = _PrivateImports(self.namespace, relative).visit(tree)
        ast.fix_missing_locations(tree)
        code = compile(tree, str(self.root / relative), "exec", flags=__future__.annotations.compiler_flag)
        module.__file__ = str(self.root / relative)
        exec(code, module.__dict__)
        if relative == "src/infra/config.py":
            # Never search the caller's cwd for another project's .project-root.
            module._PROJECT_ROOT = self.root


def _load_core(root: Path):
    sources = _verified_sources(root)
    namespace = "_testfix_wctp_" + hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    with _IMPORT_LOCK:
        if namespace not in sys.modules:
            finder = _SourceLoader(namespace, root, sources)
            try:
                for suffix in ("", ".src", ".src.engine", ".src.tcm", ".src.infra"):
                    name = namespace + suffix
                    package = ModuleType(name)
                    package.__path__ = []
                    package.__package__ = name
                    package.__spec__ = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
                    sys.modules[name] = package
                sys.meta_path.insert(0, finder)
                for relative in sources:
                    name = namespace + "." + relative[:-3].replace("/", ".")
                    importlib.import_module(name)
            except Exception as exc:
                for name in list(sys.modules):
                    if name == namespace or name.startswith(namespace + "."):
                        sys.modules.pop(name, None)
                raise WCTPSourceError(f"Cannot import verified WCTP graph engine: {exc}") from exc
            finally:
                if finder in sys.meta_path:
                    sys.meta_path.remove(finder)
    return namespace, {
        key: sys.modules[namespace + ".src." + key]
        for key in ("engine.graph_executor", "engine.context", "tcm.case_parser", "tcm.step_registry")
    }


@dataclass(frozen=True)
class WCTPDecision:
    """Optional conditional/loop callback value with an explicit branch decision."""

    status: bool
    value: Any = None


class WCTPGraphRunner:
    """Bounded adapter over the supplied WCTP GraphExecutor.execute algorithm.

    ``run_graph`` accepts registered host callbacks only, not paths, scripts or
    dynamic action imports. A timeout is checked between ready batches by WCTP;
    it cannot interrupt an already running Python callback.
    """

    def __init__(self, source_root: str | Path | None = None, *, max_workers: int = 2):
        if type(max_workers) is not int or not 1 <= max_workers <= 32:
            raise ValueError("max_workers must be an integer between 1 and 32")
        self.source_root = resolve_wctp_root(source_root)
        self.max_workers = max_workers
        self.namespace, self.core = _load_core(self.source_root)

    def run_graph(
        self,
        actions: Mapping[str, Callable[[], Any] | None],
        edges: Mapping[str, list[tuple[str, str | None]]],
        *,
        entry: str,
        exit: str,
        step_kinds: Mapping[str, str] | None = None,
        loop_totals: Mapping[str, int] | None = None,
        event_callback: Callable | None = None,
        timeout: float = 0,
    ) -> WCTPBatchResult:
        _verified_sources(self.source_root)
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("WCTP timeout must be finite and nonnegative")
        if not actions or len(actions) > 256:
            raise ValueError("WCTP graph requires between 1 and 256 registered host actions")
        if any(not isinstance(key, str) or not _SAFE_ID.fullmatch(key) for key in actions):
            raise ValueError("WCTP action IDs must be safe alphanumeric identifiers")
        if entry not in actions or exit not in actions:
            raise ValueError("WCTP entry and exit must be registered host actions")
        kinds, totals = dict(step_kinds or {}), dict(loop_totals or {})
        if (set(kinds) | set(totals)) - set(actions):
            raise ValueError("WCTP step kind/loop total references an unknown action")
        for source, outgoing in edges.items():
            if source not in actions:
                raise ValueError("WCTP edge source is unknown")
            for target, branch in outgoing:
                if target not in actions or branch not in (None, "True", "False"):
                    raise ValueError("WCTP edge target/branch is invalid")
            if len(outgoing) != len(set(outgoing)):
                raise ValueError("WCTP graph contains duplicate edges")
        reachable, pending = set(), [entry]
        while pending:
            node = pending.pop()
            if node not in reachable:
                reachable.add(node)
                pending.extend(target for target, _ in edges.get(node, []))
        if reachable != set(actions):
            raise ValueError("WCTP graph contains host actions unreachable from its entry")
        if edges.get(exit):
            raise ValueError("WCTP exit must have no outgoing edges")
        registry_module = self.core["tcm.step_registry"]
        graph_module = self.core["tcm.case_parser"]
        engine_module = self.core["engine.graph_executor"]
        context_module = self.core["engine.context"]
        values, events, spans = {}, [], []
        lock = RLock()
        activity = {"active": 0, "peak": 0}
        started = time.perf_counter()
        metadata = {
            "engine": "wctp.GraphExecutor.execute",
            "executor_source": str(self.source_root / "src/engine/graph_executor.py"),
            "source_root": str(self.source_root), "namespace": self.namespace,
            "source_sha256": dict(SOURCE_SHA256),
            "source_copied": False, "max_workers": self.max_workers,
            "import_transformations": [
                "absolute src imports relocated to a private namespace",
                "postponed annotations; context annotation-only EnvBlueprint import omitted",
            ],
            "events": events,
            "action_spans": spans,
        }

        def emit(event, step_id, result=None):
            record = {"event": event, "step_id": step_id,
                      "elapsed_seconds": round(time.perf_counter() - started, 6)}
            if hasattr(result, "status"):
                record.update(status=result.status, error=result.error)
            with lock:
                events.append(record)
            if event_callback:
                event_callback(event, step_id, result)

        def execute_action(step, ctx):
            action_start = time.perf_counter()
            span = {"step_id": step.step_id, "thread_name": current_thread().name,
                    "started_monotonic_seconds": action_start,
                    "started_at": datetime.now(timezone.utc).isoformat()}
            with lock:
                activity["active"] += 1
                activity["peak"] = max(activity["peak"], activity["active"])
            try:
                callback = actions[step.step_id]
                if callback is None:
                    value = step.action(ctx)[0] if isinstance(step, registry_module.LoopStep) else None
                else:
                    value = callback()
                is_branch = isinstance(step, (registry_module.ConditionalStep, registry_module.LoopStep))
                decision = value.status if isinstance(value, WCTPDecision) else value
                if is_branch and type(decision) is not bool:
                    raise ValueError("WCTP conditional/loop callbacks must return bool or WCTPDecision")
                status = decision if is_branch else True
                stored_value = value.value if isinstance(value, WCTPDecision) else value
                with lock:
                    values[step.step_id] = stored_value
                return registry_module.StepResult(
                    step_id=step.step_id, status=status, duration=time.perf_counter() - action_start,
                    outputs={"value": stored_value},
                )
            except Exception as exc:
                return registry_module.StepResult(
                    step_id=step.step_id, status=False, duration=time.perf_counter() - action_start,
                    outputs={}, error=f"{type(exc).__name__}: {exc}",
                )
            finally:
                span.update(finished_monotonic_seconds=time.perf_counter(),
                            finished_at=datetime.now(timezone.utc).isoformat())
                with lock:
                    activity["active"] -= 1
                    spans.append(span)

        registry = registry_module.StepRegistry()
        base_classes = {"normal": registry_module.NormalStep,
                        "conditional": registry_module.ConditionalStep,
                        "loop": registry_module.LoopStep,
                        "end": registry_module.EndStep}
        for task_id, callback in actions.items():
            kind = kinds.get(task_id, "end" if task_id == exit else "normal")
            if kind not in base_classes or (callback is not None and not callable(callback)):
                raise ValueError("WCTP kinds must be normal/conditional/loop/end and actions must be callables")
            total = totals.get(task_id, 0)
            if kind == "loop" and (type(total) is not int or not 0 <= total <= 100):
                raise ValueError("WCTP built-in loop totals must be integers between 0 and 100")
            if callback is None and kind not in {"loop", "end", "normal"}:
                raise ValueError("WCTP conditional steps require a callback")
            callback_class = type("RegisteredHost" + base_classes[kind].__name__,
                                  (base_classes[kind],), {"execute": execute_action})
            kwargs = {"step_id": task_id, "step_name": task_id, "is_critical": True}
            if kind == "loop":
                kwargs["total"] = total
            registry.steps[task_id] = callback_class(**kwargs)
        graph = graph_module.CaseGraph(
            case_id="testfix-host-actions", nodes=set(actions),
            edges={source: list(outgoing) for source, outgoing in edges.items()}, entry=entry, exit=exit,
        )
        errors = graph_module.validate_graph(graph, registry)
        if errors:
            raise ValueError("Invalid WCTP graph: " + "; ".join(errors))

        # Keep original ready/cancel/reentry logic, restricting only batch width.
        class BoundedExecutor(engine_module.GraphExecutor):
            def _execute_batch(executor, step_ids, ctx):
                results = []
                for offset in range(0, len(step_ids), self.max_workers):
                    chunk = super()._execute_batch(step_ids[offset:offset + self.max_workers], ctx)
                    results.extend(chunk)
                    if any(result.error for result in chunk):
                        break
                return results

        executor = BoundedExecutor(graph, registry, event_callback=emit)
        ctx = context_module.ExecutionContext("testfix-host-actions", None)
        try:
            step_results = executor.execute(ctx, timeout=timeout)
        except Exception as exc:
            metadata.update(peak_active=activity["peak"], max_observed_concurrency=activity["peak"],
                            elapsed_seconds=time.perf_counter() - started)
            raise WCTPExecutionError(f"WCTP graph execution raised {type(exc).__name__}: {exc}",
                                     WCTPBatchResult(values, metadata)) from exc
        metadata.update(
            peak_active=activity["peak"], elapsed_seconds=time.perf_counter() - started,
            max_observed_concurrency=activity["peak"],
            executed_steps=[item.step_id for item in step_results],
            back_edges=[list(edge) for edge in sorted(executor.back_edges)],
            initial_in_degree=dict(sorted(executor._dag_in_deg.items())),
        )
        result = WCTPBatchResult(values, metadata)
        failures = [item for item in step_results if item.error]
        if failures:
            raise WCTPExecutionError("; ".join(f"{item.step_id}: {item.error}" for item in failures), result)
        if exit not in metadata["executed_steps"]:
            raise WCTPExecutionError("WCTP graph stopped before its exit", result)
        return result


def run_wctp_batch(
    actions: Mapping[str, Callable[[], Any]],
    *, source_root: str | Path | None = None,
    max_workers: int = 2,
    event_callback: Callable | None = None,
) -> WCTPBatchResult:
    """Execute independent ready tasks through a real WCTP fork/join graph."""
    if not actions or any(not callable(action) for action in actions.values()):
        raise ValueError("WCTP batch requires at least one callable host action")
    if {"TestfixBatchStart", "TestfixBatchEnd"} & set(actions):
        raise ValueError("WCTP batch action IDs collide with reserved fork/join nodes")
    if len(actions) > 254:
        raise ValueError("WCTP batch supports at most 254 host actions")
    runner = WCTPGraphRunner(source_root, max_workers=max_workers)
    graph_actions = {"TestfixBatchStart": None, **actions, "TestfixBatchEnd": None}
    edges = {"TestfixBatchStart": [(task_id, None) for task_id in actions],
             **{task_id: [("TestfixBatchEnd", None)] for task_id in actions}}
    result = runner.run_graph(graph_actions, edges, entry="TestfixBatchStart", exit="TestfixBatchEnd",
                              event_callback=event_callback)
    return WCTPBatchResult({task_id: result[task_id] for task_id in actions}, result.metadata)
