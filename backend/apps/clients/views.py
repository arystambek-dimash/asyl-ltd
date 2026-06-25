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
        "debts": "clients.view",
        "debt_detail": "clients.view",
    }

    def _debt_orders(self, client):
        qs = (client.orders
              .filter(status="shipped")
              .select_related("client", "store")
              .prefetch_related("items__product", "payments")
              .order_by("-created_at"))
        # Реальный долг — остаток > 0; не доверяем хранимому payment_status.
        return [o for o in qs if o.remaining_amount > 0]

    def _debt_total(self, orders):
        return sum((o.total_amount - o.paid_total for o in orders), Decimal("0"))

    @action(detail=False, methods=["get"], url_path="debts")
    def debts(self, request):
        """Агрегированные долги по клиентам."""
        today = date.today()
        rows = []
        for client in Client.objects.prefetch_related("orders__payments", "stores").all():
            orders = list(self._debt_orders(client))
            debt = self._debt_total(orders)
            if debt <= 0:
                continue
            stores = [s for s in client.stores.all()
                      if any(o.store_id == s.id for o in orders)]
            rows.append({
                "client_id": client.id,
                "client_name": client.name,
                "client_phone": client.phone,
                "debt_total": str(debt.quantize(Decimal("0.01"))),
                "orders_count": len(orders),
                "unpaid_count": sum(1 for o in orders if o.payment_status == "unpaid"),
                "partial_count": sum(1 for o in orders if o.payment_status == "partial"),
                "stores_count": len(stores),
                "overdue_count": sum(
                    1 for s in stores
                    if s.payment_schedule_type != "none" and is_payment_window_open(s, today)
                ),
            })
        rows.sort(key=lambda r: Decimal(r["debt_total"]), reverse=True)
        return Response(rows)

    @action(detail=True, methods=["get"], url_path="debt-detail")
    def debt_detail(self, request, pk=None):
        """Детали долга клиента: агрегат и непогашенные заказы."""
        from apps.orders.serializers import OrderSerializer
        client = self.get_object()
        today = date.today()
        orders = list(self._debt_orders(client))
        debt = self._debt_total(orders)
        stores = [s for s in client.stores.all()
                  if any(o.store_id == s.id for o in orders)]
        return Response({
            "client": ClientSerializer(client).data,
            "debt_total": str(debt.quantize(Decimal("0.01"))),
            "orders_count": len(orders),
            "unpaid_count": sum(1 for o in orders if o.payment_status == "unpaid"),
            "partial_count": sum(1 for o in orders if o.payment_status == "partial"),
            "stores": [
                {
                    "id": s.id,
                    "name": s.name,
                    "payment_schedule_type": s.payment_schedule_type,
                    "payment_days": s.payment_days,
                    "window_open": is_payment_window_open(s, today),
                }
                for s in stores
            ],
            "orders": OrderSerializer(orders, many=True, context={"request": request}).data,
        })


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
        qs = (store.orders.filter(status="shipped")
              .select_related("client").prefetch_related("items__product", "payments")
              .order_by("created_at"))
        orders = [o for o in qs if o.remaining_amount > 0]
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
        for store in Store.objects.select_related("client").prefetch_related("orders__payments").all():
            qs = store.orders.filter(status="shipped")
            orders = [o for o in qs if o.remaining_amount > 0]
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
                "orders_count": len(orders),
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
