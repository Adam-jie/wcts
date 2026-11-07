"""图执行器 — 就绪队列驱动，支持并行、屏障、循环。"""

import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.engine.context import ExecutionContext
from src.infra.exceptions import TimeoutError
from src.infra.logkit import get_logger
from src.tcm.case_parser import CaseGraph
from src.tcm.step_registry import (
    ConditionalStep,
    LoopStep,
    Step,
    StepRegistry,
    StepResult,
)

MAX_LOOP = 1000


# ======================================================================
# 反向边检测
# ======================================================================

def find_back_edges(graph: CaseGraph) -> set[tuple[str, str]]:
    """DFS 检测反向边：边 (A→B) 中 B 是 A 的祖先（即回边，构成循环）。"""
    back_edges: set[tuple[str, str]] = set()
    visited: set[str] = set()
    on_stack: set[str] = set()

    def dfs(node: str):
        visited.add(node)
        on_stack.add(node)
        for tgt, _ in graph.edges.get(node, []):
            if tgt not in visited:
                dfs(tgt)
            elif tgt in on_stack:
                back_edges.add((node, tgt))
        on_stack.discard(node)

    if graph.entry:
        dfs(graph.entry)
    return back_edges


# ======================================================================
# DAG 构建（去除反向边）
# ======================================================================

def _build_dag(graph: CaseGraph, back_edges: set[tuple[str, str]]) -> CaseGraph:
    """从完整图去除反向边，得到 DAG。"""
    dag_edges: dict[str, list[tuple[str, str | None]]] = {}
    for src, outgoing in graph.edges.items():
        dag_edges[src] = [(t, c) for t, c in outgoing if (src, t) not in back_edges]
    return CaseGraph(
        case_id=graph.case_id,
        nodes=set(graph.nodes),
        edges=dag_edges,
        entry=graph.entry,
        exit=graph.exit,
    )


def _dag_in_degree(dag: CaseGraph) -> dict[str, int]:
    """计算 DAG 中每个节点的入度。"""
    in_deg: dict[str, int] = {n: 0 for n in dag.nodes}
    for src, outgoing in dag.edges.items():
        for tgt, _ in outgoing:
            in_deg[tgt] = in_deg.get(tgt, 0) + 1
    return in_deg


def _dag_predecessors(dag: CaseGraph) -> dict[str, set[str]]:
    """计算 DAG 中每个节点的前驱集合。"""
    preds: dict[str, set[str]] = {n: set() for n in dag.nodes}
    for src, outgoing in dag.edges.items():
        for tgt, _ in outgoing:
            preds.setdefault(tgt, set()).add(src)
    return preds


# ======================================================================
# 循环区域计算
# ======================================================================

def _can_reach_via_dag(dag: CaseGraph, start: str, target: str) -> bool:
    """判断从 start 沿 DAG 前向边能否到达 target。"""
    visited: set[str] = set()
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node == target:
            return True
        if node in visited:
            continue
        visited.add(node)
        for tgt, _ in dag.edges.get(node, []):
            queue.append(tgt)
    return False


def _compute_loop_region(
    reentry: str,
    sources: set[str],
    dag: CaseGraph,
) -> set[str]:
    """计算循环区域：从 reentry 到各 back edge source 之间的所有节点。

    reentry:  反向边目标（循环重入点）
    sources:  所有指向 reentry 的反向边源节点
    dag:      去除反向边后的 DAG
    """
    region: set[str] = {reentry}
    region.update(sources)

    # 从 reentry 出发，只走能到达某个 source 的分支
    for tgt, _ in dag.edges.get(reentry, []):
        if any(_can_reach_via_dag(dag, tgt, s) for s in sources):
            queue = deque([tgt])
            while queue:
                node = queue.popleft()
                if node in region:
                    continue
                region.add(node)
                for t, _ in dag.edges.get(node, []):
                    if t not in region:
                        queue.append(t)

    return region


