from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import ProductViewSet, ClientPricesView

router = DefaultRouter()
router.register("products", ProductViewSet)
urlpatterns = router.urls + [
    path("client-prices/", ClientPricesView.as_view(), name="client-prices"),
]
