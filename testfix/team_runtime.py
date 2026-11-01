"""Host-mediated, Skill-scoped employee loops inside the existing delivery gates.

The model proposes JSON actions; only the host touches the sealed workspace.
Task completion is an employee handoff, never an acceptance verdict.
"""
from __future__ import annotations

import hashlib
import json

from .project_tools import confined, project_profile


def _object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties),
            'additionalProperties': False}


ACTION_SCHEMA = _object({
    'action': {'type': 'string', 'enum': ['read_file', 'search', 'run_public_tests', 'apply_patch', 'apply_edit', 'finish']},
    'path': {'type': 'string'}, 'query': {'type': 'string'},
    'start_line': {'type': 'integer', 'minimum': 1, 'maximum': 100000},
    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
    'baseline_sha256': {'type': 'string'},
    'files': {'type': 'array', 'items': _object({'path': {'type': 'string'}, 'source': {'type': 'string'}})},
    'message': {'type': 'string', 'minLength': 1, 'maxLength': 3000},
})

MANAGER_GATE_SCHEMA = _object({
    'ready': {'type': 'boolean'},
    'reason': {'type': 'string', 'minLength': 1, 'maxLength': 3000},
    'missing': {'type': 'array', 'items': {'type': 'string'}},
})


class TeamReplanRequired(ValueError):
    def __init__(self, task, review, handoff, observations):
        super().__init__('Manager requested task reassignment: ' + task['id'])
        self.task = task
        self.review = review
        self.handoff = handoff
        self.observations = observations

