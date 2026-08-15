from django.urls import path

from .views import (
    ConveyorAiObservationView,
    ConveyorDeviceDetailView,
    ConveyorDeviceDisableView,
    ConveyorDeviceEmergencyStopView,
    ConveyorDeviceListView,
    ConveyorDeviceRotateSecretView,
    ConveyorDeviceSyncView,
)

urlpatterns = [
    path("conveyors/v1/device/sync/", ConveyorDeviceSyncView.as_view()),
    path("conveyors/v1/ai/observation/", ConveyorAiObservationView.as_view()),
    path("conveyors/devices/", ConveyorDeviceListView.as_view()),
    path(
        "conveyors/devices/<uuid:public_id>/",
        ConveyorDeviceDetailView.as_view(),
    ),
    path(
        "conveyors/devices/<uuid:public_id>/rotate-secret/",
        ConveyorDeviceRotateSecretView.as_view(),
    ),
    path(
        "conveyors/devices/<uuid:public_id>/disable/",
        ConveyorDeviceDisableView.as_view(),
    ),
    path(
        "conveyors/devices/<uuid:public_id>/emergency-stop/",
        ConveyorDeviceEmergencyStopView.as_view(),
    ),
]