def _region_in_degree(node: str, region: set[str], dag: CaseGraph) -> int:
    """计算节点在循环区域内的入度（只计区域内前驱）。"""
    count = 0
    for src, outgoing in dag.edges.items():
        if src not in region:
            continue
        for tgt, _ in outgoing:
            if tgt == node:
                count += 1
    return count


# ======================================================================
# 图执行器
# ======================================================================

class GraphExecutor:
    """就绪队列驱动的图执行器，支持并行、屏障同步、循环。"""

    def __init__(self, graph: CaseGraph, registry: StepRegistry, event_callback=None):
        self.graph = graph
        self.registry = registry
        self.back_edges = find_back_edges(graph)
        self.dag = _build_dag(graph, self.back_edges)
        self._dag_in_deg = _dag_in_degree(self.dag)
        self._dag_preds = _dag_predecessors(self.dag)

        # 按反向边目标分组：(reentry → set of sources)
        self._back_edge_groups: dict[str, set[str]] = {}
        for src, tgt in self.back_edges:
            self._back_edge_groups.setdefault(tgt, set()).add(src)

        # 缓存循环区域
        self._loop_regions: dict[str, set[str]] = {}
        # GUI/CI observers receive ("started"|"finished", step_id, StepResult|None).
        # Keeping this optional preserves the synchronous public API.
        self.event_callback = event_callback

    def _get_loop_region(self, reentry: str) -> set[str]:
        if reentry not in self._loop_regions:
            sources = self._back_edge_groups[reentry]
            self._loop_regions[reentry] = _compute_loop_region(
                reentry, sources, self.dag
            )
        return self._loop_regions[reentry]

    def _is_loop_controller(self, step_id: str, reentry: str) -> bool:
        """判断 step_id 是否是 reentry 循环的控制器。

        LoopStep 的 True 分支沿 DAG 能到达某个反向边源 → 它控制当前循环。
        DAG 中反向边已去除，内层循环路径自然截断，不会误判。
        """
        sources = self._back_edge_groups.get(reentry, set())
        if not sources:
            return False
        true_targets = [t for t, c in self.graph.edges.get(step_id, []) if c == "True"]
        for t in true_targets:
            if t in sources:
                return True
        # 沿 DAG 前向遍历
        visited: set[str] = set()
        queue = deque(true_targets)
        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            if node in sources:
                return True
            for tgt, _ in self.dag.edges.get(node, []):
                queue.append(tgt)
        return False

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------

    def execute(self, ctx: ExecutionContext, timeout: float = 0) -> list[StepResult]:
        """执行用例图。

        Args:
            ctx: 执行上下文
            timeout: 用例级超时（秒），0 表示不限
        """
        logger = get_logger("engine.graph_executor")
        results: list[StepResult] = []

        # 初始化等待计数器
        waiting: dict[str, int] = dict(self._dag_in_deg)
        ready: deque[str] = deque(n for n, d in waiting.items() if d == 0)

        completed: set[str] = set()
        cancelled: set[str] = set()
        loop_iters: dict[str, int] = {}
        start_time = time.perf_counter()

        while ready:
            # 用例级超时检查
            if timeout > 0 and (time.perf_counter() - start_time) > timeout:
                raise TimeoutError(f"用例执行超时 ({timeout}s)")

            batch = list(ready)
            ready.clear()

            # 并行执行就绪节点
            batch_results = self._execute_batch(batch, ctx)
            results.extend(batch_results)

            # 收集需要重入的循环（按 reentry 去重，避免同一重入点被多次处理）
            reentries: dict[str, str] = {}  # reentry → source

            for step_id, result in zip(batch, batch_results):
                completed.add(step_id)
                step = self.registry.get(step_id)

                if isinstance(step, LoopStep) and result.status:
                    current = int(ctx.get_variable(f"_loop_count_{step.step_id}", 0))
                    self._emit_event(
                        "loop_iteration", step_id,
                        {"current": current, "total": int(step.total)},
                    )

                # LoopStep False = 循环结束，ConditionalStep False = 走 False 分支
                if isinstance(step, LoopStep) and not result.status:
                    logger.debug(
                        "执行: %s (%s) → 循环结束", step.step_name, step_id,
                    )
                elif isinstance(step, ConditionalStep) and not result.status:
                    logger.debug(
                        "执行: %s (%s) → False", step.step_name, step_id,
                    )
                else:
                    logger.debug(
                        "执行: %s (%s) → %s", step.step_name, step_id,
                        "通过" if result.status else "失败",
                    )

                # 步骤异常（error 非空）或关键步骤失败 → 终止
                if not result.status:
                    if result.error:
                        logger.error("步骤异常终止: %s — %s", step_id, result.error)
                        return results
                    # False is a branch decision for ConditionalStep/LoopStep,
                    # not a failed critical step.  It must continue to the
                    # matching False edge (LoopStep uses it to exit the loop).
                    if step.is_critical and not isinstance(step, (ConditionalStep, LoopStep)):
                        return results

                # 确定要激活的后继
                if isinstance(step, (ConditionalStep, LoopStep)):
                    branch = "True" if result.status else "False"
                    activated = [
                        (t, c) for t, c in self.graph.edges.get(step_id, [])
                        if c == branch
                    ]
                    # 取消未选分支
                    unchosen = [
                        (t, c) for t, c in self.graph.edges.get(step_id, [])
                        if c != branch
                    ]
                    for tgt, _ in unchosen:
                        if (step_id, tgt) not in self.back_edges:
                            # 已取消的节点不再重复递减 waiting（循环场景下会反复进入此分支）
                            if tgt in cancelled:
                                continue
                            waiting[tgt] -= 1
                            self._cancel_node(
                                tgt, waiting, completed, cancelled, ready,
                            )
                else:
                    if not result.status:
                        logger.error("步骤失败终止: %s", step_id)
                        return results
                    activated = self.graph.edges.get(step_id, [])

                for tgt, _ in activated:
                    if (step_id, tgt) in self.back_edges:
                        # 收集反向边，稍后统一处理（同一 reentry 只处理一次）
                        if tgt not in reentries:
                            reentries[tgt] = step_id
                    else:
                        if tgt in cancelled:
                            cancelled.discard(tgt)
                            # 重新计算等待计数，而非简单的 +=1 / -=1：
                            # _cancel_node 可能多次传播减过 waiting，
                            # 直接恢复无法还原。从 DAG 入度和已完成前驱数重算。
                            n_done = sum(
                                1 for p in self._dag_preds.get(tgt, set())
                                if p in completed
                            )
                            waiting[tgt] = self._dag_in_deg[tgt] - n_done
                        else:
                            waiting[tgt] -= 1
                        if waiting[tgt] <= 0 and tgt not in completed:
                            waiting[tgt] = 0
                            ready.append(tgt)

            # 统一处理循环重入（每个 reentry 只处理一次）
            for reentry, source in reentries.items():
                self._handle_reentry(
                    reentry, source, waiting, ready,
                    loop_iters, completed, cancelled, ctx,
                )

            # 过滤 ready 队列：重入重置后，waiting>0 的节点不应在就绪队列中
            ready = deque(n for n in ready if waiting[n] <= 0 and n not in completed)

        return results

    # ------------------------------------------------------------------
    # 并行执行
    # ------------------------------------------------------------------

    def _execute_batch(self, step_ids: list[str], ctx: ExecutionContext) -> list[StepResult]:
        """并行执行一批就绪节点。"""
        if len(step_ids) == 1:
            step = self.registry.get(step_ids[0])
            self._emit_event("started", step_ids[0])
            result = step.execute(ctx)
            self._emit_event("finished", step_ids[0], result)
            return [result]

        batch_results: list[StepResult] = [None] * len(step_ids)  # type: ignore
        with ThreadPoolExecutor(max_workers=len(step_ids)) as pool:
            futures = {}
            for i, sid in enumerate(step_ids):
                step = self.registry.get(sid)
                self._emit_event("started", sid)
                futures[pool.submit(step.execute, ctx)] = i
            for f in as_completed(futures):
                idx = futures[f]
                result = f.result()
                batch_results[idx] = result
                self._emit_event("finished", step_ids[idx], result)

        return batch_results

    def _emit_event(self, event: str, step_id: str, result=None):
        if self.event_callback:
            self.event_callback(event, step_id, result)

    # ------------------------------------------------------------------
    # 取消未选分支
    # ------------------------------------------------------------------

    def _cancel_node(
        self,
        node: str,
        waiting: dict[str, int],
        completed: set[str],
        cancelled: set[str],
        ready: deque[str],
    ):
        """取消一个未选分支上的节点，并递归传播。"""
        if node in cancelled or node in completed:
            return
        cancelled.add(node)

        for tgt, _ in self.dag.edges.get(node, []):
            waiting[tgt] -= 1
            if waiting[tgt] == 0 and tgt not in completed and tgt not in cancelled:
                # 检查是否有已完成的前驱（存在活跃路径）
                has_completed_pred = any(
                    p in completed for p in self._dag_preds.get(tgt, set())
                )
                if has_completed_pred:
                    ready.append(tgt)
                else:
                    # 所有前驱都已取消 → 取消此节点
                    self._cancel_node(tgt, waiting, completed, cancelled, ready)

    # ------------------------------------------------------------------
    # 循环重入
    # ------------------------------------------------------------------

    def _handle_reentry(
        self,
        reentry: str,
        source: str,
        waiting: dict[str, int],
        ready: deque[str],
        loop_iters: dict[str, int],
        completed: set[str],
        cancelled: set[str],
        ctx: ExecutionContext,
    ):
        """处理循环重入：重置循环区域入度，重入点入队。"""
        loop_iters[reentry] = loop_iters.get(reentry, 0) + 1
        if loop_iters[reentry] > MAX_LOOP:
            raise RuntimeError(f"循环超限: {reentry} (>{MAX_LOOP})")

        region = self._get_loop_region(reentry)

        # 重置区域内节点的等待计数器（只计区域内前驱）
        # 同时清除 completed 和 cancelled 状态，允许节点重新执行
        for node in region:
            waiting[node] = _region_in_degree(node, region, self.dag)
            completed.discard(node)
            cancelled.discard(node)

        # 重入点入度为 0，直接入队
        waiting[reentry] = 0
        ready.append(reentry)

        # 重置区域内更深层嵌套 LoopStep 的迭代计数
        # 当前循环的控制器不重置（需要维持跨迭代计数）：
        #   判断标准：LoopStep 的 True 分支沿 DAG 能到达某个反向边源
        #   — 能到达 → 它的 True 导致循环继续，是当前循环控制器
        #   — 不能到达 → 它是更深层嵌套循环的控制器，应 reset
        for node in region:
            try:
                step = self.registry.get(node)
                if isinstance(step, LoopStep) and not self._is_loop_controller(node, reentry):
                    step.reset(ctx)
            except Exception as e:
                logger.warning("重置循环步骤 %s 失败: %s", node, e)

        # 调用循环控制器的 loop_init（如果有）
        try:
            reentry_step = self.registry.get(reentry)
            if isinstance(reentry_step, LoopStep):
                reentry_step.loop_init(ctx)
        except Exception as e:
            logger.warning("循环初始化 %s 失败: %s", reentry, e)


# ======================================================================
# 兼容旧接口
# ======================================================================

def execute_graph(
    graph: CaseGraph,
    registry: StepRegistry,
    ctx: ExecutionContext,
    timeout: float = 0,
    event_callback=None,
) -> list[StepResult]:
    """执行用例图（兼容旧接口）。"""
    executor = GraphExecutor(graph, registry, event_callback=event_callback)
    return executor.execute(ctx, timeout=timeout)
