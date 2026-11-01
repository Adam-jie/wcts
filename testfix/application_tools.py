"""Fixed TaskBoard application adapter: paired tests, source build and real HTTP."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import zipfile

from .package_tools import _inside, _stop_tree, run_package_suite
from .project_eval import _environment, _mapping_sha256

REQUIRED_TASKBOARD_CHECKS = ('application_start', 'frontend_assets', 'chinese_search', 'english_search',
    'empty_search', 'clear_search', 'literal_search', 'page_disjoint', 'filtered_pagination', 'last_page',
    'out_of_range_page', 'invalid_page', 'blank_title_rejected', 'completion_saved', 'completion_reset',
    'delete_task', 'sqlite_restart', *(f'cycle_{i:02}' for i in range(1, 21)))


def start_taskboard(python, project, database, directory, *, port=0):
    directory.mkdir(parents=True, exist_ok=True)
    if not port:
        with socket.socket() as bound:
            bound.bind(('127.0.0.1', 0)); port = bound.getsockname()[1]
    ready = directory / 'ready.json'; ready.unlink(missing_ok=True)
    command = [str(python), '-I', '-B', '-X', 'utf8', str(Path(__file__).with_name('application_http_worker.py')),
        '--project', str(project), '--database', str(database), '--port', str(port), '--ready', str(ready)]
    with (directory / 'server.log').open('ab') as log:
        process = subprocess.Popen(command, cwd=directory, env=_environment(), stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            start_new_session=os.name != 'nt')
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if process.poll() is not None: raise ValueError('Application exited before readiness')
            if ready.is_file():
                binding = json.loads(ready.read_text(encoding='utf-8'))
                if (binding['port'] != port or Path(binding['project']).resolve() != Path(project).resolve()
                        or not Path(binding['module']).resolve().is_relative_to(Path(project).resolve())):
                    raise ValueError('Application readiness is not bound to selected build')
                return process, f'http://127.0.0.1:{port}', command
            time.sleep(.05)
        raise TimeoutError('Application did not become ready')
    except BaseException:
        if process.poll() is None: _stop_tree(process)
        raise


def verify_application(python, candidate, directory):
    from .project_tools import files_under, confined
    directory.mkdir(parents=True, exist_ok=False)
    record = {'build': {'passed': False, 'kind': 'source_archive'},
              'http': {'passed': 0, 'total': len(REQUIRED_TASKBOARD_CHECKS), 'successful': False,
                       'checks': [], 'requests': [], 'tested_artifact': 'built source archive'}}
    process = None
    try:
        files = files_under(candidate)
        for name in files:
            if name.endswith('.py'): compile((candidate / name).read_text(encoding='utf-8'), name, 'exec')
        archive_path = directory / 'application.zip'
        built = directory / 'built-source'
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in files: archive.write(candidate / name, name)
        with zipfile.ZipFile(archive_path) as archive:
            for name in archive.namelist():
                target = confined(built, name); target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
        if files_under(built) != files: raise ValueError('Built source archive differs from candidate')
        record['build'].update(passed=True, sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                              archive=str(archive_path), source_binding=_mapping_sha256(files))
        database = directory / 'product.sqlite3'
        process, origin, command = start_taskboard(python, built, database, directory / 'server')
        record['http'].update(base_url=origin, command=command)
        def request(path, method='GET', data=None):
            req = Request(origin + path, method=method, data=json.dumps(data).encode('utf-8') if data is not None else None,
                          headers={'Content-Type': 'application/json'})
            try: response = urlopen(req, timeout=5)
            except HTTPError as error: response = error
            with response:
                raw = response.read(2_000_000); status = response.status
                result = json.loads(raw) if 'application/json' in response.headers.get('Content-Type', '') else raw
            record['http']['requests'].append({'method': method, 'path': path, 'status': status})
            return status, result
        def check(name, passed):
            record['http']['checks'].append({'name': name, 'passed': bool(passed)})
        check('application_start', request('/api/health') == (200, {'status': 'ok', 'product': 'TaskBoard'}))
        check('frontend_assets', all(request(path)[0] == 200 for path in ('/', '/app.js', '/style.css')))
        ids = []
        for title in ['验收中文关键词', 'English Needle', *(f'验收多页 {i:02}' for i in range(13)), 'literal_%_marker']:
            code, item = request('/api/tasks', 'POST', {'title': title})
            if code != 201: raise ValueError('Fixture task creation failed')
            ids.append(item['id'])
        listing = lambda q='', page=1, size=5: request('/api/tasks?' + urlencode({'q': q, 'page': page, 'page_size': size}))[1]
        check('chinese_search', listing('验收中文关键词')['total'] == 1)
        check('english_search', listing('Needle')['total'] == 1)
        check('empty_search', listing('unmatched-193749')['total'] == 0 and listing('unmatched-193749')['items'] == [])
        check('clear_search', listing()['total'] == 26)
        check('literal_search', listing('literal_%_marker')['total'] == 1 and listing('不存在_%_marker')['total'] == 0)
        first, second = listing(page=1), listing(page=2)
        check('page_disjoint', len(first['items']) == len(second['items']) == 5 and not ({x['id'] for x in first['items']} & {x['id'] for x in second['items']}))
        filtered = [listing('验收多页', p) for p in (1, 2, 3)]
        check('filtered_pagination', all(r['total'] == 13 for r in filtered) and len({x['id'] for r in filtered for x in r['items']}) == 13)
        check('last_page', len(filtered[-1]['items']) == 3)
        check('out_of_range_page', listing('验收多页', 99)['items'] == [])
        check('invalid_page', request('/api/tasks?page=0')[0] == 400)
        check('blank_title_rejected', request('/api/tasks', 'POST', {'title': '   '})[0] == 400)
        check('completion_saved', request(f'/api/tasks/{ids[0]}', 'PATCH', {'completed': True})[1]['completed'] is True
              and request(f'/api/tasks/{ids[0]}')[1]['completed'] is True)
        check('completion_reset', request(f'/api/tasks/{ids[0]}', 'PATCH', {'completed': False})[1]['completed'] is False
              and request(f'/api/tasks/{ids[0]}')[1]['completed'] is False)
        check('delete_task', request(f'/api/tasks/{ids[-1]}', 'DELETE')[0] == 200 and request(f'/api/tasks/{ids[-1]}')[0] == 404)
        request(f'/api/tasks/{ids[0]}', 'PATCH', {'completed': True})
        old_port = int(origin.rsplit(':', 1)[1]); _stop_tree(process); process = None
        process, origin, _ = start_taskboard(python, built, database, directory / 'server-restart', port=old_port)
        check('sqlite_restart', request(f'/api/tasks/{ids[0]}')[1]['completed'] is True and listing('Needle')['total'] == 1)
        for i in range(1, 21):
            expected = bool(i % 2)
            request(f'/api/tasks/{ids[0]}', 'PATCH', {'completed': expected})
            check(f'cycle_{i:02}', request(f'/api/tasks/{ids[0]}')[1]['completed'] is expected
                  and listing('验收中文关键词')['total'] == 1
                  and not ({x['id'] for x in listing('验收多页', 1)['items']} & {x['id'] for x in listing('验收多页', 2)['items']}))
        record['http']['passed'] = sum(item['passed'] for item in record['http']['checks'])
        record['http']['successful'] = record['http']['passed'] == len(REQUIRED_TASKBOARD_CHECKS) == len(record['http']['checks'])
        if files_under(built) != files: raise ValueError('Runtime changed built source files')
    except Exception as error:
        record.update(error=f'{type(error).__name__}: {error}', execution_error=True)
    finally:
        if process is not None and process.poll() is None: _stop_tree(process)
    (directory / 'result.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return record


def run_application_operation(request, guard):
    from .project_tools import files_under
    root = Path(request['root']).resolve(); run = root / 'artifacts' / request['run_id']; work = run / 'workspace'
    paths = {key: _inside(root, Path(request[key])) for key in ('baseline', 'candidate', 'generated', 'acceptance', 'output')}
    paths['python'] = Path(request['python']).resolve()
    if paths['python'] != Path(sys.executable).resolve():
        raise ValueError('Application tools must use the host registered interpreter')
    for key, relative in {'baseline': 'baseline', 'candidate': 'candidate', 'generated': 'generated_tests', 'acceptance': 'private/acceptance_tests'}.items():
        if paths[key] != (work / relative).resolve(): raise ValueError('Application tool requires exact run paths')
    if request['application_config'] != {'package': 'taskboard', 'source_root': '.'}:
        raise ValueError('No application adapter for this configuration')
    if not paths['python'].is_file() or not paths['output'].is_relative_to(run / 'tool_calls'):
        raise ValueError('Invalid application interpreter or output')
    directory = paths['output'].parent; directory.mkdir(parents=True, exist_ok=True)
    operation = request['operation']
    result = {'operation': operation, 'candidate_sha256': _mapping_sha256(files_under(paths['candidate'])),
              'limits': {'backend': guard.backend, 'memory_bytes': guard.memory_bytes, 'cpu_seconds': guard.cpu_seconds}}
    config = request['application_config']
    from .wctp_case import run_test_actions, successful, functional_failure, delivery_success
    if operation in {'baseline_public', 'candidate_public'}:
        project = paths['baseline'] if operation == 'baseline_public' else paths['candidate']
        values, result['wctp'] = run_test_actions(directory, operation, {
            'step_Regression': ('原有回归', lambda: run_package_suite(paths['python'], project, project / 'tests', directory, operation + '-original', config), successful, '原有回归全部通过'),
            'step_Generated': ('模型生成测试 · 原版缺口对照' if operation == 'baseline_public' else '模型生成测试 · 修改版',
                lambda: run_package_suite(paths['python'], project, paths['generated'], directory, operation + '-generated', config),
                functional_failure if operation == 'baseline_public' else successful,
                '暴露真实断言失败；收集错误、超时不算功能缺口' if operation == 'baseline_public' else '冻结生成测试全部通过'),
        })
        result['regression'], result['generated'] = values['step_Regression'], values['step_Generated']
    elif operation == 'verify':
        values, result['wctp'] = run_test_actions(directory, operation, {
            'step_Baseline': ('原版独立验收对照', lambda: run_package_suite(paths['python'], paths['baseline'], paths['acceptance'], directory, 'independent-baseline', config), functional_failure, '原版暴露功能缺口'),
            'step_Acceptance': ('修改版独立验收', lambda: run_package_suite(paths['python'], paths['candidate'], paths['acceptance'], directory, 'independent-candidate', config), successful, '独立验收全部通过'),
            'step_Delivery': ('构建、HTTP、多数据、重启与循环', lambda: verify_application(paths['python'], paths['candidate'], directory / 'delivery'), delivery_success, '构建成功且所有固定产品检查通过'),
        })
        result['baseline'], result['acceptance'], result['delivery'] = values['step_Baseline'], values['step_Acceptance'], values['step_Delivery']
    else: raise ValueError('Unknown application operation')
    paths['output'].write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
