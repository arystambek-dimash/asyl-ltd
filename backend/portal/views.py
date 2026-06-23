from django.conf import settings
from rest_framework import viewsets, mixins
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, APIException
from accounts.permissions import IsClientUser
from catalog.models import Product
from orders.models import Order
from orders.services import create_client_payment, set_truck_number
from eventlog.services import log_event
from .serializers import CatalogProductSerializer, PortalOrderSerializer


class Conflict(APIException):
    status_code = 409
    default_code = "conflict"


class PortalCatalogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = CatalogProductSerializer
    permission_classes = [IsClientUser]
    queryset = Product.objects.filter(is_active=True)


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
        if order.status != "confirmed":
            raise ValidationError({"detail": "Долг доступен только для подтверждённого заказа",
                                   "code": "invalid_status"})
        order.debt_requested = True
        order.save(update_fields=["debt_requested"])
        log_event("debt_override", "Клиент запросил долг", user=request.user, order=order)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["patch"], url_path="truck")
    def truck(self, request, pk=None):
        order = self.get_object()
        if order.status != "paid":
            raise Conflict({"detail": "Номер КАМАЗа доступен после оплаты",
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
