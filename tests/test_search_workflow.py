"""Sealed search integration with real WCTP and explicitly synthetic model/tests."""
from copy import deepcopy

from testfix import project_workflow as workflow
from testfix.repair_search import public_evaluation
from test_project_workflow import harness, PRIVATE_MARKER
from test_team_workflow import TeamBoundaryProvider


class AuditFixtureProvider(TeamBoundaryProvider):
    def invoke(self, skill, context, schema, **kwargs):
        if context.get('test_quality_review'):
            from testfix.test_quality import AUDIT_SCHEMA
            assert schema == AUDIT_SCHEMA
            output = {'approved':True,'findings':[],'summary':'Synthetic quality audit, no real inference'}
            self.calls.append({'skill_id':skill,'provider':'synthetic_audit','usage':{'total_tokens':1},'status':'completed'})
            return output
        return super().invoke(skill,context,schema,**kwargs)


def test_search_branches_dispatch_real_tools_and_independent_verification_runs_only_once(harness):
    run = deepcopy(harness.run)
    run.update(collaboration_request='Synthetic scoped behavior',
               budget={**workflow.DEFAULT_BUDGET, 'wall_seconds':36000, 'token_limit':4_000_000},
               search={'mode':'forest','trees':2,'iterations':3,'width':2,'depth':2,'min_successes':2})
    provider = AuditFixtureProvider()
    result = workflow.execute_project_run(run,harness.settings,provider=provider,tool_runner=harness.tools)
    assert result['status'] == 'completed', result.get('error')
    assert all(item['status']=='completed' and not item.get('active_model_calls') for item in result['workflow']['capabilities'])
    search = result['search_report']
    assert search['independent_acceptance_used'] is False
    assert search['status'] == 'selected'
    candidates = [node for node in search['nodes'] if node['parent_id']]
    assert len(candidates) == 2
    assert len({node['evidence']['branch_run_id'] for node in candidates}) == 2
    assert len([entry for entry in harness.tools.calls if entry[0] == 'verify']) == 1
    assert all(PRIVATE_MARKER not in str(entry) for entry in provider.invocations)
    assert search['promoted_candidate_sha256'] == result['tasks'][0]['history'][-1]['candidate_sha256']
    assert all(node['evidence']['employees'] for node in candidates)


def test_score_rejects_changed_test_identity_and_regression_failure():
    def suite(status, name='same'):
        return {'case_ids':[name], 'duplicate_case_ids':[], 'total':1, 'passed':int(status),
                'successful':status, 'collection_errors':0, 'parse_error':False,
                'binding_error':False, 'timeout':False}
    baseline = {'regression':suite(True), 'generated':suite(False)}
    good = {'regression':suite(True), 'generated':suite(True)}
    assert public_evaluation(baseline, good)['public_pass']
    for bad in ({'regression':suite(False),'generated':suite(True)},
                {'regression':suite(True),'generated':suite(True,'different')}):
        score = public_evaluation(baseline,bad)
        assert score['reward'] == 0 and not score['eligible']


def test_test_helper_defect_requires_regeneration_before_freezing(harness):
    from test_test_quality import helper
    class CorrectingFixture(AuditFixtureProvider):
        def invoke(self,skill,context,schema,**kwargs):
            result=super().invoke(skill,context,schema,**kwargs)
            if skill=='generate-tests' and not context.get('test_quality_review'):
                if context.get('test_quality_feedback'):
                    assert context['test_quality_history']==[context['test_quality_feedback']]
                result['files'][0]['source'] += '\n' + helper(bool(context.get('test_quality_feedback')))
            return result
    run=deepcopy(harness.run)
    run.update(collaboration_request='Synthetic scoped behavior',
        budget={**workflow.DEFAULT_BUDGET,'wall_seconds':36000,'token_limit':4_000_000},
        search={'mode':'mcts','trees':1,'iterations':1,'width':1,'depth':1,'min_successes':1})
    result=workflow.execute_project_run(run,harness.settings,provider=CorrectingFixture(),tool_runner=harness.tools)
    assert result['status']=='completed',result.get('error')
    assert result['test_quality']['passed']
    assert len(result['test_quality']['audits'])==2
    assert result['test_quality']['audits'][0]['host_checks']['defects']
    assert not result['test_quality']['audits'][1]['host_checks']['defects']
