from datetime import date
from decimal import Decimal
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.rbac.permissions import PermViewSetMixin
from .models import Client, Store
from .serializers import ClientSerializer, StoreSerializer
from .services import detect_overdue, is_payment_window_open


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
        "debts": "clients.view",
        "debt_detail": "clients.view",
    }

    @action(detail=True, methods=["get"], url_path="debt-detail")
    def debt_detail(self, request, pk=None):
        """Детали долга одного магазина: расписание, окно и непогашенные заказы."""
        from apps.orders.serializers import OrderSerializer
        store = self.get_object()
        today = date.today()
        orders = (store.orders.filter(status="shipped").exclude(payment_status="settled")
                  .select_related("client").prefetch_related("items__product", "payments")
                  .order_by("created_at"))
        debt = sum((o.total_amount - o.paid_total for o in orders), Decimal("0"))
        return Response({
            "store": StoreSerializer(store).data,
            "client_name": store.client.name,
            "debt_total": str(debt.quantize(Decimal("0.01"))),
            "window_open": is_payment_window_open(store, today),
            "orders": OrderSerializer(orders, many=True, context={"request": request}).data,
        })

    @action(detail=False, methods=["get"], url_path="debts")
    def debts(self, request):
        """Долги по магазинам: сумма непогашенного, расписание, окно/просрочка."""
        today = date.today()
        rows = []
        for store in Store.objects.select_related("client").all():
            orders = (store.orders
                      .filter(status="shipped").exclude(payment_status="settled"))
            debt = sum((o.total_amount - o.paid_total for o in orders), Decimal("0"))
            if debt <= 0:
                continue
            window_open = is_payment_window_open(store, today)
            rows.append({
                "store_id": store.id,
                "store_name": store.name,
                "client_id": store.client_id,
                "client_name": store.client.name,
                "payment_schedule_type": store.payment_schedule_type,
                "payment_days": store.payment_days,
                "debt_total": str(debt.quantize(Decimal("0.01"))),
                "orders_count": orders.count(),
                "window_open": window_open,
                # просрочка: окно сегодня открыто, но долг ещё висит
                "overdue": window_open and store.payment_schedule_type != "none",
            })
        rows.sort(key=lambda r: Decimal(r["debt_total"]), reverse=True)
        return Response(rows)

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
