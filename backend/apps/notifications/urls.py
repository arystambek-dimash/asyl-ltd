from rest_framework.routers import SimpleRouter
from .views import NotificationViewSet

router = SimpleRouter()
router.register("portal/notifications", NotificationViewSet, basename="portal-notifications")
urlpatterns = router.urls
