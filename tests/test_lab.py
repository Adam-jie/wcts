"""Evidence projection and local interactive-probe trust boundaries."""
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from testfix.api import create_app
from testfix.config import Settings
from testfix.lab import project_run
from testfix.lab_probe import _sealed_bytes, execute_probe


def record():
    return {'id': 'a'*32, 'kind': 'autonomous_project', 'project_id': 'Project/rich-cli',
        'provider': 'codex_cli', 'status': 'completed', 'summary': {'accepted': 1, 'total': 1},
        'model_calls': [{'skill_id': 'repair-code', 'status': 'completed',
            'request': {'context': {'secret': 'PRIVATE_MODEL_CONTEXT'}},
            'raw_response': 'PRIVATE_RAW_RESPONSE', 'actual_model': None}],
        'workflow': {'capabilities': [{'skill_id': 'plan-workflow', 'status': 'completed'}]},
        'tasks': [{'status': 'accepted', 'generated_baseline': {'passed': 0, 'total': 2, 'successful': False},
            'generated': {'passed': 2, 'total': 2, 'successful': True},
            'acceptance': {'passed': 3, 'total': 3, 'successful': True,
                'source': 'PRIVATE_ACCEPTANCE_SOURCE', 'stdout_path': 'PRIVATE_HIDDEN_LOG'},
            'diff': '+public change', 'build': {'sha256': '1'*64}}]}


def test_projection_keeps_real_denominators_and_omits_private_context(tmp_path):
    value = project_run(Settings(root=tmp_path), record())
    text = json.dumps(value)
    assert not any(marker in text for marker in ['PRIVATE_MODEL_CONTEXT', 'PRIVATE_RAW_RESPONSE', 'PRIVATE_ACCEPTANCE_SOURCE', 'PRIVATE_HIDDEN_LOG'])
    assert value['metrics']['suites'][0]['before']['total'] == 2
    assert value['metrics']['suites'][0]['after']['passed'] == 2
    assert value['metrics']['suites'][1]['after']['total'] == 3
    assert value['graph']['nodes'][1]['status'] == 'pending'  # No invented completion.
    assert value['model_calls'][0]['actual_model'] is None
    assert value['metrics']['peak_concurrency'] is None


