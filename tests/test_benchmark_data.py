from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.collect_benchmarks import (
    SWE_PUBLIC_FIELDS, SWE_REVISION, collect, split_swe_rows,
    verify_collection, write_json, write_rows,
)
from testfix.benchmark_data import list_benchmark_datasets, list_benchmark_tasks
from testfix.config import Settings


def sample_row():
    return {'instance_id': 'sample__repo-1', 'repo': 'sample/repo',
            'base_commit': 'a' * 40, 'problem_statement': 'Public issue text',
            'version': '1.0', 'environment_setup_commit': 'b' * 40,
            'created_at': '2020-01-01T00:00:00Z',
            'patch': 'GOLD_SECRET_FIX', 'test_patch': 'GRADER_SECRET_TEST',
            'FAIL_TO_PASS': '["secret_test_name"]', 'PASS_TO_PASS': '[]',
            'hints_text': 'SECRET_SOLUTION_IN_ISSUE_COMMENTS',
            'unrecognized_future_field': 'PRIVATE_FUTURE_FIELD'}


def collection(tmp_path):
    directory = tmp_path / 'data/benchmarks/swe-bench-lite' / SWE_REVISION
    public, private = split_swe_rows([sample_row()])
    write_rows(directory / 'public/tasks.jsonl', public)
    write_rows(directory / 'evaluator-only/grading.jsonl', private)
    paths = ['public/tasks.jsonl', 'evaluator-only/grading.jsonl']
    manifest = {'id': 'swe-bench-lite', 'name': 'SWE-bench Lite', 'revision': SWE_REVISION,
                'task_count': 1, 'repository_count': 1, 'license': 'MIT',
                'evaluation_status': 'not_run', 'notes': ['DO_NOT_FORWARD_RAW_MANIFEST_NOTES'],
                'files': {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in paths}}
    write_json(directory / 'manifest.json', manifest)
    return directory


def test_public_rows_use_allowlist_and_exclude_reference_and_hints():
    public, private = split_swe_rows([sample_row()])
    assert set(public[0]) == set(SWE_PUBLIC_FIELDS)
    serialized = json.dumps(public)
    assert not any(word in serialized for word in ['GOLD_SECRET', 'GRADER_SECRET', 'secret_test_name', 'SECRET_SOLUTION', 'PRIVATE_FUTURE'])
    assert private[0]['patch'] == 'GOLD_SECRET_FIX'


def test_duplicate_or_unpinned_rows_fail():
    with pytest.raises(ValueError, match='unique'):
        split_swe_rows([sample_row(), sample_row()])
    row = sample_row()
    row['base_commit'] = 'main'
    with pytest.raises(ValueError, match='pinned'):
        split_swe_rows([row])


def test_catalogue_summary_does_not_read_or_expose_evaluator_files(tmp_path, monkeypatch):
    directory = collection(tmp_path)
    read_bytes = Path.read_bytes
    read_text = Path.read_text

    def guard_bytes(path, *args, **kwargs):
        if 'evaluator-only' in path.parts:
            raise AssertionError('Catalogue must never read reference data')
        return read_bytes(path, *args, **kwargs)

    def guard_text(path, *args, **kwargs):
        if 'evaluator-only' in path.parts:
            raise AssertionError('Catalogue must never read reference data')
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_bytes', guard_bytes)
    monkeypatch.setattr(Path, 'read_text', guard_text)
    summaries = list_benchmark_datasets(Settings(root=tmp_path))
    selected = next(row for row in summaries if row['id'] == 'swe-bench-lite')
    assert selected['status'] == 'collected'
    assert selected['task_count'] == 1
    assert selected['ready_project_id'] is None
    serialized = json.dumps(summaries)
    assert not any(word in serialized for word in ['GOLD_SECRET', 'GRADER_SECRET', 'secret_test_name', 'DO_NOT_FORWARD'])
    assert str(directory) not in serialized


def test_tampered_public_tasks_are_not_counted(tmp_path):
    directory = collection(tmp_path)
    (directory / 'public/tasks.jsonl').write_text('{}\n', encoding='utf-8')
    selected = next(row for row in list_benchmark_datasets(Settings(root=tmp_path)) if row['id'] == 'swe-bench-lite')
    assert selected['status'] == 'integrity_failed'
    assert selected['task_count'] == 0
    with pytest.raises(ValueError, match='changed'):
        verify_collection(directory)


