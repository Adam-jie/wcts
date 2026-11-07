"""执行上下文 — 每个 test case 拥有独立实例，变量完全隔离。"""

from threading import RLock

from src.infra.env import EnvBlueprint
from src.infra.logkit import get_logger


class ExecutionContext:
    """测试用例执行上下文。

    每个 case 创建独立实例，case 间变量不共享。
    用例串行执行，不需要跨 case 状态。
    RLock 保护并行步骤下的变量读写。
    """

    def __init__(self, case_id: str, env: EnvBlueprint, config: dict = None, case_name: str = None):
        self.case_id = case_id
        self.case_name = case_name or case_id  # 向后兼容：未提供时使用 case_id
        self._env = env
        self._variables: dict[str, object] = {}
        self._lock = RLock()
        self.config = config or {}
        self.logger = get_logger(f"case.{case_id}")

    # ------------------------------------------------------------------
    # 设备访问
    # ------------------------------------------------------------------

    def get_instrument(self, name: str):
        return self._env.get_instrument(name)

    def get_dut(self, name: str = ""):
        return self._env.get_dut(name)

    def get_env(self) -> EnvBlueprint:
        return self._env

    # ------------------------------------------------------------------
    # 步骤间变量传递
    # ------------------------------------------------------------------

    def get_variable(self, name: str, default=None, removal: bool = False):
        with self._lock:
            if removal:
                return self._variables.pop(name, default)
            return self._variables.get(name, default)

    def set_variable(self, name: str, value):
        with self._lock:
            self._variables[name] = value

    def get_variables(self) -> dict:
        """返回当前所有变量的副本。"""
        with self._lock:
            return dict(self._variables)