@pytest.mark.parametrize('matching', [True, False])
def test_postcheck_is_candidate_bound_and_never_rewrites_original_scores(tmp_path, matching):
    run = record()
    run['tasks'][0]['candidate_sha256'] = '1'*64
    path = tmp_path/'artifacts'/run['id']/'postchecks.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'schema': 'testfix.postchecks.v1', 'run_id': run['id'],
        'candidate_sha256': ('1' if matching else '2')*64, 'status': 'passed',
        'PRIVATE_SOURCE': 'PRIVATE_ADDITIONAL_TEST', 'checks': [{'id': 'head-tail',
        'requirement_id': 'R2', 'status': 'failed', 'description': 'both flags preserve head',
        'baseline_exit': 0, 'candidate_exit': -1, 'expected': 'head wins', 'actual': 'error',
        'private_test_source': 'PRIVATE_ADDITIONAL_TEST'}]}), encoding='utf-8')
    value = project_run(Settings(root=tmp_path), run)
    assert value['run']['summary'] == run['summary']
    assert value['metrics']['suites'][0]['after']['passed'] == 2
    assert 'PRIVATE_ADDITIONAL_TEST' not in json.dumps(value)
    if matching:
        assert value['postchecks']['status'] == 'failed'  # Derived from actual cases.
        assert value['postchecks']['receipt_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert value['postchecks']['checks'][0]['candidate_exit'] == -1
    else:
        assert value['postchecks']['status'] == 'unverified'
        assert value['postchecks']['checks'] == []


@pytest.mark.parametrize('document', [[], {}, {'schema': 'testfix.postchecks.v1',
    'run_id': 'a'*32, 'candidate_sha256': '1'*64, 'checks': [{'status': 'failed'}]}])
def test_malformed_postcheck_is_unverified_not_a_success(tmp_path, document):
    run = record()
    run['tasks'][0]['candidate_sha256'] = '1'*64
    path = tmp_path/'artifacts'/run['id']/'postchecks.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(document), encoding='utf-8')
    assert project_run(Settings(root=tmp_path), run)['postchecks']['status'] == 'unverified'


def team_record():
    run = record()
    items = [{'id': name, 'title': name, 'skill_id': 'explore-repository', 'paths': ['public.py'], 'depends_on': []} for name in ['left', 'right']]
    run['collaboration'] = {'rounds': [{'plan': {'work_items': items}, 'employees': [], 'batches': [{
        'max_observed_concurrency': 999, 'action_spans': [
            {'step_id': 'left', 'started_monotonic_seconds': 1, 'finished_monotonic_seconds': 5},
            {'step_id': 'right', 'started_monotonic_seconds': 3, 'finished_monotonic_seconds': 6},
            {'step_id': 'syntheticStart', 'started_monotonic_seconds': 0, 'finished_monotonic_seconds': 8}]}]}]}
    return run


def test_concurrency_is_computed_from_employee_spans_not_plan_or_start_node(tmp_path):
    value = project_run(Settings(root=tmp_path), team_record())
    assert value['metrics']['peak_concurrency'] == 2
    assert value['metrics']['overlap_seconds'] == 2
    assert value['metrics']['overlap_batches'] == [2]
    assert value['graph']['nodes'][0]['kind'] == 'controller'


@pytest.mark.parametrize('outside', [True, False])
def test_receipt_outside_run_or_changed_receipt_is_not_displayed(tmp_path, outside):
    run = team_record()
    path = tmp_path/('private.py' if outside else 'artifacts'+'/'+run['id']+'/team/step.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('PRIVATE_FILE_SHOULD_NOT_BE_DISPLAYED', encoding='utf-8')
    run['collaboration']['rounds'][0]['employees'] = [{'task_id': 'left', 'steps': [
        {'path': str(path), 'sha256': '0'*64, 'action': 'read_file', 'ok': True}]}]
    value = project_run(Settings(root=tmp_path), run)
    assert 'PRIVATE_FILE_SHOULD_NOT_BE_DISPLAYED' not in json.dumps(value)
    assert value['tools'][0]['receipt_verified'] is False


def test_valid_receipt_displays_host_action_but_not_full_read_result(tmp_path):
    run = team_record()
    path = tmp_path/'artifacts'/run['id']/'team/step.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'action': {'path': 'public.py', 'message': '<img src=x onerror=alert(1)>'},
        'observation': {'result': 'PRIVATE_READ_RESULT'}}), encoding='utf-8')
    run['collaboration']['rounds'][0]['employees'] = [{'task_id': 'left', 'steps': [
        {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'action': 'read_file', 'ok': True}]}]
    value = project_run(Settings(root=tmp_path), run)
    assert value['tools'][0]['receipt_verified'] is True
    assert value['tools'][0]['path'] == 'public.py'
    assert 'PRIVATE_READ_RESULT' not in json.dumps(value)


def test_rejected_action_uses_verified_host_receipt_and_displays_bounded_reason(tmp_path):
    run = team_record()
    path = tmp_path/'artifacts'/run['id']/'team/step.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'action': {'path': 'public.py'}, 'observation': {
        'ok': False, 'error': 'Patch must be valid Python', 'result': 'PRIVATE_READ_RESULT'}}), encoding='utf-8')
    run['collaboration']['rounds'][0]['employees'] = [{'task_id': 'left', 'steps': [
        {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
         'action': 'apply_patch', 'ok': True}]}]
    value = project_run(Settings(root=tmp_path), run)
    assert value['tools'][0]['receipt_verified'] is True
    assert value['tools'][0]['ok'] is False
    assert value['tools'][0]['error'] == 'Patch must be valid Python'
    assert 'PRIVATE_READ_RESULT' not in json.dumps(value)


def test_replay_is_explicit_and_does_not_invent_nine_capabilities(tmp_path):
    run = record(); run.update(kind=None, provider='replay', events=[{'agent': 'tester', 'message': 'fixture'}])
    run.pop('model_calls')
    value = project_run(Settings(root=tmp_path), run)
    assert [node['id'] for node in value['graph']['nodes']] == ['tester']
    assert any('离线' in message for message in value['warnings'])


