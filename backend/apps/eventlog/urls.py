from rest_framework.routers import DefaultRouter
from .views import EventLogViewSet

router = DefaultRouter()
router.register("events", EventLogViewSet, basename="events")
urlpatterns = router.urls
