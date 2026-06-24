from rest_framework.routers import DefaultRouter
from .views import ClientViewSet, StoreViewSet

router = DefaultRouter()
router.register("clients", ClientViewSet)
router.register("stores", StoreViewSet)
urlpatterns = router.urls
