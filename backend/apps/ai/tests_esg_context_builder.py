"""
Phase 7e -- apps.ai.services.esg_context_builder tests. Pure, read-only
retrieval -- no AI call, no gateway, just proving the assembled context
reflects the right tenant-scoped, approval-aware data.
"""
from django.test import TestCase

from apps.ai.services.esg_context_builder import (
    _format_approved_summary,
    _format_org_summary,
    _format_recent_uploads,
    _format_reference_factor_datasets,
    build_context,
)
from apps.carbon.models import EmissionCalculation
from apps.carbon.tests.factories import activity_type, dataset, factor
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch


def _make_batch(org, ds, **extra):
    defaults = dict(organization=org, data_source=ds, file_name="context_test.csv")
    defaults.update(extra)
    return UploadBatch.objects.create(**defaults)


def _make_record(org, batch, **extra):
    defaults = dict(
        organization=org, batch=batch, row_index=1, raw_data_payload={"a": 1},
        status=EmissionRecord.RecordStatus.APPROVED, normalized_value=500,
        normalized_unit="L", scope_category="SCOPE_1",
    )
    defaults.update(extra)
    return EmissionRecord.objects.create(**defaults)


def _make_calculation(org, record, activity, **extra):
    defaults = dict(
        organization=org, emission_record=record, is_current=True,
        resolution_status=EmissionCalculation.ResolutionStatus.CALCULATED,
        activity_type=activity, scope="SCOPE_1", co2e_tonnes="1.500000000",
        reporting_date="2026-01-15",
    )
    defaults.update(extra)
    return EmissionCalculation.objects.create(**defaults)


class FormatOrgSummaryTests(TestCase):
    def test_includes_total_and_by_scope(self):
        org = Organization.objects.create(name="Context Org Summary")
        ds = DataSource.objects.create(organization=org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL)
        batch = _make_batch(org, ds)
        activity = activity_type()
        record = _make_record(org, batch)
        _make_calculation(org, record, activity)

        result = _format_org_summary(org)
        self.assertIn("total_co2e_tonnes=1.5", result.replace("1.500000000", "1.5"))
        self.assertIn("SCOPE_1", result)

    def test_empty_org_has_zero_total_not_an_error(self):
        org = Organization.objects.create(name="Context Org Empty")
        result = _format_org_summary(org)
        self.assertIn("total_co2e_tonnes=0", result)


class FormatApprovedSummaryTests(TestCase):
    def test_only_counts_approved_records(self):
        org = Organization.objects.create(name="Context Approved Org")
        ds = DataSource.objects.create(organization=org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL)
        batch = _make_batch(org, ds)
        activity = activity_type()

        approved_record = _make_record(org, batch, row_index=1, status=EmissionRecord.RecordStatus.APPROVED)
        _make_calculation(org, approved_record, activity, co2e_tonnes="2.000000000")

        draft_record = _make_record(org, batch, row_index=2, status=EmissionRecord.RecordStatus.DRAFT)
        _make_calculation(org, draft_record, activity, co2e_tonnes="99.000000000")

        result = _format_approved_summary(org)
        self.assertIn("approved_co2e_tonnes=2", result)
        self.assertNotIn("99", result)

    def test_no_approved_records_is_zero_not_none(self):
        org = Organization.objects.create(name="Context No Approved Org")
        result = _format_approved_summary(org)
        self.assertIn("approved_co2e_tonnes=0", result)


class FormatRecentUploadsTests(TestCase):
    def test_lists_recent_batches(self):
        org = Organization.objects.create(name="Context Uploads Org")
        ds = DataSource.objects.create(organization=org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL)
        _make_batch(org, ds, file_name="jan.csv")
        result = _format_recent_uploads(org)
        self.assertIn("jan.csv", result)

    def test_empty_org_shows_placeholder(self):
        org = Organization.objects.create(name="Context No Uploads Org")
        result = _format_recent_uploads(org)
        self.assertIn("no batches uploaded yet", result)


class FormatReferenceFactorDatasetsTests(TestCase):
    def test_lists_active_datasets(self):
        activity_type()  # ensures carbon app tables exist in this test's transaction
        ds = dataset()
        factor(ds, activity_type(code="REF_DS_TYPE"))
        result = _format_reference_factor_datasets()
        self.assertIn(ds.publisher, result)


class BuildContextTests(TestCase):
    def test_assembles_all_sections(self):
        org = Organization.objects.create(name="Context Full Org")
        ds_source = DataSource.objects.create(
            organization=org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        _make_batch(org, ds_source, file_name="full.csv")
        result = build_context(org)
        self.assertIn("org_summary:", result)
        self.assertIn("approved_summary:", result)
        self.assertIn("recent_uploads:", result)
        self.assertIn("reference_factor_datasets:", result)
        self.assertIn("full.csv", result)

    def test_never_leaks_another_organization_s_data(self):
        org_a = Organization.objects.create(name="Context Org A")
        org_b = Organization.objects.create(name="Context Org B")
        ds_b = DataSource.objects.create(
            organization=org_b, name="SAP B", source_type=DataSource.SourceType.SAP_FUEL,
        )
        _make_batch(org_b, ds_b, file_name="org_b_only.csv")

        result = build_context(org_a)
        self.assertNotIn("org_b_only.csv", result)
        self.assertNotIn("Org B", result)
