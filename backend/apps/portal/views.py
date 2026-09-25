from io import BytesIO

from django.db.models import Exists, OuterRef, Prefetch
from django.http import FileResponse
from django.utils.functional import cached_property
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.catalog.models import ClientPrice, Product
from apps.clients.models import Client, Store
from apps.clients.serializers import StoreSerializer
from apps.common.money import CURRENCY_CODES, DEFAULT_CURRENCY
from apps.common.permissions import IsClientUser
from apps.eventlog.services import log_event
from apps.orders.apipay import (
    CLOSED_INVOICE_STATUSES,
    ApiPayAPIError,
    ApiPayConfigurationError,
    cancel_invoice,
    provider_error,
    start_order_payment,
)
from apps.orders.invoices import build_payment_receipt_pdf
from apps.orders.models import Order, Payment
from apps.orders.serializers import TransportNumbersSerializer
from apps.orders.services import (
    client_release_invoice_error,
    create_client_payment,
    release_client_payment,
    request_client_debt,
)
from apps.orders.transport import set_order_transport
from apps.warehouse.models import StockItem, Warehouse
from config.throttles import PortalOrderCreateRateThrottle

from .exceptions import Conflict
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

    @cached_property
    def _client(self):
        # Один запрос на список: id — для цен клиента, валюта — по умолчанию.
        return Client.objects.filter(user=self.request.user).first()

    @cached_property
    def _currency(self):
        requested = (self.request.query_params.get("currency") or "").upper()
        if requested:
            if requested not in CURRENCY_CODES:
                raise ValidationError({"currency": "Выберите KZT или USD."})
            return requested
        return getattr(self._client, "currency", "") or DEFAULT_CURRENCY

    def get_queryset(self):
        price_qs = ClientPrice.objects.filter(
            client_id=getattr(self._client, "pk", None), currency=self._currency)
        default_warehouse = (
            Warehouse.objects.filter(is_default=True, is_active=True)
            .only("id")
            .order_by("id")
            .first()
        )
        if default_warehouse is None:
            return Product.objects.none()

        in_stock = StockItem.objects.filter(
            warehouse_id=default_warehouse.pk, product=OuterRef("pk"), bags__gt=0)

        return (Product.objects.filter(Exists(in_stock), is_active=True)
                .prefetch_related(Prefetch(
            "client_prices", queryset=price_qs,
            to_attr="portal_client_prices"))
                .order_by("name", "color", "weight_kg"))

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["currency"] = self._currency
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
            .select_related("store", "client__user", "truck_number_set_by", "shipment")
            .prefetch_related(
                "items__product",
                "shipment__wagons",
                Prefetch(
                    "payments",
                    queryset=Payment.objects.select_related("apipay_invoice"),
                ),
            )
            # У Order нет Meta.ordering: «Мои заказы» — новые сверху.
            .order_by("-created_at", "-pk")
        )

    def _order_response(self, order, status_code=status.HTTP_200_OK):
        # Ответ действия — заказ, перечитанный со всеми prefetch списка.
        order = self.get_queryset().get(pk=order.pk)
        return Response(self.get_serializer(order).data, status=status_code)

    @action(detail=True, methods=["post"], url_path="pay")
    def pay(self, request, pk=None):
        order = self.get_object()
        method = request.data.get("method")
        if method == "debt":
            request_client_debt(order, request.user)
        elif method in ("kaspi", "invoice"):
            try:
                start_order_payment(
                    order,
                    request.user,
                    payment_method=method,
                    phone_number=request.data.get("phone_number"),
                    amount=request.data.get("amount"),
                )
            except (ApiPayAPIError, ApiPayConfigurationError) as exc:
                raise provider_error(exc, for_client=True) from exc
        else:
            create_client_payment(
                order, method, request.user, amount=request.data.get("amount")
            )
        return self._order_response(order, status.HTTP_201_CREATED)

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
        # До отмены счёта по номеру: полученные деньги не отпускаем.
        if error := client_release_invoice_error(invoice):
            raise ValidationError(error)
        if (
                invoice is not None
                and invoice.channel == "phone"
                and invoice.status not in CLOSED_INVOICE_STATUSES
        ):
            try:
                cancel_invoice(invoice, user=request.user)
            except ApiPayAPIError as exc:
                raise provider_error(exc, for_client=True) from exc
            if invoice.status not in CLOSED_INVOICE_STATUSES:
                # ApiPay may acknowledge cancellation asynchronously. Keep the
                # amount reserved until webhook/reconciliation proves that the
                # remotely payable invoice is closed.
                return self._order_response(order, status.HTTP_202_ACCEPTED)
        release_client_payment(payment, request.user)
        return self._order_response(order)

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
        return self._order_response(order)

    @action(detail=True, methods=["patch"], url_path="truck")
    def truck(self, request, pk=None):
        order = self.get_object()
        if order.status != "confirmed":
            raise Conflict({"detail": "Номер транспорта доступен после подтверждения заказа",
                            "code": "invalid_status"})
        serializer = TransportNumbersSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        truck = serializer.validated_data.get("truck_number", "")
        if not truck:
            raise ValidationError({"detail": "Введите номер транспорта", "code": "empty"})
        set_order_transport(
            order, request.user, truck=truck, trailer=serializer.validated_data.get("trailer_number"))
        return self._order_response(order)
