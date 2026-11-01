"""Authorized fresh upload-to-repair experiment. Uses existing model setup."""
from pathlib import Path
import json
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from testfix.intake import IntakeStore

BASE = 'http://127.0.0.1:2890'
OUT = ROOT / 'artifacts/wctp-product-check'


def request(path, data=None, *, method=None, raw=None):
    body = raw if raw is not None else json.dumps(data).encode('utf-8') if data is not None else None
    req = Request(BASE + path, data=body, method=method,
                  headers={'Content-Type': 'application/octet-stream' if raw is not None else 'application/json'})
    with urlopen(req, timeout=30) as response:
        return json.load(response)


def main():
    original = IntakeStore(ROOT)
    source = original.read('44828bb8775b4d9393e596d963036ecd')
    item = next(f for f in source['files'] if f['name'].lower().endswith('.zip'))
    archive = (original.directory(source['id']) / 'files' / item['id']).read_bytes()
    task = request('/api/intakes', {'mode': 'upload', 'title': '任务管理系统 · WCTP 全流程复验',
        'description': source['description'], 'acceptance': source.get('acceptance', '')})
    request('/api/intakes/' + task['id'] + '/files?filename=taskboard-original.zip', method='PUT', raw=archive)
    request('/api/intakes/' + task['id'] + '/prepare', {})
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        task = request('/api/intakes/' + task['id'])
        if task['status'] == 'prepared': break
        if task['status'] == 'failed': raise RuntimeError(task.get('error'))
        time.sleep(.5)
    else: raise TimeoutError('Upload preparation did not finish')
    project = request('/api/intakes/' + task['id'] + '/project', {'revision': task.get('revision', 0)})
    result = request('/api/project-runs', {'project_id': project['id'], 'intake_id': task['id'],
        'intake_revision': task.get('revision', 0), 'provider': 'codex_cli', 'model': 'gpt-5.6-sol',
        'max_rounds': 2, 'strategy': 'balanced', 'collaboration_request': task['description'] + '\n验收标准：\n' + task.get('acceptance', '')})
    pointer = {'run_id': result['id'], 'intake_id': task['id'], 'project_id': project['id'],
        'source_archive_sha256': item['sha256'], 'scope': 'fresh upload, fresh model pipeline; no public checkpoint reuse'}
    (OUT / 'fresh-run.json').write_text(json.dumps(pointer, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(pointer, ensure_ascii=False), flush=True)
    seen = 0
    while True:
        run = request('/api/runs/' + result['id'])
        for event in run.get('events', [])[seen:]: print(event.get('agent'), event.get('stage'), event.get('message'), flush=True)
        seen = len(run.get('events', []))
        if run['status'] not in {'queued', 'running'}:
            print(json.dumps({'id':run['id'], 'status':run['status'], 'error':run.get('error'), 'summary':run['summary']}, ensure_ascii=False), flush=True)
            return 0 if run['status'] == 'completed' else 1
        time.sleep(5)


if __name__ == '__main__':
    raise SystemExit(main())
