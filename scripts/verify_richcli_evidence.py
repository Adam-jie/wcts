"""Verify an accepted local experiment; never call a model or change a patch."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

from testfix.project_eval import _mapping_sha256
from testfix.project_models import reject_protected_context
from testfix.project_tools import files_under
from testfix.project_workflow import delivery_passes


ROOT = Path(__file__).resolve().parent.parent


def verify(identifier: str) -> dict:
    if not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise ValueError('Expected a local run ID')
    directory = ROOT / 'artifacts' / identifier
    run = json.loads((directory / 'report.json').read_text(encoding='utf-8'))
    case = ROOT / 'experiments/rich-cli'
    source = json.loads((case / 'source.json').read_text(encoding='utf-8'))
    seal = json.loads((case / 'pre-model-seal.json').read_text(encoding='utf-8'))
    assert run['status'] == 'completed' and run['summary']['accepted'] == 1
    assert run['execution_profile'] == 'python_package' and run['project_id'] == 'Project/rich-cli'
    task = run['tasks'][0]
    assert task['source']['commit'] == source['commit']
    assert files_under(case / 'baseline') == source['files'] == seal['source']
    for path, key in [('acceptance_tests','acceptance'),('regression_tests','behavior_regression')]:
        assert files_under(case / path) == seal[key]
    checks_file = case / 'delivery-checks.json'
    assert hashlib.sha256(checks_file.read_bytes()).hexdigest() == seal['delivery_checks_sha256']
    checks = json.loads(checks_file.read_text(encoding='utf-8'))
    if isinstance(checks,dict):
        checks = checks['checks']
    assert delivery_passes(task['delivery'],adapter='python_package',required_checks=[item['name'] for item in checks])
    assert task['status'] == 'accepted' and all(value is True for value in task['verdict'].values())
    work = directory / 'workspace'
    assert files_under(work / 'baseline') == source['files']
    assert files_under(work / 'private/acceptance_tests') == seal['acceptance']
    assert files_under(work / 'private/regression_tests') == seal['behavior_regression']
    candidate = files_under(work / 'candidate')
    assert _mapping_sha256(candidate) == task['candidate_sha256'] == task['review']['candidate_sha256']
    changed = [name for name,digest in candidate.items() if source['files'].get(name) != digest]
    assert changed == ['src/rich_cli/__main__.py'] and candidate.keys() == source['files'].keys()
    for record in run['workflow']['capabilities']:
        assert record['status'] == 'completed'
        artifact = record['artifact']
        assert hashlib.sha256((ROOT / artifact['path']).read_bytes()).hexdigest() == artifact['sha256']
    calls=[]
    for call in run['model_calls']:
        assert call['status'] == 'completed' and call['requested_model'] == 'gpt-5.6-sol'
        request = call['request']
        assert request['profile'] == 'python_package'
        reject_protected_context(request['context'])
        calls.append({key:call.get(key) for key in ('skill_id','requested_model','actual_model','model_evidence','usage')})
    archive = directory / 'completed-project.zip'
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == run['download']['sha256']
    with zipfile.ZipFile(archive) as bundle:
        assert {name[8:] for name in bundle.namelist() if name.startswith('project/')} == set(candidate)
        for name,digest in candidate.items():
            assert hashlib.sha256(bundle.read('project/' + name)).hexdigest() == digest
        for tree,prefix in [(work/'generated_tests','validation/generated/'),
                            (work/'private/acceptance_tests','validation/independent/'),
                            (work/'private/regression_tests','validation/behavior-regression/')]:
            for name,digest in files_under(tree).items():
                assert hashlib.sha256(bundle.read(prefix + name)).hexdigest() == digest
    result = {'run_id':identifier,'source':{'repository':source['repository'],'commit':source['commit']},
              'status':run['status'],'duration_s':run['summary']['duration_s'],
              'changed_files':changed,'candidate_sha256':task['candidate_sha256'],
              'archive_sha256':run['download']['sha256'],'all_seals_and_artifacts_verified':True,
              'model_calls':calls,'budget':run['workflow']['budget'],
              'scores':{key:{field:task[key].get(field) for field in ('passed','total','successful')}
                        for key in ('baseline','generated_baseline','generated','upstream_regression',
                                    'behavior_regression','regression','acceptance','build','extended')},
              'note':'Offline integrity verification; no additional model or benchmark execution.'}
    (directory / 'evidence-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return {key:value for key,value in result.items() if key != 'model_calls'}


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('run_id')
    print(json.dumps(verify(parser.parse_args().run_id),ensure_ascii=False,indent=2))
