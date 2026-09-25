from decimal import Decimal
from collections import defaultdict

from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.catalog.models import ClientPrice, Product
from apps.catalog.serializers import ClientPriceUpdateSerializer
from apps.common.money import (
    CURRENCY_CODES,
    DEFAULT_CURRENCY,
    as_money_strings,
    money_string,
    sum_by_currency,
)
from apps.common.pagination import OptInPageNumberPagination
from apps.common.permissions import SUPERUSER_ONLY, PermViewSetMixin
from apps.common.query_params import parse_date_range, parse_money_param
from apps.common.viewsets import SerializerViewSetMixin
from apps.eventlog.services import log_event
from apps.orders.debt import DEBT_STATUS, debt_fields, debt_orders, order_remaining
from apps.orders.models import Order
from apps.orders.querysets import (
    filter_order_scope,
    order_remaining_by_id,
    with_order_api_relations,
)
from apps.orders.statuses import ON_POST_STATUSES
from apps.sales.access import assigned_department_id, scope_by_client_department
from apps.sales.models import Department

from .assignment import assign_client_department, log_department_change
from .models import Client, Store
from .reports.statements import (
    ALL_CLIENT_SECTIONS,
    CLIENT_SECTIONS,
    build_statement,
)
from .reports.statements.utils import (
    STATEMENT_CONTENT_TYPES,
    statement_departments,
    statement_format,
    statement_sections,
)
from .serializers import (
    ClientCreateUpdateSerializer,
    ClientPasswordSerializer,
    ClientReadSerializer,
    StoreSerializer,
)
from .services import (
    client_history,
    detect_overdue,
    is_payment_window_open,
    is_store_overdue,
)


