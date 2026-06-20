from rest_framework.routers import DefaultRouter
from .views import GradeViewSet, PackagingViewSet, ProductViewSet

router = DefaultRouter()
router.register("grades", GradeViewSet)
router.register("packagings", PackagingViewSet)
router.register("products", ProductViewSet)
urlpatterns = router.urls
