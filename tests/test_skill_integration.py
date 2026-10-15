"""Exercise Skill prompt/version wiring at the actual model transport boundary."""
import json
import shutil

import httpx
import pytest

import testfix.providers as providers
from testfix.config import Settings
from testfix.skills import DEFAULT_CATALOG, SkillCatalog, SkillValidationError


def test_direct_model_receives_role_skill_and_tracks_actual_prompt(monkeypatch):
    sent = []
    original_client = httpx.Client

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"requirements":["preserve public behavior"]}'}}]})

    monkeypatch.setattr(providers.httpx, 'Client', lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    provider = providers.ModelProvider('openai', Settings(base_url='https://model.example/v1', model='test-model', api_key='fixture-token'))
    assert provider.invoke('requirements', {'specification': 'public requirement'})['requirements']
    expected_instructions = provider.skill_catalog.render_instructions('requirements', 'pure_python')
    assert expected_instructions in sent[0]['messages'][0]['content']
    assert provider.calls[-1]['skill_id'] == 'analyze-requirements'
    assert provider.calls[-1]['skill_prompt_applied'] is True
    assert provider.metadata()['skill_catalog'] == provider.skill_snapshot
    assert 'fixture-token' not in json.dumps(provider.metadata())


def test_changed_skill_blocks_model_request_before_http(monkeypatch, tmp_path):
    library = tmp_path / 'library'
    shutil.copytree(DEFAULT_CATALOG.parent, library)
    catalog = SkillCatalog(library / 'catalog.json')
    monkeypatch.setattr(providers, 'load_catalog', lambda: catalog)
    provider = providers.ModelProvider('openai', Settings(base_url='https://model.example/v1', model='test-model', api_key='fixture-token'))
    requests = []
    monkeypatch.setattr(providers.httpx, 'Client', lambda **kwargs: requests.append(kwargs))
    file = library / 'analyze-requirements' / 'SKILL.md'
    file.write_bytes(file.read_bytes() + b'\nChanged after provider initialization.\n')
    with pytest.raises(SkillValidationError, match='stale_snapshot'):
        provider.invoke('requirements', {'specification': 'public'})
    assert not requests
    assert not provider.calls


def test_replay_skill_metadata_does_not_claim_model_generation():
    provider = providers.ModelProvider('replay', Settings())
    provider.invoke('requirements', {'task_id': 'HumanEval/0'})
    assert provider.metadata()['skill_prompt_applied'] is False
    assert provider.calls[-1]['skill_prompt_applied'] is False


@pytest.mark.parametrize('profile', ['flaskr_project', 'python_package'])
def test_full_project_cannot_verify_before_exploring_and_grounding(profile):
    catalog = SkillCatalog()
    # This satisfies each generic prerequisite chain individually, but would
    # postpone repository evidence until after a claimed full repair/delivery.
    late_grounding = ['analyze-requirements', 'generate-tests', 'diagnose-results',
                     'repair-code', 'review-patch', 'verify-delivery',
                     'plan-workflow', 'explore-repository', 'ground-code']
    with pytest.raises(SkillValidationError, match='invalid_order'):
        catalog.validate_plan(profile, late_grounding)
    pure = catalog.catalog['profiles']['pure_python']['required_skills']
    assert catalog.validate_plan('pure_python', pure)['execution_verified'] is False


@pytest.mark.parametrize('profile', ['flaskr_project', 'python_package'])
@pytest.mark.parametrize('weaken', ['reorder', 'omit'])
def test_changed_catalog_cannot_redefine_core_order_or_drop_project_evidence(tmp_path, weaken, profile):
    library = tmp_path / 'library'
    shutil.copytree(DEFAULT_CATALOG.parent, library)
    file = library / 'catalog.json'
    data = json.loads(file.read_text(encoding='utf-8'))
    stages = data['profiles'][profile]['required_skills']
    if weaken == 'reorder':
        data['profiles'][profile]['required_skills'] = stages[3:] + stages[:3]
        expected = 'invalid_order'
    else:
        data['profiles'][profile]['required_skills'] = stages[3:]
        for skill_id in stages[:3]:
            contract_file = library / skill_id / 'references' / 'contract.json'
            contract = json.loads(contract_file.read_text(encoding='utf-8'))
            contract['optimization']['mandatory'] = False
            contract_file.write_text(json.dumps(contract), encoding='utf-8')
        expected = 'missing_core_skill'
    file.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(SkillValidationError, match=expected):
        SkillCatalog(file)
