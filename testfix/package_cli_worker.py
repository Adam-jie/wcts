"""Execute one fixed package CLI from an extracted wheel, never an editable tree."""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
from pathlib import Path
import runpy
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--wheel-root', required=True)
    parser.add_argument('--package', required=True)
    parser.add_argument('--module', required=True)
    parser.add_argument('--binding-json', required=True)
    parser.add_argument('cli_args', nargs=argparse.REMAINDER)
    options = parser.parse_args()
    wheel = Path(options.wheel_root).resolve()
    binding = Path(options.binding_json).resolve()
    record = {'schema': 'testfix.package-cli-binding.v1', 'wheel_root': str(wheel), 'package': options.package,
              'module': options.module, 'success': False, 'modules': {}, 'escaped_modules': [], 'exit_code': None}
    arguments = options.cli_args[1:] if options.cli_args[:1] == ['--'] else options.cli_args
    try:
        sys.path.insert(0, str(wheel))
        root = importlib.import_module(options.package)
        location = getattr(root, '__file__', None)
        if not location or not Path(location).resolve().is_relative_to(wheel):
            raise ValueError('Package import did not come from built wheel')
        spec = importlib.util.find_spec(options.module)
        if spec is not None and spec.submodule_search_locations is not None:
            spec = importlib.util.find_spec(options.module + '.__main__')
        if spec is None or not spec.origin or not Path(spec.origin).resolve().is_relative_to(wheel):
            raise ValueError('Executed CLI module did not come from built wheel')
        record['executed_module'] = str(Path(spec.origin).resolve())
        sys.argv = [options.module, *arguments]
        try:
            runpy.run_module(options.module, run_name='__main__', alter_sys=True)
            code = 0
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 0 if error.code is None else 1
        record['exit_code'] = code
    except BaseException as error:
        record['error'] = f'{type(error).__name__}: {error}'
        code = 1
    finally:
        for name, module in sorted(sys.modules.items()):
            if name != options.package and not name.startswith(options.package + '.'):
                continue
            location = getattr(module, '__file__', None)
            if not location:
                record['escaped_modules'].append(name)
                continue
            path = Path(location).resolve()
            record['modules'][name] = str(path)
            if not path.is_relative_to(wheel):
                record['escaped_modules'].append(name)
        record['success'] = bool(options.package in record['modules'] and not record['escaped_modules'] and 'error' not in record)
        binding.parent.mkdir(parents=True, exist_ok=True)
        binding.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return code if record['success'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
