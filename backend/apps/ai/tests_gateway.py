"""
Phase 7a -- AIGateway (invoke_ai) integration tests. Uses the EchoProvider
end to end (no network, no real credentials) so the full policy -> budget ->
egress -> render -> provider -> schema-validate -> audit pipeline is
exercised for real, not mocked piecewise.

Also covers the foundation-level invariant checks (I1-I6 from
docs/AI_ARCHITECTURE.md) that are actually testable at this milestone --
feature-specific invariants (no-mutation of a real EmissionRecord, etc.)
belong to the feature milestone that introduces the first real caller.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.ai.models import AIInteraction, TenantAIPolicy
from apps.ai.providers.echo import canned
from apps.ai.services.gateway import invoke_ai
from apps.core.models import Organization

User = get_user_model()


def _enable_ai(org, **overrides):
    defaults = {"ai_enabled": True, "provider_override": "echo", "model_override": "echo-1"}
    defaults.update(overrides)
    return TenantAIPolicy.objects.create(organization=org, **defaults)


def _valid_echo_value(echo_text="hi"):
    # EchoProvider's DEFAULT (non-canned) response is a generic
    # {"echo": bool, "input_digest": ...} shape that satisfies no real
    # schema -- it exists to prove determinism/hashing, not to satisfy
    # foundation.selftest's schema. Tests asserting an OK outcome embed a
    # canned() response (matching the schema exactly) as the template's
    # $echo_value, which EchoProvider then echoes back verbatim.
    return canned({"acknowledged": True, "echo": echo_text})


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class InvokeAIHappyPathTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Gateway Org")
        self.user = User.objects.create_user("gateway_actor", password="pw")
        _enable_ai(self.org)

    def test_successful_call_returns_ok_with_parsed_body(self):
        result = invoke_ai(
            organization=self.org, actor=self.user, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": _valid_echo_value("hi")},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        self.assertEqual(result.outcome, "OK")
        self.assertEqual(result.parsed, {"acknowledged": True, "echo": "hi"})
        self.assertIsNotNone(result.interaction_id)

    def test_writes_exactly_one_ai_interaction(self):
        invoke_ai(
            organization=self.org, actor=self.user, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": "x"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        self.assertEqual(AIInteraction.objects.count(), 1)

    def test_interaction_captures_full_reproducibility_metadata(self):
        invoke_ai(
            organization=self.org, actor=self.user, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": _valid_echo_value()},
            response_schema_id="foundation.selftest", response_schema_version=1,
            context_provenance=["record-1", "record-2"],
        )
        interaction = AIInteraction.objects.get()
        self.assertEqual(interaction.provider, "echo")
        self.assertEqual(interaction.model_id, "echo-1")
        self.assertIsNotNone(interaction.prompt_version)
        self.assertTrue(interaction.prompt_template_hash)
        self.assertTrue(interaction.rendered_input_hash)
        self.assertTrue(interaction.response_hash)
        self.assertEqual(interaction.context_provenance, ["record-1", "record-2"])
        self.assertEqual(interaction.parameters["response_schema_id"], "foundation.selftest")
        self.assertTrue(interaction.schema_valid)
        self.assertIsNotNone(interaction.cost_usd)
        self.assertEqual(interaction.gateway_version, "1")

    def test_echo_provider_calls_are_free(self):
        invoke_ai(
            organization=self.org, actor=self.user, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": "x"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        self.assertEqual(AIInteraction.objects.get().cost_usd, Decimal("0"))


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class InvokeAIDisabledTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Disabled Org")

    def test_no_tenant_policy_row_returns_ai_disabled(self):
        result = invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": "x"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        self.assertEqual(result.outcome, "AI_DISABLED")

    def test_disabled_call_still_writes_an_interaction_for_observability(self):
        invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": "x"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        self.assertEqual(AIInteraction.objects.get().outcome, "AI_DISABLED")

    def test_disabled_call_never_touches_prompt_registry_or_provider(self):
        # No AIPromptVersion should be registered for a call that's refused
        # before rendering -- proves the gateway short-circuits BEFORE doing
        # any of that work, not just before recording success.
        from apps.ai.models import AIPromptVersion

        invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": "x"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        self.assertEqual(AIPromptVersion.objects.count(), 0)

    @override_settings(AI_ENABLED=False)
    def test_global_kill_switch_overrides_a_fully_enabled_tenant(self):
        _enable_ai(self.org)
        result = invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": "x"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        self.assertEqual(result.outcome, "AI_DISABLED")


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class InvokeAIBudgetTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Budget Gateway Org")

    def test_over_budget_refuses_before_calling_provider(self):
        _enable_ai(self.org, monthly_budget_usd=Decimal("1.00"))
        AIInteraction.objects.create(
            organization=self.org, capability="x", provider="echo", model_id="echo-1",
            outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
            cost_usd=Decimal("5.00"),
        )
        result = invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": "x"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        self.assertEqual(result.outcome, "BUDGET_EXCEEDED")
        # Only the pre-seeded interaction plus the refusal record -- no
        # provider call happened, so no second real completion was recorded.
        self.assertEqual(AIInteraction.objects.filter(outcome="OK").count(), 1)


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class InvokeAIEgressTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Egress Gateway Org")

    def test_no_egress_tier_permits_echo(self):
        _enable_ai(self.org, egress_tier=TenantAIPolicy.EgressTier.NO_EGRESS)
        result = invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": _valid_echo_value()},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        self.assertEqual(result.outcome, "OK")

    def test_no_egress_tier_blocks_anthropic(self):
        _enable_ai(self.org, provider_override="anthropic", egress_tier=TenantAIPolicy.EgressTier.NO_EGRESS)
        result = invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": "x"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        self.assertEqual(result.outcome, "EGRESS_BLOCKED")

    def test_redacted_tier_strips_pii_before_hash_and_render(self):
        _enable_ai(self.org, egress_tier=TenantAIPolicy.EgressTier.REDACTED)
        invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest",
            template_vars={"echo_value": "contact jane.doe@example.com"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        interaction = AIInteraction.objects.get()
        self.assertTrue(interaction.redaction_applied)


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class InvokeAISchemaValidationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Schema Gateway Org")
        _enable_ai(self.org)

    def test_invalid_response_is_schema_invalid_and_parsed_is_none(self):
        bad_payload = canned({"acknowledged": "not-a-boolean", "echo": "x"})
        result = invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": bad_payload},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        self.assertEqual(result.outcome, "SCHEMA_INVALID")
        self.assertIsNone(result.parsed)

    def test_schema_invalid_response_still_records_cost(self):
        bad_payload = canned({"acknowledged": "not-a-boolean", "echo": "x"})
        invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": bad_payload},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        interaction = AIInteraction.objects.get()
        self.assertIsNotNone(interaction.cost_usd)
        self.assertFalse(interaction.schema_valid)

    def test_unknown_response_schema_id_errors_without_calling_provider(self):
        from apps.ai.models import AIPromptVersion

        result = invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": "x"},
            response_schema_id="no.such.schema", response_schema_version=1,
        )
        self.assertEqual(result.outcome, "ERROR")
        # Never rendered/registered a prompt for an unvalidatable schema.
        self.assertEqual(AIPromptVersion.objects.count(), 0)


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class InvokeAIIdempotencyTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Idempotency Gateway Org")
        _enable_ai(self.org)

    def test_same_idempotency_key_does_not_call_provider_twice(self):
        first = invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": _valid_echo_value()},
            response_schema_id="foundation.selftest", response_schema_version=1,
            idempotency_key="job-42",
        )
        self.assertEqual(first.outcome, "OK")
        second = invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": "different"},
            response_schema_id="foundation.selftest", response_schema_version=1,
            idempotency_key="job-42",
        )
        self.assertEqual(first.interaction_id, second.interaction_id)
        self.assertEqual(AIInteraction.objects.filter(idempotency_key="job-42").count(), 1)

    def test_short_circuited_call_records_a_cache_hit(self):
        # Phase 7g: a short-circuited call writes no new AIInteraction row,
        # so the observability cache-hit counter is its only trace.
        from django.core.cache import cache

        from apps.ai.services.cache_metrics import get_cache_hit_count

        cache.clear()
        invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": _valid_echo_value()},
            response_schema_id="foundation.selftest", response_schema_version=1,
            idempotency_key="job-cache-hit",
        )
        self.assertEqual(get_cache_hit_count(), 0)  # the FIRST call is a real provider call, not a cache hit
        invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": _valid_echo_value()},
            response_schema_id="foundation.selftest", response_schema_version=1,
            idempotency_key="job-cache-hit",
        )
        self.assertEqual(get_cache_hit_count(), 1)

    def test_different_idempotency_keys_both_call_provider(self):
        invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": "x"},
            response_schema_id="foundation.selftest", response_schema_version=1,
            idempotency_key="job-1",
        )
        invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": "y"},
            response_schema_id="foundation.selftest", response_schema_version=1,
            idempotency_key="job-2",
        )
        self.assertEqual(AIInteraction.objects.count(), 2)

    def test_empty_idempotency_key_never_short_circuits(self):
        invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": "x"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": "x"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        self.assertEqual(AIInteraction.objects.count(), 2)


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class InvokeAITenantIsolationTests(TestCase):
    """I3: no cross-tenant context ever enters a prompt or a budget total."""

    def test_interactions_are_scoped_to_their_own_organization(self):
        org_a = Organization.objects.create(name="Org A")
        org_b = Organization.objects.create(name="Org B")
        _enable_ai(org_a)
        _enable_ai(org_b)

        invoke_ai(
            organization=org_a, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": "a-secret"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        invoke_ai(
            organization=org_b, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": "b-secret"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )

        self.assertEqual(AIInteraction.objects.filter(organization=org_a).count(), 1)
        self.assertEqual(AIInteraction.objects.filter(organization=org_b).count(), 1)


class InvokeAINoGovernedDataMutationTests(TestCase):
    """I1/I2: structural proof, not behavioral -- the gateway module has no
    import of, and cannot construct, EmissionRecord/EmissionCalculation."""

    def test_gateway_module_never_imports_governed_models(self):
        import ast
        from pathlib import Path

        source = Path(__file__).resolve().parent.joinpath("services", "gateway.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
            elif isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)

        self.assertNotIn("apps.ingestion.models", imported_modules)
        self.assertNotIn("apps.carbon.models", imported_modules)
