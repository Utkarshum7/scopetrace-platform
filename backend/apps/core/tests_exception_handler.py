"""
Phase 7.5 (H4-8) -- apps.core.exception_handlers.unhandled_exception_handler.
Confirms the handler is narrow: it only changes the response shape for a
genuinely unexpected exception that DRF's own default handler can't already
map (returns None for), and leaves every DRF-recognized exception type
(NotFound/PermissionDenied/ValidationError) exactly as DRF's default
handler already shapes them.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.core.models import Organization

User = get_user_model()


class UnhandledExceptionHandlerTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="Exception Handler Org")
        self.user = User.objects.create_user("exc_handler_user", password="pw")
        Membership.objects.create(user=self.user, organization=self.org, role=Role.ANALYST, active=True)
        self.client.force_authenticate(self.user)

    def test_a_genuinely_unexpected_exception_returns_a_consistent_json_500(self):
        # DataSourceViewSet.get_queryset is a plain, otherwise-reliable
        # read path -- patched here purely to simulate an exception DRF's
        # own default handler doesn't recognize (a bare RuntimeError, not
        # an APIException/Http404/PermissionDenied subclass).
        with patch(
            "apps.ingestion.views.DataSourceViewSet.get_queryset",
            side_effect=RuntimeError("simulated unexpected failure"),
        ):
            response = self.client.get("/api/datasources/")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "An unexpected error occurred."})
        # The raw exception message must never reach the response body.
        self.assertNotIn("simulated unexpected failure", response.content.decode())

    def test_a_recognized_drf_exception_is_unaffected_not_found(self):
        response = self.client.get("/api/datasources/00000000-0000-0000-0000-000000000000/")
        self.assertEqual(response.status_code, drf_status.HTTP_404_NOT_FOUND)
        # DRF's own default shape for NotFound -- proves the handler
        # delegates to it unchanged rather than intercepting everything.
        self.assertIn("detail", response.json())

    def test_a_recognized_drf_exception_is_unaffected_permission_denied(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/datasources/")
        self.assertIn(
            response.status_code,
            (drf_status.HTTP_401_UNAUTHORIZED, drf_status.HTTP_403_FORBIDDEN),
        )

    def test_an_explicit_view_response_is_completely_unaffected(self):
        # Sanity check on the handler's stated scope: a view that returns
        # Response(...) normally (never raises) never even reaches
        # EXCEPTION_HANDLER -- this is the vast majority of this codebase's
        # error responses (e.g. ingestion's workflow-transition 400s).
        response = self.client.post(
            "/api/upload/sap/", data={}, format="multipart",
        )
        self.assertEqual(response.status_code, drf_status.HTTP_400_BAD_REQUEST)
        self.assertIn("file", response.json())
