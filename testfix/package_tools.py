"""Bounded test/build/CLI tools for registered src-layout Python packages.

Requests come from the sealed registered-project adapter. Candidate code is
trusted local Python, not an operating-system filesystem/network sandbox.
"""
from __future__ import annotations

from copy import deepcopy
from email.parser import Parser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import signal
import stat
import subprocess
import threading
import time
import uuid
import zipfile

from .project_eval import _cases, _environment, _mapping_sha256


OUTPUT_LIMIT = 1024 * 1024


def _relative(value: str) -> str:
    if (not isinstance(value, str) or not value or '\\' in value or PureWindowsPath(value).drive
            or PurePosixPath(value).is_absolute() or any(part in {'', '.', '..'} for part in value.split('/'))):
        raise ValueError('Package tool paths must be nonempty relative POSIX paths')
    if any(part.startswith('.env') for part in value.split('/')):
        raise ValueError('Environment files are not package tool inputs')
    return value


def _inside(root: Path, path: Path) -> Path:
    root = root.resolve()
    raw = path.absolute()
    if not raw.is_relative_to(root) or not raw.resolve().is_relative_to(root):
        raise ValueError('Package tool path escaped the registered workspace')
    cursor = raw
    while cursor != root:
        if cursor.is_symlink() or (hasattr(cursor, 'is_junction') and cursor.is_junction()):
            raise ValueError('Package tool symlinks/junctions are forbidden')
        cursor = cursor.parent
    return raw.resolve()


def _stop_tree(process):
    if os.name == 'nt':
        try:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=8,
                           creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.wait(timeout=8)


def _run_bounded(command: list[str], *, cwd: Path, timeout_s: float, stdin: str | None = None,
                 environment: dict[str, str] | None = None) -> dict:
    """Drain both pipes with a shared byte cap and terminate the owned tree."""
    started = time.monotonic()
    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
    env = _environment()
    env.update(environment or {})
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    lock, overflow = threading.Lock(), threading.Event()

    def drain(name, stream):
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            with lock:
                remaining = OUTPUT_LIMIT - sum(len(value) for value in buffers.values())
                buffers[name].extend(chunk[:max(remaining, 0)])
                if len(chunk) > remaining:
                    overflow.set()
                    break

    threads = [threading.Thread(target=drain, args=(name, getattr(process, name)), daemon=True) for name in buffers]
    for thread in threads:
        thread.start()
    # Trusted fixed CLI inputs are bounded before this call; use a writer so a
    # candidate that ignores stdin cannot block the wall-time supervisor.
    def write_input():
        try:
            process.stdin.write((stdin or '').encode('utf-8'))
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    writer = threading.Thread(target=write_input, daemon=True)
    writer.start()
    timed_out = False
    while process.poll() is None:
        if overflow.is_set() or time.monotonic() - started >= timeout_s:
            timed_out = not overflow.is_set()
            _stop_tree(process)
            break
        time.sleep(0.02)
    for thread in threads:
        thread.join(timeout=2)
    writer.join(timeout=1)
    pipe_cleanup_error = any(thread.is_alive() for thread in threads) or writer.is_alive()
    if pipe_cleanup_error:
        # A descendant retaining a pipe must not block BufferedReader.close().
        # The outer guarded tool worker also owns every descendant; fail this
        # operation rather than run additional checks with unresolved processes.
        _stop_tree(process)
    for thread, stream in zip(threads, (process.stdout, process.stderr)):
        if not thread.is_alive():
            stream.close()
    return {'exit_code': process.returncode, 'stdout': buffers['stdout'].decode('utf-8', 'replace'),
            'stderr': buffers['stderr'].decode('utf-8', 'replace'), 'timeout': timed_out or pipe_cleanup_error,
            'pipe_cleanup_error': pipe_cleanup_error,
            'output_limit_exceeded': overflow.is_set(), 'duration_s': round(time.monotonic() - started, 3)}


