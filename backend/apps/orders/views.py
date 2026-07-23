from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.http import FileResponse
from io import BytesIO
from datetime import timedelta
from decimal import Decimal
from django.db.models import Q, Sum
from django.utils import timezone
from apps.common.permissions import HasPerm, PermViewSetMixin
from apps.common.money import money_string
from apps.common.query_params import parse_iso_date, parse_store_id, validate_date_range
from apps.clients.models import Department
from apps.eventlog.models import EventLog
from apps.shipments.services import (
    finish_train_loading, record_count, set_loading_camera, start_train_loading)
from apps.shipments.serializers import LoadSerializer
from .models import ApiPayInvoice, Order, Payment, StatusChangeRequest
from .apipay import ApiPayAPIError, cancel_invoice, create_invoice, create_refund
from .invoices import build_payment_receipt_pdf
from .querysets import with_order_api_relations
from .reports import summary_report
from .statuses import PUBLIC_STATUS_LABELS, statuses_in_group
from .serializers import (OrderSerializer, PaymentSerializer, PaymentQueueSerializer,
                          StatusChangeRequestSerializer)
from .services import (add_payment, add_mixed_payments, confirm_order, reject_order,
                       receive_payment, accountant_confirm_payment,
                       reopen_confirmed_payment, reject_payment, soft_delete_order, restore_order,
                       purge_order,
                       repeat_order,
                       request_status_change, approve_status_change, reject_status_change)
from apps.shipments.services import rollback_shipment

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


