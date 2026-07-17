from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from decimal import Decimal
from apps.common.permissions import HasPerm, PermViewSetMixin
from apps.common.money import money_string
from apps.common.query_params import parse_iso_date, parse_store_id, validate_date_range
from apps.clients.models import Department
from apps.shipments.services import (
    start_train_loading, record_count, finish_train_loading)
from apps.shipments.serializers import LoadSerializer
from .models import Order, Payment, StatusChangeRequest
from .querysets import with_order_api_relations
from .reports import summary_report
from .statuses import PUBLIC_STATUS_LABELS, statuses_in_group
from .serializers import (OrderSerializer, PaymentSerializer, PaymentQueueSerializer,
                          StatusChangeRequestSerializer)
from .services import (add_payment, confirm_order, reject_order,
                       receive_payment, accountant_confirm_payment,
                       reject_payment, soft_delete_order, restore_order,
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
        qs = Order.objects.all()
        department = request.query_params.get("department")
        if department:
            qs = qs.filter(department=department)
        store = parse_store_id(request.query_params.get("store"))
        if store:
            qs = qs.filter(store_id=store)
        return Response(summary_report(qs, date_from, date_to))


class OrderViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    # Всё, что сериализатор трогает на каждой строке, загружаем заранее —
    # список заказов не должен порождать запросы «на заказ» (N+1).
    queryset = with_order_api_relations(Order.objects.all())
    serializer_class = OrderSerializer
    required_perms = {
        "list": "orders.view",
        "retrieve": "orders.view",
        "create": "orders.create",
        "update": "orders.edit",
        "partial_update": "orders.edit", "destroy": "orders.edit",
        "trash": "orders.edit", "restore": "orders.edit",
        "purge": "orders.edit",
        "payments": "payments.create", "confirm": "orders.confirm",
        "set_status": "orders.view",
        "status_requests": "orders.view",
        "approve_status": "orders.edit",
        "reject_status": "orders.edit",
        "reject": "orders.confirm",
        "receive_payment": "payments.create",
        "confirm_payment": "payments.confirm",
        "reject_payment": "payments.confirm",
        "payments_queue": "payments.confirm",
        "train": "train.load",
        "loading_camera": "shipping.load",
        "department_summary": "orders.view",
    }

    def get_queryset(self):
        qs = super().get_queryset()
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
            store = parse_store_id(params.get("store"))
            if store:
                qs = qs.filter(store_id=store)
        return qs

    @action(detail=False, methods=["get"], url_path="department-summary")
    def department_summary(self, request):
        """Оперативная аналитика заказов в разрезе динамических отделов."""
        params = request.query_params
        # Нужны только статус/отдел/сумма — полный план загрузки здесь лишний.
        qs = Order.objects.prefetch_related("items__product")
        date_from = parse_iso_date(params.get("date_from"))
        date_to = parse_iso_date(params.get("date_to"))
        validate_date_range(date_from, date_to)
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        group = params.get("status_group")
        if group:
            if group not in PUBLIC_STATUS_LABELS:
                raise ValidationError({"detail": "Неизвестная группа статусов",
                                       "code": "bad_status_group"})
            qs = qs.filter(status__in=statuses_in_group(group))

        rows = {department.code: {
            "id": department.id,
            "code": department.code,
            "name": department.name,
            "color": department.color,
            "is_active": department.is_active,
            "orders": 0,
            "active": 0,
            "shipped": 0,
            "revenue": Decimal("0"),
        } for department in Department.objects.all()}
        for order in qs:
            row = rows.get(order.department)
            if row is None:
                continue
            row["orders"] += 1
            if order.status == "shipped":
                row["shipped"] += 1
            elif order.status not in ("rejected", "cancelled"):
                row["active"] += 1
            if order.status not in ("rejected", "cancelled"):
                row["revenue"] += order.total_amount
        return Response([
            {**row, "revenue": money_string(row["revenue"])}
            for row in rows.values()
            if row["is_active"] or row["orders"]
        ])

    @action(detail=False, methods=["get"], url_path="payments-queue")
    def payments_queue(self, request):
        """Очередь ручной обработки кассиром (requested и received)."""
        stage = request.query_params.get("stage")
        stages = [stage] if stage in Payment.STATUSES else Payment.IN_PROGRESS_STATUSES
        qs = (Payment.objects
              .filter(status__in=stages,
                      order__in=Order.objects.all())
              .select_related("order__client", "order__store", "recorded_by", "received_by")
              .order_by("paid_at"))
        department = request.query_params.get("department")
        if department:
            qs = qs.filter(order__department=department)
        store = parse_store_id(request.query_params.get("store"))
        if store:
            qs = qs.filter(order__store_id=store)
        date_from = parse_iso_date(request.query_params.get("date_from"))
        date_to = parse_iso_date(request.query_params.get("date_to"))
        validate_date_range(date_from, date_to)
        if date_from:
            qs = qs.filter(paid_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(paid_at__date__lte=date_to)
        return Response(PaymentQueueSerializer(qs, many=True).data)

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
        from apps.cameras.models import MonoblockCameraSettings
        order = self.get_object()
        camera = (request.data.get("camera") or "").strip()
        if camera:
            try:
                camera = ai.normalize(camera)  # переиспользуем валидатор имени камеры
            except ai.AiError:
                raise ValidationError({"detail": "Неизвестная камера", "code": "bad_camera"})
            if camera not in MonoblockCameraSettings.allowed_sources():
                raise ValidationError({
                    "detail": "Эта камера не разрешена администратором для Моноблока",
                    "code": "camera_not_allowed",
                })
        order.loading_camera = camera
        order.save(update_fields=["loading_camera"])
        return Response(OrderSerializer(order, context={"request": request}).data)

    def destroy(self, request, *args, **kwargs):
        """Удаление = отправка в корзину (soft-delete). Заказ исчезает из отчётов
        и списков, но сохраняется и может быть восстановлен."""
        soft_delete_order(self.get_object(), request.user)
        from rest_framework import status
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _deleted_scoped(self):
        """Удалённые заказы (корзина), доступные редактору заказов."""
        return with_order_api_relations(Order.all_objects.deleted())

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
