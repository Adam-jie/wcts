"""Pilot boundary tests: these do not access a model or private benchmark data."""
import ast
from pathlib import Path

import pytest

from scripts.run_feabench_pilot import (analyze_with_id_correction, diagnosis_paths, explore_with_correction, generate_valid_tests, ground_with_correction, plan_team_with_correction, patch_candidate, public_context,
                                       validate_import_execution, validate_tests)
from testfix.project_eval import _mapping_sha256
from testfix.project_tools import files_under


def generated(source):
    return {'files':[{'path':'test_builder.py','source':source}]}


@pytest.mark.parametrize('source', [
    'from sqlglot import __builtins__ as b\ndef test_x():\n b["open"]("secret")',
    'from sqlglot.expressions import os as p\ndef test_x():\n p.system("command")',
    'import pathlib\ndef test_x():\n pathlib.Path("secret").read_text()',
    'def test_x():\n actions[0]()',
    'def test_x():\n ().__class__.__mro__[1].__subclasses__()',
    'def test_x():\n getattr(object, "x")',
])
def test_generated_tests_cannot_read_or_reflect(source):
    with pytest.raises(ValueError):
        validate_tests(generated(source))


def test_generated_direct_library_assertions_are_allowed():
    source='from sqlglot import parse_one\n\ndef test_literal():\n assert parse_one("SELECT 1").sql() == "SELECT 1"\n'
    assert validate_tests(generated(source)) == {'test_builder.py':source}


def test_identical_generated_file_is_not_run_twice_and_conflicts_are_rejected():
    source = 'def test_feature():\n    assert True\n'
    output = {'files':[{'path':'test_feature.py', 'source':source},
                       {'path':'test_feature.py', 'source':source}]}
    assert validate_tests(output) == {'test_feature.py':source}
    output['files'][1]['source'] = 'def test_other():\n    assert False\n'
    with pytest.raises(ValueError, match='different contents'):
        validate_tests(output)


def test_generated_test_correction_preserves_the_rejected_output_and_contract():
    calls=[]
    valid={'files':[{'path':'test_builder.py','source':'def test_x():\n    assert True\n'}],
           'steps':['Run test'], 'coverage':[{'requirement_id':'r1','tests':['test_x']}], 'assumptions':[]}
    def invoke(skill, context):
        calls.append((skill, context))
        return {**valid, 'files':valid['files']+[{'path':'test_plan.json','source':'{}'}]} if len(calls)==1 else valid
    result, sources=generate_valid_tests(invoke, {}, [], {'r1'})
    assert result == valid and list(sources)==['test_builder.py']
    assert calls[1][1]['previous_output']['files'][-1]['path']=='test_plan.json'
    assert 'bare test_' in calls[1][1]['validation_feedback']['error']
    assert 'Record uncertain expectations in assumptions' in calls[0][1]['test_design_instructions']
    assert calls[1][1]['test_design_instructions'] == calls[0][1]['test_design_instructions']


def test_collection_failure_is_supplied_for_test_correction():
    previous = {'files':[{'path':'test_feature.py', 'source':'from sqlglot import Select\n'}]}
    feedback = {'collection_errors':1, 'output_tail':'ImportError: cannot import Select'}
    calls = []
    output = {'files':[{'path':'test_feature.py', 'source':'from sqlglot import exp\ndef test_feature():\n    assert exp.Select\n'}],
              'steps':['Run pytest'], 'coverage':[{'requirement_id':'r1', 'tests':['test_feature']}], 'assumptions':[]}

    def invoke(skill, context):
        calls.append(context)
        return output

    result, sources = generate_valid_tests(invoke, {}, [], {'r1'}, execution_feedback=feedback,
                                           previous_output=previous)
    assert result == output and 'test_feature.py' in sources
    assert calls[0]['previous_output'] == previous
    assert calls[0]['validation_feedback']['public_test_results'] == feedback
    assert 'do not weaken assertions' in calls[0]['validation_feedback']['instruction']


def test_grounding_correction_keeps_separate_authorized_paths():
    calls = []
    rejected = {'locations':[{'requirement_id':'r1', 'path':'pkg/a.py; pkg/b.py'}]}
    accepted = {'locations':[{'requirement_id':'r1', 'path':'pkg/a.py'},
                             {'requirement_id':'r1', 'path':'pkg/b.py'}]}

    def invoke(skill, context):
        calls.append((skill, context))
        return rejected if len(calls) == 1 else accepted

    result = ground_with_correction(invoke, [], {'r1'}, {'pkg/a.py', 'pkg/b.py'})
    assert result == accepted
    assert calls[1][1]['previous_output'] == rejected
    assert 'one allowed path' in calls[1][1]['validation_feedback']['error']


