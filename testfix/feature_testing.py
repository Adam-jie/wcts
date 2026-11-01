"""User-described features -> real Skill calls -> frozen tests -> original WCTP.

This is a test-only workflow. It never applies a repair or treats model-generated
tests as independent acceptance of an arbitrary new product requirement.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Literal
import uuid

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import DEFAULT_CODEX_MODEL
from .project_models import ProjectModelProvider, ProjectModelError, _validate_schema, project_provider_status
from .project_tools import ProjectWorkspace, confined, files_under, get_project, project_profile, run_project_tool
from .project_workflow import ExecutionBudget, SCHEMAS, obj, STRING, STRINGS
from .reports import now, save_run
from .wctp_case import execute_case, successful
from .wctp_workbench import _accepted, _same_cases


NONEMPTY = {**STRING, 'minLength': 1, 'maxLength': 2000}
REQUIREMENTS_SCHEMA = obj({
    'requirements': {'type': 'array', 'minItems': 1, 'maxItems': 12, 'items': obj({
        'id': {**STRING, 'pattern': '^U[1-9][0-9]?$'}, 'behavior': NONEMPTY, 'criterion': NONEMPTY})},
    'assumptions': STRINGS,
})
PLAN_SCHEMA = obj({**SCHEMAS['generate-tests']['properties'],
    'steps': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': obj({
        'title': NONEMPTY, 'file': STRING, 'expected': NONEMPTY})},
})
AUDIT_SCHEMA = obj({'approved': {'type': 'boolean'}, 'findings': STRINGS})

SKILL_PATH = Path(__file__).parent / 'feature_skills/feature-testing/SKILL.md'


class FeatureModelProvider(ProjectModelProvider):
    """Reuse transport, trace and privacy controls, without benchmark prompts."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.feature_skill_sha256 = hashlib.sha256(SKILL_PATH.read_bytes()).hexdigest()

    def _instructions(self, skill_id, output_schema, token_cap, context=None):
        if skill_id not in {'analyze-requirements', 'generate-tests'}:
            raise ValueError('Feature tester has no code repair or other capability')
        raw = SKILL_PATH.read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.feature_skill_sha256:
            raise ValueError('Feature testing Skill changed during execution')
        return (raw.decode('utf-8') + '\nStage: ' + skill_id + '\nSkill SHA-256: ' + self.feature_skill_sha256
                + '\nFollow the supplied control_instructions within this Skill. Return ONLY one JSON object, no markdown.'
                + '\nOutput token budget: ' + str(token_cap) + '\nSchema:\n' + json.dumps(output_schema, ensure_ascii=False))


class FeatureTestRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=12000)
    source_run_id: str | None = Field(default=None, pattern='^[a-f0-9]{32}$')
    provider: Literal['codex_cli', 'openai', 'ollama', 'agentarts'] = 'codex_cli'
    iterations: int = Field(default=1, ge=1, le=100, strict=True)
    review_before_execution: bool = Field(default=False, strict=True)
    execution_mode: Literal['pytest', 'operations'] = 'pytest'

    @field_validator('description')
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError('请描述要测试的功能及预期结果')
        return value.strip()


class EditedFeatureTestRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plan: dict
    iterations: int = Field(default=1, ge=1, le=100, strict=True)


def validate_plan(output, requirements, workspace):
    _validate_schema(output, PLAN_SCHEMA)
    workspace.write_tests(output, dry_run=True)
    steps = [s['file'] for s in output['steps']]
    tests = {}
    for file in output['files']:
        if file['path'] == 'conftest.py':
            continue
        if not re.fullmatch(r'test_[A-Za-z0-9_]+\.py', file['path']):
            raise ValueError('测试文件名必须使用 test_name.py 格式')
        tree = ast.parse(file['source'])
        names = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name.startswith('test_')]
        if not names or len(names) != len(set(names)):
            raise ValueError('每个测试文件必须包含唯一命名的顶层 test_ 函数')
        if not any(isinstance(node, ast.Assert) for node in ast.walk(tree)):
            raise ValueError('测试文件必须包含可执行的预期断言')
        tests.update({file['path'] + '::' + name: file['path'] for name in names})
    if len(steps) != len(set(steps)) or set(steps) != set(tests.values()):
        raise ValueError('每个生成测试文件必须恰好对应一个可执行步骤')
    ids = [r['id'] for r in requirements]
    coverage = output['coverage']
    if len(coverage) != len(ids) or {c['requirement_id'] for c in coverage} != set(ids):
        raise ValueError('生成测试必须逐项覆盖用户功能，不能遗漏或替换需求')
    covered = {name for c in coverage for name in c['tests']}
    if any(not c['tests'] for c in coverage) or covered != set(tests):
        raise ValueError('覆盖映射必须引用真实 file.py::test_name，并覆盖所有测试函数')


