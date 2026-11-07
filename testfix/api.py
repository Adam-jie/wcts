"""Local-only control plane; server never modifies an imported source repository."""
from __future__ import annotations

import copy
import asyncio
import hashlib
import json
import mimetypes
import re
import threading
import uuid
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import __version__
from .config import CODEX_PROJECT_MODELS, DEFAULT_CODEX_MODEL, Settings, load_settings
from .orchestrator import execute_run
from .reports import now, save_run
from .runner import docker_available
from .tasks import catalog, select_tasks
from .fea_lab import FEAProbeRequest


def _owner_alive(identifier) -> bool:
    if type(identifier) is not int or identifier<=0:
        return False
    if os.name=='nt':
        import ctypes
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
        api.OpenProcess.restype=ctypes.c_void_p
        api.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
        api.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=api.OpenProcess(0x1000,False,identifier)
        if not handle:
            return ctypes.get_last_error()==5
        try:
            status=ctypes.c_uint32()
            return bool(api.GetExitCodeProcess(handle,ctypes.byref(status)) and status.value==259)
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(identifier,0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False


class RunRequest(BaseModel):
    task_ids: list[str] = Field(min_length=1, max_length=20)
    provider: str = "replay"
    runner: str = "process"
    max_rounds: int = Field(default=2, ge=1, le=5)


class ProjectBudgetRequest(BaseModel):
    wall_seconds: int = Field(default=4500, ge=60)
    token_limit: int = Field(default=800000, ge=5000)
    model_slots: int = Field(default=1, ge=1, le=8)
    cpu_slots: int = Field(default=2, ge=1, le=8)


class ProjectSearchRequest(BaseModel):
    mode: str = 'forest'
    trees: int = Field(default=3, ge=1)
    iterations: int = Field(default=6, ge=1)
    width: int = Field(default=2, ge=1)
    depth: int = Field(default=3, ge=1)
    exploration: float = Field(default=1.4142135623730951, ge=0, allow_inf_nan=False)
    min_successes: int = Field(default=3, ge=1)
    quality_attempts: int = Field(default=6, ge=1)

    @field_validator('mode')
    @classmethod
    def search_mode(cls, value):
        if value not in {'mcts','forest'}:
            raise ValueError('Search mode must be mcts or forest')
        return value


class ProjectRunRequest(BaseModel):
    project_id: str = "Project/flaskr"
    intake_id: str | None = Field(default=None, pattern='^[a-f0-9]{32}$')
    intake_revision: int | None = Field(default=None, ge=0)
    provider: str = "codex_cli"
    model: str = Field(default=DEFAULT_CODEX_MODEL, min_length=1, max_length=160)
    max_rounds: int = Field(default=2, ge=1, le=5)
    budget: ProjectBudgetRequest = Field(default_factory=ProjectBudgetRequest)
    strategy: str = "balanced"
    collaboration_request: str | None = Field(default=None, max_length=20_000)
    search: ProjectSearchRequest | None = None

    @field_validator('collaboration_request')
    @classmethod
    def normalize_collaboration_request(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError('Collaboration request must not be empty')
        return value


class LabProbeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    mode: str = 'csv'

    @field_validator('mode')
    @classmethod
    def allowed_mode(cls, value):
        if value not in {'csv', 'print', 'csv_head_tail'}:
            raise ValueError('Only fixed CSV, CSV head/tail, and print probes are supported')
        return value


class RunStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.lock = threading.RLock()
        self.runs = {}
        self.listeners = {}
        # Ownership belongs to this executor, even when another app uses our PID.
        self._owned_run_ids: set[str] = set()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="testfix-supervisor")
        self.refresh_external_runs()

    def refresh_external_runs(self):
        """Observe CLI/peer executors without replacing our live callback state."""
        with self.lock:
            for path in self.settings.artifacts.glob("*/report.json"):
                identifier = path.parent.name
                current = self.runs.get(identifier)
                if identifier in self._owned_run_ids or current is not None and current.get("status") not in {"queued", "running"}:
                    continue
                try:
                    run = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(run, dict) or run.get("id") != identifier or not isinstance(run.get("status"), str):
                        continue
                    if run["status"] in {"queued", "running"} and not _owner_alive(run.get("execution_owner_pid")):
                        # The owner may have published its final atomic report just
                        # before exiting. Re-read after observing death first.
                        latest = json.loads(path.read_text(encoding="utf-8"))
                        if not isinstance(latest, dict) or latest.get("id") != identifier or not isinstance(latest.get("status"), str):
                            continue
                        run = latest
                        if run["status"] in {"queued", "running"} and not _owner_alive(run.get("execution_owner_pid")):
                            run.update(status="interrupted", error="Local service restarted during this run")
                            save_run(path.parent, run)
                    self.runs[identifier] = run
                except (ValueError, KeyError, OSError):
                    # An atomic writer will publish a readable report on the next
                    # poll; a partial/unreadable report never unblocks execution.
                    continue

    def snapshot(self, run_id):
        with self.lock:
            self.refresh_external_runs()
            if run_id not in self.runs:
                raise HTTPException(404, "Run not found")
            return copy.deepcopy(self.runs[run_id])

    def subscribe(self, run_id, loop):
        with self.lock:
            snapshot = self.snapshot(run_id)
            queue = asyncio.Queue()
            self.listeners.setdefault(run_id, []).append((loop, queue))
            return snapshot, queue

    def unsubscribe(self, run_id, queue):
        with self.lock:
            listeners = self.listeners.get(run_id, [])
            self.listeners[run_id] = [item for item in listeners if item[1] is not queue]
            if not self.listeners[run_id]:
                self.listeners.pop(run_id, None)

    def publish(self, value):
        with self.lock:
            snapshot = copy.deepcopy(value)
            self.runs[value['id']] = snapshot
            for loop, queue in self.listeners.get(value['id'], []):
                loop.call_soon_threadsafe(queue.put_nowait, snapshot)

    def submit(self, request: RunRequest):
        with self.lock:
            self.refresh_external_runs()
            if any(value["status"] in {"queued", "running"} for value in self.runs.values()):
                raise HTTPException(409, "A run is already active; wait for completion")
            run = {"id": uuid.uuid4().hex, "created_at": now(), "status": "queued", **request.model_dump(),
                   "execution_owner_pid":os.getpid(),
                   "events": [], "tasks": [], "summary": {"total": len(request.task_ids), "accepted": 0, "regressions": 0, "duration_s": 0}}
            self.runs[run["id"]] = copy.deepcopy(run)
            save_run(self.settings.artifacts / run["id"], run)
            self._owned_run_ids.add(run["id"])

        def persist(value):
            self.publish(value)

        self.pool.submit(execute_run, run, self.settings, persist)
        return {"id": run["id"], "status": "queued"}

    def submit_project(self, request: ProjectRunRequest):
        from .project_workflow import execute_project_run
        with self.lock:
            self.refresh_external_runs()
            if any(value["status"] in {"queued", "running"} for value in self.runs.values()):
                raise HTTPException(409, "A run is already active; wait for completion")
            run = {"id":uuid.uuid4().hex,"created_at":now(),"status":"queued","kind":"autonomous_project",
                   "execution_owner_pid":os.getpid(),
                   "runner":"project_process",**request.model_dump(),"events":[],"tasks":[],
                   "summary":{"total":1,"accepted":0,"regressions":0,"duration_s":0}}
            self.runs[run["id"]] = copy.deepcopy(run)
            save_run(self.settings.artifacts/run["id"],run)
            self._owned_run_ids.add(run["id"])
        def persist(value):
            self.publish(value)
        self.pool.submit(execute_project_run,run,self.settings,persist)
        return {"id":run["id"],"status":"queued"}


def create_app(settings: Settings | None = None) -> FastAPI:
    # Initialize the numerical backend before serving requests. On Windows a
    # first NumPy/OR-Tools import in a worker can hold the native loader lock
    # while AnyIO's event-loop thread waits for another worker to start.
    # Eager initialization keeps that DLL load out of concurrent HTTP workers.
    from . import scheduling  # noqa: F401

    settings = settings or load_settings()
    app = FastAPI(title="TestFix-Agent", version=__version__)
    store = RunStore(settings)
    app.state.store = store
    from .application_preview import attach_application_previews
    attach_application_previews(app, settings, store)
    from .wctp_workbench import attach_wctp_tests
    attach_wctp_tests(app, settings, store)
    from .feature_testing import attach_feature_tests
    attach_feature_tests(app, settings, store)
    probe_lock = threading.Lock()
    # Windows registry can associate .js with text/plain; nosniff then blocks it.
    mimetypes.init()
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    app.mount("/static", StaticFiles(directory=settings.root / "web"), name="static")
    from .hardware import create_hardware_router
    from .hardware_planner import create_hardware_planner_router, planning_provenance
    app.include_router(create_hardware_router(settings, lambda request: planning_provenance(settings, request)))
    app.include_router(create_hardware_planner_router(settings))
    from .web_live import create_web_live_router
    app.include_router(create_web_live_router(settings.root))
    from .intake import create_intake_router
    app.include_router(create_intake_router(settings))
    from .test_library import create_test_library_router
    app.include_router(create_test_library_router(settings))

    @app.middleware("http")
    async def local_origin_guard(request: Request, call_next):
        # Prevent a public web page from triggering local execution/API costs via CSRF.
        host = request.headers.get("host", "").split(":")[0]
        origin = request.headers.get("origin")
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return PlainTextResponse("Local host only", status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            from urllib.parse import urlsplit
            parsed = urlsplit(origin)
            if parsed.netloc != request.headers.get("host") or parsed.scheme not in {"http", "https"}:
                return PlainTextResponse("Cross-origin request rejected", status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        if request.url.path == '/web-testing':
            response.headers['Content-Security-Policy'] += "; frame-src http://127.0.0.1:8770 http://127.0.0.1:8771 http://127.0.0.1:8772 http://127.0.0.1:8773"
        nonce = getattr(request.state, 'style_nonce', None)
        if nonce:
            response.headers['Content-Security-Policy'] = response.headers['Content-Security-Policy'].replace(
                "style-src 'self'", "style-src 'self' 'nonce-" + nonce + "'")
        return response

    @app.get("/")
    def homepage():
        return FileResponse(settings.root / "web" / "index.html")

    @app.get('/lab')
    def visual_lab():
        return FileResponse(settings.root/'web/lab/index.html')

    @app.get('/explorer')
    def architecture_explorer():
        return FileResponse(settings.root/'web/explorer/index.html')

    @app.get('/engineering')
    def engineering_workbench(request: Request):
        if not request.query_params.get('run'):
            return RedirectResponse('/implementation', status_code=307)
        from .frontend import engineering_document
        return engineering_document(settings.root, request)

    @app.get('/engineering/flow')
    def engineering_flow(request: Request):
        from .frontend import engineering_document
        return engineering_document(settings.root, request)

    @app.get('/fea')
    def fea_visual_lab():
        return FileResponse(settings.root/'web/fea/index.html')

    @app.get('/hardware')
    def hardware_lab(request: Request):
        from .frontend import hardware_document
        return hardware_document(settings.root, request)

    @app.get('/web-testing')
    def web_testing_lab(request: Request):
        from .frontend import web_testing_document
        return web_testing_document(settings.root, request)

    @app.get('/api/fea/demo')
    def fea_visual_demo():
        from .fea_lab import demo
        try:
            return demo(settings)
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(409, 'Fixed SQLGlot evidence unavailable: ' + str(exc)) from None

    @app.post('/api/fea/probe')
    def fea_visual_probe(body: FEAProbeRequest):
        from .fea_lab import probe
        if not probe_lock.acquire(blocking=False):
            raise HTTPException(429, 'A local probe or retest is already running')
        try:
            return probe(settings, body)
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from None
        finally:
            probe_lock.release()

    @app.post('/api/fea/retest')
    def fea_visual_retest():
        from .fea_lab import retest
        if not probe_lock.acquire(blocking=False):
            raise HTTPException(429, 'A local probe or retest is already running')
        try:
            return retest(settings)
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from None
        finally:
            probe_lock.release()

    @app.get('/api/lab/bootstrap')
    def lab_bootstrap():
        from .lab import bootstrap
        with store.lock:
            store.refresh_external_runs()
            values = copy.deepcopy(list(store.runs.values()))
        try:
            return bootstrap(settings, values)
        except (OSError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get('/api/lab/runs/{run_id}')
    def lab_run(run_id: str):
        from .lab import project_run
        try:
            return project_run(settings, store.snapshot(run_id))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get('/api/lab/guide')
    def lab_guide():
        path = settings.root/'docs/contest-demo-sources.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'status': 'not_available'}

    @app.get('/api/lab/datasets/{dataset_id}/tasks')
    def lab_dataset_tasks(dataset_id: str, limit: int = Query(default=24, ge=1, le=50)):
        from .benchmark_data import list_benchmark_tasks
        try:
            return list_benchmark_tasks(settings, dataset_id, limit)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post('/api/lab/runs/{run_id}/probe')
    def lab_probe(run_id: str, body: LabProbeRequest):
        from .lab_probe import execute_probe
        if not probe_lock.acquire(blocking=False):
            raise HTTPException(429, 'An interactive probe is running; wait for its result')
        try:
            return execute_probe(settings, store.snapshot(run_id), body.text, body.mode)
        except (ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
            raise HTTPException(409, str(exc)) from None
        finally:
            probe_lock.release()

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__, "providers": settings.providers(), "docker_available": docker_available(),
                "default_provider": settings.provider, "default_runner": settings.runner}

    @app.post("/api/agentarts/check")
    def agentarts_check():
        from .agentarts_probe import check_runtime
        from .project_models import ProjectModelError
        try:
            return check_runtime(settings)
        except ProjectModelError as exc:
            raise HTTPException(502, {"code": exc.code, "message": str(exc)}) from None

    @app.get("/api/tasks")
    def tasks():
        try:
            return {"tasks": [{"id": task.id, "title": task.title, "source": task.source, "kind": task.kind} for task in catalog(settings)]}
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/projects")
    def projects():
        from .project_models import project_provider_status
        from .project_tools import project_catalog
        from .project_workflow import DEFAULT_BUDGET, default_budget
        try:
            values = project_catalog(settings)
            return {"projects":[{**{key:item[key] for key in ("id","title","source","adapter")},
                                 "default_budget":default_budget(item['adapter'])} for item in values],
                    "providers":project_provider_status(settings),
                    "defaults":{"project_id":values[0]['id'] if values else None,"provider":"codex_cli","model":DEFAULT_CODEX_MODEL,
                                "max_rounds":2,"budget":DEFAULT_BUDGET,"strategy":"balanced"}}
        except (ValueError,OSError) as exc:
            raise HTTPException(400,str(exc)) from None

    @app.get('/implementation')
    def implementation_page():
        return FileResponse(settings.root / 'web/implementation/index.html')

    @app.get('/api/implementation-projects/{project_id:path}')
    def implementation_project(project_id: str):
        from .project_tools import get_project
        try:
            project = get_project(settings, project_id)
            return {key: project.get(key) for key in ('id', 'title', 'adapter', 'requirements', 'clarifications',
                'public_runtime', 'intake_id', 'intake_revision')}
        except (ValueError, OSError): raise HTTPException(404, '工程不存在或材料不完整') from None

    @app.get('/api/project-runs/{run_id}/patch')
    def project_patch(run_id: str):
        run = store.snapshot(run_id)
        if run.get('kind') != 'autonomous_project' or not re.fullmatch('[a-f0-9]{32}', run_id):
            raise HTTPException(404, '工程运行不存在')
        patch = settings.artifacts / run_id / 'patch.diff'
        # A running task may already have applied a candidate but not reached final reporting.
        if patch.is_file(): return {'diff': patch.read_text(encoding='utf-8')}
        from .project_tools import get_project
        from .project_eval import _diff
        candidate = settings.artifacts / run_id / 'workspace/candidate'
        baseline = settings.artifacts / run_id / 'workspace/baseline'
        if candidate.is_dir() and baseline.is_dir():
            project = get_project(settings, run['project_id'])
            return {'diff': _diff(baseline, candidate, set(project['allowed_changes'])), 'final': False}
        return {'diff': ''}

    @app.get('/api/project-specialists')
    def project_specialists(project_id: str = Query(default='Project/flaskr', max_length=160)):
        from .specialists import preview_project
        try:
            return preview_project(settings, project_id)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/project-runs",status_code=202)
    def submit_project(body: ProjectRunRequest):
        from .project_models import ProjectModelError, project_provider_status
        from .project_tools import get_project
        from .project_workflow import default_budget
        try:
            selected_project=get_project(settings,body.project_id)
            if selected_project.get('intake_id'):
                from .intake import IntakeStore
                task = IntakeStore(settings.root).read(selected_project['intake_id'])
                if (body.intake_id != task['id'] or body.intake_revision != task.get('revision', 0)
                        or body.intake_revision != selected_project['intake_revision']):
                    raise ValueError('任务资料版本已变化，请重新选择项目后开始修改')
            if (body.collaboration_request is not None or body.search) and 'model_slots' not in body.budget.model_fields_set:
                body.budget.model_slots=2
            if body.search and body.search.mode == 'mcts' and body.search.trees != 1:
                raise ValueError('Single-tree MCTS requires trees=1')
            if 'wall_seconds' not in body.budget.model_fields_set:
                body.budget.wall_seconds=(36000 if body.search else 7200 if body.collaboration_request is not None else
                                          default_budget(selected_project['adapter'])['wall_seconds'])
            if body.search and 'token_limit' not in body.budget.model_fields_set:
                body.budget.token_limit=4_000_000
            if body.strategy not in {"balanced","fast","thorough"}:
                raise ValueError("Unknown strategy")
            if body.provider not in {item['id'] for item in project_provider_status(settings) if item['configured']}:
                raise ValueError("Selected real model transport is not available; no replay fallback is permitted")
            if body.provider == 'agentarts':
                from .agentarts_probe import check_runtime
                requested = body.model if 'model' in body.model_fields_set else None
                body.model = check_runtime(settings, expected_model=requested)['model']
            if body.provider == 'codex_cli' and body.model not in CODEX_PROJECT_MODELS:
                raise ValueError('请选择受支持的 Codex 工程模型')
        except (ValueError,OSError,ProjectModelError) as exc:
            raise HTTPException(400,str(exc)) from None
        return store.submit_project(body)

    @app.post("/api/runs", status_code=202)
    def submit(body: RunRequest):
        if len(set(body.task_ids)) != len(body.task_ids):
            raise HTTPException(400, "Duplicate task IDs")
        configured = {item["id"]: item["configured"] for item in settings.providers()}
        if body.provider not in configured or not configured[body.provider]:
            raise HTTPException(400, "Provider is not configured")
        if body.runner not in {"process", "docker"}:
            raise HTTPException(400, "Invalid runner")
        if body.runner == "docker" and not docker_available():
            raise HTTPException(400, "Docker is unavailable")
        try:
            select_tasks(settings, body.task_ids)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from None
        return store.submit(body)

    @app.get("/api/runs")
    def runs():
        with store.lock:
            store.refresh_external_runs()
            values = sorted(store.runs.values(), key=lambda value: value["created_at"], reverse=True)
            return {"runs": [{key: value.get(key) for key in ("id", "status", "created_at", "provider", "runner", "kind", "summary", "project_id", "intake_id", "intake_revision")} for value in values]}

    @app.get("/api/project-runs/{run_id}/download")
    def project_download(run_id: str):
        value = store.snapshot(run_id)
        if value.get('kind')!='autonomous_project' or value.get('status')!='completed' or not re.fullmatch(r'[a-f0-9]{32}',run_id):
            raise HTTPException(409,'Only an accepted completed project has a deliverable')
        path = settings.artifacts/run_id/'completed-project.zip'
        if not path.is_file():
            raise HTTPException(404,'Project deliverable is missing')
        expected=value.get('download',{}).get('sha256')
        if not isinstance(expected,str) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise HTTPException(409,'Project deliverable changed after acceptance')
        return FileResponse(path,filename=f'testfix-project-{run_id}.zip',media_type='application/zip')

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str):
        return store.snapshot(run_id)

    @app.get('/api/runs/{run_id}/stream')
    async def run_stream(run_id: str):
        snapshot, queue = store.subscribe(run_id, asyncio.get_running_loop())

        async def events():
            current = snapshot
            try:
                while True:
                    yield 'data: ' + json.dumps(current, ensure_ascii=False, separators=(',', ':')) + '\n\n'
                    if current['status'] not in {'queued', 'running'}:
                        break
                    while True:
                        try:
                            current = await asyncio.wait_for(queue.get(), timeout=1.6)
                            break
                        except asyncio.TimeoutError:
                            if run_id not in store._owned_run_ids:
                                latest = store.snapshot(run_id)
                                if latest != current:
                                    current = latest
                                    break
                            yield ': keepalive\n\n'
            finally:
                store.unsubscribe(run_id, queue)

        return StreamingResponse(events(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    @app.get("/api/runs/{run_id}/report")
    def report(run_id: str, format: str = "json"):
        store.snapshot(run_id)
        if format not in {"json", "md"} or not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise HTTPException(400, "Invalid report request")
        path = settings.artifacts / run_id / f"report.{format}"
        return FileResponse(path, filename=f"testfix-{run_id}.{format}", media_type="application/json" if format == "json" else "text/markdown")

    return app

