from rest_framework.routers import DefaultRouter
from .views import ClientViewSet, DepartmentViewSet, StoreViewSet

router = DefaultRouter()
router.register("clients", ClientViewSet)
router.register("stores", StoreViewSet)
router.register("departments", DepartmentViewSet)
urlpatterns = router.urls
