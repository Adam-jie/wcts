"""Continuation provenance tests; no network, model, or external dataset."""
import sys

import pytest

from scripts import resume_feabench_pilot as resume


def schema_value(schema):
    if 'enum' in schema:
        return schema['enum'][0]
    kind = schema['type']
    if kind == 'object':
        return {k: schema_value(v) for k, v in schema['properties'].items()}
    if kind == 'array':
        return [schema_value(schema['items']) for _ in range(schema.get('minItems', 0))]
    if kind == 'string':
        return 'x' * schema.get('minLength', 0)
    return {'string': '', 'boolean': False, 'integer': 0}[kind]


@pytest.fixture
def parent_case(tmp_path):
    baseline, parent = tmp_path / 'base', tmp_path / 'parent'
    (baseline / 'sqlglot').mkdir(parents=True)
    (baseline / 'sqlglot/parser.py').write_text('VALUE = 1\n', encoding='utf-8')
    candidate = parent / 'workspace/candidate'
    resume.copy_sealed(baseline, candidate, resume.files_under(baseline))
    (candidate / 'sqlglot/parser.py').write_text('VALUE = 2\n', encoding='utf-8')
    generated = parent / 'workspace/generated_tests'
    generated.mkdir()
    test_source = 'def test_public_contract():\n    assert True\n'
    (generated / 'test_feature.py').write_text(test_source, encoding='utf-8', newline='')
    case = {'schema': 'testfix.feabench-pilot-case.v1', 'instance_id': 'fixture-1',
        'repo_url': 'https://example.invalid/public', 'base_commit': 'pinned',
        'baseline_root': str(baseline), 'python': sys.executable,
        'requirement': {'id': 'feature', 'description': 'A public feature.'},
        'allowed_paths': ['sqlglot/parser.py'], 'package': 'sqlglot',
        'evaluation': {'command': ['never-run', '{candidate}', '{output}']}}
    catalog = resume.load_catalog()
    candidate_hash = resume._mapping_sha256(resume.files_under(candidate))
    stages, completed = [], []
    for skill in resume.CAPABILITY_NAMES:
        if skill == 'verify-delivery':
            continue
        output = schema_value(resume.PATCH_SCHEMA if skill == 'repair-code' else resume.SCHEMAS[skill])
        if skill == 'analyze-requirements':
            row = schema_value(resume.SCHEMAS[skill]['properties']['requirements']['items'])
            row['id'] = 'feature'
            output['requirements'] = [row]
        if skill == 'generate-tests':
            output.update(files=[{'path': 'test_feature.py', 'source': test_source}],
                coverage=[{'requirement_id': 'feature', 'tests': ['test_public_contract']}], steps=['Run frozen tests.'])
        if skill == 'review-patch':
            output['candidate_sha256'] = candidate_hash
        contract = catalog.get_contract(skill)
        declaration = catalog.validate_invocation(skill, 'python_package', contract['reads'],
            contract['writes'], completed, 1, 60)
        artifact = f'capabilities/{skill}-1.json'
        resume.save(parent / artifact, output)
        resume.save(parent / 'model_calls' / f'{len(stages):03d}-{skill}' / 'request.json',
            {'context': {**{k: case[k] for k in ('instance_id', 'repo_url', 'base_commit')},
                'requirements': [case['requirement']], 'scope_manifest': {'allowed_changes': case['allowed_paths']}}})
        stages.append({'skill_id': skill, 'attempt': 1, 'status': 'produced',
                       'declaration': declaration, 'artifact': artifact})
        completed.append(skill)
    public_log = parent / 'public-tests/generated.stdout.txt'
    public_log.parent.mkdir()
    public_log.write_text('9 collected, public assertions fail', encoding='utf-8')
    suite = {'total': 9, 'case_ids': [f'test_{i}' for i in range(9)],
             'duplicate_case_ids': [], 'stdout_path': str(public_log)}
    report = {'schema': 'testfix.feabench-native-pilot.v1', 'instance_id': case['instance_id'],
        'status': 'failed', 'source_revision': resume._mapping_sha256(resume.files_under(baseline)),
        'skill_catalog': catalog.snapshot(), 'stages': stages, 'model_calls': len(stages),
        'generated_tests': {'sha256': resume.files_under(generated)},
        'patches': [{'candidate_sha256': candidate_hash}], 'review': {'candidate_sha256': candidate_hash},
        'generated_baseline': suite, 'generated_candidate': suite, 'budget': {'reported_tokens': 12},
        'setting': 'native fixture', 'adapter_status': 'standalone', 'security': 'fixture'}
    resume.save(parent / 'report.json', report)
    return case, parent, catalog


