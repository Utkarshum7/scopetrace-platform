"""
Phase 7f -- apps.ai.services.report_narration tests. Like factor_
recommendation's and esg_assistant's tests, this mocks invoke_ai directly
for exact-response cases: the context this prompt consumes is built live
from real, approved-only DB queries, not a single bounded field a
canned() marker could hide inside. Refusal-path tests still exercise the
real EchoProvider end to end, since those outcomes don't depend on any
specific response content.
"""
from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.ai.models import AIInteraction, AIReportNarration, TenantAIPolicy
from apps.ai.services.gateway import AIGatewayResult
from apps.ai.services.report_narration import generate_report_narration
from apps.core.models import Organization


def _make_interaction(org):
    return AIInteraction.objects.create(
        organization=org, capability="report_narration", provider="echo", model_id="echo-1",
        outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
    )


_VALID_PARSED = {
    "executive_summary": "Total emissions for Q1 were 512.30 tCO2e.",
    "key_highlights": ["Emissions declined for five consecutive months"],
    "trend_explanations": "Monthly emissions fell steadily across the period.",
    "recommendations": ["Investigate the drivers behind the sustained decline."],
    "confidence": "HIGH",
}


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class GenerateReportNarrationHappyPathTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Report Narration Service Org")
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, provider_override="echo")
        self.date_from, self.date_to = date(2026, 1, 1), date(2026, 3, 31)

    def _invoke_ai_returning(self, parsed):
        interaction = _make_interaction(self.org)
        return AIGatewayResult(outcome=AIInteraction.Outcome.OK, interaction_id=str(interaction.id), parsed=parsed)

    def test_creates_a_narration_on_success(self):
        with patch(
            "apps.ai.services.report_narration.invoke_ai",
            return_value=self._invoke_ai_returning(_VALID_PARSED),
        ) as mocked:
            narration = generate_report_narration(self.org, self.date_from, self.date_to)
        self.assertIsNotNone(narration)
        self.assertEqual(narration.executive_summary, _VALID_PARSED["executive_summary"])
        self.assertEqual(narration.key_highlights, _VALID_PARSED["key_highlights"])
        self.assertEqual(narration.confidence, "HIGH")
        mocked.assert_called_once()

    def test_scope_defaults_to_empty_string_when_none(self):
        with patch(
            "apps.ai.services.report_narration.invoke_ai",
            return_value=self._invoke_ai_returning(_VALID_PARSED),
        ):
            narration = generate_report_narration(self.org, self.date_from, self.date_to, scope=None)
        self.assertEqual(narration.scope, "")

    def test_scope_is_persisted_when_given(self):
        with patch(
            "apps.ai.services.report_narration.invoke_ai",
            return_value=self._invoke_ai_returning(_VALID_PARSED),
        ):
            narration = generate_report_narration(self.org, self.date_from, self.date_to, scope="SCOPE_1")
        self.assertEqual(narration.scope, "SCOPE_1")

    def test_links_back_to_the_ai_interaction(self):
        with patch(
            "apps.ai.services.report_narration.invoke_ai",
            return_value=self._invoke_ai_returning(_VALID_PARSED),
        ):
            narration = generate_report_narration(self.org, self.date_from, self.date_to)
        self.assertIsNotNone(narration.interaction)
        self.assertEqual(narration.interaction.capability, "report_narration")

    def test_regenerating_creates_a_second_independent_narration(self):
        with patch(
            "apps.ai.services.report_narration.invoke_ai",
            return_value=self._invoke_ai_returning(_VALID_PARSED),
        ):
            first = generate_report_narration(self.org, self.date_from, self.date_to)
            second = generate_report_narration(self.org, self.date_from, self.date_to)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(AIReportNarration.objects.filter(organization=self.org).count(), 2)

    def test_never_writes_to_governed_models(self):
        from apps.carbon.models import EmissionCalculation, EmissionFactor
        from apps.ingestion.models import EmissionRecord

        before_records = EmissionRecord.objects.count()
        before_calcs = EmissionCalculation.objects.count()
        before_factors = EmissionFactor.objects.count()

        with patch(
            "apps.ai.services.report_narration.invoke_ai",
            return_value=self._invoke_ai_returning(_VALID_PARSED),
        ):
            generate_report_narration(self.org, self.date_from, self.date_to)

        self.assertEqual(EmissionRecord.objects.count(), before_records)
        self.assertEqual(EmissionCalculation.objects.count(), before_calcs)
        self.assertEqual(EmissionFactor.objects.count(), before_factors)


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class GenerateReportNarrationRefusalTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Report Narration Refusal Org")
        self.date_from, self.date_to = date(2026, 1, 1), date(2026, 3, 31)

    def test_ai_disabled_returns_none_and_creates_no_narration(self):
        # No TenantAIPolicy row -- AI disabled for this org.
        narration = generate_report_narration(self.org, self.date_from, self.date_to)
        self.assertIsNone(narration)
        self.assertEqual(AIReportNarration.objects.count(), 0)

    def test_schema_invalid_response_returns_none_and_creates_no_narration(self):
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, provider_override="echo")
        # EchoProvider's default (non-canned) response never matches the
        # report_narration schema.
        narration = generate_report_narration(self.org, self.date_from, self.date_to)
        self.assertIsNone(narration)
        self.assertEqual(AIReportNarration.objects.count(), 0)
        self.assertTrue(
            AIInteraction.objects.filter(capability="report_narration", outcome="SCHEMA_INVALID").exists()
        )