def feature_workspace(settings, store, run, project):
    """Copy a pinned target without borrowing or changing the old frozen suite."""
    if run.get('source_run_id'):
        accepted, original_project, source = _accepted(settings, store, run['source_run_id'])
        if original_project['id'] != project['id']:
            raise ValueError('所选补全版本不属于当前工程')
        original_hash = accepted['tasks'][0]['candidate_sha256']
    else:
        source = None
        original_hash = None
    workspace = ProjectWorkspace(settings, run['id'], project)
    if source is not None:
        candidate = files_under(source / 'candidate')
        changes = [name for name in candidate if candidate[name] != workspace.source_seal.get(name)]
        if changes:
            workspace.apply_patch({'baseline_sha256': workspace.candidate_hash(), 'files': [
                {'path': name, 'source': confined(source / 'candidate', name, exists=True).read_text(encoding='utf-8')}
                for name in changes]})
        if workspace.candidate_hash() != original_hash:
            raise ValueError('测试副本与所选补全版本不一致')
    return workspace


def execute_feature_test(run, settings, store, *, provider=None, tool_runner=run_project_tool):
    if run.get('execution_mode') == 'operations':
        from .operation_testing import execute_operation_test
        return execute_operation_test(run, settings, store, provider=provider, tool_runner=tool_runner)
    started = time.monotonic()
    workspace = None
    run.update(status='running', phase='analysis', model_calls=[], events=[],
               summary={'total': run['iterations'], 'accepted': 0, 'tested': 0, 'passed': 0})
    def persist():
        save_run(settings.artifacts / run['id'], run)
        with store.lock:
            store.runs[run['id']] = deepcopy(run)
    def event(message):
        run['events'].append({'time': now(), 'message': message})
        persist()
    # Includes real model time and all repetitions; keep child execution bounded.
    budget = ExecutionBudget({'wall_seconds': 900 + run['iterations'] * 1800,
                              'token_limit': 800000, 'model_slots': 1, 'cpu_slots': 1})
    try:
        project = get_project(settings, run['project_id'])
        workspace = feature_workspace(settings, store, run, project)
        run['candidate_sha256'] = workspace.candidate_hash()
        profile = project_profile(project)
        if run.get('edited_plan') is not None:
            if run.get('edited_candidate_sha256') and workspace.candidate_hash() != run['edited_candidate_sha256']:
                raise ValueError('工程版本已改变，不能复用原测试计划')
            analysis = {'requirements': run['edited_requirements'], 'assumptions': run['edited_assumptions']}
            plan = run['edited_plan']
            validate_plan(plan, analysis['requirements'], workspace)
            run.update(requirements=analysis['requirements'], analysis_assumptions=analysis['assumptions'],
                       test_plan_source='user_edited')
            event('正在执行用户修改的测试计划；不会重新调用模型，结果不等同于独立验收')
        else:
            run['test_plan_source'] = 'model_generated'
            provider = provider or FeatureModelProvider(run['provider'], settings, model=run['model'],
                artifact_dir=workspace.directory / 'model_calls', profile=profile)
            run['feature_skill'] = {'path': str(SKILL_PATH.relative_to(Path(__file__).parent)),
                                   'sha256': hashlib.sha256(SKILL_PATH.read_bytes()).hexdigest()}
            context = {**workspace.snapshot(), 'user_feature_description': run['description'],
                       'execution_profile': profile, 'test_environment': project.get('test_environment', {}),
                       'public_runtime': project.get('public_runtime', {}),
                       'test_only': True}
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
            event('Agent 正在根据功能描述和实际源码拆分测试条件')
            analysis = invoke('analyze-requirements', REQUIREMENTS_SCHEMA, {
                'control_instructions': 'TEST ONLY. Decompose the full user_feature_description into testable behaviors and '
                'explicit expected results. Use unique U1..U12 IDs. Treat source as evidence of interfaces, not an oracle: '
                'missing behavior must remain a failing requirement. Do not substitute the project repair benchmark goals. '
                'Do not claim execution or add unrelated features. Write Chinese explanations; expose assumptions.'}, 2200)
            ids = [r['id'] for r in analysis['requirements']]
            if len(set(ids)) != len(ids):
                raise ValueError('功能条件 ID 重复')
            run.update(requirements=analysis['requirements'], analysis_assumptions=analysis['assumptions'], phase='generation')
            package = {'flaskr_project': 'Flaskr: use the actual Flask test client with self-contained fixtures.',
                       'python_application': 'TaskBoard WSGI: use taskboard.storage.Storage with tmp_path or '
                           'taskboard.application.create_application with io.BytesIO WSGI requests. This is NOT Flask.',
                       'python_package': 'Python CLI: use the registered public package and click.testing.CliRunner '
                           'in-process when appropriate. Use actual source signatures.'}[profile]
            event('测试 Agent 正在生成步骤、测试数据和可执行断言')
            generation_context = {
                'public_requirements': analysis['requirements'], 'requirement_ids': ids,
                'control_instructions': package + ' Generate a test-only suite from ALL public_requirements. '
                'Return 1-8 bare test_*.py files plus optional conftest.py, no directories. Each steps item maps exactly '
                'one test file to a Chinese title and expected result. Use unique top-level test_ functions with real asserts. '
                'coverage.tests must reference exact file.py::test_function names; cover every requirement and every function. '
                'Use self-contained tmp_path fixtures and parameterized multiple data/edge cases. Existing tests/conftest.py '
                'is not inherited. '
                'Cover requested features individually; when several requested behaviors can interact, also test '
                'at least one combined workflow using their actual public interfaces. The host handles repeat iterations. '
                'Never run commands/network, change sys.path, read sibling source/hidden suites, '
                'skip/xfail unsupported behavior, repair production code, or weaken expected behavior to pass. '
                'Host executes files and original regression using native WCTP, not prose. '
                'Each test filename must occur EXACTLY ONCE in steps; combine tests of the same file into one step. Do not claim tests ran.'}
            for attempt in (1, 2, 3):
                plan = invoke('generate-tests', PLAN_SCHEMA, generation_context, 10000)
                try:
                    validate_plan(plan, analysis['requirements'], workspace)
                    audit = invoke('generate-tests', AUDIT_SCHEMA, {
                        'quality_review': True, 'public_requirements': analysis['requirements'], 'proposed_plan': plan,
                        'control_instructions': 'Audit these unexecuted tests against user requirements and public source. '
                        'Return the audit schema, not code. Check fixture initial state, constructor seed data, '
                        'real API signatures, assertion validity and coverage. A database created at tmp_path is not '
                        'necessarily empty: account for seeded rows or arrange known state through public APIs. '
                        'Reject only concrete defects with a reproducible counterexample or direct source contradiction. '
                        'Do not reject on speculative improvement suggestions. Do not impose delivery gates or claim tests ran. '
                        'Never approve a weaker assertion just to match a product bug. approved=true requires no findings.'}, 2400)
                    run.setdefault('test_quality_audits', []).append(audit)
                    if not audit['approved'] or audit['findings']:
                        raise ValueError('测试生成前置校核：' + '; '.join(audit['findings'] or ['未批准']))
                    break
                except (ValueError, SyntaxError) as exc:
                    if attempt == 3:
                        raise
                    run.setdefault('generation_corrections', []).append({'attempt': attempt, 'reason': str(exc)})
                    event('生成计划未通过结构校验，Agent 正在根据具体错误修正；尚未执行测试')
                    generation_context.update(previous_generated_plan=plan, validation_error=str(exc),
                        correction_policy='Correct the complete plan without changing the user requirements, expected behavior or dropping test coverage.')
        if run.get('review_before_execution') and run.get('edited_plan') is None:
            run.update(status='awaiting_review', phase='review', test_plan=plan,
                       tasks=[{'test_steps': [s['title'] + '：' + s['expected'] for s in plan['steps']],
                               'requirement_coverage': plan['coverage'], 'test_assumptions': plan['assumptions']}])
            event('测试计划已生成，等待用户检查或修改后再运行；尚未执行测试')
            return
        workspace.write_tests(plan)
        run.update(test_plan=plan, generated_sha256=workspace.generated_seal,
                   tasks=[{'test_steps': [s['title'] + '：' + s['expected'] for s in plan['steps']],
                           'requirement_coverage': plan['coverage'], 'test_assumptions': plan['assumptions']}],
                   phase='execution')
        event(f"已冻结 {len(plan['steps'])} 个测试步骤，开始 WCTP 实际执行")
        first_suites, calls = {}, []
        action_lock = threading.Lock()
        run['execution_policy'] = 'WCTP native sequential action edges; one bounded test worker at a time'
        def action(test_file, label):
            def execute():
                # Native graph edges order the actions; keep host accounting
                # atomic without making queued actions wait inside Step timeout.
                with action_lock:
                    budget.remaining()
                    result = tool_runner(settings, workspace, 'feature_tests', timeout_s=min(180, budget.remaining()),
                                         round_number=len(calls) + 1, test_file=test_file)
                    calls.append(result)
                    suite = result['suite']
                    if test_file in first_suites and not _same_cases(first_suites[test_file], suite):
                        raise ValueError('循环收集的测试用例身份发生变化：' + label)
                    first_suites.setdefault(test_file, suite)
                    run['summary']['tested'] += suite['total']
                    run['summary']['passed'] += suite['passed']
                    return suite
            return execute
        actions = {'step_Regression': ('保留原有功能回归', action(None, '原有回归'), successful, '原有回归全部通过')}
        for i, step in enumerate(plan['steps'], 1):
            actions[f'step_Feature{i:02}'] = (step['title'], action(step['file'], step['title']), successful, step['expected'])
        def progress(case):
            run['summary']['accepted'] = case['completed_iterations']
            run['events'] = [e for e in run['events'] if 'message' in e] + deepcopy(case['events'])
            persist()
        _, report = execute_case(workspace.directory / 'wctp-feature', actions, title='按功能描述生成的自动化测试',
                                 iterations=run['iterations'], callback=progress, serial_actions=True)
        workspace.assert_integrity()
        run.update(status='completed' if report['status'] == 'passed' else 'rejected', phase='finished',
                   case_artifact='wctp-feature/report.json', tool_calls=calls, error=report.get('error'))
        event('测试完成，所列用例全部通过' if run['status'] == 'completed' else '测试发现未满足的条件，请查看节点中的实际结果')
    except Exception as exc:
        run.update(status='failed', phase='finished', error=f'{type(exc).__name__}: {exc}')
    finally:
        if workspace is not None:
            try:
                workspace.assert_integrity()
            except Exception as exc:
                run.update(status='failed', error=f'IntegrityError: {exc}')
        run.update(finished_at=now(), duration_s=round(time.monotonic() - started, 3), budget=budget.report())
        persist()


