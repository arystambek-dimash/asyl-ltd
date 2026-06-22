from rest_framework import viewsets
from rbac.permissions import PermViewSetMixin
from .models import Grade, Packaging, Product
from .serializers import GradeSerializer, PackagingSerializer, ProductSerializer

_PERMS = {
    "list": "catalog.view", "retrieve": "catalog.view",
    "create": "catalog.create", "update": "catalog.edit",
    "partial_update": "catalog.edit", "destroy": "catalog.delete",
}


class GradeViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Grade.objects.all()
    serializer_class = GradeSerializer
    required_perms = _PERMS


class PackagingViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Packaging.objects.all()
    serializer_class = PackagingSerializer
    required_perms = _PERMS


class ProductViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Product.objects.select_related("grade", "packaging").all()
    serializer_class = ProductSerializer
    required_perms = _PERMS
