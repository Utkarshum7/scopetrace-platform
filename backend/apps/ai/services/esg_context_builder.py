"""
Phase 7e -- the esg_assistant capability's retrieval layer. "RAG" here
means deterministic, structured retrieval against ALREADY tenant/RBAC/
soft-delete/approval-aware services -- not a vector store or embedding
index (this platform has neither, and none is introduced here). Every
figure in the built context comes from a query this codebase already
trusts elsewhere:

- MetricsService.summary() (apps.carbon.services.metrics) -- the same
  aggregation the dashboard uses, already organization-scoped and
  already excluding soft-deleted records (emission_record__is_deleted=
  False is baked into MetricsService._base()).
- The compliance-report APPROVED-only query pattern
  (apps.carbon.services.reports.build_line_items_queryset) -- reused
  here for a separate, audit-grade "approved only" total, so a question
  like "what's our reported total" doesn't accidentally include DRAFT/
  SUSPICIOUS/REJECTED figures the dashboard summary legitimately does.
- UploadBatch / EmissionFactorDataset, both already organization- or
  platform-scoped by their own model definitions.

Tenant isolation is structural, not a filter this module has to get
right on its own: every query below takes `organization` as a required,
explicit parameter (never inferred, never optional) -- there is no code
path here that can silently cross an org boundary. RBAC is enforced one
layer up, at the API view (CanUseAI), before this function is ever
called -- this module has no user/role parameter to get wrong.

Returns a single formatted text block, ready to substitute into the
esg_assistant prompt's $context placeholder. Each section is labeled
(org_summary, approved_summary, recent_uploads,
reference_factor_datasets) -- the AI is instructed to cite these labels
back in its `citations` field.
"""
from django.db.models import Sum

from apps.carbon.models import EmissionCalculation, EmissionFactorDataset
from apps.carbon.services.metrics import MetricsService
from apps.ingestion.models import EmissionRecord, UploadBatch

RS = EmissionCalculation.ResolutionStatus
_metrics = MetricsService()


def _format_org_summary(organization) -> str:
    summary = _metrics.summary(organization)
    by_scope = ", ".join(f"{scope}={total}" for scope, total in summary["by_scope"].items()) or "(no data yet)"
    return (
        f"org_summary: total_co2e_tonnes={summary['total_co2e_tonnes']}, by_scope={{{by_scope}}}, "
        f"coverage={summary['coverage']}, pending_approval={summary['pending_approval']}, "
        f"batch_count={summary['batch_count']}"
    )


def _format_approved_summary(organization) -> str:
    # Audit-grade total: APPROVED records only, mirroring the compliance
    # report's own query shape (apps.carbon.services.reports), not the
    # broader dashboard summary above.
    approved_total = (
        EmissionCalculation.objects.filter(
            organization=organization, is_current=True, resolution_status=RS.CALCULATED,
            emission_record__status=EmissionRecord.RecordStatus.APPROVED,
        )
        .aggregate(t=Sum("co2e_tonnes"))["t"]
    )
    return f"approved_summary: approved_co2e_tonnes={approved_total if approved_total is not None else 0}"


def _format_recent_uploads(organization, *, limit=5) -> str:
    batches = UploadBatch.objects.filter(organization=organization).order_by("-created_at")[:limit]
    if not batches:
        return "recent_uploads: (no batches uploaded yet)"
    lines = [f"{b.file_name} (status={b.status}, uploaded {b.created_at.date()})" for b in batches]
    return "recent_uploads: " + "; ".join(lines)


def _format_reference_factor_datasets(*, limit=10) -> str:
    # Reference data, not organization-owned -- available to every tenant.
    datasets = EmissionFactorDataset.objects.filter(
        status=EmissionFactorDataset.Status.ACTIVE
    ).order_by("-priority")[:limit]
    if not datasets:
        return "reference_factor_datasets: (none active)"
    lines = [f"{d.publisher} {d.version} (region={d.region.code if d.region else 'GLOBAL'})" for d in datasets]
    return "reference_factor_datasets: " + "; ".join(lines)


def build_context(organization) -> str:
    """Assembles the full retrieval context for one esg_assistant turn.
    Read-only: no query in this module writes to, or even touches, a
    write path on any governed model."""
    sections = [
        _format_org_summary(organization),
        _format_approved_summary(organization),
        _format_recent_uploads(organization),
        _format_reference_factor_datasets(),
    ]
    return "\n".join(sections)
