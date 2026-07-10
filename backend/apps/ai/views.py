"""
Phase 7e -- apps.ai's own API views. The first Phase 7 capability
surfaced through its own endpoints rather than an @action on an existing
apps.ingestion/apps.carbon viewset, because esg_assistant has no single
governed record to hang off of the way anomaly_detection/
factor_recommendation/validation_assistance do. Phase 7f adds
ReportNarrationListView/ReportNarrationRegenerateView, following the same
apps.ai-owns-its-own-endpoints precedent.

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

Report narration is gated by CanViewActivity instead -- matching the
underlying compliance report's own RBAC boundary (Org Admin/Auditor),
not the broader AI-feature gate. Narration is advisory content ABOUT a
gated audit artifact, so it inherits that artifact's own access level
rather than the general "can use AI" permission, which would otherwise
let an Analyst read AI commentary on report data they can't see the
report itself for. See ADR 0013.
"""
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.mixins import TenantScopedViewSetMixin
from apps.accounts.permissions import CanUseAI, CanViewActivity
from apps.accounts.tenancy import resolve_tenant_context
from apps.ai.models import AIConversation, AIReportNarration
from apps.ai.serializers import (
    AIConversationMessageSerializer,
    AIConversationSerializer,
    AIReportNarrationSerializer,
    AskQuestionSerializer,
    RegenerateNarrationSerializer,
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

    def get_throttles(self):
        # Phase 7.5 (H4-7): only `ask` actually calls a provider (a real,
        # billable round trip) -- list/retrieve/create/messages are cheap
        # reads or a bare row insert, so they stay on the generic 'user'
        # rate rather than the tighter 'ai' scope.
        if self.action == "ask":
            self.throttle_scope = "ai"
            return [ScopedRateThrottle()]
        return super().get_throttles()

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


class _BaseReportNarrationView(APIView):
    """Org Admin / Auditor only -- matches _BaseComplianceReportView's own
    CanViewActivity gate exactly (apps.carbon.report_views), since
    narration is advisory content about that same gated report."""
    permission_classes = [CanViewActivity]

    def _resolve_organization(self, request):
        ctx = resolve_tenant_context(request)
        if ctx.organization is None:
            raise PermissionDenied(
                "Select an organization (X-Organization-ID) to view report narrations."
            )
        return ctx.organization


class ReportNarrationListView(_BaseReportNarrationView):
    """GET /api/report-narration/ -- every narration for this org, newest
    first (AIReportNarration.Meta.ordering), optionally filtered by
    date_from/date_to/scope query params (exact match, mirroring the
    compliance report's own period). Read-only -- no mutation verb."""

    def get(self, request):
        organization = self._resolve_organization(request)
        qs = AIReportNarration.objects.filter(organization=organization)
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        scope = request.query_params.get("scope")
        if date_from:
            qs = qs.filter(date_from=date_from)
        if date_to:
            qs = qs.filter(date_to=date_to)
        if scope is not None:
            qs = qs.filter(scope=scope)
        return Response(AIReportNarrationSerializer(qs, many=True).data)


class ReportNarrationRegenerateView(APIView):
    """POST /api/report-narration/regenerate/ -- dispatches
    generate_report_narration_task on the 'ai' queue and returns
    immediately (202); the narration itself is generated in the
    background and appears via ReportNarrationListView once ready. Never
    mutates a governed ESG record -- only ever queues work that writes
    AIReportNarration rows. Same CanViewActivity gate as the list view
    and the compliance report itself."""
    permission_classes = [CanViewActivity]
    # Phase 7.5 (H4-7): dispatches a real generation task (a billable
    # provider call, just async) -- same 'ai' throttle scope as
    # AIConversationViewSet.ask, for the same reason.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ai"

    def post(self, request):
        ctx = resolve_tenant_context(request)
        if ctx.organization is None:
            raise PermissionDenied(
                "Select an organization (X-Organization-ID) to regenerate a report narration."
            )
        body = RegenerateNarrationSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        d = body.validated_data

        from apps.ai.tasks import generate_report_narration_task

        generate_report_narration_task.delay(
            organization_id=str(ctx.organization.id),
            date_from=str(d["date_from"]),
            date_to=str(d["date_to"]),
            scope=d.get("scope", ""),
            actor_id=str(request.user.id) if request.user.is_authenticated else None,
        )
        return Response(
            {"detail": "Report narration generation queued."},
            status=status.HTTP_202_ACCEPTED,
        )
