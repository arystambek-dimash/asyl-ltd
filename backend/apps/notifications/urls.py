from rest_framework.routers import DefaultRouter
from .views import NotificationViewSet

router = DefaultRouter()
router.register("portal/notifications", NotificationViewSet, basename="portal-notifications")
urlpatterns = router.urls
