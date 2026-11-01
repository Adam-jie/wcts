"""Public context and additive-registry policy regressions; no LLM/judge calls."""
import ast
import hashlib
import json
from pathlib import Path

import pytest

from scripts.feabench_context import (build_stage_context, conservative_stage_reservation,
                                      serialized_bytes, stage_context_limit)
from scripts.run_feabench_pilot import validate_import_execution, make_plan
from testfix.project_workflow import ExecutionBudget
from testfix.skills import load_catalog


def case_tree(tmp_path):
    package=tmp_path/'pkg';package.mkdir()
    # A large unrelated prefix must not hide a later registry or its consumer.
    prefix=''.join(f'UNRELATED_{i} = {i}\n' for i in range(1700))
    source=('from enum import Enum\n'+prefix+'\nclass Parser:\n'
            '    EXPRESSION_PARSERS = {exp.Old: lambda self: self._parse_old()}\n'
            '    def parse_into(self, kind):\n'
            '        return self.EXPRESSION_PARSERS[kind](self)\n'
            '    def _parse_widget(self, token, kind):\n'
            '        return kind\n')
    (package/'parser.py').write_text(source,encoding='utf-8')
    (package/'builders.py').write_text('class Query:\n    def widget(self):\n        return helper_widget(self)\n\ndef helper_widget(value):\n    return value\n',encoding='utf-8')
    (tmp_path/'private_judge.py').write_text('PRIVATE JUDGE SENTINEL',encoding='utf-8')
    case={'instance_id':'public-task','requirement':{'id':'widget-builders','description':'Add widget fluent builder'},
          'allowed_paths':['pkg/parser.py','pkg/builders.py'],
          'evaluation':{'command':['PRIVATE EVALUATOR SENTINEL']},'gold_patch':'PRIVATE GOLD SENTINEL',
          'test_patch':'PRIVATE TEST PATCH SENTINEL'}
    return case,(package/'parser.py').read_bytes().decode('utf-8')


def test_late_registry_its_lookup_and_actual_helpers_are_visible(tmp_path):
    case,source=case_tree(tmp_path)
    extra={'review_findings':[{'path':'pkg/parser.py', 'problem':'Parser.EXPRESSION_PARSERS lacks widget support',
                               'fix':'Check existing _parse_widget callback and builder helper_widget'}]}
    result=build_stage_context(case,tmp_path,'repair-code',extra)
    parser=result['sources']['pkg/parser.py']
    for marker in ('EXPRESSION_PARSERS =','def parse_into(','def _parse_widget('):
        assert marker in parser
    assert 'helper_widget' in result['sources']['pkg/builders.py']
    manifest=result['source_manifest']['pkg/parser.py']
    assert manifest['sha256']==hashlib.sha256(source.encode()).hexdigest()
    assert max(b for a,b in manifest['line_ranges'])>1700
    assert manifest['omitted_lines']>1000 and manifest['excerpt_only'] is True
    for start,end in manifest['line_ranges']:
        assert ''.join(source.splitlines(keepends=True)[start-1:end]) in parser


def test_private_case_fields_never_enter_context_and_output_is_deterministic(tmp_path):
    case,_=case_tree(tmp_path)
    first=build_stage_context(case,tmp_path,'ground-code')
    assert first==build_stage_context(case,tmp_path,'ground-code')
    assert 'PRIVATE' not in json.dumps(first)
    assert first['source_index']==sorted(case['allowed_paths'])
    assert set(first['sources']) <= set(case['allowed_paths'])
    for duplicate in ('changed_sources','sources','source_manifest'):
        with pytest.raises(ValueError,match='Private/duplicate'):
            build_stage_context(case,tmp_path,'review-patch',{duplicate:{'pkg/parser.py':'duplicate'}})


def test_public_problem_title_is_included_in_model_input_and_source_selection(tmp_path):
    case,_=case_tree(tmp_path)
    case['requirement']['description']='Add support to the builder.'
    case['problem_statement']='Add widget fluent builder\n\nAdd support to the builder.'
    context=build_stage_context(case,tmp_path,'ground-code')
    assert context['problem_statement']==case['problem_statement']
    assert 'helper_widget' in context['sources']['pkg/builders.py']
    assert 'PRIVATE' not in json.dumps(context)


def test_capacity_is_real_serialized_byte_limit_and_plan_covers_reserve(tmp_path):
    case,_=case_tree(tmp_path)
    for skill in ('plan-workflow','explore-repository','repair-code','review-patch'):
        context=build_stage_context(case,tmp_path,skill,{'public_test_results':{'output_tail':'é'*500}})
        assert serialized_bytes(context)<=stage_context_limit(skill)
        budget=ExecutionBudget({'wall_seconds':1800,'token_limit':800000,'model_slots':1,'cpu_slots':1})
        assert budget.reserve(context,12000)<=conservative_stage_reservation(skill,12000)
    context=build_stage_context(case,tmp_path,'repair-code',max_source_bytes=200)
    assert context['source_selection']['excerpt_bytes']<=200
    with pytest.raises(ValueError,match='metadata exceeds'):
        build_stage_context(case,tmp_path,'repair-code',{'public_test_results':{'output_tail':'x'*51000}})
    plan=make_plan(load_catalog(),220000)
    assert plan['status'] in {'OPTIMAL','FEASIBLE'}
    assert plan['totals']['token_cap']<=800000


OLD='''class Parser:
    EXPRESSION_PARSERS = {
        exp.Old: lambda self: self._parse_old(),
        "JOIN_TYPE": lambda self: self._parse_join_side_and_kind(),
    }
'''


def extended(entry):
    return OLD.replace('        "JOIN_TYPE"',entry+'\n        "JOIN_TYPE"')


def test_additive_registry_callback_uses_existing_delayed_parse_shape():
    new=extended('        exp.Widget: lambda self: self._parse_widget(TokenType.WIDGET, exp.Widget),')
    validate_import_execution(ast.parse(OLD),ast.parse(new))


@pytest.mark.parametrize('entry',[
    '        exp.Widget: self._parse_widget(TokenType.WIDGET, exp.Widget),',
    '        exp.Widget: lambda self=evil(): self._parse_widget(),',
    '        exp.Widget: lambda self: self._parse_widget(open("secret")),',
    '        exp.Widget: lambda self: self._parse_widget(exp.Widget.__subclasses__()),',
    '        exp.Widget: lambda self: self._parse_widget(**options),',
    '        exp.Widget: lambda self: self.run(),',
    '        **extra,',
    '        exp.Old: lambda self: self._parse_old(),',
])
def test_registry_extension_never_permits_eager_or_nested_execution(entry):
    with pytest.raises(ValueError):
        validate_import_execution(ast.parse(OLD),ast.parse(extended(entry)))


def test_registry_extension_cannot_rewrite_or_delete_existing_entries():
    new=extended('        exp.Widget: lambda self: self._parse_widget(),')
    for mutated in (new.replace('_parse_old()','_parse_different()'),
                    new.replace('        exp.Old: lambda self: self._parse_old(),\n','')):
        with pytest.raises(ValueError):
            validate_import_execution(ast.parse(OLD),ast.parse(mutated))


def test_registry_extension_preserves_existing_entry_order():
    new=extended('        exp.Widget: lambda self: self._parse_widget(),')
    original='        exp.Old: lambda self: self._parse_old(),\n'
    reordered=new.replace(original,'').replace('    }',original+'    }')
    with pytest.raises(ValueError):
        validate_import_execution(ast.parse(OLD),ast.parse(reordered))
