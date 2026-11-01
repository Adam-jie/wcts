"""Task intake persists original inputs without running uploaded code."""
import hashlib
import io
import json
import tarfile
import zipfile

import pytest
from fastapi.testclient import TestClient

from testfix.api import create_app
from testfix.config import Settings
from testfix import intake


@pytest.fixture
def client(tmp_path):
    (tmp_path / 'web').mkdir()
    with TestClient(create_app(Settings(root=tmp_path))) as client:
        yield client


def create(client, mode='idea', **extra):
    response = client.post('/api/intakes', json={'mode': mode, 'title': '文档搜索', 'description': '支持中文搜索\n保留原有上传功能', 'acceptance': '中文标题匹配\n空结果提示', **extra})
    assert response.status_code == 201
    return response.json()['id']


def test_idea_retains_verbatim_requirements_and_survives_restart(client, tmp_path):
    identifier = create(client)
    assert client.post(f'/api/intakes/{identifier}/prepare').status_code == 202
    result = client.get(f'/api/intakes/{identifier}').json()
    assert result['status'] == 'prepared'
    assert result['analysis']['method'] == 'input_inspection'
    assert result['analysis']['planning_status'] == 'pending'
    assert result['analysis']['requirements'] == ['支持中文搜索', '保留原有上传功能']
    assert result['analysis']['acceptance_criteria'] == ['中文标题匹配', '空结果提示']
    assert result['analysis']['next_stage'] == 'requirements_design'
    assert client.get(f'/api/intakes/{identifier}/handoff').json() == result
    restarted = TestClient(create_app(Settings(root=tmp_path)))
    assert restarted.get(f'/api/intakes/{identifier}').json() == result
    assert restarted.get('/api/intakes').json()['tasks'][0]['id'] == identifier
    assert client.post(f'/api/intakes/{identifier}/prepare').status_code == 409


def test_uploaded_source_archive_has_original_bytes_and_inventory(client, tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('repo/package.json', '{"name":"demo"}')
        archive.writestr('repo/src/main.js', 'throw new Error("broken");')
        archive.writestr('repo/README.md', '要求：支持搜索')
    identifier = create(client, 'upload')
    raw = data.getvalue()
    response = client.put(f'/api/intakes/{identifier}/files?filename=broken.zip', content=raw)
    assert response.status_code == 201
    metadata = response.json()
    assert metadata['sha256'] == hashlib.sha256(raw).hexdigest()
    stored = tmp_path / 'artifacts/intakes' / identifier / 'files' / metadata['id']
    assert stored.read_bytes() == raw
    original = client.get(f'/api/intakes/{identifier}/files/{metadata["id"]}')
    assert original.content == raw
    assert original.headers['content-disposition'].startswith('attachment;')
    assert client.get(f'/api/intakes/{identifier}/files/not-an-id').status_code == 404
    assert client.post(f'/api/intakes/{identifier}/prepare').status_code == 202
    task = client.get(f'/api/intakes/{identifier}').json()
    inspection = task['files'][0]['inspection']
    assert inspection['source_count'] == 1
    assert inspection['manifests'] == ['repo/package.json']
    assert task['analysis']['next_stage'] == 'source_analysis'
    assert not (stored.parent / 'repo').exists()  # Archive contents never execute or extract.


def test_installer_is_saved_without_claiming_source_or_execution(client):
    identifier = create(client, 'upload')
    response = client.put(f'/api/intakes/{identifier}/files?filename=broken.exe', content=b'MZ\x00broken')
    assert response.status_code == 201
    client.post(f'/api/intakes/{identifier}/prepare')
    task = client.get(f'/api/intakes/{identifier}').json()
    assert task['status'] == 'prepared'
    assert task['files'][0]['inspection']['kind'] == 'installer'
    assert task['analysis']['source_available'] is False
    assert task['analysis']['next_stage'] == 'source_required'


@pytest.mark.parametrize('filename', ['../bad.exe', 'C:\\x.exe', 'CON', 'x.'])
def test_unsafe_upload_names_rejected(client, filename):
    identifier = create(client, 'upload')
    assert client.put(f'/api/intakes/{identifier}/files', params={'filename': filename}, content=b'data').status_code == 422
    assert client.get(f'/api/intakes/{identifier}').json()['files'] == []


def test_empty_and_oversized_uploads_leave_no_partial_file(client, monkeypatch, tmp_path):
    identifier = create(client, 'upload')
    assert client.put(f'/api/intakes/{identifier}/files?filename=a.zip', content=b'').status_code == 422
    monkeypatch.setattr(intake, 'MAX_BYTES', 3)
    assert client.put(f'/api/intakes/{identifier}/files?filename=a.zip', content=b'1234').status_code == 413
    assert list((tmp_path / 'artifacts/intakes' / identifier / 'files').iterdir()) == []
    assert client.post(f'/api/intakes/{identifier}/prepare').status_code == 422


@pytest.mark.parametrize('name', ['../escape.py', '/abs.py', 'C:/bad.py'])
def test_archive_traversal_is_failed_not_prepared(client, name):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive: archive.writestr(name, 'print(1)')
    identifier = create(client, 'upload')
    client.put(f'/api/intakes/{identifier}/files?filename=bad.zip', content=data.getvalue())
    client.post(f'/api/intakes/{identifier}/prepare')
    task = client.get(f'/api/intakes/{identifier}').json()
    assert task['status'] == 'failed'
    assert '越界' in task['error']
    assert client.get(f'/api/intakes/{identifier}/handoff').status_code == 409


def test_tar_source_and_symlink_rejection(tmp_path):
    archive_path = tmp_path / 'fixture'
    with tarfile.open(archive_path, 'w:gz') as archive:
        entry = tarfile.TarInfo('repo/app.py'); entry.size = 8
        archive.addfile(entry, io.BytesIO(b'print(1)'))
    assert intake.inspect_file(archive_path, 'repo.tar.gz')['source_count'] == 1
    with tarfile.open(archive_path, 'w') as archive:
        entry = tarfile.TarInfo('link'); entry.type = tarfile.SYMTYPE; entry.linkname = '/etc/passwd'; archive.addfile(entry)
    with pytest.raises(ValueError, match='链接'): intake.inspect_file(archive_path, 'bad.tar')


def test_url_download_becomes_hashed_attachment(client, monkeypatch):
    def fixture_download(url, target):
        assert url == 'https://example.org/source'
        target.write_bytes(b'print(1)')
        return {'name': 'main.py', 'size': 8, 'sha256': hashlib.sha256(b'print(1)').hexdigest(), 'url': url}
    monkeypatch.setattr(intake, 'download', fixture_download)
    identifier = create(client, 'url', url='https://example.org/source')
    client.post(f'/api/intakes/{identifier}/prepare')
    task = client.get(f'/api/intakes/{identifier}').json()
    assert task['status'] == 'prepared'
    assert task['files'][0]['inspection']['source_available']
    assert task['files'][0]['url'] == 'https://example.org/source'


def test_webpage_is_read_as_data_not_executed(tmp_path):
    page = tmp_path / 'page'
    page.write_text('<title>软件下载</title><a href="/app.zip">zip</a><script>alert(1)</script>', encoding='utf-8')
    result = intake.inspect_file(page, 'index.html', webpage=True)
    assert result['kind'] == 'webpage'
    assert result['page_title'] == '软件下载'
    assert result['download_links'] == ['/app.zip']
    assert not result['source_available']
    assert intake.inspect_file(page, 'index.html')['source_available']


def test_invalid_zip_is_reported_as_failed_material(tmp_path):
    file = tmp_path / 'archive'
    file.write_bytes(b'not a zip')
    with pytest.raises(ValueError, match='格式无效'): intake.inspect_file(file, 'broken.zip')


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'https://u:p@example.org/a', 'http://127.0.0.1/a', 'http://169.254.169.254/latest'])
def test_downloader_denies_nonpublic_and_credential_urls(url):
    with pytest.raises(ValueError): intake.public_target(url)


