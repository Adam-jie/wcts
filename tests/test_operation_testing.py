from copy import deepcopy
import json
import threading
from types import SimpleNamespace
import uuid

import pytest

from test_application_execution import imported
from testfix import operation_testing


REQUIREMENTS = [{'id': 'U1', 'behavior': '创建后重新读取任务', 'criterion': '返回相同标题'}]


def step(title, method, path, status, *, body='null', query='{}', checks=(), capture='', field=''):
    return {'title': title, 'method': method, 'path': path, 'query_json': query, 'body_json': body,
            'expected_status': status, 'checks': list(checks), 'capture_name': capture, 'capture_field': field,
            'requirements': ['U1'], 'expected': title + '成功'}


def check(field, value):
    return {'field': field, 'op': 'equals', 'value_json': json.dumps(value, ensure_ascii=False)}


PLAN = {'steps': [
    step('创建任务', 'POST', '/api/tasks', 201, body='{"title":"逐步骤演示"}',
         checks=[check('title', '逐步骤演示')], capture='task_id', field='id'),
    step('读取刚创建的任务', 'GET', '/api/tasks/{task_id}', 200,
         checks=[check('id', {'$var': 'task_id'}), check('title', '逐步骤演示')]),
    step('重启程序', 'RESTART', '', 0),
    step('重新读取任务', 'GET', '/api/tasks/{task_id}', 200, checks=[check('title', '逐步骤演示')]),
], 'assumptions': []}


class Provider:
    def __init__(self, plan=PLAN):
        self.plan = plan
        self.calls = []
        self.last_trace = None

    def invoke(self, skill, context, schema, **kwargs):
        self.last_trace = {'skill_id': skill, 'usage': {'total_tokens': 1}}
        self.calls.append(self.last_trace)
        return deepcopy({'requirements': REQUIREMENTS, 'assumptions': []} if skill == 'analyze-requirements' else self.plan)

    def snapshot_calls(self):
        return deepcopy(self.calls)


def make_run(project, **extra):
    return {'id': uuid.uuid4().hex, 'kind': 'feature_test', 'execution_mode': 'operations', 'status': 'queued',
            'phase': 'queued', 'project_id': project['id'], 'project_title': project['title'],
            'created_at': '2026-10-31T00:00:00Z', 'model': 'test', 'provider': 'codex_cli',
            'description': '创建任务并重启后读取', 'iterations': 2, **extra}


@pytest.fixture
def registered(tmp_path):
    settings, _, _, project = imported(tmp_path)
    (tmp_path / 'data/projects/.hide-builtin-demo').touch()
    return settings, project


def test_real_http_steps_execute_through_wctp_with_independent_rounds(registered):
    settings, project = registered
    run = make_run(project)
    store = SimpleNamespace(lock=threading.RLock(), runs={})
    provider = Provider()
    operation_testing.execute_operation_test(run, settings, store, provider=provider)
    assert run['status'] == 'completed', run.get('error')
    assert run['summary']['accepted'] == 2
    case = json.loads((settings.artifacts / run['id'] / 'wctp-feature/report.json').read_text(encoding='utf-8'))
    steps = [item for item in case['results'] if item['step_id'] == 'step_Operation02']
    assert len(steps) == 2 and all(item['status'] for item in steps)
    output = steps[0]['outputs']['step_Operation02_result']
    assert output['request']['path'].startswith('/api/tasks/')
    assert output['actual']['body']['title'] == '逐步骤演示'
    assert output['actual']['status'] == 200
    assert len(provider.calls) == 2 and case['native_step_execution']


def test_actual_failure_remains_visible_and_stops_following_steps(registered):
    settings, project = registered
    plan = deepcopy(PLAN)
    plan['steps'][1]['checks'][1] = check('title', '错误的预期标题')
    run = make_run(project)
    run['iterations'] = 1
    store = SimpleNamespace(lock=threading.RLock(), runs={})
    operation_testing.execute_operation_test(run, settings, store, provider=Provider(plan))
    assert run['status'] == 'rejected', run.get('error')
    report = json.loads((settings.artifacts / run['id'] / 'wctp-feature/report.json').read_text(encoding='utf-8'))
    failed = next(item for item in report['results'] if item['step_id'] == 'step_Operation02')
    assert not failed['status']
    assert failed['outputs']['step_Operation02_result']['actual']['body']['title'] == '逐步骤演示'
    assert not any(item['step_id'] == 'step_Operation03' for item in report['results'])


def test_review_stops_before_starting_application(registered):
    settings, project = registered
    run = make_run(project, review_before_execution=True)
    operation_testing.execute_operation_test(run, settings, SimpleNamespace(lock=threading.RLock(), runs={}), provider=Provider())
    assert run['status'] == 'awaiting_review', run.get('error')
    assert run['test_plan'] == PLAN
    assert not (settings.artifacts / run['id'] / 'operation-runs').exists()


def test_plan_rejects_uncovered_requirements_and_unavailable_paths():
    invalid = deepcopy(PLAN)
    invalid['steps'][0]['path'] = 'https://example.com'
    with pytest.raises(ValueError, match='路径'):
        operation_testing.validate_operation_plan(invalid, REQUIREMENTS)
    invalid = deepcopy(PLAN)
    for item in invalid['steps']:
        item['requirements'] = []
    with pytest.raises(ValueError, match='覆盖'):
        operation_testing.validate_operation_plan(invalid, REQUIREMENTS)


def test_plan_rejects_variable_before_capture():
    invalid = deepcopy(PLAN)
    invalid['steps'][0]['path'] = '/api/tasks/{task_id}'
    with pytest.raises(ValueError, match='尚未保存'):
        operation_testing.validate_operation_plan(invalid, REQUIREMENTS)
    invalid = deepcopy(PLAN)
    invalid['steps'][0]['body_json'] = '{"title":{"$var":"task_id"}}'
    with pytest.raises(ValueError, match='尚未保存'):
        operation_testing.validate_operation_plan(invalid, REQUIREMENTS)


def test_boolean_response_is_not_equal_to_number(registered):
    settings, project = registered
    plan = deepcopy(PLAN)
    plan['steps'][1]['checks'].append(check('completed', 0))
    run = make_run(project, iterations=1)
    operation_testing.execute_operation_test(run, settings, SimpleNamespace(lock=threading.RLock(), runs={}), provider=Provider(plan))
    assert run['status'] == 'rejected', run.get('error')
    report = json.loads((settings.artifacts / run['id'] / 'wctp-feature/report.json').read_text(encoding='utf-8'))
    output = next(item for item in report['results'] if item['step_id'] == 'step_Operation02')['outputs']['step_Operation02_result']
    assert output['actual']['body']['completed'] is False
    assert any(item['id'] == 'completed' and item['status'] == 'failure' for item in output['cases'])


@pytest.mark.parametrize('complete_object', [True, False])
def test_contains_uses_exact_list_membership(registered, complete_object):
    settings, project = registered
    expected = {'title': '逐步骤演示'}
    if complete_object:
        expected.update(id={'$var': 'task_id'}, completed=False)
    plan = deepcopy(PLAN)
    plan['steps'].append(step('核对搜索列表中的完整任务', 'GET', '/api/tasks', 200,
        query='{"q":"逐步骤演示"}', checks=[{'field': 'items', 'op': 'contains',
                                            'value_json': json.dumps(expected, ensure_ascii=False)}]))
    run = make_run(project, iterations=1)
    operation_testing.execute_operation_test(run, settings,
        SimpleNamespace(lock=threading.RLock(), runs={}), provider=Provider(plan))
    assert run['status'] == ('completed' if complete_object else 'rejected'), run.get('error')
