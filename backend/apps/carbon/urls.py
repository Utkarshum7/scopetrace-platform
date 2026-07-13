from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.carbon.metrics_views import (
    ActivityFeedView,
    MetricsBreakdownView,
    MetricsSummaryView,
    MetricsTimeseriesView,
    PlatformMetricsView,
)
from apps.carbon.report_views import ComplianceReportCSVView, ComplianceReportView
from apps.carbon.views import (
    ActivityTypeViewSet,
    EmissionCalculationViewSet,
    EmissionFactorViewSet,
    FactorDatasetViewSet,
)

router = DefaultRouter()
router.register(r"activity-types", ActivityTypeViewSet, basename="activitytype")
router.register(r"factor-datasets", FactorDatasetViewSet, basename="factordataset")
router.register(r"emission-factors", EmissionFactorViewSet, basename="emissionfactor")
router.register(r"calculations", EmissionCalculationViewSet, basename="calculation")

urlpatterns = [
    path("metrics/summary/", MetricsSummaryView.as_view(), name="metrics-summary"),
    path("metrics/timeseries/", MetricsTimeseriesView.as_view(), name="metrics-timeseries"),
    path("metrics/breakdown/", MetricsBreakdownView.as_view(), name="metrics-breakdown"),
    path("metrics/activity/", ActivityFeedView.as_view(), name="metrics-activity"),
    path("metrics/platform/", PlatformMetricsView.as_view(), name="metrics-platform"),
    path("reports/compliance/", ComplianceReportView.as_view(), name="reports-compliance"),
    path("reports/compliance/csv/", ComplianceReportCSVView.as_view(), name="reports-compliance-csv"),
    *router.urls,
]
