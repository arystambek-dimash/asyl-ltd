from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from apps.catalog.models import Product
from apps.common.permissions import PermViewSetMixin
from .models import StockItem, StockMovement
from .serializers import (
    StockAdjustmentSerializer,
    StockItemSerializer,
    StockMovementSerializer,
    StockReceiptSerializer,
)
from .services import adjust_stock, delete_stock_item, receive_stock


class StockViewSet(
    PermViewSetMixin,
    mixins.ListModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = StockItemSerializer
    required_perms = {
        "list": "warehouse.view",
        "movements": "warehouse.view",
        "adjust": "warehouse.adjust",
        "receive": "warehouse.adjust",
        "destroy": "warehouse.adjust",
    }

    def get_queryset(self):
        return StockItem.objects.select_related("product").order_by(
            "product__name", "product__weight_kg"
        )

    def _get_product(self, product_id):
        product = Product.objects.filter(pk=product_id).first()
        if product is None:
            raise ValidationError({"product": "Товар не найден"})
        return product

    def perform_destroy(self, instance):
        delete_stock_item(instance, self.request.user)

    @action(detail=False, methods=["post"])
    def adjust(self, request):
        serializer = StockAdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = self._get_product(serializer.validated_data["product"])
        item = adjust_stock(
            product,
            serializer.validated_data["delta"],
            request.user,
            note=serializer.validated_data.get("note", ""),
        )
        return Response(StockItemSerializer(item).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"])
    def receive(self, request):
        serializer = StockReceiptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = serializer.validated_data["product"]
        receipt = receive_stock(product, serializer.validated_data["bags"], request.user)
        return Response(
            StockReceiptSerializer(receipt).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["get"])
    def movements(self, request):
        queryset = StockMovement.objects.select_related("product", "created_by")
        product = request.query_params.get("product")
        if product:
            queryset = queryset.filter(product_id=product)
        return Response(StockMovementSerializer(queryset, many=True).data)
