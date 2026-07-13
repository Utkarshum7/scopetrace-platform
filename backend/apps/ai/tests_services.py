"""Phase 7a -- policy/cost/egress service tests."""
from decimal import Decimal

from django.test import TestCase, override_settings

from apps.ai.models import AIInteraction, TenantAIPolicy
from apps.ai.services.cost import check_budget, estimate_cost_usd
from apps.ai.services.egress import (
    AIEgressBlocked,
    enforce_provider_allowed,
    redact_template_vars,
)
from apps.ai.services.policy import resolve_policy
from apps.core.models import Organization


class ResolvePolicyTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Policy Org")

    @override_settings(AI_ENABLED=False)
    def test_global_kill_switch_disables_regardless_of_tenant_policy(self):
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True)
        policy = resolve_policy(self.org)
        self.assertFalse(policy.ai_enabled)

    @override_settings(AI_ENABLED=True)
    def test_missing_tenant_policy_row_resolves_disabled(self):
        policy = resolve_policy(self.org)
        self.assertFalse(policy.ai_enabled)

    @override_settings(AI_ENABLED=True)
    def test_tenant_policy_row_present_but_disabled(self):
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=False)
        policy = resolve_policy(self.org)
        self.assertFalse(policy.ai_enabled)

    @override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
    def test_tenant_enabled_with_no_overrides_uses_platform_defaults(self):
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True)
        policy = resolve_policy(self.org)
        self.assertTrue(policy.ai_enabled)
        self.assertEqual(policy.provider, "echo")
        self.assertEqual(policy.model, "echo-1")

    @override_settings(AI_ENABLED=True, AI_PROVIDER="echo")
    def test_tenant_overrides_win_over_platform_defaults(self):
        TenantAIPolicy.objects.create(
            organization=self.org, ai_enabled=True,
            provider_override="anthropic", model_override="claude-sonnet-5",
            monthly_budget_usd=Decimal("100.00"), egress_tier=TenantAIPolicy.EgressTier.NO_EGRESS,
        )
        policy = resolve_policy(self.org)
        self.assertEqual(policy.provider, "anthropic")
        self.assertEqual(policy.model, "claude-sonnet-5")
        self.assertEqual(policy.monthly_budget_usd, Decimal("100.00"))
        self.assertEqual(policy.egress_tier, "NO_EGRESS")


class EstimateCostTests(TestCase):
    def test_known_model_uses_its_pricing(self):
        cost = estimate_cost_usd("anthropic", "claude-sonnet-5", input_tokens=1000, output_tokens=1000)
        self.assertEqual(cost, Decimal("0.003") + Decimal("0.015"))

    def test_echo_is_free(self):
        cost = estimate_cost_usd("echo", "echo-1", input_tokens=1000, output_tokens=1000)
        self.assertEqual(cost, Decimal("0"))

    def test_unknown_model_falls_back_to_default_price_not_free(self):
        cost = estimate_cost_usd("anthropic", "some-future-model", input_tokens=1000, output_tokens=0)
        self.assertGreater(cost, Decimal("0"))

    def test_none_tokens_treated_as_zero(self):
        cost = estimate_cost_usd("echo", "echo-1", input_tokens=None, output_tokens=None)
        self.assertEqual(cost, Decimal("0"))


class CheckBudgetTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Budget Org")

    def _make_interaction(self, cost_usd, outcome=AIInteraction.Outcome.OK):
        return AIInteraction.objects.create(
            organization=self.org, capability="x", provider="echo", model_id="echo-1",
            outcome=outcome, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
            cost_usd=cost_usd,
        )

    def test_no_spend_is_within_any_positive_budget(self):
        status = check_budget(self.org, Decimal("10.00"))
        self.assertTrue(status.ok)
        self.assertEqual(status.spent_usd, Decimal("0"))

    def test_spend_under_budget_is_ok(self):
        self._make_interaction(Decimal("1.50"))
        status = check_budget(self.org, Decimal("10.00"))
        self.assertTrue(status.ok)
        self.assertEqual(status.spent_usd, Decimal("1.50"))

    def test_spend_at_or_over_budget_is_not_ok(self):
        self._make_interaction(Decimal("10.00"))
        status = check_budget(self.org, Decimal("10.00"))
        self.assertFalse(status.ok)

    def test_schema_invalid_outcome_still_counts_toward_spend(self):
        # A schema-invalid response still consumed real, billable tokens.
        self._make_interaction(Decimal("2.00"), outcome=AIInteraction.Outcome.SCHEMA_INVALID)
        status = check_budget(self.org, Decimal("10.00"))
        self.assertEqual(status.spent_usd, Decimal("2.00"))

    def test_null_cost_interactions_do_not_count(self):
        # e.g. AI_DISABLED / BUDGET_EXCEEDED refusals never reached a provider.
        self._make_interaction(None, outcome=AIInteraction.Outcome.AI_DISABLED)
        status = check_budget(self.org, Decimal("10.00"))
        self.assertEqual(status.spent_usd, Decimal("0"))

    def test_other_organizations_spend_is_not_counted(self):
        other_org = Organization.objects.create(name="Other Budget Org")
        AIInteraction.objects.create(
            organization=other_org, capability="x", provider="echo", model_id="echo-1",
            outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
            cost_usd=Decimal("9.00"),
        )
        status = check_budget(self.org, Decimal("10.00"))
        self.assertEqual(status.spent_usd, Decimal("0"))


