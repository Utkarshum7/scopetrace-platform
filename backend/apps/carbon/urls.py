from rest_framework.routers import DefaultRouter

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

urlpatterns = router.urls
