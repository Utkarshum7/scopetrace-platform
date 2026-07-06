"""
Phase 6a — CLI wrapper around apps.audit.services.verify_chain, for
operators without API/shell access to the Django ORM. See also
GET /api/audit/verify/ (apps.audit.views.AuditChainVerifyView) for the
same check via an authenticated API call.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.audit.services import verify_chain
from apps.core.models import Organization


class Command(BaseCommand):
    help = "Verify the audit hash chain's integrity for one or all organizations."

    def add_arguments(self, parser):
        parser.add_argument(
            "--organization", default="",
            help="Limit to one organization id. Omit to check every organization.",
        )

    def handle(self, *args, **options):
        org_id = options["organization"]
        organizations = (
            [self._get_organization(org_id)] if org_id else Organization.objects.all()
        )

        any_broken = False
        for org in organizations:
            result = verify_chain(org)
            if result.valid:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{org.name} ({org.id}): VALID — {result.entries_checked} entries checked."
                    )
                )
            else:
                any_broken = True
                self.stdout.write(
                    self.style.ERROR(
                        f"{org.name} ({org.id}): BROKEN at sequence "
                        f"{result.broken_at_sequence} — {result.detail}"
                    )
                )

        if any_broken:
            raise CommandError("One or more organizations' audit chains failed verification.")

    def _get_organization(self, org_id):
        try:
            return Organization.objects.get(pk=org_id)
        except Organization.DoesNotExist as exc:
            raise CommandError(f"No organization with id {org_id}") from exc
        except (ValueError, Organization.MultipleObjectsReturned) as exc:
            raise CommandError(f"Invalid organization id {org_id!r}") from exc
