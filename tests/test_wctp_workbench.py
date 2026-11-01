import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace
import uuid
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from testfix.api import create_app
from testfix.config import Settings
from testfix.project_tools import ProjectWorkspace
from testfix import wctp_workbench as module
from testfix.wctp_case import execute_case
from test_application_execution import imported


@pytest.fixture
def accepted(tmp_path, monkeypatch):
    settings, _, _, project = imported(tmp_path)
    workspace = ProjectWorkspace(settings, uuid.uuid4().hex, project)
    workspace.write_tests({'files': [{'path': 'test_frozen.py', 'source': 'def test_data():\n    assert 1 == 1\n'}]})
    identifier = workspace.directory.name
    artifact = workspace.directory / 'capabilities/generate-tests/attempt-1.json'
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps({'content': {'frozen_hashes': workspace.generated_seal}}), encoding='utf-8')
    run = {'id': identifier, 'kind': 'autonomous_project', 'status': 'completed', 'project_id': project['id'],
        'summary': {'accepted': 1}, 'tasks': [{'candidate_sha256': workspace.candidate_hash()}],
        'workflow': {'capabilities': [{'skill_id': 'generate-tests', 'artifact': {
            'path': str(artifact.relative_to(tmp_path)), 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()}}]}}
    monkeypatch.setattr(module, 'get_project', lambda *args: project)
    return settings, workspace, run


@pytest.fixture
def client(accepted, monkeypatch):
    settings, workspace, run = accepted
    (settings.root / 'web').mkdir(exist_ok=True)
    app = create_app(settings)
    app.state.store.runs[run['id']] = run
    submitted = []
    monkeypatch.setattr(app.state.store.pool, 'submit', lambda *args: submitted.append(args))
    with TestClient(app) as browser:
        yield browser, app, settings, workspace, run, submitted


def test_accepted_snapshot_clone_is_exact_and_does_not_modify_original(accepted):
    settings, original, run = accepted
    store = SimpleNamespace(snapshot=lambda _: run)
    clone = module.clone_accepted(settings, store, run['id'], uuid.uuid4().hex)
    assert clone.candidate_hash() == original.candidate_hash()
    assert clone.generated_seal == original.generated_seal
    assert clone.hidden_seal == original.hidden_seal
    original.assert_integrity()


@pytest.mark.parametrize('kind', ['candidate', 'generated', 'artifact', 'acceptance'])
def test_tampered_snapshot_cannot_be_reused(accepted, kind):
    settings, workspace, run = accepted
    store = SimpleNamespace(snapshot=lambda _: run)
    path = {'candidate': workspace.candidate / 'taskboard/storage.py', 'generated': workspace.generated / 'test_frozen.py',
            'artifact': workspace.directory / 'capabilities/generate-tests/attempt-1.json',
            'acceptance': workspace.acceptance / 'test_acceptance.py'}[kind]
    path.write_text('changed by a fixture', encoding='utf-8')
    with pytest.raises((ValueError, module.HTTPException)):
        module.clone_accepted(settings, store, run['id'], uuid.uuid4().hex)


def test_legacy_history_does_not_invent_test_execution(client):
    browser, app, settings, workspace, run, submitted = client
    response = browser.get('/api/project-runs/'+run['id']+'/wctp-tests')
    assert response.status_code == 200 and response.json()['cases'] == []
    assert submitted == []
    assert browser.get('/api/project-runs/../../missing/wctp-tests').status_code == 404


def test_case_graph_events_and_yaml_are_bound_to_selected_run(client):
    browser, app, settings, workspace, run, _ = client
    execute_case(workspace.directory / 'tool_calls/action/wctp', {'step_Test': ('测试', lambda: {'successful':True}, lambda r:r['successful'], '通过')}, title='原始 WCTP')
    base = '/api/project-runs/'+run['id']+'/wctp-tests'
    cases = browser.get(base).json()['cases']
    assert len(cases) == 1 and cases[0]['status'] == 'passed'
    value = browser.get(base+'/case', params={'key': cases[0]['id']}).json()
    assert value['native_step_execution'] is True and value['results']
    assert 'step_Loop -->|True|' in browser.get(base+'/yaml', params={'key': cases[0]['id']}).text
    assert browser.get(base+'/case', params={'key': '../anything'}).status_code == 404


def test_repeat_queues_once_and_tracks_desktop_lifecycle(client):
    browser, app, _, _, run, submitted = client
    url='/api/project-runs/'+run['id']+'/wctp-tests'
    response=browser.post(url,json={'iterations':3})
    assert response.status_code == 202, response.text
    value=app.state.store.runs[response.json()['id']]
    assert value['kind']=='wctp_test' and value['id'] in app.state.store._owned_run_ids
    assert value['source_run_id']==run['id'] and value['iterations']==3 and len(submitted)==1
    assert browser.post(url,json={'iterations':3}).status_code==409
    assert run['status']=='completed' and run['summary']['accepted']==1


