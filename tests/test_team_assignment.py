"""Actual integer solutions; worker cost tables below are declared fixtures."""
from copy import deepcopy

import pytest

from testfix.team_assignment import compile_options, solve_joint_plan
from testfix.team_planning import TeamPlanError
from test_team_planning import item, plan


def option(worker, *, duration=3, tokens=10, strategy='standard'):
    return {'worker_id':worker,'strategy':strategy,'duration_seconds':duration,'token_cap':tokens,'max_steps':5,'fit_penalty':0}


def solve(value, options, **kwargs):
    return solve_joint_plan(value,options,wall_seconds=kwargs.pop('wall_seconds',50),token_limit=kwargs.pop('token_limit',1000),model_slots=kwargs.pop('model_slots',2),**kwargs)


def test_worker_strategy_choice_respects_budget_instead_of_preassigning_first_option():
    value=plan(item('a'))
    choices={'a':[option('slow',duration=8,tokens=5),option('fast',duration=2,tokens=30,strategy='focused')]}
    fast=solve(value,choices);assert fast['assignments']['a']['worker_id']=='fast'
    cheap=solve(value,choices,token_limit=10);assert cheap['assignments']['a']['worker_id']=='slow'
    impossible=solve(value,choices,wall_seconds=3,token_limit=10);assert impossible['status']=='INFEASIBLE' and impossible['dispatch']==[]


def test_capacity_file_locks_and_dependencies_constrain_real_assignments():
    value=plan(item('a',paths=('a.py',)),item('b',paths=('b.py',)),item('c',depends=('a','b'),paths=('c.py',)))
    choices={key:[option('one'),option('two')] for key in ['a','b','c']}
    result=solve(value,choices);assert set(result['dispatch'])=={'a','b'}
    a,b,c=(result['assignments'][k] for k in ['a','b','c'])
    assert a['worker_id']!=b['worker_id'];assert c['start_seconds']>=max(a['end_seconds'],b['end_seconds'])
    shared=deepcopy(value);shared['work_items'][1]['paths']=['a.py']
    result=solve(shared,choices);assert len(result['dispatch'])==1


def test_active_worker_is_pinned_and_completed_dependencies_are_not_scheduled_again():
    value=plan(item('a',paths=('a.py',)),item('b',depends=('a',),paths=('b.py',)))
    choices={key:[option('one'),option('two')] for key in ['a','b']}
    active={'a':{'worker_id':'two','strategy':'standard'}}
    result=solve(value,choices,active=active);assert result['assignments']['a']['worker_id']=='two';assert result['dispatch']==[]
    result=solve(value,choices,completed=['a']);assert list(result['assignments'])==['b'];assert result['dispatch']==['b']


def test_ready_out_degree_priority_applies_when_capacity_is_one():
    value=plan(item('leaf',paths=('leaf.py',)),item('hub',paths=('hub.py',)),item('child',depends=('hub',),paths=('child.py',)))
    result=solve(value,{key:[option('one')] for key in ['leaf','hub','child']},model_slots=1)
    assert result['dispatch']==['hub']


def test_compilation_uses_host_specialists_and_inactive_domains_stay_unassigned():
    value=plan(item('db'))
    routes={'db':{'status':'ready','specialists':[{'id':'software'},{'id':'database'}]}}
    choices=compile_options(value,routes,model_slots=2,max_steps=5)
    assert {o['role'] for o in choices['db']}=={'database','integration'}
    assert all(o['role'] not in {'mcu','mpu','threejs'} for o in choices['db'])
    result=solve(value,choices,token_limit=100000,wall_seconds=1000)
    assert result['assignments']['db']['role']=='database'
    routes['db']['status']='blocked'
    with pytest.raises(TeamPlanError,match='blocked'):compile_options(value,routes,model_slots=2,max_steps=5)


def test_larger_dag_is_configurable_and_every_required_task_is_retained(monkeypatch):
    monkeypatch.setenv('TESTFIX_MAX_WORK_ITEMS','12')
    value=plan(*(item('task'+str(i),paths=(f'file{i}.py',)) for i in range(10)))
    result=solve(value,{t['id']:[option('one'),option('two')] for t in value['work_items']})
    assert len(result['assignments'])==10
    monkeypatch.setenv('TESTFIX_MAX_WORK_ITEMS','8')
    with pytest.raises(TeamPlanError,match='1–8'):solve(value,{})


def test_missing_worker_and_bad_host_costs_never_produce_a_dispatch():
    value=plan(item('a'))
    assert solve(value,{'a':[]})['status']=='INFEASIBLE'
    with pytest.raises(TeamPlanError):solve(value,{'a':[option('x',duration=True)]})


def test_zero_wall_budget_is_infeasible_instead_of_an_invalid_solver_model():
    result=solve(plan(item('a')),{'a':[option('one')]},wall_seconds=0)
    assert result['status']=='INFEASIBLE' and not result['dispatch']


def test_later_objective_timeout_retains_only_a_previously_proven_feasible_assignment(monkeypatch):
    from testfix import team_assignment
    base=team_assignment.cp_model.CpSolver
    class LaterTimeout(base):
        calls=0
        def solve(self, model):
            self.calls+=1
            return team_assignment.cp_model.UNKNOWN if self.calls>1 else super().solve(model)
    monkeypatch.setattr(team_assignment.cp_model,'CpSolver',LaterTimeout)
    result=solve(plan(item('a')),{'a':[option('one')]})
    assert result['status']=='FEASIBLE' and result['assignments']['a']['worker_id']=='one'
    assert result['phases']==['OPTIMAL','UNKNOWN']


def test_controller_joint_feasibility_admits_a_focused_plan_rejected_by_legacy_fixed_costs(tmp_path):
    from testfix.team_assignment import plan_team_execution
    from testfix.team_planning import solve_team_plan, priority_ready
    class Workspace:
        project={'adapter':'flaskr','requirements':[{'id':'R1'}]}
        allowed={'src/main.py'}
        def snapshot(self):return {'sources':{'src/main.py':'VALUE = 0'}}
    value=plan(item('inspect','explore-repository'),item('repair',depends=('inspect',)))
    assert solve_team_plan(value,wall_seconds=1000,token_limit=44000)['status']=='INFEASIBLE'
    result=plan_team_execution(Workspace(),value,wall_seconds=1000,token_limit=44000,model_slots=1)
    assert result['status']=='OPTIMAL'
    assert result['initial_assignment']['assignments']['inspect']['strategy']=='focused'
    assert result['initial_assignment']['estimates']['tokens']==38400
    routes={key:{'status':'ready','specialists':[{'id':'software'}]} for key in ['inspect','repair']}
    repeated=compile_options(value,routes,model_slots=1,max_steps=5,stages=result['stages'])
    assert next(o for o in repeated['inspect'] if o['strategy']=='focused')['token_cap']==14400
    assert priority_ready(['inspect'],result)==['inspect']
