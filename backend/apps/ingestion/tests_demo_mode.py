"""
D4 (Demo Mode) — integration proof that, under Demo Mode's execution settings,
a single upload request runs the FULL ingest -> calculate pipeline
synchronously in-process, with no Celery worker or Beat running (the test
environment has neither). The mode-derivation matrix and demo-aware health
endpoint are covered in apps/core/tests_demo_mode.py.

D7 extends this with the remaining two legs of the full advertised chain
(upload -> ingestion -> carbon calculation -> AI processing -> dashboard
update): DemoModeFullChainTests proves AI processing runs inline too (via
the echo provider) and that GET /api/metrics/summary/ -- the dashboard's
data source -- reflects the new calculation immediately after the upload
request returns, with no worker/Beat and no separate polling delay.
"""
from datetime import date, timedelta
from io import BytesIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.test import TestCase, override_settings
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.carbon.models import EmissionCalculation
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch
# Reuse the exact SAP-CSV fixture the existing API upload suite uses, so this
# test exercises the same real parse/validate/normalize path.
from apps.ingestion.tests import _make_sap_csv_bytes

User = get_user_model()


@override_settings(
    DEMO_MODE=True,
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=False,
)
class DemoModePipelineTests(TestCase):
    """With Demo Mode's execution settings, an upload drives ingest -> calculate
    to completion inside the request thread. No worker/Beat process exists in
    the test environment, so a green result here proves Demo Mode needs none."""

    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="Demo Org")
        self.user = User.objects.create_user(username="demo_analyst", password="pw")
        Membership.objects.create(
            user=self.user, organization=self.org, role=Role.ANALYST, active=True
        )
        self.client.force_authenticate(user=self.user)
        self.sap_ds = DataSource.objects.create(
            organization=self.org,
            name="SAP Fuel Feed",
            source_type=DataSource.SourceType.SAP_FUEL,
        )
        today = date.today().strftime("%d.%m.%Y")
        yesterday = (date.today() - timedelta(days=1)).strftime("%d.%m.%Y")
        self._sap_csv = _make_sap_csv_bytes(today, yesterday)

    def _upload(self):
        file_obj = InMemoryUploadedFile(
            file=BytesIO(self._sap_csv), field_name="file", name="demo.csv",
            content_type="text/csv", size=len(self._sap_csv), charset="utf-8",
        )
        return self.client.post(
            "/api/upload/sap/",
            data={"file": file_obj, "data_source": str(self.sap_ds.id)},
            format="multipart",
        )

    def test_upload_runs_ingest_and_calculate_synchronously_without_a_worker(self):
        # Demo Mode's execution setting: tasks run inline, no broker/worker.
        self.assertTrue(settings.CELERY_TASK_ALWAYS_EAGER)

        resp = self._upload()
        self.assertEqual(resp.status_code, drf_status.HTTP_202_ACCEPTED)
        data = resp.json()

        # Ingest ran synchronously in the request: the batch is already TERMINAL
        # (COMPLETED), not left QUEUED for a worker that does not exist.
        self.assertEqual(data["status"], UploadBatch.BatchStatus.COMPLETED)
        batch = UploadBatch.objects.get(id=data["batch_id"])
        self.assertIn(batch.status, UploadBatch.TERMINAL_STATUSES)

        # Ingest produced the emission records...
        records = EmissionRecord.objects.filter(batch=batch)
        self.assertEqual(records.count(), 2)

        # ...and the chained calculate_task ran synchronously right after,
        # producing a current CO2e calculation for each record and moving the
        # batch's calculation axis to a terminal state.
        self.assertIn(batch.calculation_status, UploadBatch.CALCULATION_TERMINAL_STATUSES)
        self.assertEqual(
            EmissionCalculation.objects.filter(
                emission_record__batch=batch, is_current=True
            ).count(),
            records.count(),
        )


