import json
import socket
import threading
import time

import httpx
import uvicorn

from testfix.api import create_app
from testfix.config import Settings


def test_owned_run_stream_pushes_each_checkpoint_and_closes(tmp_path):
    (tmp_path / 'web').mkdir()
    app = create_app(Settings(root=tmp_path))
    store = app.state.store
    run = {'id': 'run1', 'status': 'running', 'events': [], 'created_at': '2026-01-01'}
    store.runs[run['id']] = run.copy()
    store._owned_run_ids.add(run['id'])
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        with httpx.stream('GET', f'http://127.0.0.1:{port}/api/runs/run1/stream', timeout=5) as response:
            assert response.status_code == 200
            lines = iter(response.iter_lines())
            initial = json.loads(next(lines).removeprefix('data: '))
            assert initial['events'] == []
            started = time.monotonic()
            for name in ['主控派工', '员工交接']:
                run['events'].append({'message': name})
                store.publish(run)
            received = []
            while len(received) < 2:
                line = next(lines)
                if line.startswith('data: '):
                    received.append(json.loads(line[6:]))
            assert [item['events'][-1]['message'] for item in received] == ['主控派工', '员工交接']
            assert time.monotonic() - started < 1.5
            run['status'] = 'completed'
            store.publish(run)
            assert any('"completed"' in line for line in lines)
        deadline = time.monotonic() + 2
        while store.listeners and time.monotonic() < deadline:
            time.sleep(.01)
        assert not store.listeners
        with httpx.stream('GET', f'http://127.0.0.1:{port}/api/runs/run1/stream', timeout=5) as response:
            records = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith('data: ')]
        assert len(records) == 1 and records[0]['status'] == 'completed'
        assert [event['message'] for event in records[0]['events']] == ['主控派工', '员工交接']
        assert httpx.get(f'http://127.0.0.1:{port}/api/runs/missing/stream').status_code == 404
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
