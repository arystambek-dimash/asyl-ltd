from rest_framework import viewsets, mixins
from rest_framework.response import Response
from accounts.permissions import IsStaff, IsManager
from .models import StockItem, StockMovement
from .serializers import (
    StockItemSerializer, StockReceiptSerializer, StockMovementSerializer,
)
from .services import receive_stock, adjust_stock
from catalog.models import Product


class StockViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = StockItem.objects.select_related("product")
    serializer_class = StockItemSerializer
    permission_classes = [IsStaff]


class StockReceiptViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = StockReceiptSerializer
    permission_classes = [IsManager]

    def create(self, request, *args, **kwargs):
        product = Product.objects.get(pk=request.data["product"])
        receipt = receive_stock(product, int(request.data["bags"]), request.user)
        return Response(StockReceiptSerializer(receipt).data, status=201)


class StockAdjustViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = StockItemSerializer
    permission_classes = [IsManager]

    def create(self, request, *args, **kwargs):
        product = Product.objects.get(pk=request.data["product"])
        item = adjust_stock(
            product, int(request.data["delta"]), request.user,
            note=request.data.get("note", ""),
        )
        return Response(StockItemSerializer(item).data, status=201)


class StockMovementViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = StockMovementSerializer
    permission_classes = [IsStaff]

    def get_queryset(self):
        qs = StockMovement.objects.select_related("product", "created_by")
        product = self.request.query_params.get("product")
        if product:
            qs = qs.filter(product_id=product)
        return qs
