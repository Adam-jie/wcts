"""Register the pinned case without rewriting its pre-model evidence seal."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from testfix.project_tools import files_under


ROOT = Path(__file__).resolve().parent.parent


def register() -> dict:
    case = ROOT / 'experiments/rich-cli'
    source = json.loads((case / 'source.json').read_text(encoding='utf-8'))
    public = json.loads((case / 'requirements.json').read_text(encoding='utf-8'))
    seal = json.loads((case / 'pre-model-seal.json').read_text(encoding='utf-8'))
    checks_file = case / 'delivery-checks.json'
    checks = json.loads(checks_file.read_text(encoding='utf-8'))
    if isinstance(checks, dict):
        checks = checks['checks']
    if files_under(case / 'baseline') != source['files'] or source['files'] != seal['source']:
        raise ValueError('Pinned upstream source changed')
    for tree, key in [('acceptance_tests', 'acceptance'), ('regression_tests', 'behavior_regression')]:
        if files_under(case / tree) != seal[key]:
            raise ValueError('Pre-model evaluation seal changed: ' + tree)
    if hashlib.sha256(checks_file.read_bytes()).hexdigest() != seal['delivery_checks_sha256']:
        raise ValueError('Pre-model CLI checks changed')
    python = ROOT / '.venv-richcli/Scripts/python.exe'
    if not python.is_file():
        raise ValueError('Run scripts/setup-richcli.ps1 to prepare the pinned interpreter')
    project = {
        'id': 'Project/rich-cli', 'title': 'Rich-CLI：修复真实CSV字面值丢失与异常退出',
        'adapter': 'python_package', 'source': source, 'baseline': str(case / 'baseline'),
        'requirements': public['requirements'], 'clarifications': public['clarifications'],
        'allowed_changes': public['allowed_changes'], 'acceptance': str(case / 'acceptance_tests'),
        'python': str(python), 'requirements_lock': str(case / 'requirements.lock'),
        'package_config': {'package': 'rich_cli', 'source_root': 'src',
                           'regression': str(case / 'regression_tests'), 'cli_module': 'rich_cli',
                           'distribution': 'rich-cli', 'checks': checks},
        'public_runtime': {'package': 'rich_cli', 'source_root': 'src', 'cli_module': 'rich_cli',
                           'distribution': 'rich-cli', 'rich': '12.6.0', 'click': '8.5.0',
                           'upstream_original_tests': 1,
                           'additional_regression_origin': 'TestFix pre-frozen existing-behavior checks'},
    }
    registry = ROOT / 'data/projects/registered'
    registry.mkdir(parents=True, exist_ok=True)
    (registry / 'rich-cli.json').write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'registered': project['id'], 'commit': source['commit'], 'seal_verified': True,
            'model_calls': 0, 'note': 'Registration only; this does not run an experiment.'}


if __name__ == '__main__':
    print(json.dumps(register(), ensure_ascii=False, indent=2))
