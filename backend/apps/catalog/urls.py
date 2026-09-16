from django.urls import path
from rest_framework.routers import DefaultRouter
from .photos import ProductPhotoView
from .views import ProductViewSet, ClientPricesView

router = DefaultRouter()
router.register("products", ProductViewSet, basename="product")
urlpatterns = router.urls + [
    path("client-prices/", ClientPricesView.as_view(), name="client-prices"),
    path("product-photos/<int:pk>/", ProductPhotoView.as_view(), name="product-photo"),
]
