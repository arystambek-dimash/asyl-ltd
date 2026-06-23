from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import PortalCatalogViewSet, PortalOrderViewSet, payment_info
from .registration import RegisterView

router = DefaultRouter()
router.register("portal/catalog", PortalCatalogViewSet, basename="portal-catalog")
router.register("portal/orders", PortalOrderViewSet, basename="portal-orders")
urlpatterns = router.urls + [
    path("portal/register/", RegisterView.as_view(), name="portal-register"),
    path("portal/payment-info/", payment_info, name="portal-payment-info"),
]
