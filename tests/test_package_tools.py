"""Adapter verification uses small explicit fixtures, never business-score cases."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import zipfile

import pytest

from testfix import package_tools as tools
from testfix.project_eval import _mapping_sha256
from testfix.project_tools import files_under


def configuration():
    return {'package': 'demo_pkg', 'source_root': 'src', 'regression': '', 'cli_module': 'demo_pkg',
            'distribution': 'demo-pkg', 'checks': [{'name': 'fixture CLI', 'args': [], 'files': {},
                                                  'expected_exit': 0, 'stdout_contains': ['public'], 'stdout_not_contains': []}]}


def mini_project(path):
    (path / 'src/demo_pkg').mkdir(parents=True)
    (path / 'src/demo_pkg/__init__.py').write_text('VALUE = "public"\n', encoding='utf-8')
    (path / 'src/demo_pkg/__main__.py').write_text('raise RuntimeError("must not preimport CLI")\n', encoding='utf-8')
    (path / 'tests').mkdir()
    (path / 'tests/test_public.py').write_text('import sys\nimport demo_pkg\ndef test_public():\n    assert demo_pkg.VALUE == "public"\n    assert "demo_pkg.__main__" not in sys.modules\n', encoding='utf-8')
    (path / 'README.md').write_text('fixture metadata', encoding='utf-8')
    return path


def request_fixture(tmp_path):
    root = tmp_path
    run_id = 'a' * 32
    work = root / 'artifacts' / run_id / 'workspace'
    for name in ('baseline', 'candidate'):
        mini_project(work / name)
    for name in ('generated_tests', 'private/acceptance_tests', 'private/regression_tests'):
        path = work / name
        path.mkdir(parents=True)
        (path / 'test_public.py').write_text('def test_public():\n    assert True\n', encoding='utf-8')
    python = root / 'python.exe'
    python.write_bytes(b'explicit interpreter fixture')
    output = root / 'artifacts' / run_id / 'tool_calls' / 'attempt-1' / 'result.json'
    config = configuration()
    config['regression'] = str(work / 'private/regression_tests')
    return {'root': str(root), 'run_id': run_id, 'adapter': 'python_package', 'operation': 'candidate_public',
            'baseline': str(work / 'baseline'), 'candidate': str(work / 'candidate'), 'generated': str(work / 'generated_tests'),
            'acceptance': str(work / 'private/acceptance_tests'), 'python': str(python), 'output': str(output), 'package_config': config}


def fake_suite(directory, name, *, successful=True):
    directory.mkdir(parents=True, exist_ok=True)
    stdout, stderr = directory / (name + '.stdout'), directory / (name + '.stderr')
    stdout.write_text(name, encoding='utf-8')
    stderr.write_text('', encoding='utf-8')
    return {'total': 1, 'passed': int(successful), 'cases': [{'id': 'same::test', 'status': 'passed' if successful else 'failure'}],
            'case_ids': ['same::test'], 'duplicate_case_ids': [], 'collection_errors': 0, 'successful': successful,
            'runnable': True, 'timeout': False, 'exit_code': 0 if successful else 1, 'parse_error': '', 'binding_error': '',
            'duration_s': .01, 'stdout_path': str(stdout), 'stderr_path': str(stderr)}


def test_public_operation_hashes_all_files_and_separates_upstream_from_behavior(tmp_path, monkeypatch):
    request = request_fixture(tmp_path)
    calls = []
    def suite(python, project, tests, directory, name, config):
        calls.append(tests)
        return fake_suite(directory, name)
    monkeypatch.setattr(tools, 'run_package_suite', suite)
    guard = SimpleNamespace(backend='unit', memory_bytes=123, cpu_seconds=2)
    result = tools.run_package_operation(request, guard)
    candidate = Path(request['candidate'])
    assert result['candidate_sha256'] == _mapping_sha256(files_under(candidate))
    assert calls == [candidate / 'tests', Path(request['package_config']['regression']), Path(request['generated'])]
    assert result['upstream_regression']['total'] == result['behavior_regression']['total'] == 1
    assert result['regression']['case_ids'] == ['upstream::same::test', 'behavior::same::test']
    assert result['regression']['successful'] is True
    previous = result['candidate_sha256']
    (candidate / 'README.md').write_text('changed metadata', encoding='utf-8')
    assert tools.run_package_operation(request, guard)['candidate_sha256'] != previous


def test_behavior_failure_cannot_be_hidden_by_passing_upstream(tmp_path):
    upstream, behavior = fake_suite(tmp_path, 'upstream'), fake_suite(tmp_path, 'behavior', successful=False)
    result = tools._merge_regression(upstream, behavior, tmp_path, 'combined')
    assert result['successful'] is False and result['passed'] == 1 and result['total'] == 2


@pytest.mark.parametrize('field,value', [('generated', '../outside'), ('acceptance', '/outside'), ('output', '/outside')])
def test_operation_rejects_paths_outside_its_sealed_workspace(tmp_path, field, value):
    request = request_fixture(tmp_path)
    request[field] = value
    with pytest.raises(ValueError):
        tools.run_package_operation(request, SimpleNamespace(backend='unit', memory_bytes=1, cpu_seconds=1))


def test_operation_refuses_original_private_regression_path(tmp_path):
    request = request_fixture(tmp_path)
    request['package_config']['regression'] = str(tmp_path / 'original-regression')
    with pytest.raises(ValueError, match='sealed private'):
        tools.run_package_operation(request, SimpleNamespace(backend='unit', memory_bytes=1, cpu_seconds=1))


def test_verify_operation_uses_separate_delivery_directory(tmp_path, monkeypatch):
    request = request_fixture(tmp_path)
    request['operation'] = 'verify'
    monkeypatch.setattr(tools, 'run_package_suite', lambda python, project, tests, directory, name, config: fake_suite(directory, name))
    seen = []
    def delivery(python, project, directory, config):
        seen.append(directory)
        return {'build': {'passed': True}, 'cli': {'passed': 1, 'total': 1, 'successful': True, 'checks': []}}
    monkeypatch.setattr(tools, 'verify_package_delivery', delivery)
    result = tools.run_package_operation(request, SimpleNamespace(backend='unit', memory_bytes=1, cpu_seconds=1))
    assert result['baseline']['total'] == result['acceptance']['total'] == 1
    assert seen == [Path(request['output']).parent / 'delivery']
    assert 'cli' in result['delivery'] and 'http' not in result['delivery']


def test_actual_pytest_worker_binds_src_root_without_preimporting_cli(tmp_path):
    project = mini_project(tmp_path / 'project')
    with (project / 'tests/test_public.py').open('a',encoding='utf-8') as stream:
        stream.write('    import os\n    assert os.environ["COLUMNS"] == "200"\n    assert os.environ["LINES"] == "60"\n')
    result = tools.run_package_suite(Path(sys.executable), project, project / 'tests', tmp_path / 'evidence', 'fixture', configuration())
    assert result['successful'] is True and result['total'] == result['passed'] == 1
    assert result['binding']['before_modules'] == {'demo_pkg': str((project / 'src/demo_pkg/__init__.py').resolve())}
    assert result['command'][1:3] == ['-I', '-B']


def test_suite_identity_is_exactly_paired_across_repository_roots(tmp_path):
    baseline = mini_project(tmp_path / 'baseline')
    candidate = mini_project(tmp_path / 'candidate')
    suites = [tools.run_package_suite(Path(sys.executable), project, project / 'tests',
                                      tmp_path / 'evidence', project.name, configuration())
              for project in (baseline, candidate)]
    assert all(suite['successful'] for suite in suites)
    assert suites[0]['case_ids'] == suites[1]['case_ids'] == ['test_public::test_public']
    assert all(suite['command'][suite['command'].index('--rootdir') + 1] == str(project / 'tests')
               for suite, project in zip(suites, (baseline, candidate)))


@pytest.mark.parametrize('body', ['import pytest\n@pytest.mark.skip(reason="fixture")\ndef test_skip(): pass\n',
                                'def no_test_collected(): pass\n'])
def test_skipped_or_empty_suite_cannot_pass(tmp_path, body):
    project = mini_project(tmp_path / 'project')
    (project / 'tests/test_public.py').write_text(body, encoding='utf-8')
    result = tools.run_package_suite(Path(sys.executable), project, project / 'tests', tmp_path / 'evidence', 'fixture', configuration())
    assert result['successful'] is False


def test_escaped_submodule_binding_fails_even_when_assertion_passes(tmp_path):
    project = mini_project(tmp_path / 'project')
    (project / 'tests/test_public.py').write_text('import sys,types\ndef test_fixture():\n    module=types.ModuleType("demo_pkg.escape")\n    module.__file__="/outside/source.py"\n    sys.modules[module.__name__]=module\n    assert True\n', encoding='utf-8')
    result = tools.run_package_suite(Path(sys.executable), project, project / 'tests', tmp_path / 'evidence', 'fixture', configuration())
    assert result['passed'] == 1 and result['successful'] is False and result['binding_error']


@pytest.mark.parametrize('path', ['../file', '/absolute', 'C:/absolute', 'nested\\file', '.env'])
def test_check_fixture_path_escape_is_rejected(path):
    config = configuration()
    config['checks'][0]['files'] = {path: 'fixture'}
    with pytest.raises(ValueError):
        tools._config(config)


def test_arbitrary_command_check_is_rejected():
    config = configuration()
    config['checks'][0]['command'] = ['shell', 'anything']
    with pytest.raises(ValueError):
        tools._config(config)


def wheel_fixture(path, *, malicious=None):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('demo_pkg/__init__.py', '')
        archive.writestr('demo_pkg/__main__.py', 'from pathlib import Path\nimport os\ntext=Path("fixture.txt").read_text(encoding="utf-8")\nprint("public "+text)\nprint("viewport="+os.environ.get("COLUMNS", "missing")+"x"+os.environ.get("LINES", "missing"))\nPath("result.txt").write_text(text,encoding="utf-8")\n')
        archive.writestr('demo_pkg-1.0.dist-info/METADATA', 'Metadata-Version: 2.1\nName: demo-pkg\nVersion: 1.0\n')
        if malicious:
            archive.writestr(malicious, 'escape')


@pytest.mark.parametrize('member', ['../escape.py', '/absolute.py', 'C:/escape.py', 'demo_pkg\\escape.py'])
def test_wheel_extraction_rejects_escape_members(tmp_path, member):
    wheel = tmp_path / 'fixture.whl'
    wheel_fixture(wheel, malicious=member)
    if '\\' in member:
        # ZipInfo normalizes native separators on Windows. Restore hostile ZIP
        # filename bytes in both headers so the adapter sees the actual input.
        wheel.write_bytes(wheel.read_bytes().replace(member.replace('\\', '/').encode(), member.encode()))
    with pytest.raises(ValueError):
        tools._extract_wheel(wheel, tmp_path / 'unpacked', configuration())
    assert not (tmp_path / 'unpacked').exists()


def test_delivery_uses_built_wheel_and_independent_task_directories(tmp_path, monkeypatch):
    project = mini_project(tmp_path / 'project')
    config = configuration()
    config['checks'] = [{**config['checks'][0], 'name': word, 'files': {'fixture.txt': word},
                         'stdout_contains': ['public ' + word, 'viewport=200x60'],
                         'export': {'path': 'result.txt', 'contains': [word], 'not_contains': ['missing']}}
                        for word in ('first', 'second')]
    original = tools._run_bounded
    def build(command, **kwargs):
        if 'build' not in command:
            return original(command, **kwargs)
        output = Path(command[command.index('--outdir') + 1])
        output.mkdir()
        wheel_fixture(output / 'demo_pkg-1.0-py3-none-any.whl')
        return {'exit_code': 0, 'timeout': False, 'output_limit_exceeded': False, 'stdout': 'explicit mock build', 'stderr': '', 'duration_s': .01}
    monkeypatch.setattr(tools, '_run_bounded', build)
    directory = tmp_path / 'delivery-attempt'
    result = tools.verify_package_delivery(Path(sys.executable), project, directory, config)
    assert result['build']['passed'] and result['cli']['successful']
    assert result['cli']['passed'] == result['cli']['total'] == 2
    assert {Path(check['cwd']).name for check in result['cli']['checks']} == {'cli-000', 'cli-001'}
    assert all(check['binding']['executed_module'].endswith('demo_pkg\\__main__.py') or check['binding']['executed_module'].endswith('demo_pkg/__main__.py') for check in result['cli']['checks'])
    assert 'http' not in result
    with pytest.raises(FileExistsError):
        tools.verify_package_delivery(Path(sys.executable), project, directory, config)


def test_bounded_subprocess_enforces_output_and_wall_limit(tmp_path):
    result = tools._run_bounded([sys.executable, '-I', '-B', '-c', 'print("x"*2000000)'], cwd=tmp_path, timeout_s=3)
    assert result['output_limit_exceeded'] is True
    assert len(result['stdout'].encode()) + len(result['stderr'].encode()) <= tools.OUTPUT_LIMIT
    timeout = tools._run_bounded([sys.executable, '-I', '-B', '-c', 'import time; time.sleep(10)'], cwd=tmp_path, timeout_s=.2)
    assert timeout['timeout'] is True and timeout['duration_s'] < 4


def test_tool_worker_dispatches_package_after_resource_guard(tmp_path, monkeypatch):
    from testfix import project_tool_worker
    request = request_fixture(tmp_path)
    request_path = Path(request['output']).parent / 'request.json'
    request_path.parent.mkdir(parents=True)
    request_path.write_text(json.dumps(request), encoding='utf-8')
    guard = object()
    seen = []
    monkeypatch.setattr(project_tool_worker, 'apply_limits', lambda **kwargs: guard)
    monkeypatch.setattr(tools, 'run_package_operation', lambda value, resource: seen.append((value, resource)))
    monkeypatch.setattr(sys, 'argv', ['worker', str(request_path)])
    assert project_tool_worker.main() == 0 and seen == [(request, guard)]
