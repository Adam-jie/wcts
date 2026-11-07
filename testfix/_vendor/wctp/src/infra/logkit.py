"""WCTP 日志系统 — 两阶段 setup：控制台 + 会话文件。

环境变量:
    WCTP_LOG_DIR        日志根目录，默认项目根下的 log/
    WCTP_LOG_RETENTION  保留的最近会话目录数，默认 10，0=不清理
"""

import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from src.infra.config import get_project_root

_LOGGERS: dict[str, logging.Logger] = {}
_SESSION_DIR: Path | None = None
_CONSOLE_SETUP_DONE = False

DEFAULT_FORMAT = "%(asctime)s|%(levelname)8s|\t%(name)-32s    |%(lineno)20d|  \t%(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 可通过环境变量 WCTP_LOG_DIR 覆盖日志根目录，默认使用项目根下的 log/
_LOG_ROOT = Path(os.environ.get("WCTP_LOG_DIR", str(get_project_root() / "log")))
_LOG_RETENTION = int(os.environ.get("WCTP_LOG_RETENTION", "10"))


def _cleanup_old_sessions(log_root: Path, keep: int) -> None:
    """删除旧的会话目录，只保留最近 keep 个（按修改时间排序）。keep=0 不清理。"""
    if keep <= 0 or not log_root.exists():
        return
    sessions = sorted(
        [d for d in log_root.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    if len(sessions) <= keep:
        return
    for old_dir in sessions[keep:]:
        try:
            shutil.rmtree(old_dir)
            print(f"[logkit] 清理旧日志: {old_dir.name}", file=sys.stderr)
        except Exception:
            pass


def setup_console_logging(level: int = logging.INFO) -> None:
    """启动控制台日志（程序入口调用一次）。"""
    global _CONSOLE_SETUP_DONE
    if _CONSOLE_SETUP_DONE:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(DEFAULT_FORMAT, DATE_FORMAT))

    root = logging.getLogger("wctp")
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False

    _CONSOLE_SETUP_DONE = True


def setup_session_logging(session_name: str, level: int = logging.DEBUG) -> Path:
    """为当前测试会话创建文件日志。返回会话目录路径。"""
    global _SESSION_DIR

    # 自动清理旧日志：保留最近 _LOG_RETENTION 个会话
    _cleanup_old_sessions(_LOG_ROOT, _LOG_RETENTION)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _SESSION_DIR = _LOG_ROOT / f"{timestamp}_{session_name}"
    _SESSION_DIR.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(_SESSION_DIR / "wctp.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(DEFAULT_FORMAT, DATE_FORMAT))
    file_handler.setLevel(level)

    logging.getLogger("wctp").addHandler(file_handler)

    logger = get_logger("session")
    logger.info("会话目录: %s", _SESSION_DIR)
    return _SESSION_DIR


def get_session_dir() -> Path | None:
    """返回当前会话目录。"""
    return _SESSION_DIR


def get_logger(name: str) -> logging.Logger:
    """获取命名空间 logger（如 'devices.vsa', 'engine.graph_executor'）。"""
    full_name = f"wctp.{name}"
    if full_name not in _LOGGERS:
        _LOGGERS[full_name] = logging.getLogger(full_name)
    return _LOGGERS[full_name]
