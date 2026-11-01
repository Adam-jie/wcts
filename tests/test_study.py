import json
from pathlib import Path
import shutil

from test_application_execution import imported
from testfix.study import StudySpec, prepare_study, save_study


def test_real_registered_project_produces_fixed_paired_schedule(tmp_path):
    settings, _, _, project = imported(tmp_path)
    builtin = tmp_path / 'experiments/flaskr'
    builtin.mkdir(parents=True)
    original = Path(__file__).resolve().parents[1] / 'experiments/flaskr'
    for name in ('source.json', 'requirements.json'):
        shutil.copyfile(original / name, builtin / name)
    spec = StudySpec.model_validate({'name': '调度对照', 'provider': 'codex_cli', 'model': 'gpt-5.6-sol',
        'wall_seconds': 7200, 'token_limit': 800000, 'repetitions': 2,
        'cases': [{'id': 'case-1', 'project_id': project['id'], 'request': '修复搜索、分页和完成状态'},
                  {'id': 'case-2', 'project_id': project['id'], 'request': '修复搜索'}],
        'variants': [{'name': '单槽', 'model_slots': 1}, {'name': '双槽', 'model_slots': 2}]})
    result = prepare_study(spec, settings)
    assert [job['variant'] for job in result['jobs'][:4]] == ['单槽', '双槽', '双槽', '单槽']
    assert len({job['run_id'] for job in result['jobs']}) == 8
    assert all(job['status'] == 'pending' for job in result['jobs'])
    assert result['cases'][0]['source_files'] == project['source']['files']
    assert result['cases'][0]['acceptance_files'] == project['acceptance_files']
    target = settings.artifacts / 'studies' / result['id']
    save_study(target, result)
    assert json.loads((target / 'study.json').read_text(encoding='utf-8')) == result
