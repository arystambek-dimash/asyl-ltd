"""Stable public imports for the camera API.

The endpoint implementations are grouped by responsibility in ``api_views``.
Keeping this small facade lets existing URL configuration and third-party
imports continue to work without turning one module into the whole camera
subsystem again.
"""

from .api_views.access import (
    CAM_COOKIE,
    CAM_STREAM_PATH,
    CAM_TOKEN_AUDIENCE,
    CAM_TOKEN_MAX_AGE,
    CAM_TOKEN_SALT,
    CAM_TOKEN_VERSION,
    MAX_ORIGINAL_URI_LENGTH,
    CameraAuthView,
    CameraTokenView,
)
from .api_views.configuration import (
    CameraListView,
    MonoblockCameraSettingsView,
    MonoblockDeviceDetailView,
    MonoblockDeviceListView,
)
from .api_views.counting import (
    CameraAiResetView,
    CameraAiView,
    CameraConveyorStopView,
    CameraCountingLineView,
)
from .api_views.history import (
    RECORDING_TOKEN_MAX_AGE,
    RECORDING_TOKEN_SALT,
    CameraAiRecordingVideoView,
    CameraAiRecordingView,
    CameraAiSessionHistoryView,
    CameraAiSessionListView,
)
from .api_views.operations import (
    AlwaysOnAnalyticsArchiveView,
    AlwaysOnAnalyticsSubtractView,
    AlwaysOnAnalyticsView,
    AlwaysOnCameraSettingsView,
    AlwaysOnDetectionsView,
    AlwaysOnProductionView,
    AlwaysOnStockRetryView,
    CameraHealthView,
    ShippingBoardSettingsView,
    WagonNumberCameraSettingsView,
)

__all__ = [
    "CAM_COOKIE",
    "CAM_STREAM_PATH",
    "CAM_TOKEN_AUDIENCE",
    "CAM_TOKEN_MAX_AGE",
    "CAM_TOKEN_SALT",
    "CAM_TOKEN_VERSION",
    "MAX_ORIGINAL_URI_LENGTH",
    "RECORDING_TOKEN_MAX_AGE",
    "RECORDING_TOKEN_SALT",
    "AlwaysOnAnalyticsArchiveView",
    "AlwaysOnAnalyticsSubtractView",
    "AlwaysOnAnalyticsView",
    "AlwaysOnCameraSettingsView",
    "AlwaysOnDetectionsView",
    "AlwaysOnProductionView",
    "AlwaysOnStockRetryView",
    "CameraAiRecordingVideoView",
    "CameraAiRecordingView",
    "CameraAiResetView",
    "CameraAiSessionHistoryView",
    "CameraAiSessionListView",
    "CameraAiView",
    "CameraAuthView",
    "CameraConveyorStopView",
    "CameraCountingLineView",
    "CameraHealthView",
    "CameraListView",
    "CameraTokenView",
    "MonoblockCameraSettingsView",
    "MonoblockDeviceDetailView",
    "MonoblockDeviceListView",
    "ShippingBoardSettingsView",
    "WagonNumberCameraSettingsView",
]
