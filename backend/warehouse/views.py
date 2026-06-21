from rest_framework import viewsets, mixins
from rest_framework.response import Response
from rbac.permissions import PermViewSetMixin
from .models import StockItem, StockMovement
from .serializers import StockItemSerializer, StockReceiptSerializer, StockMovementSerializer
from .services import receive_stock, adjust_stock
from catalog.models import Product


class StockViewSet(PermViewSetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = StockItem.objects.select_related("product", "product__grade", "product__packaging")
    serializer_class = StockItemSerializer
    required_perms = {"list": "warehouse.view"}


class StockReceiptViewSet(PermViewSetMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = StockReceiptSerializer
    required_perms = {"create": "warehouse.adjust"}

    def create(self, request, *args, **kwargs):
        product = Product.objects.get(pk=request.data["product"])
        receipt = receive_stock(product, int(request.data["bags"]), request.user)
        return Response(StockReceiptSerializer(receipt).data, status=201)


class StockAdjustViewSet(PermViewSetMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = StockItemSerializer
    required_perms = {"create": "warehouse.adjust"}

    def create(self, request, *args, **kwargs):
        product = Product.objects.get(pk=request.data["product"])
        item = adjust_stock(product, int(request.data["delta"]), request.user,
                            note=request.data.get("note", ""))
        return Response(StockItemSerializer(item).data, status=201)


class StockMovementViewSet(PermViewSetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = StockMovementSerializer
    required_perms = {"list": "warehouse.view"}

    def get_queryset(self):
        qs = StockMovement.objects.select_related("product", "created_by")
        product = self.request.query_params.get("product")
        return qs.filter(product_id=product) if product else qs
