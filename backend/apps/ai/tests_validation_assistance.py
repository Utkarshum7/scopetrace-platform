"""
Phase 7d -- apps.ai.services.validation_assistance tests. Uses EchoProvider
end to end (canned() responses for the happy path) -- no network, no real
credentials, no cost. validation_errors is a JSONField (unbounded), so the
canned()-in-a-JSONField technique from tests_anomaly_detection.py works
here directly, unlike factor_recommendation's tests (which had to mock
invoke_ai because every template var there came from a bounded DB column).
"""
from django.test import TestCase, override_settings

from apps.ai.models import AIAnnotation, AIInteraction, TenantAIPolicy
from apps.ai.providers.echo import canned
from apps.ai.services.validation_assistance import (
    _format_raw_payload,
    _format_validation_errors,
    generate_validation_assistance,
)
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch


def _make_batch(org, ds):
    return UploadBatch.objects.create(organization=org, data_source=ds, file_name="validation_assist_test.csv")


def _make_failed_record(org, batch, **extra):
    defaults = dict(
        organization=org, batch=batch, row_index=1, raw_data_payload={"Menge": "-500,00"},
        status=EmissionRecord.RecordStatus.FAILED,
        validation_errors={"quantity": ["Negative quantities are not permitted."]},
    )
    defaults.update(extra)
    return EmissionRecord.objects.create(**defaults)


_VALID_RESPONSE = {
    "explanation": "The quantity is negative, likely a sign error.",
    "affected_fields": ["Menge"],
    "confidence": "MEDIUM",
    "suggested_correction": "Re-enter the row with the correct sign.",
}


class FormatValidationErrorsTests(TestCase):
    def test_formats_field_message_pairs(self):
        result = _format_validation_errors({"quantity": ["too low"], "unit": ["unknown"]})
        self.assertIn("quantity: too low", result)
        self.assertIn("unit: unknown", result)

    def test_multiple_messages_for_one_field_each_get_a_line(self):
        result = _format_validation_errors({"quantity": ["reason A", "reason B"]})
        self.assertIn("quantity: reason A", result)
        self.assertIn("quantity: reason B", result)

    def test_empty_dict_returns_placeholder_not_empty_string(self):
        result = _format_validation_errors({})
        self.assertTrue(result)
        self.assertIn("no specific validation messages", result)

    def test_none_returns_placeholder(self):
        result = _format_validation_errors(None)
        self.assertIn("no specific validation messages", result)


class FormatRawPayloadTests(TestCase):
    def test_formats_dict_as_string(self):
        result = _format_raw_payload({"Menge": "-500,00", "Einheit": "L"})
        self.assertIn("Menge", result)
        self.assertIn("-500,00", result)

    def test_empty_dict_returns_placeholder(self):
        result = _format_raw_payload({})
        self.assertIn("no raw payload", result)

    def test_none_returns_placeholder(self):
        result = _format_raw_payload(None)
        self.assertIn("no raw payload", result)


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class GenerateValidationAssistanceHappyPathTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Validation Assist Service Org")
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, provider_override="echo")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)

    def test_creates_an_annotation_on_success(self):
        record = _make_failed_record(
            self.org, self.batch,
            validation_errors={"quantity": [canned(_VALID_RESPONSE)]},
        )
        annotation = generate_validation_assistance(record)
        self.assertIsNotNone(annotation)
        self.assertEqual(annotation.explanation, _VALID_RESPONSE["explanation"])
        self.assertEqual(annotation.contributing_factors, _VALID_RESPONSE["affected_fields"])
        self.assertEqual(annotation.confidence, "MEDIUM")
        self.assertEqual(annotation.suggested_investigation, _VALID_RESPONSE["suggested_correction"])
        self.assertEqual(annotation.record, record)
        self.assertEqual(annotation.capability, AIAnnotation.Capability.VALIDATION_ASSISTANCE)

    def test_links_back_to_the_ai_interaction(self):
        record = _make_failed_record(
            self.org, self.batch,
            validation_errors={"quantity": [canned(_VALID_RESPONSE)]},
        )
        annotation = generate_validation_assistance(record)
        self.assertIsNotNone(annotation.interaction)
        self.assertEqual(annotation.interaction.capability, "validation_assistance")
        self.assertEqual(annotation.interaction.outcome, "OK")

    def test_context_provenance_references_the_record(self):
        record = _make_failed_record(
            self.org, self.batch,
            validation_errors={"quantity": [canned(_VALID_RESPONSE)]},
        )
        annotation = generate_validation_assistance(record)
        self.assertEqual(annotation.interaction.context_provenance, [str(record.id)])

    def test_never_mutates_the_record(self):
        record = _make_failed_record(
            self.org, self.batch,
            validation_errors={"quantity": [canned(_VALID_RESPONSE)]},
        )
        original_status = record.status
        original_validation_errors = dict(record.validation_errors)
        generate_validation_assistance(record)
        record.refresh_from_db()
        self.assertEqual(record.status, original_status)
        self.assertEqual(record.validation_errors, original_validation_errors)
        self.assertEqual(record.status, EmissionRecord.RecordStatus.FAILED)


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class GenerateValidationAssistanceRefusalTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Validation Assist Refusal Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)

    def test_ai_disabled_returns_none_and_creates_no_annotation(self):
        # No TenantAIPolicy row -- AI disabled for this org.
        record = _make_failed_record(self.org, self.batch)
        annotation = generate_validation_assistance(record)
        self.assertIsNone(annotation)
        self.assertEqual(AIAnnotation.objects.count(), 0)

    def test_schema_invalid_response_returns_none_and_creates_no_annotation(self):
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, provider_override="echo")
        # EchoProvider's default (non-canned) response never matches the
        # validation_assistance schema.
        record = _make_failed_record(self.org, self.batch)
        annotation = generate_validation_assistance(record)
        self.assertIsNone(annotation)
        self.assertEqual(AIAnnotation.objects.count(), 0)
        self.assertTrue(
            AIInteraction.objects.filter(capability="validation_assistance", outcome="SCHEMA_INVALID").exists()
        )
