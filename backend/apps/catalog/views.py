from rest_framework import viewsets
from rest_framework.views import APIView
from rest_framework.response import Response
from apps.rbac.permissions import HasPerm
from apps.rbac.permissions import PermViewSetMixin
from .models import Product, ClientPrice
from .serializers import ProductSerializer

_PERMS = {
    # Просмотр товаров нужен и менеджеру Отдела 2 для составления заявки.
    "list": ("catalog.view", "dept2.view"), "retrieve": ("catalog.view", "dept2.view"),
    "create": "catalog.create", "update": "catalog.edit",
    "partial_update": "catalog.edit", "destroy": "catalog.delete",
}


class ProductViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Product.objects.select_related("stock")
    serializer_class = ProductSerializer
    required_perms = _PERMS


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
