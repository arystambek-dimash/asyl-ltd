from django.urls import path
from .views import (
    CameraAiResetView, CameraAiView, CameraAuthView, CameraHealthView,
    CameraListView, CameraTokenView,
)

urlpatterns = [
    path("cameras/", CameraListView.as_view()),
    path("cameras/token/", CameraTokenView.as_view()),
    path("cameras/auth/", CameraAuthView.as_view()),
    path("cameras/health/", CameraHealthView.as_view()),
    path("cameras/<str:cam>/ai/", CameraAiView.as_view()),
    path("cameras/<str:cam>/ai/reset/", CameraAiResetView.as_view()),
]
