"""
Phase 6e — compliance report generation over the existing immutable data
model (see docs/adr/0002-compliance-reports-on-demand-not-persisted.md).

Reports are generated on demand, never persisted: a deterministic query
over already-immutable data (APPROVED EmissionRecords, is_current=True
CALCULATED EmissionCalculations), with enough provenance embedded in the
output (record_version, calculation_id, generated_at, an audit-chain
verification snapshot) that any line item traces back to an exact,
unchangeable historical row -- without a new storage layer.

Deliberately excludes DRAFT/SUBMITTED/SUSPICIOUS/REJECTED/FAILED records --
a compliance report reflects certified (APPROVED) data only, unlike
MetricsService's dashboards which intentionally include everything.

Kept renderer-agnostic (a plain dict + a queryset of line items) so a
future GHG Protocol / CSRD / ESG-specific rendering can consume the same
underlying data without touching this module -- see build_line_items_queryset
and compliance_summary docstrings.
"""
from decimal import Decimal

from django.db.models import Count, OuterRef, Subquery, Sum

from apps.audit.services import verify_chain
from apps.carbon.models import EmissionCalculation
from apps.core.csv_security import sanitize_csv_cell
from apps.ingestion.models import EmissionRecord, EmissionRecordVersion

RS = EmissionCalculation.ResolutionStatus
_ZERO = Decimal("0")

# JSON responses are capped; CSV is the uncapped, streamed path for larger
# exports (mirrors apps.ingestion.export_views.EXPORT_ROW_CAP's role for
# record exports -- a report payload is reasonably summary-sized, a raw
# export is not).
JSON_LINE_ITEM_CAP = 5_000


def _record_version_subquery():
    """The record's latest EmissionRecordVersion.version_number -- since
    APPROVED is workflow-terminal and audit-locked (EmissionRecord.clean()
    blocks further changes), a record's latest version IS its approved
    state. A Subquery, not a per-row lookup, to avoid N+1."""
    return (
        EmissionRecordVersion.objects.filter(record_id=OuterRef("emission_record_id"))
        .order_by("-version_number")
        .values("version_number")[:1]
    )


def build_line_items_queryset(organization, date_from, date_to, scope=None):
    """The N+1-safe base queryset for compliance report line items.

    Starts from EmissionCalculation (not EmissionRecord) -- the fact table
    that already carries CO2e + factor provenance -- filtered to
    is_current CALCULATED rows whose record is APPROVED, within the
    reporting period. select_related() covers the record + approver in one
    join; record_version is a single Subquery annotation, not a loop.
    Ordering is fixed (organization index order, then id) so the SAME
    query always returns line items in the SAME order -- required for
    deterministic report generation.
    """
    qs = (
        EmissionCalculation.objects.filter(
            organization=organization,
            is_current=True,
            resolution_status=RS.CALCULATED,
            emission_record__status=EmissionRecord.RecordStatus.APPROVED,
            reporting_date__gte=date_from,
            reporting_date__lte=date_to,
        )
        .select_related("emission_record", "emission_record__approved_by", "activity_type")
        .annotate(x_record_version=Subquery(_record_version_subquery()))
        .order_by("reporting_date", "emission_record_id", "id")
    )
    if scope:
        qs = qs.filter(scope=scope)
    return qs


def compliance_summary(organization, date_from, date_to, scope=None):
    """Aggregate totals over the SAME filter build_line_items_queryset
    uses (APPROVED-only), independent of MetricsService's dashboard
    aggregation, which intentionally includes non-approved records too."""
    base = EmissionCalculation.objects.filter(
        organization=organization,
        is_current=True,
        resolution_status=RS.CALCULATED,
        emission_record__status=EmissionRecord.RecordStatus.APPROVED,
        reporting_date__gte=date_from,
        reporting_date__lte=date_to,
    )
    if scope:
        base = base.filter(scope=scope)

    total = base.aggregate(t=Sum("co2e_tonnes"), n=Count("id"))
    by_scope = {
        row["scope"]: (row["t"] or _ZERO)
        for row in base.values("scope").annotate(t=Sum("co2e_tonnes"))
    }
    return {
        "total_co2e_tonnes": total["t"] or _ZERO,
        "record_count": total["n"] or 0,
        "by_scope": by_scope,
    }


