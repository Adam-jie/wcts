"""Real host/tool/prompt integration; model transport below is explicitly synthetic."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

from fastapi.testclient import TestClient
import pytest

from testfix.api import create_app
from testfix.config import Settings
from testfix.project_models import ProjectModelProvider
from testfix.specialists import route_task
from testfix.team_runtime import execute_team_round
from test_parallel_team_runtime import workspace, source_root, task, plan_for, SyntheticEmployee
from testfix.team_planning import solve_team_plan


SCHEMA = {'type': 'object', 'properties': {'summary': {'type': 'string'}},
          'required': ['summary'], 'additionalProperties': False}


def model(tmp_path):
    requests = []
    def transport(**kwargs):
        requests.append(deepcopy(kwargs['payload']))
        return {'ok': True, 'body': {'model': 'fixture-model', 'choices': [{'message': {
            'content': '{"summary":"synthetic response"}'}}]}}
    provider = ProjectModelProvider('openai', Settings(root=tmp_path, base_url='https://fixture.invalid',
                                                      api_key='fixture-credential'),
                                    model='fixture-model', artifact_dir=tmp_path/'calls', http_transport=transport)
    return provider, requests


def context():
    return {'assigned_task': task('impl', ['flaskr/auth.py']),
            'sources': {'flaskr/auth.py': 'from flask import Blueprint\ndb.execute("SELECT 1")'},
            'public_requirements': [{'id': 'SYNTHETIC'}]}


def test_only_selected_trusted_bodies_reach_actual_transport(tmp_path):
    provider, requests = model(tmp_path)
    provider.invoke('repair-code', context(), SCHEMA)
    prompt = requests[0]['messages'][0]['content']
    for identity in ('software', 'backend', 'database'):
        assert 'name: ' + identity + '\n' in prompt
    for identity in ('threejs', 'javascript', 'mcu', 'mpu', 'protocol'):
        assert 'name: ' + identity + '\n' not in prompt
    trace = provider.last_trace
    assert trace['specialist_assignment']['status'] == 'ready'
    assert len(trace['specialist_catalog']['files']) == 11
    assert 'fixture-credential' not in json.dumps(trace)


def test_forged_assignment_is_rejected_before_transport(tmp_path):
    provider, requests = model(tmp_path)
    public = context()
    public['specialist_assignment'] = route_task(public['assigned_task'], public['sources'], 'flaskr_project')
    public['specialist_assignment']['specialists'] = []
    with pytest.raises(ValueError, match='current public evidence'):
        provider.invoke('repair-code', public, SCHEMA)
    assert requests == [] and provider.calls == []


def test_missing_adapter_is_rejected_before_transport(tmp_path):
    provider, requests = model(tmp_path)
    public = {'assigned_task': task('scene', ['web/scene.js']),
              'sources': {'web/scene.js': 'import * as THREE from "three";'}}
    with pytest.raises(ValueError, match='not executable'):
        provider.invoke('repair-code', public, SCHEMA)
    assert requests == []


def test_real_team_dispatch_carries_host_routing_and_records_it(workspace, source_root):
    plan = plan_for(workspace, task('impl', ['flaskr/left.py']))
    schedule = solve_team_plan(plan, wall_seconds=900, token_limit=100000, model_slots=1)
    employee = SyntheticEmployee(workspace)
    ledger = {'request': 'Synthetic unit fixture'}
    execute_team_round(workspace, plan, schedule, call=employee, public_test=lambda: {},
                       emit=lambda *args: None, ledger=ledger, round_number=1, wctp_root=source_root)
    assignment = employee.calls[0]['context']['specialist_assignment']
    assert [item['id'] for item in assignment['specialists']] == ['software']
    round_ = ledger['rounds'][0]
    assert round_['employees'][0]['specialist_ids'] == ['software']
    assert round_['employees'][0]['agent_id'] == assignment['agent_id']
    assert round_['specialist_assignments']['impl'] == assignment


def test_registered_required_adapter_blocks_before_any_employee_call(workspace, source_root):
    workspace.project['requirements'][0]['specialist_ids'] = ['mcu']
    plan = plan_for(workspace, task('impl', ['flaskr/left.py']))
    schedule = solve_team_plan(plan, wall_seconds=900, token_limit=100000, model_slots=1)
    ledger, calls = {'request': 'Synthetic unit fixture'}, []
    before = workspace.candidate_hash()
    with pytest.raises(ValueError, match='assignment blocked'):
        execute_team_round(workspace, plan, schedule, call=lambda *args: calls.append(args),
                           public_test=lambda: {}, emit=lambda *args: None, ledger=ledger,
                           round_number=1, wctp_root=source_root)
    assert calls == [] and workspace.candidate_hash() == before
    assert ledger['rounds'][0]['status'] == 'specialist_blocked'


def test_employee_telemetry_binds_real_task_round_and_handoff_without_source_payload(workspace, source_root):
    plan = plan_for(workspace, task('impl', ['flaskr/left.py']))
    schedule = solve_team_plan(plan, wall_seconds=900, token_limit=100000, model_slots=1)
    ledger, observed = {'request': 'Synthetic telemetry fixture'}, []
    def telemetry(skill, stage, message, **details):
        observed.append({'skill': skill, 'stage': stage, 'message': message, **details})
    execute_team_round(workspace, plan, schedule, call=SyntheticEmployee(workspace), public_test=lambda: {},
                       emit=lambda *args: None, ledger=ledger, round_number=2, wctp_root=source_root,
                       telemetry=telemetry)
    employee_events = [e for e in observed if e['stage'].startswith('employee_')]
    assert [e['stage'] for e in employee_events] == ['employee_assigned', 'employee_tool', 'employee_tool', 'employee_handoff']
    assert all(e['task_id'] == 'impl' and e['round'] == 2 for e in employee_events)
    assert employee_events[0]['sender'] == 'manager' and employee_events[-1]['recipient'] == 'manager'
    assert len([e for e in observed if e['stage']=='allocation_solved']) == 2
    assert all(e['worker_id'] for e in employee_events)
    assert observed[-1]['summary'] == 'Synthetic employee tool proposal'
    assert 'VALUE = 1' not in json.dumps(observed)
    assert all(m['time'] and m['round'] == 2 for m in ledger['rounds'][0]['messages'])


def test_preview_api_reads_registered_code_without_model_or_execution(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'experiments/flaskr'
    shutil.copytree(source, tmp_path / 'experiments/flaskr')
    (tmp_path / 'web').mkdir()
    with TestClient(create_app(Settings(root=tmp_path))) as client:
        response = client.get('/api/project-specialists', params={'project_id': 'Project/flaskr'})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload['execution_performed'] is False
        assert {'backend', 'database'} <= {item['id'] for item in payload['routing']['specialists']}
        assert client.get('/api/project-specialists', params={'project_id': '../unregistered'}).status_code == 400