def test_parent_preserves_public_artifact_provenance(parent_case):
    case, parent, catalog = parent_case
    result = resume.validate_parent(case, parent, catalog)
    assert len(result['inherited']) == 8
    assert result['outputs']['generate-tests']['steps'] == ['Run frozen tests.']
    assert all(not stage['fresh_model_call'] for stage in result['inherited'])
    assert str(parent / 'public-tests/generated.stdout.txt') in result['pinned']


@pytest.mark.parametrize('target,fragment', [
    ('base/sqlglot/parser.py', 'baseline seal'),
    ('parent/workspace/candidate/sqlglot/parser.py', 'patch/review receipt'),
    ('parent/workspace/generated_tests/test_feature.py', 'frozen seal'),
])
def test_inherited_tree_tampering_is_rejected(parent_case, target, fragment):
    case, parent, catalog = parent_case
    (parent.parent / target).write_text('CHANGED = True\n', encoding='utf-8')
    with pytest.raises(ValueError, match=fragment):
        resume.validate_parent(case, parent, catalog)


def test_same_id_does_not_allow_requirement_replacement(parent_case):
    case, parent, catalog = parent_case
    case['requirement']['description'] = 'Different task'
    with pytest.raises(ValueError, match='requirement or editable scope'):
        resume.validate_parent(case, parent, catalog)


def test_skill_catalog_tampering_is_rejected(parent_case):
    case, parent, catalog = parent_case
    report = resume.read_json(parent / 'report.json')
    report['skill_catalog']['files']['catalog.json'] = '0' * 64
    resume.save(parent / 'report.json', report)
    with pytest.raises(ValueError):
        resume.validate_parent(case, parent, catalog)


def test_frozen_artifact_text_must_match_seal_even_with_valid_schema(parent_case):
    case, parent, catalog = parent_case
    artifact = parent / 'capabilities/generate-tests-1.json'
    output = resume.read_json(artifact)
    output['files'][0]['source'] += '\n# changed\n'
    resume.save(artifact, output)
    with pytest.raises(ValueError, match='artifact no longer matches'):
        resume.validate_parent(case, parent, catalog)


def test_generated_feedback_cannot_redirect_to_judge(parent_case):
    case, parent, catalog = parent_case
    report = resume.read_json(parent / 'report.json')
    report['generated_candidate'] = {**report['generated_candidate'], 'stdout_path': str(parent / 'official-evaluation/log.txt')}
    resume.save(parent / 'report.json', report)
    with pytest.raises(ValueError, match='escaped parent public evidence'):
        resume.validate_parent(case, parent, catalog)


def test_preflight_does_not_create_provider_or_candidate(parent_case, monkeypatch):
    case, parent, _ = parent_case
    case_path, output = parent.parent / 'case.json', parent.parent / 'preflight'
    resume.save(case_path, case)
    def forbidden_provider(*args, **kwargs):
        raise AssertionError('Preflight must not create model provider')
    monkeypatch.setattr(resume, 'ProjectModelProvider', forbidden_provider)
    report = resume.run(case_path, parent, output)
    assert report['status'] == 'preflight' and report['model_calls'] == 0
    assert not (output / 'workspace').exists()
    with pytest.raises(ValueError, match='Never overwrite'):
        resume.run(case_path, parent, output)


def test_copy_is_byte_identical_and_does_not_mutate_parent(parent_case):
    _, parent, _ = parent_case
    source = parent / 'workspace/generated_tests'
    seal = resume.files_under(source)
    copy = parent.parent / 'copied-tests'
    resume.copy_sealed(source, copy, seal)
    assert resume.files_under(source) == resume.files_under(copy) == seal
