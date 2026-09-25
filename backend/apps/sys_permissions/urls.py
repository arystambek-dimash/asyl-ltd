from rest_framework.routers import SimpleRouter
from .views import PermissionViewSet

router = SimpleRouter()
router.register("permissions", PermissionViewSet, basename="permissions")
urlpatterns = router.urls