def run_package_suite(python: Path, project: Path, tests: Path, directory: Path, name: str, config: dict) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    stem = name + '-' + uuid.uuid4().hex
    xml, binding = directory / (stem + '.xml'), directory / (stem + '.binding.json')
    stdout, stderr = directory / (stem + '.stdout.txt'), directory / (stem + '.stderr.txt')
    worker = Path(__file__).with_name('package_pytest_worker.py').resolve()
    # Bind collection identity to the sealed suite itself. Repository/candidate
    # directories are execution locations, never part of a test's identity.
    suite_root = tests if tests.is_dir() else tests.parent
    command = [str(python), '-I', '-B', '-X', 'utf8', str(worker), '--project', str(project), '--source-root', config['source_root'],
               '--package', config['package'], '--binding-json', str(binding), '--', '-q', '-p', 'no:cacheprovider',
               '--rootdir', str(suite_root), '--basetemp', str(directory / (stem + '-tmp')), '--junitxml', str(xml), str(tests)]
    process = _run_bounded(command, cwd=project, timeout_s=90,
                           environment={'COLUMNS': '200', 'LINES': '60'})
    stdout.write_text(process['stdout'], encoding='utf-8')
    stderr.write_text(process['stderr'], encoding='utf-8')
    records, collection_errors, parse_error, binding_error, evidence = [], 0, '', '', {}
    try:
        records, collection_errors = _cases(xml)
    except ValueError as error:
        parse_error = str(error)
    try:
        evidence = json.loads(binding.read_text(encoding='utf-8'))
        source = (project / config['source_root']).resolve()
        groups = [evidence.get('before_modules'), evidence.get('modules')]
        if (evidence.get('schema') != 'testfix.package-pytest-binding.v1' or evidence.get('success') is not True
                or evidence.get('imported') is not True or evidence.get('escaped_modules')
                or Path(evidence.get('project', '')).resolve() != project.resolve()
                or Path(evidence.get('source', '')).resolve() != source
                or evidence.get('package') != config['package']
                or any(not isinstance(group, dict) or config['package'] not in group
                       or any(not Path(path).resolve().is_relative_to(source) for path in group.values()) for group in groups)):
            binding_error = 'Invalid package pytest source binding evidence'
    except (OSError, ValueError, TypeError):
        binding_error = 'Package pytest source binding evidence missing or invalid'
    ids = [case['id'] for case in records]
    duplicates = sorted({identifier for identifier in ids if ids.count(identifier) > 1})
    passed = sum(case['status'] == 'passed' for case in records)
    successful = (not process['timeout'] and not process['output_limit_exceeded'] and process['exit_code'] == 0
                  and not parse_error and not binding_error and collection_errors == 0 and not duplicates
                  and len(records) > 0 and passed == len(records))
    return {'total': len(records), 'passed': passed, 'cases': records, 'case_ids': ids, 'duplicate_case_ids': duplicates,
            'collection_errors': collection_errors, 'runnable': not process['timeout'] and not parse_error,
            'successful': successful, 'timeout': process['timeout'], 'exit_code': process['exit_code'],
            'output_limit_exceeded': process['output_limit_exceeded'], 'duration_s': process['duration_s'],
            'binding_path': str(binding), 'binding': evidence, 'binding_error': binding_error,
            'command': command, 'cwd': str(project), 'stdout_path': str(stdout), 'stderr_path': str(stderr),
            'junit_path': str(xml), 'parse_error': parse_error}


def _merge_regression(upstream: dict, behavior: dict, directory: Path, name: str) -> dict:
    suites = {'upstream': upstream, 'behavior': behavior}
    cases = [{**case, 'id': component + '::' + case['id']} for component, suite in suites.items() for case in suite['cases']]
    ids = [case['id'] for case in cases]
    output, errors = directory / (name + '-merged.stdout.txt'), directory / (name + '-merged.stderr.txt')
    output.write_text('\n'.join(f'[{label}]\n' + Path(suite['stdout_path']).read_text(encoding='utf-8') for label, suite in suites.items()), encoding='utf-8')
    errors.write_text('\n'.join(f'[{label}]\n' + Path(suite['stderr_path']).read_text(encoding='utf-8') for label, suite in suites.items()), encoding='utf-8')
    return {'total': len(cases), 'passed': sum(case['status'] == 'passed' for case in cases), 'cases': cases,
            'case_ids': ids, 'duplicate_case_ids': sorted({identifier for identifier in ids if ids.count(identifier) > 1}),
            'collection_errors': sum(suite['collection_errors'] for suite in suites.values()),
            'successful': all(suite['successful'] for suite in suites.values()),
            'runnable': all(suite['runnable'] for suite in suites.values()), 'timeout': any(suite['timeout'] for suite in suites.values()),
            'exit_code': 0 if all(suite['exit_code'] == 0 for suite in suites.values()) else 1,
            'parse_error': '; '.join(suite['parse_error'] for suite in suites.values() if suite['parse_error']),
            'binding_error': '; '.join(suite['binding_error'] for suite in suites.values() if suite['binding_error']),
            'duration_s': sum(suite['duration_s'] for suite in suites.values()),
            'stdout_path': str(output), 'stderr_path': str(errors),
            'components': {'upstream_regression': upstream, 'behavior_regression': behavior}}


