from rest_framework import viewsets
from apps.rbac.permissions import PermViewSetMixin
from .models import Client
from .serializers import ClientSerializer


class ClientViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    required_perms = {
        "list": "clients.view", "retrieve": "clients.view",
        "create": "clients.create", "update": "clients.edit",
        "partial_update": "clients.edit", "destroy": "clients.delete",
    }
