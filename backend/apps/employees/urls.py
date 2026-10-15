from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import EmployeeViewSet, MyShopCatalogView, MyShopOrderViewSet, MyShopSummaryView

router = SimpleRouter()
router.register("employees", EmployeeViewSet)
router.register("my-shop/orders", MyShopOrderViewSet, basename="my-shop-order")
urlpatterns = [
    path("my-shop/", MyShopSummaryView.as_view(), name="my-shop"),
    path("my-shop/catalog/", MyShopCatalogView.as_view(), name="my-shop-catalog"),
    *router.urls,
]
