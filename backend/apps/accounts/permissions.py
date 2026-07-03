"""
Role-based, tenant-aware DRF permission classes.

All classes require an authenticated user with access to an active organization
(or a platform-admin superuser). Object-level checks enforce that an object
belongs to the request's active organization (defense in depth alongside
queryset scoping).
"""
from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.accounts.models import (
    ROLES_CAN_APPROVE,
    ROLES_CAN_MANAGE_ORG,
    ROLES_CAN_UPLOAD,
)
from apps.accounts.tenancy import resolve_tenant_context


class IsOrgMember(BasePermission):
    """Authenticated and resolvable to an accessible organization."""

    message = "You are not a member of an active organization."

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        # Raises PermissionDenied (403) if the user has no accessible org.
        ctx = resolve_tenant_context(request)
        return ctx.is_platform_admin or ctx.membership is not None

    def has_object_permission(self, request, view, obj):
        ctx = resolve_tenant_context(request)
        if ctx.is_platform_admin:
            return True
        obj_org_id = getattr(obj, "organization_id", None)
        return obj_org_id is not None and str(obj_org_id) == str(ctx.organization_id)


class CanUpload(IsOrgMember):
    message = "Your role does not permit uploading data."

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        ctx = resolve_tenant_context(request)
        return ctx.is_platform_admin or (
            ctx.membership is not None and ctx.membership.role in ROLES_CAN_UPLOAD
        )


class CanApprove(IsOrgMember):
    message = "Your role does not permit approving records."

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        ctx = resolve_tenant_context(request)
        return ctx.is_platform_admin or (
            ctx.membership is not None and ctx.membership.role in ROLES_CAN_APPROVE
        )


class CanManageOrgResources(IsOrgMember):
    """Reads allowed to any member; writes restricted to Org Admins."""

    message = "Your role does not permit managing organization resources."

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.method in SAFE_METHODS:
            return True
        ctx = resolve_tenant_context(request)
        return ctx.is_platform_admin or (
            ctx.membership is not None and ctx.membership.role in ROLES_CAN_MANAGE_ORG
        )
