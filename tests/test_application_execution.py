import io
import json
from pathlib import Path
import shutil
import uuid
import zipfile

import pytest
from fastapi import HTTPException

from testfix.config import Settings
from testfix.intake import IntakeRequest, IntakeStore
from testfix.intake_projects import register_intake_project
from testfix.project_tools import ProjectWorkspace, run_project_tool
from testfix.project_workflow import delivery_passes
from testfix.skills import load_catalog

ROOT = Path(__file__).resolve().parents[1]


def imported(tmp_path):
    store = IntakeStore(tmp_path)
    task = store.create(IntakeRequest(mode='upload', title='任务管理系统', description='修复搜索、分页和状态保存'))
    name = uuid.uuid4().hex; raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w') as archive:
        source = ROOT / 'examples/taskboard-upload-demo'
        for path in source.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                archive.write(path, 'project/' + path.relative_to(source).as_posix())
    import hashlib
    data = raw.getvalue(); directory = store.directory(task['id']) / 'files'; directory.mkdir()
    (directory / name).write_bytes(data)
    task['files'] = [{'id': name, 'name': 'project.zip', 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}]
    store.save(task); store.prepare(task['id']); store.process(task['id'])
    task = store.read(task['id'])
    settings = Settings(root=tmp_path)
    project = register_intake_project(settings, store, task['id'], task['revision'])
    return settings, store, task, project


def test_import_preserves_uploaded_bytes_and_registers_exact_source(tmp_path):
    settings, store, task, project = imported(tmp_path)
    assert project['intake_id'] == task['id']
    assert project['adapter'] == 'python_application'
    assert len(project['requirements']) == 4
    assert register_intake_project(settings, store, task['id'], task['revision']) == project
    assert store.read(task['id']) == task
    with pytest.raises(HTTPException) as e: register_intake_project(settings, store, task['id'], task['revision'] + 1)
    assert e.value.status_code == 409
    workspace = ProjectWorkspace(settings, uuid.uuid4().hex, project)
    assert workspace.snapshot()['sources']['taskboard/storage.py'] == (ROOT / 'examples/taskboard-upload-demo/taskboard/storage.py').read_text(encoding='utf-8')
    assert 'test_acceptance.py' not in workspace.snapshot()['sources']


def test_python_application_profile_preserves_http_and_archive_gates():
    result = load_catalog().validate_plan('python_application', ['plan-workflow', 'explore-repository', 'ground-code',
        'analyze-requirements', 'generate-tests', 'diagnose-results', 'repair-code', 'review-patch', 'verify-delivery'])
    assert {'archive_build', 'real_http', 'sqlite_restart'} <= set(result['required_gates'])
    assert 'wheel_build' not in result['required_gates']


def test_full_application_tool_baseline_and_fixed_fixture(tmp_path_factory):
    settings, store, task, project = imported(tmp_path_factory.mktemp('app'))
    workspace = ProjectWorkspace(settings, uuid.uuid4().hex, project)
    workspace.write_tests({'files': [{'path': 'test_search.py', 'source': 'from taskboard.storage import Storage\ndef test_search(tmp_path):\n    store=Storage(tmp_path/"db")\n    assert store.list("不存在的关键词")["total"] == 0\n'}]})
    baseline = run_project_tool(settings, workspace, 'baseline_public', timeout_s=90)
    assert baseline['regression']['successful'] and baseline['regression']['total'] == 6
    assert baseline['generated']['runnable'] and baseline['generated']['passed'] == 0
    source = (workspace.candidate / 'taskboard/storage.py').read_text(encoding='utf-8')
    # A deterministic test fixture verifies the new adapter; it is not the real model experiment.
    old = '''            total = connection.execute('SELECT count(*) FROM tasks').fetchone()[0]
            offset = 0
            rows = connection.execute('SELECT * FROM tasks ORDER BY id DESC LIMIT ? OFFSET ?', (page_size, offset)).fetchall()'''
    new = '''            total = connection.execute('SELECT count(*) FROM tasks WHERE instr(title, ?) > 0', (query,)).fetchone()[0]
            offset = (page - 1) * page_size
            rows = connection.execute('SELECT * FROM tasks WHERE instr(title, ?) > 0 ORDER BY id DESC LIMIT ? OFFSET ?', (query, page_size, offset)).fetchall()'''
    source = source.replace(old, new).replace("        task['completed'] = completed\n        return task", "        with self.connect() as connection:\n            connection.execute('UPDATE tasks SET completed=? WHERE id=?', (int(completed), identifier))\n        return self.get(identifier)")
    workspace.apply_patch({'baseline_sha256': workspace.candidate_hash(), 'files': [{'path': 'taskboard/storage.py', 'source': source}]})
    candidate = run_project_tool(settings, workspace, 'candidate_public', timeout_s=90)
    assert candidate['regression']['successful'] and candidate['generated']['successful']
    verification = run_project_tool(settings, workspace, 'verify', timeout_s=150)
    assert verification['acceptance']['successful'], verification
    assert verification['baseline']['passed'] < verification['baseline']['total']
    from testfix.application_tools import REQUIRED_TASKBOARD_CHECKS
    assert delivery_passes(verification['delivery'], adapter='python_application', required_checks=REQUIRED_TASKBOARD_CHECKS), verification['delivery']
    assert verification['delivery']['http']['total'] == len(REQUIRED_TASKBOARD_CHECKS)
    assert store.read(task['id']) == task
