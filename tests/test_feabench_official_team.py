import json
import hashlib
from pathlib import Path

import pytest

from scripts.feabench_context import build_stage_context
from scripts.run_feabench_official_team import make_public_case, read_standard_task


def test_official_source_files_match_the_pinned_release():
    source=Path(__file__).resolve().parents[1]/'third_party/FEA-Bench'
    expected={'instances_lite.json':'6ff14ab3e3c4a5347de1ea24332f820336d82e865c326b5492998845d55c7229',
              'feabench/get_dataset.py':'7a5662958f87214eeda7e3355742731f178f63c3e071b76254518a60bc0d45fd',
              'feabench/run_prediction.py':'392ae95e2174ed3409207facd1a8956bb69a5f2bafd288ab568901dae9e126e4',
              'feabench/get_gold_results.py':'1bde822a42451316c970b5fdb1bf0054daea42df60b33774ec986230832a3ae6',
              'swe-bench.diff':'39e18ee42734cd3a539d47ebf6894bc1e23e5b44f755e6e3eeb88e2ee6d45efb'}
    assert {name:hashlib.sha256((source/name).read_bytes()).hexdigest() for name in expected} == expected
    assert len(json.loads((source/'instances_lite.json').read_text(encoding='utf-8'))) == 200


def test_standard_adapter_uses_public_fields_and_keeps_judge_fields_out(tmp_path):
    package = tmp_path/'sqlglot'
    package.mkdir()
    (package/'expressions.py').write_text('class Select:\n    pass\n',encoding='utf-8')
    template={'instance_id':'owner__repo-1', 'repo':'owner/repo', 'base_commit':'abc',
              'requirement':{'id':'R1','description':'old'}, 'allowed_paths':['sqlglot/expressions.py']}
    row={'instance_id':'owner__repo-1', 'repo':'owner/repo', 'base_commit':'abc',
         'problem_statement':'Add Select.sort_by(expression)', 'hints_text':'A public discussion.',
         'patch':'PRIVATE GOLD', 'test_patch':'PRIVATE ACCEPTANCE',
         'FAIL_TO_PASS':['PRIVATE TEST ID'], 'PASS_TO_PASS':['PRIVATE REGRESSION ID']}
    data = tmp_path/'standard.jsonl'
    data.write_text(json.dumps(row)+'\n',encoding='utf-8')
    loaded=read_standard_task(data,'owner__repo-1')
    case=make_public_case(loaded,template)
    assert case['problem_statement']==row['problem_statement']
    assert case['hints_text']==row['hints_text']
    assert not {'patch','test_patch','FAIL_TO_PASS','PASS_TO_PASS'} & set(case)
    context=build_stage_context(case,tmp_path,'ground-code')
    assert context['problem_statement']==row['problem_statement']
    assert context['hints_text']==row['hints_text']
    assert 'PRIVATE' not in json.dumps(context)
    loaded['base_commit']='different'
    with pytest.raises(ValueError,match='base_commit'):
        make_public_case(loaded,template)
