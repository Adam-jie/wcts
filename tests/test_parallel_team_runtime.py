"""Real WCTP scheduling with explicitly synthetic employee-model callbacks.

These tests execute the supplied graph engine and real sealed file tools. They
do not invoke an LLM or claim a business-function acceptance result.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
import uuid

import pytest

from testfix.config import Settings
from testfix.project_tools import ProjectWorkspace, files_under
from testfix.team_planning import solve_team_plan, validate_team_plan
from testfix.team_runtime import ACTION_SCHEMA, execute_team_round, verify_team_evidence
from testfix.wctp_runtime import SOURCE_SHA256, WCTPExecutionError, resolve_wctp_root


LEFT = 'flaskr/left.py'
RIGHT = 'flaskr/right.py'


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


@pytest.fixture
def workspace(tmp_path):
    source = tmp_path / 'registered/source'
    write(source / 'flaskr/__init__.py', '# Synthetic scheduling fixture, never served.\n')
    write(source / LEFT, 'VALUE = 0\n')
    write(source / RIGHT, 'VALUE = 0\n')
    write(source / 'tests/test_original.py', 'def test_original():\n    assert True\n')
    acceptance = tmp_path / 'registered/acceptance'
    write(acceptance / 'test_private.py', '# Frozen fixture; no acceptance execution is claimed.\n')
    project = {'id': 'Project/parallel-unit-fixture', 'adapter': 'flaskr',
               'source': {'origin': 'synthetic-unit-fixture', 'files': files_under(source)},
               'baseline': str(source), 'acceptance': str(acceptance),
               'allowed_changes': [LEFT, RIGHT],
               'requirements': [{'id': 'SYNTHETIC', 'description': 'Synthetic scheduling assertion'}]}
    current = ProjectWorkspace(Settings(root=tmp_path), uuid.uuid4().hex, project)
    current.write_tests({'files': [{'path': 'test_generated.py',
                                  'source': 'def test_generated():\n    assert True\n'}]})
    return current


@pytest.fixture
def source_root():
    root = resolve_wctp_root()
    if not (root / 'src/engine/graph_executor.py').is_file():
        pytest.skip('External supplied WCTP source is unavailable; set TESTFIX_WCTP_ROOT')
    return root


def task(identity, paths, *, dependencies=(), skill='repair-code'):
    return {'id': identity, 'title': 'Synthetic ' + identity, 'skill_id': skill,
            'requirement_ids': ['SYNTHETIC'], 'depends_on': list(dependencies), 'paths': list(paths)}


def plan_for(workspace, *tasks):
    plan = {'request_assessment': {'supported': True, 'matched_requirement_ids': ['SYNTHETIC'],
                                  'uncovered_requests': [], 'questions': []},
            'work_items': list(tasks)}
    return validate_team_plan(plan, ['SYNTHETIC'], workspace.allowed, workspace.snapshot()['sources'])


def action(operation, *, path='', baseline='', files=None, message='Synthetic employee tool proposal'):
    return {'action': operation, 'path': path, 'query': '', 'start_line': 1, 'limit': 80,
            'baseline_sha256': baseline, 'files': files or [], 'message': message}


class SyntheticEmployee:
    """Explicit protocol fixture, using barriers instead of fake timing claims."""

    def __init__(self, workspace, *, concurrent_ids=(), hook=None):
        self.workspace, self.hook = workspace, hook
        self.concurrent_ids = set(concurrent_ids)
        self.barrier = threading.Barrier(len(self.concurrent_ids)) if self.concurrent_ids else None
        self.lock = threading.Lock()
        self.active, self.peak = 0, 0
        self.calls = []

    def __call__(self, skill, context, schema):
        assert schema == ACTION_SCHEMA
        assigned = context['assigned_task']
        assert skill == assigned['skill_id']
        identity = assigned['id']
        inference_hash = self.workspace.candidate_hash()
        with self.lock:
            self.active += 1
            self.peak = max(self.active, self.peak)
            self.calls.append({'id': identity, 'context': deepcopy(context),
                               'thread': threading.current_thread().name})
        try:
            if identity in self.concurrent_ids:
                self.barrier.wait(timeout=5)
            if not context['observations']:
                proposed = action('read_file', path=assigned['paths'][0])
            elif skill == 'explore-repository':
                proposed = action('finish', message='Synthetic informed exploration handoff')
            else:
                proposed = action('apply_patch', baseline=inference_hash,
                    files=[{'path': assigned['paths'][0], 'source': f'VALUE = 1\n# Synthetic {identity}\n'}])
            if self.hook:
                self.hook(assigned, context, proposed)
            return proposed
        finally:
            with self.lock:
                self.active -= 1


def execute(workspace, source_root, plan, callback, *, slots=2, max_steps=3):
    schedule = solve_team_plan(plan, wall_seconds=60, token_limit=10000, model_slots=slots,
                               task_seconds=1, task_tokens=100)
    assert schedule['status'] in {'OPTIMAL', 'FEASIBLE'}
    ledger = {'request': 'Synthetic employee runtime test; no real inference is performed'}
    events = []
    def run():
        return execute_team_round(workspace, plan, schedule, call=callback,
            public_test=lambda: pytest.fail('These callbacks must not request business-test execution'),
            emit=lambda *args: events.append(args), ledger=ledger, round_number=1,
            max_steps=max_steps, model_slots=slots, wctp_root=source_root)
    return run, ledger, events


def receipts(workspace, ledger):
    return [json.loads((workspace.settings.root / step['path']).read_text(encoding='utf-8'))
            for employee in ledger['rounds'][0]['employees'] for step in employee['steps']]


def test_disjoint_repairs_really_overlap_and_merge_the_same_inference_baseline(workspace, source_root):
    plan = plan_for(workspace, task('left', [LEFT]), task('right', [RIGHT]))
    callback = SyntheticEmployee(workspace, concurrent_ids=['left', 'right'])
    run, ledger, _ = execute(workspace, source_root, plan, callback)
    initial = workspace.candidate_hash()
    output, patch = run()
    assert callback.peak == 2
    assert output['baseline_sha256'] == initial
    assert [item['path'] for item in output['files']] == [LEFT, RIGHT]
    assert patch['changed_files'] == [LEFT, RIGHT]
    assert patch['candidate_sha256'] == workspace.candidate_hash() != initial
    assert '# Synthetic left' in (workspace.candidate / LEFT).read_text(encoding='utf-8')
    assert '# Synthetic right' in (workspace.candidate / RIGHT).read_text(encoding='utf-8')
    patches = [record for record in receipts(workspace, ledger) if record['action']['action'] == 'apply_patch']
    assert len(patches) == 2 and all(record['observation']['ok'] for record in patches)
    assert {record['inference_candidate_sha256'] for record in patches} == {initial}
    merges = [record['observation']['result']['merge'] for record in patches]
    assert [merge['rebased_disjoint_patch'] for merge in merges] == [False, True]
    assert {merge['proposal_baseline_sha256'] for merge in merges} == {initial}
    assert merges[1]['commit_baseline_sha256'] == patches[0]['candidate_after']
    revision = ledger['rounds'][0]
    assert revision['status'] == 'awaiting_review_and_verification'
    assert set(revision['handoffs']) == {'left', 'right'}
    assert all(batch['engine'] == 'wctp.GraphExecutor.execute' for batch in revision['batches'])
    assert all(batch['source_sha256'] == SOURCE_SHA256 and not batch['source_copied']
               for batch in revision['batches'])
    assert all(batch['max_observed_concurrency'] == 2 for batch in revision['batches'])
    assert all(batch['initial_in_degree']['TestfixBatchEnd'] == 2 for batch in revision['batches'])
    verify_team_evidence(ledger, workspace)
    workspace.assert_integrity()


def test_same_file_writers_hold_ownership_until_handoff(workspace, source_root):
    plan = plan_for(workspace, task('first', [LEFT]), task('second', [LEFT]))
    callback = SyntheticEmployee(workspace)
    run, ledger, _ = execute(workspace, source_root, plan, callback)
    _, patch = run()
    assert patch['changed_files'] == [LEFT]
    assert callback.peak == 1
    assert [call['id'] for call in callback.calls] == ['first', 'first', 'second', 'second']
    assert all(len(dispatch['active']) == 1 for dispatch in ledger['rounds'][0]['dispatches'])
    assert '# Synthetic second' in (workspace.candidate / LEFT).read_text(encoding='utf-8')
    second = [record for record in receipts(workspace, ledger) if record['task_id'] == 'second']
    assert 'Synthetic first' in second[0]['observation']['result']['text']


def test_employee_fork_join_receives_only_completed_dependency_handoffs(workspace, source_root):
    plan = plan_for(workspace,
        task('inspect', [LEFT, RIGHT], skill='explore-repository'),
        task('left', [LEFT], dependencies=['inspect']),
        task('right', [RIGHT], dependencies=['inspect']),
        task('join', [LEFT, RIGHT], dependencies=['left', 'right'], skill='explore-repository'))
    callback = SyntheticEmployee(workspace, concurrent_ids=['left', 'right'])
    run, ledger, _ = execute(workspace, source_root, plan, callback)
    run()
    for call in callback.calls:
        dependencies = call['context']['dependency_outputs']
        expected = {'inspect': set(), 'left': {'inspect'}, 'right': {'inspect'}, 'join': {'left', 'right'}}[call['id']]
        assert set(dependencies) == expected
        assert all(item['evidence'] for item in dependencies.values())
    assert callback.peak == 2
    dispatches = ledger['rounds'][0]['dispatches']
    assert dispatches[0]['started'] == ['inspect']
    assert next(item for item in dispatches if item['started'] == ['left', 'right'])['completed_dependencies'] == ['inspect']
    join = next(item for item in dispatches if item['started'] == ['join'])
    assert {'left', 'right'} <= set(join['completed_dependencies'])


def test_single_slot_dispatches_high_out_degree_before_an_independent_leaf(workspace, source_root):
    # ID order favours a-leaf, while ready out-degree must favour z-hub.
    plan = plan_for(workspace,
        task('a-leaf', [LEFT]),
        task('z-hub', [LEFT, RIGHT], skill='explore-repository'),
        task('b-child', [RIGHT], dependencies=['z-hub']),
        task('c-child', [LEFT, RIGHT], dependencies=['z-hub'], skill='explore-repository'))
    callback = SyntheticEmployee(workspace)
    run, ledger, _ = execute(workspace, source_root, plan, callback, slots=1)
    run()
    assert ledger['rounds'][0]['dispatches'][0]['ready_candidates'] == ['z-hub', 'a-leaf']
    assert ledger['rounds'][0]['dispatches'][0]['started'] == ['z-hub']
    assert callback.calls[0]['id'] == 'z-hub'
    assert callback.peak == 1


def test_wrong_baseline_is_rejected_even_when_a_disjoint_patch_can_rebase(workspace, source_root):
    def wrong_version(assigned, context, proposed):
        if assigned['id'] == 'right' and proposed['action'] == 'apply_patch':
            proposed['baseline_sha256'] = '0' * 64
    plan = plan_for(workspace, task('left', [LEFT]), task('right', [RIGHT]))
    callback = SyntheticEmployee(workspace, concurrent_ids=['left', 'right'], hook=wrong_version)
    run, ledger, _ = execute(workspace, source_root, plan, callback, max_steps=2)
    with pytest.raises(ValueError, match='exhausted'):
        run()
    assert '# Synthetic left' in (workspace.candidate / LEFT).read_text(encoding='utf-8')
    assert (workspace.candidate / RIGHT).read_text(encoding='utf-8') == 'VALUE = 0\n'
    rejected = next(record for record in receipts(workspace, ledger)
                    if record['task_id'] == 'right' and record['action']['action'] == 'apply_patch')
    assert rejected['observation'] == {'ok': False, 'error': 'Patch proposal uses a stale candidate version'}
    assert 'right' not in ledger['rounds'][0].get('handoffs', {})
    assert ledger['rounds'][0].get('status') != 'accepted'


def test_external_candidate_change_during_inference_fails_before_commit(workspace, source_root):
    def corrupt(assigned, context, proposed):
        if proposed['action'] == 'apply_patch':
            write(workspace.candidate / LEFT, 'VALUE = 999\n# external unauthorized write\n')
    plan = plan_for(workspace, task('left', [LEFT]))
    callback = SyntheticEmployee(workspace, hook=corrupt)
    run, ledger, _ = execute(workspace, source_root, plan, callback)
    with pytest.raises(ValueError, match='outside the patch tool'):
        run()
    assert not any(record['action']['action'] == 'apply_patch' and record['observation']['ok']
                   for record in receipts(workspace, ledger))
    assert ledger['rounds'][0].get('status') != 'accepted'


def test_changed_read_set_rejects_patch_despite_a_valid_inference_version(workspace, source_root):
    changed = []
    def intervening_host_patch(assigned, context, proposed):
        if proposed['action'] == 'apply_patch' and not changed:
            workspace.apply_patch({'baseline_sha256': workspace.candidate_hash(),
                'files': [{'path': LEFT, 'source': 'VALUE = 777\n# intervening registered writer\n'}],
                'reason': 'Synthetic race: authorized tool mutation after inference snapshot'})
            changed.append(True)
    plan = plan_for(workspace, task('left', [LEFT]))
    callback = SyntheticEmployee(workspace, hook=intervening_host_patch)
    run, ledger, _ = execute(workspace, source_root, plan, callback, max_steps=2)
    with pytest.raises(ValueError, match='exhausted'):
        run()
    assert 'VALUE = 777' in (workspace.candidate / LEFT).read_text(encoding='utf-8')
    record = receipts(workspace, ledger)[-1]
    assert record['action']['baseline_sha256'] == record['inference_candidate_sha256']
    assert record['observation'] == {'ok': False,
        'error': 'Concurrent patch read-set changed; refresh source before retrying'}
    assert ledger['rounds'][0].get('status') != 'accepted'


def test_employee_receipt_hash_tampering_is_detected(workspace, source_root):
    plan = plan_for(workspace, task('left', [LEFT]))
    run, ledger, _ = execute(workspace, source_root, plan, SyntheticEmployee(workspace))
    run()
    receipt = ledger['rounds'][0]['employees'][0]['steps'][0]
    target = workspace.settings.root / receipt['path']
    assert hashlib.sha256(target.read_bytes()).hexdigest() == receipt['sha256']
    target.write_bytes(target.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='changed after handoff'):
        verify_team_evidence(ledger, workspace)


def test_last_inference_batch_cannot_commit_after_prior_receipt_tampering(workspace, source_root):
    ledger_holder = {}
    def corrupt_receipt(assigned, context, proposed):
        if proposed['action'] == 'apply_patch':
            receipt = ledger_holder['ledger']['rounds'][0]['employees'][0]['steps'][0]
            target = workspace.settings.root / receipt['path']
            target.write_bytes(target.read_bytes() + b'\n# external evidence tampering\n')
    plan = plan_for(workspace, task('left', [LEFT]))
    run, ledger, _ = execute(workspace, source_root, plan, SyntheticEmployee(workspace, hook=corrupt_receipt))
    ledger_holder['ledger'] = ledger
    initial = workspace.candidate_hash()
    with pytest.raises(ValueError, match='changed after handoff'):
        run()
    assert workspace.candidate_hash() == initial
    assert (workspace.candidate / LEFT).read_text(encoding='utf-8') == 'VALUE = 0\n'


def test_one_model_callback_error_prevents_all_patch_commits_in_that_batch(workspace, source_root):
    def fail(assigned, context, proposed):
        if assigned['id'] == 'right' and proposed['action'] == 'apply_patch':
            raise RuntimeError('Explicit synthetic callback failure')
    plan = plan_for(workspace, task('left', [LEFT]), task('right', [RIGHT]))
    callback = SyntheticEmployee(workspace, concurrent_ids=['left', 'right'], hook=fail)
    run, ledger, _ = execute(workspace, source_root, plan, callback)
    initial = workspace.candidate_hash()
    with pytest.raises(WCTPExecutionError, match='Explicit synthetic callback failure'):
        run()
    assert workspace.candidate_hash() == initial
    assert all((workspace.candidate / name).read_text(encoding='utf-8') == 'VALUE = 0\n' for name in [LEFT, RIGHT])
    revision = ledger['rounds'][0]
    assert revision['status'] == 'employee_execution_failed'
    assert revision['batches'][-1]['engine'] == 'wctp.GraphExecutor.execute'
    assert any(event.get('error') and 'Explicit synthetic callback failure' in event['error']
               for event in revision['batches'][-1]['events'])
    assert not any(record['action']['action'] == 'apply_patch' for record in receipts(workspace, ledger))
    verify_team_evidence(ledger, workspace)
