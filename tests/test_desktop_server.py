import json
import os
import subprocess
import sys
import threading

from fastapi.testclient import TestClient

from testfix.api import create_app
from testfix.config import Settings
from testfix.desktop_server import attach_desktop, valid_command, watch_parent


def test_shutdown_requires_exact_session_command():
    secret = 'a' * 64
    assert valid_command(json.dumps({'action': 'shutdown', 'token': secret}), secret)
    for value in [None, [], {}, {'action': 'shutdown', 'token': 'b'*64}, {'action': 'exec', 'token': secret}, {'action': 'shutdown', 'token': secret, 'command': 'other'}, {'action': 'shutdown', 'token': 3}]:
        assert not valid_command(json.dumps(value), secret)
    assert not valid_command('invalid json', secret)


def test_handshake_is_authenticated_and_endpoint_follows_lifespan(tmp_path):
    (tmp_path/'web').mkdir()
    app = create_app(Settings(root=tmp_path))
    endpoint = tmp_path/'endpoint.json'
    attach_desktop(app, token='a'*64, session='current-session', root=tmp_path, endpoint=endpoint, origin='http://127.0.0.1:41234')
    with TestClient(app) as client:
        assert endpoint.exists()
        assert 'token' not in json.loads(endpoint.read_text())
        assert client.get('/api/desktop/status').status_code == 403
        assert client.get('/api/desktop/status', headers={'X-TestFix-Desktop': 'bad'}).status_code == 403
        app.state.store.runs['peer'] = {'status': 'running'}
        app.state.store.runs['own'] = {'status': 'running'}
        app.state.store._owned_run_ids.add('own')
        data = client.get('/api/desktop/status', headers={'X-TestFix-Desktop': 'a'*64}).json()
        assert data['session'] == 'current-session'
        assert data['workspace'] == str(tmp_path)
        assert data['pid'] == os.getpid()
        assert data['active_runs'] == 2 and data['owned_active_runs'] == 1
    assert not endpoint.exists()


def test_parent_handle_notifies_after_the_owned_parent_exits():
    parent = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    stopped = threading.Event()
    observer = threading.Thread(target=watch_parent, args=(parent.pid, stopped.set), daemon=True)
    observer.start()
    try:
        assert not stopped.wait(.15)
        parent.terminate()
        parent.wait(timeout=5)
        assert stopped.wait(3)
    finally:
        if parent.poll() is None:
            parent.terminate()
            parent.wait(timeout=5)


def test_shutdown_rejects_inflight_and_newly_queued_work_atomically(tmp_path):
    (tmp_path/'web').mkdir()
    app = create_app(Settings(root=tmp_path))
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()

    @app.post('/api/paused-mutation')
    def paused_mutation():
        entered.set()
        assert release.wait(5)
        with app.state.store.lock:
            app.state.store.runs['new'] = {'status': 'queued'}
            app.state.store._owned_run_ids.add('new')
        return {'accepted': True}

    attach_desktop(app, token='a'*64, session='race', root=tmp_path, endpoint=tmp_path/'endpoint.json', origin='http://127.0.0.1:41234')
    app.state.desktop_lifecycle.on_exit = stopped.set
    headers = {'X-TestFix-Desktop': 'a'*64}
    results = []
    with TestClient(app) as client:
        assert client.post('/api/desktop/shutdown').status_code == 403
        worker = threading.Thread(target=lambda: results.append(client.post('/api/paused-mutation')))
        worker.start()
        try:
            assert entered.wait(3)
            assert client.get('/api/desktop/status', headers=headers).json()['inflight_requests'] == 1
            assert client.post('/api/desktop/shutdown', headers=headers).status_code == 409
            assert not stopped.is_set()
        finally:
            release.set()
            worker.join(5)
        assert results[0].status_code == 200
        assert client.post('/api/desktop/shutdown', headers=headers).status_code == 409
        with app.state.store.lock:
            app.state.store.runs['new']['status'] = 'completed'
        assert client.post('/api/desktop/shutdown', headers=headers).status_code == 200
        assert stopped.is_set()
        assert client.post('/api/paused-mutation').status_code == 503
        assert not client.get('/api/desktop/status', headers=headers).json()['accepting']


def test_parent_loss_closes_admission_and_drains_existing_work(tmp_path):
    from testfix.api import RunStore
    from testfix.desktop_server import DesktopLifecycle

    store = RunStore(Settings(root=tmp_path))
    lifecycle = DesktopLifecycle(store)
    stopped = threading.Event()
    lifecycle.on_exit = stopped.set
    assert lifecycle.begin()
    worker = threading.Thread(target=lifecycle.drain, daemon=True)
    worker.start()
    assert not stopped.wait(.15)
    assert not lifecycle.begin()
    with store.lock:
        store.runs['own'] = {'status': 'running'}
        store._owned_run_ids.add('own')
    lifecycle.finish()
    assert not stopped.wait(.15)
    with store.lock:
        store.runs['own']['status'] = 'completed'
    assert stopped.wait(2)
    worker.join(2)