def test_redirect_rechecks_target_before_connecting(monkeypatch, tmp_path):
    connections = []
    monkeypatch.setattr(intake.socket, 'getaddrinfo', lambda host, port, **kwargs: [(2, 1, 6, '', ('93.184.216.34' if host == 'example.org' else '127.0.0.1', port))])
    class Response:
        status = 302
        def getheader(self, key): return 'http://internal.local/private' if key == 'Location' else None
    class Connection:
        def __init__(self, address, port, **kwargs): connections.append(address)
        def request(self, *args, **kwargs): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(intake.http.client, 'HTTPConnection', Connection)
    with pytest.raises(ValueError, match='公网'): intake.download('http://example.org/start', tmp_path / 'target')
    assert connections == ['93.184.216.34']


def test_failed_download_is_persisted_and_partial_removed(client, monkeypatch, tmp_path):
    def fail(url, target):
        target.write_bytes(b'incomplete')
        raise ValueError('远端下载失败')
    monkeypatch.setattr(intake, 'download', fail)
    identifier = create(client, 'url', url='https://example.org/a.zip')
    client.post(f'/api/intakes/{identifier}/prepare')
    task = client.get(f'/api/intakes/{identifier}').json()
    assert task['status'] == 'failed'
    assert task['error'] == '远端下载失败'
    assert not (tmp_path / 'artifacts/intakes' / identifier / 'download.part').exists()


def test_validation_and_cross_origin_rejected(client):
    assert client.post('/api/intakes', json={'mode': 'idea', 'title': ' ', 'description': 'x'}).status_code == 422
    assert client.post('/api/intakes', json={'mode': 'url', 'title': 'x', 'description': 'x'}).status_code == 422
    assert client.post('/api/intakes', json={'mode': 'idea', 'title': 'x', 'description': 'x'}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.get('/api/intakes/not-an-id').status_code == 404


def test_upload_during_processing_is_rejected(client):
    identifier = create(client, 'idea')
    client.post(f'/api/intakes/{identifier}/prepare')
    assert client.put(f'/api/intakes/{identifier}/files?filename=a.py', content=b'print(1)').status_code == 409


def test_repository_url_uses_github_default_branch():
    assert intake.repository_url('https://github.com/pallets/flask') == 'https://api.github.com/repos/pallets/flask/zipball'
    assert intake.repository_url('https://github.com/a/b/releases/download/v1/a.zip').endswith('/releases/download/v1/a.zip')
