"""
Streaming CSV export of emission records for the active organization.

Uses a server-side cursor (queryset.iterator) + StreamingHttpResponse so large
exports never materialize in memory. Tenant-scoped, reuses the ledger filters,
and row-capped. Async/Excel export is deferred to a later phase.
"""
import csv

from django.db.models import OuterRef, Subquery
from django.http import StreamingHttpResponse
from rest_framework.views import APIView

from apps.accounts.permissions import IsOrgMember
from apps.accounts.tenancy import resolve_tenant_context
from apps.carbon.models import EmissionCalculation
from apps.core.csv_security import sanitize_csv_cell
from apps.ingestion.models import EmissionRecord

EXPORT_ROW_CAP = 100_000

_HEADER = [
    "record_id", "file_name", "row_index", "status", "scope",
    "normalized_value", "normalized_unit", "co2e_kg", "co2e_tonnes",
    "calculation_status", "reporting_date", "created_at",
]


class _Echo:
    """A file-like object that returns each written row (for csv.writer)."""
    def write(self, value):
        return value


def _apply_filters(qs, request):
    data_source = request.query_params.get("data_source")
    if data_source:
        qs = qs.filter(batch__data_source_id=data_source)
    batch = request.query_params.get("batch")
    if batch:
        qs = qs.filter(batch_id=batch)
    status_param = request.query_params.get("status")
    if status_param:
        statuses = [s.strip().upper() for s in status_param.split(",")]
        qs = qs.filter(status__in=statuses)
    suspicious = request.query_params.get("suspicious")
    if suspicious is not None:
        qs = qs.filter(is_suspicious=suspicious.lower() in ("true", "1"))
    return qs


class RecordExportView(APIView):
    permission_classes = [IsOrgMember]

    def get(self, request):
        ctx = resolve_tenant_context(request)
        current = EmissionCalculation.objects.filter(
            emission_record=OuterRef("pk"), is_current=True
        )
        qs = (
            EmissionRecord.objects.select_related("batch")
            .annotate(
                x_co2e_kg=Subquery(current.values("co2e_kg")[:1]),
                x_co2e_tonnes=Subquery(current.values("co2e_tonnes")[:1]),
                x_calc_status=Subquery(current.values("resolution_status")[:1]),
                x_reporting_date=Subquery(current.values("reporting_date")[:1]),
            )
            .order_by("batch_id", "row_index")
        )
        if ctx.organization is not None:  # platform admin (no org) exports all
            qs = qs.filter(organization=ctx.organization)
        qs = _apply_filters(qs, request)

        writer = csv.writer(_Echo())

        def stream():
            yield writer.writerow(_HEADER)
            for i, r in enumerate(qs.iterator(chunk_size=2000)):
                if i >= EXPORT_ROW_CAP:
                    break
                row = [
                    str(r.id), r.batch.file_name, r.row_index, r.status, r.scope_category or "",
                    r.normalized_value if r.normalized_value is not None else "",
                    r.normalized_unit or "",
                    r.x_co2e_kg if r.x_co2e_kg is not None else "",
                    r.x_co2e_tonnes if r.x_co2e_tonnes is not None else "",
                    r.x_calc_status or "",
                    r.x_reporting_date.isoformat() if r.x_reporting_date else "",
                    r.created_at.isoformat(),
                ]
                # Phase 6f: formula-injection mitigation -- file_name is
                # user-controlled at upload time. sanitize_csv_cell() is a
                # no-op for non-string/non-prefixed values (Decimals, ints).
                yield writer.writerow([sanitize_csv_cell(v) for v in row])

        response = StreamingHttpResponse(stream(), content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="scopetrace_records.csv"'
        return response
