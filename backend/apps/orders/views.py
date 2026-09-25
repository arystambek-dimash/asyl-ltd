from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import MethodNotAllowed, NotFound, ValidationError
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.http import FileResponse
from io import BytesIO
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import re
from django.core.paginator import Paginator
from django.db.models import Count, Exists, F, OuterRef, Q, Sum
from django.utils import timezone
from apps.common.pagination import OptInPageNumberPagination
from apps.common.permissions import HasPerm, PermAPIViewMixin, PermViewSetMixin
from apps.common.money import (
    CURRENCY_CODES,
    DEFAULT_CURRENCY,
    as_money_strings,
    money_string,
    primary_currency,
)
from apps.common.query_params import filter_date_range, parse_date_range, parse_search_param
from apps.sales.access import assigned_department_id, scope_by_client_department
from apps.sales.labels import UNASSIGNED_CODE, UNASSIGNED_COLOR, UNASSIGNED_NAME
from apps.sales.models import Department
from apps.eventlog.models import EventLog
from apps.shipments.services import rollback_shipment
from apps.shipments.serializers import LoadSerializer
from django.db import transaction
from .models import (
    ApiPayInvoice, ApiPayQrRefund, Order, Payment, StatusChangeRequest,
)
from .qr_refunds import execute_qr_refund, revoke_qr_refund, serialize_qr_refund, start_qr_refund
from .apipay import (
    ApiPayAPIError, ApiPayConfigurationError, assert_apipay_currency, cancel_invoice,
    CLOSED_INVOICE_STATUSES, create_invoice, create_refund, MONEY_RECEIVED_INVOICE_STATUSES,
    normalize_phone,
    provider_error, reject_unissued_payment,
)
from .refunds import create_cash_refund
from .invoices import build_payment_receipt_pdf
from .debt import counts_as_debt, payment_status
from .querysets import (
    shipping_calendar_days,
    CASHIER_QUEUE_PAYMENT,
    cashier_queue_payments,
    awaiting_payment_orders,
    awaiting_shipment_orders,
    order_overpaid_by_id,
    order_remaining_by_id,
    overpaid_orders,
    for_post_board,
    post_board_params,
    with_order_api_relations,
    with_payment_api_relations,
    with_order_amounts, filter_order_search, order_page_sort,
    client_search_q, department_q, filter_order_scope, filter_status_group, order_department,
)
from .labels import payment_method_label, payment_status_label
from .reports import summary_report
from .references import build_order_form_options
from .statuses import (
    REVIEWABLE_STATUSES, is_financial, is_in_progress, public_status_key, statuses_in_group,
)
from .serializers import (ConfirmOrderSerializer, OrderSerializer, PaymentSerializer,
                          PaymentQueueSerializer, StatusChangeRequestSerializer)
from .transport import transport_locked
from .services import (add_payment, confirm_order, confirm_stock_context, reject_order,
                       accountant_confirm_payment, assert_payment_status_open,
                       correct_order_prices,
                       receive_and_confirm_payment,
                       record_staff_payment,
                       reopen_confirmed_payment, reject_payment,
                       restore_rejected_payment,
                       soft_delete_order, restore_order,
                       purge_order,
                       lock_live_order, move_order_to_debt,
                       request_status_change, approve_status_change, reject_status_change)


# Способы, по которым КАССА выставляет счёт платёжному сервису.
#
# QR сюда не входит намеренно. В CRM оператор выбирает «QR» уже ПОСЛЕ того,
# как деньги прошли через POS-терминал, — это отметка о факте оплаты, а не
# запрос на выставление счёта. Сгенерировать здесь Kaspi QR без явного
# channel="qr" значило бы попросить клиента заплатить второй раз.
#
# Клиентский портал платит сам и живёт по своим правилам: он вызывает
# провайдера напрямую (apps/portal/views.py) и настоящий QR по-прежнему
# создаёт и показывает.
#
# Kaspi QR касса выставляет только явным channel="qr" (POS на телефоне,
# см. _issue_staff_qr_payment); без channel kaspi — отметка своего терминала.
PROVIDER_METHOD_CHANNELS = {"invoice": "phone"}

# Счётчики сводки отделов по статусу оплаты (orders/debt.py: payment_status).
_PAYMENT_STATUS_COUNTERS = {
    "unpaid": "unpaid_orders",
    "partial": "partial_orders",
    "settled": "paid_orders",
}


def _department_summary_row(row_id, code, name, color, is_active) -> dict:
    """Пустая карточка отдела для сводки ``department_summary``."""
    return {
        "id": row_id,
        "code": code,
        "name": name,
        "color": color,
        "is_active": is_active,
        "orders": 0,
        "active": 0,
        "shipped": 0,
        # Валюты не складываются: у отдела могут быть заказы и в тенге,
        # и в долларах, а «выручка» одним числом смешала бы их.
        "revenue_by_currency": defaultdict(lambda: Decimal("0")),
        "debt_by_currency": defaultdict(lambda: Decimal("0")),
        "paid_by_currency": defaultdict(lambda: Decimal("0")),
        # Счётчики по расчётам. Считаются только по финансовым заказам:
        # черновик и «на рассмотрении» ещё ничего не должны.
        "paid_orders": 0,
        "partial_orders": 0,
        "unpaid_orders": 0,
        "debt_orders": 0,
    }


