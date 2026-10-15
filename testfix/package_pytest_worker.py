"""Trusted pytest launcher for a pinned src-layout Python package."""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True)
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--package', required=True)
    parser.add_argument('--binding-json', required=True)
    parser.add_argument('pytest_args', nargs=argparse.REMAINDER)
    options = parser.parse_args()
    project = Path(options.project).resolve()
    source = (project / options.source_root).resolve()
    binding = Path(options.binding_json).resolve()
    record = {'schema': 'testfix.package-pytest-binding.v1', 'project': str(project), 'source': str(source),
              'package': options.package, 'imported': False, 'success': False, 'before_modules': {},
              'modules': {}, 'escaped_modules': [], 'error': ''}

    def save():
        binding.parent.mkdir(parents=True, exist_ok=True)
        binding.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')

    def modules():
        paths, escaped = {}, []
        for name, module in sorted(sys.modules.items()):
            if name != options.package and not name.startswith(options.package + '.'):
                continue
            location = getattr(module, '__file__', None)
            if not location:
                escaped.append(name)
                continue
            resolved = Path(location).resolve()
            paths[name] = str(resolved)
            if not resolved.is_relative_to(source):
                escaped.append(name)
        if options.package not in paths:
            escaped.append(options.package)
        return paths, sorted(set(escaped))

    try:
        if not source.is_relative_to(project) or not source.is_dir():
            raise ValueError('Package source must stay in selected repository')
        sys.path.insert(0, str(source))
        # Import only the package root. Upstream tests may patch dependencies
        # before importing its CLI module and must retain that behavior.
        importlib.import_module(options.package)
        record['before_modules'], escaped = modules()
        if escaped:
            raise ValueError('Initial package import escaped selected source root')
        record['imported'] = True
    except Exception as error:
        record['error'] = f'{type(error).__name__}: {error}'
        save()
        return 2
    import pytest

    class BindingPlugin:
        def pytest_sessionfinish(self, session, exitstatus):
            record['modules'], record['escaped_modules'] = modules()
            record['success'] = not record['escaped_modules']
            if not record['success']:
                record['error'] = 'Package source binding verification failed'
                session.exitstatus = pytest.ExitCode.TESTS_FAILED
            save()

    arguments = options.pytest_args
    if arguments[:1] == ['--']:
        arguments = arguments[1:]
    try:
        code = pytest.main(arguments, plugins=[BindingPlugin()])
    except BaseException as error:
        record['error'] = f'{type(error).__name__}: {error}'
        save()
        return 2
    if not binding.is_file():
        record['error'] = 'pytest ended without source binding evidence'
        save()
        return 2
    return int(code)


if __name__ == '__main__':
    raise SystemExit(main())
