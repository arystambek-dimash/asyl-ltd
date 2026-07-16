from django.conf import settings
from django.db.models import Prefetch
from rest_framework import viewsets, mixins
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, APIException
from apps.common.permissions import IsClientUser
from apps.catalog.models import ClientPrice, Product
from apps.clients.models import Client, Store
from apps.clients.serializers import StoreSerializer
from apps.orders.models import Order
from apps.orders.services import create_client_payment, request_client_debt, set_truck_number
from .serializers import CatalogProductSerializer, PortalOrderSerializer


class PortalStoreViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = StoreSerializer
    permission_classes = [IsClientUser]

    def get_queryset(self):
        return Store.objects.filter(client__user=self.request.user)


class Conflict(APIException):
    status_code = 409
    default_code = "conflict"


class PortalCatalogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = CatalogProductSerializer
    permission_classes = [IsClientUser]
    # Клиент видит активные товары, даже если складская карточка ещё не создана.
    # Остаток в таком случае показываем как 0, а заказ дальше обрабатывается текущим флоу.
    def get_queryset(self):
        client_id = (Client.objects.filter(user=self.request.user)
                     .values_list("id", flat=True).first())
        price_qs = ClientPrice.objects.filter(client_id=client_id)
        return (Product.objects.filter(is_active=True)
                .select_related("stock")
                .prefetch_related(Prefetch(
                    "client_prices", queryset=price_qs,
                    to_attr="portal_client_prices"))
                .order_by("name", "color", "weight_kg"))


class PortalOrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                         mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = PortalOrderSerializer
    permission_classes = [IsClientUser]

    def get_queryset(self):
        # paid_total и has_pending_payment обходят оплаты — грузим заранее.
        return (
            Order.objects.filter(client__user=self.request.user)
            .select_related("store")
            .prefetch_related("items__product", "payments")
        )

    @action(detail=True, methods=["post"], url_path="pay")
    def pay(self, request, pk=None):
        order = self.get_object()
        method = request.data.get("method")
        if method == "debt":
            request_client_debt(order, request.user)
        else:
            create_client_payment(order, method, request.user)
        # get_object() comes from a queryset with prefetched payments.  A
        # payment created by the service does not invalidate that cache, and
        # without this the response incorrectly says that no payment is in
        # progress until the next request.
        order._prefetched_objects_cache.pop("payments", None)
        return Response(self.get_serializer(order).data, status=201)

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
