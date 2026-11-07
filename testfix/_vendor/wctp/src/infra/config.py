"""YAML 配置加载与项目根查找。"""

from pathlib import Path

import yaml

_PROJECT_ROOT: Path | None = None


def find_project_root(start: Path | None = None) -> Path:
    """从 start 向上查找 .project-root，返回项目根目录。"""
    global _PROJECT_ROOT
    if _PROJECT_ROOT is not None:
        return _PROJECT_ROOT

    current = (start or Path.cwd()).resolve()
    for parent in [current, *current.parents]:
        if (parent / ".project-root").exists():
            _PROJECT_ROOT = parent
            return _PROJECT_ROOT

    raise FileNotFoundError("找不到 .project-root，请确保在项目目录下运行")


def get_project_root() -> Path:
    """返回已缓存的项目根目录。"""
    if _PROJECT_ROOT is None:
        return find_project_root()
    return _PROJECT_ROOT


def load_yaml(relative_path: str) -> dict:
    """加载项目根目录下的 YAML 文件。"""
    path = get_project_root() / relative_path
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_path(relative_path: str) -> Path:
    """返回相对于项目根的完整路径。"""
    return get_project_root() / relative_path
