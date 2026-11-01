"""Public source pins must not silently become runnable benchmark claims."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.snapshot_dataset_sources import pin_source, snapshot


def project(ref='1.0'):
    return {'id': 'example', 'domain': 'web', 'repository': 'https://github.com/example/product', 'ref': ref}


def test_annotated_tag_uses_peeled_commit_without_install_or_checkout(monkeypatch):
    calls = []

    def remote(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=(f"{'a'*40}\trefs/tags/1.0\n{'b'*40}\trefs/tags/1.0^{{}}\n").encode())

    monkeypatch.setattr('scripts.snapshot_dataset_sources.subprocess.run', remote)
    row = pin_source(project())
    assert row['commit'] == 'b' * 40
    assert row['status'] == 'metadata_only' and row['source_downloaded'] is False
    assert row['preflight_passed'] is False and row['model_repair_executed'] is False
    assert calls[0][0][:3] == ['git', 'ls-remote', '--exit-code']
    assert calls[0][1]['env']['GIT_TERMINAL_PROMPT'] == '0'


def test_ambiguous_branch_and_tag_are_not_silently_pinned(monkeypatch):
    monkeypatch.setattr('scripts.snapshot_dataset_sources.subprocess.run', lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=(f"{'a'*40}\trefs/heads/1.0\n{'b'*40}\trefs/tags/1.0\n").encode()))
    with pytest.raises(ValueError, match='Ambiguous'):
        pin_source(project())


def test_failed_remote_does_not_publish_partial_source_lock(tmp_path, monkeypatch):
    catalog = tmp_path/'catalog.json'
    catalog.write_text(json.dumps({'schema': 'testfix.dataset-design.v1', 'id': 'suite', 'projects': [project()]}))
    output = tmp_path/'source-lock.json'
    monkeypatch.setattr('scripts.snapshot_dataset_sources.subprocess.run', lambda *a, **k: SimpleNamespace(returncode=2, stdout=b''))
    with pytest.raises(ValueError, match='Cannot resolve'):
        snapshot(catalog, output)
    assert not output.exists()


def test_old_lock_is_kept_without_network_and_catalog_counts_are_design_only(tmp_path, monkeypatch):
    output = tmp_path/'source-lock.json'
    output.write_text('old evidence')
    monkeypatch.setattr('scripts.snapshot_dataset_sources.subprocess.run', lambda *a, **k: pytest.fail('must not request remote'))
    with pytest.raises(ValueError, match='already exists'):
        snapshot(tmp_path/'absent.json', output)
    assert output.read_text() == 'old evidence'
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads((root/'examples/datasets/large-project-suite.json').read_text(encoding='utf8'))
    assert catalog['status'] == 'design_not_execution_results'
    assert len(catalog['projects']) == catalog['planned_counts']['projects'] == 8
    assert {p['domain'] for p in catalog['projects']} == {'web', 'software', 'mcu', 'mpu'}
    assert all(p['status'] == 'candidate_environment_required' for p in catalog['projects'])
    assert catalog['planned_counts']['total_cases'] == 3*8 + 8 == 32
