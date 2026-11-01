import re

from fastapi.testclient import TestClient

from testfix.api import create_app
from testfix.config import Settings


def app_for(tmp_path, source=None):
    (tmp_path / 'web').mkdir(exist_ok=True)
    if source is not None:
        output = tmp_path / 'web/hardware-app'
        output.mkdir()
        (output / 'index.html').write_text(source, encoding='utf-8')
    return create_app(Settings(root=tmp_path))


def test_built_workbench_nonce_is_per_response_and_bound_to_style_policy(tmp_path):
    app = app_for(tmp_path, '<meta name="style-nonce" content="__TESTFIX_STYLE_NONCE__"><div id="root"></div>')
    client = TestClient(app)
    first, second = client.get('/hardware'), client.get('/hardware')
    assert first.status_code == second.status_code == 200
    first_nonce = re.search(r'content="([^"]+)"', first.text).group(1)
    second_nonce = re.search(r'content="([^"]+)"', second.text).group(1)
    assert first_nonce != second_nonce
    for response, nonce in [(first, first_nonce), (second, second_nonce)]:
        policy = response.headers['Content-Security-Policy']
        assert f"style-src 'self' 'nonce-{nonce}'" in policy
        assert "script-src 'self'" in policy
        assert 'unsafe-inline' not in policy and 'unsafe-eval' not in policy
        assert '__TESTFIX_STYLE_NONCE__' not in response.text
        assert response.headers['Cache-Control'] == 'no-store'
    assert 'nonce-' not in client.get('/api/hardware/capabilities').headers['Content-Security-Policy']


def test_missing_bundle_reports_build_requirement_without_old_ui_fallback(tmp_path):
    response = TestClient(app_for(tmp_path)).get('/hardware')
    assert response.status_code == 503
    assert 'pnpm build' in response.text


def test_stale_bundle_does_not_allow_unprotected_injected_styles(tmp_path):
    response = TestClient(app_for(tmp_path, '<div id="root"></div>')).get('/hardware')
    assert response.status_code == 503
    assert 'nonce-' not in response.headers['Content-Security-Policy']


def test_new_document_keeps_cross_origin_execution_guard(tmp_path):
    client = TestClient(app_for(tmp_path))
    response = client.post('/api/hardware/run', json={}, headers={'Origin':'https://unrelated.example'})
    assert response.status_code == 403
