"""步骤注册表 — Step 类层次 + 公共/环境步骤合并加载。"""

import copy
import importlib
import sys
import time
from abc import ABC
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from src.engine.context import ExecutionContext
from src.infra.config import get_project_root, load_yaml
from src.infra.exceptions import ConfigError
from src.infra.logkit import get_logger


def now() -> str:
    return datetime.now().isoformat()


# ======================================================================
# 类型与结果
# ======================================================================

class StepType(str, Enum):
    NORMAL = "NormalStep"
    CONDITIONAL = "ConditionalStep"
    LOOP = "LoopStep"
    END = "EndStep"
    REUSE = "ReuseStep"
    SUBCASE = "SubCaseStep"


@dataclass
class StepResult:
    step_id: str
    status: bool
    duration: float
    outputs: dict
    error: str = ""
    start_time: str = ""
    end_time: str = ""


# ======================================================================
# Step 基类
# ======================================================================

class Step(ABC):
    """步骤基类 — 子类实现 action()，框架调用 execute()。

    execute() 封装参数注入、状态管理、超时控制、输出注册。
    action() 是步骤的具体动作，由子类决定行为。
    """

    def __init__(
        self,
        step_id: str,
        step_name: str,
        action: str = "",
        parameters: dict | None = None,
        output: dict | None = None,
        expected_result: str = "",
        timeout: int = 60,
        is_critical: bool = False,
    ):
        self.step_id = step_id
        self.step_name = step_name
        self.action_path = action
        self.parameters = parameters or {}
        self.output = output or {}
        self.expected_result = expected_result
        self.timeout = timeout
        self.is_critical = is_critical
        self._fn: Any = None  # 延迟解析，缓存

    # ----- 子类必须设置 -----
    @property
    def step_type(self) -> StepType:
        raise NotImplementedError

    # ----- action 解析 -----

    def _resolve_fn(self) -> Callable:
        """将 action 路径解析为 Python 函数，缓存结果。"""
        if self._fn is not None:
            return self._fn
        if not self.action_path:
            self._fn = self._default_action
            return self._fn
        module_path, func_name = self.action_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        self._fn = getattr(module, func_name)
        return self._fn

    @staticmethod
    def _default_action(ctx, **params) -> tuple[bool, dict]:
        """无 action 时的默认行为。"""
        return True, {}

    # ----- 子类覆盖 -----

    def action(self, ctx: ExecutionContext, **params) -> tuple[bool, dict]:
        """步骤具体动作。默认委托给解析出的函数。"""
        fn = self._resolve_fn()
        return fn(ctx, **params)

    # ----- 框架调用入口 -----

    def execute(self, ctx: ExecutionContext) -> StepResult:
        """框架调用入口：参数注入 → action() → 输出注册 → 超时控制。"""
        logger = get_logger("engine.step")

        # ① 参数注入
        resolved: dict[str, Any] = {
            "step_name": self.step_name,
            "step_id": self.step_id,
        }
        for name, default in self.parameters.items():
            value = ctx.get_variable(name)
            resolved[name] = value if value is not None else default

        # ② 注册默认 output（未设置时写入初始值）
        for key, value in self.output.items():
            if ctx.get_variable(key) is None:
                ctx.set_variable(key, value)

        # ③ 调用 action（带超时）
        logger.debug("执行: %s (%s), timeout=%ds", self.step_name, self.step_id, self.timeout)
        start_time = now()
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.action, ctx, **resolved)
            try:
                status, outputs = future.result(timeout=self.timeout)
                dur = time.perf_counter() - start
                end_time = now()
            except FutureTimeout:
                dur = time.perf_counter() - start
                end_time = now()
                logger.error("步骤 %s 超时 (%.1fs > %ds)", self.step_id, dur, self.timeout)
                return StepResult(
                    step_id=self.step_id, status=False, duration=dur, outputs={},
                    error=f"执行超时 ({self.timeout}s)", start_time=start_time, end_time=end_time,
                )
            except Exception as e:
                dur = time.perf_counter() - start
                end_time = now()
                logger.error("步骤 %s 异常: %s", self.step_id, e)
                return StepResult(
                    step_id=self.step_id, status=False, duration=dur, outputs={},
                    error=str(e), start_time=start_time, end_time=end_time,
                )

        # ④ 注册输出：支持 tuple（按 output 定义顺序映射）和 dict（向后兼容）
        if outputs:
            if isinstance(outputs, tuple):
                output_names = list(self.output.keys())
                for i, value in enumerate(outputs):
                    if i < len(output_names):
                        ctx.set_variable(output_names[i], value)
                if len(outputs) > len(output_names):
                    logger.warning(
                        "步骤 %s: action 返回了 %d 个值，但 output 仅定义了 %d 个 (%s)，"
                        "多余的返回值被忽略",
                        self.step_id, len(outputs), len(output_names),
                        ", ".join(output_names),
                    )
                elif len(outputs) < len(output_names):
                    logger.warning(
                        "步骤 %s: action 返回了 %d 个值，但 output 定义了 %d 个 (%s)，"
                        "部分 output 未被覆盖，将保留默认值",
                        self.step_id, len(outputs), len(output_names),
                        ", ".join(output_names),
                    )
            elif isinstance(outputs, dict):
                output_keys = set(self.output.keys())
                dict_keys = set(outputs.keys())
                extra_keys = dict_keys - output_keys
                missing_keys = output_keys - dict_keys
                if extra_keys:
                    logger.warning(
                        "步骤 %s: action 返回了未在 output 中定义的 key: %s",
                        self.step_id, ", ".join(extra_keys),
                    )
                if missing_keys:
                    logger.warning(
                        "步骤 %s: 部分 output 定义的 key 未被返回: %s，将保留默认值",
                        self.step_id, ", ".join(missing_keys),
                    )
                for key, value in outputs.items():
                    ctx.set_variable(key, value)

        logger.debug("步骤 %s: status=%s, %.2fs", self.step_id, status, dur)
        return StepResult(step_id=self.step_id, status=status, duration=dur, outputs=outputs, start_time=start_time, end_time=end_time)

    # ----- 辅助 -----

    def clone_with_id(self, new_id: str) -> "Step":
        """克隆步骤并替换 step_id，用于 registry 的 fallback 机制。"""
        new_step = copy.deepcopy(self)
        new_step.step_id = new_id
        new_step._fn = None  # 重新解析
        return new_step


