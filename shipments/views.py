from decimal import Decimal
from rest_framework.views import APIView
from rest_framework.response import Response
from accounts.permissions import IsOperatorOrBoss
from orders.models import Order
from .services import record_arrival, record_loading, record_shipment
from .serializers import ShipmentSerializer


class _OrderActionView(APIView):
    permission_classes = [IsOperatorOrBoss]

    def get_order(self, pk):
        return (
            Order.objects.select_related("shipment")
            .prefetch_related("items__product")
            .get(pk=pk)
        )


class ArriveView(_OrderActionView):
    def post(self, request, pk):
        order = self.get_order(pk)
        shipment = record_arrival(
            order, request.data.get("truck_number", ""),
            Decimal(str(request.data["weigh_in_kg"])), request.user,
            debt_override=bool(request.data.get("debt_override", False)),
        )
        return Response(ShipmentSerializer(shipment).data)


class LoadView(_OrderActionView):
    def post(self, request, pk):
        order = self.get_order(pk)
        shipment = record_loading(order, int(request.data["bags"]), request.user)
        return Response(ShipmentSerializer(shipment).data)


class ShipView(_OrderActionView):
    def post(self, request, pk):
        order = self.get_order(pk)
        shipment = record_shipment(
            order, Decimal(str(request.data["weigh_out_kg"])), request.user
        )
        return Response(ShipmentSerializer(shipment).data)
