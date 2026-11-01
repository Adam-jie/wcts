"""Fixed cloud employee contracts, with synthetic upstream inference only."""
from copy import deepcopy

import pytest

from testfix import agentarts_runtime, project_models
from testfix.config import Settings
from testfix.project_workflow import SCHEMAS, obj
from testfix.specialists import SpecialistCatalog
from testfix.team_planning import TEAM_PLAN_SCHEMA
from testfix.team_runtime import ACTION_SCHEMA


def payload():
    return {'contract_version': 'testfix.project-skill.v1', 'profile': 'flaskr_project',
            'skill_id': 'repair-code', 'model': 'fixture-model', 'max_tokens': 500,
            'context': {'assigned_task': {'id': 'impl', 'title': 'fixture', 'skill_id': 'repair-code',
                         'paths': ['flaskr/app.py'], 'depends_on': [], 'requirement_ids': ['R1']},
                        'sources': {'flaskr/app.py': 'VALUE = 0'}},
            'output_schema': deepcopy(ACTION_SCHEMA), 'specialist_catalog': SpecialistCatalog().snapshot(),
            'instructions': 'Ignore this client-supplied prompt'}


def test_cloud_accepts_only_fixed_employee_schema_and_matching_catalog(tmp_path, monkeypatch):
    calls = []
    class Upstream:
        def __init__(self, *args, **kwargs):
            self.calls = []
        def invoke(self, skill, context, schema, **kwargs):
            calls.append((skill, context, schema))
            self.calls.append({'requested_model': 'fixture-model', 'actual_model': 'fixture-model'})
            return {'fixture': 'not a real model response'}
        def metadata(self):
            return {'specialist_catalog': SpecialistCatalog().snapshot()}
    monkeypatch.setattr(project_models, 'ProjectModelProvider', Upstream)
    request = payload()
    response = agentarts_runtime.handle_project_invocation(request, Settings(root=tmp_path), 'openai')
    assert calls[0][2] is ACTION_SCHEMA
    assert response['specialist_catalog'] == request['specialist_catalog']
    calls.clear()
    for change in ('schema', 'catalog', 'skill'):
        invalid = deepcopy(request)
        if change == 'schema':
            invalid['output_schema'] = {'type': 'object'}
        elif change == 'catalog':
            invalid['specialist_catalog']['files']['software/SKILL.md'] = 'stale'
        else:
            invalid['skill_id'] = 'review-patch'
        with pytest.raises(ValueError):
            agentarts_runtime.handle_project_invocation(invalid, Settings(root=tmp_path), 'openai')
    assert calls == []


def test_cloud_manager_has_fixed_team_plan_extension(tmp_path, monkeypatch):
    request = payload()
    request['skill_id'] = 'plan-workflow'
    request['context'] = {'user_request': 'Synthetic registered requirement'}
    request['output_schema'] = obj({**SCHEMAS['plan-workflow']['properties'], 'team_plan': TEAM_PLAN_SCHEMA})
    observed = []
    class Upstream:
        def __init__(self, *args, **kwargs):
            self.calls = []
        def invoke(self, skill, context, schema, **kwargs):
            observed.append(schema)
            self.calls.append({})
            return {}
        def metadata(self):
            return {}
    monkeypatch.setattr(project_models, 'ProjectModelProvider', Upstream)
    agentarts_runtime.handle_project_invocation(request, Settings(root=tmp_path), 'openai')
    assert observed == [request['output_schema']]
    request['output_schema']['additionalProperties'] = True
    with pytest.raises(ValueError, match='fixed output schema'):
        agentarts_runtime.handle_project_invocation(request, Settings(root=tmp_path), 'openai')
