"""Generate and execute individual TaskBoard HTTP operations through WCTP."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .application_tools import start_taskboard
from .package_tools import _stop_tree
from .project_models import ProjectModelError, ProjectModelProvider, _validate_schema
from .project_tools import get_project, run_project_tool
from .project_workflow import ExecutionBudget, STRING, STRINGS, obj
from .reports import now, save_run
from .wctp_case import execute_case, successful


SKILL = Path(__file__).parent / 'feature_skills/operation-testing/SKILL.md'
CHECK = obj({'field': STRING, 'op': {'type': 'string', 'enum': ['equals', 'contains']}, 'value_json': STRING})
STEP = obj({'title': STRING, 'method': {'type': 'string', 'enum': ['GET', 'POST', 'PATCH', 'DELETE', 'RESTART']},
            'path': STRING, 'query_json': STRING, 'body_json': STRING,
            'expected_status': {'type': 'integer', 'minimum': 0, 'maximum': 599},
            'checks': {'type': 'array', 'items': CHECK}, 'capture_name': STRING, 'capture_field': STRING,
            'requirements': STRINGS, 'expected': STRING})
PLAN = obj({'steps': {'type': 'array', 'minItems': 1, 'maxItems': 27, 'items': STEP}, 'assumptions': STRINGS})


class OperationModelProvider(ProjectModelProvider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.skill_sha256 = hashlib.sha256(SKILL.read_bytes()).hexdigest()

    def _instructions(self, skill_id, output_schema, token_cap, context=None):
        raw = SKILL.read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.skill_sha256:
            raise ValueError('逐步骤测试 Skill 已变化')
        return (raw.decode('utf-8') + '\nStage: ' + skill_id + '\nFollow control_instructions. '
                'Return only one JSON object.\nSchema:\n' + json.dumps(output_schema, ensure_ascii=False))


def validate_operation_plan(plan, requirements):
    _validate_schema(plan, PLAN)
    known = set()
    used = set()
    valid_ids = {item['id'] for item in requirements}
    for step in plan['steps']:
        if not step['title'].strip() or not step['expected'].strip():
            raise ValueError('每个操作必须填写名称与预期')
        values = []
        for field in ('query_json', 'body_json'):
            try:
                value = json.loads(step[field])
            except (ValueError, TypeError):
                raise ValueError(f'{step["title"]} 的 {field} 不是有效 JSON') from None
            if field == 'query_json' and not isinstance(value, dict):
                raise ValueError('查询参数必须是 JSON 对象')
            if field == 'body_json' and value is not None and not isinstance(value, dict):
                raise ValueError('请求正文必须是 JSON 对象或 null')
            values.append(value)
        for check in step['checks']:
            try:
                values.append(json.loads(check['value_json']))
            except ValueError:
                raise ValueError(f'{step["title"]} 的断言值不是有效 JSON') from None
        if step['method'] == 'RESTART':
            if step['path'] or step['expected_status'] != 0 or step['checks'] or step['capture_name']:
                raise ValueError('重启步骤不能包含路径、状态断言或变量捕获')
        elif not re.fullmatch(r'/api/(?:health|tasks(?:/(?:[1-9][0-9]*|\{[a-zA-Z][a-zA-Z0-9_]{0,31}\}))?)', step['path']):
            raise ValueError(f'{step["title"]} 的路径不受当前 TaskBoard 适配器支持')
        if bool(step['capture_name']) != bool(step['capture_field']):
            raise ValueError('保存变量需要同时指定变量名和响应字段')
        if step['capture_name']:
            if not re.fullmatch('[a-zA-Z][a-zA-Z0-9_]{0,31}', step['capture_name']):
                raise ValueError('保存变量名格式无效')
        references = set(re.findall(r'\{([a-zA-Z][a-zA-Z0-9_]{0,31})\}', step['path']))
        references.update(variable_references(values))
        if references - known:
            raise ValueError('步骤使用了尚未保存的变量')
        if step['capture_name']:
            known.add(step['capture_name'])
        if not set(step['requirements']) <= valid_ids:
            raise ValueError('操作引用了不存在的功能条件')
        used.update(step['requirements'])
    if used != valid_ids:
        raise ValueError('操作计划未覆盖所有功能条件')


def variable_references(value):
    if isinstance(value, dict):
        if set(value) == {'$var'}:
            return {value['$var']}
        return set().union(*(variable_references(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(variable_references(item) for item in value))
    return set()


def field_value(value, path):
    for part in path.split('.') if path else []:
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def substitute(value, variables):
    if isinstance(value, dict):
        if set(value) == {'$var'}:
            return variables[value['$var']]
        return {key: substitute(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, variables) for item in value]
    return value


def execute_operation_test(run, settings, store, *, provider=None, tool_runner=run_project_tool):
    from .feature_testing import feature_workspace, REQUIREMENTS_SCHEMA

    started = time.monotonic()
    workspace = None
    server = None
    state = {'variables': {}, 'round': 0, 'server': None, 'origin': None, 'database': None, 'server_count': 0}
    run.update(status='running', phase='analysis', model_calls=[], events=[],
               summary={'total': run['iterations'], 'accepted': 0, 'tested': 0, 'passed': 0})

    def persist():
        save_run(settings.artifacts / run['id'], run)
        with store.lock:
            store.runs[run['id']] = deepcopy(run)

    def event(message):
        run['events'].append({'time': now(), 'message': message})
        persist()

    def stop_server():
        if state['server'] is not None:
            if state['server'].poll() is None:
                _stop_tree(state['server'])
            state['server'] = None

    def start_server():
        state['server_count'] += 1
        folder = workspace.directory / 'operation-runs' / f"round-{state['round']}" / f"server-{state['server_count']}"
        state['server'], state['origin'], command = start_taskboard(workspace.project['python'], workspace.candidate,
            state['database'], folder)
        return command

    try:
        project = get_project(settings, run['project_id'])
        if project.get('adapter') != 'python_application' or project.get('application_config') != {'package': 'taskboard', 'source_root': '.'}:
            raise ValueError('逐操作测试目前仅支持已登记的 TaskBoard 工程')
        workspace = feature_workspace(settings, store, run, project)
        run['candidate_sha256'] = workspace.candidate_hash()
        if run.get('edited_plan') is not None:
            if workspace.candidate_hash() != run['edited_candidate_sha256']:
                raise ValueError('工程版本已改变，不能复用原测试计划')
            analysis = {'requirements': run['edited_requirements'], 'assumptions': run['edited_assumptions']}
            plan = run['edited_plan']
            validate_operation_plan(plan, analysis['requirements'])
            run['test_plan_source'] = 'user_edited'
            event('正在执行检查后的逐操作测试计划')
        else:
            run['feature_skill'] = {'path': str(SKILL.relative_to(Path(__file__).parent)),
                                    'sha256': hashlib.sha256(SKILL.read_bytes()).hexdigest()}
            provider = provider or OperationModelProvider(run['provider'], settings, model=run['model'],
                artifact_dir=workspace.directory / 'model_calls', profile='python_application')
            budget = ExecutionBudget({'wall_seconds': 900 + run['iterations'] * 1800,
                                      'token_limit': 800000, 'model_slots': 1, 'cpu_slots': 1})
            context = {**workspace.snapshot(), 'user_feature_description': run['description'],
                       'public_runtime': project.get('public_runtime', {}), 'test_only': True}

            def invoke(skill, schema, extra, cap):
                payload = {**context, **extra}
                reservation = budget.reserve(payload, cap)
                try:
                    value = provider.invoke(skill, payload, schema, timeout_s=min(240, budget.remaining()), token_cap=cap)
                    _validate_schema(value, schema)
                    return value
                finally:
                    run['model_calls'] = provider.snapshot_calls()
                    if provider.last_trace is not None:
                        budget.reconcile(provider.last_trace, reservation)
                    persist()

            event('Agent 正在将需求拆成可验证的功能条件')
            analysis = invoke('analyze-requirements', REQUIREMENTS_SCHEMA,
                {'control_instructions': 'Decompose the user request into testable U1..U12 requirements. Return the supplied schema.'}, 2200)
            if len({item['id'] for item in analysis['requirements']}) != len(analysis['requirements']):
                raise ValueError('功能条件 ID 重复')
            run.update(requirements=analysis['requirements'], analysis_assumptions=analysis['assumptions'], phase='generation')
            event('Agent 正在生成每个请求、参数和断言')
            extra = {'public_requirements': analysis['requirements'], 'control_instructions':
                'Produce one operation per step, with observable expected_status and checks. '
                'Use JSON strings for query_json, body_json and check value_json. Use null body for GET/DELETE. '
                'Use {"$var":"task_id"} inside JSON and {task_id} in paths to reference captures. '
                'TaskBoard has 10 seed tasks; use unique task titles and filtered queries. '
                'GET /api/tasks?q=...&page=...&page_size=... returns items, total, page, page_size. '
                'POST /api/tasks {title} returns id,title,completed; PATCH /api/tasks/{id} {completed}; '
                'GET and DELETE /api/tasks/{id}. RESTART has path empty, expected_status 0 and no checks/capture. '
                'Every requirement ID must be attached to relevant steps. No shell, external URL or unexecuted claims.'}
            for attempt in range(3):
                try:
                    plan = invoke('generate-tests', PLAN, extra, 9000)
                    validate_operation_plan(plan, analysis['requirements'])
                    break
                except (ValueError, ProjectModelError) as exc:
                    if attempt == 2 or isinstance(exc, ProjectModelError) and exc.code != 'invalid_schema':
                        raise
                    extra['validation_error'] = str(exc)
                    event('步骤计划格式或覆盖有误，Agent 正在修正')
            run['test_plan_source'] = 'model_generated'
        run.update(requirements=analysis['requirements'], analysis_assumptions=analysis['assumptions'], test_plan=plan,
                   tasks=[{'test_steps': [step['title'] + '：' + step['expected'] for step in plan['steps']],
                           'requirement_coverage': [], 'test_assumptions': plan['assumptions']}])
        if run.get('review_before_execution') and run.get('edited_plan') is None:
            run.update(status='awaiting_review', phase='review')
            event('逐操作测试计划已生成，等待检查；尚未执行')
            return
        run['phase'] = 'execution'
        event(f"开始执行 {len(plan['steps'])} 个具体操作与原有回归")

        def setup():
            stop_server()
            state.update(round=state['round'] + 1, variables={}, server_count=0)
            state['database'] = workspace.directory / 'operation-runs' / f"round-{state['round']}" / 'tasks.sqlite3'
            command = start_server()
            return {'successful': True, 'cases': [{'id': '启动独立应用', 'status': 'passed'}],
                    'actual': {'command': command, 'database': str(state['database'])}}

        def regression():
            result = tool_runner(settings, workspace, 'feature_tests', timeout_s=180, round_number=state['round'])['suite']
            run['summary']['tested'] += result['total']
            run['summary']['passed'] += result['passed']
            return result

        def operation(step):
            def execute():
                if step['method'] == 'RESTART':
                    stop_server()
                    command = start_server()
                    result = {'successful': True, 'cases': [{'id': '应用重启', 'status': 'passed'}],
                              'request': {'method': 'RESTART'}, 'actual': {'command': command}}
                else:
                    path = step['path'].format(**state['variables'])
                    query = substitute(json.loads(step['query_json']), state['variables'])
                    body = substitute(json.loads(step['body_json']), state['variables'])
                    target = state['origin'] + path + ('?' + urlencode(query) if query else '')
                    request = Request(target, method=step['method'],
                        data=json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None,
                        headers={'Content-Type': 'application/json'})
                    try:
                        response = urlopen(request, timeout=8)
                    except HTTPError as error:
                        response = error
                    with response:
                        status = response.status
                        actual = json.loads(response.read(2_000_000))
                    cases = [{'id': 'HTTP 状态码', 'status': 'passed' if status == step['expected_status'] else 'failure',
                              'message': f"预期 {step['expected_status']}，实际 {status}"}]
                    for check in step['checks']:
                        expected = substitute(json.loads(check['value_json']), state['variables'])
                        try:
                            observed = field_value(actual, check['field'])
                            ok = (type(observed) is type(expected) and observed == expected) if check['op'] == 'equals' else expected in observed
                        except (KeyError, IndexError, TypeError, ValueError):
                            observed, ok = '字段不存在', False
                        cases.append({'id': check['field'] or '完整响应', 'status': 'passed' if ok else 'failure',
                                      'message': f'预期 {expected!r}，实际 {observed!r}'})
                    if step['capture_name'] and all(case['status'] == 'passed' for case in cases):
                        try:
                            state['variables'][step['capture_name']] = field_value(actual, step['capture_field'])
                        except (KeyError, IndexError, TypeError, ValueError):
                            cases.append({'id': '保存变量 ' + step['capture_name'], 'status': 'failure', 'message': '响应字段不存在'})
                    result = {'successful': all(case['status'] == 'passed' for case in cases), 'cases': cases,
                              'request': {'method': step['method'], 'path': path, 'query': query, 'body': body},
                              'actual': {'status': status, 'body': actual}, 'variables': dict(state['variables'])}
                run['summary']['tested'] += len(result['cases'])
                run['summary']['passed'] += sum(case['status'] == 'passed' for case in result['cases'])
                return result
            return execute

        actions = {'step_SetupApp': ('启动独立应用与测试数据库', setup, successful, '启动与源码版本绑定的本地应用'),
                   'step_Regression': ('执行原有功能回归', regression, successful, '原有回归全部通过')}
        for index, step in enumerate(plan['steps'], 1):
            actions[f'step_Operation{index:02}'] = (step['title'], operation(step), successful, step['expected'])

        def progress(case):
            run['summary']['accepted'] = case['completed_iterations']
            run['events'] = [item for item in run['events'] if 'message' in item] + deepcopy(case['events'])
            persist()

        _, report = execute_case(workspace.directory / 'wctp-feature', actions, title='逐操作功能测试',
                                 iterations=run['iterations'], callback=progress, serial_actions=True)
        workspace.assert_integrity()
        run.update(status='completed' if report['status'] == 'passed' else 'rejected', phase='finished',
                   case_artifact='wctp-feature/report.json', error=report.get('error'))
        event('逐操作测试完成' if run['status'] == 'completed' else '测试未通过，保留失败节点与实际响应')
    except Exception as exc:
        run.update(status='failed', phase='finished', error=f'{type(exc).__name__}: {exc}')
    finally:
        stop_server()
        if workspace is not None:
            try:
                workspace.assert_integrity()
            except Exception as exc:
                run.update(status='failed', error=f'IntegrityError: {exc}')
        run.update(finished_at=now(), duration_s=round(time.monotonic() - started, 3))
        persist()