def test_repeated_collection_validates_without_network_or_overwrite(tmp_path, monkeypatch):
    directory = collection(tmp_path)
    original = (directory / 'manifest.json').read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError('Existing evidence must not be downloaded again')

    monkeypatch.setattr('scripts.collect_benchmarks.download', forbidden)
    result = collect(tmp_path / 'data/benchmarks', 'swe-bench-lite')
    assert result['task_count'] == 1
    assert (directory / 'manifest.json').read_bytes() == original


def test_missing_data_is_not_claimed_collected(tmp_path):
    result = list_benchmark_datasets(Settings(root=tmp_path))
    assert all(row['task_count'] == 0 for row in result)
    assert all(row['ready_project_id'] is None for row in result)
    assert next(row for row in result if row['id'] == 'humaneval')['scope'] == 'function_completion_auxiliary'


def test_viewer_revision_mismatch_never_publishes_collection(tmp_path, monkeypatch):
    from scripts import collect_benchmarks as collector
    raw = b'upstream source'
    monkeypatch.setattr(collector, 'SWE_PARQUET_SHA256', collector.digest(raw))
    monkeypatch.setattr(collector, 'download', lambda *args, **kwargs: (raw, {'x-revision': 'other-revision'}))
    with pytest.raises(ValueError, match='revision'):
        collect(tmp_path, 'swe-bench-lite')
    assert not (tmp_path / 'swe-bench-lite' / SWE_REVISION).exists()


def test_wrong_upstream_archive_hash_never_publishes_collection(tmp_path, monkeypatch):
    monkeypatch.setattr('scripts.collect_benchmarks.download', lambda *args, **kwargs: (b'wrong archive', {}))
    with pytest.raises(ValueError, match='checksum'):
        collect(tmp_path, 'bugsinpy')
    assert not list(tmp_path.glob('bugsinpy/*/manifest.json'))


@pytest.mark.parametrize('limit', [-1, 0, 51, True, '24', None])
def test_task_preview_rejects_invalid_limits_without_creating_data(tmp_path, limit):
    with pytest.raises(ValueError, match='limit'):
        list_benchmark_tasks(Settings(root=tmp_path), 'swe-bench-lite', limit)
    assert not (tmp_path / 'data').exists()


@pytest.mark.parametrize('identifier', ['unknown', '../evaluator-only', 'humaneval', None])
def test_task_preview_rejects_unknown_id(tmp_path, identifier):
    with pytest.raises(ValueError, match='Unknown'):
        list_benchmark_tasks(Settings(root=tmp_path), identifier)


def test_task_preview_missing_collection_is_explicit_and_read_only(tmp_path):
    with pytest.raises(ValueError, match='not collected'):
        list_benchmark_tasks(Settings(root=tmp_path), 'bugsinpy')
    assert not (tmp_path / 'data').exists()


def test_task_preview_bounds_text_and_never_reads_or_returns_gold(tmp_path, monkeypatch):
    directory = collection(tmp_path)
    row = sample_row()
    row['problem_statement'] = 'public ' * 1000
    write_rows(directory / 'public/tasks.jsonl', [row, {**row, 'instance_id': 'sample__repo-2'}])
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    manifest['task_count'] = 2
    manifest['files']['public/tasks.jsonl'] = hashlib.sha256((directory / 'public/tasks.jsonl').read_bytes()).hexdigest()
    write_json(directory / 'manifest.json', manifest)
    original = Path.read_bytes
    original_text = Path.read_text

    def guard(path, *args, **kwargs):
        assert 'evaluator-only' not in path.parts
        return original(path, *args, **kwargs)

    def guard_text(path, *args, **kwargs):
        assert 'evaluator-only' not in path.parts
        return original_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_bytes', guard)
    monkeypatch.setattr(Path, 'read_text', guard_text)
    preview = list_benchmark_tasks(Settings(root=tmp_path), 'swe-bench-lite', 1)
    assert preview['total'] == 2 and preview['has_more'] is True
    assert preview['status'] == 'collected_not_run'
    assert len(preview['tasks'][0]['problem_statement']) == 4000
    assert preview['tasks'][0]['problem_statement_truncated'] is True
    serialized = json.dumps(preview)
    assert not any(word in serialized for word in ['GOLD_SECRET', 'GRADER_SECRET', 'secret_test_name', 'SECRET_SOLUTION', 'PRIVATE_FUTURE'])
    assert set(preview['tasks'][0]) == set(SWE_PUBLIC_FIELDS) | {'problem_statement_truncated'}


def test_task_preview_rejects_tampered_index(tmp_path):
    directory = collection(tmp_path)
    (directory / 'public/tasks.jsonl').write_text('{}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='integrity'):
        list_benchmark_tasks(Settings(root=tmp_path), 'swe-bench-lite')
