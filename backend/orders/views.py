from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from accounts.permissions import IsStaff, IsManager, IsAccountant
from .models import Order
from .serializers import OrderSerializer, PaymentSerializer
from .services import add_payment, confirm_order


class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.select_related("client").prefetch_related("items__product")
    serializer_class = OrderSerializer

    def get_permissions(self):
        # Detail actions (payments, confirm) declare their own permission_classes
        # via @action; honor those instead of the default CRUD gating below.
        if getattr(self, "action", None) in ("payments", "confirm"):
            return super().get_permissions()
        if self.action in ("list", "retrieve"):
            return [IsStaff()]
        return [IsManager()]

    @action(detail=True, methods=["post"], permission_classes=[IsAccountant],
            url_path="payments")
    def payments(self, request, pk=None):
        order = self.get_object()
        amount = request.data.get("amount")
        payment = add_payment(order, amount, request.user)
        return Response(PaymentSerializer(payment).data, status=201)

    @action(detail=True, methods=["post"], permission_classes=[IsManager],
            url_path="confirm")
    def confirm(self, request, pk=None):
        order = confirm_order(self.get_object(), request.user)
        return Response(OrderSerializer(order, context={"request": request}).data)
