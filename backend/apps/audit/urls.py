from django.urls import path

from apps.audit.views import AuditChainVerifyView

urlpatterns = [
    path("audit/verify/", AuditChainVerifyView.as_view(), name="audit-verify"),
]