class ClientNoLongerAvailable(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "client_not_active"

    def __init__(self):
        super().__init__({
            "detail": "Клиент уже удалён",
            "code": self.default_code,
        })


class ClientHasOrders(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "client_has_orders"

    def __init__(self):
        super().__init__({
            "detail": "У клиента есть заказы — обычное удаление недоступно",
            "code": self.default_code,
        })


class StoreChanged(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "store_changed"

    def __init__(self):
        super().__init__({
            "detail": "Магазин был изменён другим запросом; обновите страницу",
            "code": self.default_code,
        })


def _lock_scoped_client(client_pk, user=None):
    try:
        # Do not join here: PostgreSQL otherwise also locks related User and
        # Department before the Client authorization boundary is established.
        client = Client.objects.select_for_update().get(pk=client_pk)
    except Client.DoesNotExist as exc:
        raise ClientNoLongerAvailable() from exc
    if user is not None and not scope_by_client_department(
        Client.objects.filter(pk=client.pk),
        user,
    ).exists():
        raise PermissionDenied("Клиент передан в другой отдел")
    return client


def _lock_client_orders(client_pk):
    return list(
        Order.all_objects.select_for_update()
        .filter(client_id=client_pk)
        .only("pk", "status")
        .order_by("pk")
    )


def _lock_client_with_orders(client_pk, user):
    """Заказы → клиент → заказы: блокировки для удаления клиента.

    Порядок совпадает с сервисами заказов, которые пишут FK на клиента.
    Повторный проход закрывает окно вставки: пока клиент заблокирован,
    новый заказ не пройдёт проверку FK. Возвращает (клиент, его заказы).
    """
    _lock_client_orders(client_pk)
    client = _lock_scoped_client(client_pk, user)
    return client, _lock_client_orders(client_pk)


def _assert_no_active_loading(locked_orders):
    from apps.cameras.models import AiCountingSession

    if (
        AiCountingSession.objects.filter(
            order_id__in=[order.pk for order in locked_orders],
            status__in=AiCountingSession.OPEN_STATUSES,
        ).exists()
        or any(order.status in ON_POST_STATUSES for order in locked_orders)
    ):
        raise ValidationError({
            "detail": "Сначала завершите или верните активные погрузки клиента",
            "code": "active_loading",
        })


def _statement_options(params, available_sections):
    date_from, date_to = parse_date_range(params)
    return {
        "date_from": date_from,
        "date_to": date_to,
        "departments": statement_departments(params),
        "sections": statement_sections(params, available_sections),
    }


def _statement_payload(options, export):
    departments = options["departments"]
    sections = options["sections"]
    return {
        "date_from": str(options["date_from"]) if options["date_from"] else None,
        "date_to": str(options["date_to"]) if options["date_to"] else None,
        "departments": list(departments) if departments else None,
        "sections": list(sections) if sections else None,
        "format": export,
    }


def _statement_response(content, export, filename):
    response = HttpResponse(content, content_type=STATEMENT_CONTENT_TYPES[export])
    response["Content-Disposition"] = f'attachment; filename="{filename}.{export}"'
    return response


class ClientViewSet(
    SerializerViewSetMixin,
    PermViewSetMixin,
    viewsets.ModelViewSet,
):
    queryset = Client.objects.select_related("user", "department")
    serializer_class = ClientReadSerializer
    serializer_action_classes = {
        "create": ClientCreateUpdateSerializer,
        "update": ClientCreateUpdateSerializer,
        "partial_update": ClientCreateUpdateSerializer,
        "set_password": ClientPasswordSerializer,
    }
    pagination_class = OptInPageNumberPagination
    required_perms = {
        "list": "clients.view",
        "retrieve": "clients.view",
        "create": "clients.create",
        "update": "clients.edit",
        "partial_update": "clients.edit",
        "destroy": "clients.delete",
        # ``payments.create`` opens only the debt rows needed to enter a
        # payment. It does not grant reports/history access.
        "debts": ("reports.view", "payments.create"),
        "debt_detail": ("reports.view", "payments.create"),
        "history": "reports.view",
        "statement": "reports.export",
        "all_statement": "reports.export",
        "prices": "clients.set_price",
        "picker": "clients.view",
        "set_password": "clients.manage_access",
        "purge": SUPERUSER_ONLY,
        # Касса разбирает заявки саморегистрации и забирает клиента к себе.
        "assign_department": ("clients.edit", "orders.confirm"),
    }
    # Клиенты без отдела — общая очередь: отдел видит её отдельным списком
    # (?department=none) и забирает клиента к себе. В свой список они не подмешиваются.
    UNASSIGNED_VISIBLE_ACTIONS = frozenset({"retrieve", "assign_department"})

    def get_queryset(self):
        base = Client.objects.select_related("user", "department").order_by("-id")
        unassigned_queue = self.action in self.UNASSIGNED_VISIBLE_ACTIONS or (
            self.action == "list" and self.request.query_params.get("department") == "none"
        )
        base = scope_by_client_department(
            base,
            self.request.user,
            unassigned=Q() if unassigned_queue else None,
        )
        department = None
        if self.action == "list":
            department = self.request.query_params.get("department")
        elif self.action == "debts":
            # ``department`` on this endpoint remains the order department;
            # client ownership has its own explicit filter.
            department = self.request.query_params.get("client_department")
        if department == "none":
            base = base.filter(department__isnull=True)
        elif department:
            base = base.filter(department__code=department)
        # Долг и просрочка клиента читают только отгруженные заказы.
        if self.action == "debt_detail":
            return base.prefetch_related(
                Prefetch(
                    "orders",
                    queryset=with_order_api_relations(
                        Order.objects.filter(status=DEBT_STATUS)
                    ),
                )
            )
        if (
            self.action in {"list", "retrieve"}
            and self.request.user.has_perm_code("reports.view")
        ):
            return base.prefetch_related(
                Prefetch(
                    "orders",
                    queryset=Order.objects.filter(
                        status=DEBT_STATUS,
                    ).prefetch_related("items", "payments"),
                )
            )
        return base

    @transaction.atomic
    def perform_update(self, serializer):
        client_pk = serializer.instance.pk
        if "department" in serializer.validated_data:
            # Order mutations already use Order→Client (for example when an
            # order creates a client notification).  Take existing Orders
            # first so this path never holds Client while waiting for Order.
            _lock_client_orders(client_pk)
        client = _lock_scoped_client(client_pk, self.request.user)
        # Validation happens before ``perform_update``.  Replace its possibly
        # stale instance so a concurrent purge cannot turn ``save()`` into an
        # INSERT that resurrects the deleted Client row.
        serializer.instance = client
        previous = client.department
        target = serializer.validated_data.get("department", previous)
        if (previous.pk if previous else None) != (target.pk if target else None):
            # Repeat after Client is locked: an Order whose FK key lock began
            # before our Client lock may have committed after the first pass.
            # No new Order can pass its FK check while Client stays locked.
            _assert_no_active_loading(_lock_client_orders(client_pk))
        client = serializer.save()
        current = client.department
        if (previous.pk if previous else None) == (current.pk if current else None):
            return
        log_department_change(
            client,
            previous,
            self.request.user,
            f"Клиент «{client.name}» перенесён в другой отдел",
        )

    @action(detail=True, methods=["post"], url_path="assign-department")
    def assign_department(self, request, pk=None):
        """Закрепить клиента без отдела. Без ``department`` — за отделом сотрудника."""
        client_pk = self.get_object().pk
        raw = request.data.get("department", assigned_department_id(request.user))
        department = Department.objects.filter(pk=raw).first() if str(raw).isdigit() else None
        with transaction.atomic():
            # Тот же порядок блокировок, что у переноса клиента: заказы → клиент.
            _lock_client_orders(client_pk)
            client = _lock_scoped_client(client_pk)
            assign_client_department(client, department, request.user)
        client = self.get_queryset().get(pk=client_pk)
        return Response(ClientReadSerializer(client, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"], url_path="purge")
    def purge(self, request, pk=None):
        from apps.cameras.models import AiCountingSession

        client_pk = self.get_object().pk
        with transaction.atomic():
            client, locked_orders = _lock_client_with_orders(client_pk, request.user)
            _assert_no_active_loading(locked_orders)
            portal_user = client.user
            order_ids = [order.pk for order in locked_orders]
            orders_count = len(order_ids)
            log_event(
                "client",
                f"Клиент «{client.name}» удалён с историей "
                f"({orders_count} заказов)",
                user=request.user,
                payload={
                    "client_id": client.pk,
                    "client_name": client.name,
                    "orders": orders_count,
                    "action": "client_purged",
                },
            )
            AiCountingSession.objects.filter(order_id__in=order_ids).delete()
            Order.all_objects.filter(pk__in=order_ids).delete()
            client.delete()
            self._deactivate_portal_user(portal_user)
        return Response(status=204)

    @staticmethod
    def _deactivate_portal_user(user):
        if user.is_active:
            user.is_active = False
            user.save(update_fields=["is_active"])

    @transaction.atomic
    def perform_destroy(self, instance):
        instance, locked_orders = _lock_client_with_orders(instance.pk, self.request.user)
        # Заказы (и из корзины тоже) держат клиента через PROTECT: без этой
        # проверки delete() падает ProtectedError → 500.
        if locked_orders:
            raise ClientHasOrders()
        portal_user = instance.user
        instance.delete()
        self._deactivate_portal_user(portal_user)

    @action(detail=True, methods=["post"], url_path="password")
    @transaction.atomic
    def set_password(self, request, pk=None):
        client = _lock_scoped_client(self.get_object().pk, request.user)
        context = self.get_serializer_context()
        context["client"] = client
        serializer = self.get_serializer(data=request.data, context=context)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_event(
            "client_security",
            f"Выдан временный пароль клиенту {client.user.username}",
            user=request.user,
            payload={
                "client_id": client.pk,
                "username": client.user.username,
                "password_changed": True,
            },
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"], url_path="history")
    def history(self, request, pk=None):
        return Response(client_history(self.get_object()))

    @action(detail=False, methods=["get"], url_path="picker")
    def picker(self, request):
        clients = (
            self.get_queryset()
            .select_related(None)
            .select_related("user")
            .only(
                "id",
                "company_name",
                "user_id",
                "user__first_name",
                "user__last_name",
                "user__username",
            )
            .order_by(
                "company_name",
                "user__last_name",
                "user__first_name",
            )
        )
        return Response([
            {"id": client.id, "name": client.name}
            for client in clients
        ])

    @action(detail=True, methods=["get"], url_path="statement")
    def statement(self, request, pk=None):
        options = _statement_options(request.query_params, CLIENT_SECTIONS)
        export = statement_format(request.query_params)
        client = self.get_object()
        content = build_statement(export, client=client, **options)
        log_event(
            "client_statement",
            f"Сформирована {export.upper()}-выписка клиента «{client.name}»",
            user=request.user,
            payload={"client_id": client.pk, **_statement_payload(options, export)},
        )
        return _statement_response(content, export, f"client-{client.pk}-statement")

    @action(detail=False, methods=["get"], url_path="statement")
    def all_statement(self, request):
        options = _statement_options(request.query_params, ALL_CLIENT_SECTIONS)
        export = statement_format(request.query_params)
        client_ids = tuple(
            self.get_queryset().values_list("pk", flat=True)
        )
        content = build_statement(export, client_ids=client_ids, **options)
        log_event(
            "clients_statement",
            f"Сформирована общая {export.upper()}-выписка по клиентам",
            user=request.user,
            payload=_statement_payload(options, export),
        )
        return _statement_response(content, export, "clients-full-statement")

    def _price_rows(self, client):
        prices = {
            (row.product_id, row.currency): row
            for row in ClientPrice.objects.filter(client=client).select_related("updated_by")
        }
        rows = []
        for product in Product.objects.filter(is_active=True).order_by(
                "name", "color", "weight_kg"):
            for currency in CURRENCY_CODES:
                row = prices.get((product.id, currency))
                rows.append({
                    "product": product.id,
                    "product_label": str(product),
                    "currency": currency,
                    "price": money_string(row.price) if row else None,
                    "updated_at": row.updated_at if row else None,
                    "updated_by_name": (
                        row.updated_by.username if row and row.updated_by else None
                    ),
                })
        return rows

    @staticmethod
    def _price_client(client):
        # Прайс-листу нужны только идентификатор и подпись. Банковские
        # реквизиты, ИИН и финансовая аналитика в этот контракт не входят.
        return {"id": client.id, "name": client.name}

    @action(detail=True, methods=["get", "put"], url_path="prices")
    def prices(self, request, pk=None):
        """Личный прайс клиента. Изменять может только сотрудник с отдельным правом."""
        client = self.get_object()
        if request.method == "GET":
            return Response({"client": self._price_client(client),
                             "prices": self._price_rows(client)})

        serializer = ClientPriceUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        changed = 0
        removed = 0
        with transaction.atomic():
            client = _lock_scoped_client(client.pk, request.user)
            for row in serializer.validated_data["prices"]:
                product = row["product"]
                currency = row["currency"]
                price = row.get("price")
                if price is None:
                    deleted, _ = ClientPrice.objects.filter(
                        client=client, product=product, currency=currency).delete()
                    removed += deleted
                    continue
                ClientPrice.objects.update_or_create(
                    client=client, product=product, currency=currency,
                    defaults={"price": price, "updated_by": request.user},
                )
                changed += 1
            log_event(
                "catalog", f"Прайс-лист клиента «{client.name}» обновлён",
                user=request.user,
                payload={"client_id": client.id, "updated": changed, "removed": removed},
            )
        return Response({"client": self._price_client(client),
                         "prices": self._price_rows(client)})

    @action(detail=False, methods=["get"], url_path="debts")
    def debts(self, request):
        """Агрегированные долги по клиентам (в рамках видимых отделов)."""
        today = timezone.localdate()
        params = request.query_params
        debt_min = parse_money_param(
            params.get("remaining_min"),
            "Минимальный остаток",
        )
        debt_max = parse_money_param(
            params.get("remaining_max"),
            "Максимальный остаток",
        )
        remaining_currency = params.get("remaining_currency")
        if remaining_currency and remaining_currency not in CURRENCY_CODES:
            raise ValidationError({
                "detail": "Неизвестная валюта остатка",
                "code": "bad_currency",
            })
        if debt_min is not None and debt_max is not None and debt_min > debt_max:
            raise ValidationError(
                {"detail": "Минимальный остаток больше максимального",
                 "code": "bad_range"})
        # Погашенные заказы не считаем: payment_status ведёт сервис оплат при
        # каждом изменении оплат и позиций (services.sync_payment_status;
        # бэкфилл — manage.py sync_payment_status), а остаток по остальным
        # считается точно ниже. Иначе список растёт со всей историей продаж.
        orders_qs = Order.objects.filter(
            status=DEBT_STATUS,
        ).exclude(payment_status="settled")
        orders_qs = filter_order_scope(orders_qs, params, date_field="created_at")
        visible_clients = self.get_queryset().prefetch_related(None)
        orders_qs = orders_qs.filter(client_id__in=visible_clients.values("pk"))
        # Остаток считаем по всей выборке разом: подзапрос на каждый заказ
        # рос вместе с историей оплат, и список на телефоне грузился секундами.
        remaining = order_remaining_by_id(orders_qs)
        by_client = defaultdict(list)
        store_ids = set()
        order_rows = orders_qs.order_by().values(
            "id", "client_id", "store_id", "currency", "payment_status",
        )
        for balance in order_rows:
            amount = remaining.get(balance["id"], Decimal("0"))
            if amount <= 0:
                continue
            balance["amount_remaining"] = amount
            by_client[balance["client_id"]].append(balance)
            if balance["store_id"] is not None:
                store_ids.add(balance["store_id"])
        clients = visible_clients.filter(pk__in=by_client).prefetch_related(
            Prefetch("stores", queryset=Store.objects.filter(pk__in=store_ids)),
        )
        rows = []
        for client in clients:
            orders = by_client[client.pk]
            totals = defaultdict(lambda: Decimal("0"))
            for order in orders:
                totals[order["currency"] or DEFAULT_CURRENCY] += order["amount_remaining"]
            fields = debt_fields(totals, fallback=client.currency)
            # В by_client только заказы с положительным остатком — долг есть.
            debt = totals[fields["debt_currency"]]
            filtered_debt = (
                totals.get(remaining_currency, Decimal("0"))
                if remaining_currency
                else debt
            )
            if debt_min is not None and filtered_debt < debt_min:
                continue
            if debt_max is not None and filtered_debt > debt_max:
                continue
            stores = list(client.stores.all())
            rows.append({
                "client_id": client.id,
                "client_name": client.name,
                "client_phone": client.phone,
                # debt_total — основная валюта; полная раскладка рядом.
                **fields,
                "orders_count": len(orders),
                "unpaid_count": sum(1 for o in orders if o["payment_status"] == "unpaid"),
                "partial_count": sum(1 for o in orders if o["payment_status"] == "partial"),
                "stores_count": len(stores),
                "overdue_count": sum(1 for s in stores if is_store_overdue(s, today)),
            })
        # Валюты не ранжируем друг против друга без курса: сначала стабильная
        # группа валюты, затем остаток по убыванию внутри этой группы.
        rows.sort(key=lambda r: (
            r["debt_currency"],
            -Decimal(r["debt_total"]),
            r["client_name"].casefold(),
        ))
        return Response(rows)

    @action(detail=True, methods=["get"], url_path="debt-detail")
    def debt_detail(self, request, pk=None):
        """Детали долга клиента: агрегат и непогашенные заказы."""
        from apps.orders.serializers import OrderSerializer
        client = self.get_object()
        today = timezone.localdate()
        can_view_reports = request.user.has_perm_code("reports.view")
        # Заказы предзагружены queryset'ом — фильтруем кэш без нового запроса.
        orders = debt_orders(client.orders.all())
        orders.sort(key=lambda o: o.created_at, reverse=True)
        totals = sum_by_currency(orders, order_remaining)
        fields = debt_fields(totals, fallback=client.currency)
        currency = fields["debt_currency"]
        stores = [s for s in client.stores.all()
                  if any(o.store_id == s.id for o in orders)]
        # Просрочка — только для reports.view; итоги «за всё время» отдаёт
        # /history/. Приёмщик оплат получает текущий долг, заказы и минимум о клиенте.
        overdue = {}
        if can_view_reports:
            overdue_stores = {s.id for s in stores if is_store_overdue(s, today)}
            overdue = sum_by_currency(
                [o for o in orders if o.store_id in overdue_stores], order_remaining)
        client_data = (
            self.get_serializer(client).data
            if can_view_reports
            else {
                "id": client.id,
                "name": client.name,
                "phone": client.phone,
                "currency": client.currency,
            }
        )
        return Response({
            "client": client_data,
            **fields,
            "overdue_total": money_string(overdue.get(currency, Decimal("0"))),
            "overdue_by_currency": as_money_strings(overdue),
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
    queryset = Store.objects.select_related("client__user").order_by("id")
    serializer_class = StoreSerializer
    pagination_class = OptInPageNumberPagination

    required_perms = {
        "list": "stores.view", "retrieve": "stores.view",
        "create": "stores.create", "update": "stores.edit",
        "partial_update": "stores.edit", "destroy": "stores.delete",
        "check_overdue": "stores.edit",
    }

    def get_queryset(self):
        return scope_by_client_department(
            super().get_queryset(),
            self.request.user,
            client_path="client",
        )

    @transaction.atomic
    def perform_create(self, serializer):
        client = _lock_scoped_client(
            serializer.validated_data["client"].pk,
            self.request.user,
        )
        serializer.save(client=client)

    def _lock_store(self, store_pk):
        """Клиент → магазин под блокировкой; возвращает (магазин, клиент).

        Порядок как у удаления клиента, чей каскад доходит до Store: FK читается
        без блокировки, клиент блокируется, затем магазин перепроверяется.
        """
        client_pk = (
            Store.objects.filter(pk=store_pk)
            .values_list("client_id", flat=True)
            .first()
        )
        if client_pk is None:
            raise StoreChanged()
        client = _lock_scoped_client(client_pk, self.request.user)
        try:
            store = Store.objects.select_for_update().get(pk=store_pk)
        except Store.DoesNotExist as exc:
            raise StoreChanged() from exc
        if store.client_id != client_pk:
            raise StoreChanged()
        return store, client

    @transaction.atomic
    def perform_update(self, serializer):
        store, client = self._lock_store(serializer.instance.pk)
        requested_client = serializer.validated_data.get("client")
        if requested_client is not None and requested_client.pk != client.pk:
            raise ValidationError({
                "detail": "Клиента магазина изменить нельзя — создайте новый магазин",
                "code": "client_locked",
            })
        serializer.instance = store
        serializer.save(client=client)

    @transaction.atomic
    def perform_destroy(self, instance):
        store, _client = self._lock_store(instance.pk)
        store.delete()

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