# ======================================================================
# Step 子类
# ======================================================================

class NormalStep(Step):
    """普通步骤 — 激活所有后继（并行/汇聚/普通由图结构决定）。"""

    @property
    def step_type(self) -> StepType:
        return StepType.NORMAL


class ReuseStep(Step):
    """复用步骤：使用同一套设备和上下文执行被引用的子 Case。"""

    def __init__(self, *args, subcase_path: str = "", subcase_id: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.subcase_path = subcase_path
        self.subcase_id = subcase_id

    @property
    def step_type(self) -> StepType:
        return StepType.REUSE

    def action(self, ctx: ExecutionContext, **params) -> tuple[bool, dict]:
        from pathlib import Path

        import yaml

        from src.engine.graph_executor import execute_graph
        from src.infra.config import get_project_root
        from src.tcm.case_parser import parse_graph, validate_graph

        root = get_project_root().resolve()
        case_path = (root / self.subcase_path).resolve()
        repository_root = (root / "repository").resolve()
        if repository_root not in case_path.parents or not case_path.is_file():
            raise ValueError(f"无效的子 Case 路径: {self.subcase_path}")

        child = yaml.safe_load(case_path.read_text(encoding="utf-8")) or {}
        child_id = str(child.get("case_id") or self.subcase_id or case_path.stem)
        stack = list(ctx.get_variable("_subcase_stack", []))
        if child_id in stack:
            raise RuntimeError("检测到子 Case 递归调用: " + " -> ".join([*stack, child_id]))

        env_name = case_path.parent.parent.name
        registry = StepRegistry()
        registry.load_for_env(env_name)
        env_yaml = case_path.parent.parent / "env.yaml"
        if env_yaml.exists():
            env_data = yaml.safe_load(env_yaml.read_text(encoding="utf-8")) or {}
            base_name = str(env_data.get("base") or "")
            if base_name and base_name != env_name:
                registry.load_for_env(base_name)
        registry.register_inline(child.get("inline_steps"))
        graph = parse_graph(child_id, str(child.get("graph") or ""))
        if errors := validate_graph(graph, registry):
            raise RuntimeError("子 Case 校验失败: " + "; ".join(errors))

        logger = get_logger("engine.reuse_step")
        logger.info(
            "[复用步骤] 开始：%s（%s），内部节点 %d 个",
            child.get("case_name") or child_id,
            child_id,
            len(graph.nodes),
        )
        started_count = 0

        def report_child_progress(event, step_id, _result=None):
            nonlocal started_count
            if event == "started":
                started_count += 1
                logger.info("[复用步骤] 内部步骤 %d：%s", started_count, step_id)

        old_config = ctx.config
        ctx.config = {**(child.get("config") or {}), **old_config}
        ctx.set_variable("_subcase_stack", [*stack, child_id])
        try:
            results = execute_graph(
                graph, registry, ctx, event_callback=report_child_progress,
            )
        finally:
            ctx.config = old_config
            ctx.set_variable("_subcase_stack", stack)

        failed = []
        for result in results:
            child_step = registry.get(result.step_id)
            if result.error or (
                not result.status
                and child_step.step_type not in (StepType.CONDITIONAL, StepType.LOOP)
            ):
                failed.append(result.step_id)
        if failed:
            logger.error("[复用步骤] %s 执行失败：%s", child_id, "、".join(failed))
        else:
            logger.info("[复用步骤] %s 执行完成", child_id)
        if self.step_type == StepType.REUSE:
            summary = {
                "reuse_case_id": child_id,
                "reuse_steps": len(results),
                "reuse_failed_steps": failed,
            }
        else:
            summary = {
                "subcase_id": child_id,
                "subcase_steps": len(results),
                "subcase_failed_steps": failed,
            }
        return not failed, summary


class SubCaseStep(ReuseStep):
    """旧版复用步骤名称；仅用于兼容已经保存的 SubCaseStep。"""

    @property
    def step_type(self) -> StepType:
        return StepType.SUBCASE


class ConditionalStep(Step):
    """条件步骤 — 按返回值 True/False 选择分支。"""

    @property
    def step_type(self) -> StepType:
        return StepType.CONDITIONAL


class LoopStep(Step):
    """循环步骤 — 内置迭代管理，True=继续循环，False=退出循环。

    两种模式：
    - 内置模式（不指定 action）：自动管理 count/total
    - 自定义模式（指定 action）：委托给用户函数

    loop_init：循环初始化函数，首次进入和重入时调用。
    """

    def __init__(self, total: int = 0, loop_init: str = "", **kwargs):
        self.total = total
        self.loop_init_path = loop_init
        self._loop_init_fn: Any = None
        super().__init__(**kwargs)

    @property
    def step_type(self) -> StepType:
        return StepType.LOOP

    def _resolve_loop_init(self) -> Callable:
        """解析 loop_init 函数路径。"""
        if self._loop_init_fn is not None:
            return self._loop_init_fn
        if not self.loop_init_path:
            return None
        module_path, func_name = self.loop_init_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        self._loop_init_fn = getattr(module, func_name)
        return self._loop_init_fn

    def loop_init(self, ctx: ExecutionContext, **params) -> tuple[bool, dict]:
        """循环初始化 — 首次进入和重入时调用。"""
        fn = self._resolve_loop_init()
        if fn is None:
            return True, ()
        return fn(ctx, **params)

    def action(self, ctx: ExecutionContext, **params) -> tuple[bool, dict]:
        if self.action_path:
            # 自定义模式：委托给用户函数
            fn = self._resolve_fn()
            return fn(ctx, **params)
        # 内置模式：count/total
        key = f"_loop_count_{self.step_id}"
        count = ctx.get_variable(key, 0) + 1
        ctx.set_variable(key, count)
        step_name = params.get("step_name", self.step_id)
        logger = get_logger("engine.step")
        if count <= self.total:
            logger.info("[%s] 第 %d/%d 次循环", step_name, count, self.total)
            return True, {}
        ctx.set_variable(key, 0)  # 退出时重置
        logger.debug("[%s] 循环结束", step_name)
        return False, {}

    def reset(self, ctx: ExecutionContext):
        """外层循环重入时，重置迭代计数。"""
        ctx.set_variable(f"_loop_count_{self.step_id}", 0)


class EndStep(Step):
    """结束步骤 — 无后继，终止用例。"""

    @property
    def step_type(self) -> StepType:
        return StepType.END


# ======================================================================
# 步骤注册表
# ======================================================================

_STEP_CLASSES: dict[StepType, type[Step]] = {
    StepType.NORMAL: NormalStep,
    StepType.CONDITIONAL: ConditionalStep,
    StepType.LOOP: LoopStep,
    StepType.END: EndStep,
    StepType.REUSE: ReuseStep,
    StepType.SUBCASE: SubCaseStep,
}


class StepRegistry:
    """步骤注册表 — 合并公共仪表步骤与环境专用步骤。"""

    def __init__(self):
        self._steps: dict[str, Step] = {}
        self._env_paths: list[str] = []

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------

    def load_for_env(self, env_name: str):
        """加载 repository/common/ + repository/<env>/ 下的步骤。"""
        root = get_project_root()
        repo_dir = str(root / "repository")
        self._add_path(repo_dir)

        self._load_file(root / "repository" / "common" / "steps" / "steps.yaml")
        env_steps = root / "repository" / env_name / "steps" / "steps.yaml"
        if env_steps.exists():
            self._load_file(env_steps)

    def _load_file(self, path: Path):
        data = load_yaml(str(path.relative_to(get_project_root())))
        for entry in data:
            self._register_entry(entry)

    def register_inline(self, entries: list[dict] | None):
        """注册用例内联步骤；仅接受普通步骤定义，不执行任何动态代码生成。"""
        for entry in entries or []:
            if not isinstance(entry, dict):
                raise ValueError("inline_steps 条目必须是映射")
            if "step_id" not in entry or "step_name" not in entry:
                raise ValueError("inline_steps 缺少 step_id 或 step_name")
            if entry.get("action"):
                raise ValueError("inline_steps 不允许指定 action")
            self._register_entry(entry)

    def _register_entry(self, entry: dict):
        step_type_str = entry.get("step_type", "NormalStep")
        step_type = StepType(step_type_str)
        cls = _STEP_CLASSES[step_type]

        kwargs: dict[str, Any] = {
            "step_id": entry["step_id"],
            "step_name": entry["step_name"],
            "action": entry.get("action", ""),
            "parameters": entry.get("parameters", {}),
            "output": entry.get("output", {}),
            "expected_result": entry.get("expected_result", ""),
            "timeout": entry.get("timeout", 60),
            "is_critical": entry.get("is_critical", False),
        }
        if isinstance(cls, type) and issubclass(cls, LoopStep):
            kwargs["total"] = entry.get("total", 0)
            kwargs["loop_init"] = entry.get("loop_init", "")
        if isinstance(cls, type) and issubclass(cls, ReuseStep):
            kwargs["subcase_path"] = str(
                entry.get("reuse_case_path") or entry.get("subcase_path", "")
            )
            kwargs["subcase_id"] = str(
                entry.get("reuse_case_id") or entry.get("subcase_id", "")
            )

        step = cls(**kwargs)
        self._steps[step.step_id] = step

    def _add_path(self, path: str):
        if path not in sys.path:
            sys.path.insert(0, path)
            self._env_paths.append(path)

    # 兼容旧接口
    def load_from_yaml(self, yaml_path: str):
        self._load_file(get_project_root() / yaml_path)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get(self, step_id: str) -> Step:
        """获取步骤实例。未找到时递归去掉数字后缀查找基类步骤。

        例: step_Job2_1_1 → step_Job2_1 → step_Job2 → step_Job
        """
        if step_id in self._steps:
            return self._steps[step_id]
        import re
        base = re.sub(r"_\d+$", "", step_id)
        if base != step_id:
            try:
                parent = self.get(base)
                cloned = parent.clone_with_id(step_id)
                self._steps[step_id] = cloned
                return cloned
            except ConfigError:
                pass
        raise ConfigError(f"步骤未定义: {step_id}")

    def resolve(self, step_id: str) -> Callable:
        """返回步骤的 action 函数。"""
        step = self.get(step_id)
        return step._resolve_fn()

    @property
    def steps(self) -> dict[str, Step]:
        return self._steps
