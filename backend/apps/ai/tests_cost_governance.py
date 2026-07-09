"""
Phase 7g -- apps.ai.services.cost_governance tests. Pure, read-only
aggregation, reusing resolve_policy()/check_budget() directly for budget
utilization.
"""
from django.test import TestCase, override_settings

from apps.ai.models import AIInteraction, TenantAIPolicy
from apps.ai.services.cost_governance import org_cost_summary
from apps.core.models import Organization


def _make_interaction(org, **extra):
    defaults = dict(
        organization=org, capability="anomaly_detection", provider="echo", model_id="echo-1",
        outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
    )
    defaults.update(extra)
    return AIInteraction.objects.create(**defaults)


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class OrgCostSummaryTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Cost Governance Org")

    def test_token_consumption_and_spend_summed(self):
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, monthly_budget_usd="50.00")
        _make_interaction(self.org, input_tokens=1000, output_tokens=500, cost_usd="0.010000")
        _make_interaction(self.org, input_tokens=2000, output_tokens=1000, cost_usd="0.020000")

        summary = org_cost_summary(self.org)
        self.assertEqual(summary["token_consumption"]["input_tokens"], 3000)
        self.assertEqual(summary["token_consumption"]["output_tokens"], 1500)
        self.assertEqual(summary["estimated_spend_usd"], "0.030000")

    def test_budget_utilization_reuses_check_budget(self):
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, monthly_budget_usd="10.00")
        _make_interaction(self.org, cost_usd="5.000000")

        summary = org_cost_summary(self.org)
        self.assertEqual(summary["budget"]["spent_usd"], "5.000000")
        self.assertEqual(summary["budget"]["budget_usd"], "10.00")
        self.assertEqual(summary["budget"]["utilization_pct"], 50.0)
        self.assertFalse(summary["budget"]["over_budget"])

    def test_over_budget_is_flagged(self):
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, monthly_budget_usd="1.00")
        _make_interaction(self.org, cost_usd="2.000000")

        summary = org_cost_summary(self.org)
        self.assertTrue(summary["budget"]["over_budget"])

    def test_provider_and_capability_distribution(self):
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True)
        _make_interaction(self.org, provider="echo", capability="anomaly_detection")
        _make_interaction(self.org, provider="replay", capability="esg_assistant")

        summary = org_cost_summary(self.org)
        self.assertEqual(summary["provider_distribution"], {"echo": 1, "replay": 1})
        self.assertEqual(summary["capability_distribution"], {"anomaly_detection": 1, "esg_assistant": 1})

    def test_scoped_to_the_given_organization_only(self):
        other_org = Organization.objects.create(name="Cost Governance Other Org")
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True)
        _make_interaction(self.org, cost_usd="1.000000")
        _make_interaction(other_org, cost_usd="999.000000")

        summary = org_cost_summary(self.org)
        self.assertEqual(summary["estimated_spend_usd"], "1.000000")

    def test_ai_disabled_org_still_returns_a_summary(self):
        # No TenantAIPolicy row at all -- resolve_policy() falls back to
        # the disabled default; the summary should still resolve cleanly,
        # not raise.
        summary = org_cost_summary(self.org)
        self.assertFalse(summary["ai_enabled"])
        self.assertIn("budget_usd", summary["budget"])

    def test_never_mutates_any_ai_interaction(self):
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True)
        interaction = _make_interaction(self.org, cost_usd="1.000000")
        before = AIInteraction.objects.get(pk=interaction.pk).cost_usd

        org_cost_summary(self.org)

        self.assertEqual(AIInteraction.objects.get(pk=interaction.pk).cost_usd, before)
