from rest_framework.routers import SimpleRouter

from .shop_views import ShopOrderViewSet
from .views import EmployeeViewSet

router = SimpleRouter()
router.register("employees", EmployeeViewSet)
router.register("shop-orders", ShopOrderViewSet, basename="shop-order")
urlpatterns = router.urls