def audit_chain_snapshot(organization):
    """A read-only verify_chain() result embedded in every report -- proof
    the governance ledger was intact at generation time. Does not write
    anything (see the ADR for why report generation stays a pure read)."""
    result = verify_chain(organization)
    return {
        "valid": result.valid,
        "entries_checked": result.entries_checked,
        "broken_at_sequence": result.broken_at_sequence,
    }


def serialize_line_item(calc):
    """One compliance report row. Field names are deliberately explicit
    and typed (not one opaque blob) -- the same reasoning
    EmissionRecordVersion's columns follow -- so a future GHG Protocol /
    CSRD-specific renderer can select/relabel a subset without re-deriving
    anything from calculation_trace.

    Phase 6d: `record = calc.emission_record` resolves correctly EVEN IF
    the record is now soft-deleted -- forward FK access uses Django's
    _base_manager (EmissionRecord.Meta.base_manager_name = "all_objects"),
    not the filtered default. is_deleted/deleted_at are surfaced here
    (not filtered out) so a reader can see a line item's source record
    was later deleted, and when -- see
    docs/adr/0004-soft-delete-orthogonal-fields.md for why compliance
    reports deliberately preserve deleted records' history.
    """
    record = calc.emission_record
    return {
        "record_id": str(record.id),
        "calculation_id": str(calc.id),
        "record_version": calc.x_record_version,
        "reporting_date": calc.reporting_date.isoformat() if calc.reporting_date else None,
        "scope": calc.scope,
        "activity_type": calc.activity_type.code if calc.activity_type else None,
        "activity_quantity": str(calc.activity_quantity) if calc.activity_quantity is not None else None,
        "activity_unit": calc.activity_unit,
        "co2e_kg": str(calc.co2e_kg) if calc.co2e_kg is not None else None,
        "co2e_tonnes": str(calc.co2e_tonnes) if calc.co2e_tonnes is not None else None,
        "factor_publisher": calc.factor_publisher,
        "factor_version": calc.factor_version,
        "approved_by": record.approved_by.username if record.approved_by else None,
        "approved_at": record.approved_at.isoformat() if record.approved_at else None,
        "is_deleted": record.is_deleted,
        "deleted_at": record.deleted_at.isoformat() if record.deleted_at else None,
    }


CSV_HEADER = [
    "record_id", "calculation_id", "record_version", "reporting_date", "scope",
    "activity_type", "activity_quantity", "activity_unit",
    "co2e_kg", "co2e_tonnes", "factor_publisher", "factor_version",
    "approved_by", "approved_at", "is_deleted", "deleted_at",
]


def csv_row(calc):
    """Same field set/order as CSV_HEADER and serialize_line_item, as a
    plain list (csv.writer, not a dict) for the streaming CSV path.
    Phase 6f: every cell passes through sanitize_csv_cell() -- formula-
    injection mitigation, a no-op for non-string/non-prefixed values."""
    record = calc.emission_record
    row = [
        str(record.id), str(calc.id), calc.x_record_version,
        calc.reporting_date.isoformat() if calc.reporting_date else "",
        calc.scope,
        calc.activity_type.code if calc.activity_type else "",
        calc.activity_quantity if calc.activity_quantity is not None else "",
        calc.activity_unit or "",
        calc.co2e_kg if calc.co2e_kg is not None else "",
        calc.co2e_tonnes if calc.co2e_tonnes is not None else "",
        calc.factor_publisher or "",
        calc.factor_version or "",
        record.approved_by.username if record.approved_by else "",
        record.approved_at.isoformat() if record.approved_at else "",
        record.is_deleted,
        record.deleted_at.isoformat() if record.deleted_at else "",
    ]
    return [sanitize_csv_cell(v) for v in row]
