"""Read-only, bounded projections of persisted experiment evidence for the lab."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

CAPABILITIES = [
    ('plan-workflow', '总控规划'), ('explore-repository', '仓库探索'),
    ('ground-code', '代码定位'), ('analyze-requirements', '需求分析'),
    ('generate-tests', '生成测试'), ('diagnose-results', '失败诊断'),
    ('repair-code', '修复代码'), ('review-patch', '独立评审'),
    ('verify-delivery', '独立验收'),
]
IDENTIFIER = re.compile(r'[a-f0-9]{32}')


def _text(value, limit=2000):
    return str(value)[:limit] if value is not None else ''


def public_source(source):
    if isinstance(source, str):
        return {'description': source[:1000]}
    if not isinstance(source, dict):
        return {}
    return {key: source[key] for key in ('repository', 'commit', 'version', 'license',
        'issue_url', 'description', 'name', 'url', 'archive_sha256') if key in source}


def _artifact(settings, run_id, value, subtree, *, digest=None, limit=64_000, as_bytes=False):
    """A report path is data, never permission to read another local file."""
    if not IDENTIFIER.fullmatch(run_id) or not isinstance(value, str):
        raise ValueError('Invalid evidence reference')
    allowed = (settings.artifacts / run_id / subtree).resolve()
    original = Path(value)
    original = original if original.is_absolute() else settings.root / original
    path = original.resolve()
    if not path.is_relative_to(allowed) or not path.is_file() or original.is_symlink():
        raise ValueError('Evidence reference leaves the allowed artifact directory')
    if path.stat().st_size > limit:
        raise ValueError('Evidence file exceeds its display limit')
    data = path.read_bytes()
    if digest and hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('Evidence receipt changed after recording')
    return data if as_bytes else data.decode('utf-8', errors='replace')


def _suite(value):
    if not isinstance(value, dict) or 'total' not in value:
        return {'passed': None, 'total': None, 'successful': None, 'cases': []}
    result = {**{key: value.get(key) for key in ('passed', 'total', 'successful',
        'exit_code', 'timeout', 'collection_errors', 'runnable', 'duration_s')},
        'cases': [{key: item.get(key) for key in ('id', 'status')} for item in value.get('cases', [])[:300]]}
    if 'successful' not in value and 'passed' in value and value.get('total', 0) > 0:
        result['successful'] = value['passed'] == value['total'] and value.get('exit_code') == 0 and not value.get('timeout')
    return result


def _skill_summary(identifier):
    if identifier not in {item[0] for item in CAPABILITIES}:
        return {}
    path = Path(__file__).with_name('skill_library')/identifier/'references/contract.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    return {key: value.get(key) for key in ('id', 'version', 'kind', 'role', 'profiles', 'inputs', 'outputs', 'reads', 'writes', 'prerequisites', 'budgets')}


def _overlap(spans, ids):
    events = []
    for span in spans:
        if span.get('step_id') not in ids:
            continue
        start, end = span.get('started_monotonic_seconds'), span.get('finished_monotonic_seconds')
        if (isinstance(start, (int, float)) and isinstance(end, (int, float))
                and math.isfinite(start) and math.isfinite(end) and end >= start):
            events.extend([(start, 1), (end, -1)])
    active = peak = 0
    previous = overlap = 0.0
    for moment, delta in sorted(events):
        if active >= 2:
            overlap += moment - previous
        active += delta
        peak = max(peak, active)
        previous = moment
    return round(overlap, 6), peak


def _postchecks(settings, run_id, candidate):
    """Append-only human/evaluator evidence, bound to this exact candidate."""
    path = settings.artifacts / run_id / 'postchecks.json'
    if not path.exists():
        return None
    try:
        raw = _artifact(settings, run_id, str(path), '', limit=64_000, as_bytes=True)
        value = json.loads(raw)
        if (not isinstance(value, dict) or value.get('schema') != 'testfix.postchecks.v1' or value.get('run_id') != run_id
                or not isinstance(candidate, str) or not re.fullmatch(r'[a-f0-9]{64}', candidate)
                or value.get('candidate_sha256') != candidate):
            raise ValueError('Post-check receipt does not match this candidate')
        checks = value.get('checks')
        if not isinstance(checks, list) or not 1 <= len(checks) <= 30:
            raise ValueError('Invalid post-check list')
        public = []
        for item in checks:
            if not isinstance(item, dict) or item.get('status') not in {'passed', 'failed'}:
                raise ValueError('Post-check has no explicit result')
            if any(type(item.get(key)) is not int for key in ('baseline_exit', 'candidate_exit')):
                raise ValueError('Post-check has no concrete process exits')
            public.append({key: item.get(key) if key in {'baseline_exit', 'candidate_exit'}
                else _text(item.get(key), 2000) for key in ('id', 'requirement_id', 'description',
                'baseline_exit', 'candidate_exit', 'expected', 'actual', 'status')})
        result = {'status': 'failed' if any(item['status'] == 'failed' for item in public) else 'passed',
            'candidate_sha256': candidate, 'checks': public,
            'receipt_sha256': hashlib.sha256(raw).hexdigest(),
            'created_at': _text(value.get('created_at'), 100)}
        if isinstance(value.get('followup_run_id'), str) and IDENTIFIER.fullmatch(value['followup_run_id']):
            result['followup_run_id'] = value['followup_run_id']
        return result
    except (OSError, ValueError, TypeError) as exc:
        return {'status': 'unverified', 'checks': [], 'note': _text(exc)}


def project_run(settings, run):
    """No request contexts, reference patches, hidden sources or raw responses."""
    run_id = run.get('id', '')
    if not IDENTIFIER.fullmatch(run_id):
        raise ValueError('Invalid run identifier')
    task = next(iter(run.get('tasks', [])), {})
    collab = run.get('collaboration') or {}
    capabilities = run.get('workflow', {}).get('capabilities', [])
    states = {item['skill_id']: item for item in capabilities if 'skill_id' in item}
    is_project = run.get('kind') == 'autonomous_project'
    graph_nodes, edges = [], []
    if is_project:
        for index, (key, label) in enumerate(CAPABILITIES):
            item = states.get(key, {})
            graph_nodes.append({'id': key, 'label': label, 'role': key,
                'kind': 'controller' if key == 'plan-workflow' and collab else 'capability',
                'status': item.get('status', 'pending'), 'in_degree': int(index > 0),
                'out_degree': int(index < len(CAPABILITIES) - 1),
                'depends_on': [CAPABILITIES[index-1][0]] if index else [],
                'detail': {key: item.get(key) for key in ('attempt', 'started_at', 'finished_at', 'duration_s', 'timing_basis')}})
            if index:
                edges.append({'source': CAPABILITIES[index-1][0], 'target': key, 'kind': 'capability_flow'})
    else:
        # Function benchmarks retain their own measured events rather than
        # inventing nine completed capabilities from a successful summary.
        roles = list(dict.fromkeys(event.get('agent') for event in run.get('events', []) if event.get('agent')))
        for role in roles:
            graph_nodes.append({'id': role, 'label': role, 'role': role, 'kind': 'capability',
                'status': 'observed', 'in_degree': None, 'out_degree': None,
                'depends_on': [], 'detail': {'note': '角色出现在函数任务事件中，未推断额外依赖。'}})
    agents, tools, overlaps, peaks, warnings = [], [], [], [], []
    rounds = collab.get('rounds', [])
    if not rounds and collab.get('plans'):
        rounds = [collab['plans'][-1]]
    for number, round_value in enumerate(rounds, 1):
        round_number = round_value.get('round', number)
        plan = round_value.get('plan', {}).get('work_items', [])
        metrics = round_value.get('schedule', {}).get('graph_metrics', {})
        actual = {item.get('task_id'): item for item in round_value.get('employees', [])}
        identifiers = {item['id'] for item in plan}
        for item in plan:
            employee = actual.get(item['id'], {})
            node_id = f"employee:{round_number}:{item['id']}"
            summary = {'id': node_id, 'task_id': item['id'], 'round': round_number,
                'label': _text(item.get('title', item['id']), 120), 'role': item.get('skill_id'),
                'kind': 'employee', 'status': employee.get('status', 'pending'),
                'depends_on': [f'employee:{round_number}:{dep}' for dep in item.get('depends_on', [])],
                'task_dependencies': item.get('depends_on', []), 'paths': item.get('paths', []),
                'in_degree': metrics.get(item['id'], {}).get('in_degree', len(item.get('depends_on', []))),
                'out_degree': metrics.get(item['id'], {}).get('out_degree', sum(item['id'] in x.get('depends_on', []) for x in plan)),
                'detail': {'title': item.get('title'), 'paths': item.get('paths', []),
                    'skill_id': item.get('skill_id'), 'requirement_ids': item.get('requirement_ids', []),
                    'graph_metrics': metrics.get(item['id'], {}), 'steps': []}}
            for step in employee.get('steps', []):
                observed = {'action': step.get('action'), 'ok': step.get('ok'), 'sha256': step.get('sha256')}
                try:
                    if not isinstance(step.get('sha256'), str) or not re.fullmatch(r'[a-fA-F0-9]{64}', step['sha256']):
                        raise ValueError('Employee receipt has no valid SHA-256')
                    saved = json.loads(_artifact(settings, run_id, step.get('path'), 'team',
                        digest=step.get('sha256'), limit=512_000))
                    action = saved.get('action', {})
                    observed.update({key: _text(action.get(key), 1200) for key in ('path', 'query', 'message')})
                    observed['candidate_before'] = saved.get('candidate_before')
                    observed['candidate_after'] = saved.get('candidate_after')
                    observation = saved.get('observation', {})
                    if not isinstance(observation, dict):
                        raise ValueError('Employee observation is not a structured host receipt')
                    if isinstance(observation.get('ok'), bool):
                        observed['ok'] = observation['ok']
                    if observation.get('ok') is False:
                        observed['error'] = _text(observation.get('error'), 1500)
                    observed['receipt_verified'] = True
                except (OSError, ValueError, TypeError) as exc:
                    observed.update(receipt_verified=False, error=_text(exc))
                    warnings.append('员工步骤回执不可复核：' + _text(item['id'], 100))
                summary['detail']['steps'].append(observed)
                tools.append({'employee_id': node_id, **observed})
            graph_nodes.append(summary)
            agents.append(summary)
            if item.get('depends_on'):
                edges.extend({'source': f'employee:{round_number}:{dep}', 'target': node_id, 'kind': 'dependency'}
                             for dep in item['depends_on'])
            else:
                edges.append({'source': 'repair-code', 'target': node_id, 'kind': 'delegation'})
        for batch in round_value.get('batches', []):
            overlap, peak = _overlap(batch.get('action_spans', []), identifiers)
            if overlap:
                overlaps.append(overlap)
            if peak:
                peaks.append(peak)
    calls = [{key: call.get(key) for key in ('invocation_id', 'skill_id', 'provider',
        'requested_model', 'actual_model', 'model_evidence', 'status', 'duration_s', 'usage')}
        for call in run.get('model_calls', [])]
    budget = run.get('workflow', {}).get('budget', run.get('budget', {}))
    suites = []
    if task:
        for key, label, before, after in [
            ('generated', '模型生成测试', 'generated_baseline', 'generated'),
            ('independent', '冻结独立验收', 'baseline', 'acceptance'),
            ('regression', '原有与冻结行为回归', 'regression_baseline', 'regression'),
            ('build', 'wheel 构建', '_none', 'build'),
            ('runtime', '安装后 CLI / HTTP 检查', '_none', 'extended'),
        ]:
            suites.append({'id': key, 'label': label, 'before': _suite(task.get(before)), 'after': _suite(task.get(after))})
    evidence = []
    # Regression includes frozen evaluator tests; its pytest traceback may
    # contain their source. Only generated-test logs are public here.
    for key, label in [('generated_baseline', '原版生成测试日志'), ('generated', '候选生成测试日志')]:
        value = task.get(key, {})
        for stream in ('stdout', 'stderr'):
            if not value.get(stream + '_path'):
                continue
            try:
                output = _artifact(settings, run_id, value[stream + '_path'], 'tool_calls', limit=256_000)
                output = output.replace(str(settings.root), '<workspace>')
                evidence.append({'label': label + ' ' + stream, 'text': output[-12_000:]})
            except (ValueError, OSError):
                evidence.append({'label': label + ' ' + stream, 'text': '原始日志当前不可读取；可下载原报告复核。'})
    if run.get('provider') == 'replay':
        warnings.append('这是离线回放流程验证，没有发起真实模型调用。')
    if calls and not any(call.get('actual_model') for call in calls):
        warnings.append('服务未回报实际模型型号；请求参数不等于已核实的服务端型号。')
    postchecks = _postchecks(settings, run_id, task.get('candidate_sha256'))
    if postchecks and postchecks['status'] == 'failed':
        warnings.append('原注册验收通过后，追加独立检查发现兼容性回归；请查看追加检查，不能据原分数宣称完整正确。')
    elif postchecks and postchecks['status'] == 'unverified':
        warnings.append('追加检查回执当前无法与本次候选绑定核验。')
    warnings.extend(['3D 图展示记录中的状态，不作为正确性证明。',
                     '有限测试只覆盖注册需求和输入域；人工探针不改写原验收分数。'])
    can_probe = bool(run.get('project_id') == 'Project/rich-cli' and run.get('status') == 'completed'
        and task.get('status') == 'accepted' and task.get('build', {}).get('sha256'))
    for node in graph_nodes:
        node['detail']['current_skill_contract'] = _skill_summary(node.get('role'))
    return {'run': {**{key: run.get(key) for key in ('id', 'status', 'kind', 'provider', 'created_at',
            'project_id', 'model', 'summary', 'collaboration_request', 'error')}, 'can_probe': can_probe},
        'source': public_source(task.get('source', run.get('source', {}))),
        'graph': {'nodes': graph_nodes, 'edges': edges}, 'capabilities': capabilities,
        'agents': agents, 'tools': tools,
        'metrics': {'suites': suites, 'model_calls': len(calls),
            'reported_tokens': budget.get('reported_tokens'),
            'peak_concurrency': max(peaks) if peaks else None,
            'overlap_seconds': round(sum(overlaps), 6), 'overlap_batches': overlaps,
            'elapsed_seconds': run.get('summary', {}).get('duration_s')},
        'budget': budget, 'model_calls': calls, 'events': run.get('events', [])[-250:],
        'requirements': task.get('requirements', []),
        'test_steps': task.get('test_steps', []), 'coverage': task.get('requirement_coverage', []),
        'diff': _text(task.get('diff'), 200_000), 'verdict': task.get('verdict', {}), 'postchecks': postchecks,
        'evidence': evidence, 'downloads': {'json': f'/api/runs/{run_id}/report?format=json',
            'md': f'/api/runs/{run_id}/report?format=md',
            'zip': f'/api/project-runs/{run_id}/download' if run.get('download') else None},
        'warnings': list(dict.fromkeys(warnings))}


def bootstrap(settings, runs):
    from .project_models import project_provider_status
    from .project_tools import project_catalog
    from .project_workflow import default_budget
    from .benchmark_data import list_benchmark_datasets
    projects = []
    for item in project_catalog(settings):
        request = ('请补全 Flaskr 博客的文章详情、按标题搜索和每页五篇分页；保留注册登录与文章增删改。'
                   '请总控按依赖分工，员工读取当前代码、根据真实测试修改，最终通过独立验收、原有回归、构建和 HTTP 验证。')
        example = settings.root / 'examples/rich-cli-parallel-request.txt'
        if item['id'] == 'Project/rich-cli' and example.is_file():
            request = example.read_text(encoding='utf-8')[:20_000]
        projects.append({'id': item['id'], 'title': item['title'], 'adapter': item['adapter'],
            'source': public_source(item['source']), 'default_budget': default_budget(item['adapter']),
            'default_request': request})
    index = sorted(runs, key=lambda item: item.get('created_at', ''), reverse=True)
    return {'projects': projects, 'providers': project_provider_status(settings),
        'defaults': {'project_id': 'Project/rich-cli' if any(x['id'] == 'Project/rich-cli' for x in projects) else 'Project/flaskr',
            'provider': 'codex_cli', 'model': 'gpt-5.6-sol', 'max_rounds': 2,
            'budget': {'wall_seconds': 7200, 'token_limit': 1_000_000, 'model_slots': 2, 'cpu_slots': 2},
            'run_id': next((item['id'] for item in index if item.get('project_id') == 'Project/rich-cli'
                           and item.get('status') == 'completed' and item.get('collaboration')), None)},
        'datasets': list_benchmark_datasets(settings),
        'runs': [{key: item.get(key) for key in ('id', 'status', 'kind', 'project_id', 'created_at', 'provider', 'summary')} for item in index],
        'guide_url': '/api/lab/guide'}
