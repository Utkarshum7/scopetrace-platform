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
    ROLES_CAN_USE_AI,
    ROLES_CAN_VIEW_ACTIVITY,
)
from apps.accounts.tenancy import resolve_tenant_context


class IsPlatformAdmin(BasePermission):
    """Platform administrators (Django superusers) only — cross-tenant access."""

    message = "Platform administrator access required."

    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and request.user.is_superuser
        )


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


class CanViewActivity(IsOrgMember):
    """Organization Admins and Auditors may view the audit/activity feed."""

    message = "Only organization admins and auditors can view activity."

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        ctx = resolve_tenant_context(request)
        return ctx.is_platform_admin or (
            ctx.membership is not None and ctx.membership.role in ROLES_CAN_VIEW_ACTIVITY
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


class CanUseAI(IsOrgMember):
    """Phase 7a: a pure role-gate, deliberately NOT checking whether AI is
    actually enabled for this organization -- that's a business-state
    question (TenantAIPolicy.ai_enabled), answered by
    apps.ai.services.policy.resolve_policy() inside the gateway itself, the
    same way CanApprove doesn't also check whether a specific record is in
    a state that can be approved (apps.ingestion.services.workflow does).
    A caller with this permission but an AI-disabled org still reaches the
    gateway and gets back a clean AI_DISABLED outcome, not a bare 403 --
    more informative, and keeps RBAC and business-state gating in their own
    layers."""

    message = "Your role does not permit using AI features."

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        ctx = resolve_tenant_context(request)
        return ctx.is_platform_admin or (
            ctx.membership is not None and ctx.membership.role in ROLES_CAN_USE_AI
        )


class CanManageAIPolicy(IsOrgMember):
    """Organization Admin (or Platform Admin) only -- who can change
    TenantAIPolicy (turn AI on/off, set budget/egress tier/provider
    overrides) for their org. Behaviorally identical to IsOrgAdmin today;
    kept as its own class (not a reused alias) for the same reason IsOrgAdmin
    itself is distinct from CanManageOrgResources despite an overlapping
    role set -- a distinct call-site meaning that can diverge later without
    a rename."""

    message = "Your role does not permit managing AI policy."

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        ctx = resolve_tenant_context(request)
        return ctx.is_platform_admin or (
            ctx.membership is not None and ctx.membership.role in ROLES_CAN_MANAGE_ORG
        )


class CanViewAICosts(IsOrgMember):
    """Organization Admins and Auditors may view AI cost/observability data
    -- mirrors CanViewActivity's role set exactly (AI spend is governance-
    adjacent observability, the same category as the audit/activity feed)."""

    message = "Only organization admins and auditors can view AI cost data."

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        ctx = resolve_tenant_context(request)
        return ctx.is_platform_admin or (
            ctx.membership is not None and ctx.membership.role in ROLES_CAN_VIEW_ACTIVITY
        )


class IsOrgAdmin(IsOrgMember):
    """Organization Admin (or Platform Admin) only, for EVERY method --
    unlike CanManageOrgResources, which deliberately allows reads to any
    member and only restricts writes. Used where even VIEWING something
    should be admin-only, not just mutating it (Phase 6d: the soft-deleted
    records list -- GET /api/records/?deleted=true -- is itself an
    administrative-oversight capability, not a routine read)."""

    message = "Your role does not permit this action."

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        ctx = resolve_tenant_context(request)
        return ctx.is_platform_admin or (
            ctx.membership is not None and ctx.membership.role in ROLES_CAN_MANAGE_ORG
        )
