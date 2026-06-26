from django.conf import settings
from rest_framework import viewsets, mixins
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, APIException
from apps.accounts.permissions import IsClientUser
from apps.catalog.models import Product
from apps.clients.models import Store
from apps.clients.serializers import StoreSerializer
from apps.orders.models import Order
from apps.orders.services import create_client_payment, set_truck_number
from apps.eventlog.services import log_event
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
    queryset = (Product.objects.filter(is_active=True)
                .select_related("stock")
                .order_by("name", "color", "weight_kg"))


class PortalOrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                         mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = PortalOrderSerializer
    permission_classes = [IsClientUser]

    def get_queryset(self):
        return Order.objects.filter(
            client__user=self.request.user
        ).prefetch_related("items__product")

    @action(detail=True, methods=["post"], url_path="pay")
    def pay(self, request, pk=None):
        order = self.get_object()
        create_client_payment(order, request.data.get("method"), request.user)
        return Response(self.get_serializer(order).data, status=201)

    @action(detail=True, methods=["post"], url_path="request-debt")
    def request_debt(self, request, pk=None):
        order = self.get_object()
        if order.status != "shipped":
            raise ValidationError({"detail": "Долг фиксируется после отгрузки",
                                   "code": "invalid_status"})
        order.debt_requested = True
        order.save(update_fields=["debt_requested"])
        log_event("debt_override", "Клиент запросил долг", user=request.user, order=order)
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
