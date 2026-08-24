from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    GrainSupplyViewSet,
    SiloTypeViewSet,
    SiloViewSet,
    TruckScaleReadingView,
    WagonViewSet,
)

router = DefaultRouter()
router.register("grain/supplies", GrainSupplyViewSet, basename="grain-supply")
router.register("grain/wagons", WagonViewSet, basename="grain-wagon")
router.register("grain/silos", SiloViewSet, basename="grain-silo")
router.register(
    "grain/silo-types", SiloTypeViewSet, basename="grain-silo-type")
router.register(
    "grain/types", SiloTypeViewSet, basename="grain-type")

urlpatterns = [
    path("truck-scale/reading/", TruckScaleReadingView.as_view()),
    path(
        "truck-scales/<str:scale_key>/reading/",
        TruckScaleReadingView.as_view(),
    ),
    *router.urls,
]
