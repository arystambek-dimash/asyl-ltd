from rest_framework.routers import SimpleRouter
from .views import EventLogViewSet

router = SimpleRouter()
router.register("events", EventLogViewSet, basename="events")
urlpatterns = router.urls
