from rest_framework.routers import DefaultRouter
from .views import PermissionViewSet, RoleViewSet

router = DefaultRouter()
router.register("permissions", PermissionViewSet, basename="permissions")
router.register("roles", RoleViewSet)
urlpatterns = router.urls
