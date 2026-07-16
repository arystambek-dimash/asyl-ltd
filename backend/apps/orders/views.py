from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from apps.common.permissions import HasPerm, PermViewSetMixin
from apps.common.query_params import parse_iso_date, validate_date_range
from apps.rbac.scoping import scope_by_department
from apps.shipments.services import (
    start_train_loading, record_count, finish_train_loading)
from apps.shipments.serializers import LoadSerializer
from .models import Order, Payment, StatusChangeRequest
from .querysets import with_order_api_relations
from .reports import summary_report
from .statuses import PUBLIC_STATUS_LABELS, statuses_in_group
from .serializers import (OrderSerializer, PaymentSerializer, PaymentQueueSerializer,
                          StatusChangeRequestSerializer)
from .services import (add_payment, pay_via_bank, confirm_order, reject_order,
                       receive_payment, accountant_confirm_payment,
                       reject_payment, approve_debt, soft_delete_order, restore_order,
                       purge_order,
                       request_status_change, approve_status_change, reject_status_change)

class ReportSummaryView(APIView):
    """Сводный отчёт за период: касса (нал/безнал), отгрузки, долги, кассиры."""

    def get_permissions(self):
        return [HasPerm("reports.view")]

    def get(self, request):
        date_from = parse_iso_date(request.query_params.get("from"))
        date_to = parse_iso_date(request.query_params.get("to"))
        validate_date_range(date_from, date_to)
        qs = scope_by_department(
            Order.objects.all(), request.user, "reports.view",
            owner_field="client__manager")
        department = request.query_params.get("department")
        if department:
            qs = qs.filter(department=department)
        store = request.query_params.get("store")
        if store:
            if not store.isdigit():
                raise ValidationError(
                    {"detail": "Некорректный магазин", "code": "bad_store"})
            qs = qs.filter(store_id=store)
        return Response(summary_report(qs, date_from, date_to))


class OrderViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    # Всё, что сериализатор трогает на каждой строке, загружаем заранее —
    # список заказов не должен порождать запросы «на заказ» (N+1).
    queryset = with_order_api_relations(Order.objects.all())
    serializer_class = OrderSerializer
    required_perms = {
        "list": ("orders.view", "dept2.view"),
        "retrieve": ("orders.view", "dept2.view"),
        "create": ("orders.create", "dept2.create"),
        "update": "orders.edit",
        "partial_update": "orders.edit", "destroy": "orders.edit",
        "trash": "orders.edit", "restore": "orders.edit",
        "purge": "orders.edit",
        "payments": "payments.create", "confirm": "orders.confirm",
        "pay_bank": "payments.create",
        "debts": "reports.view",
        "set_status": "orders.view",
        "status_requests": "orders.view",
        "approve_status": "orders.edit",
        "reject_status": "orders.edit",
        "reject": "orders.confirm",
        "receive_payment": "payments.create",
        "confirm_payment": "payments.confirm",
        "reject_payment": "payments.confirm",
        "payments_queue": "payments.confirm",
        "approve_debt": "shipping.debt_override",
        "train_queue": "train.view",
        "train": "train.load",
        "loading_camera": "shipping.load",
    }

    def get_queryset(self):
        required = self.required_perms.get(self.action, "orders.view")
        scope_perm = required if isinstance(required, str) else "orders.view"
        qs = scope_by_department(
            super().get_queryset(), self.request.user, scope_perm,
            owner_field="client__manager")
        if self.action == "list":
            params = self.request.query_params
            for field in ("department", "status", "payment_status"):
                value = params.get(field)
                if value:
                    qs = qs.filter(**{field: value})
            # Фильтр по публичной группе: «Ожидает загрузки» покрывает
            # confirmed/arrived/loading — точечный status для этого не годится.
            group = params.get("status_group")
            if group:
                if group not in PUBLIC_STATUS_LABELS:
                    raise ValidationError(
                        {"detail": "Неизвестная группа статусов",
                         "code": "bad_status_group"})
                qs = qs.filter(status__in=statuses_in_group(group))
            date_from = parse_iso_date(params.get("date_from"))
            date_to = parse_iso_date(params.get("date_to"))
            validate_date_range(date_from, date_to)
            if date_from:
                qs = qs.filter(created_at__date__gte=date_from)
            if date_to:
                qs = qs.filter(created_at__date__lte=date_to)
            store = params.get("store")
            if store:
                if not store.isdigit():
                    raise ValidationError(
                        {"detail": "Некорректный магазин", "code": "bad_store"})
                qs = qs.filter(store_id=store)
        return qs

    @action(detail=False, methods=["get"], url_path="payments-queue")
    def payments_queue(self, request):
        """Очередь ручной обработки кассиром (requested и received)."""
        stage = request.query_params.get("stage")
        stages = [stage] if stage in Payment.STATUSES else Payment.IN_PROGRESS_STATUSES
        qs = (Payment.objects
              .filter(status__in=stages,
                      order__in=scope_by_department(
                          Order.objects.all(), request.user, "payments.confirm",
                          owner_field="client__manager"))
              .select_related("order__client", "order__store", "recorded_by", "received_by")
              .order_by("paid_at"))
        department = request.query_params.get("department")
        if department:
            qs = qs.filter(order__department=department)
        store = request.query_params.get("store")
        if store:
            if not store.isdigit():
                raise ValidationError(
                    {"detail": "Некорректный магазин", "code": "bad_store"})
            qs = qs.filter(order__store_id=store)
        date_from = parse_iso_date(request.query_params.get("date_from"))
        date_to = parse_iso_date(request.query_params.get("date_to"))
        validate_date_range(date_from, date_to)
        if date_from:
            qs = qs.filter(paid_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(paid_at__date__lte=date_to)
        return Response(PaymentQueueSerializer(qs, many=True).data)

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
            serializer = LoadSerializer(data={"bags": request.data.get("bags")})
            serializer.is_valid(raise_exception=True)
            record_count(order, serializer.validated_data["bags"], request.user)
        elif what == "finish":
            finish_train_loading(order, request.user)
        else:
            raise ValidationError({"detail": "Неизвестное действие", "code": "bad_action"})
        # The action may create/update the select_related Shipment row. Clear
        # relation caches before serializing the new state.
        order.refresh_from_db()
        return Response(OrderSerializer(order, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="loading-camera")
    def loading_camera(self, request, pk=None):
        """Занять/освободить камеру под погрузку этого заказа. Пустая — освободить."""
        from apps.cameras import ai
        order = self.get_object()
        camera = (request.data.get("camera") or "").strip()
        if camera:
            try:
                camera = ai.normalize(camera)  # переиспользуем валидатор имени камеры
            except ai.AiError:
                raise ValidationError({"detail": "Неизвестная камера", "code": "bad_camera"})
        order.loading_camera = camera
        order.save(update_fields=["loading_camera"])
        return Response(OrderSerializer(order, context={"request": request}).data)

    @action(detail=False, methods=["get"], url_path="debts")
    def debts(self, request):
        """Все отгруженные заказы «в долг» с непогашенным остатком."""
        orders = [o for o in self.get_queryset() if o.is_debt]
        data = OrderSerializer(orders, many=True, context={"request": request}).data
        return Response(data)

    def destroy(self, request, *args, **kwargs):
        """Удаление = отправка в корзину (soft-delete). Заказ исчезает из отчётов
        и списков, но сохраняется и может быть восстановлен."""
        soft_delete_order(self.get_object(), request.user)
        from rest_framework import status
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _deleted_scoped(self):
        """Удалённые заказы (корзина) в рамках прав пользователя по отделу."""
        qs = with_order_api_relations(Order.all_objects.deleted())
        return scope_by_department(qs, self.request.user, "orders.edit",
                                   owner_field="client__manager")

    @action(detail=False, methods=["get"], url_path="trash")
    def trash(self, request):
        """Корзина: удалённые заказы, доступные для восстановления."""
        qs = self._deleted_scoped().order_by("-deleted_at")
        department = request.query_params.get("department")
        if department:
            qs = qs.filter(department=department)
        return Response(OrderSerializer(qs, many=True, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="restore")
    def restore(self, request, pk=None):
        """Восстановить заказ из корзины."""
        order = self._deleted_scoped().filter(pk=pk).first()
        if order is None:
            raise ValidationError({"detail": "Заказ не найден в корзине", "code": "not_found"})
        restore_order(order, request.user)
        return Response(OrderSerializer(order, context={"request": request}).data)

    @action(detail=True, methods=["delete"], url_path="purge")
    def purge(self, request, pk=None):
        """Удалить заказ из корзины навсегда (безвозвратно)."""
        order = self._deleted_scoped().filter(pk=pk).first()
        if order is None:
            raise ValidationError({"detail": "Заказ не найден в корзине", "code": "not_found"})
        purge_order(order, request.user)
        from rest_framework import status
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="payments")
    def payments(self, request, pk=None):
        """Начало цепочки: stage=requested (счёт выставлен) или received (деньги приняты)."""
        order = self.get_object()
        payment = add_payment(
            order, request.data.get("amount"), request.user,
            method=request.data.get("method") or "cash",
            stage=request.data.get("stage") or "received",
            note=request.data.get("note") or "")
        return Response(PaymentSerializer(payment).data, status=201)

    @action(detail=True, methods=["post"], url_path=r"payments/(?P<pid>\d+)/receive")
    def receive_payment(self, request, pk=None, pid=None):
        payment = get_object_or_404(Payment, pk=pid, order=self.get_object())
        receive_payment(payment, request.user)
        return Response(OrderSerializer(payment.order, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="pay-bank")
    def pay_bank(self, request, pk=None):
        payment = pay_via_bank(self.get_object(), request.user)
        return Response(PaymentSerializer(payment).data, status=201)

    @action(detail=True, methods=["post"], url_path="confirm")
    def confirm(self, request, pk=None):
        order = confirm_order(self.get_object(), request.user,
                              prices=request.data.get("prices"))
        # confirm_order updates item instances loaded inside the service; the
        # view's prefetched items still contain the old prices until refreshed.
        order.refresh_from_db()
        return Response(OrderSerializer(order, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        order = reject_order(self.get_object(), request.user)
        return Response(OrderSerializer(order, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path=r"payments/(?P<pid>\d+)/confirm")
    def confirm_payment(self, request, pk=None, pid=None):
        """Подтверждение бухгалтером-кассой: received → confirmed (деньги учтены)."""
        payment = get_object_or_404(Payment, pk=pid, order=self.get_object())
        accountant_confirm_payment(payment, request.user)
        return Response(OrderSerializer(payment.order, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path=r"payments/(?P<pid>\d+)/reject")
    def reject_payment(self, request, pk=None, pid=None):
        payment = get_object_or_404(Payment, pk=pid, order=self.get_object())
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
            url_path=r"status-requests/(?P<rid>\d+)/approve")
    def approve_status(self, request, pk=None, rid=None):
        req = get_object_or_404(
            StatusChangeRequest, pk=rid, order=self.get_object())
        approve_status_change(req, request.user)
        return Response(StatusChangeRequestSerializer(req).data)

    @action(detail=True, methods=["post"],
            url_path=r"status-requests/(?P<rid>\d+)/reject")
    def reject_status(self, request, pk=None, rid=None):
        req = get_object_or_404(
            StatusChangeRequest, pk=rid, order=self.get_object())
        reject_status_change(req, request.user)
        return Response(StatusChangeRequestSerializer(req).data)
