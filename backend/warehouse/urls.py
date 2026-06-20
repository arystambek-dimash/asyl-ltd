from rest_framework.routers import DefaultRouter
from .views import StockViewSet, StockReceiptViewSet

router = DefaultRouter()
router.register("stock/receipts", StockReceiptViewSet, basename="stock-receipts")
router.register("stock", StockViewSet, basename="stock")
urlpatterns = router.urls
