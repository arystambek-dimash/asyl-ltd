from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from apps.rbac.permissions import PermViewSetMixin
from apps.shipments.services import (
    start_train_loading, record_count, finish_train_loading)
from .models import Order, Payment, StatusChangeRequest
from .serializers import OrderSerializer, PaymentSerializer, StatusChangeRequestSerializer
from .services import (add_payment, pay_via_bank, confirm_order, reject_order,
                       confirm_payment, reject_payment, approve_debt,
                       request_status_change, approve_status_change, reject_status_change)


class OrderViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Order.objects.select_related("client").prefetch_related("items__product")
    serializer_class = OrderSerializer
    required_perms = {
        "list": "orders.view", "retrieve": "orders.view",
        "create": "orders.create", "update": "orders.edit",
        "partial_update": "orders.edit", "destroy": "orders.edit",
        "payments": "payments.create", "confirm": "orders.confirm",
        "pay_bank": "payments.create",
        "debts": "orders.view",
        "set_status": "orders.view",
        "status_requests": "orders.view",
        "approve_status": "orders.edit",
        "reject_status": "orders.edit",
        "reject": "orders.confirm",
        "confirm_payment": "payments.confirm",
        "reject_payment": "payments.confirm",
        "approve_debt": "shipping.debt_override",
        "train_queue": "train.view",
        "train": "train.load",
    }

    @action(detail=False, methods=["get"], url_path="train/queue")
    def train_queue(self, request):
        """Очередь поездов для загрузчика: подтверждённые и идущие на загрузке."""
        qs = (self.get_queryset()
              .filter(transport_type="train", status__in=["confirmed", "loading"])
              .order_by("created_at"))
        return Response(OrderSerializer(qs, many=True, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="train")
    def train(self, request, pk=None):
        """Единый эндпоинт загрузки поезда: action = start | count | finish."""
        order = self.get_object()
        what = request.data.get("action")
        if what == "start":
            start_train_loading(order, request.user)
        elif what == "count":
            record_count(order, int(request.data.get("bags") or 0), request.user)
        elif what == "finish":
            finish_train_loading(order, request.user)
        else:
            raise ValidationError({"detail": "Неизвестное действие", "code": "bad_action"})
        return Response(OrderSerializer(order, context={"request": request}).data)

    @action(detail=False, methods=["get"], url_path="debts")
    def debts(self, request):
        """Все отгруженные заказы «в долг» с непогашенным остатком."""
        qs = (self.get_queryset().select_related("store").prefetch_related("payments"))
        orders = [o for o in qs if o.is_debt]
        data = OrderSerializer(orders, many=True, context={"request": request}).data
        return Response(data)

    @action(detail=True, methods=["post"], url_path="payments")
    def payments(self, request, pk=None):
        order = self.get_object()
        payment = add_payment(order, request.data.get("amount"), request.user)
        return Response(PaymentSerializer(payment).data, status=201)

    @action(detail=True, methods=["post"], url_path="pay-bank")
    def pay_bank(self, request, pk=None):
        payment = pay_via_bank(self.get_object(), request.user)
        return Response(PaymentSerializer(payment).data, status=201)

    @action(detail=True, methods=["post"], url_path="confirm")
    def confirm(self, request, pk=None):
        order = confirm_order(self.get_object(), request.user,
                              prices=request.data.get("prices"))
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
        """Ручная смена статуса. orders.edit — сразу; иначе запрос на одобрение."""
        order = self.get_object()
        result = request_status_change(order, request.data.get("status"), request.user)
        order.refresh_from_db()
        return Response({
            "applied": result["applied"],
            "order": OrderSerializer(order, context={"request": request}).data,
            "request": (StatusChangeRequestSerializer(result["request"]).data
                        if result["request"] else None),
        }, status=200 if result["applied"] else 202)

    @action(detail=True, methods=["get"], url_path="status-requests")
    def status_requests(self, request, pk=None):
        qs = self.get_object().status_requests.filter(status="pending")
        return Response(StatusChangeRequestSerializer(qs, many=True).data)

    @action(detail=True, methods=["post"],
            url_path="status-requests/(?P<rid>[^/.]+)/approve")
    def approve_status(self, request, pk=None, rid=None):
        req = StatusChangeRequest.objects.get(pk=rid, order=self.get_object())
        approve_status_change(req, request.user)
        return Response(StatusChangeRequestSerializer(req).data)

    @action(detail=True, methods=["post"],
            url_path="status-requests/(?P<rid>[^/.]+)/reject")
    def reject_status(self, request, pk=None, rid=None):
        req = StatusChangeRequest.objects.get(pk=rid, order=self.get_object())
        reject_status_change(req, request.user)
        return Response(StatusChangeRequestSerializer(req).data)