class PaymentTransactionListView(APIView):
    def get_permissions(self):
        return [HasPerm("payments.view")]

    def get(self, request):
        qs = (
            Payment.objects.select_related(
                "order__client", "recorded_by", "received_by", "confirmed_by",
                "apipay_invoice",
            )
            .prefetch_related("apipay_invoice__refunds")
            .order_by("-paid_at")
        )
        status = request.query_params.get("status")
        method = request.query_params.get("method")
        search = request.query_params.get("search")
        if status:
            qs = qs.filter(status=status)
        if method:
            qs = qs.filter(method=method)
        if search:
            search_query = (
                Q(order__client__first_name__icontains=search)
                | Q(order__client__last_name__icontains=search)
                | Q(order__client__company_name__icontains=search)
                | Q(order__client__phone__icontains=search)
            )
            if search.isdigit():
                search_query |= Q(order_id=int(search)) | Q(id=int(search))
            qs = qs.filter(search_query)
        try:
            page = max(1, int(request.query_params.get("page") or 1))
            page_size = min(
                100, max(10, int(request.query_params.get("page_size") or 50))
            )
        except ValueError as exc:
            raise ValidationError({
                "detail": "Некорректный номер страницы.",
                "code": "invalid_page",
            }) from exc
        count = qs.count()
        pages = max(1, (count + page_size - 1) // page_size)
        if page > pages:
            page = pages
        start = (page - 1) * page_size
        rows = qs[start:start + page_size]
        paid_by_currency = {
            currency: money_string(
                qs.filter(status="confirmed", order__currency=currency)
                .aggregate(total=Sum("amount"))["total"] or Decimal("0")
            )
            for currency in ("KZT", "USD")
        }
        refunded_kzt = (
            ApiPayInvoice.objects.filter(
                payment__in=qs, total_refunded__gt=0
            ).aggregate(total=Sum("total_refunded"))["total"]
            or Decimal("0")
        )
        return Response({
            "results": PaymentSerializer(rows, many=True).data,
            "count": count,
            "page": page,
            "pages": pages,
            "summary": {
                "paid_by_currency": paid_by_currency,
                "refunded_kzt": money_string(refunded_kzt),
            },
        })


class PaymentReceiptView(APIView):
    def get_permissions(self):
        return [HasPerm("payments.view")]

    def get(self, request, payment_id):
        payment = get_object_or_404(Payment, pk=payment_id)
        if payment.status != "confirmed":
            raise ValidationError({
                "detail": "Квитанция доступна только после подтверждения оплаты.",
                "code": "receipt_not_available",
            })
        pdf = build_payment_receipt_pdf(payment)
        return FileResponse(
            BytesIO(pdf), content_type="application/pdf", as_attachment=True,
            filename=f"receipt_{payment.id}.pdf",
        )


class PaymentRefundView(APIView):
    def get_permissions(self):
        return [HasPerm("payments.confirm")]

    def post(self, request, payment_id):
        invoice = get_object_or_404(
            ApiPayInvoice.objects.select_related("payment"), payment_id=payment_id
        )
        try:
            refund = create_refund(
                invoice, request.user, amount=request.data.get("amount"),
                reason=request.data.get("reason") or "",
            )
        except ApiPayAPIError as exc:
            raise ValidationError({
                "detail": exc.message, "code": exc.error_code
            }) from exc
        return Response({
            "id": refund.refund_id,
            "amount": money_string(refund.amount),
            "status": refund.status,
        }, status=201)


class PaymentKaspiQrView(APIView):
    def get_permissions(self):
        return [HasPerm("payments.create")]

    def post(self, request, payment_id):
        payment = get_object_or_404(
            Payment.objects.select_related("order__client"), pk=payment_id,
            method="kaspi",
        )
        try:
            invoice = create_invoice(payment, channel="qr")
        except ApiPayAPIError as exc:
            raise ValidationError({
                "detail": exc.message, "code": exc.error_code
            }) from exc
        return Response(PaymentSerializer(payment).data, status=201)


class PaymentRejectView(APIView):
    def get_permissions(self):
        return [HasPerm("payments.confirm")]

    def post(self, request, payment_id):
        payment = get_object_or_404(
            Payment.objects.select_related("order", "apipay_invoice"),
            pk=payment_id,
        )
        if payment.status not in Payment.IN_PROGRESS_STATUSES:
            raise ValidationError({
                "detail": "Отклонить можно только ожидающий платёж.",
                "code": "invalid_payment_stage",
            })
        reason = str(request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError({
                "detail": "Укажите причину отклонения.",
                "code": "rejection_reason_required",
            })
        try:
            invoice = payment.apipay_invoice
        except ApiPayInvoice.DoesNotExist:
            invoice = None
        if invoice and invoice.status not in ("cancelled", "expired", "error"):
            try:
                cancel_invoice(invoice)
            except ApiPayAPIError as exc:
                raise ValidationError({
                    "detail": exc.message, "code": exc.error_code
                }) from exc
            if invoice.status == "cancelling":
                payment.note = (
                    f"{payment.note}\n" if payment.note else ""
                ) + f"Запрошена отмена: {reason}"
                payment.save(update_fields=["note"])
                return Response(PaymentSerializer(payment).data, status=202)
        payment.note = (
            f"{payment.note}\n" if payment.note else ""
        ) + f"Отклонено: {reason}"
        payment.save(update_fields=["note"])
        reject_payment(payment, request.user)
        payment.refresh_from_db()
        return Response(PaymentSerializer(payment).data)


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
        "rollback_shipment": "shipping.rollback",
        "status_requests": "orders.view",
        "approve_status": "orders.edit",
        "reject_status": "orders.edit",
        "reject": "orders.confirm",
        "receive_payment": "payments.create",
        "confirm_payment": "payments.confirm",
        "reopen_payment": "payments.confirm",
        "reject_payment": "payments.confirm",
        "payments_queue": "payments.confirm",
        "cashier_log": "payments.confirm",
        "train": "train.load",
        "loading_camera": "shipping.load",
        "department_summary": "orders.view",
        "repeat": "orders.create",
    }

    def get_queryset(self):
        qs = super().get_queryset()
        device = getattr(self.request.user, "active_monoblock_device", None)
        if device is not None:
            # Устройство видит только очередь старта и собственную текущую
            # погрузку; остальная CRM-история ему не раскрывается.
            qs = qs.filter(
                Q(status="confirmed")
                | Q(loading_camera=device.camera_source,
                    status__in=("arrived", "loading", "loaded"))
            )
        if self.action == "list":
            params = self.request.query_params
            if params.get("post_board") == "1":
                # Живой пост не должен тянуть всю историю заказов. Политика
                # хранения завершённых управляется админом, клиент её не
                # переопределяет query-параметром.
                from apps.cameras.models import MonoblockCameraSettings
                row = MonoblockCameraSettings.objects.filter(singleton=True).only(
                    "completed_orders_days"
                ).first()
                days = row.completed_orders_days if row else 1
                since = timezone.localdate() - timedelta(days=max(0, days - 1))
                qs = qs.filter(
                    Q(status__in=("confirmed", "arrived", "loading", "loaded"))
                    | Q(status="shipped", shipment__shipped_at__date__gte=since)
                )
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

    @action(detail=True, methods=["post"], url_path="repeat")
    def repeat(self, request, pk=None):
        order = repeat_order(self.get_object(), request.user)
        order = with_order_api_relations(Order.objects.all()).get(pk=order.pk)
        return Response(
            OrderSerializer(order, context={"request": request}).data,
            status=201,
        )

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

    @action(detail=False, methods=["get"], url_path="cashier-log")
    def cashier_log(self, request):
        """Неизменяемый журнал действий с оплатами для экрана кассы."""
        qs = (EventLog.objects
              .filter(event_type="payment", order__in=Order.objects.all())
              .select_related("user", "order__client", "order__store"))
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
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        events = list(qs[:200])
        payment_ids = {
            event.payload.get("payment_id") for event in events
            if event.payload.get("payment_id") is not None
        }
        current_statuses = dict(Payment.objects.filter(pk__in=payment_ids)
                                .values_list("pk", "status"))
        # После цикла confirm → reopen → confirm в журнале несколько событий
        # confirmed. Кнопка отката должна быть только у самого свежего.
        latest_confirmation: dict[int, int] = {}
        for event in events:
            payment_id = event.payload.get("payment_id")
            if (payment_id is not None
                    and event.payload.get("payment_stage") == "confirmed"
                    and payment_id not in latest_confirmation):
                latest_confirmation[payment_id] = event.id
        return Response([{
            "id": event.id,
            "message": event.message,
            "user_name": event.user.username if event.user else None,
            "order": event.order_id,
            "client_name": event.order.client.name if event.order_id else None,
            "store_name": (event.order.store.name
                           if event.order_id and event.order.store_id else None),
            "payload": event.payload,
            "created_at": event.created_at,
            "can_reopen": (
                event.payload.get("payment_stage") == "confirmed"
                and current_statuses.get(event.payload.get("payment_id")) == "confirmed"
                and latest_confirmation.get(event.payload.get("payment_id")) == event.id
            ),
        } for event in events])

    @action(detail=True, methods=["post"], url_path="train")
    def train(self, request, pk=None):
        """Единый эндпоинт загрузки вагона: action = start | count | finish."""
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
            device = getattr(request.user, "active_monoblock_device", None)
            if device is not None and device.camera_source != camera:
                raise PermissionDenied("Эта камера закреплена за другим моноблоком")
            if camera not in MonoblockCameraSettings.allowed_sources():
                raise ValidationError({
                    "detail": "Эта камера не разрешена администратором для Моноблока",
                    "code": "camera_not_allowed",
                })
            if order.status not in ("arrived", "loading"):
                raise ValidationError({
                    "detail": "Предварительное назначение камеры недоступно: начните заказ через Моноблок",
                    "code": "invalid_status",
                })
        order = set_loading_camera(order, camera, request.user)
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
        order = restore_order(order, request.user)
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
        parts = request.data.get("parts")
        if parts is not None:
            payments = add_mixed_payments(
                order, parts, request.user, note=request.data.get("note") or "")
            return Response(PaymentSerializer(payments, many=True).data, status=201)
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

    @action(detail=True, methods=["post"],
            url_path=r"payments/(?P<pid>\d+)/reopen")
    def reopen_payment(self, request, pk=None, pid=None):
        """Отмена случайного подтверждения: confirmed → received."""
        payment = get_object_or_404(Payment, pk=pid, order=self.get_object())
        payment = reopen_confirmed_payment(payment, request.user)
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
        bags_loaded = request.data.get("bags_loaded")
        if bags_loaded is not None:
            serializer = LoadSerializer(data={"bags": bags_loaded})
            serializer.is_valid(raise_exception=True)
            bags_loaded = serializer.validated_data["bags"]
        result = request_status_change(
            order,
            request.data.get("status"),
            request.user,
            bags_loaded=bags_loaded,
        )
        order.refresh_from_db()
        return Response({
            "applied": result["applied"],
            "order": OrderSerializer(order, context={"request": request}).data,
            "request": (StatusChangeRequestSerializer(result["request"]).data
                        if result["request"] else None),
        }, status=200 if result["applied"] else 202)

    @action(detail=True, methods=["post"], url_path="rollback-shipment")
    def rollback_shipment(self, request, pk=None):
        order = rollback_shipment(
            self.get_object(),
            request.user,
            target_status=request.data.get("status") or "confirmed",
            reason=request.data.get("reason") or "",
        )
        order.refresh_from_db()
        return Response({
            "order": OrderSerializer(order, context={"request": request}).data,
        })

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
