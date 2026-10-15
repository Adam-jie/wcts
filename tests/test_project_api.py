"""Project entry, ownership recovery and accepted-deliverable integrity."""
import hashlib
import json
import os
from pathlib import Path
import threading

from fastapi.testclient import TestClient
import pytest

from testfix import api
from testfix.config import Settings
from testfix.reports import now


@pytest.fixture
def app(tmp_path):
    (tmp_path/'web').mkdir()
    (tmp_path/'web/index.html').write_text('<h1>fixture control plane</h1>',encoding='utf-8')
    return api.create_app(Settings(root=tmp_path))


def project_fixture():
    return {'id':'Project/flaskr','title':'Fixture project','source':{'repository':'https://github.com/pallets/flask'},
            'adapter':'flaskr','acceptance':'PRIVATE_PATH','python':'PRIVATE_EXECUTABLE'}


def test_project_catalog_exposes_only_public_metadata(app,monkeypatch):
    import testfix.project_tools as tools
    import testfix.project_models as models
    monkeypatch.setattr(tools,'project_catalog',lambda settings:[project_fixture()])
    monkeypatch.setattr(models,'project_provider_status',lambda settings:[{'id':'codex_cli','label':'fixture','configured':True}])
    response=TestClient(app).get('/api/projects')
    assert response.status_code==200
    assert 'PRIVATE' not in response.text
    assert response.json()['defaults']['provider']=='codex_cli'
    from testfix.project_workflow import DEFAULT_BUDGET
    assert response.json()['defaults']['budget']==DEFAULT_BUDGET


def test_project_submission_runs_the_real_controller_entry_and_persists_events(app,monkeypatch):
    import testfix.project_models as models
    import testfix.project_tools as tools
    import testfix.project_workflow as workflow
    finished=threading.Event()
    calls=[]
    monkeypatch.setattr(models,'project_provider_status',lambda settings:[{'id':'codex_cli','configured':True}])
    monkeypatch.setattr(tools,'get_project',lambda settings,identifier:project_fixture())
    def boundary(run,settings,persist):
        calls.append(run.copy())
        run.update(status='rejected',events=[{'agent':'fixture-controller','message':'unit-only'}])
        persist(run)
        finished.set()
    monkeypatch.setattr(workflow,'execute_project_run',boundary)
    client=TestClient(app)
    response=client.post('/api/project-runs',json={'provider':'codex_cli'})
    assert response.status_code==202
    assert finished.wait(3)
    result=client.get('/api/runs/'+response.json()['id']).json()
    assert result['kind']=='autonomous_project' and result['status']=='rejected'
    assert result['events'][0]['agent']=='fixture-controller'
    assert calls[0]['execution_owner_pid']==os.getpid()


@pytest.mark.parametrize('adapter,supplied_wall,expected_wall',[
    ('python_package',None,5400),('python_package',4500,4500),('flaskr',None,4500),
])
@pytest.mark.parametrize('include_null_request',[False,True])
def test_legacy_submission_uses_adapter_budget_only_when_wall_unspecified(app,monkeypatch,adapter,supplied_wall,expected_wall,include_null_request):
    import testfix.project_models as models
    import testfix.project_tools as tools
    import testfix.project_workflow as workflow
    finished=threading.Event()
    calls=[]
    monkeypatch.setattr(models,'project_provider_status',lambda settings:[{'id':'codex_cli','configured':True}])
    monkeypatch.setattr(tools,'get_project',lambda settings,identifier:{**project_fixture(),'adapter':adapter})
    def boundary(run,settings,persist):
        calls.append(run.copy())
        run.update(status='rejected')
        persist(run)
        finished.set()
    monkeypatch.setattr(workflow,'execute_project_run',boundary)
    body={'project_id':'Project/package','provider':'codex_cli','budget':{'token_limit':600000}}
    if include_null_request:
        body['collaboration_request']=None
    if supplied_wall is not None:
        body['budget']['wall_seconds']=supplied_wall
    response=TestClient(app).post('/api/project-runs',json=body)
    assert response.status_code==202 and finished.wait(3)
    assert calls[0]['budget']['wall_seconds']==expected_wall
    assert calls[0]['budget']['token_limit']==600000
    assert calls[0]['collaboration_request'] is None


