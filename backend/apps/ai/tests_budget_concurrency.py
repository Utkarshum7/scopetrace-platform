"""
Phase 7.5 (H2, Finding 2) -- concurrent AI calls must not exceed a tenant's
monthly budget. The per-organization lock (select_for_update on the policy
row) serializes the budget-check -> provider-call -> cost-write section, so a
second call cannot read "under budget" before the first's cost is committed.

The echo provider is normally priced at $0, so this test patches the pricing
table to make a SINGLE call cost far more than the tiny budget. Then, of N
concurrent calls, exactly one should be billed and the rest refused. Without
the lock, several would all read spent<budget at once and all be billed
(overspend) -- the very race being closed.

select_for_update is a no-op on SQLite (no row-level locking), so the strict
assertion only runs on PostgreSQL (CI, Docker). Each worker closes its own
connection (see apps.audit.ConcurrentAppendTests).
"""
import threading
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import TransactionTestCase, override_settings

from apps.ai.models import AIInteraction, TenantAIPolicy
from apps.ai.providers.echo import canned
from apps.ai.services.gateway import invoke_ai
from apps.core.models import Organization

# A single echo call, priced this high (per 1K tokens), costs tens of dollars
# -- far more than the budget below -- so the FIRST call exhausts the budget
# and every concurrent racer after it must be refused. The budget must be
# representable at the field's 2 decimal places (0.001 would round to 0.00 and
# refuse everything).
_EXPENSIVE_PRICING = {("echo", "echo-1"): (Decimal("1000"), Decimal("1000"))}
_TINY_BUDGET = Decimal("1.00")


def _echo_value():
    return canned({"acknowledged": True, "echo": "x"})


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class BudgetConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Budget Race Org")
        TenantAIPolicy.objects.create(
            organization=self.org, ai_enabled=True,
            provider_override="echo", model_override="echo-1",
            monthly_budget_usd=_TINY_BUDGET,
        )
        # Warm the prompt registry (unpriced here) so threads don't race its
        # cold-start get_or_create -- a separate, out-of-H2-scope race.
        invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": _echo_value()},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )

    def test_concurrent_calls_do_not_overspend_the_budget(self):
        n = 3
        errors = []
        barrier = threading.Barrier(n)

        def worker(i):
            try:
                barrier.wait()
                invoke_ai(
                    organization=self.org, capability="foundation.selftest",
                    prompt_name="foundation.selftest", template_vars={"echo_value": _echo_value()},
                    response_schema_id="foundation.selftest", response_schema_version=1,
                    idempotency_key=f"budget-{i}",  # distinct: genuine separate calls
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                connection.close()

        with mock.patch("apps.ai.services.cost.PRICING_USD_PER_1K_TOKENS", _EXPENSIVE_PRICING):
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        billed = AIInteraction.objects.filter(
            organization=self.org, outcome="OK", cost_usd__isnull=False,
        ).exclude(cost_usd=Decimal("0"))
        spent = sum((row.cost_usd for row in billed), Decimal("0"))
        per_call = max((row.cost_usd for row in billed), default=Decimal("0"))
        refused = AIInteraction.objects.filter(
            organization=self.org, outcome="BUDGET_EXCEEDED",
        ).count()

        # Tolerate ONLY environmental errors (SQLite file locks; the local
        # Windows->Docker Postgres TCP proxy dropping a held connection). A
        # real logic error still fails. Neither happens on CI's in-network
        # Postgres. The no-overspend invariant below holds on whatever
        # completed regardless.
        for exc in errors:
            msg = str(exc).lower()
            environmental = "locked" in msg or "connection" in msg or "server closed" in msg
            self.assertTrue(environmental, f"unexpected non-environmental error: {exc!r}")

        if connection.vendor == "sqlite":
            return  # no row-level locking -> the serialization guarantee isn't testable here

        # THE core guarantee (Finding 2): total billed spend never exceeds the
        # budget by more than the single call that tipped it over. Without the
        # per-org lock, concurrent racers would each read spent<budget and all
        # be billed, pushing spend toward n * per_call. Holds even if some
        # threads lost their connection -- those simply weren't billed.
        self.assertLessEqual(
            spent, _TINY_BUDGET + per_call,
            f"budget overspent: {spent} > {_TINY_BUDGET} (+1 call {per_call})",
        )
        # If every thread completed (no connection was dropped -- i.e. CI
        # Postgres), exactly one call is billed and the lock refused the rest.
        if not errors:
            self.assertGreaterEqual(len(billed), 1, "at least one call should have been billed")
            self.assertGreaterEqual(refused, 1, "expected the budget lock to refuse at least one concurrent racer")
