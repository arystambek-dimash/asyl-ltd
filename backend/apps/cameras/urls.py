from django.urls import path
from .views import (
    CameraAiRecordingVideoView, CameraAiRecordingView, CameraAiResetView,
    CameraAiSessionHistoryView, CameraAiSessionListView, CameraAiView,
    CameraAuthView, CameraCountingLineView, CameraHealthView, CameraListView, CameraTokenView,
    MonoblockCameraSettingsView, ShippingBoardSettingsView,
)

urlpatterns = [
    path("cameras/", CameraListView.as_view()),
    path("cameras/token/", CameraTokenView.as_view()),
    path("cameras/auth/", CameraAuthView.as_view()),
    path("cameras/health/", CameraHealthView.as_view()),
    path("cameras/monoblock-settings/", MonoblockCameraSettingsView.as_view()),
    path("cameras/shipping-settings/", ShippingBoardSettingsView.as_view()),
    path("cameras/ai/sessions/", CameraAiSessionListView.as_view()),
    path("cameras/ai/history/", CameraAiSessionHistoryView.as_view()),
    path("cameras/ai/history/<int:pk>/recording/", CameraAiRecordingView.as_view()),
    path("cameras/ai/history/<int:pk>/recording/video/", CameraAiRecordingVideoView.as_view()),
    path("cameras/<str:cam>/counting-line", CameraCountingLineView.as_view()),
    path("cameras/<str:cam>/ai/", CameraAiView.as_view()),
    path("cameras/<str:cam>/ai/reset/", CameraAiResetView.as_view()),
]
