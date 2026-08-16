from django.urls import path
from .views import (
    CameraAiRecordingVideoView, CameraAiRecordingView, CameraAiResetView,
    CameraAiSessionHistoryView, CameraAiSessionListView, CameraAiView,
    CameraConveyorStopView,
    CameraAuthView, CameraCountingLineView, CameraHealthView, CameraListView, CameraTokenView,
    AlwaysOnAnalyticsArchiveView,
    AlwaysOnAnalyticsSubtractView, AlwaysOnAnalyticsView,
    AlwaysOnCameraSettingsView,
    AlwaysOnDetectionsView, MonoblockCameraSettingsView, ShippingBoardSettingsView,
    AlwaysOnProductionView, AlwaysOnStockRetryView,
    MonoblockDeviceDetailView, MonoblockDeviceListView,
    WagonNumberCameraSettingsView,
)

urlpatterns = [
    path("cameras/", CameraListView.as_view()),
    path("cameras/token/", CameraTokenView.as_view()),
    path("cameras/auth/", CameraAuthView.as_view()),
    path("cameras/health/", CameraHealthView.as_view()),
    path("cameras/monoblock-settings/", MonoblockCameraSettingsView.as_view()),
    path("cameras/monoblock-devices/", MonoblockDeviceListView.as_view()),
    path("cameras/monoblock-devices/<int:pk>/", MonoblockDeviceDetailView.as_view()),
    path("cameras/always-on-settings/", AlwaysOnCameraSettingsView.as_view()),
    path("cameras/always-on-detections/", AlwaysOnDetectionsView.as_view()),
    path(
        "cameras/wagon-number-settings/",
        WagonNumberCameraSettingsView.as_view(),
    ),
    path("cameras/always-on-analytics/", AlwaysOnAnalyticsView.as_view()),
    path("cameras/always-on-production/", AlwaysOnProductionView.as_view()),
    path(
        "cameras/always-on-production/batches/<int:batch_id>/retry/",
        AlwaysOnStockRetryView.as_view(),
    ),
    path(
        "cameras/always-on-analytics/<str:cam>/subtract/",
        AlwaysOnAnalyticsSubtractView.as_view(),
    ),
    path(
        "cameras/always-on-analytics/archives/",
        AlwaysOnAnalyticsArchiveView.as_view(),
    ),
    path(
        "cameras/always-on-analytics/archives/<int:archive_id>/",
        AlwaysOnAnalyticsArchiveView.as_view(),
    ),
    path(
        "cameras/always-on-analytics/<str:cam>/archive/",
        AlwaysOnAnalyticsArchiveView.as_view(),
    ),
    path("cameras/shipping-settings/", ShippingBoardSettingsView.as_view()),
    path("cameras/ai/sessions/", CameraAiSessionListView.as_view()),
    path("cameras/ai/history/", CameraAiSessionHistoryView.as_view()),
    path("cameras/ai/history/<int:pk>/recording/", CameraAiRecordingView.as_view()),
    path("cameras/ai/history/<int:pk>/recording/video/", CameraAiRecordingVideoView.as_view()),
    path("cameras/<str:cam>/counting-line", CameraCountingLineView.as_view()),
    path("cameras/<str:cam>/ai/", CameraAiView.as_view()),
    path("cameras/<str:cam>/ai/reset/", CameraAiResetView.as_view()),
    path(
        "cameras/<str:cam>/ai/conveyor/stop/",
        CameraConveyorStopView.as_view(),
    ),
]
