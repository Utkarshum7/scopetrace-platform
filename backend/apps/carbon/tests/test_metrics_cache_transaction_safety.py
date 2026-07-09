"""
Phase 7.5 (H3) -- bump_calc_version() must never make a metrics cache-version
bump visible to other readers before the calculation write that triggered it
has actually committed. Deterministic (no threading needed).

Testing note: Django's TestCase wraps every test method in its OWN atomic()
block that is always rolled back at teardown, so on_commit() callbacks
registered anywhere in a plain TestCase test never fire on their own --
there's no real outer commit to defer to. Django's
self.captureOnCommitCallbacks(execute=True) is the correct tool: it captures
callbacks registered inside the `with` block and, at the block's exit,
executes them exactly as a real commit would -- independent of the actual
(rolled-back) database transaction state. Every assertion here that expects
the bump to have "happened" is inside that context manager for that reason.
"""
from django.core.cache import cache
from django.db import transaction
from django.test import TestCase

from apps.carbon.services import metrics_cache
from apps.core.models import Organization


class BumpCalcVersionTransactionSafetyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name="Cache Race Org")

    def test_version_is_not_bumped_while_the_transaction_is_still_open(self):
        """THE race this milestone closes: a caller inside an open atomic()
        block calls bump_calc_version() -- the version must NOT change until
        that block actually commits. Pre-7.5, cache.incr() fired immediately,
        so a concurrent reader mid-transaction could see the bumped version
        and cache a pre-write snapshot under the post-write key."""
        before = metrics_cache.get_calc_version(self.org.id)
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with transaction.atomic():
                metrics_cache.bump_calc_version(self.org.id)
                # Still inside the transaction: the callback must not have
                # run yet, so the version a concurrent reader would see is
                # still the OLD one.
                self.assertEqual(
                    metrics_cache.get_calc_version(self.org.id), before,
                    "version must not change until the transaction commits",
                )
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(metrics_cache.get_calc_version(self.org.id), before + 1)

    def test_version_is_not_bumped_if_the_transaction_rolls_back(self):
        """A useful side effect of the fix: a failed write no longer performs
        a cache invalidation for data that was never actually persisted. An
        on_commit callback registered inside an atomic() block that itself
        rolls back is discarded, not deferred further -- true regardless of
        any TestCase-level wrapping, so no captureOnCommitCallbacks needed."""
        before = metrics_cache.get_calc_version(self.org.id)

        class _Boom(Exception):
            pass

        with self.assertRaises(_Boom):
            with transaction.atomic():
                metrics_cache.bump_calc_version(self.org.id)
                raise _Boom("simulated failure after the bump call")

        self.assertEqual(
            metrics_cache.get_calc_version(self.org.id), before,
            "a rolled-back transaction must not leave a bumped version behind",
        )

    def test_nested_atomic_only_bumps_when_the_outermost_block_commits(self):
        """A savepoint (nested atomic) exiting does NOT mean the transaction
        has committed -- on_commit must defer to the OUTERMOST block,
        matching how carbon_service.calculate_for_batch's single atomic() and
        soft_delete's caller-owned atomic() both behave."""
        before = metrics_cache.get_calc_version(self.org.id)
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():  # outer (within this capture scope)
                with transaction.atomic():  # inner savepoint
                    metrics_cache.bump_calc_version(self.org.id)
                # Inner savepoint released, but the outer block hasn't exited.
                self.assertEqual(metrics_cache.get_calc_version(self.org.id), before)
        self.assertEqual(metrics_cache.get_calc_version(self.org.id), before + 1)

    def test_captured_on_commit_callback_fires_exactly_once(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with transaction.atomic():
                metrics_cache.bump_calc_version(self.org.id)
        self.assertEqual(len(callbacks), 1)
