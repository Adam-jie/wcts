"""Search invariants; controlled reward fixtures are not LLM effectiveness data."""
import pytest

from testfix.repair_search import SearchConfig, relocate_public_identities, search_forest
from test_parallel_team_runtime import workspace


def test_fork_isolated_from_parent_and_sibling_and_promotes_only_selected(workspace):
    before = workspace.candidate_hash()
    left, right = workspace.fork_candidate(), workspace.fork_candidate()
    left.apply_patch({'baseline_sha256': left.candidate_hash(),
                      'files': [{'path': 'flaskr/left.py', 'source': 'VALUE = 42\n'}]})
    assert workspace.candidate_hash() == right.candidate_hash() == before
    assert left.candidate_hash() != before
    workspace.promote_candidate(left, expected_sha256=before)
    assert workspace.candidate_hash() == left.candidate_hash()
    assert right.candidate_hash() == before
    with pytest.raises(ValueError, match='stale'):
        workspace.promote_candidate(right, expected_sha256=before)
    workspace.assert_integrity()


def test_fork_of_changed_candidate_inherits_frozen_tests_and_parent_bytes(workspace):
    workspace.apply_patch({'baseline_sha256': workspace.candidate_hash(),
                          'files': [{'path': 'flaskr/left.py', 'source': 'VALUE = 9\n'}]})
    child = workspace.fork_candidate()
    assert child.candidate_hash() == workspace.candidate_hash()
    assert child.generated_seal == workspace.generated_seal
    assert child.source_seal == workspace.source_seal
    assert child.directory != workspace.directory


def test_uct_forest_visits_multiple_roots_backpropagates_and_uses_feedback():
    seen = []
    def expand(state, context):
        seen.append(context)
        depth = context['depth']
        return {'state': state + 1, 'candidate_sha256': str(len(seen)),
                'evaluation': {'reward': depth / 3, 'eligible': True, 'public_pass': depth >= 2},
                'feedback': {'observed': depth}, 'evidence': {'fixture': True}}
    selected, report = search_forest(0, {'observed': 0}, expand,
                                    config=SearchConfig(trees=2, iterations=6, width=1, depth=3, min_successes=2))
    assert {item['tree'] for item in seen} == {0, 1}
    assert any(item['parent_id'] != 'root-' + str(item['tree']) for item in seen)
    assert any(item['parent_feedback']['observed'] == 1 for item in seen)
    assert selected['evaluation']['public_pass']
    nodes = {node['id']: node for node in report['nodes']}
    assert sum(nodes[key]['visits'] for key in report['roots']) == len(seen)
    assert all(node['visits'] >= 1 for node in nodes.values() if node['parent_id'])
    assert 'state' not in str(report)


def test_ineligible_candidate_cannot_win_even_with_large_reward():
    def expand(state, context):
        valid = context['iteration'] > 1
        return {'state': context['iteration'], 'candidate_sha256': str(context['iteration']),
                'evaluation': {'reward': .2 if valid else 100, 'eligible': valid, 'public_pass': False},
                'feedback': {}, 'evidence': {}}
    selected, report = search_forest(0, {}, expand, config=SearchConfig(trees=1, iterations=3, width=3))
    assert selected['evaluation']['eligible'] is True
    assert report['selected_id'] != 'node-1'


@pytest.mark.parametrize('kwargs', [{'trees': 0}, {'iterations': -1}, {'width': True}, {'depth': 0}, {'exploration': float('nan')}])
def test_invalid_search_settings_rejected(kwargs):
    with pytest.raises(ValueError):
        SearchConfig(**kwargs)


def test_relocation_preserves_test_names_parameters_and_raw_evidence():
    raw = {'generated': {'case_ids':['artifacts.branch.workspace.generated_tests.features::test_search[%]'],
                         'cases':[{'id':'artifacts.branch.workspace.generated_tests.features::test_search[%]','status':'passed'}]}}
    result = relocate_public_identities(raw, original_prefix='artifacts.original.workspace.generated_tests.',
                                       branch_prefix='artifacts.branch.workspace.generated_tests.')
    assert result['generated']['case_ids'] == ['artifacts.original.workspace.generated_tests.features::test_search[%]']
    assert raw['generated']['case_ids'][0].startswith('artifacts.branch.')
    changed = {'generated':{'case_ids':['different::test_search[%]']}}
    assert relocate_public_identities(changed, original_prefix='expected.',branch_prefix='branch.') == changed
