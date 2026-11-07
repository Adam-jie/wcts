"""Lifecycle-owned previews of accepted TaskBoard candidate hashes."""
from contextlib import asynccontextmanager
from pathlib import Path
import re
import socket
import threading

from fastapi import APIRouter, HTTPException
from .application_tools import start_taskboard
from .package_tools import _stop_tree
from .project_eval import _mapping_sha256
from .project_tools import files_under, get_project


class ApplicationPreviews:
    def __init__(self, settings, runs):
        self.settings, self.runs = settings, runs
        self.lock = threading.RLock(); self.processes = {}

    def accepted(self, identifier):
        if not re.fullmatch('[a-f0-9]{32}', identifier): raise HTTPException(404, '运行不存在')
        run = self.runs.snapshot(identifier)
        if run.get('status') != 'completed' or not run.get('summary', {}).get('accepted'):
            raise HTTPException(409, '请先完成补全与验收，再启动程序')
        project = get_project(self.settings, run['project_id'])
        if project['adapter'] != 'python_application': raise HTTPException(422, '本入口用于任务管理系统；其他项目请使用各自运行入口')
        candidate = self.settings.artifacts / identifier / 'workspace/candidate'
        if _mapping_sha256(files_under(candidate)) != run['tasks'][0]['candidate_sha256']:
            raise HTTPException(409, '验收后的源码发生变化，未启动程序')
        return run, project, candidate

    def start(self, identifier):
        with self.lock:
            run, project, candidate = self.accepted(identifier)
            existing = self.processes.get(identifier)
            if existing and existing['process'].poll() is None:
                return {'url': existing['url'], 'running': True,
                        'candidate_sha256': run['tasks'][0]['candidate_sha256']}
            port = None
            for value in (8774, 8773, 8772, 8771, 8770):
                try:
                    with socket.socket() as bound: bound.bind(('127.0.0.1', value))
                    port = value; break
                except OSError: continue
            if port is None: raise HTTPException(409, '演示端口均被其他程序占用，请关闭一个旧演示后重试')
            directory = self.settings.artifacts / identifier / 'preview-live'
            try: process, url, _ = start_taskboard(project['python'], candidate, directory / 'preview.sqlite3', directory, port=port)
            except (ValueError, OSError, TimeoutError): raise HTTPException(409, '程序启动失败，请查看本次运行的 preview-live/server.log') from None
            self.processes[identifier] = {'process': process, 'url': url}
            return {'url': url, 'running': True, 'candidate_sha256': run['tasks'][0]['candidate_sha256']}

    def close(self):
        with self.lock:
            for item in self.processes.values():
                if item['process'].poll() is None: _stop_tree(item['process'])
            self.processes.clear()


def attach_application_previews(app, settings, runs):
    manager, router = ApplicationPreviews(settings, runs), APIRouter()
    @router.post('/api/project-runs/{identifier}/preview')
    def start(identifier: str): return manager.start(identifier)
    app.include_router(router)
    original = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with original(application):
            try: yield
            finally: manager.close()
    app.router.lifespan_context = lifespan
    app.state.application_previews = manager
