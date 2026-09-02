from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.catalog.models import Product
from apps.common.permissions import PermViewSetMixin

from .models import StockItem, StockMovement, Warehouse
from .serializers import (
    StockAdjustmentSerializer,
    StockItemSerializer,
    StockMovementSerializer,
    StockReceiptSerializer,
    WarehouseSerializer,
)
from .services import (
    DEFAULT_WAREHOUSE_CODE,
    adjust_stock,
    delete_stock_item,
    receive_stock,
    resolve_warehouse,
)


class WarehouseViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    serializer_class = WarehouseSerializer
    queryset = Warehouse.objects.all()
    required_perms = {
        "list": "warehouse.view",
        "retrieve": "warehouse.view",
        "create": "warehouse.adjust",
        "update": "warehouse.adjust",
        "partial_update": "warehouse.adjust",
        "destroy": "warehouse.adjust",
    }

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        # Match the serializer's global lock order so promotion and deletion of
        # the same secondary warehouse cannot cross in flight.
        Warehouse.objects.select_for_update().get(code=DEFAULT_WAREHOUSE_CODE)
        warehouse = Warehouse.objects.select_for_update().get(
            pk=self.get_object().pk
        )
        if warehouse.code == DEFAULT_WAREHOUSE_CODE or warehouse.is_default:
            raise ValidationError(
                {
                    "detail": "Основной склад нельзя удалить",
                    "code": "default_warehouse",
                }
            )
        try:
            warehouse.delete()
        except ProtectedError as exc:
            raise ValidationError(
                {
                    "detail": "Склад используется и не может быть удалён",
                    "code": "warehouse_in_use",
                }
            ) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)


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
        queryset = StockItem.objects.select_related("product", "warehouse").order_by(
            "product__name", "product__weight_kg"
        )
        raw_warehouse = self.request.query_params.get("warehouse")
        if raw_warehouse:
            warehouse = resolve_warehouse(raw_warehouse, require_active=False)
            if warehouse.code == DEFAULT_WAREHOUSE_CODE:
                queryset = queryset.filter(
                    Q(warehouse=warehouse) | Q(warehouse__isnull=True)
                )
            else:
                queryset = queryset.filter(warehouse=warehouse)
        return queryset

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
            warehouse=serializer.validated_data.get("warehouse"),
        )
        return Response(
            StockItemSerializer(item).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"])
    def receive(self, request):
        serializer = StockReceiptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = serializer.validated_data["product"]
        receipt = receive_stock(
            product,
            serializer.validated_data["bags"],
            request.user,
            warehouse=serializer.validated_data.get("warehouse"),
        )
        return Response(
            StockReceiptSerializer(receipt).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["get"])
    def movements(self, request):
        queryset = StockMovement.objects.select_related(
            "warehouse", "product", "created_by"
        )
        raw_warehouse = request.query_params.get("warehouse")
        if raw_warehouse:
            warehouse = resolve_warehouse(raw_warehouse, require_active=False)
            if warehouse.code == DEFAULT_WAREHOUSE_CODE:
                queryset = queryset.filter(
                    Q(warehouse=warehouse) | Q(warehouse__isnull=True)
                )
            else:
                queryset = queryset.filter(warehouse=warehouse)
        product = request.query_params.get("product")
        if product:
            queryset = queryset.filter(product_id=product)
        return Response(StockMovementSerializer(queryset, many=True).data)
