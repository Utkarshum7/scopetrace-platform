"""
bootstrap_data — seed a fresh database so a new deployment is usable immediately.

Idempotent by design (get_or_create): safe to run on every deploy as part of the
release/entrypoint step. Seeds one demo Organization, the three DataSources the
frontend expects, and — safely — an admin superuser.

Admin creation policy:
  * If the user already exists, it is left untouched (password never reset).
  * Password is read from DJANGO_SUPERUSER_PASSWORD.
  * If no password is provided:
      - DEBUG=True  -> use an insecure dev default and warn.
      - DEBUG=False -> skip admin creation and warn (never create a weak prod admin).
"""
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Membership, Role
from apps.core.models import DataSource, Organization

User = get_user_model()

DEMO_ORG_NAME = "ScopeTrace Demo Organization"

DATA_SOURCES = [
    ("SAP Fuel Feed", DataSource.SourceType.SAP_FUEL),
    ("Utility Electricity Feed", DataSource.SourceType.UTILITY_ELECTRICITY),
    ("Corporate Travel Feed", DataSource.SourceType.CORP_TRAVEL),
]

# Demo users, one per organization-scoped role (created only when explicitly
# requested via --demo-users or BOOTSTRAP_DEMO_USERS=true).
DEMO_USERS = [
    ("orgadmin", Role.ORG_ADMIN),
    ("analyst", Role.ANALYST),
    ("auditor", Role.AUDITOR),
    ("viewer", Role.VIEWER),
]

DEV_DEFAULT_PASSWORD = "admin12345"


class Command(BaseCommand):
    help = (
        "Idempotently seed a demo Organization, its DataSources, and an admin "
        "user so a fresh deployment is immediately usable."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-admin",
            action="store_true",
            help="Seed the Organization and DataSources but do not create the admin user.",
        )
        parser.add_argument(
            "--demo-users",
            action="store_true",
            help="Also create one demo user per role with a membership in the demo org.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        org, created = Organization.objects.get_or_create(name=DEMO_ORG_NAME)
        self.stdout.write(f"{'Created' if created else 'Exists '} organization: {org.name}")

        for name, source_type in DATA_SOURCES:
            data_source, created = DataSource.objects.get_or_create(
                organization=org,
                name=name,
                defaults={"source_type": source_type},
            )
            # Keep the source_type aligned if a prior seed drifted.
            if not created and data_source.source_type != source_type:
                data_source.source_type = source_type
                data_source.save(update_fields=["source_type"])
            self.stdout.write(
                f"{'Created' if created else 'Exists '} data source: {name} ({source_type})"
            )

        if not options["skip_admin"]:
            self._ensure_admin()

        demo_flag = os.environ.get("BOOTSTRAP_DEMO_USERS", "false").lower() == "true"
        if options["demo_users"] or demo_flag:
            self._ensure_demo_users(org)

        self.stdout.write(self.style.SUCCESS("bootstrap_data complete."))

    def _ensure_admin(self):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@scopetrace.local")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")

        if User.objects.filter(username=username).exists():
            self.stdout.write(f"Exists  admin user: {username} (password unchanged)")
            return

        if not password:
            if settings.DEBUG:
                password = DEV_DEFAULT_PASSWORD
                self.stdout.write(
                    self.style.WARNING(
                        "DJANGO_SUPERUSER_PASSWORD not set — using insecure dev default "
                        f"'{DEV_DEFAULT_PASSWORD}' (allowed only because DEBUG=True)."
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "DJANGO_SUPERUSER_PASSWORD not set and DEBUG=False — skipping admin "
                        "creation. Set the variable and re-run to create the admin user."
                    )
                )
                return

        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f"Created admin user: {username} (Platform Admin)"))

    def _ensure_demo_users(self, org):
        password = os.environ.get("DEMO_USER_PASSWORD", "demo12345")
        for username, role in DEMO_USERS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"email": f"{username}@scopetrace.local"},
            )
            if created:
                user.set_password(password)
                user.save(update_fields=["password"])
            _, m_created = Membership.objects.get_or_create(
                user=user,
                organization=org,
                defaults={"role": role, "active": True},
            )
            self.stdout.write(
                f"{'Created' if created else 'Exists '} demo user: {username} "
                f"({role}){' + membership' if m_created else ''}"
            )
