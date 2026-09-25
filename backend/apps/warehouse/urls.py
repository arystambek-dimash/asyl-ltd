from rest_framework.routers import SimpleRouter

from .views import StockViewSet, WarehouseViewSet

router = SimpleRouter()
router.register("warehouses", WarehouseViewSet, basename="warehouse")
router.register("stock", StockViewSet, basename="stock")
urlpatterns = [*router.urls]
