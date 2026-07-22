from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from rest_framework_simplejwt.views import TokenObtainPairView
from apps.accounts.views import MeView, RevocableTokenRefreshView
from config.throttles import LoginRateThrottle


class ThrottledTokenObtainPairView(TokenObtainPairView):
    """Логин под отдельным жёстким лимитом (защита от подбора пароля)."""
    throttle_classes = [LoginRateThrottle]


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/login/", ThrottledTokenObtainPairView.as_view(), name="login"),
    path("api/auth/refresh/", RevocableTokenRefreshView.as_view(), name="refresh"),
    path("api/auth/me/", MeView.as_view(), name="me"),
    path("api/", include("apps.catalog.urls")),
    path("api/", include("apps.clients.urls")),
    path("api/", include("apps.eventlog.urls")),
    path("api/", include("apps.orders.urls")),
    path("api/", include("apps.warehouse.urls")),
    path("api/", include("apps.shipments.urls")),
    path("api/", include("apps.portal.urls")),
    path("api/", include("apps.notifications.urls")),
    path("api/", include("apps.rbac.urls")),
    path("api/", include("apps.employees.urls")),
    path("api/", include("apps.cameras.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
