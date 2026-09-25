from django.shortcuts import get_object_or_404
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.clients.models import Client
from apps.common.money import CURRENCY_CODES, DEFAULT_CURRENCY
from apps.common.permissions import PermAPIViewMixin, PermViewSetMixin
from apps.sales.access import scope_by_client_department

from .models import ClientPrice, Product, ProductAlias
from .photos import remove_product_photo, set_product_photo
from .serializers import ProductSerializer
from .services import archive_product, forget_product_alias, remember_product_alias, restore_product


class ProductViewSet(
    PermViewSetMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = ProductSerializer
    required_perms = {
        # Склад добавляет товар на свой склад из каталога, не имея доступа к разделу «Каталог».
        "list": ("catalog.view", "warehouse.adjust"),
        "retrieve": "catalog.view",
        "create": "catalog.create", "update": "catalog.edit",
        "partial_update": "catalog.edit",
        "archive": "catalog.edit", "restore": "catalog.edit",
        "photo": "catalog.edit",
        "add_alias": "catalog.edit", "remove_alias": "catalog.edit",
    }

    def get_queryset(self):
        qs = Product.objects.prefetch_related("aliases")
        if self.request.query_params.get("archived") in ("1", "true"):
            return qs.filter(is_active=False)
        return qs.filter(is_active=True)

    def _any_product(self, pk):
        obj = get_object_or_404(Product.objects.prefetch_related("aliases"), pk=pk)
        self.check_object_permissions(self.request, obj)
        return obj

    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, pk=None):
        product = archive_product(self._any_product(pk), request.user)
        return Response(ProductSerializer(product, context={"request": request}).data)

    @action(
        detail=True,
        methods=["post", "delete"],
        url_path="photo",
        parser_classes=[MultiPartParser],
    )
    def photo(self, request, pk=None):
        """POST multipart ``photo`` заменяет фото товара, DELETE убирает его."""
        product = self._any_product(pk)
        if request.method == "DELETE":
            product = remove_product_photo(product, request.user)
        else:
            product = set_product_photo(product, request.FILES.get("photo"), request.user)
        return Response(ProductSerializer(product, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="aliases")
    def add_alias(self, request, pk=None):
        """Код товара в отчётах о вагонах. Код другого товара переносится только с ``move``."""
        product = self._any_product(pk)
        code = str(request.data.get("code") or "")
        remember_product_alias(code, product, request.user, move=request.data.get("move") is True)
        return Response(ProductSerializer(self._any_product(pk), context={"request": request}).data)

    @action(detail=True, methods=["delete"], url_path=r"aliases/(?P<alias_id>[0-9]+)")
    def remove_alias(self, request, pk=None, alias_id=None):
        product = self._any_product(pk)
        forget_product_alias(get_object_or_404(ProductAlias, pk=alias_id, product=product), request.user)
        return Response(ProductSerializer(self._any_product(pk), context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path="restore")
    def restore(self, request, pk=None):
        product = restore_product(self._any_product(pk), request.user)
        return Response(ProductSerializer(product, context={"request": request}).data)


class ClientPricesView(PermAPIViewMixin, APIView):
    required_perms = {
        "get": ["orders.create", "orders.edit"],
    }

    def get(self, request):
        # HEAD probes the endpoint/permission contract, not a concrete price
        # lookup. Returning no body also avoids turning it into an ID oracle.
        if request.method == "HEAD":
            return Response()
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
        if currency and currency not in CURRENCY_CODES:
            raise ValidationError({"currency": "Выберите KZT или USD."})
        client = get_object_or_404(
            scope_by_client_department(
                Client.objects.only("id", "currency"),
                request.user,
            ),
            pk=client_id,
        )
        qs = ClientPrice.objects.filter(client=client)

        if not currency:
            currency = client.currency or DEFAULT_CURRENCY

        qs = qs.filter(currency=currency)
        return Response({
            str(price.product_id): str(price.price)
            for price in qs
        })
