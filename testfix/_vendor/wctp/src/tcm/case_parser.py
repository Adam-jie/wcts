"""Mermaid graph 解析与校验。"""

import re
from dataclasses import dataclass, field

from src.tcm.step_registry import StepRegistry, StepType


@dataclass
class CaseGraph:
    case_id: str
    nodes: set[str]
    edges: dict[str, list[tuple[str, str | None]]] = field(default_factory=dict)
    entry: str = ""
    exit: str = ""
    orientation: str = "LR"  # "LR" 或 "TD"


def _strip_label(node: str) -> str:
    """去除 mermaid 节点标签语法: step_ConfigParam[加载配置] → step_ConfigParam。"""
    return re.sub(r'\s*\[[^\]]*\]\s*$', '', node.strip()).strip()


def _parse_edge(line: str) -> tuple[str, str, str | None] | None:
    """解析边行，无 --> 返回 None（跳过节点声明行如 A[label]）。

    支持的格式:
        A --> B
        A -->|True| B
        A --> |True| B        (mermaid 允许 --> 和 | 之间有空格)
        A[label] -->|True| B[label]
    """
    if "-->" not in line:
        return None
    # 规范化 -->|cond| 周围的空白: "--> |True|" → "-->|True|"
    line = re.sub(r'-->\s*\|', '-->|', line)
    if "-->|" in line:
        left, rest = line.split("-->|", 1)
        cond, right = rest.rsplit("|", 1)
        return _strip_label(left), _strip_label(right), cond.strip()
    else:
        left, right = line.split("-->", 1)
        return _strip_label(left), _strip_label(right), None


def parse_graph(case_id: str, graph_text: str) -> CaseGraph:
    """解析 mermaid graph 文本。"""
    nodes: set[str] = set()
    edges: dict[str, list[tuple[str, str | None]]] = {}
    in_deg: dict[str, int] = {}
    orientation = "LR"

    for line in graph_text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("graph "):
            parts = line.split()
            if len(parts) > 1 and parts[1].upper() in ("LR", "TD"):
                orientation = parts[1].upper()
            continue
        parsed = _parse_edge(line)
        if parsed is None:
            continue  # 节点声明行 A[label]
        src, tgt, cond = parsed
        nodes.add(src)
        nodes.add(tgt)
        edges.setdefault(src, []).append((tgt, cond))
        in_deg[tgt] = in_deg.get(tgt, 0) + 1
        in_deg.setdefault(src, 0)

    # 入口：入度为 0 的非 EndStep（严格唯一）
    entries = [n for n in nodes if in_deg.get(n, 0) == 0 and n != "step_End"]
    entry = entries[0] if len(entries) == 1 else ""

    # 出口：出度为 0
    exit_node = ""
    for n in nodes:
        if n not in edges or not edges[n]:
            exit_node = n
            break

    return CaseGraph(case_id=case_id, nodes=nodes, edges=edges,
                     entry=entry, exit=exit_node, orientation=orientation)


# ------------------------------------------------------------------
# 反向边检测（供校验使用）
# ------------------------------------------------------------------

def find_back_edges(graph: CaseGraph) -> set[tuple[str, str]]:
    """DFS 检测反向边。"""
    from src.engine.graph_executor import find_back_edges as _find
    return _find(graph)


# ------------------------------------------------------------------
# 校验
# ------------------------------------------------------------------

def validate_graph(graph: CaseGraph, registry: StepRegistry) -> list[str]:
    """校验用例图。返回错误列表，空列表 = 合法。"""
    errors: list[str] = []

    # ① 必须有唯一的入口节点
    if not graph.entry:
        # 进一步检测：是完全没有入口还是多个入口
        in_deg_dict: dict[str, int] = {}
        for src, edges_list in graph.edges.items():
            in_deg_dict.setdefault(src, 0)
            for tgt, _ in edges_list:
                in_deg_dict[tgt] = in_deg_dict.get(tgt, 0) + 1
        entries_found = [n for n in graph.nodes if in_deg_dict.get(n, 0) == 0 and n != "step_End"]
        if not entries_found:
            errors.append("缺少入口节点（入度为 0 的步骤）")
        else:
            errors.append(f"存在多个入口节点（入度为 0）：{entries_found}")

    # ② 必须有唯一的 EndStep 出口
    end_nodes = [n for n in graph.nodes if n not in graph.edges or not graph.edges[n]]
    end_steps = [n for n in end_nodes if _step_type(n, registry) == StepType.END]
    if not end_steps:
        errors.append("缺少 EndStep")
    elif len(end_steps) > 1:
        errors.append(f"存在多个 EndStep: {end_steps}")

    # ③ 所有节点必须已在步骤库中注册
    for n in graph.nodes:
        try:
            registry.get(n)
        except Exception:
            errors.append(f"步骤未定义: {n}")

    # ④ ConditionalStep / LoopStep 必须有恰好两条出边（True + False）
    for src, outgoing in graph.edges.items():
        st = _step_type(src, registry)
        if st in (StepType.CONDITIONAL, StepType.LOOP):
            if len(outgoing) != 2:
                errors.append(
                    f"{st.value} {src} 需恰好 2 条出边，实际 {len(outgoing)}"
                )
            else:
                labels = {c for _, c in outgoing}
                if labels != {"True", "False"}:
                    errors.append(
                        f"{st.value} {src} 出边须标注 True/False，实际: {labels}"
                    )

    # ⑤ 并行分支的 output 变量不能同名（写冲突）
    #   对出度 > 1 的 NormalStep，检查直接后继的 output 不重名
    for src, outgoing in graph.edges.items():
        if len(outgoing) < 2:
            continue
        st = _step_type(src, registry)
        if st in (StepType.CONDITIONAL, StepType.LOOP):
            continue  # 条件/循环步骤不会同时激活两个分支
        # NormalStep 出度 > 1 → 并行分叉
        seen: dict[str, str] = {}
        for tgt, _ in outgoing:
            sd = registry.get(tgt)
            for var in sd.output:
                if var in seen:
                    errors.append(
                        f"并行步骤 {tgt} 与 {seen[var]} 输出同名变量 '{var}'，存在写冲突"
                    )
                else:
                    seen[var] = tgt

    return errors