@pytest.mark.parametrize('iterations',[0,101,True,1.2,'3'])
def test_repeat_rejects_invalid_total(client, iterations):
    browser, _, _, _, run, submitted=client
    assert browser.post('/api/project-runs/'+run['id']+'/wctp-tests',json={'iterations':iterations}).status_code==422
    assert not submitted


def test_unaccepted_project_cannot_start_repeat(client):
    browser, _, _, _, run, submitted=client
    run['status']='failed'
    assert browser.post('/api/project-runs/'+run['id']+'/wctp-tests',json={'iterations':1}).status_code==409
    assert not submitted


def suite(ids, *, passed=True):
    return {'successful':passed,'total':len(ids),'passed':len(ids) if passed else 0,'case_ids':ids,
            'duplicate_case_ids':[],'cases':[{'id':i,'status':'passed' if passed else 'failure','assertion_failure':not passed} for i in ids],
            'timeout':False,'parse_error':'','collection_errors':0}


def test_repeat_cannot_accept_fewer_or_different_generated_cases():
    accepted={'regression':suite(['original']), 'generated':suite(['data_1','data_2'])}
    assert module.public_repetition_passes(deepcopy(accepted),accepted)
    for ids in [['data_1'], ['data_1','replacement'], []]:
        actual=deepcopy(accepted);actual['generated']=suite(ids)
        assert not module.public_repetition_passes(actual,accepted)
    actual=deepcopy(accepted);actual['regression']=suite(['other_original'])
    assert not module.public_repetition_passes(actual,accepted)


def test_repeat_requires_matching_independent_ids_and_source_acceptance():
    accepted={'acceptance':suite(['fixed_1','fixed_2'])}
    value={'baseline':suite(['fixed_1','fixed_2'],passed=False),'acceptance':suite(['fixed_1','fixed_2']),
        'delivery':{'build':{'passed':True},'http':{'successful':True,'total':1,'passed':1,'checks':[{'name':'fixed','passed':True}]}}}
    check=lambda actual:module.independent_repetition_passes(actual,accepted,adapter='python_application',required_checks=['fixed'])
    assert check(value)
    fewer=deepcopy(value);fewer['acceptance']=suite(['fixed_1']);assert not check(fewer)
    both_fewer=deepcopy(fewer);both_fewer['baseline']=suite(['fixed_1'],passed=False);assert not check(both_fewer)
    changed_baseline=deepcopy(value);changed_baseline['baseline']=suite(['other_1','other_2'],passed=False);assert not check(changed_baseline)


@pytest.mark.parametrize('status',['interrupted','failed'])
def test_unfinished_case_projects_owner_terminal_state_without_rewriting_receipts(client,status):
    browser,app,settings,workspace,source,_=client
    owner={'id':uuid.uuid4().hex,'kind':'wctp_test','status':status,'created_at':'2026-10-27T00:00:00Z',
           'iterations':3,'source_run_id':source['id'],'error':'worker stopped'}
    app.state.store.runs[owner['id']]=owner
    path=settings.artifacts/owner['id']/'wctp-repeat/report.json';path.parent.mkdir(parents=True)
    original={'title':'unfinished fixture','status':'running','created_at':owner['created_at'],'iterations':3,
              'completed_iterations':1,'events':[{'event':'finished','step_id':'step_Test'}],'results':[]}
    path.write_text(json.dumps(original),encoding='utf-8')
    base='/api/project-runs/'+source['id']+'/wctp-tests'
    listing=browser.get(base).json();assert listing['cases'][0]['status']==status
    assert listing['active_repeat'] is None and listing['latest_repeat']['status']==status
    view=browser.get(base+'/case',params={'key':owner['id']+':repeat'}).json()
    assert view['status']==status and view['recorded_status']=='running' and view['events']==original['events']
    assert json.loads(path.read_text(encoding='utf-8'))==original


def test_existing_active_repeat_can_be_recovered_on_reopen(client):
    browser,app,_,_,source,_=client
    owner={'id':uuid.uuid4().hex,'kind':'wctp_test','status':'running','created_at':'2026-10-27T00:00:00Z',
           'iterations':3,'source_run_id':source['id']}
    app.state.store.runs[owner['id']]=owner;app.state.store._owned_run_ids.add(owner['id'])
    listing=browser.get('/api/project-runs/'+source['id']+'/wctp-tests').json()
    assert listing['active_repeat']['id']==owner['id'] and listing['latest_repeat']['id']==owner['id']
