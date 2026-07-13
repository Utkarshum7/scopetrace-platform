"""
Server-side active-organization resolution (multi-tenant isolation).

The active organization for a request is derived ONLY from the authenticated
user's memberships (and an optional, validated X-Organization-ID header). No
`organization` query parameter or request body value is ever trusted.
"""
from django.core.exceptions import ValidationError

from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import Membership

# WSGI-normalized form of the "X-Organization-ID" HTTP header.
ORG_HEADER = "HTTP_X_ORGANIZATION_ID"


class TenantContext:
    """Resolved tenant scope for a request."""

    def __init__(self, organization=None, membership=None, is_platform_admin=False):
        self.organization = organization
        self.membership = membership
        self.is_platform_admin = is_platform_admin

    @property
    def role(self):
        return self.membership.role if self.membership else None

    @property
    def organization_id(self):
        return self.organization.id if self.organization else None


def resolve_tenant_context(request):
    """
    Resolve and cache the active organization for an authenticated request.

    Platform admins (superusers):
      - X-Organization-ID present -> scope to that org (must exist).
      - absent -> unscoped (organization=None), i.e. can see all orgs.

    Regular users:
      - X-Organization-ID present -> must match one of the user's ACTIVE
        memberships, else 403.
      - absent -> the user's first active membership is used.
      - no active membership -> 403.
    """
    cached = getattr(request, "_tenant_context", None)
    if cached is not None:
        return cached

    user = request.user
    header_org_id = request.META.get(ORG_HEADER) or None

    if getattr(user, "is_superuser", False):
        organization = None
        if header_org_id:
            from apps.core.models import Organization
            try:
                organization = Organization.objects.get(pk=header_org_id)
            except (Organization.DoesNotExist, ValidationError, ValueError):
                raise PermissionDenied("Unknown organization.")
        ctx = TenantContext(organization=organization, membership=None, is_platform_admin=True)
        request._tenant_context = ctx
        return ctx

    memberships = list(
        Membership.objects.filter(user=user, active=True).select_related("organization")
    )

    if header_org_id:
        membership = next(
            (m for m in memberships if str(m.organization_id) == str(header_org_id)),
            None,
        )
        if membership is None:
            raise PermissionDenied("You do not have access to the requested organization.")
    else:
        membership = memberships[0] if memberships else None
        if membership is None:
            raise PermissionDenied("Your account is not a member of any active organization.")

    ctx = TenantContext(
        organization=membership.organization,
        membership=membership,
        is_platform_admin=False,
    )
    request._tenant_context = ctx
    return ctx
