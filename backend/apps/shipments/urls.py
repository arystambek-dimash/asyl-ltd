from django.urls import path
from .views import ShipmentViewSet

shipment_actions = {
    "arrive": ShipmentViewSet.as_view({"post": "arrive"}),
    "load": ShipmentViewSet.as_view({"post": "load"}),
    "finish_loading": ShipmentViewSet.as_view({"post": "finish_loading"}),
    "rewind_loading": ShipmentViewSet.as_view({"post": "rewind_loading"}),
    "ship": ShipmentViewSet.as_view({"post": "ship"}),
}

urlpatterns = [
    path("orders/<int:pk>/arrive/", shipment_actions["arrive"]),
    path("orders/<int:pk>/load/", shipment_actions["load"]),
    path("orders/<int:pk>/finish-loading/", shipment_actions["finish_loading"]),
    path("orders/<int:pk>/rewind-loading/", shipment_actions["rewind_loading"]),
    path("orders/<int:pk>/ship/", shipment_actions["ship"]),
]
