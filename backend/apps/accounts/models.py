import uuid

from django.conf import settings
from django.db import models

from apps.core.models import Organization


class Role(models.TextChoices):
    """Organization-scoped roles. Platform Admin is modeled as a Django
    superuser (cross-tenant) and is intentionally NOT a membership role."""
    ORG_ADMIN = "ORG_ADMIN", "Organization Admin"
    ANALYST = "ANALYST", "ESG Analyst"
    AUDITOR = "AUDITOR", "Auditor"
    VIEWER = "VIEWER", "Viewer"


# Capability sets by role (org-scoped). Platform admins bypass these entirely.
#   read (list/retrieve)  : all roles
#   upload data           : ORG_ADMIN, ANALYST
#   approve records       : ORG_ADMIN, ANALYST, AUDITOR
#   manage org resources  : ORG_ADMIN
ROLES_CAN_UPLOAD = frozenset({Role.ORG_ADMIN, Role.ANALYST})
ROLES_CAN_APPROVE = frozenset({Role.ORG_ADMIN, Role.ANALYST, Role.AUDITOR})
ROLES_CAN_MANAGE_ORG = frozenset({Role.ORG_ADMIN})
ROLES_CAN_VIEW_ACTIVITY = frozenset({Role.ORG_ADMIN, Role.AUDITOR})


class Membership(models.Model):
    """
    Binds a User to an Organization with a role. This is the unit of tenant
    access: a non-superuser can only see/act within organizations where they
    hold an ACTIVE membership. Platform admins (superusers) bypass memberships.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VIEWER)
    active = models.BooleanField(
        default=True,
        help_text="Inactive memberships are denied all access to the organization.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Membership"
        verbose_name_plural = "Memberships"
        ordering = ["organization", "user"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization"],
                name="unique_user_organization_membership",
            ),
        ]

    def __str__(self):
        return f"{self.user} @ {self.organization} ({self.role})"

    @property
    def can_upload(self) -> bool:
        return self.role in ROLES_CAN_UPLOAD

    @property
    def can_approve(self) -> bool:
        return self.role in ROLES_CAN_APPROVE

    @property
    def can_manage_org(self) -> bool:
        return self.role in ROLES_CAN_MANAGE_ORG
