from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.http import FileResponse
from io import BytesIO
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
import re
from django.db.models import Q, Sum
from django.utils import timezone
from apps.common.permissions import HasPerm, PermViewSetMixin
from apps.common.money import money_string
from apps.common.query_params import (
    filter_date_range, parse_date_range, parse_store_id,
)
from apps.clients.models import Department
from apps.eventlog.models import EventLog
from apps.shipments.services import (
    finish_train_loading, record_count, set_loading_camera, start_train_loading)
from apps.shipments.serializers import LoadSerializer
from .models import ApiPayInvoice, Order, Payment, StatusChangeRequest
from .apipay import (
    ApiPayAPIError, ApiPayConfigurationError, cancel_invoice,
    create_cash_refund, create_invoice, create_refund,
    MONEY_RECEIVED_INVOICE_STATUSES, normalize_phone,
)
from .invoices import build_payment_receipt_pdf
from .debt import as_money_strings, primary_currency
from .querysets import with_order_api_relations, with_payment_api_relations
from .reports import summary_report
from .statuses import (
    PUBLIC_STATUS_LABELS, is_financial, is_in_progress, statuses_in_group,
)
from .serializers import (OrderSerializer, PaymentSerializer, PaymentQueueSerializer,
                          StatusChangeRequestSerializer)
from .services import (add_payment, add_mixed_payments, confirm_order, reject_order,
                       receive_payment, accountant_confirm_payment,
                       reopen_confirmed_payment, reject_payment,
                       restore_rejected_payment, soft_delete_order, restore_order,
                       purge_order,
                       repeat_order,
                       request_status_change, approve_status_change, reject_status_change)
from apps.shipments.services import rollback_shipment


PROVIDER_METHOD_CHANNELS = {"kaspi": "qr", "invoice": "phone"}
PROVIDER_CLOSED_STATUSES = {"cancelled", "expired", "error", "superseded"}


def _provider_error(exc):
    if isinstance(exc, ApiPayConfigurationError):
        return ValidationError({
            "detail": "Счёт на оплату временно недоступен.",
            "code": "payment_provider_not_configured",
        })
    if isinstance(exc, ApiPayAPIError):
        return ValidationError({"detail": exc.message, "code": exc.error_code})
    return exc


def _issue_provider_payment(payment: Payment, *, phone_number=None):
    """Создать обязательный внешний счёт для QR/счёта на оплату."""
    channel = PROVIDER_METHOD_CHANNELS.get(payment.method)
    if channel is None:
        return None
    current = getattr(payment, "apipay_invoice", None)
    if (
        current is not None
        and current.channel == "qr"
        and current.invoice_id is None
        and current.status == "creating"
    ):
        raise ValidationError({
            "detail": (
                "Создание QR ещё сверяется с платёжным сервисом. "
                "Повторный QR может списать деньги дважды."
            ),
            "code": "qr_issue_recovery_pending",
        })
    if (
        current is not None
        and current.invoice_id is not None
        and current.status in PROVIDER_CLOSED_STATUSES
    ):
        raise ValidationError({
            "detail": "Этот счёт уже закрыт. Создайте новую платёжную операцию.",
            "code": "provider_invoice_closed",
        })
    return create_invoice(
        payment,
        channel=channel,
        phone_number=phone_number if channel == "phone" else None,
    )


def _provider_issue_is_unresolved(payment: Payment) -> bool:
    return ApiPayInvoice.objects.filter(
        payment=payment,
        invoice_id__isnull=True,
        status="creating",
    ).exists()


def _reject_created_payments(payments, user, *, keep_payment_ids=None):
    keep_payment_ids = set(keep_payment_ids or ())
    for payment in payments:
        if payment.pk in keep_payment_ids:
            continue
        payment.refresh_from_db()
        if (
            payment.status in Payment.IN_PROGRESS_STATUSES
            and not _provider_issue_is_unresolved(payment)
        ):
            reject_payment(payment, user)


