"""
MetricsService — tenant-scoped aggregation over the EmissionCalculation fact
table. Pure (no caching, no HTTP); the API layer applies caching. All queries
use the analytic indexes added in 4a (organization, is_current, scope/date).

Aggregation semantics:
  - Only CURRENT calculations (`is_current=True`) count.
  - CO2e totals include only CALCULATED rows; UNRESOLVED rows have no CO2e.
  - Coverage = CALCULATED / (CALCULATED + UNRESOLVED); EXCLUDED_FAILED rows are
    intentionally not part of the coverage base (they cannot be calculated).
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth, TruncQuarter, TruncYear

from apps.carbon.models import EmissionCalculation
from apps.ingestion.models import EmissionRecord, UploadBatch

RS = EmissionCalculation.ResolutionStatus
_ZERO = Decimal("0")

_TRUNC = {"month": TruncMonth, "quarter": TruncQuarter, "year": TruncYear}
_BREAKDOWN_FIELD = {
    "scope": "scope",
    "activity_type": "activity_type__code",
    "data_source": "emission_record__batch__data_source__name",
}
_PENDING_STATUSES = [
    EmissionRecord.RecordStatus.DRAFT,
    EmissionRecord.RecordStatus.SUSPICIOUS,
    EmissionRecord.RecordStatus.VALIDATED,
    # Phase 6c: mid-workflow records are still "not yet approved" for this
    # dashboard count -- without these two, a record would silently vanish
    # from "pending" the moment it's submitted (or rejected) and only
    # reappear once approved.
    EmissionRecord.RecordStatus.SUBMITTED,
    EmissionRecord.RecordStatus.REJECTED,
]


class MetricsService:
    # ------------------------------------------------------------------
    def _base(self, organization, filters):
        # Phase 6d: __-traversal filters don't respect a related model's
        # manager (EmissionRecord.objects' is_deleted=False default doesn't
        # apply across this join), so a soft-deleted record's emissions
        # must be excluded explicitly here -- a deleted record must not
        # inflate the org's live dashboard totals.
        qs = EmissionCalculation.objects.filter(
            organization=organization, is_current=True, emission_record__is_deleted=False,
        )
        return self._apply_filters(qs, filters or {})

    @staticmethod
    def _apply_filters(qs, filters):
        if filters.get("date_from"):
            qs = qs.filter(reporting_date__gte=filters["date_from"])
        if filters.get("date_to"):
            qs = qs.filter(reporting_date__lte=filters["date_to"])
        if filters.get("scope"):
            qs = qs.filter(scope=filters["scope"])
        if filters.get("data_source"):
            qs = qs.filter(emission_record__batch__data_source_id=filters["data_source"])
        return qs

    # ------------------------------------------------------------------
    def summary(self, organization, filters=None):
        base = self._base(organization, filters)
        calculated = base.filter(resolution_status=RS.CALCULATED)

        total = calculated.aggregate(t=Sum("co2e_tonnes"))["t"] or _ZERO
        by_scope = {
            row["scope"]: (row["t"] or _ZERO)
            for row in calculated.values("scope").annotate(t=Sum("co2e_tonnes"))
        }
        status_counts = {
            row["resolution_status"]: row["n"]
            for row in base.values("resolution_status").annotate(n=Count("id"))
        }
        calc_n = status_counts.get(RS.CALCULATED, 0)
        unresolved_n = (
            status_counts.get(RS.UNRESOLVED_NO_FACTOR, 0)
            + status_counts.get(RS.UNRESOLVED_NO_ACTIVITY_TYPE, 0)
        )
        coverage_base = calc_n + unresolved_n
        coverage = (calc_n / coverage_base) if coverage_base else 1.0

        pending = EmissionRecord.objects.filter(
            organization=organization, status__in=_PENDING_STATUSES
        ).count()
        batches = UploadBatch.objects.filter(organization=organization).count()

        return {
            "total_co2e_tonnes": total,
            "previous_total_co2e_tonnes": self._previous_total(organization, filters),
            "by_scope": by_scope,
            "status_counts": status_counts,
            "calculated_count": calc_n,
            "unresolved_count": unresolved_n,
            "coverage": round(coverage, 4),
            "pending_approval": pending,
            "batch_count": batches,
        }

    def _previous_total(self, organization, filters):
        """CO2e for the immediately-preceding window of equal length (trend)."""
        filters = filters or {}
        date_from, date_to = filters.get("date_from"), filters.get("date_to")
        if not (date_from and date_to):
            return None
        span = date_to - date_from
        prev_to = date_from - timedelta(days=1)
        prev_from = prev_to - span
        prev = self._apply_filters(
            EmissionCalculation.objects.filter(
                organization=organization, is_current=True, resolution_status=RS.CALCULATED
            ),
            {"date_from": prev_from, "date_to": prev_to, "scope": filters.get("scope"),
             "data_source": filters.get("data_source")},
        )
        return prev.aggregate(t=Sum("co2e_tonnes"))["t"] or _ZERO

    # ------------------------------------------------------------------
    def timeseries(self, organization, filters=None, bucket="month", group_by=None):
        base = self._base(organization, filters).filter(
            resolution_status=RS.CALCULATED, reporting_date__isnull=False
        )
        trunc = _TRUNC.get(bucket, TruncMonth)
        base = base.annotate(period=trunc("reporting_date"))
        if group_by == "scope":
            rows = (
                base.values("period", "scope")
                .annotate(t=Sum("co2e_tonnes")).order_by("period", "scope")
            )
            return [
                {"period": r["period"], "scope": r["scope"], "co2e_tonnes": r["t"] or _ZERO}
                for r in rows
            ]
        rows = base.values("period").annotate(t=Sum("co2e_tonnes")).order_by("period")
        return [{"period": r["period"], "co2e_tonnes": r["t"] or _ZERO} for r in rows]

    # ------------------------------------------------------------------
    def breakdown(self, organization, filters=None, dimension="scope"):
        base = self._base(organization, filters).filter(resolution_status=RS.CALCULATED)
        field = _BREAKDOWN_FIELD.get(dimension, "scope")
        rows = base.values(field).annotate(t=Sum("co2e_tonnes")).order_by("-t")
        return [{"key": r[field], "co2e_tonnes": r["t"] or _ZERO} for r in rows]