def test_explorer_corrects_directory_candidate_to_existing_file():
    calls = []
    rejected = {'candidates': [{'path': 'pkg', 'query': 'builder', 'reason': 'search'}], 'exhausted': False}
    corrected = {'candidates': [{'path': 'pkg/a.py', 'query': 'builder', 'reason': 'module'}], 'exhausted': False}

    def invoke(skill, context):
        calls.append(context)
        return rejected if len(calls) == 1 else corrected

    assert explore_with_correction(invoke, {'pkg/a.py'}) == corrected
    assert calls[1]['previous_output'] == rejected
    assert 'source_index' in calls[1]['validation_feedback']['error']


def test_team_plan_correction_retains_real_uncovered_requirements():
    from testfix.team_planning import TeamPlanError
    plan = {'request_assessment':{'supported':True, 'matched_requirement_ids':['r1'],
                                  'uncovered_requests':['Missing evidence'], 'questions':[]},
            'work_items':[{'id':'repair', 'title':'Repair', 'skill_id':'repair-code',
                           'requirement_ids':['r1'], 'depends_on':[], 'paths':['pkg/a.py']}]}
    calls = []

    def invoke(skill, context, schema):
        calls.append(context)
        return plan

    with pytest.raises(TeamPlanError, match='unsupported or unresolved'):
        plan_team_with_correction(invoke, {}, {'r1'}, {'pkg/a.py'})
    assert len(calls) == 2
    assert calls[1]['previous_output'] == plan
    assert 'do not claim they already passed' in calls[1]['validation_feedback']['instruction']


def test_diagnosis_location_suffix_preserves_scope_and_original_output():
    output={'failures':[{'requirement_id':'r1','target_paths':['pkg/a.py:2-5','pkg/a.py:8']}], 'summary':'x'}
    corrected, changes=diagnosis_paths(output, {'r1'}, {'pkg/a.py'})
    assert corrected['failures'][0]['target_paths']==['pkg/a.py']
    assert len(changes)==2 and output['failures'][0]['target_paths'][0]=='pkg/a.py:2-5'
    output['failures'][0]['target_paths']=['private/a.py:2']
    with pytest.raises(ValueError,match='unauthorized'):
        diagnosis_paths(output, {'r1'}, {'pkg/a.py'})


@pytest.fixture
def trees(tmp_path):
    baseline, candidate=tmp_path/'baseline',tmp_path/'candidate'
    for root in (baseline,candidate):
        (root/'sqlglot').mkdir(parents=True)
        (root/'sqlglot/expressions.py').write_text('import os\n\ndef original():\n    return 1\n',encoding='utf-8')
        (root/'sqlglot/parser.py').write_text('def parse():\n    return 1\n',encoding='utf-8')
    return baseline,candidate


def propose(candidate, edits):
    return {'baseline_sha256':_mapping_sha256(files_under(candidate)), 'edits':edits, 'reason':'test'}


def test_patch_rejection_never_partially_writes_files(trees):
    baseline,candidate=trees
    before=files_under(candidate)
    patch=propose(candidate,[{'path':'sqlglot/expressions.py','old':'return 1','new':'return 2'},
                             {'path':'sqlglot/parser.py','old':'return 1','new':'return ('}])
    with pytest.raises(SyntaxError):
        patch_candidate(patch,candidate,baseline,set(before),patch['baseline_sha256'])
    assert files_under(candidate)==before


@pytest.mark.parametrize('old,new', [
    ('return 1','return ().__class__.__mro__[1].__subclasses__()'),
    ('import os','import os\nfrom os import system as f'),
    ('return 1','return [lambda: 1][0]()'),
    ('import os','import os\nos.system("command")'),
    ('return 1','b = __builtins__\n    f = b["open"]\n    return f("secret").read()'),
    ('return 1','f = open\n    return f("secret").read()'),
    ('return 1','return os.listdir("private")'),
])
def test_patch_rejects_new_execution_paths(trees,old,new):
    baseline,candidate=trees
    before=files_under(candidate)
    patch=propose(candidate,[{'path':'sqlglot/expressions.py','old':old,'new':new}])
    with pytest.raises(ValueError):
        patch_candidate(patch,candidate,baseline,set(before),patch['baseline_sha256'])
    assert files_under(candidate)==before