def _issue_mixed_provider_payments(payments, parts, user):
    """Выдать онлайн-счета сервером; QR создаётся последним для компенсации."""
    part_by_method = {
        str(part.get("method") or ""): part
        for part in parts if isinstance(part, dict)
    }
    online = sorted(
        (payment for payment in payments if payment.method in PROVIDER_METHOD_CHANNELS),
        key=lambda payment: 1 if payment.method == "kaspi" else 0,
    )
    issued = []
    try:
        for payment in online:
            part = part_by_method.get(payment.method, {})
            record = _issue_provider_payment(
                payment, phone_number=part.get("phone_number")
            )
            if record is not None:
                issued.append(record)
    except (ApiPayAPIError, ApiPayConfigurationError, ValidationError) as exc:
        # Телефонный счёт можно попытаться закрыть. QR создаётся последним,
        # поэтому после успешного QR следующих потенциально падающих шагов нет.
        still_payable = set()
        for record in issued:
            if (
                record.channel == "phone"
                and record.status not in PROVIDER_CLOSED_STATUSES
            ):
                try:
                    cancel_invoice(record)
                except (ApiPayAPIError, ValidationError):
                    pass
            record.refresh_from_db()
            if record.status not in PROVIDER_CLOSED_STATUSES:
                still_payable.add(record.payment_id)
        # A phone invoice in cancelling/unknown state continues to reserve its
        # amount until a webhook or reconciliation proves it is closed.
        _reject_created_payments(
            payments, user, keep_payment_ids=still_payable
        )
        raise _provider_error(exc)
    return issued


def _restore_payment_and_provider(payment: Payment, user):
    payment = restore_rejected_payment(payment, user)
    try:
        _issue_provider_payment(payment)
    except (ApiPayAPIError, ApiPayConfigurationError, ValidationError) as exc:
        payment.refresh_from_db()
        if (
            payment.status in Payment.IN_PROGRESS_STATUSES
            and not _provider_issue_is_unresolved(payment)
        ):
            reject_payment(payment, user)
        raise _provider_error(exc)
    payment.refresh_from_db()
    return payment


def _reject_payment_with_provider(payment: Payment, user, *, reason: str):
    """Reject a pending payment without leaving a payable phone invoice."""
    reason = reason.strip() or "Отклонено сотрудником"
    try:
        invoice = payment.apipay_invoice
    except ApiPayInvoice.DoesNotExist:
        invoice = None
    if (
        invoice is not None
        and invoice.status in MONEY_RECEIVED_INVOICE_STATUSES
    ):
        raise ValidationError({
            "detail": "Платёж уже оплачен и будет подтверждён автоматически.",
            "code": "payment_already_paid",
        })
    if invoice is not None and invoice.status not in (
        "cancelled", "expired", "error", "superseded",
    ):
        if invoice.channel == "qr":
            raise ValidationError({
                "detail": (
                    "Активный Kaspi QR нельзя отменить. Дождитесь его истечения "
                    "или предложите клиенту не оплачивать этот QR."
                ),
                "code": "qr_cancel_unsupported",
            })
        cancel_invoice(invoice)
        payment.refresh_from_db()
        invoice.refresh_from_db()
        if (
            payment.status == "confirmed"
            or invoice.status in MONEY_RECEIVED_INVOICE_STATUSES
        ):
            raise ValidationError({
                "detail": (
                    "Платёж уже оплачен и будет подтверждён автоматически."
                ),
                "code": "payment_already_paid",
            })
        if invoice.status not in PROVIDER_CLOSED_STATUSES:
            payment.note = (
                f"{payment.note}\n" if payment.note else ""
            ) + f"Запрошена отмена: {reason}"
            payment.save(update_fields=["note"])
            return payment, True
    payment.note = (
        f"{payment.note}\n" if payment.note else ""
    ) + f"Отклонено: {reason}"
    payment.save(update_fields=["note"])
    if payment.status == "rejected":
        return payment, False
    reject_payment(payment, user)
    payment.refresh_from_db()
    return payment, False


