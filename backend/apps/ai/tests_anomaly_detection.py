"""
Phase 7b -- apps.ai.services.anomaly_detection tests. Uses EchoProvider
end to end (canned() responses for the happy path) -- no network, no real
credentials, no cost.
"""
from django.test import TestCase, override_settings

from apps.ai.models import AIAnnotation, AIInteraction, TenantAIPolicy
from apps.ai.providers.echo import canned
from apps.ai.services.anomaly_detection import _format_validation_flags, generate_anomaly_explanation
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch


def _make_batch(org, ds):
    return UploadBatch.objects.create(organization=org, data_source=ds, file_name="anomaly_test.csv")


def _make_suspicious_record(org, batch, **extra):
    defaults = dict(
        organization=org, batch=batch, row_index=1, raw_data_payload={"a": 1},
        status=EmissionRecord.RecordStatus.SUSPICIOUS, is_suspicious=True,
        normalized_value=500, normalized_unit="L", scope_category="SCOPE_1",
        validation_errors={"quantity": ["Quantity 500.00 is more than 5.0x the batch median (95.00)."]},
    )
    defaults.update(extra)
    return EmissionRecord.objects.create(**defaults)


_VALID_RESPONSE = {
    "explanation": "Quantity is far above the batch median.",
    "contributing_factors": ["bulk purchase", "possible data-entry error"],
    "confidence": "HIGH",
    "suggested_investigation": "Confirm with the site's fuel log.",
}


class FormatValidationFlagsTests(TestCase):
    def test_formats_field_message_pairs(self):
        result = _format_validation_flags({"quantity": ["too high"], "date": ["too old"]})
        self.assertIn("quantity: too high", result)
        self.assertIn("date: too old", result)

    def test_multiple_messages_for_one_field_each_get_a_line(self):
        result = _format_validation_flags({"quantity": ["reason A", "reason B"]})
        self.assertIn("quantity: reason A", result)
        self.assertIn("quantity: reason B", result)

    def test_empty_dict_returns_placeholder_not_empty_string(self):
        result = _format_validation_flags({})
        self.assertTrue(result)
        self.assertIn("no specific validation messages", result)

    def test_none_returns_placeholder(self):
        result = _format_validation_flags(None)
        self.assertIn("no specific validation messages", result)


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class GenerateAnomalyExplanationHappyPathTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Anomaly Service Org")
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, provider_override="echo")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)

    def test_creates_an_annotation_on_success(self):
        record = _make_suspicious_record(
            self.org, self.batch,
            validation_errors={"quantity": [canned(_VALID_RESPONSE)]},
        )
        annotation = generate_anomaly_explanation(record)
        self.assertIsNotNone(annotation)
        self.assertEqual(annotation.explanation, _VALID_RESPONSE["explanation"])
        self.assertEqual(annotation.confidence, "HIGH")
        self.assertEqual(annotation.record, record)
        self.assertEqual(annotation.capability, AIAnnotation.Capability.ANOMALY_DETECTION)

    def test_links_back_to_the_ai_interaction(self):
        record = _make_suspicious_record(
            self.org, self.batch,
            validation_errors={"quantity": [canned(_VALID_RESPONSE)]},
        )
        annotation = generate_anomaly_explanation(record)
        self.assertIsNotNone(annotation.interaction)
        self.assertEqual(annotation.interaction.capability, "anomaly_detection")
        self.assertEqual(annotation.interaction.outcome, "OK")

    def test_context_provenance_references_the_record(self):
        record = _make_suspicious_record(
            self.org, self.batch,
            validation_errors={"quantity": [canned(_VALID_RESPONSE)]},
        )
        annotation = generate_anomaly_explanation(record)
        self.assertEqual(annotation.interaction.context_provenance, [str(record.id)])

    def test_never_mutates_the_record(self):
        record = _make_suspicious_record(
            self.org, self.batch,
            validation_errors={"quantity": [canned(_VALID_RESPONSE)]},
        )
        original_status = record.status
        original_validation_errors = dict(record.validation_errors)
        generate_anomaly_explanation(record)
        record.refresh_from_db()
        self.assertEqual(record.status, original_status)
        self.assertEqual(record.validation_errors, original_validation_errors)
        self.assertTrue(record.is_suspicious)


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class GenerateAnomalyExplanationRefusalTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Anomaly Refusal Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)

    def test_ai_disabled_returns_none_and_creates_no_annotation(self):
        # No TenantAIPolicy row -- AI disabled for this org.
        record = _make_suspicious_record(self.org, self.batch)
        annotation = generate_anomaly_explanation(record)
        self.assertIsNone(annotation)
        self.assertEqual(AIAnnotation.objects.count(), 0)

    def test_schema_invalid_response_returns_none_and_creates_no_annotation(self):
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, provider_override="echo")
        # EchoProvider's default (non-canned) response never matches the
        # anomaly_detection schema.
        record = _make_suspicious_record(self.org, self.batch)
        annotation = generate_anomaly_explanation(record)
        self.assertIsNone(annotation)
        self.assertEqual(AIAnnotation.objects.count(), 0)
        # But the attempt is still audited.
        self.assertTrue(
            AIInteraction.objects.filter(capability="anomaly_detection", outcome="SCHEMA_INVALID").exists()
        )