AGENT_LOOP_INSTRUCTIONS = (
    'You are one employee assigned by the manager. Return an action matching the supplied schema. '
    'Every field is required; use empty strings and files=[] for unused fields, start_line=1, limit=80. '
    'The host, not the model, executes tools and returns observed results on the next turn. '
    'If observations is empty your first action MUST be read_file or search, even when source text '
    'was supplied in context. An earlier context snapshot is not a host tool observation. '
    'read_file(path,start_line,limit) reads only this task\'s public files; '
    'search(query) is a bounded literal search over those files; use newline-separated queries '
    'to search for several different symbols in one action. '
    'Only repair-code employees can request run_public_tests, apply_patch or apply_edit. '
    'apply_patch requires the exact CURRENT candidate_sha256 and nonempty full file sources '
    'within assigned paths. apply_edit uses the same hash, query as exact nonempty old text and '
    'one files entry containing its path and replacement source. The old text must match once. '
    'Either operation completes this implementation handoff. '
    'finish may report exploration findings or a repair investigation that needs no additional patch after '
    'reading code. Report which tests have not run; the manager reviews the handoff and the host later runs '
    'the required public and independent tests. No arbitrary command, network, hidden test access, '
    'test change, delegation permission or scope expansion exists. '
    'When source_context says indexed_excerpts, the context contains partial files; use read_file or search '
    'for exact source before editing. Otherwise use complete source snapshots already in context; '
    'When employee_steps_remaining is 1, do not start another investigation: finish with the '
    'observed findings and unresolved questions, or submit an evidence-backed patch. '
    'do not repeat identical reads or search ranges. Read an existing assigned file before creating an absent file. '
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
                       ledger, round_number, max_steps=5, model_slots=None, wctp_root=None, telemetry=None,
                       remaining_budget=None, manager_check=None, replan_on_rejected_handoff=False):
    """Execute bounded parallel employee decisions through the real WCTP backend.

    Model decisions overlap. Host tools and commits execute at barriers, with
    file ownership and optimistic read-set checks before disjoint patch merges.
    """
    from .team_planning import priority_ready
    from .wctp_runtime import run_wctp_batch
    from .specialists import SpecialistCatalog, registered_requirements, route_task
    from .team_assignment import compile_options, solve_joint_plan
    import time
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
    started_at = time.monotonic()
    initial_hash = workspace.candidate_hash()
    revision = {'round': round_number, 'candidate_before': initial_hash,
                'plan': plan, 'schedule': schedule, 'employees': [], 'messages': [],
                'execution': 'wctp_parallel_model_batches_serial_host_commits',
                'model_slots': slots, 'batches': [], 'dispatches': [], 'allocation_plans': []}
    if manager_check is not None:
        revision['handoff_gate'] = 'codeagent_review'
    ledger.setdefault('rounds', []).append(revision)
    specialist_catalog = SpecialistCatalog()
    profile = project_profile(workspace.project)
    revision['specialist_catalog'] = specialist_catalog.snapshot()
    revision['specialist_assignments'] = {}

    def specialization(assigned):
        routing = route_task(assigned, workspace.snapshot()['sources'], profile,
                             required_specialists=registered_requirements(workspace.project, assigned),
                             allowed_new_paths=workspace.allowed,
                             catalog=specialist_catalog)
        revision['specialist_assignments'][assigned['id']] = routing
        if routing['status'] != 'ready':
            revision['status'] = 'specialist_' + routing['status']
            raise ValueError('Specialist assignment ' + routing['status'] + ': ' + assigned['id']
                             + '; missing=' + ','.join(routing['missing_capabilities'])
                             + '; unknown=' + ','.join(routing['unknown_source_types']))
        return routing

    # Check every implementation task before any employee inference or write.
    for assigned in tasks.values():
        specialization(assigned)
    revision['task_graph'] = {'schema':'testfix.feature-task-graph.v1',
        'feature_groups':{requirement['id']:[item['id'] for item in tasks.values()
                          if requirement['id'] in item['requirement_ids']]
                          for requirement in workspace.project['requirements']},
        'tasks':{key:{**item,'kind':'analysis' if item['skill_id']=='explore-repository' else 'implementation',
                      'required_specialists':[s['id'] for s in revision['specialist_assignments'][key]['specialists']],
                      'acceptance':[r['description'] for r in workspace.project['requirements']
                                    if r['id'] in item['requirement_ids'] and 'description' in r],
                      'status':'pending','input_tasks':item['depends_on'],'output_receipts':[]}
                 for key,item in tasks.items()}}
    location = workspace.directory / 'team' / f'round-{round_number}'
    location.mkdir(parents=True, exist_ok=False)

    def message(sender, recipient, kind, content):
        from .reports import now
        revision['messages'].append({'sender': sender, 'recipient': recipient, 'kind': kind,
                                     'time': now(), 'round': round_number,
                                     'candidate_sha256': workspace.candidate_hash(), 'content': content})

    def publish(skill, stage, text, **details):
        if telemetry is None:
            emit(skill, stage, text)
        else:
            telemetry(skill, stage, text, round=round_number, **details)

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
        routes = {key: specialization(item) for key,item in tasks.items() if key not in outputs}
        choices = compile_options({'request_assessment':plan['request_assessment'],
                                   'work_items':plan['work_items']},
                                  {**revision['specialist_assignments'], **routes},
                                  model_slots=slots, max_steps=max_steps, stages=schedule.get('stages', []))
        estimates = schedule.get('budget', {})
        limits = remaining_budget() if remaining_budget else {
            'wall_seconds': max(0, int(estimates.get('wall_seconds', 1000000) - (time.monotonic()-started_at))),
            'token_limit': estimates.get('token_limit', 1000000000)}
        for key in active:
            worker = workers[key]
            charged = limits.get('task_accounted_tokens',{}).get(key,0)
            if type(charged) is not int or charged<0:
                raise ValueError('Invalid host employee token accounting')
            original = worker['assignment']
            residual_time = max(1,int(original['duration_seconds']-(time.monotonic()-worker['started_at'])))
            residual_tokens = max(1,original['token_cap']-charged)
            choices[key] = [{**o,'duration_seconds':residual_time,'token_cap':residual_tokens} for o in choices[key]]
        allocation = solve_joint_plan(plan, choices, wall_seconds=limits['wall_seconds'],
            token_limit=limits['token_limit'], model_slots=slots, completed=outputs,
            active={key:workers[key]['assignment'] for key in active})
        allocation['id'] = f'round-{round_number}-allocation-{len(revision["allocation_plans"])+1}'
        allocation['accounting'] = {'active_task_accounted_tokens':{
            key:limits.get('task_accounted_tokens',{}).get(key,0) for key in active},
            'basis':'host_reconciled_charges_and_monotonic_elapsed_time; residual_active_allowances'}
        revision['allocation_plans'].append(allocation)
        if allocation['status'] not in {'OPTIMAL','FEASIBLE'}:
            revision['status'] = 'allocation_blocked'
            raise ValueError('Joint task/employee assignment is ' + allocation['status'])
        if telemetry is not None:
            publish('plan-workflow','allocation_solved','主控完成任务、专业员工与策略联合分配：'+allocation['status'],
                    assignment_id=allocation['id'])
        started = []
        for identifier in allocation['dispatch']:
            if len(active) >= slots:
                break
            assigned = tasks[identifier]
            if conflicts(assigned, active):
                continue
            routing = specialization(assigned)
            assignment = allocation['assignments'][identifier]
            employee = {'task_id': identifier, 'agent_id': routing['agent_id'],
                        'specialist_ids': [item['id'] for item in routing['specialists']],
                        'skill_id': assigned['skill_id'], 'status': 'running', 'steps': [],
                        'worker_id': assignment['worker_id'], 'worker_role': assignment['role'],
                        'execution_strategy': assignment['strategy'], 'max_steps': assignment['max_steps'],
                        'assignment_id': allocation['id']}
            revision['employees'].append(employee)
            revision['task_graph']['tasks'][identifier].update(status='running',worker_id=assignment['worker_id'],
                                                               assignment_id=allocation['id'])
            workers[identifier] = {'employee': employee, 'observations': [], 'read_observed': False,
                                   'assignment': assignment,'started_at':time.monotonic()}
            message('manager', employee['agent_id'], 'assignment', assigned)
            publish(assigned['skill_id'], 'employee_assigned', '主控联合分配任务：' + assigned['title'],
                    task_id=identifier, sender='manager', recipient=employee['agent_id'],
                    worker_id=assignment['worker_id'], worker_role=assignment['role'],
                    strategy=assignment['strategy'], requirement_ids=assigned['requirement_ids'],
                    depends_on=assigned['depends_on'], paths=assigned['paths'], assignment_id=allocation['id'])
            active.append(identifier)
            started.append(identifier)
        if not active:
            raise ValueError('Employee graph is blocked by unresolved dependencies or write ownership')
        batch_order = priority_ready(active, plan)
        revision['dispatches'].append({'ready_candidates': ranked, 'started': started,
                                       'active': batch_order, 'completed_dependencies': sorted(outputs),
                                       'assignment_id': allocation['id']})
        actions, snapshots = {}, {}
        for identifier in batch_order:
            assigned, worker = tasks[identifier], workers[identifier]
            if len(worker['employee']['steps']) >= worker['employee']['max_steps']:
                worker['employee']['status'] = 'incomplete'
                raise ValueError('Employee exhausted bounded action loop: ' + identifier)
            before = workspace.candidate_hash()
            read_set = {name: workspace.candidate_seal.get(name) for name in assigned['paths']}
            snapshots[identifier] = {'candidate_sha256': before, 'read_set': read_set}
            context = {'agent_loop_instructions': AGENT_LOOP_INSTRUCTIONS, 'assigned_task': assigned,
                       'specialist_assignment': specialization(assigned),
                       'dependency_outputs': {key: outputs[key] for key in assigned['depends_on']},
                       'observations': worker['observations'],
                       'selected_worker': worker['assignment'],
                       'employee_steps_remaining': worker['employee']['max_steps'] - len(worker['employee']['steps']),
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
                readable = {name: confined(workspace.candidate,name,exists=True).read_text(encoding='utf-8')
                            for name in assigned['paths'] if name in sources}
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
                    queries = [item for item in query.splitlines() if item]
                    if not queries:
                        raise ValueError('Search needs a bounded nonempty literal query')
                    hits = []
                    for name, text in readable.items():
                        for number, line in enumerate(text.splitlines(), 1):
                            for term in queries:
                                if term in line and len(hits) < action['limit']:
                                    hit = {'path': name, 'line': number, 'text': line[:500]}
                                    if len(queries) > 1:
                                        hit['query'] = term
                                    hits.append(hit)
                    observed = {'query': query, 'hits': hits, 'candidate_sha256': before}
                    worker['read_observed'] = True
                elif operation == 'run_public_tests':
                    if skill != 'repair-code':
                        raise ValueError('Exploration Skill cannot execute test tools')
                    observed = public_test()
                elif operation in {'apply_patch', 'apply_edit'}:
                    if skill != 'repair-code' or not worker['read_observed']:
                        raise ValueError('Patch requires repair Skill and observed code information')
                    if not {item['path'] for item in action['files']} <= set(assigned['paths']):
                        raise ValueError('Patch exceeded manager-assigned file ownership')
                    if action['baseline_sha256'] != proposed['candidate_sha256']:
                        raise ValueError('Patch proposal uses a stale candidate version; copy the current candidate_sha256 exactly: '
                                         + proposed['candidate_sha256'])
                    changed_reads = [name for name, digest in proposed['read_set'].items()
                                     if workspace.candidate_seal.get(name) != digest]
                    if changed_reads:
                        worker['read_observed'] = False
                        raise ValueError('Concurrent patch read-set changed; refresh source before retrying')
                    proposed_files = action['files']
                    if operation == 'apply_edit':
                        if len(proposed_files) != 1 or not action['query']:
                            raise ValueError('An edit needs one file and nonempty exact old text')
                        item = proposed_files[0]
                        source = readable.get(item['path'])
                        if source is None or source.count(action['query']) != 1:
                            raise ValueError('Edit old text must match exactly once in the assigned file')
                        proposed_files = [{'path':item['path'], 'source':source.replace(action['query'],item['source'],1)}]
                    patch = {'baseline_sha256': before, 'files': proposed_files, 'reason': action['message']}
                    observed = workspace.apply_patch(patch)
                    observed['merge'] = {'proposal_baseline_sha256': action['baseline_sha256'],
                                         'commit_baseline_sha256': before,
                                         'rebased_disjoint_patch': before != action['baseline_sha256'],
                                         'read_preconditions': proposed['read_set']}
                    touched.update(item['path'] for item in proposed_files)
                    employee['status'] = 'awaiting_manager' if manager_check is not None else 'handed_off'
                elif operation == 'finish':
                    if not worker['read_observed']:
                        raise ValueError('Finishing without a patch requires observed code evidence')
                    observed = {'reported_findings': action['message'], 'verified_implementation': False,
                                'candidate_sha256': before}
                    employee['status'] = 'awaiting_manager' if manager_check is not None else 'handed_off'
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
            if employee['status'] == 'awaiting_manager':
                revision['task_graph']['tasks'][identifier]['status'] = 'awaiting_manager'
            worker['observations'].append({'action': operation, **result})
            message('host', employee['agent_id'], 'observation', result)
            publish(skill, 'employee_tool', identifier + ': ' + operation + (' 完成' if result['ok'] else ' 被拒绝'),
                    task_id=identifier, sender=employee['agent_id'], recipient='host', operation=operation, ok=result['ok'],
                    worker_id=employee['worker_id'], worker_role=employee['worker_role'])
            if employee['status'] in {'handed_off', 'awaiting_manager'}:
                handoff = {'agent_id': employee['agent_id'], 'message': action['message'],
                           'candidate_sha256': workspace.candidate_hash(), 'evidence': list(employee['steps'])}
                if manager_check is not None:
                    gate_observations = list(worker['observations'])
                    for correction in range(2):
                        review = manager_check(assigned, handoff, gate_observations)
                        _validate_schema(review, MANAGER_GATE_SCHEMA)
                        if not review['ready'] or not review['missing']:
                            break
                        if correction:
                            raise ValueError('Manager cannot approve a handoff with unresolved missing items')
                        gate_observations.append({'action': 'manager_output_validation', 'ok': False,
                            'result': 'Your approved response included missing items. List only issues that block this handoff; '
                                      'later testing and acceptance do not block a partial handoff. If ready is true, missing must be empty.'})
                    message('manager', employee['agent_id'], 'handoff_check', review)
                    employee.setdefault('manager_checks', []).append(review)
                    publish('plan-workflow', 'handoff_checked', identifier + ('：总控允许交接' if review['ready'] else '：总控要求补充'),
                            task_id=identifier, sender='manager', recipient=employee['agent_id'],
                            ok=review['ready'], summary=review['reason'][:1200])
                    if not review['ready']:
                        worker['observations'].append({'action': 'manager_check', 'ok': False, 'result': review})
                        if replan_on_rejected_handoff and (skill != 'explore-repository'
                                or len(employee['steps']) >= employee['max_steps']
                                or any(path in ' '.join([review['reason'], *review['missing']])
                                       for path in workspace.allowed - set(assigned['paths']))):
                            employee['status'] = 'replan_requested'
                            revision['task_graph']['tasks'][identifier]['status'] = 'replan_requested'
                            revision.update(status='replan_required', candidate_after=workspace.candidate_hash(),
                                            handoffs=dict(outputs), rejected_handoff=handoff,
                                            manager_feedback=review)
                            verify_team_evidence(ledger, workspace)
                            raise TeamReplanRequired(assigned, review, handoff, list(worker['observations']))
                        employee['status'] = 'running'
                        revision['task_graph']['tasks'][identifier]['status'] = 'revision_requested'
                        continue
                    handoff['manager_check'] = review
                    employee['status'] = 'handed_off'
                outputs[identifier] = handoff
                message(employee['agent_id'], 'manager', 'handoff', outputs[identifier])
                revision['task_graph']['tasks'][identifier].update(status='handed_off',
                    output_receipts=[receipt['sha256'] for receipt in employee['steps']])
                if telemetry is not None:
                    publish(skill, 'employee_handoff', identifier + '：产物已交回主控', task_id=identifier,
                            sender=employee['agent_id'], recipient='manager', summary=action['message'][:1200],
                            worker_id=employee['worker_id'], worker_role=employee['worker_role'],
                            artifact_ids=[receipt['sha256'] for receipt in employee['steps']])
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
