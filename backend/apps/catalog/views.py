from rest_framework import viewsets
from apps.rbac.permissions import PermViewSetMixin
from .models import Product
from .serializers import ProductSerializer

_PERMS = {
    "list": "catalog.view", "retrieve": "catalog.view",
    "create": "catalog.create", "update": "catalog.edit",
    "partial_update": "catalog.edit", "destroy": "catalog.delete",
}


class ProductViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    required_perms = _PERMS
