"""Reviewable task-detail drafts over the existing bounded real-model transports."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import time
from typing import Literal
import uuid
import zipfile

from fastapi import BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import DEFAULT_CODEX_MODEL
from .project_models import ProjectModelProvider, _validate_schema, project_provider_status
from .reports import now

SKILL_PATH = Path(__file__).parent / 'intake_skills/task-details/SKILL.md'
FIELDS = ('title', 'description', 'acceptance', 'environment')
SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': [*FIELDS, 'assumptions', 'questions'], 'properties': {
        'title': {'type': 'string', 'minLength': 1, 'maxLength': 120, 'pattern': r'^[^\r\n]+$'},
        'description': {'type': 'string', 'minLength': 1, 'maxLength': 20000},
        'acceptance': {'type': 'string', 'maxLength': 12000},
        'environment': {'type': 'string', 'maxLength': 4000},
        'assumptions': {'type': 'array', 'maxItems': 12, 'items': {'type': 'string', 'maxLength': 1000}},
        'questions': {'type': 'array', 'maxItems': 12, 'items': {'type': 'string', 'maxLength': 1000}}}}


class DetailsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task_id: str | None = Field(default=None, pattern='^[a-f0-9]{32}$')
    revision: int | None = Field(default=None, ge=0)
    mode: Literal['upload', 'url', 'idea'] = 'idea'
    title: str = Field(default='', max_length=120)
    description: str = Field(default='', max_length=20000)
    acceptance: str = Field(default='', max_length=12000)
    environment: str = Field(default='', max_length=4000)
    url: str = Field(default='', max_length=2048)
    attachment_names: list[str] = Field(default_factory=list, max_length=20)
    prompt: str = Field(default='', max_length=6000)
    provider: Literal['codex_cli', 'openai', 'ollama', 'agentarts'] = 'codex_cli'
    parent_proposal_id: str | None = Field(default=None, pattern='^[a-f0-9]{32}$')
    answers: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode='after')
    def meaningful_input(self):
        if not any(s.strip() for s in (self.title, self.description, self.prompt)):
            raise ValueError('请先写一句想实现的功能或问题描述，再生成详细资料')
        if any(len(name) > 180 for name in self.attachment_names):
            raise ValueError('附件名称过长')
        if (self.task_id is None) != (self.revision is None):
            raise ValueError('编辑已有任务时须提供资料版本')
        if any(not answer.strip() or len(answer) > 4000 for answer in self.answers):
            raise ValueError('请填写每个问题的回答，每项不超过 4000 字')
        if bool(self.parent_proposal_id) != bool(self.answers):
            raise ValueError('继续澄清时需要原草稿和全部回答')
        return self


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class IntakeDetails:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.root = settings.artifacts / 'intake-proposals'
        self.active = set()

    def models(self):
        names = {'codex_cli': DEFAULT_CODEX_MODEL, 'openai': self.settings.model,
                 'ollama': self.settings.ollama_model, 'agentarts': self.settings.model}
        return [{**item, 'model': names[item['id']], 'configured': item['configured'] and bool(names[item['id']])}
                for item in project_provider_status(self.settings)]

    def path(self, identifier):
        if not re.fullmatch('[a-f0-9]{32}', identifier):
            raise HTTPException(404, 'AI 草稿不存在')
        return self.root / identifier / 'proposal.json'

    def save(self, proposal):
        path = self.path(proposal['id']); path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)

    def read(self, identifier):
        with self.store.lock:
            try: proposal = json.loads(self.path(identifier).read_text(encoding='utf-8'))
            except FileNotFoundError: raise HTTPException(404, 'AI 草稿不存在') from None
            if proposal['status'] == 'running' and identifier not in self.active:
                proposal.update(status='failed', error='本地引擎曾中断，请重新生成。原任务资料已保留。')
                self.save(proposal)
            return proposal

    def provenance(self, identifier, task_id, revision, fields):
        if not identifier:
            return {'details_source': 'manual', 'details_proposal_id': None}
        proposal = self.read(identifier)
        if proposal['status'] != 'ready' or proposal['task_id'] != task_id or proposal['base_revision'] != revision:
            raise HTTPException(409, 'AI 草稿未完成或不属于当前任务版本，请重新生成或切换自己填写')
        exact = all(fields.get(key, '').strip() == proposal['result'][key].strip() for key in FIELDS)
        return {'details_source': 'ai' if exact else 'ai_edited', 'details_proposal_id': identifier}

    def material_context(self, task):
        """Bounded README/manifest excerpts; no extraction, executable, test or .env content."""
        result, remaining = [], 16000
        for file in task['files']:
            item = {'name': file['name'], 'sha256': file['sha256'], 'inspection': file.get('inspection'), 'excerpts': []}
            result.append(item)
            if remaining <= 0 or file.get('inspection', {}).get('kind') != 'archive': continue
            path = self.store.directory(task['id']) / 'files' / file['id']
            def eligible(name):
                parts = PurePosixPath(name).parts
                base = PurePosixPath(name).name.lower()
                return (not any(p.startswith('.') or p.lower() in {'node_modules', 'vendor', 'tests', 'test', 'fixtures', '__pycache__'} for p in parts)
                        and (base in {'readme.md', 'readme.txt', 'readme', 'package.json', 'pyproject.toml', 'requirements.txt', 'cmakelists.txt', 'go.mod'}))
            try:
                if zipfile.is_zipfile(path):
                    with zipfile.ZipFile(path) as archive:
                        for entry in archive.infolist():
                            if remaining <= 0 or len(item['excerpts']) >= 4: break
                            if not eligible(entry.filename) or entry.file_size > 100000: continue
                            with archive.open(entry) as stream: raw = stream.read(min(6000, remaining))
                            remaining -= len(raw)
                            item['excerpts'].append({'path': entry.filename, 'text': raw.decode('utf-8', errors='replace')})
                elif tarfile.is_tarfile(path):
                    with tarfile.open(path) as archive:
                        for entry in archive:
                            if remaining <= 0 or len(item['excerpts']) >= 4: break
                            if not entry.isfile() or not eligible(entry.name) or entry.size > 100000: continue
                            stream = archive.extractfile(entry)
                            with stream: raw = stream.read(min(6000, remaining))
                            remaining -= len(raw)
                            item['excerpts'].append({'path': entry.name, 'text': raw.decode('utf-8', errors='replace')})
            except (ValueError, OSError, zipfile.BadZipFile, tarfile.TarError):
                item['excerpt_note'] = '文档摘录不可用，仅依据文件清单和用户描述生成'
        return result

    def start(self, body):
        model = next(item for item in self.models() if item['id'] == body.provider)
        if not model['configured']:
            raise HTTPException(422, '所选模型服务尚未配置或登录，不能生成 AI 草稿')
        with self.store.lock:
            materials = [{'name': name} for name in body.attachment_names]
            if body.task_id:
                task = self.store.read(body.task_id)
                if task.get('revision', 0) != body.revision or task['status'] == 'reading' or body.task_id in self.store.uploading:
                    raise HTTPException(409, '任务已更新或材料正在读取，请刷新详情后再生成')
                for identifier in self.active:
                    if self.read(identifier)['task_id'] == body.task_id:
                        raise HTTPException(409, '此任务已有正在生成的草稿，请等待完成')
                materials = self.material_context(task)
            context = {'user_request': body.prompt, 'input_mode': body.mode, 'task_title': body.title,
                       'problem_and_features': body.description, 'public_criteria': body.acceptance,
                       'declared_environment': body.environment, 'source_url': body.url, 'materials': materials}
            if body.parent_proposal_id:
                previous = self.read(body.parent_proposal_id)
                if (previous['status'] != 'ready' or previous['task_id'] != body.task_id
                        or previous['base_revision'] != body.revision):
                    raise HTTPException(409, '原草稿未完成或任务资料已更新，请重新生成')
                questions = previous['result']['questions']
                if not questions or len(body.answers) != len(questions):
                    raise HTTPException(422, '请逐项回答原草稿中的全部问题')
                context['clarification'] = {
                    'previous_request': previous['context']['user_request'],
                    'previous_result': previous['result'],
                    'answers': [{'question': question, 'answer': answer.strip()}
                                for question, answer in zip(questions, body.answers)],
                }
            if len(canonical(context).encode()) > 150000:
                raise HTTPException(422, '资料过长，请精简功能描述后再生成')
            identifier = uuid.uuid4().hex
            proposal = {'id': identifier, 'task_id': body.task_id, 'base_revision': body.revision,
                        'parent_proposal_id': body.parent_proposal_id,
                        'created_at': now(), 'status': 'running', 'provider': body.provider,
                        'requested_model': model['model'], 'actual_model': None, 'model_evidence': None,
                        'model_attempted': False, 'result': None, 'context': context, 'usage': None,
                        'skill_id': 'task-details', 'snapshot_sha256': hashlib.sha256(canonical(context).encode()).hexdigest()}
            self.active.add(identifier)
            try: self.save(proposal)
            except Exception:
                self.active.discard(identifier)
                raise
            return proposal

    def generate(self, identifier):
        proposal = self.read(identifier)
        started = time.monotonic()
        try:
            skill = SKILL_PATH.read_bytes()
            instructions = skill.decode('utf-8') + '\nReturn ONLY one JSON object matching this schema.\n' + canonical(SCHEMA)
            proposal.update(skill_sha256=hashlib.sha256(skill).hexdigest(),
                            prompt_sha256=hashlib.sha256(instructions.encode()).hexdigest())
            transport = ProjectModelProvider(proposal['provider'], self.settings, model=proposal['requested_model'],
                timeout_s=120, token_cap=3500, profile='python_package', artifact_dir=self.path(identifier).parent / 'transport')
            public = canonical(proposal['context'])
            if transport._scrub(public) != public:
                raise ValueError('资料含凭据，拒绝发送')
            proposal['model_attempted'] = True
            with self.store.lock: self.save(proposal)
            if proposal['provider'] == 'codex_cli':
                result, _raw, actual, evidence, usage = transport._invoke_cli(instructions, public, SCHEMA, 120)
            else:
                result, _raw, actual, usage = transport._invoke_http('task-details', instructions, proposal['context'], SCHEMA, 120, 3500)
                evidence = 'service_response' if actual else None
            proposal.update(actual_model=actual, model_evidence=evidence, usage=usage)
            if actual and actual != proposal['requested_model'] and not actual.startswith(proposal['requested_model'] + '-'):
                raise ValueError('服务返回的模型与请求不符')
            _validate_schema(result, SCHEMA)
            if not result['title'].strip() or not result['description'].strip():
                raise ValueError('模型返回了空的任务资料')
            if transport._scrub(canonical(result)) != canonical(result):
                raise ValueError('模型返回了凭据')
            if hashlib.sha256(SKILL_PATH.read_bytes()).hexdigest() != proposal['skill_sha256']:
                raise ValueError('生成期间 Skill 有变更')
            proposal.update(status='ready', result=result)
        except Exception as error:
            proposal.update(status='failed', error='AI 草稿生成失败，请检查模型连接或移除资料中的凭据后重试；也可以自己填写。',
                            error_code=getattr(error, 'code', 'generation_failed'))
        finally:
            proposal.update(duration_s=round(time.monotonic() - started, 3), completed_at=now())
            with self.store.lock:
                try: self.save(proposal)
                finally: self.active.discard(identifier)

    def add_routes(self, router):
        @router.get('/api/intake-models')
        def models(): return {'providers': self.models()}

        @router.get('/api/intake-proposals')
        def history(task_id: str = Query(pattern='^[a-f0-9]{32}$')):
            self.store.read(task_id)
            with self.store.lock:
                values = []
                for path in self.root.glob('*/proposal.json'):
                    try:
                        proposal = json.loads(path.read_text(encoding='utf-8'))
                        if proposal.get('task_id') != task_id: continue
                        proposal = self.read(proposal['id'])
                        values.append({key: proposal.get(key) for key in ('id', 'created_at', 'status', 'requested_model',
                            'actual_model', 'duration_s', 'base_revision', 'error')})
                    except (ValueError, OSError, KeyError): continue
                return {'proposals': sorted(values, key=lambda item: item['created_at'], reverse=True)[:30]}

        @router.post('/api/intake-proposals', status_code=202)
        def generate(body: DetailsRequest, background: BackgroundTasks):
            proposal = self.start(body)
            background.add_task(self.generate, proposal['id'])
            return proposal

        @router.get('/api/intake-proposals/{identifier}')
        def proposal(identifier: str): return self.read(identifier)
