from rest_framework.routers import SimpleRouter

from .views import ApiPayAccountViewSet, DepartmentViewSet

router = SimpleRouter()
router.register("departments", DepartmentViewSet, basename="department")
router.register("apipay-accounts", ApiPayAccountViewSet, basename="apipay-account")
urlpatterns = router.urls
