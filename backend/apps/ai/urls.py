from rest_framework.routers import DefaultRouter

from apps.ai.views import AIConversationViewSet

router = DefaultRouter()
router.register(r"esg-assistant/conversations", AIConversationViewSet, basename="aiconversation")

urlpatterns = [
    *router.urls,
]
