from rest_framework import viewsets, mixins
from accounts.permissions import IsClientUser
from catalog.models import Product
from orders.models import Order
from .serializers import CatalogProductSerializer, PortalOrderSerializer


class PortalCatalogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = CatalogProductSerializer
    permission_classes = [IsClientUser]
    queryset = Product.objects.filter(is_active=True)


class PortalOrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                         mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = PortalOrderSerializer
    permission_classes = [IsClientUser]

    def get_queryset(self):
        return Order.objects.filter(
            client__user=self.request.user
        ).prefetch_related("items__product")
