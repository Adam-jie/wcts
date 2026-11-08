"""Fixed, bounded test-only action; model text never becomes a shell command."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

from .package_tools import _inside, run_package_suite, _merge_regression
from .project_eval import _run_suite, _mapping_sha256, _runtime_files, _sha256
from .project_tools import confined, files_under


def run_step(request, guard):
    root = Path(request['root']).resolve()
    identifier = request['run_id']
    if not re.fullmatch('[a-f0-9]{32}', identifier):
        raise ValueError('Invalid feature test run')
    run = root / 'artifacts' / identifier
    work = run / 'workspace'
    paths = {key: _inside(root, Path(request[key])) for key in ('baseline', 'candidate', 'generated', 'acceptance', 'output')}
    for key, name in {'baseline': 'baseline', 'candidate': 'candidate', 'generated': 'generated_tests',
                      'acceptance': 'private/acceptance_tests'}.items():
        if paths[key] != (work / name).resolve():
            raise ValueError('Feature action requires exact owned workspace paths')
    if not paths['output'].is_relative_to(run / 'tool_calls'):
        raise ValueError('Feature result must stay within owned tool calls')
    python = Path(request['python']).absolute()
    resolved_python = python.resolve(strict=True)
    adapter = request.get('adapter', 'flaskr')
    if adapter == 'python_application' and resolved_python != Path(sys.executable).resolve():
        raise ValueError('Application tests must use the host registered interpreter')
    if adapter != 'python_application' and not resolved_python.is_relative_to(root):
        raise ValueError('Registered interpreter must stay within workspace')
    candidate, directory = paths['candidate'], paths['output'].parent
    name = request.get('test_file')
    if name is not None and (not isinstance(name, str) or not re.fullmatch(r'test_[A-Za-z0-9_]+\.py', name)):
        raise ValueError('Only bare test filenames are allowed')
    tests = confined(paths['generated'], name, exists=True) if name else candidate / 'tests'
    if adapter == 'flaskr':
        suite = _run_suite(python, candidate, tests, directory, 'feature' if name else 'regression')
        digest = _mapping_sha256({p: _sha256(candidate / p) for p in sorted(_runtime_files(candidate))})
    elif adapter in {'python_application', 'python_package'}:
        config = request['application_config' if adapter == 'python_application' else 'package_config']
        if adapter == 'python_application' and config != {'package': 'taskboard', 'source_root': '.'}:
            raise ValueError('Unknown application adapter')
        suite = run_package_suite(python, candidate, tests, directory, 'feature' if name else 'regression', config)
        if not name and adapter == 'python_package':
            behavior = _inside(root, Path(config['regression']))
            if behavior != (work / 'private/regression_tests').resolve():
                raise ValueError('Behavioral regression must stay in owned frozen input')
            extra = run_package_suite(python, candidate, behavior, directory, 'behavior', config)
            suite = _merge_regression(suite, extra, directory, 'regression')
        digest = _mapping_sha256(files_under(candidate))
    else:
        raise ValueError('No registered feature test adapter')
    # Surface bounded assertion diagnostics as data, not just aggregate counts.
    import xml.etree.ElementTree as ET
    xml = suite.get('junit_path')
    if xml and Path(xml).is_file():
        details = {}
        for case in ET.parse(xml).getroot().iter('testcase'):
            key = case.attrib.get('classname', '') + '::' + case.attrib.get('name', '')
            details[key] = '\n'.join((child.text or child.attrib.get('message', ''))[:2500]
                                    for child in case if child.tag in {'failure', 'error', 'skipped'})[:3000]
        for case in suite['cases']:
            if details.get(case['id']):
                case['message'] = details[case['id']]
    result = {'operation': 'feature_tests', 'candidate_sha256': digest, 'suite': suite,
              'limits': {'backend': guard.backend, 'memory_bytes': guard.memory_bytes, 'cpu_seconds': guard.cpu_seconds}}
    paths['output'].write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
