"""
Phase 7e -- apps.ai's own API views. The first Phase 7 capability
surfaced through its own endpoints rather than an @action on an existing
apps.ingestion/apps.carbon viewset, because esg_assistant has no single
governed record to hang off of the way anomaly_detection/
factor_recommendation/validation_assistance do.

Read-only by structure, not just by convention: AIConversationViewSet
mixes in only List/Retrieve/Create -- there is no Update or Destroy mixin
anywhere in this file, so PUT/PATCH/DELETE 405 on every route
automatically, the same guarantee the immutable AIConversationMessage
model itself enforces at the DB layer. `create` and `ask` are the two
necessarily-mutating actions a conversational feature requires; neither
ever touches a governed model (apps.carbon/apps.ingestion) -- only
AIConversation/AIConversationMessage rows.

Gated by CanUseAI (apps.accounts.permissions) -- the first real usage of
that permission class, which has existed since Phase 7a but had no
AI-specific endpoint to protect until now.
"""
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.mixins import TenantScopedViewSetMixin
from apps.accounts.permissions import CanUseAI
from apps.accounts.tenancy import resolve_tenant_context
from apps.ai.models import AIConversation
from apps.ai.serializers import (
    AIConversationMessageSerializer,
    AIConversationSerializer,
    AskQuestionSerializer,
)
from apps.ai.services.esg_assistant import ask_esg_assistant


class AIConversationViewSet(
    TenantScopedViewSetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET  /api/esg-assistant/conversations/            -- list (org-scoped)
    POST /api/esg-assistant/conversations/             -- start a new conversation
    GET  /api/esg-assistant/conversations/{id}/         -- retrieve one
    GET  /api/esg-assistant/conversations/{id}/messages/ -- full history
    POST /api/esg-assistant/conversations/{id}/ask/     -- ask a question

    Org-scoped, not per-user-private (see AIConversation's own docstring):
    any org member who can use AI sees every conversation in their org.
    """

    queryset = AIConversation.objects.all().order_by("-created_at")
    serializer_class = AIConversationSerializer
    permission_classes = [CanUseAI]

    def perform_create(self, serializer):
        ctx = resolve_tenant_context(self.request)
        serializer.save(organization=ctx.organization, user=self.request.user)

    @action(detail=True, methods=["GET"])
    def messages(self, request, pk=None):
        """GET /api/esg-assistant/conversations/{id}/messages/ -- every
        message in this conversation, oldest first (AIConversationMessage.
        Meta.ordering)."""
        conversation = self.get_object()
        return Response(AIConversationMessageSerializer(conversation.messages.all(), many=True).data)

    @action(detail=True, methods=["POST"])
    def ask(self, request, pk=None):
        """POST /api/esg-assistant/conversations/{id}/ask/ -- ask a
        question in this conversation. Always returns 200/201; a refused
        or failed AI call is NOT an HTTP error (AI being unavailable is an
        expected, non-exceptional state -- I6 fail-safe) -- the response
        simply carries assistant_message: null, and the question itself
        is still recorded (see ask_esg_assistant's own docstring)."""
        conversation = self.get_object()
        body = AskQuestionSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        message = ask_esg_assistant(conversation, body.validated_data["question"], actor=request.user)
        if message is None:
            return Response(
                {"assistant_message": None, "detail": "The assistant could not generate a response right now."},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"assistant_message": AIConversationMessageSerializer(message).data},
            status=status.HTTP_201_CREATED,
        )
