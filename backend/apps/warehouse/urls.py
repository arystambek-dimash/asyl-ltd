from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import FactoryMapView, StockViewSet

router = DefaultRouter()
router.register("stock", StockViewSet, basename="stock")
urlpatterns = [
    path("factory/map/", FactoryMapView.as_view(), name="factory-map"),
    *router.urls,
]
