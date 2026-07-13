"""
Phase 6f — security hardening: response headers, CSV formula-injection
sanitization (unit + integration on both CSV export endpoints), removal of
the dead FEATURE_* flags, and the JWT signing key setting. Logging
additions (audit chain break, failed login, compliance report generation)
are tested alongside the features they instrument -- see
apps/audit/tests.py, apps/accounts/tests.py, apps/carbon/tests/
test_reports.py.
"""
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.carbon.models import EmissionCalculation
from apps.core.csv_security import sanitize_csv_cell
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch

User = get_user_model()


class SanitizeCsvCellTests(TestCase):
    def test_leading_equals_is_prefixed(self):
        self.assertEqual(sanitize_csv_cell("=cmd|'/c calc'!A1"), "'=cmd|'/c calc'!A1")

    def test_leading_plus_is_prefixed(self):
        self.assertEqual(sanitize_csv_cell("+1+1"), "'+1+1")

    def test_leading_at_is_prefixed(self):
        self.assertEqual(sanitize_csv_cell("@SUM(A1:A9)"), "'@SUM(A1:A9)")

    def test_leading_tab_is_prefixed(self):
        self.assertEqual(sanitize_csv_cell("\t=1+1"), "'\t=1+1")

    def test_ordinary_string_is_untouched(self):
        self.assertEqual(sanitize_csv_cell("sap_q1_2026.csv"), "sap_q1_2026.csv")

    def test_negative_number_is_not_a_string_and_is_untouched(self):
        # The real reason this can't be "prefix every leading '-'": a
        # Decimal is never a str, so isinstance() already excludes it --
        # but assert the actual value explicitly, since corrupting a
        # legitimate negative number would silently break every numeric
        # export column.
        value = Decimal("-100.50")
        self.assertEqual(sanitize_csv_cell(value), value)
        self.assertNotIsInstance(sanitize_csv_cell(value), str)

    def test_none_is_untouched(self):
        self.assertIsNone(sanitize_csv_cell(None))

    def test_int_is_untouched(self):
        self.assertEqual(sanitize_csv_cell(5), 5)

    def test_string_that_happens_to_start_with_a_digit_is_untouched(self):
        self.assertEqual(sanitize_csv_cell("500L"), "500L")


class CSVExportInjectionIntegrationTests(TestCase):
    """The real, not-theoretical exposure this codebase had: UploadBatch.
    file_name is user-controlled at upload time and is written verbatim
    into RecordExportView's CSV."""

    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="CSV Injection Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.user = User.objects.create_user("csvtester", password="pw")
        Membership.objects.create(user=self.user, organization=self.org, role=Role.ORG_ADMIN, active=True)
        self.client.force_authenticate(self.user)

    def _body(self, response):
        return b"".join(response.streaming_content).decode("utf-8")

    def test_record_export_sanitizes_malicious_filename(self):
        malicious_batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds,
            file_name="=cmd|'/c calc'!A1.csv",
        )
        EmissionRecord.objects.create(
            organization=self.org, batch=malicious_batch, row_index=1,
            raw_data_payload={"x": 1}, status=EmissionRecord.RecordStatus.DRAFT,
        )
        response = self.client.get("/api/records/export/")
        body = self._body(response)
        self.assertIn("'=cmd|'/c calc'!A1.csv", body)
        # The unsanitized payload must never appear as a bare leading cell.
        self.assertNotIn("\n=cmd|", body)
        self.assertNotIn(",=cmd|", body)

    def test_compliance_report_csv_sanitizes_malicious_activity_type_code(self):
        from apps.carbon.models import ActivityType

        malicious_activity_type = ActivityType.objects.create(
            code="=cmd|'/c calc'!A1", name="Malicious", default_scope="SCOPE_1", base_unit="L",
        )
        batch = UploadBatch.objects.create(organization=self.org, data_source=self.ds, file_name="b.csv")
        record = EmissionRecord.objects.create(
            organization=self.org, batch=batch, row_index=1, raw_data_payload={"x": 1},
            status=EmissionRecord.RecordStatus.APPROVED, normalized_value=Decimal("1"),
            normalized_unit="L", scope_category="SCOPE_1",
        )
        EmissionCalculation.objects.create(
            organization=self.org, emission_record=record, is_current=True, scope="SCOPE_1",
            reporting_date=date(2026, 1, 15), reporting_month=date(2026, 1, 1),
            co2e_tonnes=Decimal("1"), co2e_kg=Decimal("1000"),
            resolution_status=EmissionCalculation.ResolutionStatus.CALCULATED,
            activity_type=malicious_activity_type,
        )
        response = self.client.get(
            "/api/reports/compliance/csv/?date_from=2026-01-01&date_to=2026-01-31"
        )
        body = self._body(response)
        self.assertIn("'=cmd|'/c calc'!A1", body)
        self.assertNotIn(",=cmd|", body)


class SecurityHeadersTests(TestCase):
    """Phase 6f: SECURE_REFERRER_POLICY / SECURE_CROSS_ORIGIN_OPENER_POLICY
    made explicit. Both already matched Django's own defaults before this
    change (verified live before writing this test) -- this asserts the
    posture is what it claims to be, not that behavior changed."""

    def test_referrer_policy_header_present(self):
        response = self.client.get("/api/organizations/")
        self.assertEqual(response.get("Referrer-Policy"), "same-origin")

    def test_cross_origin_opener_policy_header_present(self):
        response = self.client.get("/api/organizations/")
        self.assertEqual(response.get("Cross-Origin-Opener-Policy"), "same-origin")


class DeadFeatureFlagsRemovedTests(TestCase):
    def test_feature_flags_no_longer_exist(self):
        for name in ("FEATURE_JWT_AUTH", "FEATURE_ENFORCE_TENANT_SCOPE", "FEATURE_EMISSION_FACTORS"):
            self.assertFalse(
                hasattr(settings, name),
                f"{name} should have been removed in Phase 6f (read nowhere in the codebase)",
            )


class JWTSigningKeyTests(TestCase):
    def test_signing_key_is_explicit_in_simple_jwt_config(self):
        self.assertIn("SIGNING_KEY", settings.SIMPLE_JWT)
        self.assertEqual(settings.SIMPLE_JWT["SIGNING_KEY"], settings.JWT_SIGNING_KEY)

    def test_signing_key_defaults_to_secret_key(self):
        # Default (no JWT_SIGNING_KEY env var set in the test environment):
        # falls back to SECRET_KEY -- zero behavior change for any existing
        # deployment that never set the new var.
        self.assertEqual(settings.JWT_SIGNING_KEY, settings.SECRET_KEY)
