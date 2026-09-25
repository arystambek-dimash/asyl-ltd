from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.permissions import PermViewSetMixin

from .models import StockItem, Warehouse
from .serializers import (
    StockAdjustmentSerializer,
    StockItemSerializer,
    StockTransferSerializer,
    WarehouseSerializer,
)
from .services import (
    adjust_stock,
    resolve_warehouse,
    transfer_stock,
)


class WarehouseViewSet(
    PermViewSetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = WarehouseSerializer
    queryset = Warehouse.objects.all()
    required_perms = {
        "list": "warehouse.view",
        "retrieve": "warehouse.view",
        "create": "warehouse.adjust",
        "update": "warehouse.adjust",
        "partial_update": "warehouse.adjust",
    }


class StockViewSet(
    PermViewSetMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = StockItemSerializer
    required_perms = {
        "list": "warehouse.view",
        "adjust": "warehouse.adjust",
        "transfer": "warehouse.adjust",
    }

    def get_queryset(self):
        queryset = StockItem.objects.select_related("product", "warehouse").order_by(
            "product__name", "product__weight_kg"
        )
        raw_warehouse = self.request.query_params.get("warehouse")
        if raw_warehouse:
            warehouse = resolve_warehouse(raw_warehouse, require_active=False)
            queryset = queryset.filter(warehouse=warehouse)
        return queryset

    @action(detail=False, methods=["post"])
    def adjust(self, request):
        serializer = StockAdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = adjust_stock(
            serializer.validated_data["product"],
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
    def transfer(self, request):
        serializer = StockTransferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = transfer_stock(
            serializer.validated_data["product"],
            serializer.validated_data["bags"],
            request.user,
            from_warehouse=serializer.validated_data["from_warehouse"],
            to_warehouse=serializer.validated_data["to_warehouse"],
            note=serializer.validated_data.get("note", ""),
        )
        return Response(
            {
                "transfer_id": str(result["transfer_id"]),
                "product": result["product"],
                "bags": result["bags"],
                "source": StockItemSerializer(result["source"]).data,
                "destination": StockItemSerializer(result["destination"]).data,
            }
        )
