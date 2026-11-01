import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
import pytest

from testfix.api import create_app
from testfix.config import Settings
from testfix import web_live


def client_for(tmp_path, monkeypatch):
    (tmp_path / 'web').mkdir(exist_ok=True)
    async def unavailable(*args):
        return False
    monkeypatch.setattr(web_live, '_bounded_probe', unavailable)
    return TestClient(create_app(Settings(root=tmp_path)))


def test_state_is_evidence_only_and_cannot_replace_urls_or_claim_server_ready(tmp_path, monkeypatch):
    folder = tmp_path / 'experiments/web-live/uptime-kuma'
    folder.mkdir(parents=True)
    (folder / 'status.json').write_text(json.dumps({
        'reference_url': 'http://external.example', 'readiness': {'reference': True},
        'status': 'completed', 'source_commit': 'a'*40,
        'model': {'requested_model': 'gpt-5.6-sol'},
        'tests': [{'id': 'pending-test', 'label': '未执行', 'status': {'passed': True}}],
        'notes': ['api_key=sk-fakeABCDEFGHIJKLMNOPQRST'],
        'private_credentials': 'do-not-expose',
    }), encoding='utf-8')
    (folder / 'tester-receipt.json').write_text(json.dumps({'status': 'completed', 'actual_model': None}), encoding='utf-8')
    response = client_for(tmp_path, monkeypatch).get('/api/web-live')
    assert response.status_code == 200
    project = response.json()['projects'][0]
    assert project['reference_url'] == 'http://127.0.0.1:8770'
    assert project['readiness'] == {'reference': False, 'candidate': False}
    assert project['tests'][0]['status'] == 'pending'
    assert project['model']['actual_model'] is None
    assert project['model']['completed_calls'] == 1
    assert 'do-not-expose' not in response.text and 'sk-fake' not in response.text


def test_log_alias_allowlist_tail_and_redaction(tmp_path, monkeypatch):
    folder = tmp_path / 'experiments/web-live/uptime-kuma'
    folder.mkdir(parents=True)
    (folder / 'install.log').write_text('x'*18000+'\nAuthorization: Bearer private-token\npassword=hidden\n', encoding='utf-8')
    client = client_for(tmp_path, monkeypatch)
    value = client.get('/api/web-live/uptime-kuma/logs?name=install').json()
    assert value['truncated'] and len(value['text']) <= 16384
    assert 'private-token' not in value['text'] and 'hidden' not in value['text']
    assert client.get('/api/web-live/uptime-kuma/logs?name=../../.env').status_code == 404
    assert client.get('/api/web-live/unknown/logs').status_code == 404


def test_completed_calls_include_fixed_native_ui_case_without_double_counting_aliases(tmp_path, monkeypatch):
    folder = tmp_path / 'experiments/web-live/paperless-ngx'
    folder.mkdir(parents=True)
    for name in ['tester-receipt.json', 'repair-receipt.json', 'developer-receipt.json',
                 'review-receipt.json', 'ui-tester-receipt.json', 'ui-repair-receipt.json',
                 'ui-review-receipt.json', 'arbitrary-receipt.json']:
        (folder / name).write_text(json.dumps({'status': 'completed', 'actual_model': None}), encoding='utf-8')
    project = client_for(tmp_path, monkeypatch).get('/api/web-live').json()['projects'][1]
    assert project['model']['completed_calls'] == 6
    assert project['model']['actual_model'] is None


def test_malformed_status_and_missing_log_never_mean_passed(tmp_path, monkeypatch):
    folder = tmp_path / 'experiments/web-live/paperless-ngx'
    folder.mkdir(parents=True)
    (folder / 'status.json').write_text('{unfinished', encoding='utf-8')
    client = client_for(tmp_path, monkeypatch)
    assert client.get('/api/web-live').json()['projects'][1]['status'] == 'blocked'
    assert client.get('/api/web-live/paperless-ngx/logs?name=tests').json()['text'] == '尚无执行日志'


def test_complete_quoted_secrets_and_json_authorization_are_redacted():
    value = web_live._redact('{"password":"alpha beta", "Authorization":"Bearer demo-token"}')
    assert 'alpha' not in value and 'beta' not in value and 'demo-token' not in value


def test_tail_cannot_expose_secret_when_its_field_prefix_is_cut(tmp_path, monkeypatch):
    folder = tmp_path / 'experiments/web-live/uptime-kuma'
    folder.mkdir(parents=True)
    (folder / 'install.log').write_text('password="'+'private-fragment '*2000+'"', encoding='utf-8')
    value = client_for(tmp_path, monkeypatch).get('/api/web-live/uptime-kuma/logs?name=install').json()
    assert value['truncated'] and 'private-fragment' not in value['text']


def test_multiline_and_incomplete_secrets_redacted_before_tailing(tmp_path, monkeypatch):
    folder = tmp_path / 'experiments/web-live/uptime-kuma'
    folder.mkdir(parents=True)
    log = folder / 'install.log'
    client = client_for(tmp_path, monkeypatch)
    for ending in ['"\nnormal event', '', '\\']:
        log.write_text('password="'+('private-fragment\n'*2000)+ending, encoding='utf-8')
        value = client.get('/api/web-live/uptime-kuma/logs?name=install').json()
        assert 'private-fragment' not in value['text']


@pytest.mark.parametrize('redirect', [False, True])
def test_probe_requires_correct_project_html_and_rejects_external_redirect(redirect):
    def handler(request):
        if redirect:
            return httpx.Response(302, headers={'Location': 'http://external.example/login'})
        return httpx.Response(200, text='<title>unrelated service</title>', headers={'Content-Type':'text/html'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await web_live._bounded_probe(client, 'http://127.0.0.1:8770', 'uptime kuma')
    assert asyncio.run(run()) is False


def test_json_with_project_marker_is_not_a_ready_html_page():
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={'title':'uptime kuma'}))
        async with httpx.AsyncClient(transport=transport) as client:
            return await web_live._bounded_probe(client, 'http://127.0.0.1:8770', 'uptime kuma')
    assert asyncio.run(run()) is False


def test_ready_application_keeps_sameorigin_frame_protection():
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text='<title>Uptime Kuma</title>',
                       headers={'Content-Type': 'text/html', 'X-Frame-Options': 'SAMEORIGIN'}))
        policies = {}
        async with httpx.AsyncClient(transport=transport) as client:
            ready = await web_live._bounded_probe(client, 'http://127.0.0.1:8770', 'uptime kuma', policies)
        return ready, policies
    ready, policies = asyncio.run(run())
    assert ready is True
    assert policies == {'http://127.0.0.1:8770': False}


def test_web_testing_csp_limits_frames_to_fixed_project_ports(tmp_path, monkeypatch):
    app_client = client_for(tmp_path, monkeypatch)
    bundle = tmp_path / 'web/hardware-app'
    bundle.mkdir()
    (bundle / 'web-testing.html').write_text('<meta name="style-nonce" content="__TESTFIX_STYLE_NONCE__">', encoding='utf-8')
    response = app_client.get('/web-testing')
    assert response.status_code == 200
    policy = response.headers['Content-Security-Policy']
    assert 'frame-src http://127.0.0.1:8770' in policy
    assert "frame-ancestors 'none'" in policy and 'unsafe-inline' not in policy
    assert '__TESTFIX_STYLE_NONCE__' not in response.text
    assert 'frame-src' not in app_client.get('/api/web-live').headers['Content-Security-Policy']
