from datetime import date
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase

from apps.carbon.models import EmissionCalculation
from apps.carbon.services.metrics import MetricsService
from apps.carbon.services import metrics_cache
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch

RS = EmissionCalculation.ResolutionStatus


class MetricsServiceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Metrics Org")
        self.other = Organization.objects.create(name="Other Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="b.csv"
        )
        self._i = 0
        self.service = MetricsService()

    def _calc(self, scope, date_str, tonnes, status=RS.CALCULATED, org=None):
        org = org or self.org
        self._i += 1
        rec = EmissionRecord.objects.create(
            organization=org, batch=self.batch, row_index=self._i,
            raw_data_payload={"x": 1}, status=EmissionRecord.RecordStatus.DRAFT,
            normalized_value=Decimal("1"), normalized_unit="L", scope_category=scope,
        )
        d = date.fromisoformat(date_str)
        return EmissionCalculation.objects.create(
            organization=org, emission_record=rec, is_current=True, scope=scope,
            reporting_date=d, reporting_month=d.replace(day=1),
            co2e_tonnes=Decimal(tonnes), co2e_kg=Decimal(tonnes) * 1000,
            resolution_status=status,
        )

    def test_summary_totals_and_by_scope(self):
        self._calc("SCOPE_1", "2024-01-15", "10")
        self._calc("SCOPE_1", "2024-02-15", "5")
        self._calc("SCOPE_2", "2024-02-20", "3")
        self._calc("SCOPE_3", "2024-03-01", "2", status=RS.UNRESOLVED_NO_FACTOR)  # no CO2e
        s = self.service.summary(self.org)
        self.assertEqual(s["total_co2e_tonnes"], Decimal("18"))  # 10+5+3 (unresolved excluded)
        self.assertEqual(s["by_scope"]["SCOPE_1"], Decimal("15"))
        self.assertEqual(s["by_scope"]["SCOPE_2"], Decimal("3"))
        # coverage = 3 calculated / (3 calculated + 1 unresolved) = 0.75
        self.assertEqual(s["coverage"], 0.75)

    def test_summary_is_tenant_scoped(self):
        self._calc("SCOPE_1", "2024-01-15", "10")
        self._calc("SCOPE_1", "2024-01-15", "99", org=self.other)
        s = self.service.summary(self.org)
        self.assertEqual(s["total_co2e_tonnes"], Decimal("10"))

    def test_date_filter_and_previous_period(self):
        self._calc("SCOPE_1", "2024-02-10", "8")   # in window
        self._calc("SCOPE_1", "2024-01-10", "5")   # previous window
        s = self.service.summary(self.org, {"date_from": date(2024, 2, 1), "date_to": date(2024, 2, 28)})
        self.assertEqual(s["total_co2e_tonnes"], Decimal("8"))
        self.assertEqual(s["previous_total_co2e_tonnes"], Decimal("5"))

    def test_timeseries_by_month(self):
        self._calc("SCOPE_1", "2024-01-15", "10")
        self._calc("SCOPE_1", "2024-01-20", "5")
        self._calc("SCOPE_2", "2024-02-01", "3")
        ts = self.service.timeseries(self.org, bucket="month")
        totals = {r["period"].strftime("%Y-%m"): r["co2e_tonnes"] for r in ts}
        self.assertEqual(totals["2024-01"], Decimal("15"))
        self.assertEqual(totals["2024-02"], Decimal("3"))

    def test_breakdown_by_scope(self):
        self._calc("SCOPE_1", "2024-01-15", "10")
        self._calc("SCOPE_2", "2024-01-15", "4")
        rows = self.service.breakdown(self.org, dimension="scope")
        self.assertEqual(rows[0], {"key": "SCOPE_1", "co2e_tonnes": Decimal("10")})


class MetricsCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org_id = "org-123"

    def test_cached_memoizes_until_version_bump(self):
        calls = {"n": 0}

        def producer():
            calls["n"] += 1
            return {"value": calls["n"]}

        r1 = metrics_cache.cached(self.org_id, "summary", {"a": 1}, producer)
        r2 = metrics_cache.cached(self.org_id, "summary", {"a": 1}, producer)
        self.assertEqual(r1, r2)
        self.assertEqual(calls["n"], 1)  # producer ran once (cache hit second time)

        # Phase 7.5 (H3): bump_calc_version() now defers its cache mutation to
        # transaction.on_commit() -- TestCase wraps every test in an atomic
        # block that's rolled back, never committed, so the bump would never
        # fire without explicitly executing on_commit callbacks here.
        with self.captureOnCommitCallbacks(execute=True):
            metrics_cache.bump_calc_version(self.org_id)
        metrics_cache.cached(self.org_id, "summary", {"a": 1}, producer)
        self.assertEqual(calls["n"], 2)  # version bump invalidated -> producer ran again

    def test_different_params_are_separate_keys(self):
        k1 = metrics_cache.cache_key(self.org_id, "summary", {"a": 1})
        k2 = metrics_cache.cache_key(self.org_id, "summary", {"a": 2})
        self.assertNotEqual(k1, k2)
