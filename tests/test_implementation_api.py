"""The plain project workflow binds user-selected tasks and never runs on page load."""
import io
import json
from pathlib import Path
import shutil
from urllib.request import urlopen
import zipfile
import pytest
from fastapi.testclient import TestClient

from testfix.api import create_app
from testfix.config import Settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def application(tmp_path):
    (tmp_path / 'web/implementation').mkdir(parents=True)
    (tmp_path / 'web/implementation/index.html').write_text('<h1>补全与修改</h1>', encoding='utf-8')
    base = tmp_path / 'experiments/flaskr'; base.mkdir(parents=True)
    (base / 'source.json').write_text('{}')
    (base / 'requirements.json').write_text(json.dumps({'requirements': [], 'clarifications': [], 'allowed_changes': []}))
    app = create_app(Settings(root=tmp_path))
    with TestClient(app) as client: yield app, client, tmp_path


def upload(client):
    task = client.post('/api/intakes', json={'mode': 'upload', 'title': '任务管理系统', 'description': '修复搜索和分页，完成状态应保存'}).json()
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        for file in (ROOT / 'examples/taskboard-upload-demo').rglob('*'):
            if file.is_file() and '__pycache__' not in file.parts:
                archive.write(file, 'repo/' + file.relative_to(ROOT / 'examples/taskboard-upload-demo').as_posix())
    assert client.put('/api/intakes/'+task['id']+'/files?filename=repo.zip', content=data.getvalue()).status_code == 201
    client.post('/api/intakes/'+task['id']+'/prepare')
    return client.get('/api/intakes/'+task['id']).json()


def test_simple_default_page_does_not_submit_model_work(application):
    app, client, _ = application
    response = client.get('/engineering', follow_redirects=False)
    assert response.status_code == 307 and response.headers['location'] == '/implementation'
    assert client.get('/implementation').status_code == 200
    assert app.state.store.runs == {}


def test_selected_upload_registers_as_an_immutable_project(application):
    app, client, _ = application
    task = upload(client)
    response = client.post('/api/intakes/'+task['id']+'/project', json={'revision': task['revision']})
    assert response.status_code == 201, response.text
    project = response.json()
    assert project['intake_id'] == task['id']
    assert [r['id'] for r in project['requirements']] == ['R1', 'R2', 'R3', 'R4']
    assert client.get('/api/implementation-projects/'+project['id']).json()['id'] == project['id']
    assert project['id'] in [p['id'] for p in client.get('/api/projects').json()['projects']]
    assert app.state.store.runs == {}


def test_start_requires_current_task_revision_and_passes_exact_request(application, monkeypatch):
    from testfix import project_models
    app, client, _ = application
    monkeypatch.setattr(project_models, 'project_provider_status', lambda _: [{'id': 'codex_cli', 'configured': True}])
    task = upload(client)
    project = client.post('/api/intakes/'+task['id']+'/project', json={'revision': task['revision']}).json()
    submitted = []
    monkeypatch.setattr(app.state.store, 'submit_project', lambda body: submitted.append(body) or {'id': 'a'*32, 'status': 'queued'})
    body = {'project_id': project['id'], 'collaboration_request': '修复搜索、分页和状态保存'}
    assert client.post('/api/project-runs', json=body).status_code == 400
    body.update(intake_id=task['id'], intake_revision=task['revision'])
    response = client.post('/api/project-runs', json=body)
    assert response.status_code == 202 and len(submitted) == 1
    assert submitted[0].intake_id == task['id'] and submitted[0].collaboration_request == body['collaboration_request']
    assert submitted[0].budget.wall_seconds == 7200
    client.patch('/api/intakes/'+task['id'], json={'revision': task['revision'], 'title': task['title'], 'description': '更新了需求'})
    assert client.post('/api/project-runs', json=body).status_code == 400
    assert len(submitted) == 1


def test_preview_requires_an_accepted_current_candidate(application):
    app, client, root = application
    identifier = 'b' * 32
    app.state.store.runs[identifier] = {'id': identifier, 'status': 'running'}
    assert client.post(f'/api/project-runs/{identifier}/preview').status_code == 409
    task = upload(client)
    project = client.post('/api/intakes/'+task['id']+'/project', json={'revision': task['revision']}).json()
    candidate = root / 'artifacts' / identifier / 'workspace/candidate'; candidate.mkdir(parents=True)
    (candidate / 'different.py').write_text('print(1)')
    app.state.store.runs[identifier] = {'id': identifier, 'status': 'completed', 'summary': {'accepted': 1},
        'project_id': project['id'], 'tasks': [{'candidate_sha256': '0'*64}]}
    assert client.post(f'/api/project-runs/{identifier}/preview').status_code == 409
    assert app.state.application_previews.processes == {}


def test_preview_reuse_preserves_the_accepted_candidate_binding(application):
    from testfix.project_eval import _mapping_sha256
    from testfix.project_tools import files_under

    app, client, root = application
    task = upload(client)
    project = client.post('/api/intakes/'+task['id']+'/project', json={'revision': task['revision']}).json()
    identifier = 'c' * 32
    candidate = root / 'artifacts' / identifier / 'workspace/candidate'
    shutil.copytree(ROOT / 'examples/taskboard-upload-demo', candidate,
                    ignore=shutil.ignore_patterns('__pycache__'))
    candidate_hash = _mapping_sha256(files_under(candidate))
    app.state.store.runs[identifier] = {'id': identifier, 'status': 'completed', 'summary': {'accepted': 1},
        'project_id': project['id'], 'tasks': [{'candidate_sha256': candidate_hash}]}

    first = client.post(f'/api/project-runs/{identifier}/preview')
    assert first.status_code == 200, first.text
    process = app.state.application_previews.processes[identifier]['process']
    second = client.post(f'/api/project-runs/{identifier}/preview')
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert second.json()['candidate_sha256'] == candidate_hash
    assert app.state.application_previews.processes[identifier]['process'] is process
    with urlopen(second.json()['url'] + '/api/health') as response:
        assert json.load(response) == {'status': 'ok', 'product': 'TaskBoard'}
