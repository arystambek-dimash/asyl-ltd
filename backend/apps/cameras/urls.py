from django.urls import path

from .api_views.access import CameraAuthView, CameraTokenView
from .api_views.configuration import CameraListView, MonoblockCameraSettingsView
from .api_views.counting import CameraCountingLineFrameView, CameraCountingLineView
from .api_views.history import CameraAiSessionListView
from .api_views.operations import (
    AlwaysOnAnalyticsView,
    AlwaysOnCameraSettingsView,
    AlwaysOnDetectionsView,
    AlwaysOnProductionView,
    AlwaysOnStockRetryView,
    AlwaysOnUnknownColorView,
    ShippingBoardSettingsView,
    ShippingContinuousAnalyticsView,
    ShippingContinuousDetectionsView,
    ShippingContinuousHistoryView,
    ShippingContinuousSettingsView,
    WagonNumberCameraSettingsView,
)
from .api_views.shipping_sessions import (
    ShippingSessionListView, ShippingSegmentDetailView, ShippingSegmentIdentifyView,
    ShippingSessionSettingsView, ShippingSegmentPhotoView,
)
from .api_views.transport_camera import (
    ShippingTransportCameraView,
    ShippingTransportRecognizeView,
)
from .api_views.vehicle_runtime import VehiclePlateRuntimeView
from .api_views.wagon_arch_runtime import WagonArchCameraRuntimeView
from .vehicle_plate_events import VehiclePlateWebhookView

urlpatterns = [
    path(
        "integrations/vehicle-plate-events",
        VehiclePlateWebhookView.as_view(),
        name="vehicle-plate-events-webhook",
    ),
    path("cameras/", CameraListView.as_view()),
    path("cameras/shipping-sessions/", ShippingSessionListView.as_view()),
    path("cameras/shipping-session-settings/", ShippingSessionSettingsView.as_view()),
    path("cameras/shipping-segments/<int:pk>/", ShippingSegmentDetailView.as_view()),
    path("cameras/shipping-segments/<int:pk>/identify/", ShippingSegmentIdentifyView.as_view()),
    path("cameras/shipping-segments/<int:pk>/photo/", ShippingSegmentPhotoView.as_view()),
    path("cameras/token/", CameraTokenView.as_view()),
    path("cameras/auth/", CameraAuthView.as_view()),
    path("cameras/monoblock-settings/", MonoblockCameraSettingsView.as_view()),
    path("cameras/always-on-settings/", AlwaysOnCameraSettingsView.as_view()),
    path("cameras/always-on-detections/", AlwaysOnDetectionsView.as_view()),
    path(
        "cameras/shipping-continuous-settings/",
        ShippingContinuousSettingsView.as_view(),
    ),
    path(
        "cameras/shipping-continuous-detections/",
        ShippingContinuousDetectionsView.as_view(),
    ),
    path(
        "cameras/shipping-continuous-analytics/",
        ShippingContinuousAnalyticsView.as_view(),
    ),
    path(
        "cameras/shipping-continuous-history/",
        ShippingContinuousHistoryView.as_view(),
    ),
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
        "cameras/always-on-production/unknown-colors/",
        AlwaysOnUnknownColorView.as_view(),
    ),
    path("cameras/shipping-settings/", ShippingBoardSettingsView.as_view()),
    path("cameras/ai/sessions/", CameraAiSessionListView.as_view()),
    path("cameras/<str:cam>/counting-line", CameraCountingLineView.as_view()),
    path("cameras/<str:cam>/counting-line/frame", CameraCountingLineFrameView.as_view()),
    # Runtime-экраны: без <cam> — только чтение камеры из настроек,
    # с <cam> — только сохранение зоны этой камеры суперпользователем.
    path(
        "cameras/vehicle-plate-runtime/",
        VehiclePlateRuntimeView.as_view(http_method_names=["get", "options"]),
        name="vehicle-plate-runtime-bootstrap",
    ),
    path(
        "cameras/<str:cam>/vehicle-plate-runtime/",
        VehiclePlateRuntimeView.as_view(http_method_names=["put", "options"]),
        name="vehicle-plate-runtime",
    ),
    path(
        "cameras/wagon-arch-runtime/",
        WagonArchCameraRuntimeView.as_view(http_method_names=["get", "options"]),
        name="wagon-arch-runtime-bootstrap",
    ),
    path(
        "cameras/<str:cam>/wagon-arch-runtime/",
        WagonArchCameraRuntimeView.as_view(http_method_names=["put", "options"]),
        name="wagon-arch-runtime",
    ),
    path("cameras/<str:cam>/transport-camera/", ShippingTransportCameraView.as_view()),
    path("cameras/<str:cam>/transport-camera/recognize/", ShippingTransportRecognizeView.as_view()),
]