class ReportSummaryView(APIView):
    """Сводный отчёт за период: касса (нал/безнал), отгрузки, долги, кассиры."""

    def get_permissions(self):
        return [HasPerm("reports.view")]

    def get(self, request):
        date_from, date_to = parse_date_range(request.query_params)
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
        # Оплаты корзины деньгами не считаются: у Payment своего мягкого
        # удаления нет, а order__ не проходит через LiveOrderManager — скоуп
        # задаём явно, иначе итог кассы включает удалённые заказы.
        qs = with_payment_api_relations(
            Payment.objects.filter(order__deleted_at__isnull=True),
            order_context=True,
        ).order_by("-paid_at")
        status = request.query_params.get("status")
        method = request.query_params.get("method")
        search = request.query_params.get("search")
        if status:
            qs = qs.filter(status=status)
        if method:
            qs = qs.filter(method=method)
        if search:
            normalized_search = search.strip()
            search_query = (
                Q(order__client__first_name__icontains=normalized_search)
                | Q(order__client__last_name__icontains=normalized_search)
                | Q(order__client__company_name__icontains=normalized_search)
                | Q(order__client__phone__icontains=normalized_search)
            )
            operation_match = re.fullmatch(
                r"(?:PAY[-\s]*)?0*(\d+)", normalized_search, flags=re.IGNORECASE
            )
            if operation_match:
                operation_id = int(operation_match.group(1))
                search_query |= Q(order_id=operation_id) | Q(id=operation_id)
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
        # One grouped aggregate instead of a query per currency and metric.
        # ``values()`` also drops the row prefetches, which the totals never use.
        # ``order_by()`` is cleared deliberately: the default "-paid_at"
        # ordering would join GROUP BY and split each currency per timestamp.
        # Группировка сразу по валюте и способу: итог кассы сам по себе не
        # отвечает, чем платили, а при смешанной оплате это главный вопрос.
        totals = (
            qs.filter(status="confirmed")
            .order_by()
            .values("order__currency", "method")
            .annotate(gross=Sum("amount"), refunded=Sum("refunded_amount"))
        )
        paid_by_currency = {}
        refunded_by_currency = {}
        paid_by_method = {}
        for row in totals:
            currency = row["order__currency"]
            net = (row["gross"] or Decimal("0")) - (
                row["refunded"] or Decimal("0")
            )
            paid_by_currency[currency] = (
                paid_by_currency.get(currency, Decimal("0")) + net
            )
            refunded_by_currency[currency] = refunded_by_currency.get(
                currency, Decimal("0")
            ) + (row["refunded"] or Decimal("0"))
            if net > 0:
                by_method = paid_by_method.setdefault(currency, {})
                by_method[row["method"]] = (
                    by_method.get(row["method"], Decimal("0")) + net
                )
        for currency in ("KZT", "USD"):
            paid_by_currency.setdefault(currency, Decimal("0"))
            refunded_by_currency.setdefault(currency, Decimal("0"))
        return Response({
            "results": PaymentSerializer(rows, many=True).data,
            "count": count,
            "page": page,
            "pages": pages,
            "summary": {
                "paid_by_currency": {
                    currency: money_string(amount)
                    for currency, amount in paid_by_currency.items()
                },
                "refunded_by_currency": {
                    currency: money_string(amount)
                    for currency, amount in refunded_by_currency.items()
                },
                # {валюта: {способ: сумма}} — суммы уже чистые, поэтому их
                # сложение по способам совпадает с paid_by_currency.
                "paid_by_method": {
                    currency: {
                        method: money_string(amount)
                        for method, amount in sorted(
                            methods.items(), key=lambda item: -item[1]
                        )
                    }
                    for currency, methods in paid_by_method.items()
                },
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
        payment = get_object_or_404(
            Payment.objects.select_related("order", "apipay_invoice"),
            pk=payment_id,
        )
        mode = str(request.data.get("mode") or "auto")
        if mode not in ("auto", "apipay", "cash"):
            raise ValidationError({
                "detail": "Выберите возврат по счёту или из кассы.",
                "code": "invalid_refund_mode",
            })
        try:
            invoice = payment.apipay_invoice
        except ApiPayInvoice.DoesNotExist:
            invoice = None
        use_apipay = (
            mode != "cash"
            and invoice is not None
        )
        if mode == "apipay" and not use_apipay:
            raise ValidationError({
                "detail": (
                    "Возврат через ApiPay доступен только для оплаченного "
                    "онлайн-счёта."
                ),
                "code": "apipay_refund_unavailable",
            })
        try:
            if use_apipay:
                refund = create_refund(
                    invoice, request.user, amount=request.data.get("amount"),
                    reason=request.data.get("reason") or "",
                )
                response_data = {
                    "id": refund.refund_id,
                    "amount": money_string(refund.amount),
                    "status": refund.status,
                    "method": "apipay",
                }
            else:
                refund = create_cash_refund(
                    payment, request.user, amount=request.data.get("amount"),
                    reason=request.data.get("reason") or "",
                )
                response_data = {
                    "id": refund.pk,
                    "amount": money_string(refund.amount),
                    "status": refund.status,
                    "method": "cash",
                }
        except ApiPayAPIError as exc:
            raise ValidationError({
                "detail": exc.message, "code": exc.error_code
            }) from exc
        return Response(response_data, status=201)


class PaymentKaspiQrView(APIView):
    def get_permissions(self):
        return [HasPerm("payments.create")]

    def post(self, request, payment_id):
        payment = get_object_or_404(
            Payment.objects.select_related("order__client"), pk=payment_id,
            method="kaspi",
            status__in=Payment.IN_PROGRESS_STATUSES,
            order__deleted_at__isnull=True,
        )
        try:
            _issue_provider_payment(payment)
        except (ApiPayAPIError, ApiPayConfigurationError, ValidationError) as exc:
            raise _provider_error(exc) from exc
        payment.refresh_from_db()
        return Response(PaymentSerializer(payment).data, status=201)


class PaymentProviderIssueView(APIView):
    """Повторно выдать отсутствующий QR или счёт для активной операции."""

    def get_permissions(self):
        return [HasPerm("payments.create")]

    def post(self, request, payment_id):
        payment = get_object_or_404(
            Payment.objects.select_related("order__client", "apipay_invoice"),
            pk=payment_id,
            status__in=Payment.IN_PROGRESS_STATUSES,
            method__in=PROVIDER_METHOD_CHANNELS,
            order__deleted_at__isnull=True,
        )
        try:
            _issue_provider_payment(
                payment, phone_number=request.data.get("phone_number")
            )
        except (ApiPayAPIError, ApiPayConfigurationError, ValidationError) as exc:
            raise _provider_error(exc) from exc
        payment.refresh_from_db()
        return Response(PaymentSerializer(payment).data, status=201)


class PaymentRestoreView(APIView):
    """Вернуть отклонённую операцию и снова выдать онлайн-счёт при необходимости."""

    def get_permissions(self):
        return [HasPerm("payments.confirm")]

    def post(self, request, payment_id):
        payment = get_object_or_404(
            Payment.objects.select_related("order__client", "apipay_invoice"),
            pk=payment_id,
        )
        payment = _restore_payment_and_provider(payment, request.user)
        return Response(PaymentSerializer(payment).data)


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
            payment, pending = _reject_payment_with_provider(
                payment, request.user, reason=reason
            )
        except ApiPayAPIError as exc:
            raise ValidationError({
                "detail": exc.message, "code": exc.error_code
            }) from exc
        return Response(
            PaymentSerializer(payment).data,
            status=202 if pending else 200,
        )


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
        "restore_payment": "payments.confirm",
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
            date_from, date_to = parse_date_range(params)
            qs = filter_date_range(qs, "created_at", date_from, date_to)
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
        # Выручка складывается из quantity/unit_price позиции — сам товар в
        # сводке не читается, поэтому джоин к каталогу здесь лишний.
        qs = Order.objects.prefetch_related("items")
        date_from, date_to = parse_date_range(params)
        qs = filter_date_range(qs, "created_at", date_from, date_to)
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
            # Валюты не складываются: у отдела могут быть заказы и в тенге,
            # и в долларах, а «выручка» одним числом смешала бы их.
            "revenue_by_currency": defaultdict(lambda: Decimal("0")),
        } for department in Department.objects.all()}
        for order in qs:
            row = rows.get(order.department)
            if row is None:
                continue
            row["orders"] += 1
            if order.status == "shipped":
                row["shipped"] += 1
            elif is_in_progress(order.status):
                row["active"] += 1
            # В выручку идут только финансовые заказы: черновик и «на
            # рассмотрении» ещё не подтверждены и оборотом не являются.
            if is_financial(order.status):
                row["revenue_by_currency"][order.currency or "KZT"] += order.total_amount
        result = []
        for row in rows.values():
            if not (row["is_active"] or row["orders"]):
                continue
            totals = dict(row["revenue_by_currency"])
            currency = primary_currency(totals)
            result.append({
                **row,
                # Плоское поле описывает основную валюту отдела; полная
                # раскладка идёт рядом, чтобы фронт показал обе строки.
                "revenue": money_string(totals.get(currency, Decimal("0"))),
                "revenue_currency": currency,
                "revenue_by_currency": as_money_strings(totals),
            })
        return Response(result)

    @action(detail=False, methods=["get"], url_path="payments-queue")
    def payments_queue(self, request):
        """Очередь ручной обработки кассиром (requested и received)."""
        stage = request.query_params.get("stage")
        stages = [stage] if stage in Payment.STATUSES else Payment.IN_PROGRESS_STATUSES
        qs = with_payment_api_relations(
            Payment.objects.filter(
                status__in=stages,
                method="cash",
                order__in=Order.objects.all(),
            ),
            order_context=True,
        ).order_by("paid_at")
        department = request.query_params.get("department")
        if department:
            qs = qs.filter(order__department=department)
        store = parse_store_id(request.query_params.get("store"))
        if store:
            qs = qs.filter(order__store_id=store)
        date_from, date_to = parse_date_range(request.query_params)
        qs = filter_date_range(qs, "paid_at", date_from, date_to)
        return Response(PaymentQueueSerializer(qs, many=True).data)

    @action(detail=False, methods=["get"], url_path="cashier-log")
    def cashier_log(self, request):
        """Неизменяемый журнал действий с оплатами для экрана кассы."""
        def public_message(message: str) -> str:
            """Скрыть технические названия провайдера и коды способов оплаты."""
            replacements = (
                ("ApiPay: счёт", "Счёт на оплату"),
                ("Счёт ApiPay", "Счёт на оплату"),
                ("Возврат ApiPay", "Возврат по счёту"),
                ("Статус возврата ApiPay", "Статус возврата по счёту"),
                ("(invoice)", "(счёт на оплату)"),
            )
            for source, target in replacements:
                message = message.replace(source, target)
            return message

        qs = (EventLog.objects
              .filter(event_type="payment", order__in=Order.objects.all())
              .select_related("user", "order__client", "order__store"))
        department = request.query_params.get("department")
        if department:
            qs = qs.filter(order__department=department)
        store = parse_store_id(request.query_params.get("store"))
        if store:
            qs = qs.filter(order__store_id=store)
        date_from, date_to = parse_date_range(request.query_params)
        qs = filter_date_range(qs, "created_at", date_from, date_to)

        events = list(qs[:200])
        payment_ids = {
            event.payload.get("payment_id") for event in events
            if event.payload.get("payment_id") is not None
        }
        current_statuses = dict(Payment.objects.filter(pk__in=payment_ids)
                                .values_list("pk", "status"))
        closed_provider_payments = set(
            ApiPayInvoice.objects.filter(
                payment_id__in=payment_ids,
                status__in=("cancelled", "expired", "error", "superseded"),
            ).values_list("payment_id", flat=True)
        )
        provider_payments = set(
            ApiPayInvoice.objects.filter(
                payment_id__in=payment_ids,
            ).values_list("payment_id", flat=True)
        )
        # После цикла confirm → reopen → confirm в журнале несколько событий
        # confirmed. Кнопка отката должна быть только у самого свежего.
        latest_confirmation: dict[int, int] = {}
        latest_rejection: dict[int, int] = {}
        for event in events:
            payment_id = event.payload.get("payment_id")
            if (payment_id is not None
                    and event.payload.get("payment_stage") == "confirmed"
                    and payment_id not in latest_confirmation):
                latest_confirmation[payment_id] = event.id
            if (payment_id is not None
                    and event.payload.get("payment_stage") == "rejected"
                    and payment_id not in latest_rejection):
                latest_rejection[payment_id] = event.id
        return Response([{
            "id": event.id,
            "message": public_message(event.message),
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
                and event.payload.get("payment_id") not in provider_payments
            ),
            "can_restore": (
                event.payload.get("payment_stage") == "rejected"
                and current_statuses.get(event.payload.get("payment_id")) == "rejected"
                and latest_rejection.get(event.payload.get("payment_id")) == event.id
                and event.payload.get("payment_id") not in closed_provider_payments
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
            if isinstance(parts, list):
                normalized_parts = []
                for raw_part in parts:
                    part = dict(raw_part) if isinstance(raw_part, dict) else raw_part
                    if isinstance(part, dict) and part.get("method") == "invoice":
                        part["phone_number"] = normalize_phone(
                            part.get("phone_number") or order.client.phone
                        )
                    normalized_parts.append(part)
                parts = normalized_parts
            payments = add_mixed_payments(
                order, parts, request.user, note=request.data.get("note") or "")
            _issue_mixed_provider_payments(payments, parts, request.user)
            for payment in payments:
                payment.refresh_from_db()
            return Response(PaymentSerializer(payments, many=True).data, status=201)
        method = request.data.get("method") or "cash"
        phone_number = request.data.get("phone_number")
        if method == "invoice":
            phone_number = normalize_phone(
                phone_number or order.client.phone
            )
        payment = add_payment(
            order, request.data.get("amount"), request.user,
            method=method,
            stage=request.data.get("stage") or "received",
            note=request.data.get("note") or "")
        if method in PROVIDER_METHOD_CHANNELS:
            try:
                _issue_provider_payment(
                    payment, phone_number=phone_number
                )
            except (ApiPayAPIError, ApiPayConfigurationError, ValidationError) as exc:
                _reject_created_payments([payment], request.user)
                raise _provider_error(exc) from exc
            payment.refresh_from_db()
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

    @action(detail=True, methods=["post"],
            url_path=r"payments/(?P<pid>\d+)/restore")
    def restore_payment(self, request, pk=None, pid=None):
        """Восстановление случайно отклонённой оплаты: rejected → очередь."""
        payment = get_object_or_404(Payment, pk=pid, order=self.get_object())
        payment = _restore_payment_and_provider(payment, request.user)
        return Response(OrderSerializer(payment.order, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path=r"payments/(?P<pid>\d+)/reject")
    def reject_payment(self, request, pk=None, pid=None):
        payment = get_object_or_404(
            Payment.objects.select_related("apipay_invoice"),
            pk=pid,
            order=self.get_object(),
        )
        if payment.status not in Payment.IN_PROGRESS_STATUSES:
            raise ValidationError({
                "detail": "Отклонить можно только ожидающий платёж.",
                "code": "invalid_payment_stage",
            })
        try:
            payment, pending = _reject_payment_with_provider(
                payment,
                request.user,
                reason=str(request.data.get("reason") or ""),
            )
        except ApiPayAPIError as exc:
            raise ValidationError({
                "detail": exc.message, "code": exc.error_code
            }) from exc
        return Response(
            OrderSerializer(payment.order, context={"request": request}).data,
            status=202 if pending else 200,
        )

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
