from decimal import Decimal, InvalidOperation
from django.utils import timezone
from django.db import transaction
from django.db.models import Count
from django.db.models.deletion import ProtectedError
from django.db.models import Prefetch
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import mixins
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from apps.common.permissions import HasPerm, IsStaff, PermViewSetMixin
from apps.common.money import money_string
from apps.common.query_params import parse_iso_date, validate_date_range
from apps.orders.models import Order
from apps.orders.querysets import with_order_api_relations
from apps.catalog.models import ClientPrice, Product
from apps.catalog.serializers import ClientPriceUpdateSerializer
from apps.eventlog.services import log_event
from .models import Client, Department, Store
from .serializers import ClientSerializer, DepartmentSerializer, StoreSerializer
from .services import detect_overdue, is_payment_window_open, client_history

def _money_param(raw, name):
    if raw in (None, ""):
        return None
    try:
        value = Decimal(raw)
    except (InvalidOperation, TypeError):
        raise ValidationError(
            {"detail": f"Некорректное значение: {name}", "code": "bad_amount"})
    if value < 0:
        raise ValidationError(
            {"detail": f"{name} не может быть меньше нуля", "code": "bad_amount"})
    return value


class DepartmentViewSet(viewsets.ModelViewSet):
    """Динамические отделы заказов. Управление встроено в экран заказов."""
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsStaff()]
        return [HasPerm("rbac.manage")]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == "list" and self.request.query_params.get("all") != "1":
            qs = qs.filter(is_active=True)
        return qs

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["department_order_counts"] = dict(
            Order.all_objects.values("department").annotate(total=Count("id"))
            .values_list("department", "total")
        )
        return context

    def perform_destroy(self, instance):
        if Order.all_objects.filter(department=instance.code).exists():
            raise ValidationError({
                "detail": "Отдел используется в заказах. Отключите его вместо удаления.",
                "code": "department_in_use",
            })
        if instance.is_default:
            raise ValidationError({
                "detail": "Сначала назначьте другой основной отдел",
                "code": "default_department",
            })
        try:
            instance.delete()
        except ProtectedError as exc:
            raise ValidationError({"detail": "Отдел используется", "code": "department_in_use"}) from exc


