"""
Phase 7d -- apps.ai.tasks.generate_validation_assistance_task tests, plus
confirming ingest_task dispatches it on a successful run (mirrors
apps.ai.tests_anomaly_task's exact style: CELERY_TASK_ALWAYS_EAGER is
forced True under the test runner, so .delay(...) executes inline/
synchronously here -- no mocking of Celery dispatch needed).
"""
from datetime import date

from django.test import TestCase, override_settings

from apps.ai.models import AIAnnotation, TenantAIPolicy
from apps.ai.providers.echo import canned
from apps.ai.tasks import generate_validation_assistance_task
from apps.core.models import DataSource, Organization
from apps.core.storage import get_storage_service
from apps.ingestion.models import EmissionRecord, UploadBatch
from apps.ingestion.tasks import ingest_task

_VALID_RESPONSE = {
    "explanation": "The quantity is negative, likely a sign error.",
    "affected_fields": ["quantity"],
    "confidence": "MEDIUM",
    "suggested_correction": "Re-enter the row with the correct sign.",
}


def _make_batch(org, ds):
    return UploadBatch.objects.create(organization=org, data_source=ds, file_name="validation_assist_task_test.csv")


def _make_failed_record(org, batch, row_index=1, **extra):
    defaults = dict(
        organization=org, batch=batch, row_index=row_index, raw_data_payload={"a": 1},
        status=EmissionRecord.RecordStatus.FAILED,
        validation_errors={"quantity": ["Negative quantity is physically impossible."]},
    )
    defaults.update(extra)
    return EmissionRecord.objects.create(**defaults)


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class GenerateValidationAssistanceTaskTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Validation Assist Task Org")
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, provider_override="echo")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)

    def test_generates_annotations_only_for_failed_records(self):
        failed = _make_failed_record(
            self.org, self.batch, row_index=1,
            validation_errors={"quantity": [canned(_VALID_RESPONSE)]},
        )
        clean = EmissionRecord.objects.create(
            organization=self.org, batch=self.batch, row_index=2, raw_data_payload={"a": 2},
            status=EmissionRecord.RecordStatus.DRAFT,
            normalized_value=10, normalized_unit="L", scope_category="SCOPE_1",
        )
        generate_validation_assistance_task(batch_id=str(self.batch.id))
        self.assertEqual(AIAnnotation.objects.filter(record=failed).count(), 1)
        self.assertEqual(AIAnnotation.objects.filter(record=clean).count(), 0)

    def test_suspicious_records_are_not_included(self):
        # SUSPICIOUS is a different, already-handled capability
        # (anomaly_detection, Phase 7b) -- validation_assistance is scoped
        # to FAILED only.
        suspicious = EmissionRecord.objects.create(
            organization=self.org, batch=self.batch, row_index=1, raw_data_payload={"a": 1},
            status=EmissionRecord.RecordStatus.SUSPICIOUS, is_suspicious=True,
            normalized_value=500, normalized_unit="L", scope_category="SCOPE_1",
        )
        generate_validation_assistance_task(batch_id=str(self.batch.id))
        self.assertEqual(AIAnnotation.objects.filter(record=suspicious).count(), 0)

    def test_idempotent_skips_already_annotated_records(self):
        record = _make_failed_record(
            self.org, self.batch,
            validation_errors={"quantity": [canned(_VALID_RESPONSE)]},
        )
        generate_validation_assistance_task(batch_id=str(self.batch.id))
        first_count = AIAnnotation.objects.filter(record=record).count()
        self.assertEqual(first_count, 1)

        # Redelivery: same batch_id, task runs again.
        generate_validation_assistance_task(batch_id=str(self.batch.id))
        self.assertEqual(AIAnnotation.objects.filter(record=record).count(), 1)

    def test_one_bad_record_does_not_abort_the_rest_of_the_batch(self):
        # A record with no canned response -> schema-invalid -> no
        # annotation, but the task must continue to the next record.
        bad = _make_failed_record(self.org, self.batch, row_index=1)
        good = _make_failed_record(
            self.org, self.batch, row_index=2,
            validation_errors={"quantity": [canned(_VALID_RESPONSE)]},
        )
        result = generate_validation_assistance_task(batch_id=str(self.batch.id))
        self.assertEqual(AIAnnotation.objects.filter(record=bad).count(), 0)
        self.assertEqual(AIAnnotation.objects.filter(record=good).count(), 1)
        self.assertIn("generated=1", result)

    def test_empty_batch_is_a_no_op(self):
        result = generate_validation_assistance_task(batch_id=str(self.batch.id))
        self.assertEqual(result, "generated=0 errored=0")

    def test_routed_to_the_ai_queue(self):
        from django.conf import settings

        routes = settings.CELERY_TASK_ROUTES
        self.assertEqual(routes["apps.ai.tasks.generate_validation_assistance_task"]["queue"], "ai")


class IngestTaskDispatchesValidationAssistanceTests(TestCase):
    """Mirrors apps.ai.tests_anomaly_task's IngestTaskDispatchesAnomalyExplanationsTests."""

    def setUp(self):
        self.org = Organization.objects.create(name="Ingest Validation Dispatch Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )

    def _sap_csv_bytes(self, quantity="-500,00"):
        today = date.today().strftime("%d.%m.%Y")
        return (
            "Werk;Buchungsdatum;Material;Materialkurztext;Menge;Einheit;Nettopreis\n"
            f"DE01;{today};DSL;Diesel;{quantity};L;750.00\n"
        ).encode("utf-8")

    def test_successful_ingest_dispatches_the_validation_assistance_task_without_error(self):
        # AI stays disabled (no TenantAIPolicy, default settings) -- this
        # just proves the dispatch itself never breaks a normal ingest run,
        # even for a batch containing a genuinely FAILED row (negative
        # quantity), and even though nothing in this test enables AI at all.
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="sap.csv",
            status=UploadBatch.BatchStatus.PENDING,
        )
        key = f"uploads/{self.org.id}/{batch.id}/sap.csv"
        get_storage_service().save(key, self._sap_csv_bytes(), content_type="text/csv")

        result = ingest_task(str(batch.id), key, workflow_id="wf-validation-assist-dispatch")
        self.assertEqual(result, "completed")
        record = EmissionRecord.objects.get(batch=batch)
        self.assertEqual(record.status, EmissionRecord.RecordStatus.FAILED)
        # No AIInteraction/AIAnnotation at all -- AI is disabled by default.
        self.assertEqual(AIAnnotation.objects.count(), 0)
