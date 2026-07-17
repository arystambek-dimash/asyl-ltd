from django.urls import path
from .views import (
    CameraAiResetView, CameraAiSessionListView, CameraAiView, CameraAuthView, CameraHealthView,
    CameraListView, CameraTokenView, MonoblockCameraSettingsView,
)

urlpatterns = [
    path("cameras/", CameraListView.as_view()),
    path("cameras/token/", CameraTokenView.as_view()),
    path("cameras/auth/", CameraAuthView.as_view()),
    path("cameras/health/", CameraHealthView.as_view()),
    path("cameras/monoblock-settings/", MonoblockCameraSettingsView.as_view()),
    path("cameras/ai/sessions/", CameraAiSessionListView.as_view()),
    path("cameras/<str:cam>/ai/", CameraAiView.as_view()),
    path("cameras/<str:cam>/ai/reset/", CameraAiResetView.as_view()),
]
