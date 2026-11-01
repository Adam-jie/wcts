"""Team controller boundaries with synthetic inference and business-tool outcomes.

These tests execute real ProjectWorkspace reads/writes, seals, hashes and CP-SAT.
They do not invoke a model, business pytest, HTTP server or an installed wheel;
their completed runs are unit fixtures, never an experiment success claim.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from test_project_workflow import (
    BoundaryProvider, BoundaryTools, CORE, PRIVATE_MARKER, harness,
)
from testfix import project_workflow as workflow
from testfix.project_models import _validate_schema, reject_protected_context
from testfix.team_runtime import ACTION_SCHEMA, MANAGER_GATE_SCHEMA, execute_team_round


def employee_action(operation, *, path='', baseline='', files=None, message='Synthetic employee observation'):
    return {'action': operation, 'path': path, 'query': '', 'start_line': 1, 'limit': 80,
            'baseline_sha256': baseline, 'files': files or [], 'message': message}


class TeamBoundaryProvider(BoundaryProvider):
    """Scripted employee choices and explicit non-real-model traces."""

    def __init__(self):
        super().__init__()
        self.attack = None
        self.unsupported = False
        self.planning_calls = 0
        self.employee_tests = False
        self.repeat_tests = False
        self.reject_first_handoff = False
        self.reject_all_handoffs = False
        self.inconsistent_first_handoff = False
        self.gate_calls = []

    def team_plan(self, context):
        number = context.get('business_round', 1)
        identify = f'inspect-{number}'
        repair = f'implement-{number}'
        ids = context['requirement_ids']
        return {
            'request_assessment': {
                'supported': not self.unsupported, 'matched_requirement_ids': ids,
                'uncovered_requests': ['Add unregistered network feature'] if self.unsupported else [],
                'questions': [],
            },
            'work_items': [
                {'id': identify, 'title': 'Inspect actual fixture value', 'skill_id': 'explore-repository',
                 'requirement_ids': ids, 'depends_on': [], 'paths': ['flaskr/app.py']},
                {'id': repair, 'title': 'Implement scoped fixture update', 'skill_id': 'repair-code',
                 'requirement_ids': ids, 'depends_on': [identify], 'paths': ['flaskr/app.py']},
            ],
        }

    def invoke(self, skill, context, schema, *, timeout_s, token_cap):
        if 'manager_gate' in context:
            assert skill == 'plan-workflow' and schema == MANAGER_GATE_SCHEMA
            reject_protected_context(context)
            task_id = context['manager_gate']['task']['id']
            self.gate_calls.append(task_id)
            rejected = self.reject_all_handoffs or self.reject_first_handoff and len(self.gate_calls) == 1
            output = {'ready': not rejected, 'reason': 'Need a clearer handoff' if rejected else 'Evidence reviewed',
                      'missing': ['Clarify the observation'] if rejected else []}
            if self.inconsistent_first_handoff and len(self.gate_calls) == 1:
                output['missing'] = ['Later independent tests remain to be run']
            elif self.inconsistent_first_handoff and len(self.gate_calls) == 2:
                assert context['manager_gate']['observations'][-1]['action'] == 'manager_output_validation'
            self.calls.append({'skill_id': skill, 'provider': 'injected-unit-boundary-fixture',
                               'real_model_executed': False, 'synthetic': True,
                               'usage': {'total_tokens': self.reported_tokens}, 'status': 'completed',
                               'result': deepcopy(output)})
            return output
        if 'assigned_task' not in context:
            output = super().invoke(skill, context, workflow.SCHEMAS[skill],
                                    timeout_s=timeout_s, token_cap=token_cap)
            if skill == 'plan-workflow':
                self.planning_calls += 1
                output['team_plan'] = self.team_plan(context)
                _validate_schema(output, schema)
                self.calls[-1]['result'] = deepcopy(output)
            return output
        assert schema == ACTION_SCHEMA
        assert timeout_s > 0 and token_cap > 0
        reject_protected_context(context)
        assert PRIVATE_MARKER not in json.dumps(context, ensure_ascii=False)
        self.invocations.append({'skill': skill, 'context': deepcopy(context), 'token_cap': token_cap})
        observed = context['observations']
        if skill == 'explore-repository':
            if self.attack == 'hidden_read':
                action = employee_action('read_file', path='private/acceptance_tests/test_private.py')
            elif not observed:
                action = employee_action('read_file', path='flaskr/app.py')
            else:
                assert observed[0]['result']['text'].startswith('VALUE = ')
                action = employee_action('finish', message='Actual public fixture VALUE observed')
        else:
            assert skill == 'repair-code'
            assert context['dependency_outputs'], 'Repair must receive actual explorer handoff'
            if not observed:
                action = employee_action('read_file', path='flaskr/app.py')
            elif self.repeat_tests or self.employee_tests and len(observed) == 1:
                action = employee_action('run_public_tests')
            else:
                path = 'tests/test_original.py' if self.attack == 'outside_patch' else 'flaskr/app.py'
                baseline = '0' * 64 if self.attack == 'stale_hash' else context['candidate_sha256']
                action = employee_action('apply_patch', baseline=baseline,
                    files=[{'path': path, 'source': f"VALUE = {context['business_round']}\n"}],
                    message='Synthetic model proposes actual bounded workspace mutation')
        _validate_schema(action, schema)
        self.calls.append({'skill_id': skill, 'provider': 'injected-unit-boundary-fixture',
                           'real_model_executed': False, 'synthetic': True,
                           'usage': {'total_tokens': self.reported_tokens}, 'status': 'completed',
                           'result': deepcopy(action)})
        return action


class FailFirstPublicTools(BoundaryTools):
    """A scripted public assertion failure exercises manager reassignment."""

    def __call__(self, settings, workspace, operation, *, timeout_s, round_number=1):
        self.public_failure = 'generated' if operation == 'candidate_public' and round_number == 1 else None
        return super().__call__(settings, workspace, operation,
                                timeout_s=timeout_s, round_number=round_number)


class TamperTeamStepTools(BoundaryTools):
    """Modify only saved tool evidence after the completed employee handoff."""

    def __call__(self, settings, workspace, operation, *, timeout_s, round_number=1):
        result = super().__call__(settings, workspace, operation,
                                  timeout_s=timeout_s, round_number=round_number)
        if operation == 'candidate_public':
            path = next((workspace.directory / 'team').rglob('*-step-*.json'))
            path.write_bytes(path.read_bytes() + b'\n')
        return result


def execute_team(harness, *, provider=None, tools=None):
    provider = provider or TeamBoundaryProvider()
    tools = tools or BoundaryTools()
    harness.run.update(collaboration_request='Update the mini fixture value; preserve existing behavior.',
                       budget={**workflow.DEFAULT_BUDGET, 'wall_seconds': 7200})
    result = workflow.execute_project_run(harness.run, harness.settings, provider=provider, tool_runner=tools)
    return result, provider, tools


def step_documents(harness, result):
    records = []
    for revision in result['collaboration']['rounds']:
        for employee in revision['employees']:
            for entry in employee['steps']:
                path = harness.settings.root / entry['path']
                assert hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256']
                records.append(json.loads(path.read_text(encoding='utf-8')))
    return records


def test_manager_employee_handoffs_use_real_workspace_and_retain_nine_gates(harness):
    result, provider, tools = execute_team(harness)
    assert result['status'] == 'completed', result.get('error')
    assert result['provenance']['provider']['real_model_executed'] is False
    assert result['collaboration']['execution'] == 'wctp_parallel_model_batches_serial_host_commits'
    assert [item['skill_id'] for item in result['workflow']['capabilities']] == CORE
    assert all(item['status'] == 'completed' for item in result['workflow']['capabilities'])
    schedule = result['collaboration']['plans'][0]['schedule']
    assert schedule['solver']['name'] == 'OR-Tools CP-SAT'
    assert schedule['status'] in {'OPTIMAL', 'FEASIBLE'}
    assert schedule['order'] == ['inspect-1', 'implement-1']
    revision = result['collaboration']['rounds'][0]
    assert set(revision['handoffs']) == {'inspect-1', 'implement-1'}
    assert provider.gate_calls == ['inspect-1', 'implement-1']
    assert all(item['manager_check']['ready'] for item in revision['handoffs'].values())
    assert [item['kind'] for item in revision['messages']].count('assignment') == 2
    assert [item['kind'] for item in revision['messages']].count('handoff') == 2
    documents = step_documents(harness, result)
    assert [record['action']['action'] for record in documents] == ['read_file', 'finish', 'read_file', 'apply_patch']
    assert documents[0]['observation']['result']['text'] == 'VALUE = 0'
    assert documents[-1]['candidate_before'] != documents[-1]['candidate_after']
    assert all(record['observation']['ok'] for record in documents)
    with zipfile.ZipFile(harness.settings.root / result['download']['path']) as package:
        ledger = json.loads(package.read('provenance/manager-employees.json'))
        assert ledger == result['collaboration']
        for employee in revision['employees']:
            for step in employee['steps']:
                suffix = (harness.settings.root / Path(step['path'])).relative_to(
                    harness.settings.artifacts / result['id'] / 'team').as_posix()
                archived = package.read('evidence/team/' + suffix)
                assert hashlib.sha256(archived).hexdigest() == step['sha256']
    workspace = tools.workspaces[-1]
    workspace.assert_integrity()
    assert (workspace.candidate / 'flaskr/app.py').read_text(encoding='utf-8') == 'VALUE = 1\n'
    assert (workspace.baseline / 'flaskr/app.py').read_text(encoding='utf-8') == 'VALUE = 0\n'
    assert [operation for operation, _, _ in tools.calls] == ['baseline_public', 'candidate_public', 'verify']
    assert result['tasks'][0]['review']['candidate_sha256'] == workspace.candidate_hash()
    assert all(result['tasks'][0]['verdict'].values())
    employee_contexts = [item['context'] for item in provider.invocations if 'assigned_task' in item['context']]
    for context in employee_contexts:
        assert set(context['sources']) == set(context['assigned_task']['paths'])
        assert 'tests/test_original.py' not in context['sources']
        if context['assigned_task']['skill_id'] == 'explore-repository':
            assert not {'generated_tests', 'public_test_results', 'diagnosis_report'} & set(context)
    repair_context = next(context for context in employee_contexts if context['assigned_task']['id'] == 'implement-1')
    assert repair_context['dependency_outputs']['inspect-1']['message'] == 'Actual public fixture VALUE observed'


def test_manager_rejects_incomplete_handoff_before_downstream_assignment(harness):
    provider = TeamBoundaryProvider()
    provider.reject_first_handoff = True
    result, provider, _ = execute_team(harness, provider=provider)
    assert result['status'] == 'completed', result.get('error')
    assert provider.gate_calls == ['inspect-1', 'inspect-1', 'implement-1']
    revision = result['collaboration']['rounds'][0]
    messages = revision['messages']
    checks = [i for i, item in enumerate(messages) if item['kind'] == 'handoff_check']
    next_assignment = next(i for i, item in enumerate(messages)
                           if item['kind'] == 'assignment' and item['content']['id'] == 'implement-1')
    assert messages[checks[0]]['content']['ready'] is False
    assert messages[checks[1]]['content']['ready'] is True
    assert checks[1] < next_assignment
    observations = [item['context']['observations'] for item in provider.invocations
                    if item['context'].get('assigned_task', {}).get('id') == 'inspect-1']
    assert any(any(step['action'] == 'manager_check' for step in value) for value in observations)


def test_unresolved_manager_rejection_never_releases_downstream(harness):
    provider = TeamBoundaryProvider()
    provider.reject_all_handoffs = True
    result, provider, tools = execute_team(harness, provider=provider)
    assert result['status'] == 'failed'
    assert 'exhausted bounded action loop' in result['error']
    assert set(provider.gate_calls) == {'inspect-1'}
    assert all(item['context'].get('assigned_task', {}).get('id') != 'implement-1'
               for item in provider.invocations)
    assert all(operation != 'verify' for operation, _, _ in tools.calls)


def test_inconsistent_manager_output_is_corrected_before_handoff(harness):
    provider = TeamBoundaryProvider()
    provider.inconsistent_first_handoff = True
    result, provider, _ = execute_team(harness, provider=provider)
    assert result['status'] == 'completed', result.get('error')
    assert provider.gate_calls == ['inspect-1', 'inspect-1', 'implement-1']
    handoff = result['collaboration']['rounds'][0]['handoffs']['inspect-1']
    assert handoff['manager_check']['ready'] and not handoff['manager_check']['missing']


def test_manager_error_leaves_handoff_pending_and_blocks_dependents(harness):
    class UnavailableManager(TeamBoundaryProvider):
        def invoke(self, skill, context, schema, **kwargs):
            if 'manager_gate' in context:
                raise ValueError('manager service unavailable')
            return super().invoke(skill, context, schema, **kwargs)

    result, provider, _ = execute_team(harness, provider=UnavailableManager())
    assert result['status'] == 'failed'
    revision = result['collaboration']['rounds'][0]
    assert revision['employees'][0]['status'] == 'awaiting_manager'
    assert 'handoffs' not in revision
    assert all(item['context'].get('assigned_task', {}).get('id') != 'implement-1'
               for item in provider.invocations)


def test_unchanged_module_can_handoff_before_other_worker_patches(harness):
    class NoOpProvider(TeamBoundaryProvider):
        def team_plan(self, context):
            result = super().team_plan(context)
            result['work_items'].insert(1, {'id': 'api-check', 'title': 'Inspect API and preserve behavior',
                'skill_id': 'repair-code', 'requirement_ids': context['requirement_ids'],
                'depends_on': ['inspect-1'], 'paths': ['flaskr/app.py']})
            result['work_items'][-1]['depends_on'] = ['api-check']
            return result

        def invoke(self, skill, context, schema, **kwargs):
            if context.get('assigned_task', {}).get('id') != 'api-check':
                return super().invoke(skill, context, schema, **kwargs)
            action = employee_action('finish', message='No API change needed; tests have not run') if context['observations'] else employee_action('read_file', path='flaskr/app.py')
            self.calls.append({'skill_id': skill, 'provider': 'synthetic', 'status': 'completed',
                               'usage': {'total_tokens': 1}, 'result': deepcopy(action)})
            return action

    result, provider, tools = execute_team(harness, provider=NoOpProvider())
    assert result['status'] == 'completed', result.get('error')
    revision = result['collaboration']['rounds'][0]
    assert revision['handoffs']['api-check']['manager_check']['ready']
    assert [operation for operation, _, _ in tools.calls] == ['baseline_public', 'candidate_public', 'verify']


def test_employee_public_test_observation_returns_to_same_repair_agent(harness):
    provider = TeamBoundaryProvider()
    provider.employee_tests = True
    result, provider, tools = execute_team(harness, provider=provider)
    assert result['status'] == 'completed', result.get('error')
    documents = step_documents(harness, result)
    assert [item['action']['action'] for item in documents][-3:] == ['read_file', 'run_public_tests', 'apply_patch']
    assert [operation for operation, _, _ in tools.calls] == [
        'baseline_public', 'candidate_public', 'candidate_public', 'verify']
    repair_calls = [item for item in provider.invocations if item['skill'] == 'repair-code']
    assert repair_calls[-1]['context']['observations'][-1]['action'] == 'run_public_tests'
    assert repair_calls[-1]['context']['observations'][-1]['result']['generated']['successful'] is True


def test_integration_worker_can_report_no_further_patch_after_public_check(harness):
    class IntegrationProvider(TeamBoundaryProvider):
        def team_plan(self, context):
            result = super().team_plan(context)
            result['work_items'].append({'id': 'integration', 'title': 'Inspect integration',
                'skill_id': 'repair-code', 'requirement_ids': context['requirement_ids'],
                'depends_on': ['implement-1'], 'paths': ['flaskr/app.py']})
            return result

        def invoke(self, skill, context, schema, *, timeout_s, token_cap):
            if context.get('assigned_task', {}).get('id') != 'integration':
                return super().invoke(skill, context, schema, timeout_s=timeout_s, token_cap=token_cap)
            actions = [employee_action('read_file', path='flaskr/app.py'),
                       employee_action('run_public_tests'),
                       employee_action('finish', message='No additional patch needed')]
            action = actions[len(context['observations'])]
            self.invocations.append({'skill': skill, 'context': deepcopy(context), 'token_cap': token_cap})
            self.calls.append({'skill_id': skill, 'provider': 'injected-unit-boundary-fixture',
                'real_model_executed': False, 'synthetic': True, 'usage': {'total_tokens': self.reported_tokens},
                'status': 'completed', 'result': deepcopy(action)})
            return action

    result, provider, tools = execute_team(harness, provider=IntegrationProvider())
    assert result['status'] == 'completed', result.get('error')
    documents = step_documents(harness, result)
    steps = [item for item in documents if item['task_id'] == 'integration']
    assert [step['action']['action'] for step in steps] == ['read_file', 'run_public_tests', 'finish']
    assert steps[-1]['observation']['ok'] and not steps[-1]['observation']['result']['verified_implementation']


def test_employee_cannot_exceed_two_public_test_calls_or_skip_final_patch(harness):
    provider = TeamBoundaryProvider()
    provider.repeat_tests = True
    result, provider, tools = execute_team(harness, provider=provider)
    assert result['status'] == 'failed', result.get('error')
    assert 'exhausted bounded action loop' in result['error']
    assert [operation for operation, _, _ in tools.calls] == [
        'baseline_public', 'candidate_public', 'candidate_public']
    documents = step_documents(harness, result)
    rejected = [item for item in documents if not item['observation']['ok']]
    assert rejected and all('public-test tool budget exhausted' in item['observation']['error'] for item in rejected)
    assert result['summary']['accepted'] == 0 and not result.get('download')
    workspace = tools.workspaces[-1]
    assert (workspace.candidate / 'flaskr/app.py').read_text(encoding='utf-8') == 'VALUE = 0\n'
    workspace.assert_integrity()


def test_failed_public_check_replans_from_observed_handoffs_and_current_source(harness):
    result, provider, tools = execute_team(harness, tools=FailFirstPublicTools())
    assert result['status'] == 'completed', result.get('error')
    assert provider.planning_calls == 2
    assert [revision['round'] for revision in result['collaboration']['plans']] == [1, 2]
    assert [revision['schedule']['order'] for revision in result['collaboration']['plans']] == [
        ['inspect-1', 'implement-1'], ['inspect-2', 'implement-2']]
    planner_context = [item['context'] for item in provider.invocations if item['skill'] == 'plan-workflow'][1]
    assert planner_context['business_round'] == 2
    assert planner_context['remaining_skill_invocations']['plan-workflow'] > 0
    assert planner_context['manager_feedback']['generated']['successful'] is False
    assert set(planner_context['previous_employee_handoffs']) == {'inspect-1', 'implement-1'}
    assert planner_context['sources']['flaskr/app.py'] == 'VALUE = 1\n'
    assert planner_context['previous_work_items'][0]['id'] == 'inspect-1'
    assert (tools.workspaces[-1].candidate / 'flaskr/app.py').read_text(encoding='utf-8') == 'VALUE = 2\n'
    counts = Counter(item['skill'] for item in provider.invocations)
    assert counts['explore-repository'] == 5
    assert counts['repair-code'] == 4
    assert [operation for operation, _, _ in tools.calls] == [
        'baseline_public', 'candidate_public', 'candidate_public', 'verify']
    assert len(step_documents(harness, result)) == 8


def test_uncovered_user_request_cannot_start_employee_or_public_test(harness):
    provider = TeamBoundaryProvider()
    provider.unsupported = True
    result, provider, tools = execute_team(harness, provider=provider)
    assert result['status'] == 'failed'
    assert result['summary']['accepted'] == 0 and not result.get('download')
    assert 'unsupported or unresolved' in result['error']
    assert tools.calls == []
    assert all(item['skill'] == 'plan-workflow' for item in provider.invocations)
    assert result['collaboration']['rounds'] == []


def test_stale_patch_feedback_contains_current_candidate_hash(harness):
    provider = TeamBoundaryProvider()
    provider.attack = 'stale_hash'
    result, _, _ = execute_team(harness, provider=provider)
    assert result['status'] == 'failed'
    rejected = [item for item in step_documents(harness, result)
                if item['action']['action'] == 'apply_patch' and not item['observation']['ok']]
    assert rejected and rejected[0]['inference_candidate_sha256'] in rejected[0]['observation']['error']


@pytest.mark.parametrize(('attack', 'error_fragment'), [
    ('hidden_read', 'outside assigned public file scope'),
    ('outside_patch', 'exceeded manager-assigned file ownership'),
    ('stale_hash', 'stale candidate version'),
])
def test_rejected_employee_tools_never_change_protected_or_candidate_files(harness, attack, error_fragment):
    provider = TeamBoundaryProvider()
    provider.attack = attack
    result, provider, tools = execute_team(harness, provider=provider)
    assert result['status'] in {'failed', 'budget_exhausted'}, result.get('error')
    assert result['summary']['accepted'] == 0 and not result.get('download')
    assert all(operation != 'verify' for operation, _, _ in tools.calls)
    documents = step_documents(harness, result)
    rejected = [record for record in documents if not record['observation']['ok']]
    assert rejected and all(error_fragment in record['observation']['error'] for record in rejected)
    assert all(PRIVATE_MARKER not in json.dumps(item['context']) for item in provider.invocations)
    workspace = tools.workspaces[-1]
    workspace.assert_integrity()
    assert (workspace.candidate / 'flaskr/app.py').read_text(encoding='utf-8') == 'VALUE = 0\n'


def test_infeasible_controller_budget_performs_no_inference(harness):
    provider = TeamBoundaryProvider()
    harness.run.update(collaboration_request='Update the mini value',
                       budget={**workflow.DEFAULT_BUDGET, 'wall_seconds': 60})
    result = workflow.execute_project_run(harness.run, harness.settings, provider=provider, tool_runner=harness.tools)
    assert result['status'] == 'budget_exhausted'
    assert result['plan']['solver_status'] == 'INFEASIBLE'
    assert provider.calls == [] and harness.tools.calls == []
    assert result['summary']['accepted'] == 0 and not result.get('download')


def test_changed_employee_step_evidence_cannot_produce_accepted_archive(harness):
    result, provider, tools = execute_team(harness, tools=TamperTeamStepTools())
    assert result['status'] == 'failed', result.get('error')
    assert 'Employee tool evidence changed after handoff' in result['error']
    assert result['summary']['accepted'] == 0 and not result.get('download')
    assert not (harness.settings.artifacts / result['id'] / 'completed-project.zip').exists()
    assert all(operation != 'verify' for operation, _, _ in tools.calls)
    assert all(item['skill'] != 'review-patch' for item in provider.invocations)
    # Source integrity alone cannot establish the integrity of the observations.
    tools.workspaces[-1].assert_integrity()


def test_employee_step_limit_does_not_invent_completion(harness):
    # Obtain the real sealed workspace via one ordinary synthetic controller run.
    result = harness.execute()
    assert result['status'] == 'completed'
    workspace = harness.tools.workspaces[-1]
    plan = TeamBoundaryProvider().team_plan({'requirement_ids': ['MINI-VALUE']})
    calls = []
    ledger = {'request': 'Synthetic bounded employee loop', 'rounds': []}

    def keep_reading(skill, context, schema):
        calls.append((skill, deepcopy(context)))
        return employee_action('read_file', path='flaskr/app.py')

    before = workspace.candidate_hash()
    with pytest.raises(ValueError, match='exhausted bounded action loop'):
        execute_team_round(workspace, plan, {'status': 'OPTIMAL', 'order': ['inspect-1', 'implement-1']},
            call=keep_reading, public_test=lambda: pytest.fail('Explorer cannot run tests'),
            emit=lambda *args: None, ledger=ledger, round_number=9, max_steps=2)
    assert len(calls) == 2
    assert ledger['rounds'][0]['employees'][0]['status'] == 'incomplete'
    assert workspace.candidate_hash() == before
    workspace.assert_integrity()


def test_indexed_large_project_reads_and_edits_real_assigned_source(harness):
    from testfix.project_tools import ProjectWorkspace, files_under
    source = Path(harness.project['baseline'])
    target = source / 'flaskr/app.py'
    target.write_text('# padding\n' * 9000 + 'VALUE = 0\n', encoding='utf-8')
    (source / 'flaskr/other.py').write_text('# unrelated\n' * 20000, encoding='utf-8')
    harness.project['source']['files'] = files_under(source)
    harness.project['context_mode'] = 'indexed'
    workspace = ProjectWorkspace(harness.settings, harness.run['id'], harness.project)
    snapshot = workspace.snapshot()
    assert snapshot['source_context']['mode'] == 'indexed_excerpts'
    assert len(snapshot['sources']['flaskr/app.py']) < len(target.read_text(encoding='utf-8'))
    plan = TeamBoundaryProvider().team_plan({'requirement_ids': ['MINI-VALUE']})
    ledger = {'request': 'Update the value in the actual assigned file', 'rounds': []}

    def decide(skill, context, schema):
        observed = context['observations']
        if not observed:
            action = employee_action('read_file', path='flaskr/app.py')
            action['start_line'] = 9001
            return action
        assert observed[0]['result']['text'] == 'VALUE = 0'
        if skill == 'explore-repository':
            return employee_action('finish')
        action = employee_action('apply_edit', baseline=workspace.candidate_hash(),
                                 files=[{'path':'flaskr/app.py', 'source':'VALUE = 1'}])
        action['query'] = 'VALUE = 0'
        return action

    output, result = execute_team_round(workspace, plan,
        {'status':'OPTIMAL', 'order':['inspect-1','implement-1']}, call=decide,
        public_test=lambda: pytest.fail('No test is requested'), emit=lambda *args: None,
        ledger=ledger, round_number=1,
        manager_check=lambda *args: {'ready':True, 'reason':'Observed task evidence', 'missing':[]})
    assert result['changed_files'] == ['flaskr/app.py']
    assert (workspace.candidate/'flaskr/app.py').read_text(encoding='utf-8').endswith('VALUE = 1\n')
    assert target.read_text(encoding='utf-8').endswith('VALUE = 0\n')
    assert ledger['rounds'][0]['handoffs']['implement-1']['manager_check']['ready']
    workspace.assert_integrity()
