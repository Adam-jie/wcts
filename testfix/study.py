"""Run paired, fixed-budget studies using the existing project workflow."""
from __future__ import annotations

import json
from pathlib import Path
import time
import uuid

from pydantic import BaseModel, ConfigDict, Field

from .project_tools import files_under, get_project
from .project_workflow import execute_project_run
from .reports import _publish_text, now


class StudyCase(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=120)
    project_id: str = Field(min_length=1, max_length=160)
    request: str = Field(min_length=1, max_length=20000)


class StudyVariant(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)
    model_slots: int = Field(ge=1, le=8, strict=True)


class StudySpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)
    provider: str = Field(pattern='^(codex_cli|openai|ollama|agentarts)$')
    model: str = Field(min_length=1, max_length=160)
    wall_seconds: int = Field(ge=60, strict=True)
    token_limit: int = Field(ge=5000, strict=True)
    cpu_slots: int = Field(default=2, ge=1, le=8, strict=True)
    max_rounds: int = Field(default=2, ge=1, le=5, strict=True)
    repetitions: int = Field(default=1, ge=1, le=20, strict=True)
    cases: list[StudyCase] = Field(min_length=1, max_length=100)
    variants: list[StudyVariant] = Field(min_length=1, max_length=8)


def prepare_study(spec: StudySpec, settings):
    """Freeze the registered inputs before any model call."""
    ids = [case.id for case in spec.cases]
    names = [variant.name for variant in spec.variants]
    if len(ids) != len(set(ids)) or len(names) != len(set(names)):
        raise ValueError('案例 ID 和配置名称必须唯一')
    cases = []
    for case in spec.cases:
        project = get_project(settings, case.project_id)
        cases.append({'id': case.id, 'project_id': case.project_id, 'request': case.request,
            'source_files': files_under(Path(project['baseline'])),
            'acceptance_files': files_under(Path(project['acceptance'])),
            'regression_files': files_under(Path(project['package_config']['regression']))
                                if project.get('adapter') == 'python_package' else None})
    jobs = []
    for case in cases:
        for repetition in range(1, spec.repetitions + 1):
            variants = spec.variants if repetition % 2 else list(reversed(spec.variants))
            for variant in variants:
                jobs.append({'case_id': case['id'], 'project_id': case['project_id'], 'variant': variant.name,
                             'repetition': repetition, 'model_slots': variant.model_slots,
                             'run_id': uuid.uuid4().hex, 'status': 'pending'})
    return {'schema': 'testfix.study.v1', 'id': uuid.uuid4().hex, 'created_at': now(),
            'status': 'prepared', 'spec': spec.model_dump(), 'cases': cases, 'jobs': jobs}


def save_study(directory: Path, study: dict):
    directory.mkdir(parents=True, exist_ok=True)
    _publish_text(directory / 'study.json', json.dumps(study, ensure_ascii=False, indent=2))


def execute_study(study, settings):
    directory = settings.artifacts / 'studies' / study['id']
    spec = study['spec']
    study.update(status='running', started_at=now())
    save_study(directory, study)
    started = time.monotonic()
    for job in study['jobs']:
        case = next(case for case in study['cases'] if case['id'] == job['case_id'])
        job.update(status='running', started_at=now())
        save_study(directory, study)
        run = {'id': job['run_id'], 'created_at': now(), 'project_id': job['project_id'],
               'provider': spec['provider'], 'model': spec['model'], 'max_rounds': spec['max_rounds'],
               'strategy': 'balanced', 'collaboration_request': case['request'], 'independent_once': True,
               'budget': {'wall_seconds': spec['wall_seconds'], 'token_limit': spec['token_limit'],
                          'cpu_slots': spec['cpu_slots'], 'model_slots': job['model_slots']}}
        result = execute_project_run(run, settings)
        task = result.get('tasks', [{}])[0] if result.get('tasks') else {}
        job.update(status=result['status'], error=result.get('error'),
                   accepted=result.get('summary', {}).get('accepted', 0),
                   regression_preserved=task.get('verdict', {}).get('regression_preserved'),
                   duration_s=result.get('summary', {}).get('duration_s'),
                   model_calls=len(result.get('model_calls', [])),
                   actual_models=sorted({c['actual_model'] for c in result.get('model_calls', []) if c.get('actual_model')}),
                   budget=result.get('workflow', {}).get('budget'), finished_at=now())
        save_study(directory, study)
    summary = {}
    for variant in spec['variants']:
        jobs = [job for job in study['jobs'] if job['variant'] == variant['name']]
        summary[variant['name']] = {'planned': len(jobs),
            'accepted': sum(job.get('accepted') == 1 for job in jobs),
            'failed_or_rejected': sum(job['status'] not in {'completed', 'pending', 'running'} for job in jobs),
            'model_calls': sum(job.get('model_calls', 0) for job in jobs)}
    study.update(status='completed', finished_at=now(), duration_s=round(time.monotonic()-started, 3), summary=summary)
    save_study(directory, study)
    return study
