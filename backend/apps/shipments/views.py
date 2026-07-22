from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.orders.models import Order
from apps.common.permissions import PermViewSetMixin
from .serializers import ArrivalSerializer, LoadSerializer, ShipmentSerializer
from .services import (
    finish_loading, record_arrival, record_count, record_shipment, rewind_loading,
)


class ShipmentViewSet(PermViewSetMixin, viewsets.GenericViewSet):
    queryset = Order.objects.select_related("shipment").prefetch_related("items__product")
    required_perms = {
        "arrive": "shipping.arrive",
        "load": "shipping.load",
        "finish_loading": "shipping.load",
        "ship": "shipping.ship",
        # Право управления погрузкой даёт полный контроль живого поста:
        # завершение и безопасный возврат в ожидание.
        "rewind_loading": "shipping.load",
    }

    def get_queryset(self):
        qs = super().get_queryset()
        device = getattr(self.request.user, "active_monoblock_device", None)
        if device is not None:
            # A physical post may mutate only its own live loading workflow.
            qs = qs.filter(
                loading_camera=device.camera_source,
                status__in=("arrived", "loading", "loaded"),
            )
        return qs

    @action(detail=True, methods=["post"], url_path="arrive")
    def arrive(self, request, pk=None):
        serializer = ArrivalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        shipment = record_arrival(
            self.get_object(),
            serializer.validated_data.get("weigh_in_kg"),  # None → расчётный вес
            request.user,
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

    @action(detail=True, methods=["post"], url_path="rewind-loading")
    def rewind_loading(self, request, pk=None):
        order = rewind_loading(self.get_object(), request.user)
        # Клиенту достаточно нового статуса; список доски сразу перечитывается.
        return Response({"id": order.pk, "status": order.status})

    @action(detail=True, methods=["post"], url_path="ship")
    def ship(self, request, pk=None):
        shipment = record_shipment(self.get_object(), request.user)
        return Response(ShipmentSerializer(shipment).data)
