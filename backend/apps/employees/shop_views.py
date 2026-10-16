from django.utils.functional import cached_property
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.pagination import OptInPageNumberPagination
from apps.common.permissions import HasPerm

from .shop import require_shop
from .shop_sales import cancel_shop_sale, place_shop_sale, sale_catalog, sale_orders
from .shop_serializers import ShopCatalogSerializer, ShopSaleCreateSerializer, ShopSaleSerializer


class ShopOrderViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Изолированная история продаж из остатка своего магазина."""

    serializer_class = ShopSaleSerializer
    pagination_class = OptInPageNumberPagination

    def get_permissions(self):
        return [HasPerm("orders.create")]

    @cached_property
    def shop(self):
        return require_shop(self.request.user)

    def get_queryset(self):
        return sale_orders(self.shop)

    @action(detail=False, methods=["get"])
    def summary(self, request):
        return Response({
            "name": self.shop.client.name,
            "currency": self.shop.client.currency,
            "client": self.shop.client_id,
            "warehouse_name": self.shop.warehouse.name,
        })

    @action(detail=False, methods=["get"])
    def catalog(self, request):
        return Response(ShopCatalogSerializer(
            sale_catalog(self.shop), many=True,
            context={"request": request, "currency": self.shop.client.currency},
        ).data)

    def create(self, request):
        serializer = ShopSaleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sale = place_shop_sale(request.user, **serializer.validated_data)
        return self._sale_response(sale, status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._sale_response(cancel_shop_sale(request.user, pk))

    def _sale_response(self, sale, status_code=status.HTTP_200_OK):
        sale = self.get_queryset().get(pk=sale.pk)
        return Response(self.get_serializer(sale).data, status=status_code)
