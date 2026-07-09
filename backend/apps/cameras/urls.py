from django.urls import path
from .views import (
    CameraAiResetView, CameraAiView, CameraAuthView, CameraListView, CameraTokenView,
)

urlpatterns = [
    path("cameras/", CameraListView.as_view()),
    path("cameras/token/", CameraTokenView.as_view()),
    path("cameras/auth/", CameraAuthView.as_view()),
    path("cameras/<int:cam_id>/ai/", CameraAiView.as_view()),
    path("cameras/<int:cam_id>/ai/reset/", CameraAiResetView.as_view()),
]
