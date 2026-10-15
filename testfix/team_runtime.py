"""Host-mediated, Skill-scoped employee loops inside the existing delivery gates.

The model proposes JSON actions; only the host touches the sealed workspace.
Task completion is an employee handoff, never an acceptance verdict.
"""
from __future__ import annotations

import hashlib
import json

from .project_tools import confined


def _object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties),
            'additionalProperties': False}


ACTION_SCHEMA = _object({
    'action': {'type': 'string', 'enum': ['read_file', 'search', 'run_public_tests', 'apply_patch', 'finish']},
    'path': {'type': 'string'}, 'query': {'type': 'string'},
    'start_line': {'type': 'integer', 'minimum': 1, 'maximum': 100000},
    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
    'baseline_sha256': {'type': 'string'},
    'files': {'type': 'array', 'items': _object({'path': {'type': 'string'}, 'source': {'type': 'string'}})},
    'message': {'type': 'string', 'minLength': 1, 'maxLength': 3000},
})

AGENT_LOOP_INSTRUCTIONS = (
    'You are one employee assigned by the manager. Return an action matching the supplied schema. '
    'Every field is required; use empty strings and files=[] for unused fields, start_line=1, limit=80. '
    'The host, not the model, executes tools and returns observed results on the next turn. '
    'If observations is empty your first action MUST be read_file or search, even when source text '
    'was supplied in context. An earlier context snapshot is not a host tool observation. '
    'read_file(path,start_line,limit) reads only this task\'s public files; '
    'search(query) is a bounded literal search over those files. '
    'Only repair-code employees can request run_public_tests or apply_patch. '
    'apply_patch requires the exact CURRENT candidate_sha256 and nonempty full file sources '
    'within assigned paths; it completes this implementation handoff. '
    'finish is for exploration findings only. No arbitrary command, network, hidden test access, '
    'test change, delegation permission or scope expansion exists. '
    'Use dependency_outputs and observed tool results to cooperate. A reported finding or completed '
    'handoff does not prove that requirements or independent acceptance have passed.'
)


def verify_team_evidence(ledger, workspace):
    """A handoff references immutable, run-local tool receipts."""
    expected = workspace.directory / 'team'
    seen = set()
    for revision in ledger.get('rounds', []):
        for employee in revision['employees']:
            for receipt in employee['steps']:
                name = receipt['path']
                path = confined(workspace.settings.root, name, exists=True)
                if not path.resolve().is_relative_to(expected.resolve()) or name in seen:
                    raise ValueError('Employee evidence escaped or duplicated its run-local receipt')
                seen.add(name)
                if hashlib.sha256(path.read_bytes()).hexdigest() != receipt['sha256']:
                    raise ValueError('Employee tool evidence changed after handoff')


