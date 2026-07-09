"""
Phase 7.5 (H2, Findings 1 & 2) -- concurrency proofs for the AI gateway.

Row-level locking (select_for_update, Finding 2) is a no-op on SQLite, which
serializes writers at the database level instead. These threaded tests are
therefore only *meaningful* on PostgreSQL (as in CI and Docker); on SQLite
they still pass but prove less. Each worker thread opens its own DB
connection and closes it in a finally, mirroring apps.audit's
ConcurrentAppendTests so test-DB teardown isn't blocked.
"""
import threading

from django.db import connection
from django.test import TransactionTestCase, override_settings

from apps.ai.models import AIInteraction, TenantAIPolicy
from apps.ai.providers.echo import canned
from apps.ai.services.gateway import invoke_ai
from apps.core.models import Organization


def _valid_echo_value(echo_text="hi"):
    return canned({"acknowledged": True, "echo": echo_text})


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class IdempotencyConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Concurrency Org")
        TenantAIPolicy.objects.create(
            organization=self.org, ai_enabled=True, provider_override="echo", model_override="echo-1",
        )
        # Warm the prompt registry (AIPromptVersion row for this template) with
        # a single non-concurrent call BEFORE the threaded hammer. Otherwise
        # all threads would race to create that same row on a cold table -- a
        # SEPARATE prompt-registry cold-start race (get_or_create under an
        # already-empty table), unrelated to the idempotency behavior under
        # test here. Uses a distinct key so it doesn't pre-create a race row.
        invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest",
            template_vars={"echo_value": _valid_echo_value("warm")},
            response_schema_id="foundation.selftest", response_schema_version=1,
            idempotency_key="warm-up",
        )

    def _hammer(self, key, n=4):
        results, errors = [], []
        barrier = threading.Barrier(n)

        def worker():
            try:
                barrier.wait()  # maximize the overlap
                res = invoke_ai(
                    organization=self.org, capability="foundation.selftest",
                    prompt_name="foundation.selftest",
                    template_vars={"echo_value": _valid_echo_value("hi")},
                    response_schema_id="foundation.selftest", response_schema_version=1,
                    idempotency_key=key,
                )
                results.append(res)
            except Exception as exc:  # noqa: BLE001 - surfaced as a test failure
                errors.append(exc)
            finally:
                # Close ONLY this thread's own connection (not connections.
                # close_all(), which would yank connections other threads are
                # still using). Django's teardown only closes the main thread's
                # connection, so each worker must close its own or it leaks and
                # blocks DROP DATABASE at teardown. Mirrors apps.audit's
                # ConcurrentAppendTests.
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results, errors

    def _assert_backend_errors_ok(self, errors):
        """SQLite has no row-level locking -- concurrent writers lose a
        file-lock race with OperationalError('database ... locked'), which is
        a backend artifact, not a correctness bug (same rationale as
        apps.audit.ConcurrentAppendTests). On Postgres there is no excuse: the
        select_for_update serialization must let every caller through."""
        if connection.vendor == "sqlite":
            for exc in errors:
                self.assertIn("locked", str(exc).lower(), f"unexpected non-lock error: {exc!r}")
        else:
            self.assertEqual(errors, [], f"no call should raise on {connection.vendor}: {errors}")

    def test_concurrent_same_key_never_creates_two_ok_rows(self):
        results, errors = self._hammer("race-key")
        self._assert_backend_errors_ok(errors)
        # The invariant that must hold on EVERY backend: at most one OK row for
        # this idempotency key -- guaranteed by the partial UniqueConstraint
        # (backstop) plus the per-org lock + IntegrityError->replay.
        ok_rows = AIInteraction.objects.filter(idempotency_key="race-key", outcome="OK")
        self.assertEqual(ok_rows.count(), 1, "at most one OK interaction per idempotency key")
        # Every caller that SUCCEEDED got a consistent result pointing at it.
        self.assertGreaterEqual(len(results), 1)
        winner_id = str(ok_rows.first().id)
        for res in results:
            self.assertEqual(res.outcome, "OK")
            self.assertEqual(res.interaction_id, winner_id)

    def test_all_successful_callers_get_the_replayable_parsed_body(self):
        results, errors = self._hammer("race-key-2")
        self._assert_backend_errors_ok(errors)
        self.assertGreaterEqual(len(results), 1)
        for res in results:
            self.assertEqual(res.parsed, {"acknowledged": True, "echo": "hi"})