class ClientViewSet(PermViewSetMixin, viewsets.ModelViewSet):
    # debt_total в сериализаторе обходит заказы с позициями и оплатами —
    # грузим их заранее, иначе список клиентов даёт N+1 на каждую строку.
    queryset = (
        Client.objects
        .prefetch_related(Prefetch(
            "orders", queryset=with_order_api_relations(Order.objects.all())
        ))
    )
    serializer_class = ClientSerializer
    required_perms = {
        "list": "clients.view",
        "retrieve": "clients.view",
        "create": "clients.create",
        "update": "clients.edit",
        "partial_update": "clients.edit",
        "destroy": "clients.delete",
        # Финансовая детализация и долги — под reports.view.
        "debts": "reports.view",
        "debt_detail": "reports.view",
        "history": "reports.view",
        "prices": "clients.set_price",
    }

    def get_queryset(self):
        return super().get_queryset()

    @action(detail=True, methods=["get"], url_path="history")
    def history(self, request, pk=None):
        return Response(client_history(self.get_object()))

    def _price_rows(self, client):
        prices = {
            row.product_id: row
            for row in ClientPrice.objects.filter(client=client).select_related("updated_by")
        }
        return [
            {
                "product": product.id,
                "product_label": str(product),
                "base_price": money_string(product.price),
                "price": money_string(prices[product.id].price)
                if product.id in prices else None,
                "updated_at": prices[product.id].updated_at
                if product.id in prices else None,
                "updated_by_name": prices[product.id].updated_by.username
                if product.id in prices and prices[product.id].updated_by else None,
            }
            for product in Product.objects.filter(is_active=True).order_by(
                "name", "color", "weight_kg")
        ]

    @action(detail=True, methods=["get", "put"], url_path="prices")
    def prices(self, request, pk=None):
        """Личный прайс клиента. Изменять может только сотрудник с отдельным правом."""
        client = self.get_object()
        if request.method == "GET":
            return Response({"client": ClientSerializer(client).data,
                             "prices": self._price_rows(client)})

        serializer = ClientPriceUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        changed = 0
        removed = 0
        with transaction.atomic():
            for row in serializer.validated_data["prices"]:
                product = row["product"]
                price = row.get("price")
                if price is None:
                    deleted, _ = ClientPrice.objects.filter(
                        client=client, product=product).delete()
                    removed += deleted
                    continue
                _, created = ClientPrice.objects.update_or_create(
                    client=client, product=product,
                    defaults={"price": price, "updated_by": request.user},
                )
                changed += 1
            log_event(
                "catalog", f"Прайс-лист клиента «{client.name}» обновлён",
                user=request.user,
                payload={"client_id": client.id, "updated": changed, "removed": removed},
            )
        return Response({"client": ClientSerializer(client).data,
                         "prices": self._price_rows(client)})

    def _debt_orders(self, client):
        # Заказы уже предзагружены queryset'ом — фильтруем кэш, не создавая
        # новый запрос на каждого клиента.
        orders = [o for o in client.orders.all() if o.is_debt]
        orders.sort(key=lambda o: o.created_at, reverse=True)
        return orders

    def _debt_total(self, orders):
        return sum((o.total_amount - o.paid_total for o in orders), Decimal("0"))

    @action(detail=False, methods=["get"], url_path="debts")
    def debts(self, request):
        """Агрегированные долги по клиентам (в рамках видимых отделов)."""
        today = timezone.localdate()
        params = request.query_params
        date_from = parse_iso_date(params.get("date_from"))
        date_to = parse_iso_date(params.get("date_to"))
        validate_date_range(date_from, date_to)
        debt_min = _money_param(params.get("remaining_min"), "Минимальный остаток")
        debt_max = _money_param(params.get("remaining_max"), "Максимальный остаток")
        if debt_min is not None and debt_max is not None and debt_min > debt_max:
            raise ValidationError(
                {"detail": "Минимальный остаток больше максимального",
                 "code": "bad_range"})
        store_id = params.get("store")
        if store_id and not store_id.isdigit():
            raise ValidationError(
                {"detail": "Некорректный магазин", "code": "bad_store"})

        clients = self.get_queryset()
        department = params.get("department")
        rows = []
        for client in clients.prefetch_related("stores"):
            orders = list(self._debt_orders(client))
            if department:
                orders = [o for o in orders if o.department == department]
            if date_from:
                orders = [o for o in orders
                          if timezone.localdate(o.created_at) >= date_from]
            if date_to:
                orders = [o for o in orders
                          if timezone.localdate(o.created_at) <= date_to]
            if store_id:
                orders = [o for o in orders if o.store_id == int(store_id)]
            debt = self._debt_total(orders)
            if debt <= 0:
                continue
            if debt_min is not None and debt < debt_min:
                continue
            if debt_max is not None and debt > debt_max:
                continue
            stores = [s for s in client.stores.all()
                      if any(o.store_id == s.id for o in orders)]
            rows.append({
                "client_id": client.id,
                "client_name": client.name,
                "client_phone": client.phone,
                "debt_total": money_string(debt),
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
        today = timezone.localdate()
        orders = list(self._debt_orders(client))
        debt = self._debt_total(orders)
        stores = [s for s in client.stores.all()
                  if any(o.store_id == s.id for o in orders)]
        # За всё время: отгруженные заказы «в долг», включая уже погашенные.
        lifetime = [o for o in client.orders.all()
                    if o.status == "shipped" and o.settlement_intent == "debt"]
        # Просрочено = остаток по заказам магазинов, у которых сегодня день оплаты.
        overdue_stores = {s.id for s in stores
                          if s.payment_schedule_type != "none"
                          and is_payment_window_open(s, today)}
        overdue = sum((o.total_amount - o.paid_total for o in orders
                       if o.store_id in overdue_stores), Decimal("0"))
        return Response({
            "client": ClientSerializer(client).data,
            "debt_total": money_string(debt),
            "lifetime_total": money_string(sum(
                (o.total_amount for o in lifetime), Decimal("0"))),
            "lifetime_paid": money_string(sum(
                (o.paid_total for o in lifetime), Decimal("0"))),
            "overdue_total": money_string(overdue),
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

    def get_queryset(self):
        return super().get_queryset()

    required_perms = {
        "list": "clients.view", "retrieve": "clients.view",
        "create": "clients.create", "update": "clients.edit",
        "partial_update": "clients.edit", "destroy": "clients.delete",
        # check_overdue создаёт уведомления (side-effect) → требует edit.
        "check_overdue": "clients.edit",
        # Долги магазинов — финансовое, под reports.view.
        "debts": "reports.view",
        "debt_detail": "reports.view",
    }

    @action(detail=True, methods=["get"], url_path="debt-detail")
    def debt_detail(self, request, pk=None):
        """Детали долга одного магазина: расписание, окно и непогашенные заказы."""
        from apps.orders.serializers import OrderSerializer
        store = self.get_object()
        today = timezone.localdate()
        qs = with_order_api_relations(store.orders.all()).order_by("created_at")
        orders = [o for o in qs if o.is_debt]
        debt = sum((o.total_amount - o.paid_total for o in orders), Decimal("0"))
        return Response({
            "store": StoreSerializer(store).data,
            "client_name": store.client.name,
            "debt_total": money_string(debt),
            "window_open": is_payment_window_open(store, today),
            "orders": OrderSerializer(orders, many=True, context={"request": request}).data,
        })

    @action(detail=False, methods=["get"], url_path="debts")
    def debts(self, request):
        """Долги по магазинам: сумма непогашенного, расписание, окно/просрочка."""
        today = timezone.localdate()
        rows = []
        for store in self.get_queryset().prefetch_related(
                "orders__items__product", "orders__payments"):
            orders = [o for o in store.orders.all() if o.is_debt]
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
                "debt_total": money_string(debt),
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
        today = timezone.localdate()
        total = 0
        checked = 0
        for store in self.get_queryset().exclude(payment_schedule_type="none"):
            checked += 1
            total += detect_overdue(store, today)
        return Response({"checked": checked, "overdue_notifications": total})
