class WctpError(Exception):
    """WCTP 根异常。"""


class ConfigError(WctpError):
    """配置加载或解析错误。"""


class DeviceError(WctpError):
    """设备通信或操作错误。"""


class TransportError(DeviceError):
    """传输层错误（VISA/SSH/ZMQ/Serial）。"""


class StepExecutionError(WctpError):
    """步骤执行错误。"""


class GraphParseError(WctpError):
    """用例 graph 解析错误。"""


class EnvironmentError(WctpError):
    """环境分配或健康检查错误。"""


class TimeoutError(WctpError):
    """执行超时错误。"""
