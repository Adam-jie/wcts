"""An owned, ephemeral loopback engine for the Windows desktop client."""
from __future__ import annotations

import argparse
import ctypes
import hmac
import json
import os
import socket
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, HTTPException, Request
from starlette.responses import JSONResponse

from .api import create_app
from .config import load_settings


def valid_command(line: str, token: str) -> bool:
    try:
        value = json.loads(line)
        return isinstance(value, dict) and set(value) == {'action', 'token'} and value['action'] == 'shutdown' and isinstance(value['token'], str) and hmac.compare_digest(value['token'], token)
    except (ValueError, TypeError):
        return False


class DesktopLifecycle:
    """One admission lock covers requests and the transition to shutdown."""

    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.accepting = True
        self.inflight = 0
        self.on_exit = lambda: None

    def begin(self):
        with self.lock:
            if not self.accepting:
                return False
            self.inflight += 1
            return True

    def finish(self):
        with self.lock:
            self.inflight -= 1

    def snapshot(self):
        with self.lock, self.store.lock:
            self.store.refresh_external_runs()
            active = sum(run.get('status') in {'queued', 'running'} for run in self.store.runs.values())
            owned = sum(self.store.runs.get(identifier, {}).get('status') in {'queued', 'running'} for identifier in self.store._owned_run_ids)
            return {'active_runs': active, 'owned_active_runs': owned, 'inflight_requests': self.inflight, 'accepting': self.accepting}

    def shutdown_if_idle(self):
        with self.lock:
            snapshot = self.snapshot()
            if snapshot['owned_active_runs'] or snapshot['inflight_requests']:
                return False
            self.accepting = False
            return True

    def drain(self):
        # Parent loss/EOF closes admission but lets already admitted work finish.
        with self.lock:
            self.accepting = False
        while not self.shutdown_if_idle():
            time.sleep(.1)
        self.on_exit()


class DesktopAdmissionMiddleware:
    def __init__(self, app, lifecycle):
        self.app = app
        self.lifecycle = lifecycle

    async def __call__(self, scope, receive, send):
        mutation = scope['type'] == 'http' and scope['method'] not in {'GET', 'HEAD', 'OPTIONS'} and scope['path'] != '/api/desktop/shutdown'
        if not mutation:
            return await self.app(scope, receive, send)
        if not self.lifecycle.begin():
            return await JSONResponse({'detail': 'Desktop engine is stopping'}, status_code=503)(scope, receive, send)
        try:
            # Includes response background work, not just response headers.
            await self.app(scope, receive, send)
        finally:
            self.lifecycle.finish()


def attach_desktop(app, *, token: str, session: str, root: Path, endpoint: Path, origin: str):
    lifecycle = DesktopLifecycle(app.state.store)
    app.state.desktop_lifecycle = lifecycle
    app.add_middleware(DesktopAdmissionMiddleware, lifecycle=lifecycle)

    def authorize(request):
        provided = request.headers.get('X-TestFix-Desktop', '')
        if not hmac.compare_digest(provided.encode('utf-8'), token.encode('ascii')):
            raise HTTPException(403, 'Desktop session required')

    @app.get('/api/desktop/status', include_in_schema=False)
    def status(request: Request):
        authorize(request)
        return {'status': 'ready', 'session': session, 'pid': os.getpid(), 'workspace': str(root), **lifecycle.snapshot()}

    @app.post('/api/desktop/shutdown', include_in_schema=False)
    def shutdown(request: Request, background_tasks: BackgroundTasks):
        authorize(request)
        if not lifecycle.shutdown_if_idle():
            raise HTTPException(409, 'Desktop work is still active')
        background_tasks.add_task(lifecycle.on_exit)
        return {'status': 'stopping', 'session': session}

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application):
            info = {'origin': origin, 'session': session, 'pid': os.getpid()}
            temporary = endpoint.with_suffix('.tmp')
            temporary.write_text(json.dumps(info), encoding='utf-8')
            os.replace(temporary, endpoint)
            try:
                yield
            finally:
                endpoint.unlink(missing_ok=True)

    app.router.lifespan_context = lifespan
    return app


def watch_parent(parent_pid: int, stop):
    """Use a Windows process handle so a recycled PID cannot keep us alive."""
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, parent_pid)
        if not handle:
            stop()
            return
        try:
            kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
            stop()
        finally:
            kernel.CloseHandle(handle)
    else:
        while True:
            try:
                os.kill(parent_pid, 0)
            except OSError:
                stop()
                return
            time.sleep(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--endpoint', required=True, type=Path)
    parser.add_argument('--session', required=True)
    parser.add_argument('--parent-pid', required=True, type=int)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    token = os.environ.pop('TESTFIX_DESKTOP_TOKEN', '')
    if len(token) != 64 or args.parent_pid <= 0:
        parser.error('A parent-owned desktop session is required')
    endpoint = args.endpoint.resolve()
    endpoint.parent.mkdir(parents=True, exist_ok=True)
    if endpoint.exists():
        parser.error('Desktop endpoint already exists')

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    listener.set_inheritable(False)
    port = listener.getsockname()[1]
    app = attach_desktop(create_app(load_settings(root)), token=token, session=args.session, root=root, endpoint=endpoint, origin=f'http://127.0.0.1:{port}')
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='info', access_log=False))

    def stop():
        server.should_exit = True

    app.state.desktop_lifecycle.on_exit = stop

    def commands():
        for line in iter(lambda: sys.stdin.readline(1024), ''):
            if valid_command(line, token):
                app.state.desktop_lifecycle.drain()
                return
        app.state.desktop_lifecycle.drain()

    threading.Thread(target=commands, daemon=True).start()
    threading.Thread(target=watch_parent, args=(args.parent_pid, app.state.desktop_lifecycle.drain), daemon=True).start()
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()
        endpoint.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
