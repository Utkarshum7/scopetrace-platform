"""
Phase 7e -- apps.ai's own API tests (AIConversationViewSet). No PUT/PATCH/
DELETE anywhere is enforced structurally (ListModelMixin/RetrieveModelMixin/
CreateModelMixin only, no Update/Destroy mixin) -- these tests still prove
it at the HTTP layer, matching every other Phase 7 endpoint's coverage.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.ai.models import AIConversation, AIConversationMessage, AIInteraction, TenantAIPolicy
from apps.core.models import Organization

User = get_user_model()


def _make_interaction(org):
    return AIInteraction.objects.create(
        organization=org, capability="esg_assistant", provider="echo", model_id="echo-1",
        outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
    )


class AIConversationAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="ESG Assistant API Org")
        self.analyst = self._user("esg_api_analyst", Role.ANALYST)
        self.viewer = self._user("esg_api_viewer", Role.VIEWER)

    def _user(self, name, role):
        u = User.objects.create_user(name, password="pw")
        Membership.objects.create(user=u, organization=self.org, role=role, active=True)
        return u

    def test_create_conversation(self):
        self.client.force_authenticate(self.analyst)
        response = self.client.post("/api/esg-assistant/conversations/", {}, format="json")
        self.assertEqual(response.status_code, drf_status.HTTP_201_CREATED)
        conversation = AIConversation.objects.get(id=response.json()["id"])
        self.assertEqual(conversation.organization, self.org)
        self.assertEqual(conversation.user, self.analyst)

    def test_list_conversations_scoped_to_org(self):
        AIConversation.objects.create(organization=self.org, user=self.analyst)
        other_org = Organization.objects.create(name="Other ESG API Org")
        AIConversation.objects.create(organization=other_org)

        self.client.force_authenticate(self.analyst)
        response = self.client.get("/api/esg-assistant/conversations/")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        body = response.json()
        results = body["results"] if isinstance(body, dict) and "results" in body else body
        self.assertEqual(len(results), 1)

    def test_messages_endpoint_returns_history_ordered(self):
        conversation = AIConversation.objects.create(organization=self.org, user=self.analyst)
        interaction = _make_interaction(self.org)
        first = AIConversationMessage.objects.create(
            organization=self.org, conversation=conversation,
            role=AIConversationMessage.Role.USER, content="first question",
        )
        second = AIConversationMessage.objects.create(
            organization=self.org, conversation=conversation, interaction=interaction,
            role=AIConversationMessage.Role.ASSISTANT, content="first answer",
        )
        self.client.force_authenticate(self.analyst)
        response = self.client.get(f"/api/esg-assistant/conversations/{conversation.id}/messages/")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(len(body), 2)
        self.assertEqual(body[0]["id"], str(first.id))
        self.assertEqual(body[1]["id"], str(second.id))

    def test_ask_records_the_question_even_when_ai_is_disabled(self):
        conversation = AIConversation.objects.create(organization=self.org, user=self.analyst)
        self.client.force_authenticate(self.analyst)
        response = self.client.post(
            f"/api/esg-assistant/conversations/{conversation.id}/ask/",
            {"question": "What is our total CO2e?"}, format="json",
        )
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        self.assertIsNone(response.json()["assistant_message"])
        self.assertEqual(
            conversation.messages.filter(role=AIConversationMessage.Role.USER).count(), 1,
        )

    def test_ask_returns_the_assistant_message_on_success(self):
        conversation = AIConversation.objects.create(organization=self.org, user=self.analyst)
        interaction = _make_interaction(self.org)
        fake_message = AIConversationMessage.objects.create(
            organization=self.org, conversation=conversation, interaction=interaction,
            role=AIConversationMessage.Role.ASSISTANT, content="Total CO2e was 842.15 tonnes.",
            citations=["org_summary"], confidence=AIConversationMessage.Confidence.HIGH,
        )
        self.client.force_authenticate(self.analyst)
        with patch("apps.ai.views.ask_esg_assistant", return_value=fake_message):
            response = self.client.post(
                f"/api/esg-assistant/conversations/{conversation.id}/ask/",
                {"question": "What is our total CO2e?"}, format="json",
            )
        self.assertEqual(response.status_code, drf_status.HTTP_201_CREATED)
        self.assertEqual(response.json()["assistant_message"]["content"], "Total CO2e was 842.15 tonnes.")

    def test_ask_rejects_empty_question(self):
        conversation = AIConversation.objects.create(organization=self.org, user=self.analyst)
        self.client.force_authenticate(self.analyst)
        response = self.client.post(
            f"/api/esg-assistant/conversations/{conversation.id}/ask/", {"question": ""}, format="json",
        )
        self.assertEqual(response.status_code, drf_status.HTTP_400_BAD_REQUEST)

    def test_viewer_role_is_denied(self):
        self.client.force_authenticate(self.viewer)
        response = self.client.get("/api/esg-assistant/conversations/")
        self.assertEqual(response.status_code, drf_status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_request_rejected(self):
        response = self.client.get("/api/esg-assistant/conversations/")
        self.assertIn(response.status_code, (drf_status.HTTP_401_UNAUTHORIZED, drf_status.HTTP_403_FORBIDDEN))

    def test_cross_tenant_conversation_is_not_reachable(self):
        other_org = Organization.objects.create(name="Cross Tenant ESG API Org")
        other_user = User.objects.create_user("esg_api_other_user", password="pw")
        Membership.objects.create(user=other_user, organization=other_org, role=Role.ANALYST, active=True)
        conversation = AIConversation.objects.create(organization=self.org, user=self.analyst)

        self.client.force_authenticate(other_user)
        response = self.client.get(f"/api/esg-assistant/conversations/{conversation.id}/")
        self.assertEqual(response.status_code, drf_status.HTTP_404_NOT_FOUND)

    def test_no_mutation_verb_is_accepted_on_the_list_or_detail_path(self):
        conversation = AIConversation.objects.create(organization=self.org, user=self.analyst)
        self.client.force_authenticate(self.analyst)
        for method, url in (
            (self.client.put, "/api/esg-assistant/conversations/"),
            (self.client.patch, "/api/esg-assistant/conversations/"),
            (self.client.delete, "/api/esg-assistant/conversations/"),
            (self.client.put, f"/api/esg-assistant/conversations/{conversation.id}/"),
            (self.client.patch, f"/api/esg-assistant/conversations/{conversation.id}/"),
            (self.client.delete, f"/api/esg-assistant/conversations/{conversation.id}/"),
        ):
            with self.subTest(method=method, url=url):
                response = method(url, data={}, format="json")
                self.assertEqual(response.status_code, drf_status.HTTP_405_METHOD_NOT_ALLOWED)