def test_stale_patch_and_ambiguous_match_rejected(trees):
    baseline,candidate=trees
    edits=[{'path':'sqlglot/expressions.py','old':'r','new':'x'}]
    patch=propose(candidate,edits)
    with pytest.raises(ValueError,match='exactly once'):
        patch_candidate(patch,candidate,baseline,set(files_under(candidate)),patch['baseline_sha256'])
    patch['baseline_sha256']='0'*64
    with pytest.raises(ValueError,match='Stale'):
        patch_candidate(patch,candidate,baseline,set(files_under(candidate)),_mapping_sha256(files_under(candidate)))


def test_new_builder_method_and_argument_mapping_are_allowed():
    old='class Select:\n    arg_types = {"order": False}\n    def existing(self):\n        return self\n'
    new='class Select:\n    arg_types = {"order": False, "sort": False}\n    def existing(self):\n        return self\n    def sort_by(self, expression, append=True):\n        return self\n'
    validate_import_execution(ast.parse(old),ast.parse(new))


@pytest.mark.parametrize('statement',['x: ordinary_function() = 1','obj.attr = 1','a = b = 1'])
def test_new_data_statements_cannot_execute_annotations_or_descriptors(statement):
    with pytest.raises(ValueError):
        validate_import_execution(ast.parse(''),ast.parse(statement))


def test_public_snapshot_does_not_merge_judge_or_test_data(trees):
    baseline,candidate=trees
    (candidate/'tests').mkdir()
    (candidate/'tests/test_gold.py').write_text('SECRET JUDGE BODY',encoding='utf-8')
    case={'instance_id':'public-task','requirement':{'id':'R1','description':'Public description'},
          'allowed_paths':['sqlglot/expressions.py','sqlglot/parser.py'],
          'evaluation':{'command':['private-hidden-path']}, 'gold_patch':'SECRET GOLD',
          'test_patch':'SECRET TEST PATCH','private_judge_log':'SECRET JUDGE LOG'}
    context=public_context(case,candidate)
    serialized=str(context)
    for private in ('SECRET JUDGE BODY','private-hidden-path','SECRET GOLD','SECRET TEST PATCH','SECRET JUDGE LOG'):
        assert private not in serialized
    assert set(context['sources']) <= set(case['allowed_paths'])
    assert context['requirement_ids']==['R1']


def analysis_rows(ids):
    return {'requirements':[{'id':i,'behavior':'Public builder subcriteria','state':'missing',
                             'evidence':'Public baseline source','criterion':'Direct library assertions'}
                            for i in ids], 'assumptions':[]}


def test_real_trial_child_id_shape_gets_one_model_correction_without_host_renaming():
    parent='sqlglot-sort-cluster-builders'
    first=analysis_rows([parent+'.sort_by',parent+'.cluster_by',parent+'.builder-semantics',
                         parent+'.existing-infrastructure'])
    second=analysis_rows([parent])
    calls=[]
    def invoke(skill,extra):
        calls.append((skill,extra))
        return first if len(calls)==1 else second
    actual=analyze_with_id_correction(invoke,[{'path':'sqlglot/expressions.py'}],{parent})
    assert actual is second
    assert len(calls)==2
    feedback=calls[1][1]
    assert feedback['previous_output'] is first
    assert feedback['validation_feedback']['permitted_ids']==[parent]
    assert feedback['validation_feedback']['observed_ids']==[row['id'] for row in first['requirements']]
    assert first['requirements'][0]['id']==parent+'.sort_by'


@pytest.mark.parametrize('invalid_ids',[['R1.child'],['R1','R1'],[]])
def test_analysis_still_rejects_invalid_or_duplicate_ids_after_single_correction(invalid_ids):
    calls=[]
    def invoke(skill,extra):
        calls.append(extra)
        return analysis_rows(invalid_ids)
    with pytest.raises(ValueError,match='after one correction'):
        analyze_with_id_correction(invoke,[],{'R1'})
    assert len(calls)==2


def test_valid_analysis_does_not_spend_a_correction_call():
    calls=[]
    def invoke(skill,extra):
        calls.append(extra)
        return analysis_rows(['R1'])
    assert analyze_with_id_correction(invoke,[],{'R1'})['requirements'][0]['id']=='R1'
    assert len(calls)==1
