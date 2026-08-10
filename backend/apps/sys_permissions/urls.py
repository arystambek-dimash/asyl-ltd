from rest_framework.routers import DefaultRouter
from .views import PermissionViewSet

router = DefaultRouter()
router.register("permissions", PermissionViewSet, basename="permissions")
urlpatterns = router.urls
