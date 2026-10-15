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
    regression = case / 'regression_tests'
    clarification = list(public['clarifications'])
    evaluation_revision = 'original-frozen-v1'
    supplement_file = case / 'compatibility-v2-seal.json'
    if supplement_file.is_file():
        supplement = json.loads(supplement_file.read_text(encoding='utf-8'))
        regression = case / 'behavior_regression_v2'
        additional_checks = case / 'delivery-checks-v2.json'
        if (not isinstance(supplement, dict) or supplement.get('schema') != 'testfix.rich-cli.compatibility.v2'
                or files_under(regression) != supplement.get('regression_files')
                or hashlib.sha256(additional_checks.read_bytes()).hexdigest() != supplement.get('delivery_checks_sha256')):
            raise ValueError('Expanded compatibility evaluation seal changed')
        public_clarifications = supplement.get('public_clarifications')
        extra = json.loads(additional_checks.read_text(encoding='utf-8'))
        if (not isinstance(public_clarifications, list) or not public_clarifications
                or len(public_clarifications) > 10 or any(not isinstance(text, str) or not text.strip()
                or len(text) > 5000 for text in public_clarifications)
                or not isinstance(extra, list) or not extra or len(extra) > 10
                or any(not isinstance(item, dict) for item in extra)):
            raise ValueError('Invalid expanded compatibility metadata')
        checks = [*checks, *extra]
        clarification.extend(public_clarifications)
        evaluation_revision = 'head-tail-compatibility-v2'
    python = ROOT / '.venv-richcli/Scripts/python.exe'
    if not python.is_file():
        raise ValueError('Run scripts/setup-richcli.ps1 to prepare the pinned interpreter')
    project = {
        'id': 'Project/rich-cli', 'title': 'Rich-CLI：修复真实CSV字面值丢失与异常退出',
        'adapter': 'python_package', 'source': source, 'baseline': str(case / 'baseline'),
        'requirements': public['requirements'], 'clarifications': clarification,
        'evaluation_revision': evaluation_revision,
        'allowed_changes': public['allowed_changes'], 'acceptance': str(case / 'acceptance_tests'),
        'python': str(python), 'requirements_lock': str(case / 'requirements.lock'),
        'package_config': {'package': 'rich_cli', 'source_root': 'src',
                           'regression': str(regression), 'cli_module': 'rich_cli',
                           'distribution': 'rich-cli', 'checks': checks},
        'public_runtime': {'package': 'rich_cli', 'source_root': 'src', 'cli_module': 'rich_cli',
                           'distribution': 'rich-cli', 'rich': '12.6.0', 'click': '8.5.0',
                           'upstream_original_tests': 1,
                           'additional_regression_origin': 'TestFix pre-frozen existing-behavior checks'},
    }
    from testfix.package_tools import _config
    _config(project['package_config'])
    registry = ROOT / 'data/projects/registered'
    registry.mkdir(parents=True, exist_ok=True)
    (registry / 'rich-cli.json').write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'registered': project['id'], 'commit': source['commit'], 'seal_verified': True,
            'evaluation_revision': evaluation_revision,
            'model_calls': 0, 'note': 'Registration only; this does not run an experiment.'}


if __name__ == '__main__':
    print(json.dumps(register(), ensure_ascii=False, indent=2))
