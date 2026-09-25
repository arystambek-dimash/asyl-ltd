from rest_framework.routers import SimpleRouter

from .views import ClientViewSet, StoreViewSet

router = SimpleRouter()
router.register("clients", ClientViewSet)
router.register("stores", StoreViewSet)
urlpatterns = router.urls
