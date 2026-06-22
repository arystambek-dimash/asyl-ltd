from django.contrib import admin
from django.urls import include, path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from accounts.views import MeView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/login/", TokenObtainPairView.as_view(), name="login"),
    path("api/auth/refresh/", TokenRefreshView.as_view(), name="refresh"),
    path("api/auth/me/", MeView.as_view(), name="me"),
    path("api/", include("catalog.urls")),
    path("api/", include("clients.urls")),
    path("api/", include("eventlog.urls")),
    path("api/", include("orders.urls")),
    path("api/", include("warehouse.urls")),
    path("api/", include("shipments.urls")),
    path("api/", include("portal.urls")),
    path("api/", include("rbac.urls")),
    path("api/", include("employees.urls")),
    path("api/", include("webhooks.urls")),
]

from django.conf import settings as _settings
from django.conf.urls.static import static as _static

if _settings.DEBUG:
    urlpatterns += _static(_settings.MEDIA_URL, document_root=_settings.MEDIA_ROOT)
