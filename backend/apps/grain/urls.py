from django.urls import path
from rest_framework.routers import DefaultRouter

from .photos import WeighingPhotoView
from .views import (
    AutomaticPassageScaleAcknowledgeView,
    AutomaticPassageScaleRuntimeView,
    AutomaticPassageScaleSettingsView,
    GrainSupplyViewSet,
    SiloTypeViewSet,
    SiloViewSet,
    TruckScaleReadingView,
    UnassignedWeighingViewSet,
    WagonViewSet,
)

router = DefaultRouter()
router.register("grain/supplies", GrainSupplyViewSet, basename="grain-supply")
router.register("grain/wagons", WagonViewSet, basename="grain-wagon")
router.register("grain/silos", SiloViewSet, basename="grain-silo")
router.register(
    "grain/unassigned-weighings",
    UnassignedWeighingViewSet,
    basename="grain-unassigned-weighing",
)
router.register(
    "grain/silo-types", SiloTypeViewSet, basename="grain-silo-type")
router.register(
    "grain/types", SiloTypeViewSet, basename="grain-type")

urlpatterns = [
    path("truck-scale/reading/", TruckScaleReadingView.as_view()),
    path(
        "grain/automatic-passage-scale/acknowledge/",
        AutomaticPassageScaleAcknowledgeView.as_view(),
    ),
    path(
        "grain/automatic-passage-scale/runtime/",
        AutomaticPassageScaleRuntimeView.as_view(),
    ),
    path(
        "grain/automatic-passage-scale/settings/",
        AutomaticPassageScaleSettingsView.as_view(),
    ),
    path(
        "truck-scales/<str:scale_key>/reading/",
        TruckScaleReadingView.as_view(),
    ),
    path("grain/photos/<str:kind>/<int:pk>/", WeighingPhotoView.as_view()),
    *router.urls,
]
