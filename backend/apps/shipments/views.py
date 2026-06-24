from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.orders.models import Order
from apps.rbac.permissions import PermViewSetMixin
from .serializers import ArrivalSerializer, LoadSerializer, ShipmentSerializer
from .services import finish_loading, record_arrival, record_count, record_shipment


class ShipmentViewSet(PermViewSetMixin, viewsets.GenericViewSet):
    queryset = Order.objects.select_related("shipment").prefetch_related("items__product")
    required_perms = {
        "arrive": "shipping.arrive",
        "load": "shipping.load",
        "finish_loading": "shipping.load",
        "ship": "shipping.ship",
    }

    @action(detail=True, methods=["post"], url_path="arrive")
    def arrive(self, request, pk=None):
        serializer = ArrivalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        shipment = record_arrival(
            self.get_object(),
            serializer.validated_data["weigh_in_kg"],
            request.user,
            debt_override=serializer.validated_data["debt_override"],
        )
        return Response(ShipmentSerializer(shipment).data)

    @action(detail=True, methods=["post"], url_path="load")
    def load(self, request, pk=None):
        serializer = LoadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        shipment = record_count(
            self.get_object(),
            serializer.validated_data["bags"],
            request.user,
        )
        return Response(ShipmentSerializer(shipment).data)

    @action(detail=True, methods=["post"], url_path="finish-loading")
    def finish_loading(self, request, pk=None):
        shipment = finish_loading(self.get_object(), request.user)
        return Response(ShipmentSerializer(shipment).data)

    @action(detail=True, methods=["post"], url_path="ship")
    def ship(self, request, pk=None):
        shipment = record_shipment(self.get_object(), request.user)
        return Response(ShipmentSerializer(shipment).data)
