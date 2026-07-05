from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.carbon.cache_mixin import bump_refdata_version
from apps.carbon.models import ActivityType, Scope
from apps.core.models import Organization

User = get_user_model()


class ReferenceCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_carbon")
        self.org = Organization.objects.create(name="Ref Org")
        self.user = User.objects.create_user("u", password="pw")
        Membership.objects.create(user=self.user, organization=self.org, role=Role.VIEWER, active=True)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_reference_list_is_cached_and_version_invalidates(self):
        first = self.client.get("/api/activity-types/").json()
        self.assertEqual(len(first), 8)

        # Add a new activity type; the cached response should NOT reflect it yet.
        ActivityType.objects.create(
            code="NEW_TYPE", name="New", default_scope=Scope.SCOPE_1, base_unit="L"
        )
        cached = self.client.get("/api/activity-types/").json()
        self.assertEqual(len(cached), 8)  # served from cache (stale)

        # Bumping the reference version invalidates the cache.
        bump_refdata_version()
        fresh = self.client.get("/api/activity-types/").json()
        self.assertEqual(len(fresh), 9)
