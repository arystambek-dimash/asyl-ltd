from apps.common.permissions import PermViewSetMixin, PermAPIViewMixin
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Product, ClientPrice
from .serializers import ProductSerializer
from .services import archive_product, restore_product


class ProductViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    required_perms = {
        "list": "catalog.view",
        "retrieve": "catalog.view",
        "create": "catalog.create", "update": "catalog.edit",
        "partial_update": "catalog.edit", "destroy": "catalog.delete",
        "archive": "catalog.edit", "restore": "catalog.edit",
    }

    def get_queryset(self):
        qs = Product.objects.select_related("stock")
        if self.request.query_params.get("archived") in ("1", "true"):
            return qs.filter(is_active=False)
        return qs.filter(is_active=True)

    def destroy(self, request, *args, **kwargs):
        archive_product(self.get_object(), request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _any_product(self, pk):
        from django.shortcuts import get_object_or_404
        obj = get_object_or_404(Product, pk=pk)
        self.check_object_permissions(self.request, obj)
        return obj

    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, pk=None):
        product = archive_product(self._any_product(pk), request.user)
        return Response(ProductSerializer(product, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="restore")
    def restore(self, request, pk=None):
        product = restore_product(self._any_product(pk), request.user)
        return Response(ProductSerializer(product, context={"request": request}).data)


class ClientPricesView(PermAPIViewMixin, APIView):
    required_perms = {
        "get": ["orders.create", "orders.edit"],
    }

    def get(self, request):
        raw_client_id = request.query_params.get("client")
        if not raw_client_id:
            raise ValidationError({"client": "Выберите клиента."})
        try:
            client_id = int(raw_client_id)
            if client_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            raise ValidationError({"client": "Некорректный клиент."})
        currency = (request.query_params.get("currency") or "").upper()
        if currency and currency not in dict(ClientPrice.CURRENCIES):
            raise ValidationError({"currency": "Выберите KZT или USD."})
        qs = ClientPrice.objects.filter(client_id=client_id)

        if not currency:
            currency = (
                    qs.values_list("client__currency", flat=True).first()
                    or "KZT"
            )

        qs = qs.filter(currency=currency)
        return Response({
            str(price.product_id): str(price.price)
            for price in qs
        })
