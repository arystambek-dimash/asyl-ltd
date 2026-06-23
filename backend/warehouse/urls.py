from rest_framework.routers import DefaultRouter
from .views import (
    StockViewSet, StockReceiptViewSet, StockAdjustViewSet, StockMovementViewSet,
)

router = DefaultRouter()
router.register("stock/receipts", StockReceiptViewSet, basename="stock-receipts")
router.register("stock/adjust", StockAdjustViewSet, basename="stock-adjust")
router.register("stock/movements", StockMovementViewSet, basename="stock-movements")
router.register("stock", StockViewSet, basename="stock")
urlpatterns = router.urls

