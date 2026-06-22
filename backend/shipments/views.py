from decimal import Decimal
from rest_framework.views import APIView
from rest_framework.response import Response
from rbac.permissions import HasPerm
from orders.models import Order
from .services import record_arrival, record_count, finish_loading, record_shipment
from .serializers import ShipmentSerializer


class _CanArrive(HasPerm):
    def __init__(self): super().__init__("shipping.arrive")


class _CanLoad(HasPerm):
    def __init__(self): super().__init__("shipping.load")


class _CanShip(HasPerm):
    def __init__(self): super().__init__("shipping.ship")


class _Base(APIView):
    def get_order(self, pk):
        return (Order.objects.select_related("shipment")
                .prefetch_related("items__product").get(pk=pk))


class ArriveView(_Base):
    permission_classes = [_CanArrive]

    def post(self, request, pk):
        order = self.get_order(pk)
        s = record_arrival(order, Decimal(str(request.data["weigh_in_kg"])),
                           request.user,
                           debt_override=bool(request.data.get("debt_override", False)))
        return Response(ShipmentSerializer(s).data)


class LoadView(_Base):
    permission_classes = [_CanLoad]

    def post(self, request, pk):
        s = record_count(self.get_order(pk), int(request.data["bags"]), request.user)
        return Response(ShipmentSerializer(s).data)


class FinishLoadingView(_Base):
    permission_classes = [_CanLoad]

    def post(self, request, pk):
        s = finish_loading(self.get_order(pk), request.user)
        return Response(ShipmentSerializer(s).data)


class ShipView(_Base):
    permission_classes = [_CanShip]

    def post(self, request, pk):
        s = record_shipment(self.get_order(pk), Decimal(str(request.data["weigh_out_kg"])), request.user)
        return Response(ShipmentSerializer(s).data)
