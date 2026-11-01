"""Exercise the FEA team bridge with real workspace, WCTP and CP-SAT."""

from pathlib import Path

import pytest

from scripts.feabench_team import PilotTeamWorkspace, pending_team_work
from scripts.run_feabench_pilot import patch_candidate
from testfix.config import Settings
from testfix.project_eval import _mapping_sha256
from testfix.project_tools import files_under
from testfix.team_assignment import plan_team_execution
from testfix.team_planning import validate_team_plan
from testfix.team_runtime import execute_team_round, TeamReplanRequired


def test_manager_sees_unfinished_sibling_work():
    plan = {'work_items':[{'id':'builder'}, {'id':'parser'}, {'id':'finished'}]}
    revision = {'task_graph':{'tasks':{'builder':{'status':'awaiting_manager'},
                                       'parser':{'status':'pending'}, 'finished':{'status':'handed_off'}}}}
    assert pending_team_work(plan, revision, 'builder') == [{'id':'parser'}]


def test_team_bridge_dispatches_and_applies_candidate_without_exposing_judge(tmp_path):
    baseline, candidate = tmp_path/'baseline', tmp_path/'candidate'
    for root in (baseline, candidate):
        (root/'sqlglot').mkdir(parents=True)
        (root/'sqlglot/builder.py').write_text('VALUE = 0\n',encoding='utf-8')
    case={'allowed_paths':['sqlglot/builder.py'],
          'requirement':{'id':'r1', 'description':'Set VALUE to 1'}}
    initial = files_under(baseline)
    current = files_under(candidate)
    def integrity():
        assert files_under(baseline)==initial and files_under(candidate)==current
    def apply(proposal):
        nonlocal current
        item = proposal['files'][0]
        receipt = patch_candidate({'baseline_sha256':proposal['baseline_sha256'],
            'edits':[{'path':item['path'], 'old':(candidate/item['path']).read_text(encoding='utf-8'),
                      'new':item['source']}]}, candidate, baseline, set(case['allowed_paths']),
                      _mapping_sha256(current), host_full_files=True)
        current = files_under(candidate)
        return receipt
    directory=tmp_path/'artifacts'/'pilot'
    directory.mkdir(parents=True)
    workspace=PilotTeamWorkspace(Settings(root=tmp_path),directory,case,baseline,candidate,integrity,apply)
    plan={'request_assessment':{'supported':True,'matched_requirement_ids':['r1'],
                                'uncovered_requests':[],'questions':[]},
          'work_items':[{'id':'inspect','title':'Inspect builder','skill_id':'explore-repository',
                         'requirement_ids':['r1'],'depends_on':[],'paths':['sqlglot/builder.py']},
                        {'id':'repair','title':'Update builder','skill_id':'repair-code',
                         'requirement_ids':['r1'],'depends_on':['inspect'],'paths':['sqlglot/builder.py']}]}
    plan=validate_team_plan(plan,{'r1'},workspace.allowed,workspace.allowed)
    schedule=plan_team_execution(workspace,plan,wall_seconds=1000,token_limit=100000,model_slots=1)
    def call(skill, context, schema):
        action={'action':'search','path':'','query':'VALUE\nMISSING','start_line':1,'limit':80,
                'baseline_sha256':'','files':[],'message':'Observed source'}
        if context['observations']:
            hits=context['observations'][0]['result']['hits']
            assert hits==[{'path':'sqlglot/builder.py','line':1,'text':'VALUE = 0','query':'VALUE'}]
            if skill=='explore-repository':
                action['action']='finish'
            else:
                action.update(action='apply_edit',query='VALUE = 0',baseline_sha256=workspace.candidate_hash(),
                              files=[{'path':'sqlglot/builder.py','source':'VALUE = 1'}])
        return action
    ledger={'request':'Set VALUE to 1','rounds':[]}
    reviews = []
    def manager_check(assigned, handoff, observations):
        reviews.append(assigned['id'])
        if assigned['id']=='inspect' and reviews.count('inspect')==1:
            return {'ready':False, 'reason':'Clarify the handoff', 'missing':['Explain the observed value']}
        return {'ready':True, 'reason':'Observed evidence', 'missing':[]}
    _, receipt=execute_team_round(workspace,plan,schedule,call=call,public_test=lambda:None,
        emit=lambda *args:None,ledger=ledger,round_number=1,
        manager_check=manager_check, replan_on_rejected_handoff=True)
    assert (candidate/'sqlglot/builder.py').read_text(encoding='utf-8')=='VALUE = 1\n'
    assert receipt['changed_files']==['sqlglot/builder.py']
    assert ledger['rounds'][0]['handoffs']['repair']['manager_check']['ready']
    assert reviews == ['inspect', 'inspect', 'repair']
    assert len(ledger['rounds'][0]['employees'][0]['steps']) == 3
    assert (baseline/'sqlglot/builder.py').read_text(encoding='utf-8')=='VALUE = 0\n'