class EnforceProviderAllowedTests(TestCase):
    def test_no_egress_tier_permits_echo(self):
        enforce_provider_allowed("echo", "NO_EGRESS")  # must not raise

    def test_no_egress_tier_blocks_anthropic(self):
        with self.assertRaises(AIEgressBlocked):
            enforce_provider_allowed("anthropic", "NO_EGRESS")

    def test_no_egress_tier_blocks_openai(self):
        with self.assertRaises(AIEgressBlocked):
            enforce_provider_allowed("openai", "NO_EGRESS")

    def test_redacted_tier_permits_any_provider(self):
        enforce_provider_allowed("anthropic", "REDACTED")
        enforce_provider_allowed("openai", "REDACTED")
        enforce_provider_allowed("echo", "REDACTED")

    def test_raw_tier_permits_any_provider(self):
        enforce_provider_allowed("anthropic", "RAW")


class RedactTemplateVarsTests(TestCase):
    def test_redacted_tier_scrubs_email(self):
        result = redact_template_vars({"note": "contact jane.doe@example.com"}, "REDACTED")
        self.assertNotIn("jane.doe@example.com", result.values["note"])
        self.assertTrue(result.redacted)

    def test_redacted_tier_scrubs_long_digit_sequences(self):
        result = redact_template_vars({"note": "account 1234567890"}, "REDACTED")
        self.assertNotIn("1234567890", result.values["note"])
        self.assertTrue(result.redacted)

    def test_redacted_tier_leaves_clean_text_unchanged(self):
        result = redact_template_vars({"note": "no sensitive content here"}, "REDACTED")
        self.assertEqual(result.values["note"], "no sensitive content here")
        self.assertFalse(result.redacted)

    def test_raw_tier_skips_redaction_entirely(self):
        result = redact_template_vars({"note": "contact jane.doe@example.com"}, "RAW")
        self.assertIn("jane.doe@example.com", result.values["note"])
        self.assertFalse(result.redacted)

    def test_non_string_values_pass_through_untouched(self):
        result = redact_template_vars({"count": 42, "flag": True}, "REDACTED")
        self.assertEqual(result.values["count"], 42)
        self.assertEqual(result.values["flag"], True)

    # Phase 7.5 (H4-3): before this fix, only TOP-LEVEL string values were
    # scrubbed -- a nested dict/list value passed through completely
    # unredacted. Latent (every current caller passes flat strings), but a
    # structural gap. These pin the recursive fix.
    def test_redacted_tier_scrubs_a_string_nested_in_a_dict(self):
        result = redact_template_vars(
            {"contact": {"email": "jane.doe@example.com", "note": "clean"}}, "REDACTED",
        )
        self.assertNotIn("jane.doe@example.com", result.values["contact"]["email"])
        self.assertEqual(result.values["contact"]["note"], "clean")
        self.assertTrue(result.redacted)

    def test_redacted_tier_scrubs_a_string_nested_in_a_list(self):
        result = redact_template_vars(
            {"notes": ["clean entry", "contact jane.doe@example.com"]}, "REDACTED",
        )
        self.assertEqual(result.values["notes"][0], "clean entry")
        self.assertNotIn("jane.doe@example.com", result.values["notes"][1])
        self.assertTrue(result.redacted)

    def test_redacted_tier_scrubs_a_string_nested_several_levels_deep(self):
        result = redact_template_vars(
            {"items": [{"contacts": ["jane.doe@example.com"]}]}, "REDACTED",
        )
        self.assertNotIn("jane.doe@example.com", result.values["items"][0]["contacts"][0])
        self.assertTrue(result.redacted)

    def test_redacted_tier_recursion_preserves_structure_and_non_string_leaves(self):
        original = {"meta": {"count": 3, "tags": ["a", "b"]}, "flag": True}
        result = redact_template_vars(original, "REDACTED")
        self.assertEqual(result.values, original)
        self.assertFalse(result.redacted)

    def test_raw_tier_skips_redaction_for_nested_values_too(self):
        result = redact_template_vars(
            {"contact": {"email": "jane.doe@example.com"}}, "RAW",
        )
        self.assertIn("jane.doe@example.com", result.values["contact"]["email"])
        self.assertFalse(result.redacted)
