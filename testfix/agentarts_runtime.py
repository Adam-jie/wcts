"""Business handler for the documented AgentArts high-code invocation endpoint."""
from __future__ import annotations

import json
import os

from .config import Settings, load_settings
from .providers import ModelProvider, SCHEMAS

CONTRACT_VERSION = "testfix.role.v1"


def handle_invocation(payload: dict, settings: Settings | None = None, provider_name: str | None = None) -> dict:
    if isinstance(payload,dict) and payload.get('contract_version') == 'testfix.project-skill.v1':
        return handle_project_invocation(payload,settings,provider_name)
    if not isinstance(payload, dict) or payload.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("Unsupported TestFix runtime contract")
    role = payload.get("role")
    context = payload.get("context")
    if role not in SCHEMAS or not isinstance(context, dict) or not isinstance(context.get("task_id"), str):
        raise ValueError("Invalid role/context")
    if len(json.dumps(context, allow_nan=False)) > 100_000:
        raise ValueError("Runtime context exceeds 100 KB")
    if any(key in context for key in ("canonical_solution", "acceptance", "acceptance_code", "api_key")):
        raise ValueError("Protected data may not enter runtime inference context")
    settings = settings or load_settings()
    provider_name = provider_name or os.getenv("TESTFIX_RUNTIME_PROVIDER", "openai")
    if provider_name not in {"openai", "ollama"}:
        raise ValueError("Runtime requires a real upstream model; recursive AgentArts/replay calls are forbidden")
    provider = ModelProvider(provider_name, settings)
    result = provider.invoke(role, context)
    return {"contract_version": CONTRACT_VERSION, "result": result, "usage": provider.calls[-1]["usage"]}


def handle_project_invocation(payload: dict, settings: Settings | None = None, provider_name: str | None = None) -> dict:
    """Host the same nine-capability model contract without trusting client prompts."""
    from .project_models import PROJECT_CONTRACT, ProjectModelProvider, reject_protected_context
    from .project_workflow import SCHEMAS
    if not isinstance(payload,dict):
        raise ValueError('Runtime payload must be an object')
    if payload.get('contract_version') != PROJECT_CONTRACT or not isinstance(payload.get('profile'),str) or payload['profile'] not in {'flaskr_project','python_package'}:
        raise ValueError('Runtime requires testfix.project-skill.v1 and a registered complete-project profile')
    skill = payload.get('skill_id')
    if not isinstance(skill,str) or skill not in SCHEMAS or payload.get('output_schema') != SCHEMAS[skill]:
        raise ValueError('Runtime only accepts a registered Skill and its fixed output schema')
    context = payload.get('context')
    if not isinstance(context,dict):
        raise ValueError('Runtime context must be an object')
    reject_protected_context(context)
    provider_name = provider_name or os.getenv('TESTFIX_RUNTIME_PROVIDER','openai')
    if provider_name not in {'openai','ollama','codex_cli'}:
        raise ValueError('Runtime requires a real upstream transport; recursion/replay forbidden')
    settings = settings or load_settings()
    provider = ProjectModelProvider(provider_name,settings,model=payload.get('model','gpt-5.6-sol'),profile=payload['profile'])
    result = provider.invoke(skill,context,SCHEMAS[skill],token_cap=payload.get('max_tokens',4096))
    trace = provider.calls[-1]
    return {'contract_version':PROJECT_CONTRACT,'result':result,'usage':trace.get('usage'),
            'model':trace.get('actual_model'),'requested_model':trace.get('requested_model'),
            'model_evidence':trace.get('model_evidence'),'skill_catalog':provider.metadata().get('skill_catalog')}