def _issue_provider_payment(payment: Payment, *, user, phone_number=None):
    """Создать обязательный внешний счёт провайдеру для счёта на оплату."""
    channel = PROVIDER_METHOD_CHANNELS.get(payment.method)
    if channel is None:
        return None
    current = getattr(payment, "apipay_invoice", None)
    if current is None:
        # Новый счёт провайдеру — запрос денег (statuses.is_payment_open):
        # до отгрузки его не выдаёт ни одна ручка, в том числе повторная выдача
        # после отката отгрузки. Сверка уже начатой выдачи не блокируется.
        assert_payment_status_open(payment.order, method=None)
    if (
        current is not None
        and current.invoice_id is not None
        and current.status in CLOSED_INVOICE_STATUSES
    ):
        raise ValidationError({
            "detail": "Этот счёт уже закрыт. Создайте новую платёжную операцию.",
            "code": "provider_invoice_closed",
        })
    return create_invoice(
        payment,
        channel=channel,
        phone_number=phone_number,
        user=user,
    )


def _issue_staff_qr_payment(order, amount_raw, user) -> Payment:
    """Касса выставляет Kaspi QR через ApiPay (POS на телефоне кассира).

    Заказ остаётся «в долг»: оплата создаётся кассовым add_payment, а не
    портальным create_client_payment — тот переводит заказ в моментальную
    оплату, и долг пропал бы из списка. Деньги подтверждает вебхук или сверка.
    """
    assert_apipay_currency(order)
    try:
        amount = Decimal(str(amount_raw))
    except (InvalidOperation, TypeError, ValueError):
        amount = None
    if (
        amount is None
        or not amount.is_finite()
        or amount <= 0
        or amount != amount.to_integral_value()
    ):
        raise ValidationError({
            "detail": "Kaspi QR принимает только целые тенге.",
            "code": "qr_whole_tenge",
        })
    payment = add_payment(order, amount, user, method="kaspi", stage="requested")
    try:
        create_invoice(payment, channel="qr", user=user)
    except (ApiPayAPIError, ApiPayConfigurationError, ValidationError) as exc:
        reject_unissued_payment(payment, user)
        raise provider_error(exc) from exc
    payment.refresh_from_db()
    return payment


def _restore_payment_and_provider(payment: Payment, user):
    payment = restore_rejected_payment(payment, user)
    try:
        _issue_provider_payment(payment, user=user)
    except (ApiPayAPIError, ApiPayConfigurationError, ValidationError) as exc:
        reject_unissued_payment(payment, user)
        raise provider_error(exc) from exc
    payment.refresh_from_db()
    return payment


def _reject_payment_with_provider(
    payment: Payment, user, *, reason: str, any_department=False,
):
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
    if invoice is not None and invoice.status not in CLOSED_INVOICE_STATUSES:
        if invoice.channel == "qr":
            raise ValidationError({
                "detail": (
                    "Активный Kaspi QR нельзя отменить. Дождитесь его истечения "
                    "или предложите клиенту не оплачивать этот QR."
                ),
                "code": "qr_cancel_unsupported",
            })
        cancel_invoice(invoice, user=user)
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
        if invoice.status not in CLOSED_INVOICE_STATUSES:
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
    reject_payment(payment, user, any_department=any_department)
    payment.refresh_from_db()
    return payment, False


class ReportSummaryView(PermAPIViewMixin, APIView):
    """Сводный отчёт за период: касса (нал/безнал), отгрузки, долги, кассиры."""

    required_perms = {"get": "reports.view"}

    def get(self, request):
        date_from, date_to = parse_date_range(request.query_params)
        qs = scope_by_client_department(
            Order.objects.all(),
            request.user,
            client_path="client",
        )
        qs = filter_order_scope(qs, request.query_params)
        section = request.query_params.get("section", "all")
        if section not in ("all", "income"):
            raise ValidationError({"detail": "Неизвестный раздел отчёта", "code": "invalid_report_section"})
        return Response(summary_report(qs, date_from, date_to, income_only=section == "income"))


