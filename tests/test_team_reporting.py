"""Aggregated capability timing and typed budget reporting across real WCTP.

Inference and business outcomes are explicit unit fixtures, never real-model
experiment claims. Source copies, sealed receipts and WCTP execution are real.
"""
from datetime import datetime, timedelta, timezone

import pytest

from test_project_workflow import harness
from test_team_workflow import TeamBoundaryProvider, execute_team
from testfix import project_workflow as workflow


class TimedFixtureProvider(TeamBoundaryProvider):
    def invoke(self, skill, context, schema, **kwargs):
        started = workflow.now()
        output = super().invoke(skill, context, schema, **kwargs)
        self.calls[-1].update(fixture_started_at=started, fixture_finished_at=workflow.now())
        return output


def test_capability_interval_covers_initial_and_later_employee_calls(harness, monkeypatch):
    # A deterministic timestamp clock exposes overwritten starts and stale ends
    # without sleeps or assertions dependent on local machine performance.
    moment = datetime(2026, 10, 15, tzinfo=timezone.utc)
    calls = [0]
    def next_time():
        calls[0] += 1
        return (moment + timedelta(seconds=calls[0])).isoformat()
    monkeypatch.setattr(workflow,'now',next_time)
    result, provider, _ = execute_team(harness,provider=TimedFixtureProvider())
    assert result['status']=='completed',result.get('error')
    states = {item['skill_id']:item for item in result['workflow']['capabilities']}
    exploration = [call for call in provider.calls if call['skill_id']=='explore-repository']
    assert len(exploration)==3  # Original core exploration, then employee read and finish.
    for skill, state in states.items():
        assert state['started_at'] <= state['finished_at']
        assert state['duration_s'] >= 0
        assert state['timing_basis']=='first_start_to_latest_completion; per_call_traces_are_authoritative'
        for call in provider.calls:
            if call['skill_id']==skill:
                assert state['started_at'] <= call['fixture_started_at']
                assert state['finished_at'] >= call['fixture_finished_at']
        if skill!='verify-delivery':
            assert state['active_model_calls']==0


class EmployeeFailureProvider(TeamBoundaryProvider):
    def __init__(self, mode):
        super().__init__()
        self.mode=mode

    def invoke(self, skill, context, schema, **kwargs):
        if 'assigned_task' not in context:
            return super().invoke(skill,context,schema,**kwargs)
        output = super().invoke(skill,context,schema,**kwargs)
        if self.mode=='usage':
            self.calls[-1]['usage']={'total_tokens':800001}
        elif self.mode=='typed':
            raise workflow.BudgetExceeded('Synthetic selected invocation allowance exhausted')
        elif self.mode=='text':
            raise RuntimeError('BudgetExceeded: synthetic text from an untrusted model callback')
        return output


@pytest.mark.parametrize('mode',['usage','typed'])
def test_employee_budget_failure_keeps_budget_status_after_wctp_wraps_error(harness, mode):
    result, _, tools = execute_team(harness,provider=EmployeeFailureProvider(mode))
    assert result['status']=='budget_exhausted',result.get('error')
    assert result['summary']['accepted']==0 and not result.get('download')
    assert result['error'].startswith('WCTPExecutionError:')
    assert result['workflow']['budget']['pending_tokens']==0
    assert result['workflow']['budget']['pending_calls']==0
    assert result['collaboration']['rounds'][0]['status']=='employee_execution_failed'
    workspace=tools.workspaces[-1]
    assert (workspace.candidate/'flaskr/app.py').read_text(encoding='utf-8')=='VALUE = 0\n'
    assert all(operation!='candidate_public' for operation, _, _ in tools.calls)
    if mode=='usage':
        assert result['workflow']['budget']['reported_tokens'] > 800000
        assert result['workflow']['budget']['concurrent_budget_exceeded'] is True


def test_budget_error_text_alone_does_not_change_failure_classification(harness):
    result, _, _ = execute_team(harness,provider=EmployeeFailureProvider('text'))
    assert result['status']=='failed',result.get('error')
    assert 'BudgetExceeded:' in result['error']
    assert result['summary']['accepted']==0 and not result.get('download')
    assert result['workflow']['budget']['pending_calls']==0
