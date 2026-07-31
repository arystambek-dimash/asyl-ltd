from rest_framework.routers import DefaultRouter

from .views import GrainSupplyViewSet, SiloViewSet, WagonViewSet

router = DefaultRouter()
router.register("grain/supplies", GrainSupplyViewSet, basename="grain-supply")
router.register("grain/wagons", WagonViewSet, basename="grain-wagon")
router.register("grain/silos", SiloViewSet, basename="grain-silo")

urlpatterns = router.urls