def test_rejected_handoff_can_be_replanned_without_losing_candidate(tmp_path):
    baseline, candidate = tmp_path/'baseline', tmp_path/'candidate'
    for root in (baseline, candidate):
        (root/'sqlglot').mkdir(parents=True)
        for name in ('expressions.py', 'parser.py'):
            (root/'sqlglot'/name).write_text('VALUE = 0\n', encoding='utf-8')
    case = {'allowed_paths':['sqlglot/expressions.py', 'sqlglot/parser.py'],
            'requirement':{'id':'r1', 'description':'Update the builder and parser'}}
    original, current = files_under(baseline), files_under(candidate)

    def integrity():
        assert files_under(baseline) == original and files_under(candidate) == current

    def apply(proposal):
        nonlocal current
        item = proposal['files'][0]
        receipt = patch_candidate({'baseline_sha256':proposal['baseline_sha256'],
            'edits':[{'path':item['path'], 'old':(candidate/item['path']).read_text(encoding='utf-8'),
                      'new':item['source']}]}, candidate, baseline, set(case['allowed_paths']),
                      _mapping_sha256(current), host_full_files=True)
        current = files_under(candidate)
        return receipt

    directory = tmp_path/'artifacts'/'pilot'
    directory.mkdir(parents=True)
    workspace = PilotTeamWorkspace(Settings(root=tmp_path), directory, case, baseline, candidate, integrity, apply)
    ledger = {'request':'Update the builder and parser', 'rounds':[]}

    def plan(path, identity):
        return validate_team_plan({'request_assessment':{'supported':True, 'matched_requirement_ids':['r1'],
            'uncovered_requests':[], 'questions':[]}, 'work_items':[{'id':identity, 'title':'Update source',
            'skill_id':'repair-code', 'requirement_ids':['r1'], 'depends_on':[], 'paths':[path]}]},
            {'r1'}, workspace.allowed, workspace.allowed)

    def call(skill, context, schema):
        path = context['assigned_task']['paths'][0]
        action = {'action':'read_file', 'path':path, 'query':'', 'start_line':1, 'limit':80,
                  'baseline_sha256':'', 'files':[], 'message':'Read assigned file'}
        if context['observations']:
            action.update(action='apply_edit', query='VALUE = 0', baseline_sha256=workspace.candidate_hash(),
                          files=[{'path':path, 'source':'VALUE = 1'}], message='Update assigned file')
        return action

    first = plan('sqlglot/expressions.py', 'builder')
    schedule = plan_team_execution(workspace, first, wall_seconds=1000, token_limit=100000, model_slots=1)
    with pytest.raises(TeamReplanRequired) as caught:
        execute_team_round(workspace, first, schedule, call=call, public_test=lambda:None,
            emit=lambda *args:None, ledger=ledger, round_number=1, replan_on_rejected_handoff=True,
            manager_check=lambda *args:{'ready':False, 'reason':'Parser missing', 'missing':['Update parser.py']})
    assert caught.value.review['missing'] == ['Update parser.py']
    assert [item['action'] for item in caught.value.observations] == ['read_file', 'apply_edit', 'manager_check']
    assert caught.value.observations[0]['result']['text'] == 'VALUE = 0'
    assert ledger['rounds'][0]['status'] == 'replan_required'
    assert (candidate/'sqlglot/expressions.py').read_text(encoding='utf-8') == 'VALUE = 1\n'
    assert (candidate/'sqlglot/parser.py').read_text(encoding='utf-8') == 'VALUE = 0\n'

    second = plan('sqlglot/parser.py', 'parser')
    schedule = plan_team_execution(workspace, second, wall_seconds=1000, token_limit=100000, model_slots=1)
    _, receipt = execute_team_round(workspace, second, schedule, call=call, public_test=lambda:None,
        emit=lambda *args:None, ledger=ledger, round_number=2, replan_on_rejected_handoff=True,
        manager_check=lambda *args:{'ready':True, 'reason':'Ready for testing', 'missing':[]})
    assert receipt['candidate_sha256'] == workspace.candidate_hash()
    assert (candidate/'sqlglot/parser.py').read_text(encoding='utf-8') == 'VALUE = 1\n'
    assert (candidate/'sqlglot/expressions.py').read_text(encoding='utf-8') == 'VALUE = 1\n'
    workspace.assert_integrity()
