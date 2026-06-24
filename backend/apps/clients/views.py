from rest_framework import viewsets
from apps.rbac.permissions import PermViewSetMixin
from .models import Client, Store
from .serializers import ClientSerializer, StoreSerializer


class ClientViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    required_perms = {
        "list": "clients.view", "retrieve": "clients.view",
        "create": "clients.create", "update": "clients.edit",
        "partial_update": "clients.edit", "destroy": "clients.delete",
    }


class StoreViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Store.objects.select_related("client").all()
    serializer_class = StoreSerializer
    required_perms = {
        "list": "clients.view", "retrieve": "clients.view",
        "create": "clients.create", "update": "clients.edit",
        "partial_update": "clients.edit", "destroy": "clients.delete",
    }
