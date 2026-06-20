from rest_framework import viewsets, mixins
from rest_framework.response import Response
from accounts.permissions import IsStaff, IsManager
from .models import StockItem
from .serializers import StockItemSerializer, StockReceiptSerializer
from .services import receive_stock
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
