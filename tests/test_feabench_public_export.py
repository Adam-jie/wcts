import pytest

from scripts.export_feabench_public import public_task


def test_public_export_does_not_include_reference_or_test_material():
    row = {'instance_id': 'owner__repo-1', 'repo': 'owner/repo', 'base_commit': 'abc',
           'problem_statement': 'Implement the feature', 'hints_text': '',
           'patch': 'private reference', 'test_patch': 'private tests',
           'FAIL_TO_PASS': ['private test name']}
    assert public_task(row) == {
        'instance_id': 'owner__repo-1', 'repo': 'owner/repo', 'base_commit': 'abc',
        'problem_statement': 'Implement the feature', 'hints_text': '',
    }


def test_public_export_rejects_missing_requirement():
    with pytest.raises(ValueError, match='required public field'):
        public_task({'instance_id': 'owner__repo-1', 'repo': 'owner/repo', 'base_commit': 'abc'})
