"""Concurrent reservations must bound aggregate outstanding work."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from testfix.concurrency import ConcurrentBudget
from testfix.project_workflow import BudgetExceeded, ExecutionBudget


def budget(limit=5000):
    return ConcurrentBudget(ExecutionBudget({'wall_seconds': 60, 'token_limit': limit,
        'model_slots': 2, 'cpu_slots': 2}), error_type=BudgetExceeded)


def test_simultaneous_reservations_cannot_both_spend_the_last_capacity():
    shared, ready, reserved = budget(), threading.Barrier(2), threading.Barrier(2)
    def request():
        ready.wait(timeout=3)
        ticket = None
        try:
            ticket = shared.reserve({}, 3000)
        except BudgetExceeded:
            pass
        reserved.wait(timeout=3)
        if ticket is not None:
            shared.reconcile({'usage': {'total_tokens': 10}}, ticket)
        return ticket is not None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: request(), range(2)))
    assert sorted(outcomes) == [False, True]
    assert shared.report()['accounted_tokens'] == 10
    assert shared.report()['calls'] == 1 and shared.report()['pending_calls'] == 0


def test_equal_amount_tickets_are_independent_and_single_use():
    shared = budget()
    first, second = shared.reserve({}, 1000), shared.reserve({}, 1000)
    assert first == second == 1002 and first.ticket != second.ticket
    shared.cancel(first)
    with pytest.raises(ValueError, match='already completed'):
        shared.reconcile({'usage': {'total_tokens': 999}}, first)
    assert shared.report()['pending_tokens'] == 1002 and shared.calls == 0
    shared.reconcile({'usage': {'total_tokens': 19}}, second)
    assert shared.accounted_tokens == 19 and shared.calls == 1
    with pytest.raises(ValueError, match='already completed'):
        shared.cancel(second)


def test_cancellation_releases_only_its_reservation_and_forged_ticket_is_rejected():
    shared = budget()
    first = shared.reserve({}, 3000)
    with pytest.raises(BudgetExceeded, match='in-flight'):
        shared.reserve({}, 3000)
    with pytest.raises(ValueError, match='Unknown'):
        shared.cancel(int(first))
    with pytest.raises(ValueError, match='Unknown'):
        budget().cancel(first)
    shared.cancel(first)
    second = shared.reserve({}, 3000)
    shared.reconcile({}, second)
    assert shared.report()['token_usage_complete'] is False
    assert shared.report()['tokens_used'] is None and shared.report()['accounted_tokens'] == 3002


def test_reported_overrun_preserves_charge_and_stops_dispatch_with_pending_work():
    shared = budget()
    first, second = shared.reserve({}, 2000), shared.reserve({}, 2000)
    with pytest.raises(BudgetExceeded, match='in-flight'):
        shared.reconcile({'usage': {'total_tokens': 3500}}, first)
    assert shared.report()['reported_tokens'] == 3500 and shared.report()['pending_tokens'] == 2002
    assert shared.report()['concurrent_budget_exceeded'] is True
    with pytest.raises(BudgetExceeded, match='subsequent calls stopped'):
        shared.reserve({}, 1)
    # Still-running calls must be charged even after another request exceeds its estimate.
    with pytest.raises(BudgetExceeded):
        shared.reconcile({'usage': {'total_tokens': 2000}}, second)
    assert shared.accounted_tokens == 5500 and shared.calls == 2 and shared.pending_tokens == 0


def test_budget_views_do_not_expose_mutable_limit_and_wall_deadline_is_enforced():
    shared = budget()
    view = shared.values
    view['token_limit'] = 999999
    assert shared.values['token_limit'] == 5000
    shared._base.started -= 61
    with pytest.raises(BudgetExceeded, match='wall-time'):
        shared.reserve({}, 1000)
    assert shared.report()['pending_calls'] == 0