def test_frozen_regression_traceback_is_not_exposed_even_inside_tool_calls(tmp_path):
    run = record()
    log = tmp_path/'artifacts'/run['id']/'tool_calls/regression.stdout.txt'
    log.parent.mkdir(parents=True)
    log.write_text('def hidden_check(): assert PRIVATE_FROZEN_TEST_SOURCE', encoding='utf-8')
    run['tasks'][0]['regression'] = {'passed': 0, 'total': 1, 'successful': False, 'stdout_path': str(log)}
    value = project_run(Settings(root=tmp_path), run)
    assert 'PRIVATE_FROZEN_TEST_SOURCE' not in json.dumps(value)
    assert value['metrics']['suites'][2]['after']['successful'] is False


@pytest.mark.parametrize('digest', [None, '', 'not-a-sha'])
def test_missing_receipt_hash_is_never_reported_as_verified(tmp_path, digest):
    run = team_record()
    path = tmp_path/'artifacts'/run['id']/'team/step.json'
    path.parent.mkdir(parents=True)
    path.write_text('{}', encoding='utf-8')
    run['collaboration']['rounds'][0]['employees'] = [{'task_id': 'left', 'steps': [
        {'path': str(path), 'sha256': digest, 'action': 'read_file'}]}]
    assert project_run(Settings(root=tmp_path), run)['tools'][0]['receipt_verified'] is False


@pytest.mark.parametrize('identifier', ['../private', '', 'z'*32])
def test_projection_rejects_unbound_run_ids(tmp_path, identifier):
    run = record(); run['id'] = identifier
    with pytest.raises(ValueError): project_run(Settings(root=tmp_path), run)


def test_probe_rejects_unaccepted_run_before_touching_project_or_starting_process(tmp_path):
    run = record(); run['status'] = 'running'
    with pytest.raises(ValueError, match='accepted'):
        execute_probe(Settings(root=tmp_path), run, 'a,b\n1,2\n')


def test_probe_binary_is_confined_and_sha_checked(tmp_path):
    inside = tmp_path/'run'; inside.mkdir()
    outside = tmp_path/'private'; outside.write_bytes(b'private')
    digest = hashlib.sha256(b'private').hexdigest()
    with pytest.raises(ValueError, match='outside'):
        _sealed_bytes(outside, inside, digest)
    valid = inside/'candidate.whl'; valid.write_bytes(b'wheel')
    with pytest.raises(ValueError, match='changed'):
        _sealed_bytes(valid, inside, '0'*64)
    assert _sealed_bytes(valid, inside, hashlib.sha256(b'wheel').hexdigest()) == b'wheel'


@pytest.fixture
def app(tmp_path):
    (tmp_path/'web').mkdir()
    (tmp_path/'web/index.html').write_text('fixture', encoding='utf-8')
    return create_app(Settings(root=tmp_path))


@pytest.mark.parametrize('body', [{'text': ''}, {'text': 'a'*5001}, {'text': 'safe', 'mode': 'shell'}])
def test_invalid_probe_is_not_executed(app, body):
    response = TestClient(app).post('/api/lab/runs/'+'a'*32+'/probe', json=body)
    assert response.status_code == 422


def test_new_projection_and_probe_retain_local_origin_guard(app):
    client = TestClient(app)
    assert client.get('/api/lab/bootstrap', headers={'host': 'evil.example'}).status_code == 403
    assert client.post('/api/lab/runs/'+'a'*32+'/probe', json={'text': 'safe'},
        headers={'origin': 'https://evil.example'}).status_code == 403
    assert client.get('/api/lab/runs/'+'a'*32).status_code == 404


def test_projection_endpoint_reads_observed_store_state(app):
    app.state.store.runs['a'*32] = record()
    response = TestClient(app).get('/api/lab/runs/'+'a'*32)
    assert response.status_code == 200
    assert response.json()['metrics']['suites'][0]['after']['passed'] == 2