@override_settings(
    DEMO_MODE=True, CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=False,
    AI_ENABLED=True, AI_PROVIDER="echo",
)
class DemoModeFullChainTests(TestCase):
    """D7 — the full advertised chain in one HTTP request, with no worker or
    Beat process in the test environment: upload -> ingestion -> AI anomaly/
    validation -> carbon calculation -> AI factor-recommendation -> dashboard
    update (GET /api/metrics/summary/, the same endpoint DashboardPage's KPI
    cards read from)."""

    def setUp(self):
        # Real emission-factor/activity-type reference data, built the same
        # way apps.carbon.tests.factories (this codebase's established
        # calculation-integration-test helper) does for every other test
        # that needs a REAL resolved CO2e, not just terminal ingestion
        # statuses. NOT seed_carbon's bundled DEFRA 2024 subset: that
        # dataset's validity window (2024-01-01..2024-12-31, see
        # apps/carbon/management/commands/seed_carbon.py) cannot resolve
        # ANY current-dated (2026) upload -- a real, pre-existing limitation
        # of the illustrative bundled dataset (its own seeding notes call it
        # "replace with the full official dataset in production"), and
        # orthogonal to Demo Mode -- so a wide-open validity window here
        # keeps this test resolving correctly regardless of the real clock.
        from apps.carbon.models import UnitConversion
        from apps.carbon.tests.factories import activity_type, dataset, factor, mapping, unit_conversion
        diesel_at = activity_type(code="DIESEL_STATIONARY", base_unit="L")
        gas_at = activity_type(code="NATURAL_GAS", base_unit="L")
        mapping(DataSource.SourceType.SAP_FUEL, diesel_at, match_key="")
        mapping(DataSource.SourceType.SAP_FUEL, gas_at, match_key="GAS")
        unit_conversion("M3", "L", "1000", UnitConversion.Dimension.VOLUME)
        factor_dataset = dataset(valid_from=date(2000, 1, 1), valid_to=date(2100, 1, 1))
        factor(factor_dataset, diesel_at, value="2.68", unit="L")
        factor(factor_dataset, gas_at, value="2.03", unit="L")

        self.client = APIClient()
        self.org = Organization.objects.create(name="Demo Full Chain Org")
        self.user = User.objects.create_user(username="demo_full_chain", password="pw")
        Membership.objects.create(
            user=self.user, organization=self.org, role=Role.ANALYST, active=True
        )
        self.client.force_authenticate(user=self.user)
        self.sap_ds = DataSource.objects.create(
            organization=self.org, name="SAP Fuel Feed", source_type=DataSource.SourceType.SAP_FUEL,
        )
        from apps.ai.models import TenantAIPolicy
        TenantAIPolicy.objects.create(
            organization=self.org, ai_enabled=True, provider_override="echo",
            monthly_budget_usd="50.00",
        )
        today = date.today().strftime("%d.%m.%Y")
        yesterday = (date.today() - timedelta(days=1)).strftime("%d.%m.%Y")
        old = (date.today() - timedelta(days=500)).strftime("%d.%m.%Y")
        self._csv = (
            "Werk;Buchungsdatum;Material;Materialkurztext;Menge;Einheit;Nettopreis\n"
            f"DE01;{today};DSL;Diesel;500,00;L;750.00\n"
            f"DE02;{yesterday};GAS;Gas;100,00;M3;200.00\n"
            f"DE01;{old};DSL;Old Fuel;100.00;L;120.00\n"
        ).encode("utf-8")

    def test_upload_to_dashboard_update_in_one_request_no_worker(self):
        # Dashboard baseline BEFORE the upload -- proves what changes below is
        # actually caused by this request, not pre-existing data.
        before = self.client.get("/api/metrics/summary/").json()
        self.assertEqual(before["total_co2e_tonnes"], "0")
        self.assertEqual(before["batch_count"], 0)

        file_obj = InMemoryUploadedFile(
            file=BytesIO(self._csv), field_name="file", name="full_chain.csv",
            content_type="text/csv", size=len(self._csv), charset="utf-8",
        )
        # apps.carbon.services.metrics_cache.bump_calc_version() defers its
        # cache-invalidating write via transaction.on_commit() (Phase 7.5 H3
        # -- see that function's docstring) so a concurrent metrics read can
        # never observe a bumped version before the underlying calculation
        # rows are actually durably committed. In a real request this fires
        # naturally when the enclosing atomic() block exits, well before the
        # response returns -- TestCase's own outer test-transaction (rolled
        # back, never committed) is what suppresses on_commit hooks here;
        # captureOnCommitCallbacks(execute=True) is this codebase's existing
        # pattern for simulating that real commit (see
        # apps.ingestion.tests_soft_delete's identical use).
        with self.captureOnCommitCallbacks(execute=True):
            upload_resp = self.client.post(
                "/api/upload/sap/", data={"file": file_obj, "data_source": str(self.sap_ds.id)},
                format="multipart",
            )
        self.assertEqual(upload_resp.status_code, drf_status.HTTP_202_ACCEPTED)
        batch_id = upload_resp.json()["batch_id"]
        batch = UploadBatch.objects.get(id=batch_id)

        # Ingestion: terminal, no worker needed.
        self.assertIn(batch.status, UploadBatch.TERMINAL_STATUSES)
        # Carbon calculation: terminal, no worker needed.
        self.assertIn(batch.calculation_status, UploadBatch.CALCULATION_TERMINAL_STATUSES)
        self.assertTrue(
            EmissionCalculation.objects.filter(emission_record__batch=batch, is_current=True).exists()
        )

        # AI processing: the one suspicious record (500-day-old fuel entry)
        # reached the gateway and got a real (echo) provider round trip --
        # this is the "AI processing" leg, also with no worker.
        from apps.ai.models import AIInteraction
        anomaly_interactions = AIInteraction.objects.filter(
            organization=self.org, capability="anomaly_detection",
        )
        self.assertTrue(anomaly_interactions.exists())

        # Dashboard update: GET /api/metrics/summary/ (unchanged endpoint,
        # unchanged cache) already reflects the new batch and a non-zero
        # total -- no delay, no separate worker-populated read model.
        after = self.client.get("/api/metrics/summary/").json()
        self.assertEqual(after["batch_count"], 1)
        self.assertNotEqual(after["total_co2e_tonnes"], "0")
