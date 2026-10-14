from django.urls import path

from .views import DashboardSettingsView

urlpatterns = [
    path("dashboard/settings/", DashboardSettingsView.as_view()),
]
