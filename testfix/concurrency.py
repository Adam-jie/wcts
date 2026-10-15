"""Thread-safe outstanding-call accounting for a workflow execution budget.

The wrapped budget remains responsible for its conservative request estimate
and provider-reported token accounting. This wrapper also accounts for requests
already in flight, which a sequential budget cannot see.
"""
from __future__ import annotations

import threading
import uuid


class _Reservation(int):
    """Integer-compatible amount with an unambiguous owner and ticket identity."""

    def __new__(cls, amount: int, owner: str):
        value = super().__new__(cls, amount)
        value.owner = owner
        value.ticket = uuid.uuid4().hex
        return value


class ConcurrentBudget:
    def __init__(self, base, *, error_type=ValueError):
        self._base = base
        self._error_type = error_type
        self._lock = threading.RLock()
        self._owner = uuid.uuid4().hex
        self._pending: dict[str, int] = {}
        self._exceeded = False

    @property
    def values(self):
        with self._lock:
            return self._base.values.copy()

    @property
    def accounted_tokens(self):
        with self._lock:
            return self._base.accounted_tokens

    @property
    def tokens_used(self):
        with self._lock:
            return self._base.tokens_used

    @property
    def calls(self):
        with self._lock:
            return self._base.calls

    @property
    def usage_complete(self):
        with self._lock:
            return self._base.usage_complete

    @property
    def pending_tokens(self):
        with self._lock:
            return sum(self._pending.values())

    def remaining(self):
        with self._lock:
            if self._exceeded:
                raise self._error_type('Reported model usage exceeded the concurrent budget; subsequent calls stopped')
            return self._base.remaining()

    def reserve(self, context: dict, output_cap: int):
        with self._lock:
            self.remaining()
            amount = self._base.reserve(context, output_cap)
            if self._base.accounted_tokens + sum(self._pending.values()) + amount > self._base.values['token_limit']:
                raise self._error_type('Insufficient token budget including in-flight conservative reservations')
            reservation = _Reservation(amount, self._owner)
            self._pending[reservation.ticket] = int(reservation)
            return reservation

    def _take(self, reservation):
        if (not isinstance(reservation, _Reservation) or reservation.owner != self._owner
                or reservation.ticket not in self._pending):
            raise ValueError('Unknown or already completed budget reservation')
        return self._pending.pop(reservation.ticket)

    def cancel(self, reservation):
        """Release a request that failed before transmission without charging it."""
        with self._lock:
            self._take(reservation)

    def reconcile(self, call: dict, reservation):
        """Replace one outstanding estimate with this exact call's accounting.

        Provider usage can exceed a prompt-only CLI estimate. Already running
        requests cannot be undone, so such excess is recorded and makes further
        dispatch fail closed; it is never reported as a hard provider token cap.
        """
        with self._lock:
            amount = self._take(reservation)
            try:
                self._base.reconcile(call, amount)
            finally:
                if self._base.accounted_tokens + sum(self._pending.values()) > self._base.values['token_limit']:
                    self._exceeded = True
            if self._exceeded:
                raise self._error_type('Reported model usage exceeded the concurrent budget including in-flight reservations')

    def report(self):
        with self._lock:
            return {**self._base.report(), 'pending_tokens': sum(self._pending.values()),
                    'pending_calls': len(self._pending), 'concurrent_budget_exceeded': self._exceeded}