def execute_team_round(workspace, plan, schedule, *, call, public_test, emit,
                       ledger, round_number, max_steps=5, model_slots=None, wctp_root=None):
    """Execute bounded parallel employee decisions through the real WCTP backend.

    Model decisions overlap. Host tools and commits execute at barriers, with
    file ownership and optimistic read-set checks before disjoint patch merges.
    """
    from .team_planning import priority_ready
    from .wctp_runtime import run_wctp_batch
    if schedule.get('status') not in {'OPTIMAL', 'FEASIBLE'}:
        raise ValueError('No executable employee task schedule')
    tasks = {item['id']: item for item in plan['work_items']}
    order = schedule.get('order', [])
    if len(order) != len(tasks) or set(order) != set(tasks):
        raise ValueError('Employee schedule omitted or duplicated tasks')
    slots = schedule.get('budget', {}).get('model_slots', 1) if model_slots is None else model_slots
    if type(slots) is not int or not 1 <= slots <= 8:
        raise ValueError('Employee concurrency must be an integer from 1 to 8')
    outputs, touched, workers = {}, set(), {}
    initial_hash = workspace.candidate_hash()
    revision = {'round': round_number, 'candidate_before': initial_hash,
                'plan': plan, 'schedule': schedule, 'employees': [], 'messages': [],
                'execution': 'wctp_parallel_model_batches_serial_host_commits',
                'model_slots': slots, 'batches': [], 'dispatches': []}
    ledger.setdefault('rounds', []).append(revision)
    location = workspace.directory / 'team' / f'round-{round_number}'
    location.mkdir(parents=True, exist_ok=False)

    def message(sender, recipient, kind, content):
        revision['messages'].append({'sender': sender, 'recipient': recipient, 'kind': kind,
                                     'candidate_sha256': workspace.candidate_hash(), 'content': content})

    def conflicts(item, active):
        return item['skill_id'] == 'repair-code' and any(
            tasks[key]['skill_id'] == 'repair-code' and set(item['paths']) & set(tasks[key]['paths'])
            for key in active)

    while len(outputs) < len(tasks):
        workspace.assert_integrity()
        verify_team_evidence(ledger, workspace)
        active = [key for key, worker in workers.items() if worker['employee']['status'] == 'running']
        ready = [key for key, item in tasks.items() if key not in workers
                 and set(item['depends_on']) <= set(outputs)]
        ranked = priority_ready(ready, plan)
        started = []
        for identifier in ranked:
            if len(active) >= slots:
                break
            assigned = tasks[identifier]
            if conflicts(assigned, active):
                continue
            employee = {'task_id': identifier, 'agent_id': assigned['skill_id'] + '/' + identifier,
                        'skill_id': assigned['skill_id'], 'status': 'running', 'steps': []}
            revision['employees'].append(employee)
            workers[identifier] = {'employee': employee, 'observations': [], 'read_observed': False}
            message('manager', employee['agent_id'], 'assignment', assigned)
            emit(assigned['skill_id'], 'employee_assigned', '总控按出度优先分配任务：' + assigned['title'])
            active.append(identifier)
            started.append(identifier)
        if not active:
            raise ValueError('Employee graph is blocked by unresolved dependencies or write ownership')
        batch_order = priority_ready(active, plan)
        revision['dispatches'].append({'ready_candidates': ranked, 'started': started,
                                       'active': batch_order, 'completed_dependencies': sorted(outputs)})
        actions, snapshots = {}, {}
        for identifier in batch_order:
            assigned, worker = tasks[identifier], workers[identifier]
            if len(worker['employee']['steps']) >= max_steps:
                worker['employee']['status'] = 'incomplete'
                raise ValueError('Employee exhausted bounded action loop: ' + identifier)
            before = workspace.candidate_hash()
            read_set = {name: workspace.candidate_seal.get(name) for name in assigned['paths']}
            snapshots[identifier] = {'candidate_sha256': before, 'read_set': read_set}
            context = {'agent_loop_instructions': AGENT_LOOP_INSTRUCTIONS, 'assigned_task': assigned,
                       'dependency_outputs': {key: outputs[key] for key in assigned['depends_on']},
                       'observations': worker['observations'],
                       'employee_steps_remaining': max_steps - len(worker['employee']['steps']),
                       'manager_request': ledger['request'], 'business_round': round_number}
            def decide(item=assigned, public_context=context):
                return call(item['skill_id'], public_context, ACTION_SCHEMA)
            actions[identifier] = decide
        # The external GraphExecutor owns real parallel workers. No candidate
        # writes occur while these decisions are in flight.
        try:
            decisions = run_wctp_batch(actions, source_root=wctp_root, max_workers=slots)
        except Exception as exc:
            metadata = getattr(exc, 'metadata', None)
            if metadata is not None:
                revision['batches'].append(metadata)
            revision['status'] = 'employee_execution_failed'
            raise
        revision['batches'].append(decisions.metadata)
        workspace.assert_integrity()
        verify_team_evidence(ledger, workspace)
        for identifier in batch_order:
            assigned, worker = tasks[identifier], workers[identifier]
            employee, skill = worker['employee'], assigned['skill_id']
            action = decisions[identifier]
            from .project_models import _validate_schema
            _validate_schema(action, ACTION_SCHEMA)
            operation = action['action']
            before = workspace.candidate_hash()
            proposed = snapshots[identifier]
            message(employee['agent_id'], 'host', 'tool_request', action)
            try:
                sources = workspace.snapshot()['sources']
                readable = {name: sources[name] for name in assigned['paths'] if name in sources}
                if operation == 'read_file':
                    if action['path'] not in readable:
                        raise ValueError('Employee read is outside assigned public file scope')
                    lines = readable[action['path']].splitlines()
                    start, limit = action['start_line'] - 1, action['limit']
                    observed = {'path': action['path'], 'start_line': start + 1,
                                'text': '\n'.join(lines[start:start + limit])[:12000],
                                'total_lines': len(lines), 'candidate_sha256': before}
                    worker['read_observed'] = True
                elif operation == 'search':
                    query = action['query']
                    if not query or len(query) > 200:
                        raise ValueError('Search needs a bounded nonempty literal query')
                    hits = []
                    for name, text in readable.items():
                        for number, line in enumerate(text.splitlines(), 1):
                            if query in line and len(hits) < action['limit']:
                                hits.append({'path': name, 'line': number, 'text': line[:500]})
                    observed = {'query': query, 'hits': hits, 'candidate_sha256': before}
                    worker['read_observed'] = True
                elif operation == 'run_public_tests':
                    if skill != 'repair-code':
                        raise ValueError('Exploration Skill cannot execute test tools')
                    observed = public_test()
                elif operation == 'apply_patch':
                    if skill != 'repair-code' or not worker['read_observed']:
                        raise ValueError('Patch requires repair Skill and observed code information')
                    if not {item['path'] for item in action['files']} <= set(assigned['paths']):
                        raise ValueError('Patch exceeded manager-assigned file ownership')
                    if action['baseline_sha256'] != proposed['candidate_sha256']:
                        raise ValueError('Patch proposal uses a stale candidate version')
                    changed_reads = [name for name, digest in proposed['read_set'].items()
                                     if workspace.candidate_seal.get(name) != digest]
                    if changed_reads:
                        worker['read_observed'] = False
                        raise ValueError('Concurrent patch read-set changed; refresh source before retrying')
                    patch = {'baseline_sha256': before, 'files': action['files'], 'reason': action['message']}
                    observed = workspace.apply_patch(patch)
                    observed['merge'] = {'proposal_baseline_sha256': action['baseline_sha256'],
                                         'commit_baseline_sha256': before,
                                         'rebased_disjoint_patch': before != action['baseline_sha256'],
                                         'read_preconditions': proposed['read_set']}
                    touched.update(item['path'] for item in action['files'])
                    employee['status'] = 'handed_off'
                elif operation == 'finish':
                    if skill != 'explore-repository' or not worker['read_observed']:
                        raise ValueError('Only an informed explorer can finish without a patch')
                    observed = {'reported_findings': action['message'], 'verified_implementation': False,
                                'candidate_sha256': before}
                    employee['status'] = 'handed_off'
                else:
                    raise ValueError('Unknown employee tool')
                result = {'ok': True, 'result': observed}
            except (ValueError, SyntaxError) as exc:
                result = {'ok': False, 'error': str(exc)}
            workspace.assert_integrity()
            step = len(employee['steps']) + 1
            record = {'task_id': identifier, 'step': step, 'skill_id': skill, 'action': action,
                      'inference_candidate_sha256': proposed['candidate_sha256'],
                      'candidate_before': before, 'candidate_after': workspace.candidate_hash(),
                      'observation': result}
            target = location / f'{identifier}-step-{step}.json'
            target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            employee['steps'].append({'path': target.relative_to(workspace.settings.root).as_posix(),
                                      'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                                      'action': operation, 'ok': result['ok']})
            worker['observations'].append({'action': operation, **result})
            message('host', employee['agent_id'], 'observation', result)
            emit(skill, 'employee_tool', identifier + ': ' + operation + (' 完成' if result['ok'] else ' 被拒绝'))
            if employee['status'] == 'handed_off':
                outputs[identifier] = {'agent_id': employee['agent_id'], 'message': action['message'],
                                       'candidate_sha256': workspace.candidate_hash(), 'evidence': employee['steps']}
                message(employee['agent_id'], 'manager', 'handoff', outputs[identifier])
    if not touched:
        raise ValueError('Employee graph produced no implementation patch')
    workspace.assert_integrity()
    verify_team_evidence(ledger, workspace)
    files = [{'path': name, 'source': confined(workspace.candidate, name, exists=True).read_text(encoding='utf-8')}
             for name in sorted(touched)]
    output = {'baseline_sha256': initial_hash, 'files': files,
              'reason': 'WCTP parallel employee handoffs with degree priority and checked host commits'}
    patch = {'candidate_sha256': workspace.candidate_hash(), 'changed_files': sorted(touched),
             'hashes': {name: workspace.candidate_seal[name] for name in sorted(touched)}}
    revision.update(candidate_after=workspace.candidate_hash(), handoffs=outputs, status='awaiting_review_and_verification')
    return output, patch
