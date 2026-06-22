from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rbac.permissions import PermViewSetMixin
from .models import Order
from .serializers import OrderSerializer, PaymentSerializer
from .services import add_payment, confirm_order


class OrderViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Order.objects.select_related("client").prefetch_related("items__product")
    serializer_class = OrderSerializer
    required_perms = {
        "list": "orders.view", "retrieve": "orders.view",
        "create": "orders.create", "update": "orders.edit",
        "partial_update": "orders.edit", "destroy": "orders.edit",
        "payments": "payments.create", "confirm": "orders.confirm",
    }

    @action(detail=True, methods=["post"], url_path="payments")
    def payments(self, request, pk=None):
        order = self.get_object()
        payment = add_payment(order, request.data.get("amount"), request.user)
        return Response(PaymentSerializer(payment).data, status=201)

    @action(detail=True, methods=["post"], url_path="confirm")
    def confirm(self, request, pk=None):
        order = confirm_order(self.get_object(), request.user)
        return Response(OrderSerializer(order, context={"request": request}).data)
