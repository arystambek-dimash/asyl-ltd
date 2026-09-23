from django.urls import path

from .views import LoaderViewSet, ShipmentViewSet, WaybillSettingsView

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
    path("loader/queue/", LoaderViewSet.as_view({"get": "queue"})),
    path("loader/history/", LoaderViewSet.as_view({"get": "history"})),
    path("loader/orders/<int:pk>/dispatch/", LoaderViewSet.as_view({"post": "confirm"})),
    path("loader/orders/<int:pk>/rollback/", LoaderViewSet.as_view({"post": "rollback"})),
    path("loader/orders/<int:pk>/waybill/", LoaderViewSet.as_view({"get": "waybill"})),
    path("loader/waybill-settings/", WaybillSettingsView.as_view()),
    path("loader/rail-report/preview/", LoaderViewSet.as_view({"post": "rail_preview"})),
    path("loader/rail-report/options/", LoaderViewSet.as_view({"get": "rail_options"})),
    path("loader/rail-report/product-codes/", LoaderViewSet.as_view({"post": "rail_product_code"})),
    path("loader/rail-report/client-names/", LoaderViewSet.as_view({"post": "rail_client_name"})),
    path("loader/rail-report/apply/", LoaderViewSet.as_view({"post": "rail_apply"})),
]