class PaymentTransactionListView(PermAPIViewMixin, APIView):
    required_perms = {"get": "payments.view"}

    def get(self, request):
        # order__ не проходит через LiveOrderManager — корзину отсекаем явно.
        payments = scope_by_client_department(
            Payment.objects.filter(order__deleted_at__isnull=True),
            request.user,
            client_path="order__client",
        )
        qs = with_payment_api_relations(
            payments,
            order_context=True,
        ).order_by("-paid_at")
        status = request.query_params.get("status")
        search = request.query_params.get("search")
        qs = filter_order_scope(qs, request.query_params, prefix="order__")
        if search:
            normalized_search = search.strip()
            search_query = client_search_q(normalized_search, "order__client__")
            operation_match = re.fullmatch(
                r"(?:PAY[-\s]*)?0*(\d+)", normalized_search, flags=re.IGNORECASE
            )
            if operation_match:
                operation_id = int(operation_match.group(1))
                search_query |= Q(order_id=operation_id) | Q(id=operation_id)
            qs = qs.filter(search_query)
        # Счётчики статусов считаются ДО статус-фильтра: пилюли должны
        # показывать, сколько операций стоит за каждым статусом при текущем
        # поиске, а не только за выбранным.
        # Публичные группы отличаются от внутренних статусов. Провайдерская
        # операция со статусом received всё ещё ожидает клиента и должна быть
        # в «Ожидает», а не в ручной кассовой очереди «В кассе».
        public_status_filters = {
            "requested": (
                Q(status="requested")
                | Q(status="received", apipay_invoice__isnull=False)
            ),
            "received": Q(status="received", apipay_invoice__isnull=True),
            "confirmed": Q(status="confirmed"),
            "rejected": Q(status="rejected"),
        }
        grouped_counts = qs.aggregate(**{
            key: Count("pk", filter=condition)
            for key, condition in public_status_filters.items()
        })
        status_counts = {key: count for key, count in grouped_counts.items() if count}
        if status:
            condition = public_status_filters.get(status)
            if condition is None:
                raise ValidationError({
                    "detail": "Неизвестная группа статусов.",
                    "code": "invalid_payment_status",
                })
            qs = qs.filter(condition)
        try:
            page_size = min(
                100, max(10, int(request.query_params.get("page_size") or 50))
            )
        except ValueError as exc:
            raise ValidationError({
                "detail": "Некорректный размер страницы.",
                "code": "invalid_page",
            }) from exc
        # Лента всегда постраничная; номер за пределами — последняя страница.
        page = Paginator(qs, page_size).get_page(request.query_params.get("page"))
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
        paid_by_currency: dict[str, Decimal] = {}
        refunded_by_currency: dict[str, Decimal] = {}
        paid_by_method: dict[str, dict[str, Decimal]] = {}
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
        for currency in CURRENCY_CODES:
            paid_by_currency.setdefault(currency, Decimal("0"))
            refunded_by_currency.setdefault(currency, Decimal("0"))
        return Response({
            "results": PaymentSerializer(
                page.object_list,
                many=True,
                context={"request": request},
            ).data,
            "count": page.paginator.count,
            "page": page.number,
            "pages": page.paginator.num_pages,
            "status_counts": status_counts,
            # Подписи пилюль статус-фильтра — из того же словаря, что и строки.
            "status_labels": {
                key: payment_status_label(key) for key in public_status_filters
            },
            "summary": {
                "paid_by_currency": as_money_strings(paid_by_currency),
                "refunded_by_currency": as_money_strings(refunded_by_currency),
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
                "method_labels": {
                    method: payment_method_label(method)
                    for methods in paid_by_method.values()
                    for method in methods
                },
            },
        })


class PaymentReceiptView(PermAPIViewMixin, APIView):
    required_perms = {"get": "payments.view"}

    def get(self, request, payment_id):
        payments = scope_by_client_department(
            Payment.objects.all(),
            request.user,
            client_path="order__client",
        )
        payment = get_object_or_404(payments, pk=payment_id)
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


