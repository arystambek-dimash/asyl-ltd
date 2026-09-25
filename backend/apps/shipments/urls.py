from django.urls import path

from .views import LoaderViewSet, WaybillSettingsView

urlpatterns = [
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
    path("loader/wagon-report/compose/", LoaderViewSet.as_view({"get": "report_compose"})),
    path("loader/wagon-report/send/", LoaderViewSet.as_view({"post": "report_send"})),
]
