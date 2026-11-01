"""Host-owned task/worker/strategy assignment with reactive CP-SAT scheduling.

All tasks remain mandatory. Estimates are declared allowances, not measured
quality or latency. A solution selects actual logical workers for WCTP calls;
the runtime re-solves at tool barriers rather than waiting for predicted times.
"""
from __future__ import annotations

from copy import deepcopy
import time

from ortools.sat.python import cp_model

from .team_planning import TeamPlanError, _graph, _coverage, graph_metrics, priority_ready

WORKER_CAPABILITIES = {
    'software': {'software'},
    'frontend': {'software', 'frontend', 'javascript'},
    'javascript': {'software', 'javascript', 'frontend'},
    'threejs': {'software', 'frontend', 'javascript', 'threejs'},
    'backend': {'software', 'backend', 'database'},
    'database': {'software', 'database'},
    'integration': {'software', 'frontend', 'javascript', 'threejs', 'backend', 'database', 'integration'},
    'mcu': {'software', 'mcu', 'protocol'},
    'mpu': {'software', 'mpu', 'protocol', 'backend', 'database'},
    'protocol': {'software', 'mcu', 'mpu', 'protocol'},
}


def compile_options(plan, routings, *, model_slots, max_steps, stages=()):
    """Build candidates from trusted source routing, never LLM-granted Skills."""
    _graph(plan)
    if type(model_slots) is not int or not 1 <= model_slots <= 8:
        raise TeamPlanError('Invalid worker model capacity')
    if type(max_steps) is not int or not 1 <= max_steps <= 32:
        raise TeamPlanError('Invalid employee step allowance')
    bounds = {stage['id']: stage for stage in stages}
    choices = {}
    for task in plan['work_items']:
        routing = routings[task['id']]
        if routing['status'] != 'ready':
            raise TeamPlanError('Specialist assignment ' + routing['status'] + ': ' + task['id'])
        required = {entry['id'] for entry in routing['specialists']}
        if not required:
            required = {'software'}
        stage = bounds.get(task['id'], {})
        duration = stage.get('base_duration_seconds',stage.get('duration_seconds', 180))
        tokens = stage.get('base_token_cap',stage.get('token_cap', 24000))
        candidates = []
        for role, capabilities in WORKER_CAPABILITIES.items():
            if role not in required and role != 'integration' and not (role == 'software' and required == {'software'}):
                continue
            if not required <= capabilities:
                continue
            for slot in range(model_slots):
                for strategy, steps in [('standard', max_steps), *([('focused', 3)] if
                        task['skill_id'] == 'explore-repository' and len(task['paths']) == 1 and max_steps > 3 else [])]:
                    candidates.append({'worker_id': f'{role}#{slot+1}', 'role': role,
                        'strategy': strategy, 'max_steps': steps,
                        'duration_seconds': max(1, duration * steps // max_steps),
                        'token_cap': max(1, tokens * steps // max_steps),
                        'fit_penalty': len(capabilities - required),
                        'specialist_ids': [entry['id'] for entry in routing['specialists']]})
        if not candidates:
            raise TeamPlanError('No capable employee for ' + task['id'])
        choices[task['id']] = candidates
    return choices


def plan_team_execution(workspace, plan, *, wall_seconds, token_limit, model_slots, max_steps=5):
    """Use joint feasibility at the controller boundary, not a fixed v1 veto."""
    from .specialists import route_task, registered_requirements
    from .project_tools import project_profile
    from .team_planning import _priority_topological_order
    sources = workspace.snapshot()['sources']
    routes = {task['id']:route_task(task,sources,project_profile(workspace.project),
                required_specialists=registered_requirements(workspace.project,task),
                allowed_new_paths=workspace.allowed) for task in plan['work_items']}
    options = compile_options(plan,routes,model_slots=model_slots,max_steps=max_steps)
    solved = solve_joint_plan(plan,options,wall_seconds=wall_seconds,token_limit=token_limit,model_slots=model_slots)
    metrics = graph_metrics(plan)
    order = _priority_topological_order(_graph(plan),metrics) if solved['status'] in {'OPTIMAL','FEASIBLE'} else []
    return {'schema_version':'testfix.team-integer-plan.v2','status':solved['status'],'order':order,
            'solver':{'name':'OR-Tools CP-SAT','joint_assignment':True},'initial_assignment':solved,
            'graph_metrics':metrics,'budget':solved['budget'],'execution_verified':False,
            'stages':[{**next(t for t in plan['work_items'] if t['id']==key),**solved['assignments'][key],
                       'base_duration_seconds':180,'base_token_cap':24000} for key in order]}


def solve_joint_plan(plan, options, *, wall_seconds, token_limit, model_slots,
                     completed=(), active=None, solver_seconds=2.0):
    """Assign all unfinished tasks, including precedence and ownership locks.

    active maps task IDs to already pinned worker/strategy selections. The next
    dispatch contains only ready zero-start tasks. No worker assignment grants
    file or tool authority; the existing host independently enforces both.
    """
    by_id = _graph(plan)
    _coverage(plan, set(plan['request_assessment']['matched_requirement_ids']))
    for name, value in [('wall_seconds', wall_seconds), ('token_limit', token_limit), ('model_slots', model_slots)]:
        if type(value) is not int or not 0 <= value <= 10**9:
            raise TeamPlanError('Invalid joint budget: ' + name)
    if not 1 <= model_slots <= 8 or type(solver_seconds) not in (int,float) or not 0 < solver_seconds <= 30:
        raise TeamPlanError('Invalid joint solver capacity or time limit')
    completed, active = set(completed), active or {}
    if completed - set(by_id) or set(active) - (set(by_id) - completed):
        raise TeamPlanError('Unknown or completed active task')
    remaining = {key: task for key, task in by_id.items() if key not in completed}
    result = {'schema': 'testfix.joint-assignment.v1', 'status': 'INFEASIBLE', 'assignments': {},
              'dispatch': [], 'completed_dependencies': sorted(completed), 'active': sorted(active),
              'basis': 'host_declared_allowances_and_capability_matrix; not_measured_performance',
              'policy': 'all_tasks_mandatory; reactive_resolve_at_tool_barriers',
              'budget': {'wall_seconds': wall_seconds, 'token_limit': token_limit, 'model_slots': model_slots},
              'solver': 'OR-Tools CP-SAT', 'blocked_reasons': []}
    if not remaining:
        result['status'] = 'OPTIMAL'
        return result
    candidates = {}
    for identity in remaining:
        values = deepcopy(options.get(identity, []))
        for option in values:
            if (not isinstance(option.get('worker_id'), str) or not option['worker_id'] or
                not isinstance(option.get('strategy'), str) or not option['strategy'] or
                any(type(option.get(k)) is not int or not 1 <= option[k] <= 10**7
                    for k in ('duration_seconds', 'token_cap', 'max_steps')) or
                type(option.get('fit_penalty', 0)) is not int or not 0 <= option.get('fit_penalty', 0) <= 100):
                raise TeamPlanError('Invalid host worker option for ' + identity)
        if len({(o['worker_id'],o['strategy']) for o in values}) != len(values):
            raise TeamPlanError('Duplicate host worker option')
        if identity in active:
            pinned = active[identity]
            values = [o for o in values if all(o[k] == pinned[k] for k in ('worker_id','strategy'))]
        if not values:
            result['blocked_reasons'].append('No eligible worker/strategy for ' + identity)
        candidates[identity] = values
    if result['blocked_reasons']:
        return result
    horizon = min(wall_seconds, sum(max(o['duration_seconds'] for o in values) for values in candidates.values()))
    if horizon < 1:
        result['blocked_reasons'] = ['No remaining wall-time allowance']
        return result
    model, starts, ends, chosen, intervals, worker_intervals = cp_model.CpModel(), {}, {}, {}, {}, {}
    token_terms, fit_terms = [], []
    for identity, values in candidates.items():
        starts[identity] = model.new_int_var(0,horizon,'start:'+identity)
        ends[identity] = model.new_int_var(0,horizon,'end:'+identity)
        duration = []
        for index, option in enumerate(values):
            flag = model.new_bool_var(f'assign:{identity}:{index}')
            chosen[identity,index] = flag
            interval = model.new_optional_interval_var(starts[identity], option['duration_seconds'], ends[identity], flag, f'work:{identity}:{index}')
            worker_intervals.setdefault(option['worker_id'], []).append(interval)
            duration.append(option['duration_seconds']*flag)
            token_terms.append(option['token_cap']*flag)
            fit_terms.append((option['token_cap'] + option.get('fit_penalty',0)*10000 + index)*flag)
        model.add_exactly_one([chosen[identity,j] for j in range(len(values))])
        model.add(ends[identity] == starts[identity]+sum(duration))
        size = model.new_int_var(1,horizon,'duration:'+identity)
        model.add(size == sum(duration))
        task_interval = model.new_interval_var(starts[identity], size, ends[identity], 'task:'+identity)
        intervals[identity] = task_interval
        if identity in active:
            model.add(starts[identity] == 0)
    # Precedence is added only after every interval variable exists.
    for identity, task in remaining.items():
        for predecessor in task['depends_on']:
            if predecessor not in completed:
                model.add(starts[identity] >= ends[predecessor])
    for group in worker_intervals.values():
        model.add_no_overlap(group)
    model.add_cumulative(list(intervals.values()), [1]*len(intervals), model_slots)
    writers = [key for key, task in remaining.items() if task['skill_id'] == 'repair-code']
    for i,left in enumerate(writers):
        for right in writers[i+1:]:
            if set(remaining[left]['paths']) & set(remaining[right]['paths']):
                model.add_no_overlap([intervals[left],intervals[right]])
    model.add(sum(token_terms) <= token_limit)
    makespan = model.new_int_var(0,horizon,'makespan')
    model.add_max_equality(makespan,list(ends.values()))
    ranked = priority_ready(list(remaining), graph_metrics(plan))
    priority_delay = sum((len(ranked)-i)*starts[key] for i,key in enumerate(ranked))
    solver, deadline = cp_model.CpSolver(), time.monotonic()+solver_seconds
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    phases, best = [], None
    for objective in (makespan, priority_delay, sum(fit_terms)):
        model.minimize(objective)
        solver.parameters.max_time_in_seconds = max(.001, deadline-time.monotonic())
        code = solver.solve(model)
        status = solver.status_name(code)
        phases.append(status)
        if status not in {'OPTIMAL','FEASIBLE'}:
            if status == 'UNKNOWN' and best is not None:
                break  # The previous validated solution still satisfies the fixed optima.
            result['status'] = status
            result['blocked_reasons'] = ['No executable joint plan within dependency/resource/token/time constraints']
            return result
        best = {'assignments':{},'estimates':{'makespan_seconds':solver.value(makespan),'tokens':solver.value(sum(token_terms))}}
        for identity, values in candidates.items():
            picked = next(option for index,option in enumerate(values) if solver.value(chosen[identity,index]))
            best['assignments'][identity] = {**picked,'start_seconds':solver.value(starts[identity]),'end_seconds':solver.value(ends[identity])}
        if status != 'OPTIMAL' or time.monotonic() >= deadline:
            break
        model.add(objective == solver.value(objective))
    result.update(status='OPTIMAL' if all(s=='OPTIMAL' for s in phases) and len(phases)==3 else 'FEASIBLE',
                  objective_order=['makespan','ready_priority_delay','declared_token_and_fit_cost'], phases=phases,
                  **best)
    ready = [key for key,task in remaining.items() if key not in active and set(task['depends_on']) <= completed
             and result['assignments'][key]['start_seconds']==0]
    result['dispatch'] = priority_ready(ready,graph_metrics(plan))
    return result
