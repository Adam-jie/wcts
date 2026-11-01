import pytest

from scripts.feabench_score import summarize


def test_single_case_does_not_become_full_benchmark_score():
    manifest = {'dataset_revision': 'v1', 'entries': [
        {'instance_id': 'a', 'lite': True, 'previously_used_for_debugging': True},
        {'instance_id': 'b', 'lite': True, 'previously_used_for_debugging': False}]}
    report = {'submitted_ids': ['a'], 'completed_ids': ['a'], 'resolved_ids': ['a'],
              'unresolved_ids': [], 'empty_patch_ids': [], 'error_ids': []}
    result = summarize(manifest, report, 'full')
    assert (result['planned'], result['submitted'], result['resolved'], result['not_submitted']) == (2, 1, 1, 1)
    assert result['resolved_ratio_percent'] is None
    assert result['previously_debugged_tasks_in_scope'] == ['a']


def test_complete_run_counts_all_selected_tasks():
    manifest = {'dataset_revision': 'v1', 'entries': [
        {'instance_id': 'a', 'lite': True, 'previously_used_for_debugging': False},
        {'instance_id': 'b', 'lite': True, 'previously_used_for_debugging': False},
    ]}
    report = {'submitted_ids': ['a', 'b'], 'completed_ids': ['a', 'b'],
              'resolved_ids': ['a'], 'unresolved_ids': ['b'], 'empty_patch_ids': [], 'error_ids': []}
    assert summarize(manifest, report, 'lite')['resolved_ratio_percent'] == 50.0
    report.update(completed_ids=['a'], unresolved_ids=[], error_ids=['b'])
    assert summarize(manifest, report, 'lite')['resolved_ratio_percent'] is None


def test_rejects_foreign_or_duplicate_instances():
    manifest = {'dataset_revision': 'v1', 'entries': [
        {'instance_id': 'a', 'lite': True, 'previously_used_for_debugging': False}]}
    report = {'submitted_ids': ['a', 'b'], 'completed_ids': [], 'resolved_ids': [],
              'unresolved_ids': [], 'empty_patch_ids': [], 'error_ids': []}
    with pytest.raises(ValueError, match='out-of-scope'):
        summarize(manifest, report, 'full')


def test_empty_prediction_is_a_counted_failure_not_a_missing_task():
    manifest = {'dataset_revision': 'v1', 'entries': [
        {'instance_id': 'a', 'lite': True, 'previously_used_for_debugging': False}]}
    report = {'submitted_ids': ['a'], 'completed_ids': [], 'resolved_ids': [],
              'unresolved_ids': [], 'empty_patch_ids': ['a'], 'error_ids': []}
    result = summarize(manifest, report, 'full')
    assert result['not_submitted'] == 0
    assert result['empty_patch'] == 1
    assert result['resolved_ratio_percent'] == 0.0
