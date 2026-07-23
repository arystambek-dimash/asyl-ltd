from django.conf import settings
from django.db.models import Prefetch
from django.http import FileResponse
from django.utils import timezone
from io import BytesIO
from rest_framework import viewsets, mixins
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, APIException
from apps.common.permissions import IsClientUser
from apps.catalog.models import ClientPrice, Product
from apps.clients.models import Client, Store
from apps.clients.serializers import StoreSerializer
from apps.orders.models import Order, Payment
from apps.orders.invoices import build_invoice_pdf, build_payment_receipt_pdf
from apps.orders.services import create_client_payment, request_client_debt, set_truck_number
from apps.orders.apipay import (
    ApiPayAPIError, ApiPayConfigurationError, start_order_payment,
)
from apps.eventlog.services import log_event
from config.throttles import PortalOrderCreateRateThrottle
from .serializers import CatalogProductSerializer, PortalOrderSerializer


class PortalStoreViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = StoreSerializer
    permission_classes = [IsClientUser]

    def get_queryset(self):
        return Store.objects.filter(client__user=self.request.user)


class Conflict(APIException):
    status_code = 409
    default_code = "conflict"


class PaymentProviderError(APIException):
    status_code = 502
    default_code = "payment_provider_error"


class PortalCatalogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = CatalogProductSerializer
    permission_classes = [IsClientUser]
    # Клиент видит активные товары, даже если складская карточка ещё не создана.
    # Остаток в таком случае показываем как 0, а заказ дальше обрабатывается текущим флоу.
    def _currency(self):
        requested = (self.request.query_params.get("currency") or "").upper()
        if requested:
            if requested not in dict(Order.CURRENCIES):
                raise ValidationError({"currency": "Выберите KZT или USD."})
            return requested
        return (Client.objects.filter(user=self.request.user)
                .values_list("currency", flat=True).first() or "KZT")

    def get_queryset(self):
        client_id = (Client.objects.filter(user=self.request.user)
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
        # paid_total и has_pending_payment обходят оплаты — грузим заранее.
        return (
            Order.objects.filter(client__user=self.request.user)
            .select_related("store", "client")
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
        elif method == "kaspi":
            try:
                invoice = start_order_payment(
                    order,
                    request.user,
                    channel=request.data.get("channel") or "qr",
                    phone_number=request.data.get("phone_number"),
                )
            except ApiPayConfigurationError as exc:
                raise PaymentProviderError({
                    "detail": "Онлайн-оплата Kaspi временно не настроена.",
                    "code": "apipay_not_configured",
                }) from exc
            except ApiPayAPIError as exc:
                raise PaymentProviderError({
                    "detail": exc.message,
                    "code": exc.error_code,
                }) from exc
        else:
            create_client_payment(order, method, request.user)
        # get_object() comes from a queryset with prefetched payments.  A
        # payment created by the service does not invalidate that cache, and
        # without this the response incorrectly says that no payment is in
        # progress until the next request.
        order._prefetched_objects_cache.pop("payments", None)
        data = self.get_serializer(order).data
        if method == "kaspi":
            data["payment_redirect_url"] = invoice.qr_token_url or None
        return Response(data, status=201)

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


@api_view(["GET"])
@permission_classes([IsClientUser])
def payment_info(request):
    return Response(settings.PORTAL_PAYMENT_INFO)