@pytest.mark.parametrize('collaboration_request,supplied_wall,expected_request,expected_wall',[
    ('  修复 CSV 中方括号显示；分配代码与测试 Agent 协作。\n',None,'修复 CSV 中方括号显示；分配代码与测试 Agent 协作。',7200),
    ('Keep explicit budget',4500,'Keep explicit budget',4500),
    ('修'*20_000,None,'修'*20_000,7200),
],ids=['trimmed-request','explicit-wall-budget','maximum-length-request'])
def test_collaboration_request_reaches_controller_with_normalized_text_and_budget(app,monkeypatch,collaboration_request,supplied_wall,expected_request,expected_wall):
    import testfix.project_models as models
    import testfix.project_tools as tools
    import testfix.project_workflow as workflow
    finished=threading.Event()
    calls=[]
    monkeypatch.setattr(models,'project_provider_status',lambda settings:[{'id':'codex_cli','configured':True}])
    monkeypatch.setattr(tools,'get_project',lambda settings,identifier:project_fixture())
    def boundary(run,settings,persist):
        calls.append(run.copy())
        run.update(status='rejected')
        persist(run)
        finished.set()
    monkeypatch.setattr(workflow,'execute_project_run',boundary)
    body={'provider':'codex_cli','collaboration_request':collaboration_request,'budget':{'token_limit':600000}}
    if supplied_wall is not None:
        body['budget']['wall_seconds']=supplied_wall
    client=TestClient(app)
    response=client.post('/api/project-runs',json=body)
    assert response.status_code==202 and finished.wait(3)
    assert calls[0]['collaboration_request']==expected_request
    assert calls[0]['budget']['wall_seconds']==expected_wall
    assert calls[0]['budget']['token_limit']==600000
    assert client.get('/api/runs/'+response.json()['id']).json()['collaboration_request']==expected_request


@pytest.mark.parametrize('collaboration_request',['',' \t\r\n','修'*20_001],ids=['empty','blank','too-long'])
def test_empty_or_oversized_collaboration_request_is_not_started(app,collaboration_request):
    response=TestClient(app).post('/api/project-runs',json={'collaboration_request':collaboration_request})
    assert response.status_code==422
    assert not app.state.store.runs


@pytest.mark.parametrize('body',[
    {'provider':'replay'}, {'provider':'unknown'}, {'provider':'codex_cli','model':'another-model'},
    {'provider':'codex_cli','budget':{'wall_seconds':1}}, {'provider':'codex_cli','strategy':'skip-tests'},
])
def test_unauthorized_transport_or_invalid_execution_request_is_not_started(app,monkeypatch,body):
    import testfix.project_models as models
    import testfix.project_tools as tools
    monkeypatch.setattr(models,'project_provider_status',lambda settings:[{'id':'codex_cli','configured':True}])
    monkeypatch.setattr(tools,'get_project',lambda settings,identifier:project_fixture())
    assert TestClient(app).post('/api/project-runs',json=body).status_code in {400,422}
    assert not app.state.store.runs


def test_active_existing_run_blocks_new_project(app,monkeypatch):
    import testfix.project_models as models
    import testfix.project_tools as tools
    monkeypatch.setattr(models,'project_provider_status',lambda settings:[{'id':'codex_cli','configured':True}])
    monkeypatch.setattr(tools,'get_project',lambda settings,identifier:project_fixture())
    app.state.store.runs['unit']={'status':'running'}
    assert TestClient(app).post('/api/project-runs',json={}).status_code==409


def test_cross_origin_project_execution_is_blocked(app):
    response=TestClient(app).post('/api/project-runs',json={},headers={'origin':'https://unrelated.example'})
    assert response.status_code==403


def test_download_matches_accepted_hash_and_rejects_post_acceptance_changes(app):
    identifier='a'*32
    directory=app.state.store.settings.artifacts/identifier
    directory.mkdir(parents=True)
    archive=directory/'completed-project.zip'
    archive.write_bytes(b'accepted-fixture-archive')
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    app.state.store.runs[identifier]={'id':identifier,'kind':'autonomous_project','status':'completed','download':{'sha256':digest}}
    client=TestClient(app)
    assert client.get('/api/project-runs/'+identifier+'/download').content==b'accepted-fixture-archive'
    archive.write_bytes(b'changed-after-acceptance')
    assert client.get('/api/project-runs/'+identifier+'/download').status_code==409
    app.state.store.runs[identifier]['status']='rejected'
    assert client.get('/api/project-runs/'+identifier+'/download').status_code==409


def test_loading_another_control_plane_does_not_interrupt_a_live_owner(app):
    settings=app.state.store.settings
    identifier='b'*32
    directory=settings.artifacts/identifier
    directory.mkdir(parents=True)
    report={'id':identifier,'status':'running','execution_owner_pid':os.getpid()}
    (directory/'report.json').write_text(json.dumps(report),encoding='utf-8')
    other=api.RunStore(settings)
    assert other.snapshot(identifier)['status']=='running'
    assert json.loads((directory/'report.json').read_text(encoding='utf-8'))==report
