"""
Phase 6a — the audit app's first-ever HTTP view. Exposes hash-chain
verification (apps.audit.services.verify_chain) to authenticated operators
without requiring shell/management-command access — the same check the
`verify_audit_chain` management command runs.
"""
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import CanViewActivity
from apps.accounts.tenancy import resolve_tenant_context
from apps.audit.services import verify_chain


class AuditChainVerifyView(APIView):
    """GET /api/audit/verify/ — verifies the active organization's audit hash
    chain. Same RBAC as the existing audit/activity feed (Org Admin /
    Auditor, or Platform Admin scoped via X-Organization-ID) — reusing
    CanViewActivity rather than introducing a new permission class for what
    is, at its core, the same "who may inspect this organization's audit
    trail" question apps.carbon.metrics_views.ActivityFeedView already
    answers.
    """
    permission_classes = [CanViewActivity]

    def get(self, request):
        ctx = resolve_tenant_context(request)
        if ctx.organization is None:
            raise PermissionDenied(
                "Select an organization (X-Organization-ID) to verify its audit chain."
            )
        result = verify_chain(ctx.organization)
        return Response({
            "organization": result.organization_id,
            "valid": result.valid,
            "entries_checked": result.entries_checked,
            "broken_at_sequence": result.broken_at_sequence,
            "detail": result.detail,
        })
