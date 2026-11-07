"""Exercise real catalogue imports, immutable bundles and the normal task store."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import zipfile

from fastapi.testclient import TestClient
import pytest

from testfix.api import create_app
from testfix.config import Settings
from testfix.benchmark_data import SWE_REVISION

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def library(tmp_path):
    (tmp_path / 'web').mkdir()
    target = tmp_path / 'data/test-library'
    target.mkdir(parents=True)
    bundle = target / 'public/bundles/example.zip'
    bundle.parent.mkdir(parents=True)
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('main.c', 'int main(void) { return 1; }\n')
        archive.writestr('LICENSE', 'MIT\n')
    entries = []
    for domain, operation in [('mcu', 'completion'), ('software', 'modification'), ('mpu', 'development')]:
        entries.append({'id': domain + '-' + operation, 'dataset': 'project-cases', 'domain': domain,
                        'operation': operation, 'title': domain + '工程', 'description': '恢复采样与数据传输',
                        'acceptance': '循环100次、原有回归通过', 'environment': '指定工具链',
                        'source': {'repository': 'https://github.com/example/firmware', 'commit': 'a' * 40, 'license': 'MIT'},
                        'origin': 'controlled_missing_file', 'status': 'source_ready', 'file_count': 2,
                        'bundle': 'public/bundles/example.zip', 'bundle_sha256': hashlib.sha256(bundle.read_bytes()).hexdigest(),
                        'execution': '需要工具链'})
    entries.append({'id': 'idea', 'dataset': 'requirements', 'domain': 'software', 'operation': 'development',
                    'title': '需求任务', 'description': '开发设备台账', 'acceptance': '重启保存', 'environment': '待确认',
                    'source': {}, 'origin': 'authored_requirement', 'status': 'requirements_ready', 'execution': '待开发'})
    (target / 'index.json').write_text(json.dumps({'entries': entries}), encoding='utf-8')
    return TestClient(create_app(Settings(root=tmp_path))), tmp_path, bundle


def test_filter_and_paginate_full_inputs_without_publishing_internal_bundle_paths(library):
    client, _, _ = library
    result = client.get('/api/test-library?domain=mcu&operation=completion&q=采样').json()
    assert result['total'] == 1 and result['entries'][0]['id'] == 'mcu-completion'
    assert result['entries'][0]['has_bundle'] is True
    assert 'bundle' not in result['entries'][0] and 'bundle_sha256' not in result['entries'][0]
    assert client.get('/api/test-library?offset=3&limit=1').json()['entries'][0]['id'] == 'idea'
    assert client.get('/api/test-library?domain=unknown').status_code == 422


def test_import_creates_editable_independent_task_with_actual_source_attachment(library):
    client, root, bundle = library
    before = bundle.read_bytes()
    response = client.post('/api/test-library/mcu-completion/import', json={'description': '恢复采样，并按200次循环核验'})
    assert response.status_code == 201, response.text
    imported = response.json()
    assert imported['status'] == 'prepared'
    task = client.get('/api/intakes/' + imported['task_id']).json()
    assert task['description'] == '恢复采样，并按200次循环核验'
    assert task['test_library']['domain'] == 'mcu' and task['analysis']['source_available']
    assert 'execution_url' not in task['test_library']
    assert '需要工具链' in task['analysis']['message']
    attachment = root / 'artifacts/intakes' / task['id'] / 'files' / task['files'][0]['id']
    assert attachment.read_bytes() == before == bundle.read_bytes()
    attachment.write_bytes(b'changed candidate upload')
    assert bundle.read_bytes() == before
    listing = client.get('/api/intakes').json()
    assert listing['total'] == 1 and listing['tasks'][0]['execution_ready'] is False


def test_corrupt_bundle_is_rejected_before_task_creation(library):
    client, _, bundle = library
    bundle.write_bytes(b'corrupted')
    assert client.post('/api/test-library/mcu-completion/import', json={}).status_code == 409
    assert client.get('/api/intakes').json()['total'] == 0


def test_requirements_only_import_and_missing_ids(library):
    client, _, _ = library
    response = client.post('/api/test-library/idea/import', json={'title': '我的需求'})
    task = client.get('/api/intakes/' + response.json()['task_id']).json()
    assert task['mode'] == 'idea' and task['title'] == '我的需求'
    assert task['status'] == 'prepared' and task['files'] == []
    assert client.post('/api/test-library/missing/import', json={}).status_code == 404
    assert client.post('/api/test-library/idea/import', json={'title': '  '}).status_code == 422


def test_swe_import_keeps_public_issue_and_pinned_download_url_without_grading_data(library):
    client, root, _ = library
    source = root / 'data/benchmarks/swe-bench-lite' / SWE_REVISION
    (source / 'public').mkdir(parents=True)
    raw = json.dumps({'instance_id': 'example__app-1', 'repo': 'example/app', 'base_commit': 'a' * 40,
                      'problem_statement': 'Fix search', 'patch': 'DO_NOT_EXPOSE_GOLD'}).encode()
    (source / 'public/tasks.jsonl').write_bytes(raw)
    (source / 'manifest.json').write_text(json.dumps({'id': 'swe-bench-lite', 'revision': SWE_REVISION,
        'task_count': 1, 'files': {'public/tasks.jsonl': hashlib.sha256(raw).hexdigest()}}))
    result = client.get('/api/test-library?dataset=swe-bench-lite')
    assert 'DO_NOT_EXPOSE_GOLD' not in result.text
    entry = result.json()['entries'][0]
    response = client.post('/api/test-library/' + entry['id'] + '/import', json={})
    task = client.get('/api/intakes/' + response.json()['task_id']).json()
    assert task['status'] == 'draft' and task['url'].endswith('/archive/' + 'a' * 40 + '.zip')
    assert 'DO_NOT_EXPOSE_GOLD' not in json.dumps(task)


def test_humaneval_inputs_have_no_reference_solution_or_hidden_tests(library):
    client, root, _ = library
    if not (ROOT / 'data/humaneval/manifest.json').is_file():
        pytest.skip('Collect the pinned HumanEval fixture to validate its actual inputs')
    shutil.copytree(ROOT / 'data/humaneval', root / 'data/humaneval')
    listing = client.get('/api/test-library?dataset=humaneval').json()
    assert listing['total'] == 164
    assert 'canonical_solution' not in json.dumps(listing)
    response = client.post('/api/test-library/humaneval-0/import', json={})
    task = client.get('/api/intakes/' + response.json()['task_id']).json()
    attachment = root / 'artifacts/intakes' / task['id'] / 'files' / task['files'][0]['id']
    with zipfile.ZipFile(attachment) as archive:
        assert archive.namelist() == ['solution.py']
        assert b'check(candidate)' not in archive.read('solution.py')
        compile(archive.read('solution.py'), 'solution.py', 'exec')


def test_bundled_collection_covers_domains_and_operations_and_omits_missing_files():
    source = ROOT / 'data/test-library'
    if not (source / 'index.json').is_file():
        pytest.skip('Run scripts/collect_test_library.py to validate collected source bundles')
    entries = json.loads((source / 'index.json').read_text(encoding='utf-8'))['entries']
    projects = [entry for entry in entries if entry['dataset'] == 'project-cases']
    assert {(entry['domain'], entry['operation']) for entry in projects} == {
        (domain, operation) for domain in ('web', 'software', 'mcu', 'mpu')
        for operation in ('completion', 'modification', 'development')}
    for entry in projects:
        path = source / entry['bundle']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry['bundle_sha256']
        with zipfile.ZipFile(path) as archive:
            assert len(archive.namelist()) == entry['file_count']
            assert any('license' in name.lower() or 'copying' in name.lower() for name in archive.namelist())
            if entry['id'] == 'freertos-completion': assert 'list.c' not in archive.namelist()
            if entry['id'] == 'libmodbus-completion': assert 'src/modbus-data.c' not in archive.namelist()
    rtl = [entry for entry in entries if entry['dataset'] == 'verilogeval']
    assert len(rtl) == 312 and {entry['operation'] for entry in rtl} == {'completion', 'development'}
    assert all('_ref.sv' not in entry['description'] and '_test.sv' not in entry['description'] for entry in rtl)


def test_registered_fixed_contract_link_is_removed_when_requirements_are_edited(library):
    client, root, _ = library
    index = root / 'data/test-library/index.json'
    data = json.loads(index.read_text(encoding='utf-8'))
    entry = dict(data['entries'][1], id='rich-cli-modification')
    data['entries'].append(entry)
    index.write_text(json.dumps(data), encoding='utf-8')
    registry = root / 'data/projects/registered'
    registry.mkdir(parents=True)
    (registry / 'rich-cli.json').write_text(json.dumps({'source': {'commit': 'a' * 40}, 'python': sys.executable}))
    for overrides, expected in [({}, True), ({'description': '新增另外一个功能'}, False)]:
        imported = client.post('/api/test-library/rich-cli-modification/import', json=overrides).json()
        task = client.get('/api/intakes/' + imported['task_id']).json()
        assert ('execution_url' in task['test_library']) is expected