class PaymentRefundView(PermAPIViewMixin, APIView):
    required_perms = {"post": "payments.confirm"}

    def post(self, request, payment_id):
        payments = scope_by_client_department(
            Payment.objects.select_related("order", "apipay_invoice"),
            request.user,
            client_path="order__client",
        )
        payment = get_object_or_404(
            payments,
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
            if use_apipay and invoice.channel == "qr":
                # Kaspi возвращает оплату по QR только с подтверждением покупателя.
                session, link = start_qr_refund(
                    invoice, request.user, amount=request.data.get("amount"),
                    reason=request.data.get("reason") or "",
                )
                qr_refund = serialize_qr_refund(session)
                qr_refund["customer_url"] = link
                return Response({
                    "id": session.refund_id,
                    "amount": money_string(session.refund.amount),
                    "status": session.refund.status,
                    "method": "apipay_qr",
                    "qr_refund": qr_refund,
                }, status=201)
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
            raise provider_error(exc) from exc
        return Response(response_data, status=201)


class PaymentQrRefundView(PermAPIViewMixin, APIView):
    """Последний возврат по Kaspi QR оплаты: состояние, отзыв ссылки, ручной выбор покупки."""

    required_perms = {"get": "payments.confirm", "post": "payments.confirm"}

    def _session(self, request, payment_id):
        payments = scope_by_client_department(Payment.objects.all(), request.user, client_path="order__client")
        payment = get_object_or_404(payments, pk=payment_id)
        session = (
            ApiPayQrRefund.objects.select_related("refund", "invoice__payment")
            .filter(invoice__payment=payment)
            .order_by("-pk")
            .first()
        )
        if session is None:
            raise NotFound("Возврат по ссылке не найден")
        return session

    def get(self, request, payment_id, action=None):
        # Действия (revoke/execute) — только POST; GET на их путь — не 500, а 405.
        if action is not None:
            raise MethodNotAllowed(request.method)
        return Response(serialize_qr_refund(self._session(request, payment_id)))

    def post(self, request, payment_id, action=None):
        session = self._session(request, payment_id)
        try:
            if action == "revoke":
                revoke_qr_refund(session.pk, request.user)
            elif action == "execute":
                with transaction.atomic():
                    lock_live_order(session.invoice.payment.order_id, request.user)
                execute_qr_refund(session.pk, str(request.data.get("operation_ref") or ""), user=request.user)
            else:
                raise NotFound()
        except ApiPayAPIError as exc:
            raise provider_error(exc) from exc
        session = ApiPayQrRefund.objects.select_related("refund").get(pk=session.pk)
        return Response(serialize_qr_refund(session))


class PaymentProviderIssueView(PermAPIViewMixin, APIView):
    """Повторно выдать отсутствующий QR или счёт для активной операции."""

    required_perms = {"post": "payments.create"}

    def post(self, request, payment_id):
        payments = scope_by_client_department(
            Payment.objects.select_related("order__client__user", "apipay_invoice"),
            request.user,
            client_path="order__client",
        )
        payment = get_object_or_404(
            payments,
            pk=payment_id,
            status__in=Payment.IN_PROGRESS_STATUSES,
            method__in=PROVIDER_METHOD_CHANNELS,
            order__deleted_at__isnull=True,
        )
        try:
            _issue_provider_payment(
                payment,
                phone_number=request.data.get("phone_number"),
                user=request.user,
            )
        except (ApiPayAPIError, ApiPayConfigurationError, ValidationError) as exc:
            raise provider_error(exc) from exc
        payment.refresh_from_db()
        return Response(PaymentSerializer(payment, context={"request": request}).data, status=201)


class PaymentRestoreView(PermAPIViewMixin, APIView):
    """Вернуть отклонённую операцию и снова выдать онлайн-счёт при необходимости."""

    required_perms = {"post": "payments.confirm"}

    def post(self, request, payment_id):
        payments = scope_by_client_department(
            Payment.objects.select_related("order__client__user", "apipay_invoice"),
            request.user,
            client_path="order__client",
        )
        payment = get_object_or_404(
            payments,
            pk=payment_id,
        )
        payment = _restore_payment_and_provider(payment, request.user)
        return Response(PaymentSerializer(payment, context={"request": request}).data)


class OrderViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    # Всё, что сериализатор трогает на каждой строке, загружаем заранее —
    # список заказов не должен порождать запросы «на заказ» (N+1).
    # Явный порядок обязателен для стабильных страниц пагинации.
    queryset = with_order_api_relations(Order.objects.all()).order_by("-id")
    serializer_class = OrderSerializer
    pagination_class = OptInPageNumberPagination
    required_perms = {
        "list": "orders.view",
        "retrieve": "orders.view",
        "create": "orders.create",
        "update": "orders.edit",
        "partial_update": "orders.edit", "destroy": "orders.edit",
        "trash": "orders.edit", "trash_preview": "orders.edit",
        "restore": "orders.edit",
        "purge": "orders.edit",
        "payments": "payments.create", "confirm": "orders.confirm",
        "confirm_context": "orders.confirm",
        "payment_detail": ("payments.create", "payments.view"),
        "correct_price": "orders.correct_price",
        "set_status": "orders.view",
        "rollback_shipment": "orders.rollback",
        "approve_status": "orders.edit",
        "reject_status": "orders.edit",
        "reject": "orders.confirm",
        "receive_payment": "payments.confirm",
        "confirm_payment": "payments.confirm",
        "reopen_payment": "payments.confirm",
        "reject_payment": "payments.confirm",
        "payments_queue": "payments.confirm",
        # «Оплаты» кассы: принять оплату или перевести в долг отгруженный заказ.
        "awaiting_payment": ("payments.confirm", "payments.create"),
        # «К отгрузке» (предоплата) и «К возврату» (переплата) — там же, в «Оплатах».
        "awaiting_shipment": ("payments.confirm", "payments.create"),
        "to_refund": ("payments.confirm", "payments.create"),
        "to_debt": ("payments.confirm", "payments.create"),
        "fixate": "orders.edit",
        "department_summary": "orders.view",
        "list_summary": "orders.view",
        "dashboard_operational": "orders.view",
        "form_options": ("orders.create", "orders.edit"),
        # Календарь отгрузки открыт тем же, кому открыта очередь поста.
        "shipping_calendar": ("orders.view", "monoblock.view", "loader.view"),
    }

    def get_permissions(self):
        if (
            self.action == "list"
            and self.request.query_params.get("post_board") == "1"
        ):
            # This query is a deliberately bounded projection of active work.
            # Моноблок и грузчик не должны получать весь архив заказов ради очереди.
            return [HasPerm("orders.view", "monoblock.view", "loader.view")]
        return super().get_permissions()

    # Новая заявка клиента без отдела — общая очередь: её видит и разбирает
    # любой отдел, подтверждение закрепляет клиента за отделом.
    UNASSIGNED_REQUEST_ACTIONS = frozenset(
        {"list", "list_summary", "retrieve", "confirm", "confirm_context", "reject"}
    )
    # Итоги списка считаются по той же выборке, что и сам список.
    LIST_ACTIONS = frozenset({"list", "list_summary"})
    # Общие очереди для сотрудника, закреплённого за отделом. Оплаты в ручной
    # очереди кассы видны и разбираются всеми отделами. Заявки всех отделов
    # («Заказы» → «Заявки») — только с правом orders.confirm_all; список заявок
    # входит в очередь только с ``?confirm_queue=1``. Отмена подтверждения и
    # восстановление оплаты, долги, отчёты, транзакции и POS остаются в его отделе.
    SHARED_REQUEST_ACTIONS = frozenset({"retrieve", "confirm", "confirm_context", "reject"})
    SHARED_PAYMENT_ACTIONS = frozenset(
        {"receive_payment", "confirm_payment", "reject_payment"}
    )

    def _confirm_queue_requested(self) -> bool:
        return self.request.query_params.get("confirm_queue") == "1"

    def _shared_queue(self):
        """Условие на заказы других отделов, открытые общей очередью кассы."""
        if self.action in self.SHARED_PAYMENT_ACTIONS:
            # Чужой заказ открыт ровно на оплату из адреса, пока она в очереди.
            return Exists(Payment.objects.filter(
                CASHIER_QUEUE_PAYMENT, order=OuterRef("pk"), pk=self.kwargs.get("pid"),
            ))
        in_queue = (
            self._confirm_queue_requested() if self.action in self.LIST_ACTIONS
            else self.action in self.SHARED_REQUEST_ACTIONS
        )
        user = self.request.user
        if in_queue and user.has_perm_code("orders.confirm") and user.has_perm_code("orders.confirm_all"):
            return Q(status__in=REVIEWABLE_STATUSES)
        return None

    def get_queryset(self):
        qs = scope_by_client_department(
            super().get_queryset(),
            self.request.user,
            client_path="client",
            unassigned=(
                Q(status__in=REVIEWABLE_STATUSES)
                if self.action in self.UNASSIGNED_REQUEST_ACTIONS else None
            ),
            shared=self._shared_queue(),
        )
        if self.action in self.LIST_ACTIONS:
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
                qs = for_post_board(qs, days, **post_board_params(params))
            qs = filter_order_scope(
                qs, params, date_field="created_at",
                # Касса отдела видит и заявки клиентов без отдела, чтобы забрать их к себе.
                department_extra=(
                    department_q(UNASSIGNED_CODE) & Q(status__in=REVIEWABLE_STATUSES)
                    if self._confirm_queue_requested() else None
                ),
            )
            qs = filter_status_group(qs, params.get("status_group"))
            if params.get("post_board") != "1":
                qs = filter_order_search(qs, parse_search_param(params.get("search")))
            if params.get("ordering"):
                qs = order_page_sort(qs, params["ordering"])
        return qs

    @action(detail=False, methods=["get"], url_path="form-options")
    def form_options(self, request):
        """Minimal cross-domain reference data needed by the order form."""
        return Response(build_order_form_options(request.user))

    @action(
        detail=False,
        methods=["get"],
        url_path="dashboard-operational",
    )
    def dashboard_operational(self, request):
        """Small, authoritative dashboard projection instead of order history."""
        qs = self.get_queryset()
        date_from, date_to = parse_date_range(request.query_params)

        queue = qs.filter(status__in=("arrived", "loading")).order_by("id")
        shipment_events = EventLog.objects.filter(
            order__in=qs.filter(status="shipped"),
            event_type="shipment",
        ).only("id", "order_id", "payload", "created_at")
        shipment_events = filter_date_range(
            shipment_events,
            "created_at",
            date_from,
            date_to,
        ).order_by("order_id", "-created_at", "-id")

        # A rolled-back and re-shipped order can have several snapshots.
        latest_by_order: dict[int, EventLog] = {}
        for event in shipment_events:
            latest_by_order.setdefault(event.order_id, event)

        days: dict[str, dict[str, int]] = defaultdict(
            lambda: {"bags": 0, "orders": 0}
        )
        for event in latest_by_order.values():
            raw_bags = event.payload.get("bags_loaded", 0)
            try:
                bags = int(raw_bags)
            except (TypeError, ValueError):
                bags = 0
            day = timezone.localtime(event.created_at).date().isoformat()
            days[day]["bags"] += max(0, bags)
            days[day]["orders"] += 1

        pending_payments = 0
        if request.user.has_perm_code("payments.confirm"):
            # Ссылка ведёт в «Кассу» — число то же, что в её очереди.
            pending_payments = cashier_queue_payments(request.user).count()

        return Response({
            "queue": OrderSerializer(
                queue,
                many=True,
                context={"request": request},
            ).data,
            "attention": {
                "pending_payments": pending_payments,
                "awaiting_review": qs.filter(
                    status__in=statuses_in_group("pending")
                ).count(),
            },
            "days": [
                {"date": date, **values}
                for date, values in sorted(days.items())
            ],
        })

    @action(detail=True, methods=["post"], url_path="correct-price")
    def correct_price(self, request, pk=None):
        order = correct_order_prices(
            self.get_object(),
            request.user,
            total_amount=request.data.get("total_amount"),
            prices=request.data.get("prices"),
        )
        corrected = scope_by_client_department(
            Order.objects.all(),
            request.user,
            client_path="client",
        )
        order = with_order_api_relations(corrected).get(pk=order.pk)
        return Response(
            OrderSerializer(order, context={"request": request}).data
        )

    @action(detail=False, methods=["get"], url_path="shipping-calendar")
    def shipping_calendar(self, request):
        """Календарь отгрузки на месяц: по дням — сколько грузить и сколько уехало.

        Экран грузчика показывает работу по датам, а не таблицу заказов,
        поэтому сам список дня приходит обычной очередью поста (``post_board``).
        """
        month = request.query_params.get("month") or timezone.localdate().strftime("%Y-%m")
        try:
            first_day = datetime.strptime(f"{month}-01", "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValidationError({"detail": "Месяц указывается как ГГГГ-ММ", "code": "bad_month"}) from exc
        next_month = date(
            first_day.year + (first_day.month == 12),
            1 if first_day.month == 12 else first_day.month + 1,
            1,
        )
        last_day = next_month - timedelta(days=1)
        qs = scope_by_client_department(
            Order.objects.all(),
            request.user,
            client_path="client",
        )
        return Response({
            "month": first_day.strftime("%Y-%m"),
            "days": shipping_calendar_days(qs, first_day, last_day),
        })

    @action(detail=False, methods=["get"], url_path="department-summary")
    def department_summary(self, request):
        """Оперативная аналитика заказов в разрезе динамических отделов."""
        params = request.query_params
        qs = scope_by_client_department(
            Order.objects.all(),
            request.user,
            client_path="client",
        )
        date_from, date_to = parse_date_range(params)
        qs = filter_date_range(qs, "created_at", date_from, date_to)
        qs = filter_status_group(qs, params.get("status_group"))

        # Закреплённый за отделом сотрудник видит одну карточку — своего отдела:
        # в неё идут все его заказы, даже записанные на другой код отдела.
        department_id = assigned_department_id(request.user)
        own = Department.objects.filter(pk=department_id).first() if department_id else None
        departments = [own] if own else Department.objects.all()
        rows = {
            department.code: _department_summary_row(
                department.id, department.code, department.name,
                department.color, department.is_active,
            )
            for department in departments
        }
        # Заказ без своего отдела — в карточке отдела клиента, как в фильтре списка.
        rows[""] = _department_summary_row(
            0, UNASSIGNED_CODE, UNASSIGNED_NAME, UNASSIGNED_COLOR, False,
        )
        totals = with_order_amounts(qs).annotate(department_code=order_department()).values(
            "department_code", "status", "currency", "amount_total", "amount_paid"
        )
        for order in totals.iterator(chunk_size=2000):
            row = rows[own.code] if own else rows.get(order["department_code"])
            if row is None:
                continue
            row["orders"] += 1
            if order["status"] == "shipped":
                row["shipped"] += 1
            elif is_in_progress(order["status"]):
                row["active"] += 1
            # В выручку идут только финансовые заказы: черновик и «на
            # рассмотрении» ещё не подтверждены и оборотом не являются.
            if not is_financial(order["status"]):
                continue
            currency = order["currency"] or DEFAULT_CURRENCY
            total, paid = order["amount_total"], order["amount_paid"]
            row["revenue_by_currency"][currency] += total
            row["paid_by_currency"][currency] += paid
            # Дебиторка — по тому же правилу, что и везде (Order.is_debt),
            # иначе цифра в дашборде разойдётся с «Кассой» и выпиской.
            if counts_as_debt(order["status"], total, paid):
                row["debt_orders"] += 1
                row["debt_by_currency"][currency] += total - paid
            if total <= 0:
                continue
            row[_PAYMENT_STATUS_COUNTERS[payment_status(total, paid)]] += 1
        result = []
        for row in rows.values():
            if not (row["is_active"] or row["orders"]):
                continue
            totals = dict(row["revenue_by_currency"])
            debts = dict(row["debt_by_currency"])
            currency = primary_currency(totals)
            result.append({
                **row,
                # Плоское поле описывает основную валюту отдела; полная
                # раскладка идёт рядом, чтобы фронт показал обе строки.
                "revenue": money_string(totals.get(currency, Decimal("0"))),
                "revenue_currency": currency,
                "revenue_by_currency": as_money_strings(totals),
                "debt": money_string(debts.get(currency, Decimal("0"))),
                "debt_by_currency": as_money_strings(debts),
                "paid": money_string(
                    dict(row["paid_by_currency"]).get(currency, Decimal("0"))),
                "paid_by_currency": as_money_strings(dict(row["paid_by_currency"])),
            })
        return Response(result)

    @action(detail=False, methods=["get"], url_path="list-summary")
    def list_summary(self, request):
        """Итоги «Общей» аналитики по всей выборке списка, а не по его странице.

        Фильтры и поиск те же, что у списка. Отменённые и отклонённые в итоги
        не входят. Это стоимость заказов, а не выручка: «на рассмотрении»
        считается отдельной долей. Валюты не складываются.
        """
        qs = self.get_queryset().select_related(None).prefetch_related(None).order_by()
        qs = qs.exclude(status__in=statuses_in_group("cancelled"))
        orders = active = 0
        totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        by_group: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
        rows = with_order_amounts(qs).values("status", "currency", "amount_total")
        for order in rows.iterator(chunk_size=2000):
            orders += 1
            if is_in_progress(order["status"]):
                active += 1
            currency = order["currency"] or DEFAULT_CURRENCY
            totals[currency] += order["amount_total"]
            by_group[(currency, public_status_key(order["status"]))] += order["amount_total"]
        currency = primary_currency(totals)
        return Response({
            "orders": orders,
            "active": active,
            "total_currency": currency,
            "total_by_currency": as_money_strings(dict(totals)),
            # Доли статусов — только в основной валюте: ₸ и $ в один бар не идут.
            "by_status_group": as_money_strings({
                group: amount for (code, group), amount in by_group.items() if code == currency
            }),
        })

    @action(detail=False, methods=["get"], url_path="payments-queue")
    def payments_queue(self, request):
        """Очередь ручной обработки кассиром (requested и received)."""
        qs = filter_order_scope(
            cashier_queue_payments(request.user).order_by("paid_at", "id"),
            request.query_params, prefix="order__", date_field="paid_at",
        )
        if request.query_params.get("summary") == "1":
            return Response([
                {"currency": row["order__currency"], "method": row["method"],
                 "amount": money_string(row["amount"]), "count": row["count"]}
                for row in qs.order_by().values("order__currency", "method").annotate(
                    amount=Sum("amount"), count=Count("id")
                ).order_by("order__currency", "method")
            ])
        qs = with_payment_api_relations(qs, order_context=True)
        page = self.paginate_queryset(qs)
        data = PaymentQueueSerializer(
            page if page is not None else qs, many=True,
            context=self.get_serializer_context(),
        ).data
        return self.get_paginated_response(data) if page is not None else Response(data)

    def _cashier_orders(self, request, qs, *, date_field, ordering, amounts=order_remaining_by_id):
        """Список заказов «Оплат» кассы: фильтры отдела/магазина/дат, сводка, страница.

        Сводка (``summary=1``) — сумма и число заказов по валютам. Сумму по
        каждому заказу выборки отдаёт ``amounts``: остаток к оплате, а для
        «К возврату» — переплата (``order_overpaid_by_id``).
        """
        qs = filter_order_scope(qs, request.query_params, date_field=date_field)
        if request.query_params.get("summary") == "1":
            rows = list(qs.order_by().values("pk", "currency"))
            by_id = amounts(qs)
            totals: dict[str, dict] = {}
            for row in rows:
                entry = totals.setdefault(row["currency"] or DEFAULT_CURRENCY, {"amount": Decimal("0"), "count": 0})
                entry["amount"] += by_id.get(row["pk"], Decimal("0"))
                entry["count"] += 1
            return Response([
                {"currency": currency, "amount": money_string(entry["amount"]), "count": entry["count"]}
                for currency, entry in sorted(totals.items())
            ])
        qs = qs.order_by(*ordering)
        page = self.paginate_queryset(qs)
        data = self.get_serializer(page if page is not None else qs, many=True).data
        return self.get_paginated_response(data) if page is not None else Response(data)

    @action(detail=False, methods=["get"], url_path="awaiting-payment")
    def awaiting_payment(self, request):
        """«Ждут оплаты»: несогласованные долги отдела — принять оплату или согласовать долг."""
        return self._cashier_orders(
            request,
            awaiting_payment_orders(self.get_queryset()),
            date_field="shipment__shipped_at",
            # Свежие отгрузки сверху; заказы без записи отгрузки (старые данные) — в конце.
            ordering=(F("shipment__shipped_at").desc(nulls_last=True), "-id"),
        )

    @action(detail=False, methods=["get"], url_path="awaiting-shipment")
    def awaiting_shipment(self, request):
        """«К отгрузке»: неоплаченные заказы отдела, ждущие отгрузки, — принять предоплату."""
        return self._cashier_orders(
            request,
            awaiting_shipment_orders(self.get_queryset()),
            date_field="created_at",
            ordering=("-created_at", "-id"),
        )

    @action(detail=False, methods=["get"], url_path="to-refund")
    def to_refund(self, request):
        """«К возврату»: переплаченные заказы отдела — вернуть клиенту излишек."""
        return self._cashier_orders(
            request,
            overpaid_orders(self.get_queryset()),
            date_field="created_at",
            ordering=("-created_at", "-id"),
            amounts=order_overpaid_by_id,
        )

    @action(detail=True, methods=["post"], url_path="fixate")
    def fixate(self, request, pk=None):
        """Зафиксировать статус и оплату существующего заказа задним числом."""
        from .fixation import OrderFixationSerializer, fixate_order

        params = OrderFixationSerializer(data=request.data)
        params.is_valid(raise_exception=True)
        order = fixate_order(self.get_object(), request.user, **params.validated_data)
        order = self.get_queryset().get(pk=order.pk)
        return Response(OrderSerializer(order, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="to-debt")
    def to_debt(self, request, pk=None):
        """Касса согласует долг по заказу из «Ждут оплаты»."""
        order = move_order_to_debt(self.get_object(), request.user)
        order = self.get_queryset().get(pk=order.pk)
        return Response(OrderSerializer(order, context={"request": request}).data)

    def destroy(self, request, *args, **kwargs):
        """Удаление = отправка в корзину (soft-delete). Заказ исчезает из отчётов
        и списков, но сохраняется и может быть восстановлен."""
        soft_delete_order(self.get_object(), request.user)
        return Response(status=204)

    def _deleted_scoped(self):
        """Удалённые заказы (корзина), доступные редактору заказов."""
        deleted = scope_by_client_department(
            Order.all_objects.deleted(),
            self.request.user,
            client_path="client",
        )
        return with_order_api_relations(deleted)

    @action(detail=False, methods=["get"], url_path="trash")
    def trash(self, request):
        """Корзина: удалённые заказы, доступные для восстановления."""
        qs = self._deleted_scoped().order_by("-deleted_at")
        return Response(OrderSerializer(qs, many=True, context={"request": request}).data)

    @action(detail=False, methods=["get"], url_path="trash-preview")
    def trash_preview(self, request):
        """Small archive dock projection; full history loads only on demand."""
        base = self._deleted_scoped().order_by("-deleted_at")
        rows = base[:4]
        return Response({
            "count": base.count(),
            "results": OrderSerializer(
                rows,
                many=True,
                context={"request": request},
            ).data,
        })

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
        return Response(status=204)

    @action(detail=True, methods=["post"], url_path="payments")
    def payments(self, request, pk=None):
        """Начало цепочки: счёт — requested, прочие способы — received (деньги приняты)."""
        order = self.get_object()
        method = request.data.get("method") or "cash"
        # POS кассы: Kaspi QR через ApiPay. Без channel кассовый kaspi — это
        # отметка о собственном терминале и подтверждается сразу, как раньше.
        channel = request.data.get("channel")
        if method == "kaspi" and channel not in (None, "", "qr"):
            raise ValidationError({
                "detail": "Недопустимый канал оплаты.",
                "code": "invalid_payment_channel",
            })
        if method == "kaspi" and channel == "qr":
            payment = _issue_staff_qr_payment(
                order, request.data.get("amount"), request.user
            )
            return Response(PaymentSerializer(payment, context={"request": request}).data, status=201)
        phone_number = request.data.get("phone_number")
        if method == "invoice":
            phone_number = normalize_phone(
                phone_number or order.client.phone
            )
        payment = record_staff_payment(
            order, request.data.get("amount"), request.user,
            method=method,
            note=request.data.get("note") or "")
        if method in PROVIDER_METHOD_CHANNELS:
            try:
                _issue_provider_payment(
                    payment,
                    phone_number=phone_number,
                    user=request.user,
                )
            except (ApiPayAPIError, ApiPayConfigurationError, ValidationError) as exc:
                reject_unissued_payment(payment, request.user)
                raise provider_error(exc) from exc
            payment.refresh_from_db()
        return Response(PaymentSerializer(payment, context={"request": request}).data, status=201)

    @action(detail=True, methods=["get"], url_path=r"payments/(?P<pid>\d+)")
    def payment_detail(self, request, pk=None, pid=None):
        """Статус одной оплаты заказа: POS опрашивает его, пока клиент платит по QR."""
        order = self.get_object()
        payment = get_object_or_404(
            with_payment_api_relations(Payment.objects.filter(order=order)),
            pk=pid,
        )
        return Response(
            PaymentSerializer(payment, context={"request": request}).data
        )

    def _order_payment(self, pid, queryset=Payment):
        """Оплата заказа из адреса и признак общей очереди кассы.

        Оплату в очереди подтверждения обрабатывает касса любого отдела —
        сервис тогда не сверяет отдел. Прочие оплаты — только отдел клиента.
        """
        payment = get_object_or_404(queryset, pk=pid, order=self.get_object())
        in_queue = Payment.objects.filter(CASHIER_QUEUE_PAYMENT, pk=payment.pk).exists()
        return payment, in_queue

    @action(detail=True, methods=["post"], url_path=r"payments/(?P<pid>\d+)/receive")
    def receive_payment(self, request, pk=None, pid=None):
        payment, in_queue = self._order_payment(pid)
        receive_and_confirm_payment(payment, request.user, any_department=in_queue)
        return Response(OrderSerializer(payment.order, context={"request": request}).data)

    @action(detail=True, methods=["get"], url_path="confirm-context")
    def confirm_context(self, request, pk=None):
        """Окно подтверждения: остаток на складе и можно ли менять номер транспорта.

        Страна клиента — маска пустого поля номера, как в форме заказа.
        """
        order = self.get_object()
        return Response({
            "items": confirm_stock_context(order),
            "transport_locked": transport_locked(order, request.user),
            "client_country": order.client.country,
        })

    @action(detail=True, methods=["post"], url_path="confirm")
    def confirm(self, request, pk=None):
        order = self.get_object()
        serializer = ConfirmOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        order = confirm_order(
            order,
            request.user,
            prices=data.get("prices"),
            department=data["department"],
            quantities=data.get("quantities"),
            truck=data.get("truck_number"),
            trailer=data.get("trailer_number"),
        )
        # confirm_order updates item instances loaded inside the service; the
        # view's prefetched items still contain the old prices until refreshed.
        order.refresh_from_db()
        return Response(OrderSerializer(order, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        order = reject_order(
            self.get_object(), request.user, reason=request.data.get("reason", "")
        )
        return Response(OrderSerializer(order, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path=r"payments/(?P<pid>\d+)/confirm")
    def confirm_payment(self, request, pk=None, pid=None):
        """Подтверждение бухгалтером-кассой: received → confirmed (деньги учтены)."""
        payment, in_queue = self._order_payment(pid)
        accountant_confirm_payment(payment, request.user, any_department=in_queue)
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
        payment, in_queue = self._order_payment(
            pid, Payment.objects.select_related("apipay_invoice"),
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
                any_department=in_queue,
            )
        except ApiPayAPIError as exc:
            raise provider_error(exc) from exc
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

    @action(detail=True, methods=["post"],
            url_path=r"status-requests/(?P<rid>\d+)/approve")
    def approve_status(self, request, pk=None, rid=None):
        req = get_object_or_404(
            StatusChangeRequest, pk=rid, order=self.get_object())
        req = approve_status_change(req, request.user)
        return Response(StatusChangeRequestSerializer(req).data)

    @action(detail=True, methods=["post"],
            url_path=r"status-requests/(?P<rid>\d+)/reject")
    def reject_status(self, request, pk=None, rid=None):
        req = get_object_or_404(
            StatusChangeRequest, pk=rid, order=self.get_object())
        req = reject_status_change(req, request.user)
        return Response(StatusChangeRequestSerializer(req).data)
