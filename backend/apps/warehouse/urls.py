from rest_framework.routers import DefaultRouter

from .views import StockViewSet, WarehouseViewSet

router = DefaultRouter()
router.register("warehouses", WarehouseViewSet, basename="warehouse")
router.register("stock", StockViewSet, basename="stock")
urlpatterns = [*router.urls]
