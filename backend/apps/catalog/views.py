from apps.common.permissions import HasPerm, PermViewSetMixin
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Product, ClientPrice
from .serializers import ProductSerializer
from .services import archive_product, restore_product


class ProductViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    required_perms = {
        "list": ("catalog.view", "dept2.view"),
        "retrieve": ("catalog.view", "dept2.view"),
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


class ClientPricesView(APIView):
    """Текущие цены клиента: {product_id: price} — для предзаполнения формы заказа."""

    def get_permissions(self):
        return [HasPerm("orders.create", "dept2.create")]

    def get(self, request):
        client_id = request.query_params.get("client")
        qs = ClientPrice.objects.all()
        if client_id:
            qs = qs.filter(client_id=client_id)
        return Response({str(cp.product_id): str(cp.price) for cp in qs})
