"""Run-bound WCTP test views and independent repetitions of accepted snapshots."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .project_tools import ProjectWorkspace, files_under, get_project, run_project_tool, confined
from .project_eval import _mapping_sha256, _runtime_files, _sha256, _paired_cases_match
from .reports import now, save_run
from .wctp_case import execute_case


class RepeatRequest(BaseModel):
    iterations: int = Field(default=3, ge=1, le=100, strict=True)


def _candidate_hash(project, directory):
    if project.get('adapter', 'flaskr') == 'flaskr':
        return _mapping_sha256({name: _sha256(directory / name) for name in sorted(_runtime_files(directory))})
    return _mapping_sha256(files_under(directory))


def _same_cases(expected, actual):
    required = {'total', 'case_ids', 'duplicate_case_ids'}
    return (isinstance(expected, dict) and isinstance(actual, dict)
            and required <= expected.keys() and required <= actual.keys()
            and _paired_cases_match(expected, actual))


def public_repetition_passes(value, accepted):
    return all(value.get(key, {}).get('successful') is True and _same_cases(accepted.get(key), value.get(key))
               for key in ('regression', 'generated'))


def independent_repetition_passes(value, accepted, *, adapter, required_checks=None):
    from .project_workflow import delivery_passes
    from .wctp_case import functional_failure
    return (functional_failure(value['baseline']) and value['acceptance']['successful']
            and _same_cases(value['baseline'], value['acceptance'])
            and _same_cases(accepted.get('acceptance'), value['acceptance'])
            and delivery_passes(value['delivery'], adapter=adapter, required_checks=required_checks))


def _accepted(settings, store, identifier):
    if not re.fullmatch('[a-f0-9]{32}', identifier):
        raise HTTPException(404, '运行不存在')
    run = store.snapshot(identifier)
    if run.get('kind') != 'autonomous_project' or run.get('status') != 'completed' or run.get('summary', {}).get('accepted') != 1:
        raise HTTPException(409, '请先完成一次补全与验收，再对冻结版本循环重测')
    project = get_project(settings, run['project_id'])
    source = settings.artifacts / identifier / 'workspace'
    if _candidate_hash(project, source / 'candidate') != run['tasks'][0].get('candidate_sha256'):
        raise HTTPException(409, '候选源码已改变，不能沿用原验收记录')
    return run, project, source


def clone_accepted(settings, store, identifier, new_id):
    run, project, source = _accepted(settings, store, identifier)
    cap = next((item.get('artifact') for item in run['workflow']['capabilities'] if item['skill_id'] == 'generate-tests'), None)
    if not cap or not isinstance(cap.get('path'), str):
        raise ValueError('Missing frozen generation artifact')
    # Historical host-written artifact references use Windows separators.
    artifact = confined(settings.root, cap['path'].replace('\\', '/'), exists=True)
    if not artifact.resolve().is_relative_to((settings.artifacts / identifier).resolve()):
        raise ValueError('Generation artifact belongs to another run')
    raw = artifact.read_bytes()
    if hashlib.sha256(raw).hexdigest() != cap.get('sha256'):
        raise ValueError('Generation artifact hash mismatch')
    content = json.loads(raw)['content']
    expected = content['frozen_hashes']
    if files_under(source / 'generated_tests') != expected:
        raise ValueError('Frozen generated tests changed after acceptance')
    workspace = ProjectWorkspace(settings, new_id, project)
    if files_under(source / 'baseline') != workspace.source_seal or files_under(source / 'private/acceptance_tests') != workspace.hidden_seal:
        raise ValueError('Source or independent acceptance differs from the sealed contract')
    if workspace.regression_seal is not None and files_under(source / 'private/regression_tests') != workspace.regression_seal:
        raise ValueError('Behavior regression differs from the sealed contract')
    workspace.write_tests({'files': [{'path': name, 'source': confined(source / 'generated_tests', name, exists=True).read_text(encoding='utf-8')} for name in expected]})
    candidate = files_under(source / 'candidate')
    changes = [name for name in candidate if candidate[name] != workspace.source_seal.get(name)]
    if changes:
        workspace.apply_patch({'baseline_sha256': workspace.candidate_hash(), 'files': [
            {'path': name, 'source': confined(source / 'candidate', name, exists=True).read_text(encoding='utf-8')} for name in changes]})
    if workspace.candidate_hash() != run['tasks'][0]['candidate_sha256'] or workspace.generated_seal != expected:
        raise ValueError('Repetition does not match accepted source/test hashes')
    workspace.assert_integrity()
    return workspace


def execute_repeat(run, settings, store):
    started = time.monotonic()
    run.update(status='running', events=[], summary={'total': run['iterations'], 'accepted': 0})
    def persist():
        save_run(settings.artifacts / run['id'], run)
        with store.lock:
            store.runs[run['id']] = deepcopy(run)
    persist()
    try:
        accepted = store.snapshot(run['source_run_id'])['tasks'][0]
        workspace = clone_accepted(settings, store, run['source_run_id'], run['id'])
        count = {'public': 0, 'verify': 0}
        def operation(name, key):
            def call():
                count[key] += 1
                return run_project_tool(settings, workspace, name, timeout_s=600, round_number=count[key])
            return call
        def public_ok(value):
            return public_repetition_passes(value, accepted)
        def verify_ok(value):
            adapter = workspace.adapter
            checks = None
            if adapter == 'python_application':
                from .application_tools import REQUIRED_TASKBOARD_CHECKS
                checks = REQUIRED_TASKBOARD_CHECKS
            elif adapter == 'python_package':
                checks = [c['name'] for c in workspace.project['package_config']['checks']]
            return independent_repetition_passes(value, accepted, adapter=adapter, required_checks=checks)
        def progress(case):
            run['events'] = deepcopy(case['events'])
            run['summary']['accepted'] = case['completed_iterations']
            persist()
        _, report = execute_case(workspace.directory / 'wctp-repeat', {
            'step_Public': ('原有回归与冻结生成测试', operation('candidate_public', 'public'), public_ok, '每次全部通过，且用例身份与原验收一致'),
            'step_Independent': ('原版对照、独立验收与产品运行', operation('verify', 'verify'), verify_ok, '原版暴露缺口；前后及原验收用例身份一致；候选通过验收、构建及运行检查'),
        }, title='冻结版本循环重测', iterations=run['iterations'], callback=progress)
        workspace.assert_integrity()
        run.update(status='completed' if report['status'] == 'passed' else 'failed',
                   candidate_sha256=workspace.candidate_hash(), error=report.get('error'),
                   case_artifact='wctp-repeat/report.json')
    except Exception as error:
        run.update(status='failed', error=f'{type(error).__name__}: {error}')
    finally:
        run.update(finished_at=now(), duration_s=round(time.monotonic() - started, 3))
        persist()


def case_paths(settings, store, identifier):
    if not re.fullmatch('[a-f0-9]{32}', identifier):
        raise HTTPException(404, '运行不存在')
    store.snapshot(identifier)
    owners = [identifier]
    with store.lock:
        owners += [r['id'] for r in store.runs.values() if r.get('kind') == 'wctp_test' and r.get('source_run_id') == identifier]
    paths = {}
    for owner in owners:
        root = settings.artifacts / owner
        for path in [*root.glob('tool_calls/*/wctp*/report.json'), root / 'wctp-repeat/report.json', root / 'wctp-feature/report.json']:
            if path.is_file() and path.resolve().is_relative_to(root.resolve()):
                key = owner + ':' + ('feature' if path.parent.name == 'wctp-feature' else 'repeat' if path.parent.name == 'wctp-repeat' else path.parent.parent.name + '-' + path.parent.name)
                paths[key] = path
    return paths


def case_projection(path, owner):
    report = json.loads(path.read_text(encoding='utf-8'))
    report.update(owner_run_id=owner['id'], owner_status=owner['status'])
    if report['status'] == 'running' and owner['status'] not in {'running', 'queued'}:
        report.update(recorded_status='running', status='failed' if owner['status'] == 'failed' else 'interrupted',
                      error=owner.get('error') or '执行所属任务已结束，该用例没有完成回执；保留已完成步骤')
    return report


def attach_wctp_tests(app, settings, store):
    router = APIRouter()

    @router.get('/api/project-runs/{identifier}/wctp-tests')
    def listing(identifier: str):
        cases = []
        for key, path in case_paths(settings, store, identifier).items():
            try:
                report = case_projection(path, store.snapshot(key.split(':')[0]))
                cases.append({'id': key, 'title': report['title'], 'status': report['status'],
                    'created_at': report['created_at'], 'iterations': report['iterations'],
                    'completed_iterations': report['completed_iterations'], 'events': len(report['events'])})
            except (OSError, ValueError, KeyError):
                continue
        cases.sort(key=lambda c: c['created_at'])
        with store.lock:
            repeats = sorted([{'id': r['id'], 'status': r['status'], 'created_at': r['created_at'],
                               'iterations': r['iterations'], 'error': r.get('error')}
                              for r in store.runs.values() if r.get('kind') == 'wctp_test' and r.get('source_run_id') == identifier],
                             key=lambda r: r['created_at'])
        return {'cases': cases, 'engine': 'WCTP 原始图执行器', 'historical': not cases,
                'active_repeat': next((r for r in reversed(repeats) if r['status'] in {'queued', 'running'}), None),
                'latest_repeat': repeats[-1] if repeats else None}

    def selected(identifier, key):
        path = case_paths(settings, store, identifier).get(key)
        if path is None:
            raise HTTPException(404, 'WCTP 用例尚未生成或不存在')
        return path

    @router.get('/api/project-runs/{identifier}/wctp-tests/case')
    def detail(identifier: str, key: str):
        path = selected(identifier, key)
        return case_projection(path, store.snapshot(key.split(':')[0]))

    @router.get('/api/project-runs/{identifier}/wctp-tests/yaml')
    def export(identifier: str, key: str):
        path = selected(identifier, key).with_name('case.yaml')
        if not path.is_file() or not path.resolve().is_relative_to(settings.artifacts.resolve()):
            raise HTTPException(404, '用例文件不存在或路径无效')
        return FileResponse(path, media_type='application/yaml', filename='wctp-case.yaml')

    @router.post('/api/project-runs/{identifier}/wctp-tests', status_code=202)
    def repeat(identifier: str, request: RepeatRequest):
        source, project, _ = _accepted(settings, store, identifier)
        # Validate the original WCTP dependency before accepting background work.
        from .wctp_runtime import WCTPGraphRunner
        WCTPGraphRunner(max_workers=1)
        with store.lock:
            store.refresh_external_runs()
            if any(r.get('status') in {'running', 'queued'} for r in store.runs.values()):
                raise HTTPException(409, '已有任务正在执行，请等待完成后重测')
            run = {'id': uuid.uuid4().hex, 'kind': 'wctp_test', 'status': 'queued', 'created_at': now(),
                'source_run_id': identifier, 'project_id': project['id'], 'iterations': request.iterations,
                'execution_owner_pid': os.getpid(), 'events': [], 'summary': {'total': request.iterations, 'accepted': 0}}
            store.runs[run['id']] = deepcopy(run)
            store._owned_run_ids.add(run['id'])
            save_run(settings.artifacts / run['id'], run)
            store.pool.submit(execute_repeat, run, settings, store)
        return {'id': run['id'], 'status': 'queued'}

    app.include_router(router)
