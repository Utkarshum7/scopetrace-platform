"""
Phase 7f -- apps.ai.services.report_context_builder tests. Pure,
read-only retrieval -- no AI call, no gateway, just proving the
assembled context reflects APPROVED-only, tenant-scoped data and matches
apps.carbon.services.reports.compliance_summary exactly.
"""
from datetime import date

from django.test import TestCase

from apps.ai.services.report_context_builder import (
    _format_activity_breakdown,
    _format_summary,
    _format_trend,
    build_report_context,
)
from apps.carbon.models import EmissionCalculation
from apps.carbon.services.reports import compliance_summary
from apps.carbon.tests.factories import activity_type
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch


def _make_batch(org, ds, **extra):
    defaults = dict(organization=org, data_source=ds, file_name="report_context_test.csv")
    defaults.update(extra)
    return UploadBatch.objects.create(**defaults)


def _make_record(org, batch, status=EmissionRecord.RecordStatus.APPROVED, **extra):
    defaults = dict(
        organization=org, batch=batch, row_index=1, raw_data_payload={"a": 1},
        status=status, normalized_value=500, normalized_unit="L", scope_category="SCOPE_1",
    )
    defaults.update(extra)
    return EmissionRecord.objects.create(**defaults)


def _make_calculation(org, record, activity, **extra):
    defaults = dict(
        organization=org, emission_record=record, is_current=True,
        resolution_status=EmissionCalculation.ResolutionStatus.CALCULATED,
        activity_type=activity, scope="SCOPE_1", co2e_tonnes="1.500000000",
        reporting_date=date(2026, 1, 15),
    )
    defaults.update(extra)
    return EmissionCalculation.objects.create(**defaults)


class FormatSummaryTests(TestCase):
    def test_matches_compliance_summary_exactly(self):
        org = Organization.objects.create(name="Report Context Summary Org")
        ds = DataSource.objects.create(organization=org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL)
        batch = _make_batch(org, ds)
        activity = activity_type()
        record = _make_record(org, batch)
        _make_calculation(org, record, activity)

        date_from, date_to = date(2026, 1, 1), date(2026, 1, 31)
        summary = compliance_summary(org, date_from, date_to)
        result = _format_summary(org, date_from, date_to, None)
        self.assertIn(f"total_co2e_tonnes={summary['total_co2e_tonnes']}", result)
        self.assertIn(f"record_count={summary['record_count']}", result)

    def test_excludes_non_approved_records(self):
        org = Organization.objects.create(name="Report Context Non-Approved Org")
        ds = DataSource.objects.create(organization=org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL)
        batch = _make_batch(org, ds)
        activity = activity_type()
        draft_record = _make_record(org, batch, status=EmissionRecord.RecordStatus.DRAFT)
        _make_calculation(org, draft_record, activity, co2e_tonnes="99.000000000")

        result = _format_summary(org, date(2026, 1, 1), date(2026, 1, 31), None)
        self.assertIn("total_co2e_tonnes=0", result)
        self.assertNotIn("99", result)


class FormatActivityBreakdownTests(TestCase):
    def test_lists_activity_types_with_totals(self):
        org = Organization.objects.create(name="Report Context Activity Org")
        ds = DataSource.objects.create(organization=org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL)
        batch = _make_batch(org, ds)
        activity = activity_type()
        record = _make_record(org, batch)
        _make_calculation(org, record, activity)

        result = _format_activity_breakdown(org, date(2026, 1, 1), date(2026, 1, 31), None)
        self.assertIn(activity.code, result)

    def test_empty_period_shows_placeholder(self):
        org = Organization.objects.create(name="Report Context Empty Activity Org")
        result = _format_activity_breakdown(org, date(2026, 1, 1), date(2026, 1, 31), None)
        self.assertIn("no data", result)


class FormatTrendTests(TestCase):
    def test_groups_by_month(self):
        org = Organization.objects.create(name="Report Context Trend Org")
        ds = DataSource.objects.create(organization=org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL)
        batch = _make_batch(org, ds)
        activity = activity_type()
        record = _make_record(org, batch)
        _make_calculation(org, record, activity, reporting_date=date(2026, 2, 10))

        result = _format_trend(org, date(2026, 1, 1), date(2026, 3, 31), None)
        self.assertIn("2026-02=", result)

    def test_empty_period_shows_placeholder(self):
        org = Organization.objects.create(name="Report Context Empty Trend Org")
        result = _format_trend(org, date(2026, 1, 1), date(2026, 1, 31), None)
        self.assertIn("no data", result)


class BuildReportContextTests(TestCase):
    def test_assembles_all_sections(self):
        org = Organization.objects.create(name="Report Context Full Org")
        ds = DataSource.objects.create(organization=org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL)
        batch = _make_batch(org, ds)
        activity = activity_type()
        record = _make_record(org, batch)
        _make_calculation(org, record, activity)

        result = build_report_context(org, date(2026, 1, 1), date(2026, 1, 31))
        self.assertIn("summary:", result)
        self.assertIn("activity_breakdown:", result)
        self.assertIn("trend:", result)

    def test_never_leaks_another_organization_s_data(self):
        org_a = Organization.objects.create(name="Report Context Org A")
        org_b = Organization.objects.create(name="Report Context Org B")
        ds_b = DataSource.objects.create(organization=org_b, name="SAP B", source_type=DataSource.SourceType.SAP_FUEL)
        batch_b = _make_batch(org_b, ds_b)
        activity = activity_type()
        record_b = _make_record(org_b, batch_b)
        _make_calculation(org_b, record_b, activity, co2e_tonnes="777.000000000")

        result = build_report_context(org_a, date(2026, 1, 1), date(2026, 1, 31))
        self.assertNotIn("777", result)

    def test_scope_filters_the_context(self):
        org = Organization.objects.create(name="Report Context Scope Org")
        ds = DataSource.objects.create(organization=org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL)
        batch = _make_batch(org, ds)
        activity = activity_type()
        record = _make_record(org, batch)
        _make_calculation(org, record, activity, scope="SCOPE_2", co2e_tonnes="42.000000000")

        result_matching = build_report_context(org, date(2026, 1, 1), date(2026, 1, 31), scope="SCOPE_2")
        result_other = build_report_context(org, date(2026, 1, 1), date(2026, 1, 31), scope="SCOPE_1")
        self.assertIn("42", result_matching)
        self.assertIn("total_co2e_tonnes=0", result_other)