def _config(value: dict) -> dict:
    required = {'package', 'source_root', 'regression', 'cli_module', 'distribution', 'checks'}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError('Package adapter requires its registered fixed configuration')
    for key in ('package', 'cli_module'):
        if not isinstance(value[key], str) or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', value[key]):
            raise ValueError('Invalid package/module name')
    if value['cli_module'] != value['package'] and not value['cli_module'].startswith(value['package'] + '.'):
        raise ValueError('CLI module must belong to the registered package')
    _relative(value['source_root'])
    if not isinstance(value['distribution'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value['distribution']):
        raise ValueError('Invalid distribution name')
    if not isinstance(value['checks'], list) or not value['checks'] or len(value['checks']) > 40:
        raise ValueError('CLI delivery requires a nonempty bounded fixed check list')
    names = set()
    for check in value['checks']:
        if (not isinstance(check, dict) or set(check) - {'name', 'args', 'stdin', 'files', 'expected_exit', 'stdout_contains', 'stdout_not_contains', 'export'}
                or not {'name', 'args', 'expected_exit', 'stdout_contains', 'stdout_not_contains'} <= set(check)):
            raise ValueError('Invalid fixed CLI check')
        if not isinstance(check['name'], str) or not check['name'].strip() or check['name'] in names:
            raise ValueError('CLI check names must be unique and nonempty')
        names.add(check['name'])
        if type(check['expected_exit']) is not int or not isinstance(check['args'], list) or len(check['args']) > 100 or any(not isinstance(arg, str) or '\x00' in arg for arg in check['args']):
            raise ValueError('Invalid fixed CLI arguments/exit code')
        if sum(len(arg.encode('utf-8')) for arg in check['args']) > 100_000:
            raise ValueError('CLI arguments exceed their byte limit')
        for key in ('stdout_contains', 'stdout_not_contains'):
            if not isinstance(check[key], list) or any(not isinstance(text, str) for text in check[key]):
                raise ValueError('CLI assertions must be strings')
        if not isinstance(check.get('stdin', ''), str) or len(check.get('stdin', '').encode('utf-8')) > 100_000:
            raise ValueError('CLI stdin exceeds its byte limit')
        files = check.get('files', {})
        if not isinstance(files, dict) or len(files) > 20:
            raise ValueError('Invalid fixed CLI fixture files')
        for path, source in files.items():
            _relative(path)
            if not isinstance(source, str) or len(source.encode('utf-8')) > 100_000:
                raise ValueError('CLI fixture exceeds its byte limit')
        if 'export' in check:
            export = check['export']
            if not isinstance(export, dict) or set(export) != {'path', 'contains', 'not_contains'}:
                raise ValueError('Invalid fixed CLI export check')
            _relative(export['path'])
            if any(not isinstance(export[key], list) or any(not isinstance(text, str) for text in export[key]) for key in ('contains', 'not_contains')):
                raise ValueError('CLI export assertions must be strings')
    return deepcopy(value)


def _extract_wheel(wheel: Path, destination: Path, config: dict) -> list[str]:
    with zipfile.ZipFile(wheel) as archive:
        members = archive.infolist()
        names = [member.filename for member in members]
        if config['package'].replace('.', '/') + '/__main__.py' not in names or len(names) != len(set(names)):
            raise ValueError('Built wheel is missing its CLI module or repeats members')
        if sum(member.file_size for member in members) > 64 * 1024 * 1024:
            raise ValueError('Built wheel exceeds its extracted byte limit')
        for member in members:
            _relative(member.orig_filename.rstrip('/'))
            _relative(member.filename.rstrip('/'))
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError('Wheel symlink members are forbidden')
            _inside(destination, destination / member.filename)
        metadata = [name for name in names if name.endswith('.dist-info/METADATA')]
        if len(metadata) != 1:
            raise ValueError('Built wheel must have one distribution metadata file')
        name = Parser().parsestr(archive.read(metadata[0]).decode('utf-8'))['Name'] or ''
        normalize = lambda text: re.sub(r'[-_.]+', '-', text).lower()
        if normalize(name) != normalize(config['distribution']):
            raise ValueError('Built wheel distribution does not match the registered package')
        archive.extractall(destination)
    return names


def verify_package_delivery(python: Path, candidate: Path, directory: Path, config: dict) -> dict:
    directory.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    output = directory / 'build'
    command = [str(python), '-I', '-B', '-X', 'utf8', '-m', 'build', '--no-isolation', '--wheel', '--outdir', str(output), str(candidate)]
    record = {'build': {'command': command, 'passed': False, 'exit_code': None},
              'cli': {'passed': 0, 'total': len(config['checks']), 'successful': False, 'checks': [], 'tested_artifact': 'built wheel'}}
    try:
        build = _run_bounded(command, cwd=directory, timeout_s=90)
        for stream in ('stdout', 'stderr'):
            (directory / ('build.' + stream + '.txt')).write_text(build[stream], encoding='utf-8')
        record['build'].update({key: build[key] for key in ('exit_code', 'timeout', 'output_limit_exceeded', 'duration_s')})
        wheels = sorted(output.glob('*.whl'))
        if build['exit_code'] != 0 or build['timeout'] or build['output_limit_exceeded'] or len(wheels) != 1:
            raise ValueError('Wheel build failed or did not produce exactly one wheel')
        wheel, unpacked = wheels[0], directory / 'installed-wheel'
        names = _extract_wheel(wheel, unpacked, config)
        record['build'].update(passed=True, wheel=str(wheel), sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(), members=names)
        for index, check in enumerate(config['checks']):
            case = directory / f'cli-{index:03d}'
            case.mkdir()
            for name, source in check.get('files', {}).items():
                target = _inside(case, case / _relative(name))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding='utf-8', newline='')
            binding = case / 'source-binding.json'
            worker = Path(__file__).with_name('package_cli_worker.py').resolve()
            command = [str(python), '-I', '-B', '-X', 'utf8', str(worker), '--wheel-root', str(unpacked), '--package', config['package'],
                       '--module', config['cli_module'], '--binding-json', str(binding), '--', *check['args']]
            process = _run_bounded(command, cwd=case, timeout_s=10, stdin=check.get('stdin'),
                                   environment={'COLUMNS': '200', 'LINES': '60'})
            for stream in ('stdout', 'stderr'):
                (case / (stream + '.txt')).write_text(process[stream], encoding='utf-8')
            evidence = json.loads(binding.read_text(encoding='utf-8')) if binding.is_file() else {}
            paths = evidence.get('modules', {})
            bound = (evidence.get('schema') == 'testfix.package-cli-binding.v1' and evidence.get('success') is True
                     and Path(evidence.get('wheel_root', '')).resolve() == unpacked.resolve()
                     and isinstance(paths, dict) and config['package'] in paths
                     and Path(evidence.get('executed_module', '')).resolve().is_relative_to(unpacked.resolve())
                     and all(Path(path).resolve().is_relative_to(unpacked.resolve()) for path in paths.values()))
            passed = (bound and not process['timeout'] and not process['output_limit_exceeded']
                      and process['exit_code'] == check['expected_exit']
                      and all(text in process['stdout'] for text in check['stdout_contains'])
                      and all(text not in process['stdout'] for text in check['stdout_not_contains']))
            export_record = None
            if 'export' in check:
                export = check['export']
                path = _inside(case, case / _relative(export['path']))
                bounded = path.is_file() and path.stat().st_size <= OUTPUT_LIMIT
                text = path.read_text(encoding='utf-8') if bounded else ''
                export_passed = bounded and all(item in text for item in export['contains']) and all(item not in text for item in export['not_contains'])
                passed = passed and export_passed
                export_record = {'path': str(path), 'passed': export_passed}
            record['cli']['checks'].append({'name': check['name'], 'passed': bool(passed), 'command': command,
                                            'exit_code': process['exit_code'], 'timeout': process['timeout'],
                                            'output_limit_exceeded': process['output_limit_exceeded'], 'binding': evidence,
                                            'cwd': str(case), 'stdout_path': str(case / 'stdout.txt'), 'stderr_path': str(case / 'stderr.txt'),
                                            'duration_s': process['duration_s'], 'export': export_record})
        record['cli']['passed'] = sum(check['passed'] for check in record['cli']['checks'])
        record['cli']['successful'] = (len(record['cli']['checks']) == len(config['checks'])
                                       and record['cli']['passed'] == len(config['checks']))
    except Exception as error:
        record['error'] = f'{type(error).__name__}: {error}'
        record['execution_error'] = True
    record['duration_s'] = round(time.monotonic() - started, 3)
    (directory / 'result.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return record


def run_package_operation(request: dict, guard) -> dict:
    from .project_tools import files_under
    root = Path(request['root']).resolve()
    run_id = request['run_id']
    if not isinstance(run_id, str) or not re.fullmatch(r'[0-9a-f]{32}', run_id):
        raise ValueError('Invalid package run ID')
    run = root / 'artifacts' / run_id
    work = run / 'workspace'
    paths = {name: _inside(root, Path(request[name])) for name in ('baseline', 'candidate', 'generated', 'acceptance', 'python', 'output')}
    for name, expected in {'baseline': work / 'baseline', 'candidate': work / 'candidate', 'generated': work / 'generated_tests',
                           'acceptance': work / 'private/acceptance_tests'}.items():
        if paths[name] != expected.resolve() or not paths[name].is_dir():
            raise ValueError('Package operation requires its exact sealed run workspace')
    if not paths['python'].is_file() or not paths['output'].is_relative_to(run / 'tool_calls'):
        raise ValueError('Invalid package interpreter/output path')
    config = _config(request['package_config'])
    regression = _inside(root, Path(config['regression']))
    if regression != (work / 'private/regression_tests').resolve() or not regression.is_dir():
        raise ValueError('Behavior regression must use the sealed private run copy')
    directory = paths['output'].parent
    directory.mkdir(parents=True, exist_ok=True)
    candidate_hash = _mapping_sha256(files_under(paths['candidate']))
    operation = request['operation']
    result = {'operation': operation, 'candidate_sha256': candidate_hash,
              'limits': {'backend': guard.backend, 'memory_bytes': guard.memory_bytes, 'cpu_seconds': guard.cpu_seconds}}
    if operation in {'baseline_public', 'candidate_public'}:
        project = paths['baseline'] if operation == 'baseline_public' else paths['candidate']
        upstream = _inside(project, project / 'tests')
        result['upstream_regression'] = run_package_suite(paths['python'], project, upstream, directory, operation + '-upstream', config)
        result['behavior_regression'] = run_package_suite(paths['python'], project, regression, directory, operation + '-behavior', config)
        result['regression'] = _merge_regression(result['upstream_regression'], result['behavior_regression'], directory, operation)
        result['generated'] = run_package_suite(paths['python'], project, paths['generated'], directory, operation + '-generated', config)
    elif operation == 'verify':
        result['baseline'] = run_package_suite(paths['python'], paths['baseline'], paths['acceptance'], directory, 'independent-baseline', config)
        result['acceptance'] = run_package_suite(paths['python'], paths['candidate'], paths['acceptance'], directory, 'independent-candidate', config)
        result['delivery'] = verify_package_delivery(paths['python'], paths['candidate'], directory / 'delivery', config)
    else:
        raise ValueError('Unknown fixed package operation')
    paths['output'].write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    return result
