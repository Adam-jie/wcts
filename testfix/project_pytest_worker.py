"""Small trusted pytest launcher that binds imports to one project tree."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--binding-json", required=True)
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    project = Path(options.project).resolve()
    binding_path = Path(options.binding_json).resolve()
    record = {"schema": "testfix.pytest-binding.v1", "project": str(project), "imported": False,
              "success": False, "modules": {}, "escaped_modules": [], "error": ""}

    def save() -> None:
        binding_path.parent.mkdir(parents=True, exist_ok=True)
        binding_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        sys.path.insert(0, str(project))
        import flaskr  # noqa: PLC0415 - import must happen after binding sys.path
        location = getattr(flaskr, "__file__", None)
        if not location or not _within(Path(location), project):
            raise RuntimeError("initial flaskr import escaped the selected project")
        record["imported"] = True
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        save()
        return 2

    import pytest  # noqa: PLC0415

    class BindingPlugin:
        def pytest_sessionfinish(self, session, exitstatus):
            modules = {}
            escaped = []
            for name, module in sorted(sys.modules.items()):
                if name != "flaskr" and not name.startswith("flaskr."):
                    continue
                location = getattr(module, "__file__", None)
                if not location:
                    escaped.append(name)
                    continue
                resolved = Path(location).resolve()
                modules[name] = str(resolved)
                if not _within(resolved, project):
                    escaped.append(name)
            record["modules"] = modules
            record["escaped_modules"] = escaped
            record["success"] = bool("flaskr" in modules and not escaped)
            if not record["success"]:
                record["error"] = "flaskr module binding verification failed"
                session.exitstatus = pytest.ExitCode.TESTS_FAILED
            save()

    arguments = options.pytest_args
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    try:
        code = pytest.main(arguments, plugins=[BindingPlugin()])
    except BaseException as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        save()
        return 2
    if not binding_path.is_file():
        record["error"] = "pytest ended without binding evidence"
        save()
        return 2
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
