"""
Phase 7f -- the report_narration capability's retrieval layer. Built
ONLY from approved, deterministic data, per the milestone's explicit
requirement -- this module deliberately does NOT use
apps.carbon.services.metrics.MetricsService, which intentionally
includes non-approved (DRAFT/SUSPICIOUS/SUBMITTED/REJECTED) records for
its dashboard use case (see MetricsService's own docstring). Every query
here uses the SAME APPROVED-only filter shape
apps.carbon.services.reports.compliance_summary/build_line_items_queryset
already use for the compliance report itself -- narration and the report
it narrates are always looking at identical data.

`compliance_summary()` is reused directly (not reimplemented) for the
headline total/by-scope figures; this module adds two NEW approved-only
queries (activity breakdown, monthly trend) the compliance report
endpoints don't currently expose, both built with the identical
organization/is_current/resolution_status/emission_record__status/
reporting_date filter apps.carbon.services.reports already established.

Tenant isolation is structural: every query takes `organization` as a
required, explicit parameter.
"""
from django.db.models import Sum
from django.db.models.functions import TruncMonth

from apps.carbon.models import EmissionCalculation
from apps.carbon.services.reports import compliance_summary
from apps.ingestion.models import EmissionRecord

RS = EmissionCalculation.ResolutionStatus
_ZERO = 0


def _approved_base_qs(organization, date_from, date_to, scope=None):
    """Identical filter shape to apps.carbon.services.reports.
    build_line_items_queryset/compliance_summary -- narration must never
    see a wider (or narrower) slice of data than the report it narrates."""
    qs = EmissionCalculation.objects.filter(
        organization=organization, is_current=True, resolution_status=RS.CALCULATED,
        emission_record__status=EmissionRecord.RecordStatus.APPROVED,
        reporting_date__gte=date_from, reporting_date__lte=date_to,
    )
    if scope:
        qs = qs.filter(scope=scope)
    return qs


def _format_summary(organization, date_from, date_to, scope) -> str:
    summary = compliance_summary(organization, date_from, date_to, scope)
    by_scope = ", ".join(f"'{s}': {t}" for s, t in summary["by_scope"].items()) or "(no data)"
    return (
        f"summary: total_co2e_tonnes={summary['total_co2e_tonnes']}, "
        f"record_count={summary['record_count']}, by_scope={{{by_scope}}}"
    )


def _format_activity_breakdown(organization, date_from, date_to, scope, *, limit=10) -> str:
    rows = (
        _approved_base_qs(organization, date_from, date_to, scope)
        .values("activity_type__code").annotate(t=Sum("co2e_tonnes")).order_by("-t")[:limit]
    )
    if not rows:
        return "activity_breakdown: (no data)"
    parts = ", ".join(f"{r['activity_type__code']}={r['t']}" for r in rows if r["activity_type__code"])
    return f"activity_breakdown: {parts or '(no data)'}"


def _format_trend(organization, date_from, date_to, scope) -> str:
    rows = (
        _approved_base_qs(organization, date_from, date_to, scope)
        .annotate(period=TruncMonth("reporting_date"))
        .values("period").annotate(t=Sum("co2e_tonnes")).order_by("period")
    )
    if not rows:
        return "trend: (no data)"
    parts = ", ".join(f"{r['period']:%Y-%m}={r['t']}" for r in rows)
    return f"trend: {parts}"


def build_report_context(organization, date_from, date_to, scope=None) -> str:
    """Assembles the full, APPROVED-only retrieval context for one
    report_narration call. Read-only: no query in this module writes to,
    or even touches, a write path on any governed model."""
    sections = [
        _format_summary(organization, date_from, date_to, scope),
        _format_activity_breakdown(organization, date_from, date_to, scope),
        _format_trend(organization, date_from, date_to, scope),
    ]
    return "\n".join(sections)
