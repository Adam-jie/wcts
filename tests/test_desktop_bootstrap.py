"""Exercise cold desktop startup without model calls or executable projects."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx


def test_cold_desktop_serves_concurrent_bootstrap_and_static_requests(tmp_path):
    (tmp_path / 'web').mkdir()
    (tmp_path / 'web/index.html').write_text('<h1>Cold bootstrap</h1>', encoding='utf-8')
    (tmp_path / 'web/console.css').write_text('body{color:#abc}', encoding='utf-8')
    fixture = tmp_path / 'experiments/flaskr'
    fixture.mkdir(parents=True)
    (fixture / 'source.json').write_text(json.dumps({'repository': 'https://example.invalid/fixture'}), encoding='utf-8')
    (fixture / 'requirements.json').write_text(json.dumps({
        'requirements': [], 'clarifications': [], 'allowed_changes': [],
    }), encoding='utf-8')
    token = 'a' * 64
    endpoint = tmp_path / 'endpoint.json'
    environment = {**os.environ, 'TESTFIX_DESKTOP_TOKEN': token, 'PYTHONUTF8': '1'}
    # No user's .env or API/model credentials are loaded from this fixture root.
    environment.pop('TESTFIX_LEGACY_ENV', None)
    with (tmp_path / 'engine.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([
            sys.executable, '-X', 'utf8', '-m', 'testfix.desktop_server',
            '--root', str(tmp_path), '--endpoint', str(endpoint),
            '--session', 'cold-bootstrap', '--parent-pid', str(os.getpid()),
        ], cwd=Path(__file__).resolve().parents[1], env=environment,
            stdin=subprocess.PIPE, stdout=log, stderr=log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            deadline = time.monotonic() + 20
            while not endpoint.exists():
                assert process.poll() is None, 'Desktop engine exited before readiness'
                assert time.monotonic() < deadline, 'Desktop engine readiness timed out'
                time.sleep(.05)
            origin = json.loads(endpoint.read_text(encoding='utf-8'))['origin']
            routes = ['/api/health', '/api/tasks', '/api/runs', '/api/projects', '/', '/static/console.css']
            def read(route):
                response = httpx.get(origin + route, timeout=20, trust_env=False)
                assert response.status_code == 200, (route, response.status_code)
                return response
            with ThreadPoolExecutor(max_workers=len(routes)) as pool:
                responses = list(pool.map(read, routes))
            assert responses[3].json()['projects'][0]['id'] == 'Project/flaskr'
            assert 'Cold bootstrap' in responses[4].text
            assert 'text/css' in responses[5].headers['content-type']
            status = httpx.get(origin + '/api/desktop/status',
                               headers={'X-TestFix-Desktop': token}, timeout=5, trust_env=False)
            assert status.json()['owned_active_runs'] == 0
            process.stdin.close()
            process.wait(timeout=8)
            assert process.returncode == 0
            assert not endpoint.exists()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