def _step_type(step_id: str, registry: StepRegistry) -> StepType | None:
    try:
        return registry.get(step_id).step_type
    except Exception:
        return None


# ------------------------------------------------------------------
# Sequence → Mermaid 转换
# ------------------------------------------------------------------

def sequence_to_graph(sequence: list) -> str:
    """将 sequence 声明式格式转换为 mermaid graph 文本。

    sequence 每项可以是:
      - 字符串: step_id → NormalStep，串行连接
      - dict:
          parallel: [step_ids]  → 并行分叉，下一项为汇聚点
          loop: step_id         → LoopStep，body 为循环体
          condition: step_id    → ConditionalStep，on_true/on_false 为分支目标

    特殊关键字:
      __loop__  → 指向当前循环的 loop step
      __exit__  → 指向当前循环的出口
      __end__   → 指向 step_End
    """
    edges: list[tuple[str, str, str | None]] = []

    def _first(item):
        if isinstance(item, str):
            return item
        if isinstance(item, dict):
            for key in ("loop", "condition", "parallel"):
                if key in item:
                    val = item[key]
                    return val[0] if isinstance(val, list) else val
        return None

    def _last_steps(block: list) -> set[str]:
        result: set[str] = set()
        for item in block:
            if isinstance(item, str):
                result = {item}
            elif isinstance(item, dict):
                if "parallel" in item:
                    result = set(item["parallel"])
                elif "loop" in item:
                    body = item.get("body", [])
                    result = _last_steps(body) if body else {item["loop"]}
                elif "condition" in item:
                    bt = item.get("body_true", [])
                    bf = item.get("body_false", [])
                    if bt or bf:
                        result = _last_steps(bt) | _last_steps(bf)
                    else:
                        result = {item["condition"]}
        return result

    def _resolve(target: str, loop_info: dict | None) -> str:
        if not target:
            return ""
        kw_map = {"__loop__": "loop", "__exit__": "exit", "__end__": "step_End"}
        if target in kw_map:
            if kw_map[target] == "step_End":
                return "step_End"
            if loop_info:
                return loop_info.get(kw_map[target], "")
            return ""
        return target

    def _walk(block: list, predecessor: str | None, loop_info: dict | None):
        prev = predecessor
        i = 0
        while i < len(block):
            item = block[i]

            if isinstance(item, str):
                if prev:
                    edges.append((prev, item, None))
                prev = item
                i += 1

            elif isinstance(item, dict):
                if "parallel" in item:
                    psteps = item["parallel"]
                    join = None
                    if i + 1 < len(block):
                        join = _first(block[i + 1])
                    if prev:
                        for ps in psteps:
                            edges.append((prev, ps, None))
                    if join:
                        for ps in psteps:
                            edges.append((ps, join, None))
                        prev = join
                        i += 2
                    else:
                        i += 1

                elif "loop" in item:
                    loop = item["loop"]
                    body = item.get("body", [])
                    exit_step = _first(block[i + 1]) if i + 1 < len(block) else None
                    false_target = exit_step or "step_End"

                    if prev:
                        edges.append((prev, loop, None))
                    if body:
                        body_first = _first(body[0])
                        edges.append((loop, body_first, "True"))
                        edges.append((loop, false_target, "False"))
                        inner_info = {"loop": loop, "exit": false_target}
                        _walk(body, None, inner_info)
                        body_last = _last_steps(body)
                        for ls in body_last:
                            if not any(e[0] == ls and e[1] == loop for e in edges):
                                edges.append((ls, loop, None))
                        # False 分支已连到出口，跳过外层串行连接避免冗余边
                        if exit_step:
                            prev = exit_step
                            i += 2
                            continue
                    prev = loop
                    i += 1

                elif "condition" in item:
                    cond = item["condition"]
                    on_t = _resolve(item.get("on_true", ""), loop_info)
                    on_f = _resolve(item.get("on_false", ""), loop_info)
                    body_t = item.get("body_true", [])
                    body_f = item.get("body_false", [])
                    join = _first(block[i + 1]) if i + 1 < len(block) else None

                    if prev:
                        edges.append((prev, cond, None))

                    if body_t and body_f:
                        # 双分支各自有 body，汇聚到 join
                        t_first = _first(body_t[0])
                        f_first = _first(body_f[0])
                        edges.append((cond, t_first, "True"))
                        edges.append((cond, f_first, "False"))
                        _walk(body_t, None, loop_info)
                        _walk(body_f, None, loop_info)
                        t_last = _last_steps(body_t)
                        f_last = _last_steps(body_f)
                        if join:
                            for s in t_last | f_last:
                                edges.append((s, join, None))
                            prev = join
                            i += 2
                        else:
                            prev = cond
                            i += 1
                    elif on_t or on_f:
                        if on_t:
                            edges.append((cond, on_t, "True"))
                        if on_f:
                            edges.append((cond, on_f, "False"))
                        prev = cond
                        i += 1
                    else:
                        prev = cond
                        i += 1

    _walk(sequence, None, None)

    lines = ["graph LR"]
    for src, tgt, cond in edges:
        if cond:
            lines.append(f"    {src} -->|{cond}| {tgt}")
        else:
            lines.append(f"    {src} --> {tgt}")
    return "\n".join(lines)