def attach_feature_tests(app, settings, store):
    @app.get('/api/feature-tests')
    def listing(project_id: str = Query(max_length=160)):
        with store.lock:
            store.refresh_external_runs()
            values = [{k: r.get(k) for k in ('id', 'project_id', 'source_run_id', 'description', 'status', 'created_at', 'phase')}
                      for r in store.runs.values() if r.get('kind') == 'feature_test' and r.get('project_id') == project_id]
        return {'runs': sorted(values, key=lambda r: r['created_at'], reverse=True)}

    @app.post('/api/feature-tests/{identifier}/edited', status_code=202)
    def submit_edited(identifier: str, body: EditedFeatureTestRequest):
        with store.lock:
            parent = store.snapshot(identifier)
            if (parent.get('kind') != 'feature_test' or parent.get('status') in {'queued', 'running'}
                    or not parent.get('test_plan') or not parent.get('requirements')
                    or not parent.get('candidate_sha256')):
                raise HTTPException(409, '原测试尚未生成可编辑计划')
            try:
                if parent.get('execution_mode') == 'operations':
                    from .operation_testing import validate_operation_plan
                    validate_operation_plan(body.plan, parent['requirements'])
                else:
                    _validate_schema(body.plan, PLAN_SCHEMA)
            except (ValueError, ProjectModelError) as exc:
                raise HTTPException(422, str(exc)) from None
            if any(r['status'] in {'queued', 'running'} for r in store.runs.values()):
                raise HTTPException(409, '已有任务正在执行，请等待完成后再开始测试')
            run = {'id': uuid.uuid4().hex, 'kind': 'feature_test', 'status': 'queued', 'phase': 'queued',
                   'created_at': now(), 'execution_owner_pid': os.getpid(),
                   **{key: parent.get(key) for key in ('project_id', 'project_title', 'source_run_id',
                      'description', 'provider', 'model', 'execution_mode')},
                   'iterations': body.iterations, 'parent_feature_id': identifier,
                   'test_plan_source': 'user_edited',
                   'edited_plan': body.plan, 'edited_requirements': parent['requirements'],
                   'edited_assumptions': parent.get('analysis_assumptions', []),
                   'edited_candidate_sha256': parent['candidate_sha256'],
                   'events': [], 'tasks': [], 'model_calls': []}
            save_run(settings.artifacts / run['id'], run)
            store.runs[run['id']] = deepcopy(run)
            store._owned_run_ids.add(run['id'])
            store.pool.submit(execute_feature_test, run, settings, store)
        return {'id': run['id'], 'status': 'queued'}

    @app.post('/api/feature-tests', status_code=202)
    def submit(body: FeatureTestRequest):
        try:
            project = get_project(settings, body.project_id)
            if body.execution_mode == 'operations' and (project.get('adapter') != 'python_application'
                    or project.get('application_config') != {'package': 'taskboard', 'source_root': '.'}):
                raise ValueError('逐操作测试目前仅支持已登记的 TaskBoard 工程')
            if body.source_run_id:
                source, _, _ = _accepted(settings, store, body.source_run_id)
                if source['project_id'] != body.project_id:
                    raise ValueError('补全版本与当前工程不匹配')
            if body.provider not in {p['id'] for p in project_provider_status(settings) if p['configured']}:
                raise ValueError('所选真实模型服务未配置或未登录')
            model = {'codex_cli': DEFAULT_CODEX_MODEL, 'openai': settings.model, 'ollama': settings.ollama_model}.get(body.provider)
            if body.provider == 'agentarts':
                from .agentarts_probe import check_runtime
                model = check_runtime(settings)['model']
            if not model:
                raise ValueError('模型名称未配置')
            from .wctp_runtime import WCTPGraphRunner
            WCTPGraphRunner(max_workers=1)
        except (OSError, ValueError, ProjectModelError) as exc:
            raise HTTPException(400, str(exc)) from None
        with store.lock:
            store.refresh_external_runs()
            if any(r['status'] in {'queued', 'running'} for r in store.runs.values()):
                raise HTTPException(409, '已有任务正在执行，请等待完成后再开始测试')
            run = {'id': uuid.uuid4().hex, 'kind': 'feature_test', 'status': 'queued', 'phase': 'queued',
                   'created_at': now(), 'execution_owner_pid': os.getpid(), 'model': model,
                   **body.model_dump(), 'project_title': project['title'], 'events': [], 'tasks': [], 'model_calls': []}
            save_run(settings.artifacts / run['id'], run)
            store.runs[run['id']] = deepcopy(run)
            store._owned_run_ids.add(run['id'])
            store.pool.submit(execute_feature_test, run, settings, store)
        return {'id': run['id'], 'status': 'queued'}
