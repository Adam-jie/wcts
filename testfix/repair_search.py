"""Execution-guided UCT forest; independent acceptance is outside this module.

Method references: SWE-Search (ICLR 2025), Forest-of-Thought (ICML 2025).
This implementation uses measured public tests rather than an LLM numeric value
function. It is not a reproduction of either paper or a claim of their scores.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
import math
import time


@dataclass(frozen=True)
class SearchConfig:
    trees: int = 3
    iterations: int = 6
    width: int = 2
    depth: int = 3
    exploration: float = 1.4142135623730951
    min_successes: int = 3
    quality_attempts: int = 6

    def __post_init__(self):
        for key in ('trees', 'iterations', 'width', 'depth', 'min_successes', 'quality_attempts'):
            if type(getattr(self, key)) is not int or getattr(self, key) < 1:
                raise ValueError('Search ' + key + ' must be a positive integer')
        if (type(self.exploration) not in (int, float) or not math.isfinite(self.exploration)
                or self.exploration < 0):
            raise ValueError('Search exploration must be finite and nonnegative')


def public_evaluation(baseline, observed):
    """Reward only identical, frozen public test identities and preserved regression."""
    from .project_eval import _paired_cases_match
    reg, gen = observed['regression'], observed['generated']
    identity_ok = all(_paired_cases_match(baseline[key], observed[key]) for key in ('regression', 'generated'))
    valid = identity_ok and all(not suite.get('collection_errors') and not suite.get('parse_error')
                               and not suite.get('binding_error') and not suite.get('timeout')
                               for suite in (reg, gen))
    regression_ok = valid and reg['successful'] and baseline['regression']['successful']
    ratio = gen['passed'] / gen['total'] if valid and gen['total'] else 0.
    return {'eligible': bool(regression_ok), 'reward': .25 + .75 * ratio if regression_ok else 0.,
            'public_pass': bool(regression_ok and gen['successful']), 'identity_match': identity_ok,
            'generated_passed': gen['passed'], 'generated_total': gen['total'],
            'regression_passed': reg['passed'], 'regression_total': reg['total'],
            'basis': 'frozen_public_tests_only'}


def relocate_public_identities(observed, *, original_prefix, branch_prefix):
    """Map only a known generated-suite location, retaining test names/parameters.

    The caller first binds identical frozen suite hashes. Raw JUnit evidence is
    retained separately. No suffix matching, case deletion or assertion changes
    are allowed as a substitute for exact suite identity.
    """
    result = deepcopy(observed)
    suite = result['generated']
    def relocate(identity):
        return original_prefix + identity[len(branch_prefix):] if identity.startswith(branch_prefix) else identity
    suite['case_ids'] = [relocate(identity) for identity in suite['case_ids']]
    if len(set(suite['case_ids'])) != len(suite['case_ids']):
        raise ValueError('Test identity relocation introduced duplicate cases')
    if 'cases' in suite:
        suite['cases'] = [{**case, 'id':relocate(case['id'])} for case in suite['cases']]
    return result


def search_forest(initial_state, initial_feedback, expand, *, config=None, publish=None):
    """Select/expand/evaluate/backpropagate with sparse, one-branch activation.

    ``expand`` owns isolated host workspaces and real public execution. Host
    objects never enter the serializable search report. Visits include actual
    expansions only; no synthetic rollouts or model estimates count as tests.
    """
    config = config or SearchConfig()
    started = time.monotonic()
    nodes, states, feedbacks, results = {}, {}, {}, {}
    roots = ['root-' + str(index) for index in range(config.trees)]
    report = {'schema': 'testfix.search-forest.v1', 'algorithm': 'UCT', 'config': asdict(config),
              'roots': roots, 'nodes': [], 'selected_id': None, 'status': 'searching',
              'reward_source': 'frozen_public_tests_only', 'independent_acceptance_used': False,
              'activation': 'one_selected_branch_per_iteration; employee_models_may_overlap'}
    for tree, identity in enumerate(roots):
        nodes[identity] = {'id': identity, 'tree': tree, 'parent_id': None, 'depth': 0,
                           'children': [], 'visits': 0, 'value_sum': 0., 'mean_value': 0., 'terminal': False}
        states[identity], feedbacks[identity] = initial_state, initial_feedback

    def sync():
        report['nodes'] = deepcopy(list(nodes.values()))
        report['elapsed_seconds'] = round(time.monotonic() - started, 4)
        if publish:
            publish(deepcopy(report))

    def expandable(identity):
        node = nodes[identity]
        if node['terminal'] or node['depth'] >= config.depth:
            return False
        return len(node['children']) < config.width or any(expandable(child) for child in node['children'])

    def uct(identity, parent_visits):
        node = nodes[identity]
        if not node['visits']:
            return math.inf
        return node['mean_value'] + config.exploration * math.sqrt(math.log(parent_visits + 1) / node['visits'])

    def descend(identity):
        while len(nodes[identity]['children']) >= config.width:
            available = [child for child in nodes[identity]['children'] if expandable(child)]
            identity = max(available, key=lambda child: (uct(child, nodes[identity]['visits']), -list(nodes).index(child)))
        return identity

    sync()
    for iteration in range(1, config.iterations + 1):
        available = [root for root in roots if expandable(root)]
        if not available:
            report['stop_reason'] = 'frontiers_exhausted'
            break
        root = max(available, key=lambda identity: (uct(identity, iteration), -roots.index(identity)))
        parent = descend(root)
        identifier = 'node-' + str(iteration)
        context = {'iteration': iteration, 'tree': nodes[parent]['tree'], 'depth': nodes[parent]['depth'] + 1,
                   'node_id': identifier, 'parent_id': parent, 'parent_feedback': deepcopy(feedbacks[parent]),
                   'sibling_feedback': [deepcopy(feedbacks[key]) for key in nodes[parent]['children']],
                   'strategy': ('minimal_change', 'interface_contracts', 'boundary_and_recovery')[nodes[parent]['tree'] % 3]}
        nodes[identifier] = {'id':identifier,'tree':context['tree'],'parent_id':parent,'depth':context['depth'],
                             'children':[],'visits':0,'value_sum':0.,'mean_value':0.,'terminal':False,
                             'iteration':iteration,'status':'expanding','strategy':context['strategy']}
        nodes[parent]['children'].append(identifier)
        sync()
        result = expand(states[parent], context)
        evaluation = deepcopy(result['evaluation'])
        reward = evaluation['reward'] if evaluation['eligible'] else 0.
        if type(reward) not in (int, float) or not math.isfinite(reward) or not 0 <= reward <= 1:
            raise ValueError('Search reward must be finite and in [0, 1]')
        node = {'id': identifier, 'tree': nodes[parent]['tree'], 'parent_id': parent,
                'depth': context['depth'], 'children': [], 'visits': 0, 'value_sum': 0., 'mean_value': 0.,
                'terminal': bool(evaluation['public_pass']), 'candidate_sha256': result['candidate_sha256'],
                'evaluation': evaluation, 'evidence': deepcopy(result.get('evidence', {})),
                'strategy': context['strategy'], 'iteration': iteration}
        nodes[identifier] = node
        states[identifier], feedbacks[identifier], results[identifier] = result['state'], result['feedback'], result
        cursor = identifier
        while cursor is not None:
            current = nodes[cursor]
            current['visits'] += 1
            current['value_sum'] += reward
            current['mean_value'] = current['value_sum'] / current['visits']
            cursor = current['parent_id']
        sync()
        if sum(item['evaluation']['public_pass'] for item in results.values()) >= config.min_successes:
            report['stop_reason'] = 'public_success_candidates_reached'
            break
    else:
        report['stop_reason'] = 'iteration_allowance_consumed'
    eligible = [identity for identity, result in results.items() if result['evaluation']['eligible']]
    if eligible:
        votes = Counter(results[identity]['candidate_sha256'] for identity in eligible)
        winner = max(eligible, key=lambda identity: (
            results[identity]['evaluation']['public_pass'], results[identity]['evaluation']['reward'],
            votes[results[identity]['candidate_sha256']], -nodes[identity]['depth'], -nodes[identity]['iteration']))
        report.update(selected_id=winner, status='selected', selection_basis='public_pass_then_reward_then_identical_candidate_votes')
        selected = results[winner]
    else:
        selected = None
        report['status'] = 'no_eligible_candidate'
    sync()
    return selected, report
