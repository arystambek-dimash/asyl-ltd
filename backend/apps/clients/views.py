from datetime import date
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.rbac.permissions import PermViewSetMixin
from .models import Client, Store
from .serializers import ClientSerializer, StoreSerializer
from .services import detect_overdue


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
        "check_overdue": "clients.view",
    }

    @action(detail=False, methods=["post"], url_path="check-overdue")
    def check_overdue(self, request):
        """Прогнать детектор просрочки по всем магазинам на сегодня."""
        today = date.today()
        total = 0
        checked = 0
        for store in Store.objects.exclude(payment_schedule_type="none"):
            checked += 1
            total += detect_overdue(store, today)
        return Response({"checked": checked, "overdue_notifications": total})
