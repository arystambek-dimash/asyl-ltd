from django.urls import path
from rest_framework.routers import DefaultRouter

from .registration import RegisterView
from .views import PortalCatalogViewSet, PortalOrderViewSet, PortalStoreViewSet

router = DefaultRouter()
router.register("portal/catalog", PortalCatalogViewSet, basename="portal-catalog")
router.register("portal/orders", PortalOrderViewSet, basename="portal-orders")
router.register("portal/stores", PortalStoreViewSet, basename="portal-stores")
urlpatterns = router.urls + [
    path("portal/register/", RegisterView.as_view(), name="portal-register")
]
