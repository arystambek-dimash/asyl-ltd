from rest_framework.routers import DefaultRouter
from .views import PortalCatalogViewSet, PortalOrderViewSet

router = DefaultRouter()
router.register("portal/catalog", PortalCatalogViewSet, basename="portal-catalog")
router.register("portal/orders", PortalOrderViewSet, basename="portal-orders")
urlpatterns = router.urls
