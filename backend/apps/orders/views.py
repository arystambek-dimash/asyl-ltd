from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from apps.rbac.permissions import PermViewSetMixin
from apps.eventlog.services import log_event
from .models import Order, Payment
from .serializers import OrderSerializer, PaymentSerializer
from .services import (add_payment, confirm_order, reject_order,
                       confirm_payment, reject_payment, approve_debt)


class OrderViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Order.objects.select_related("client").prefetch_related("items__product")
    serializer_class = OrderSerializer
    required_perms = {
        "list": "orders.view", "retrieve": "orders.view",
        "create": "orders.create", "update": "orders.edit",
        "partial_update": "orders.edit", "destroy": "orders.edit",
        "payments": "payments.create", "confirm": "orders.confirm",
        "set_status": "orders.edit",
        "reject": "orders.confirm",
        "confirm_payment": "payments.confirm",
        "reject_payment": "payments.confirm",
        "approve_debt": "shipping.debt_override",
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

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        order = reject_order(self.get_object(), request.user)
        return Response(OrderSerializer(order, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="payments/(?P<pid>[^/.]+)/confirm")
    def confirm_payment(self, request, pk=None, pid=None):
        payment = Payment.objects.get(pk=pid, order=self.get_object())
        confirm_payment(payment, request.user)
        return Response(OrderSerializer(payment.order, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="payments/(?P<pid>[^/.]+)/reject")
    def reject_payment(self, request, pk=None, pid=None):
        payment = Payment.objects.get(pk=pid, order=self.get_object())
        reject_payment(payment, request.user)
        return Response(OrderSerializer(payment.order, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="approve-debt")
    def approve_debt(self, request, pk=None):
        order = approve_debt(self.get_object(), request.user)
        return Response(OrderSerializer(order, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="set-status")
    def set_status(self, request, pk=None):
        """Ручная смена статуса (право orders.edit) — для исправления ошибок."""
        order = self.get_object()
        new = request.data.get("status")
        if new not in Order.STATUSES:
            raise ValidationError({"detail": "Неизвестный статус", "code": "bad_status"})
        old = order.status
        order.status = new
        order.save(update_fields=["status"])
        log_event("status_override",
                  f"Статус заказа изменён вручную: {old} → {new}",
                  user=request.user, order=order,
                  payload={"from": old, "to": new})
        return Response(OrderSerializer(order, context={"request": request}).data)
