"""Run three controlled software cases through the real local TestFix API."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.request import Request, urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from testfix.intake_projects import REQUIREMENTS
from testfix.project_tools import files_under

BASELINE = ROOT / 'examples/taskboard-upload-demo'
REFERENCE = ROOT / 'examples/taskboard-reference'
ACCEPTANCE = ROOT / 'testfix/application_contracts/taskboard'
ACCEPTANCE_START = ACCEPTANCE.joinpath('test_acceptance.py').read_text(encoding='utf-8').split('@pytest.mark.parametrize', 1)[0]


def prepare(case, directory):
    source = directory / case / 'source'
    source.mkdir(parents=True)
    acceptance = source.parent / 'acceptance'
    shutil.copytree(ACCEPTANCE, acceptance, ignore=shutil.ignore_patterns('__pycache__'))
    requirements = list(REQUIREMENTS)
    allowed = ['taskboard/storage.py']
    if case == 'repair':
        shutil.copytree(BASELINE, source, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
    elif case == 'completion':
        shutil.copytree(REFERENCE, source, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
        storage = source / 'taskboard/storage.py'
        original = storage.read_text(encoding='utf-8')
        begin = original.index('    def complete(')
        end = original.index('    def delete(', begin)
        storage.write_text(original[:begin] + '    def complete(self, identifier, completed):\n'
                           '        """TODO: persist the requested completion state."""\n'
                           '        return self.get(identifier)\n\n' + original[end:], encoding='utf-8')
        requirements = [REQUIREMENTS[2], {'id': 'R4', 'description': '保持搜索、分页和原有增删、输入校验、静态界面与 SQLite 数据。'}]
    else:
        for name in ('taskboard', 'tests', 'web'):
            (source / name).mkdir()
        (source / 'pyproject.toml').write_text('[project]\nname = "taskboard-greenfield"\nversion = "0.1.0"\ndependencies = []\n', encoding='utf-8')
        (source / 'taskboard/__init__.py').touch()
        shutil.copyfile(BASELINE / 'server.py', source / 'server.py')
        (source / 'web/index.html').write_text('<!doctype html><title>TaskBoard starter</title>\n', encoding='utf-8')
        (source / 'web/app.js').write_text('// Application behavior to implement.\n', encoding='utf-8')
        (source / 'web/style.css').write_text('/* Application styles to implement. */\n', encoding='utf-8')
        (source / 'taskboard/storage.py').write_text('class Storage:\n    """Implement SQLite storage here."""\n    def __init__(self, database):\n        self.database = database\n', encoding='utf-8')
        (source / 'taskboard/application.py').write_text(
            'def create_application(database):\n'
            '    def application(environ, start_response):\n'
            '        if environ.get("PATH_INFO") == "/api/health":\n'
            '            start_response("200 OK", [("Content-Type", "application/json")])\n'
            '            return [b\'{"status":"ok","product":"TaskBoard"}\']\n'
            '        start_response("501 Not Implemented", [("Content-Type", "application/json")])\n'
            '        return [b\'{"error":"not implemented"}\']\n'
            '    return application\n', encoding='utf-8')
        (source / 'tests/test_bootstrap.py').write_text(
            'from taskboard.application import create_application\n\n'
            'def test_launcher_health(tmp_path):\n'
            '    status = []\n'
            '    response = create_application(tmp_path / "db.sqlite")(\n'
            '        {"REQUEST_METHOD": "GET", "PATH_INFO": "/api/health"}, lambda code, headers: status.append(code))\n'
            '    assert status == ["200 OK"] and b"TaskBoard" in b"".join(response)\n', encoding='utf-8')
        allowed += ['taskboard/application.py', 'web/index.html', 'web/app.js', 'web/style.css']
        (acceptance / 'test_acceptance.py').write_text(ACCEPTANCE_START + '''
def test_task_lifecycle(product):
    request, database = product
    status, item = request('/api/tasks', 'POST', {'title': '独立验收_%'})
    assert status == 201
    route = f'/api/tasks/{item["id"]}'
    assert request('/api/tasks?' + urlencode({'q': '独立验收_%'}))[1]['total'] == 1
    assert request(route, 'PATCH', {'completed': True})[0] == 200
    assert Storage(database).get(item['id'])['completed'] is True
    assert request(route, 'DELETE')[0] == 200
    assert request(route)[0] == 404


def test_initial_list_and_page(product):
    request, _ = product
    status, first = request('/api/tasks?page=1&page_size=5')
    assert status == 200 and first['total'] == 10
    second = request('/api/tasks?page=2&page_size=5')[1]
    assert len(first['items']) == len(second['items']) == 5
    assert not ({x['id'] for x in first['items']} & {x['id'] for x in second['items']})


def test_deleted_seed_tasks_stay_deleted(product):
    request, database = product
    status, tasks = request('/api/tasks?page_size=20')
    assert status == 200 and len(tasks['items']) == 10
    for task in tasks['items']:
        assert request(f'/api/tasks/{task["id"]}', 'DELETE')[0] == 200
    response = create_application(database)({'REQUEST_METHOD': 'GET', 'PATH_INFO': '/api/tasks'},
                                            lambda status, headers: None)
    assert json.loads(b''.join(response))['total'] == 0
''', encoding='utf-8')
        requirements = [
            {'id': 'R1', 'description': '从空业务代码实现标准库 Python WSGI 与 SQLite 任务板。启动入口 taskboard.application.create_application(database)，Storage(database).get(id)；保留 GET /api/health。GET/POST /api/tasks，GET/PATCH/DELETE /api/tasks/{id}。新增返回201，读/修改/删除成功200，缺失404，空标题及无效页400。任务字段 id,title,completed；PATCH 使用布尔 completed，重启后状态仍保存。'},
            {'id': 'R2', 'description': '首次建表时只初始化10条任务，清空全部数据后重启仍为空。列表支持 q 字面值子串搜索（含中英文、% 和 _），page/page_size 稳定 id DESC 分页。返回 items,total,page,page_size，默认5条，total 为匹配总数，无结果和超页为空。'},
            {'id': 'R3', 'description': '实现实际可操作的中文网页并提供 /、/app.js、/style.css。浏览器合同：新增表单 #create-form 和 #new-title，搜索 #search-form 和 #search，列表 #tasks，各条 label 包含 checkbox 与标题 span，checkbox accessible name 为“完成：标题”，删除按钮 accessible name 为“删除：标题”，#total 显示“N 项”，#page-label 显示“第 N 页”，#next、#previous 翻页。搜索和变更须实际请求接口，加载、空结果 #tasks .empty 和错误 #error 可见。'}
        ]
    title = {'repair': '软件实测 · 缺陷修复', 'completion': '软件实测 · 补全缺失功能',
             'greenfield': '软件实测 · 纯功能实现'}[case]
    identifier = 'software-check-' + case + '-' + directory.name[-8:]
    entry = {'id': 'Project/' + identifier, 'title': title, 'adapter': 'python_application',
             'baseline': str(source), 'acceptance': str(acceptance), 'acceptance_files': files_under(acceptance),
             'python': sys.executable, 'requirements': requirements, 'clarifications': [],
             'allowed_changes': allowed, 'application_config': {'package': 'taskboard', 'source_root': '.'},
             'public_runtime': {'package': 'taskboard', 'factory': 'taskboard.application.create_application(database)',
                                'protocol': 'WSGI', 'dependencies': [], 'test_runner': 'pytest'},
             'source': {'kind': 'controlled_verification_fixture', 'files': files_under(source)}}
    registry = ROOT / 'data/projects/registered' / (identifier + '.json')
    with registry.open('x', encoding='utf-8') as handle:
        json.dump(entry, handle, ensure_ascii=False, indent=2)
    return entry


def run(base, directory, cases, fixtures):
    def request(path, body=None):
        data = None if body is None else json.dumps(body).encode('utf-8')
        with urlopen(Request(base + path, data, {'Content-Type': 'application/json'}), timeout=30) as response:
            return json.load(response)

    def finish(identifier):
        phase = None
        while True:
            result = request('/api/runs/' + identifier)
            event = result.get('events', [])[-1:] or [{}]
            current = (result['status'], result.get('phase'), event[0].get('message'))
            if current != phase:
                print(identifier[:8], *current, flush=True)
                phase = current
            if result['status'] not in {'queued', 'running'}:
                return result
            time.sleep(4)

    summary = []
    for case in cases:
        project = fixtures.get(case) or prepare(case, directory)
        row = {'case': case, 'project_id': project['id']}
        summary.append(row)
        output = directory / 'runs.json'
        def save():
            output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        result = request('/api/project-runs', {'project_id': project['id'], 'provider': 'codex_cli',
            'model': 'gpt-5.6-luna', 'strategy': 'balanced', 'max_rounds': 2})
        row['run_id'] = result['id']
        save()
        completed = finish(result['id'])
        row['status'] = completed['status']
        row['accepted'] = bool(completed.get('summary', {}).get('accepted'))
        row['error'] = completed.get('error')
        save()
        if not row['accepted']:
            continue
        if os.environ.get('TESTFIX_UI_VISIBLE') == '1':
            ui = directory / case / 'browser'
            env = {**os.environ, 'TESTFIX_UI_BASE': base, 'TESTFIX_UI_RUN_ID': result['id'],
                   'TESTFIX_UI_CASE': {'repair': '软件修复', 'completion': '软件补全',
                                       'greenfield': '纯功能实现'}[case],
                   'TESTFIX_UI_EVIDENCE': str(ui)}
            row['browser_exit_code'] = subprocess.run(
                ['node', str(ROOT / 'scripts/verify-taskboard-ui.cjs')], cwd=ROOT, env=env, check=False
            ).returncode
            row['browser_evidence'] = str(ui)
            save()
        scenario = request('/api/feature-tests', {'project_id': project['id'], 'source_run_id': result['id'],
            'provider': 'codex_cli', 'execution_mode': 'operations', 'iterations': 2,
            'description': '实际新增标题为软件自动化验收的任务，确认读取返回标题。标记完成，重启应用后重新读取，确认 completed 为 true。'})
        row['feature_id'] = scenario['id']
        save()
        checked = finish(scenario['id'])
        row['feature_status'] = checked['status']
        row['feature_summary'] = checked.get('summary')
        save()
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base')
    parser.add_argument('cases', nargs='*', choices=['repair', 'completion', 'greenfield'])
    parser.add_argument('--reuse-fixtures', type=Path)
    parser.add_argument('--prepare-only', action='store_true', help='Register the three software fixtures without model calls')
    args = parser.parse_args()
    folder = ROOT / 'artifacts' / ('software-check-1107-' + uuid.uuid4().hex[:8])
    folder.mkdir()
    (ROOT / 'data/projects/registered').mkdir(parents=True, exist_ok=True)
    if args.prepare_only:
        prepared = [prepare(case, folder) for case in args.cases or ['repair', 'completion', 'greenfield']]
        manifest = [{'case': case, 'project_id': project['id']}
                    for case, project in zip(args.cases or ['repair', 'completion', 'greenfield'], prepared)]
        (folder / 'runs.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        sys.exit(0)
    fixtures = {}
    if args.reuse_fixtures:
        previous = json.loads(args.reuse_fixtures.read_text(encoding='utf-8'))
        fixtures = {row['case']: {'id': row['project_id']} for row in previous}
    print(folder, flush=True)
    summary = run(args.base, folder, args.cases or ['repair', 'completion', 'greenfield'], fixtures)
    passed = all(row['accepted'] and row.get('feature_status') == 'completed'
                 and row.get('browser_exit_code', 0) == 0 for row in summary)
    print('整轮验证通过' if passed else '整轮验证未通过，详情见 runs.json', flush=True)
    sys.exit(0 if passed else 1)
