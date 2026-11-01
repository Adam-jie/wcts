"""Bridge execution-guided search to real employee tools and public test suites."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from .project_tools import confined
from .repair_search import public_evaluation, relocate_public_identities, search_forest
from .team_planning import team_plan_schema, validate_team_plan
from .team_assignment import plan_team_execution
from .team_runtime import execute_team_round, verify_team_evidence, MANAGER_GATE_SCHEMA


def run_repair_forest(workspace, initial_plan, baseline, repair_context, *, config,
                      request, call, public_test, emit, remaining, token_remaining,
                      model_slots, publish, round_number=1, employee_budget=None):
    """Each expanded node owns a full isolated registered workspace.

    The only test callback is candidate_public. No independent suite, result or
    canonical solution enters selection, feedback, reward or model context.
    """
    from .project_workflow import CAPABILITY_NAMES, SCHEMAS, _public_feedback, obj
    folder = workspace.directory / 'search'
    folder.mkdir(parents=True, exist_ok=True)
    cache, branches = {}, {}
    before = workspace.candidate_hash()
    root_feedback = _public_feedback(baseline)
    manager_schema = obj({**SCHEMAS['plan-workflow']['properties'], 'team_plan': team_plan_schema()})
    evidence_by_id = {}

    def expand(parent, context):
        remaining()
        branch = parent.fork_candidate()
        identity = context['node_id']
        branches[identity] = branch
        ledger = {'request': request, 'plans': [], 'rounds': []}
        evidence = {'branch_run_id': branch.directory.name, 'parent_candidate_sha256': parent.candidate_hash(),
                    'public_result': None, 'employee_ledger': None, 'cached_public_result': False}
        emit('repair-code', 'search_expanding', f"搜索 {identity}：树 {context['tree'] + 1}，深度 {context['depth']}，隔离候选工作区")
        try:
            plan = deepcopy(initial_plan)
            manager = call(branch, 'plan-workflow', {
                'required_skill_ids': list(CAPABILITY_NAMES), 'search_context': context,
                'manager_feedback': context['parent_feedback'], 'previous_work_items': initial_plan['work_items'],
                'planning_guidance': 'Core exploration, grounding, requirements analysis and test generation are already complete. '
                    'Assign focused implementation employees from current evidence. Avoid redundant exploration tasks when '
                    'their answers are already present. Split backend and templates when their file ownership permits it; '
                    'preserve explicit interface handoffs and all registered requirements.'}, manager_schema)
            if (manager['skill_ids'] != list(CAPABILITY_NAMES)
                    or {item['requirement_id'] for item in manager['goals']} != set(context_ids)):
                raise ValueError('Search manager omitted core capabilities or requirements')
            plan = manager['team_plan']
            plan = validate_team_plan(plan, context_ids, branch.allowed, branch.snapshot()['sources'])
            schedule = plan_team_execution(branch,plan, wall_seconds=max(1, int(remaining())),
                                       token_limit=token_remaining(), model_slots=model_slots)
            ledger['plans'].append({'round': 1, 'plan': plan, 'schedule': schedule})
            local_context = {**repair_context, 'public_test_results': context['parent_feedback'],
                             'search_context': context}
            if context['depth'] > 1:
                diagnosis = call(branch, 'diagnose-results', {
                    'requirement_matrix': repair_context['requirement_matrix'],
                    'public_test_results': context['parent_feedback'], 'search_context': context}, SCHEMAS['diagnose-results'])
                if any(item['requirement_id'] not in context_ids or not set(item['target_paths']) <= branch.allowed
                       for item in diagnosis['failures']):
                    raise ValueError('Search diagnosis exceeded registered requirements or file scope')
                local_context['diagnosis_report'] = diagnosis

            def employee_call(skill, extra, schema):
                return call(branch, skill, {**(local_context if skill == 'repair-code' else {}),
                                             'search_context': context, **extra}, schema)

            def observed_public():
                result = public_test(branch)
                return _public_feedback(result)

            def manager_check(assigned, handoff, observations):
                return call(branch, 'plan-workflow', {
                    'manager_gate': {'task': assigned, 'handoff': handoff, 'observations': observations},
                    'business_round': round_number, 'search_context': context,
                    'control_instructions': 'Review this handoff against its assigned task, public requirements, '
                        'observed tools and current candidate before dependent tasks start. Identify specific '
                        'missing work; do not require a partial task to complete the whole feature. '
                        'A no-patch handoff may be sufficient when supported by observed code; '
                        'verified_implementation=false means only that independent verification has not run. '
                        'The missing array contains only issues blocking this handoff; if ready is true it must be empty. '
                        'Mention later tests in reason only. Approval does not imply tests or independent acceptance passed.'}, MANAGER_GATE_SCHEMA)

            execute_team_round(branch, plan, schedule, call=employee_call, public_test=observed_public,
                               emit=emit, telemetry=emit, ledger=ledger, round_number=1, model_slots=model_slots,
                               manager_check=manager_check,
                               remaining_budget=lambda: employee_budget(branch) if employee_budget else
                                   {'wall_seconds':max(1,int(remaining())),'token_limit':token_remaining()})
            branch.assert_integrity()
            verify_team_evidence(ledger, branch)
            digest = branch.candidate_hash()
            if digest in cache:
                public, evidence_branch = cache[digest]
                evidence['cached_public_result'] = True
            else:
                public = public_test(branch)
                if public['candidate_sha256'] != digest:
                    raise ValueError('Search public evidence uses a different candidate')
                evidence_branch = branch
                cache[digest] = (public, branch)
            if branch.generated_seal != workspace.generated_seal or evidence_branch.generated_seal != workspace.generated_seal:
                raise ValueError('Search test relocation requires identical frozen suite bytes')
            original_prefix = '.'.join(workspace.generated.relative_to(workspace.settings.root).parts) + '.'
            branch_prefix = '.'.join(evidence_branch.generated.relative_to(workspace.settings.root).parts) + '.'
            normalized = relocate_public_identities(public, original_prefix=original_prefix, branch_prefix=branch_prefix)
            evaluation = public_evaluation(baseline, normalized)
            evidence['identity_relocation'] = {'from_prefix':branch_prefix,'to_prefix':original_prefix,
                                               'frozen_generated_hashes':workspace.generated_seal}
            feedback = _public_feedback(public)
            evidence['public_result'] = public
        except Exception as exc:
            # Resource failures must stop, never masquerade as a bad code branch.
            from .project_workflow import BudgetExceeded
            cursor, chain = exc, set()
            while cursor is not None and id(cursor) not in chain:
                chain.add(id(cursor))
                if isinstance(cursor, BudgetExceeded):
                    raise
                cursor = cursor.__cause__ or cursor.__context__
            remaining()
            evaluation = {'reward': 0., 'eligible': False, 'public_pass': False,
                          'basis': 'branch_execution_failed_without_test_success'}
            feedback = {'error_type': type(exc).__name__, 'message': str(exc)[:2000]}
            evidence['execution_error'] = feedback
            digest = branch.candidate_hash()
        branch.assert_integrity()
        ledger_path = folder / (identity + '-employees.json')
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding='utf-8')
        evidence['employee_ledger'] = {'path': str(ledger_path.relative_to(workspace.settings.root)),
                                      'sha256': hashlib.sha256(ledger_path.read_bytes()).hexdigest()}
        evidence['work_items'] = plan['work_items']
        evidence['employees'] = [{key: item[key] for key in ('task_id', 'agent_id', 'skill_id', 'status', 'specialist_ids')}
                                 for revision in ledger['rounds'] for item in revision['employees']]
        evidence_by_id[identity] = evidence
        emit('repair-code', 'search_evaluated', f"{identity}：公开测试评价 {evaluation['reward']:.3f}，回归可接受 {evaluation['eligible']}")
        return {'state': branch, 'candidate_sha256': digest, 'evaluation': evaluation,
                'feedback': feedback, 'evidence': evidence}

    context_ids = [item['id'] for item in workspace.project['requirements']]
    selected, report = search_forest(workspace, root_feedback, expand, config=config, publish=publish)
    if selected is None:
        raise ValueError('Search produced no candidate preserving the frozen public regression')
    chosen = selected['state']
    chosen.assert_integrity()
    for identity, evidence in evidence_by_id.items():
        receipt = evidence['employee_ledger']
        path = workspace.settings.root / receipt['path']
        if hashlib.sha256(path.read_bytes()).hexdigest() != receipt['sha256']:
            raise ValueError('Search employee handoff evidence changed')
        verify_team_evidence(json.loads(path.read_text(encoding='utf-8')), branches[identity])
    changes = [name for name, digest in chosen.candidate_seal.items() if workspace.candidate_seal.get(name) != digest]
    output = {'baseline_sha256': before, 'files': [
        {'path': name, 'source': confined(chosen.candidate, name, exists=True).read_text(encoding='utf-8')} for name in changes],
        'reason': 'Selected isolated candidate using frozen public tests and UCT forest; independent verification remains pending'}
    patch = workspace.promote_candidate(chosen, expected_sha256=before)
    report['promoted_candidate_sha256'] = workspace.candidate_hash()
    report['selected_branch_run_id'] = chosen.directory.name
    publish(report)
    return output, patch, report
