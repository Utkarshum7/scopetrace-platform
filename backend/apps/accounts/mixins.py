"""Reusable view mixin that scopes querysets to the request's active org."""
from apps.accounts.tenancy import resolve_tenant_context


class TenantScopedViewSetMixin:
    """
    Filters `get_queryset()` to the request's active organization.

    Applies to models that have an `organization` ForeignKey. Platform admins
    are unscoped (all orgs) unless they target one via X-Organization-ID. Tenant
    scope is resolved server-side — it is never taken from a query parameter.
    """

    def get_queryset(self):
        queryset = super().get_queryset()
        ctx = resolve_tenant_context(self.request)
        if ctx.organization is not None:
            return queryset.filter(organization=ctx.organization)
        return queryset
