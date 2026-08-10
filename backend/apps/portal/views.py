from io import BytesIO

from apps.catalog.models import ClientPrice, Product
from apps.clients.models import Client, Store
from apps.clients.serializers import StoreSerializer
from apps.common.permissions import IsClientUser
from apps.eventlog.services import log_event
from apps.orders.apipay import (
    MONEY_RECEIVED_INVOICE_STATUSES,
    ApiPayAPIError, ApiPayConfigurationError, cancel_invoice,
    start_order_payment,
)
from apps.orders.invoices import build_invoice_pdf, build_payment_receipt_pdf
from apps.orders.models import Order, Payment
from apps.orders.services import (
    create_client_payment, release_client_payment, request_client_debt,
    set_truck_number,
)
from config.throttles import PortalOrderCreateRateThrottle
from django.db.models import Prefetch
from django.http import FileResponse
from django.utils import timezone
from rest_framework import status, viewsets, mixins
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .exceptions import Conflict, PaymentProviderError
from .serializers import CatalogProductSerializer, PortalOrderSerializer


class PortalStoreViewSet(
    mixins.ListModelMixin,
    viewsets.GenericViewSet
):
    serializer_class = StoreSerializer
    permission_classes = [IsClientUser]

    def get_queryset(self):
        return Store.objects.filter(
            client__user=self.request.user
        ).select_related("client__user")


class PortalCatalogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = CatalogProductSerializer
    permission_classes = [IsClientUser]

    def _currency(self):
        requested = (self.request.query_params.get("currency") or "").upper()
        if requested:
            if requested not in dict(Order.CURRENCIES):
                raise ValidationError({"currency": "Выберите KZT или USD."})
            return requested
        return (Client.objects.filter(user=self.request.user)
                .values_list("currency", flat=True).first() or "KZT")

    def get_queryset(self):
        client_id = (
            Client.objects.filter(
                user=self.request.user)
            .values_list("id", flat=True).first())
        price_qs = ClientPrice.objects.filter(
            client_id=client_id, currency=self._currency())
        return (Product.objects.filter(is_active=True)
                .select_related("stock")
                .prefetch_related(Prefetch(
            "client_prices", queryset=price_qs,
            to_attr="portal_client_prices"))
                .order_by("name", "color", "weight_kg"))

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["currency"] = self._currency()
        return context


class PortalOrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                         mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = PortalOrderSerializer
    permission_classes = [IsClientUser]

    def get_throttles(self):
        throttles = super().get_throttles()
        if self.action == "create":
            throttles.append(PortalOrderCreateRateThrottle())
        return throttles

    def get_queryset(self):
        return (
            Order.objects.filter(client__user=self.request.user)
            .select_related("store", "client__user")
            .prefetch_related(
                "items__product",
                Prefetch(
                    "payments",
                    queryset=Payment.objects.select_related("apipay_invoice"),
                ),
            )
        )

    @action(detail=True, methods=["post"], url_path="pay")
    def pay(self, request, pk=None):
        order = self.get_object()
        method = request.data.get("method")
        if method == "debt":
            request_client_debt(order, request.user)
        elif method in ("kaspi", "invoice"):
            try:
                invoice = start_order_payment(
                    order,
                    request.user,
                    channel="qr" if method == "kaspi" else "phone",
                    phone_number=request.data.get("phone_number"),
                    payment_method=method,
                    amount=request.data.get("amount"),
                )
            except ApiPayConfigurationError as exc:
                raise PaymentProviderError({
                    "detail": "Счёт на оплату временно недоступен.",
                    "code": "apipay_not_configured",
                }) from exc
            except ApiPayAPIError as exc:
                raise PaymentProviderError({
                    "detail": exc.message,
                    "code": exc.error_code,
                }) from exc
        else:
            create_client_payment(
                order, method, request.user, amount=request.data.get("amount")
            )
        order._prefetched_objects_cache.pop("payments", None)
        data = self.get_serializer(order).data
        if method == "kaspi":
            data["payment_redirect_url"] = invoice.qr_token_url or None
        return Response(data, status=201)

    @action(
        detail=True, methods=["post"],
        url_path=r"payments/(?P<payment_id>\d+)/release",
    )
    def release_payment(self, request, pk=None, payment_id=None):
        order = self.get_object()
        try:
            payment = order.payments.get(
                pk=payment_id,
                recorded_by=request.user,
            )
        except Payment.DoesNotExist as exc:
            raise ValidationError({
                "detail": "Эту заявку нельзя изменить из кабинета клиента.",
                "code": "payment_not_found",
            }) from exc
        invoice = getattr(payment, "apipay_invoice", None)
        if (
                invoice is not None
                and invoice.status in MONEY_RECEIVED_INVOICE_STATUSES
        ):
            raise ValidationError({
                "detail": (
                    "Платёж уже получен и обрабатывается. Обновите страницу."
                ),
                "code": "payment_already_paid",
            })
        if (
                invoice is not None
                and invoice.channel == "phone"
                and invoice.status not in ("cancelled", "expired", "error", "superseded")
        ):
            try:
                cancel_invoice(invoice)
            except ApiPayAPIError as exc:
                raise PaymentProviderError({
                    "detail": exc.message,
                    "code": exc.error_code,
                }) from exc
            if invoice.status not in (
                    "cancelled", "expired", "error", "superseded",
            ):
                # ApiPay may acknowledge cancellation asynchronously. Keep the
                # amount reserved until webhook/reconciliation proves that the
                # remotely payable invoice is closed.
                order._prefetched_objects_cache.pop("payments", None)
                return Response(
                    self.get_serializer(order).data,
                    status=status.HTTP_202_ACCEPTED,
                )
        release_client_payment(payment, request.user)
        order._prefetched_objects_cache.pop("payments", None)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["get"], url_path="invoice")
    def invoice(self, request, pk=None):
        order = self.get_object()
        if order.status != "shipped" or order.payment_method != "invoice":
            raise ValidationError({
                "detail": "Счет доступен после отгрузки и выбора способа «Счет на оплату»",
                "code": "invoice_not_available",
            })
        missing = []
        if not order.client.iin.strip():
            missing.append("ИИН/БИН")
        if not (order.client.company_name.strip() or order.client.name):
            missing.append("название ТОО / ИП")
        if missing:
            raise ValidationError({
                "detail": "Для счета заполните реквизиты клиента: " + ", ".join(missing),
                "code": "client_requisites_missing",
            })
        payment = order.payments.filter(
            method="invoice", status__in=("requested", "received", "confirmed")
        ).order_by("-paid_at").first()
        if payment is None:
            raise ValidationError({
                "detail": "Сначала выберите способ оплаты «Счет на оплату»",
                "code": "invoice_payment_missing",
            })
        pdf = build_invoice_pdf(order)
        log_event(
            "payment", f"Счет на оплату №{order.id} сформирован",
            user=request.user, order=order,
            payload={"payment_id": payment.id, "method": "invoice", "action": "invoice_generated"},
        )
        filename = f"schet_na_oplatu_{order.id}_ot_{timezone.localdate():%d.%m.%Y}.pdf"
        return FileResponse(BytesIO(pdf), content_type="application/pdf",
                            as_attachment=True, filename=filename)

    @action(detail=True, methods=["get"], url_path="receipt")
    def receipt(self, request, pk=None):
        order = self.get_object()
        payment = order.payments.filter(
            status="confirmed"
        ).order_by("-confirmed_at", "-paid_at").first()
        if payment is None:
            raise ValidationError({
                "detail": "Квитанция доступна только после подтверждения оплаты.",
                "code": "receipt_not_available",
            })
        pdf = build_payment_receipt_pdf(payment)
        log_event(
            "payment", f"Квитанция PAY-{payment.id:06d} скачана клиентом",
            user=request.user, order=order,
            payload={
                "payment_id": payment.id,
                "action": "payment_receipt_downloaded",
            },
        )
        return FileResponse(
            BytesIO(pdf), content_type="application/pdf", as_attachment=True,
            filename=f"receipt_order_{order.id}.pdf",
        )

    @action(detail=True, methods=["post"], url_path="request-debt")
    def request_debt(self, request, pk=None):
        order = self.get_object()
        request_client_debt(order, request.user)
        order._prefetched_objects_cache.pop("payments", None)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["patch"], url_path="truck")
    def truck(self, request, pk=None):
        order = self.get_object()
        if order.status != "confirmed":
            raise Conflict({"detail": "Номер КАМАЗа доступен после подтверждения заказа",
                            "code": "invalid_status"})
        value = (request.data.get("truck_number") or "").strip()
        if not value:
            raise ValidationError({"detail": "Введите номер КАМАЗа", "code": "empty"})
        set_truck_number(order, value, request.user)
        return Response(self.get_serializer(order).data)
