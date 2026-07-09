from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.ai.ops_views import AICostGovernanceView, AIObservabilityView, AIOpsHealthView
from apps.ai.views import AIConversationViewSet, ReportNarrationListView, ReportNarrationRegenerateView

router = DefaultRouter()
router.register(r"esg-assistant/conversations", AIConversationViewSet, basename="aiconversation")

urlpatterns = [
    path("report-narration/", ReportNarrationListView.as_view(), name="report-narration-list"),
    path("report-narration/regenerate/", ReportNarrationRegenerateView.as_view(), name="report-narration-regenerate"),
    path("ai/costs/", AICostGovernanceView.as_view(), name="ai-costs"),
    path("ai/ops/observability/", AIObservabilityView.as_view(), name="ai-ops-observability"),
    path("ai/ops/health/", AIOpsHealthView.as_view(), name="ai-ops-health"),
    *router.urls,
]
