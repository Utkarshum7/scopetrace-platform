from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.ai.views import AIConversationViewSet, ReportNarrationListView, ReportNarrationRegenerateView

router = DefaultRouter()
router.register(r"esg-assistant/conversations", AIConversationViewSet, basename="aiconversation")

urlpatterns = [
    path("report-narration/", ReportNarrationListView.as_view(), name="report-narration-list"),
    path("report-narration/regenerate/", ReportNarrationRegenerateView.as_view(), name="report-narration-regenerate"),
    *router.urls,
]
