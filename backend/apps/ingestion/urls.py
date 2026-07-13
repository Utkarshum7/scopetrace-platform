"""
apps/ingestion/urls.py

URL routing for the ingestion API.

WHY a separate urls.py per app:
  Keeps each app self-contained.  The root config/urls.py simply
  includes this file — it doesn't need to know about individual view classes.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    SAPUploadView,
    UtilityUploadView,
    TravelUploadView,
    UploadBatchViewSet,
    EmissionRecordViewSet,
    OrganizationViewSet,
    DataSourceViewSet,
)
from .export_views import RecordExportView

router = DefaultRouter()
router.register(r"batches", UploadBatchViewSet, basename="uploadbatch")
router.register(r"records", EmissionRecordViewSet, basename="emissionrecord")
router.register(r"organizations", OrganizationViewSet, basename="organization")
router.register(r"datasources", DataSourceViewSet, basename="datasource")

urlpatterns = [
    # File upload endpoints — one per source type
    path("upload/sap/", SAPUploadView.as_view(), name="upload-sap"),
    path("upload/utility/", UtilityUploadView.as_view(), name="upload-utility"),
    path("upload/travel/", TravelUploadView.as_view(), name="upload-travel"),
    # Streaming CSV export — MUST precede the router so "records/export/" is not
    # captured as a record detail lookup.
    path("records/export/", RecordExportView.as_view(), name="records-export"),
    # ViewSet-managed endpoints (list + detail)
    path("", include(router.urls)),
]
