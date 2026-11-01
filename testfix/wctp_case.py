"""Execute software test actions with the user's unchanged WCTP test classes.

Only host-registered actions are bound. Mermaid/YAML, StepRegistry, native
Step.execute, LoopStep, ExecutionContext and GraphExecutor come from verified
external WCTP sources. Pytest/HTTP adapters are actions, not another scheduler.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re
import threading
import time
import uuid

import yaml

from .reports import now
from .wctp_runtime import WCTPGraphRunner, SOURCE_SHA256


def write_json(path, value):
    from .reports import _publish_text
    _publish_text(path, json.dumps(value, ensure_ascii=False, allow_nan=False))


def execute_case(directory: Path, actions: dict, *, title: str, iterations=1, callback=None, serial_actions=False):
    """Run (title, callback, predicate, expectation) actions in a native WCTP loop.

    Callbacks must provide their own bounded subprocess execution. The original
    Step timeout cannot interrupt Python callbacks; this is not a sandbox.
    Failed cases stop at the first failed batch, keeping every finished result.
    """
    if type(iterations) is not int or not 1 <= iterations <= 100:
        raise ValueError('WCTP iterations must be an integer between 1 and 100')
    if not actions or len(actions) > 32 or any(not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}', k) for k in actions):
        raise ValueError('WCTP requires 1–32 host-registered action identifiers')
    runner = WCTPGraphRunner(max_workers=1)
    core = runner.core
    registry = core['tcm.step_registry'].StepRegistry()
    steps = [
        {'step_id': 'step_Prepare', 'step_name': '载入冻结输入', 'step_type': 'NormalStep'},
        {'step_id': 'step_Loop', 'step_name': '循环测试', 'step_type': 'LoopStep', 'total': iterations},
        {'step_id': 'step_Dispatch', 'step_name': '测试分支', 'step_type': 'NormalStep'},
        *[{'step_id': key, 'step_name': spec[0], 'step_type': 'NormalStep', 'timeout': 600,
           'output': {key + '_result': None}, 'expected_result': spec[3]} for key, spec in actions.items()],
        {'step_id': 'step_Join', 'step_name': '汇总本轮结果', 'step_type': 'NormalStep'},
        {'step_id': 'step_End', 'step_name': '完成', 'step_type': 'EndStep'},
    ]
    action_ids = list(actions)
    action_edges = ([f'    {left} --> {right}' for left, right in zip(
        ['step_Dispatch', *action_ids], [*action_ids, 'step_Join'])] if serial_actions else
        [line for key in actions for line in (f'    step_Dispatch --> {key}', f'    {key} --> step_Join')])
    graph_text = '\n'.join(['graph TD', '    step_Prepare --> step_Loop',
        '    step_Loop -->|True| step_Dispatch', '    step_Loop -->|False| step_End',
        *action_edges,
        '    step_Join --> step_Loop'])
    document = {'case_id': 'testfix_software_tests', 'case_name': title, 'graph': graph_text,
                'inline_steps': steps, 'config': {'iterations': iterations},
                'host_binding': 'Actions are bound by TestFix to sealed source and test hashes; YAML alone grants no execution authority.'}
    registry.register_inline(steps)
    graph = core['tcm.case_parser'].parse_graph(document['case_id'], graph_text)
    errors = core['tcm.case_parser'].validate_graph(graph, registry)
    if errors:
        raise ValueError('Invalid original WCTP test graph: ' + '; '.join(errors))
    directory.mkdir(parents=True, exist_ok=False)
    # An exported graph must fail closed in the original GUI without its host
    # action registry. Otherwise inline NormalSteps with no action default True.
    exported = {**document, 'inline_steps': [s for s in steps if s['step_id'] not in actions],
                'required_host_steps': [s for s in steps if s['step_id'] in actions]}
    (directory / 'case.yaml').write_text(yaml.safe_dump(exported, allow_unicode=True, sort_keys=False), encoding='utf-8')
    lock = threading.RLock()
    values, completed = {}, []
    report = {'schema': 'testfix.wctp-case.v1', 'title': title, 'status': 'running', 'created_at': now(),
        'iterations': iterations, 'completed_iterations': 0, 'events': [], 'results': [],
        'case': document, 'engine': 'wctp.GraphExecutor.execute',
        'native_step_execution': True, 'source_root': str(runner.source_root),
        'source_sha256': dict(SOURCE_SHA256), 'source_copied': False}
    started = time.monotonic()
    context = core['engine.context'].ExecutionContext(document['case_id'], None)

    def save():
        write_json(directory / 'report.json', report)
        if callback:
            callback(report)

    def bind(key, spec):
        def action(ctx, **params):
            value = spec[1]()
            with lock:
                values[key] = value
            success = bool(spec[2](value))
            return success, {key + '_result': value}
        registry.get(key)._fn = action

    for key, spec in actions.items():
        bind(key, spec)

    def emit(event, step_id, result=None):
        with lock:
            record = {'event': event, 'step_id': step_id, 'time': now(),
                      'iteration': int(context.get_variable('_loop_count_step_Loop', 0))}
            if event == 'finished':
                item = asdict(result)
                item['iteration'] = record['iteration']
                item['control'] = step_id == 'step_Loop'
                report['results'].append(item)
                if step_id == 'step_Join' and result.status:
                    completed.append(record['iteration'])
                    report['completed_iterations'] = len(completed)
                record.update(status=result.status, error=result.error, duration=result.duration)
            elif event == 'loop_iteration':
                record['progress'] = result
            report['events'].append(record)
            save()

    save()
    try:
        native = core['engine.graph_executor'].GraphExecutor(graph, registry, event_callback=emit)
        results = native.execute(context, timeout=0)
        success = (len(completed) == iterations and any(r.step_id == 'step_End' and r.status for r in results)
                   and all(r.status or r.step_id == 'step_Loop' for r in results) and not any(r.error for r in results))
        report['status'] = 'passed' if success else 'failed'
        if not success:
            report['error'] = next((r.error or r.step_id + ': expected result not met' for r in results
                                    if r.error or not r.status and r.step_id != 'step_Loop'), 'WCTP stopped before its EndStep')
    except Exception as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        report.update(finished_at=now(), duration_s=round(time.monotonic() - started, 3))
        save()
    return values, report


def successful(value):
    return value.get('successful') is True


def functional_failure(value):
    from .project_eval import _functional_failure
    required = {'timeout', 'parse_error', 'collection_errors', 'duplicate_case_ids', 'total', 'cases'}
    return required <= value.keys() and _functional_failure(value)


def delivery_success(value):
    checks = value.get('http', value.get('cli'))
    if checks is not None and 'build' in value:
        return value['build'].get('passed') is True and checks.get('successful') is True
    return value.get('passed') is True


def run_test_actions(directory, operation, actions):
    target = directory / 'wctp'
    if target.exists():
        target = directory / ('wctp-' + uuid.uuid4().hex)
    values, report = execute_case(target, actions, title=operation)
    return values, {'engine': report['engine'], 'status': report['status'],
                    'native_step_execution': True, 'source_sha256': report['source_sha256'],
                    'report_path': str(target / 'report.json'),
                    'case_path': str(target / 'case.yaml')}
