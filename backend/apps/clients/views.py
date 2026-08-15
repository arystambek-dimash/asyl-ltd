from decimal import Decimal

from django.db import transaction
from django.db.models import Prefetch
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.catalog.models import ClientPrice, Product
from apps.catalog.serializers import ClientPriceUpdateSerializer
from apps.common.money import (
    as_money_strings,
    money_string,
    primary_currency,
    sum_by_currency,
)
from apps.common.pagination import OptInPageNumberPagination
from apps.common.permissions import PermViewSetMixin
from apps.common.query_params import (
    parse_iso_date,
    parse_money_param,
    parse_store_id,
    validate_date_range,
)
from apps.common.viewsets import SerializerViewSetMixin
from apps.eventlog.services import log_event
from apps.orders.debt import debt_orders, order_remaining
from apps.orders.models import Order
from apps.orders.querysets import with_order_api_relations
from apps.sales.access import scope_by_client_department

from .models import Client, Store
from .reports.statements import (
    ALL_CLIENT_SECTIONS,
    CLIENT_SECTIONS,
    build_all_clients_statement,
    build_all_clients_statement_pdf,
    build_client_statement,
    build_client_statement_pdf,
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
from .services import client_history, detect_overdue, is_payment_window_open


class ClientNoLongerAvailable(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "client_not_active"

    def __init__(self):
        super().__init__({
            "detail": "Клиент уже удалён",
            "code": self.default_code,
        })


class ClientDeviceHistoryProtected(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "device_history_protected"

    def __init__(self):
        super().__init__({
            "detail": (
                "Нельзя удалить клиента: история ESP32 связана с его сессией "
                "погрузки"
            ),
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
        "debts": "reports.view",
        "debt_detail": "reports.view",
        "history": "reports.view",
        "statement": "reports.export",
        "all_statement": "reports.export",
        "prices": "clients.set_price",
        "picker": "clients.view",
        "set_password": "clients.manage_access",
        "purge": "clients.delete",
    }

    def get_queryset(self):
        base = Client.objects.select_related("user", "department").order_by("-id")
        base = scope_by_client_department(base, self.request.user)
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
        can_view_financials = self.request.user.has_perm_code("reports.view")
        if self.action not in {"list", "retrieve", "debts", "debt_detail"}:
            return base
        if not can_view_financials:
            return base
        if self.action == "debt_detail":
            return base.prefetch_related(
                Prefetch(
                    "orders", queryset=with_order_api_relations(Order.objects.all())
                )
            )
        return base.prefetch_related(
            Prefetch(
                "orders",
                queryset=Order.objects.filter(
                    status="shipped",
                    settlement_intent="debt",
                ).prefetch_related("items", "payments"),
            )
        )

    @transaction.atomic
    def perform_update(self, serializer):
        client_pk = serializer.instance.pk
        if "department" in serializer.validated_data:
            # Order mutations already use Order→Client (for example when an
            # order creates a client notification).  Take existing Orders
            # first so this path never holds Client while waiting for Order.
            self._lock_client_orders(client_pk)
        client = self._lock_client(client_pk, self.request.user)
        # Validation happens before ``perform_update``.  Replace its possibly
        # stale instance so a concurrent purge cannot turn ``save()`` into an
        # INSERT that resurrects the deleted Client row.
        serializer.instance = client
        previous = client.department
        target = serializer.validated_data.get("department", previous)
        if (previous.pk if previous else None) != (target.pk if target else None):
            from apps.cameras.models import AiCountingSession

            # Repeat after Client is locked: an Order whose FK key lock began
            # before our Client lock may have committed after the first pass.
            # No new Order can pass its FK check while Client stays locked.
            locked_orders = self._lock_client_orders(client_pk)
            order_ids = [order.pk for order in locked_orders]
            if (
                AiCountingSession.objects.filter(
                    order_id__in=order_ids,
                    status__in=AiCountingSession.OPEN_STATUSES,
                ).exists()
                or any(
                    order.status in ("arrived", "loading", "loaded")
                    for order in locked_orders
                )
            ):
                raise ValidationError({
                    "detail": "Сначала завершите или верните активные погрузки клиента",
                    "code": "active_loading",
                })
        client = serializer.save()
        current = client.department
        if (previous.pk if previous else None) == (current.pk if current else None):
            return
        log_event(
            "client",
            f"Клиент «{client.name}» перенесён в другой отдел",
            user=self.request.user,
            payload={
                "client_id": client.pk,
                "action": "client_department_changed",
                "department_from": previous.code if previous else None,
                "department_to": current.code if current else None,
            },
        )

    @action(detail=True, methods=["post"], url_path="purge")
    def purge(self, request, pk=None):
        if not request.user.is_superuser:
            raise PermissionDenied("Удаление с историей доступно только суперадмину.")
        client_pk = self.get_object().pk
        from apps.cameras.models import AiCountingSession
        from apps.conveyors.models import ConveyorDevice
        from apps.orders.models import Order as OrderModel

        try:
            with transaction.atomic():
                # Existing Orders→Client→Orders again is deliberate.  It
                # agrees with order-side services that write a Client FK and
                # closes the insert gap once the Client row is held.
                self._lock_client_orders(client_pk)
                client = self._lock_client(client_pk, request.user)
                portal_user = client.user
                locked_orders = self._lock_client_orders(client_pk)
                order_ids = [order.pk for order in locked_orders]
                orders_count = len(order_ids)
                if (
                    AiCountingSession.objects.filter(
                        order_id__in=order_ids,
                        status__in=AiCountingSession.OPEN_STATUSES,
                    ).exists()
                    or any(
                        order.status in ("arrived", "loading", "loaded")
                        for order in locked_orders
                    )
                ):
                    raise ValidationError({
                        "detail": (
                            "Сначала завершите или верните активные погрузки клиента"
                        ),
                        "code": "active_loading",
                    })

                # ConveyorDevice.command_session deliberately uses PROTECT:
                # deleting that session would erase the provenance of the
                # latest command.  Keep both histories intact and return a
                # stable conflict instead of clearing the safety reference.
                if ConveyorDevice.objects.filter(
                    command_session__order_id__in=order_ids,
                ).exists():
                    raise ClientDeviceHistoryProtected()

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
                OrderModel.all_objects.filter(pk__in=order_ids).delete()
                client.delete()
                self._deactivate_portal_user(portal_user)
        except ProtectedError as exc:
            # Close the small check/delete race as well.  Only translate the
            # ESP32 safety reference; unrelated future PROTECT relations must
            # retain their own policy rather than being mislabeled.
            if any(
                isinstance(obj, ConveyorDevice) for obj in exc.protected_objects
            ):
                raise ClientDeviceHistoryProtected() from exc
            raise
        return Response(status=204)

    @staticmethod
    def _lock_client(client_pk, user=None):
        return _lock_scoped_client(client_pk, user)

    @staticmethod
    def _lock_client_orders(client_pk):
        return list(
            Order.all_objects.select_for_update()
            .filter(client_id=client_pk)
            .only("pk", "status")
            .order_by("pk")
        )

    @staticmethod
    def _deactivate_portal_user(user):
        if user.is_active:
            user.is_active = False
            user.save(update_fields=["is_active"])

    @transaction.atomic
    def perform_destroy(self, instance):
        client_pk = instance.pk
        self._lock_client_orders(client_pk)
        instance = self._lock_client(client_pk, self.request.user)
        self._lock_client_orders(client_pk)
        portal_user = instance.user
        instance.delete()
        self._deactivate_portal_user(portal_user)

    @action(detail=True, methods=["post"], url_path="password")
    @transaction.atomic
    def set_password(self, request, pk=None):
        client = self._lock_client(self.get_object().pk, request.user)
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
        date_from = parse_iso_date(request.query_params.get("date_from"))
        date_to = parse_iso_date(request.query_params.get("date_to"))
        validate_date_range(date_from, date_to)
        departments = statement_departments(request.query_params)
        sections = statement_sections(request.query_params, CLIENT_SECTIONS)
        export = statement_format(request.query_params)
        client = self.get_object()
        builder = (
            build_client_statement_pdf if export == "pdf" else build_client_statement
        )
        content = builder(
            client, date_from, date_to, departments=departments,
            sections=sections,
        )
        response = HttpResponse(content, content_type=STATEMENT_CONTENT_TYPES[export])
        response["Content-Disposition"] = (
            f'attachment; filename="client-{client.pk}-statement.{export}"'
        )
        log_event(
            "client_statement",
            f"Сформирована {export.upper()}-выписка клиента «{client.name}»",
            user=request.user,
            payload={"client_id": client.pk,
                     "date_from": str(date_from) if date_from else None,
                     "date_to": str(date_to) if date_to else None,
                     "departments": list(departments) if departments else None,
                     "sections": list(sections) if sections else None,
                     "format": export},
        )
        return response

    @action(detail=False, methods=["get"], url_path="statement")
    def all_statement(self, request):
        date_from = parse_iso_date(request.query_params.get("date_from"))
        date_to = parse_iso_date(request.query_params.get("date_to"))
        validate_date_range(date_from, date_to)
        departments = statement_departments(request.query_params)
        sections = statement_sections(request.query_params, ALL_CLIENT_SECTIONS)
        export = statement_format(request.query_params)
        builder = (
            build_all_clients_statement_pdf if export == "pdf"
            else build_all_clients_statement
        )
        client_ids = tuple(
            self.get_queryset().values_list("pk", flat=True)
        )
        content = builder(
            date_from, date_to, departments=departments, sections=sections,
            client_ids=client_ids,
        )
        response = HttpResponse(content, content_type=STATEMENT_CONTENT_TYPES[export])
        response["Content-Disposition"] = (
            f'attachment; filename="clients-full-statement.{export}"'
        )
        log_event(
            "clients_statement",
            f"Сформирована общая {export.upper()}-выписка по клиентам",
            user=request.user,
            payload={
                "date_from": str(date_from) if date_from else None,
                "date_to": str(date_to) if date_to else None,
                "departments": list(departments) if departments else None,
                "sections": list(sections) if sections else None,
                "format": export,
            },
        )
        return response

    def _price_rows(self, client):
        prices = {
            (row.product_id, row.currency): row
            for row in ClientPrice.objects.filter(client=client).select_related("updated_by")
        }
        return [
            {
                "product": product.id,
                "product_label": str(product),
                "currency": currency,
                "price": money_string(prices[(product.id, currency)].price)
                if (product.id, currency) in prices else None,
                "updated_at": prices[(product.id, currency)].updated_at
                if (product.id, currency) in prices else None,
                "updated_by_name": prices[(product.id, currency)].updated_by.username
                if ((product.id, currency) in prices
                    and prices[(product.id, currency)].updated_by) else None,
            }
            for product in Product.objects.filter(is_active=True).order_by(
                "name", "color", "weight_kg")
            for currency, _label in ClientPrice.CURRENCIES
        ]

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
            client = self._lock_client(client.pk, request.user)
            for row in serializer.validated_data["prices"]:
                product = row["product"]
                currency = row["currency"]
                price = row.get("price")
                if price is None:
                    deleted, _ = ClientPrice.objects.filter(
                        client=client, product=product, currency=currency).delete()
                    removed += deleted
                    continue
                _, created = ClientPrice.objects.update_or_create(
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

    def _debt_orders(self, client):
        # Заказы уже предзагружены queryset'ом — фильтруем кэш, не создавая
        # новый запрос на каждого клиента.
        orders = debt_orders(client.orders.all())
        orders.sort(key=lambda o: o.created_at, reverse=True)
        return orders

    @action(detail=False, methods=["get"], url_path="debts")
    def debts(self, request):
        """Агрегированные долги по клиентам (в рамках видимых отделов)."""
        today = timezone.localdate()
        params = request.query_params
        date_from = parse_iso_date(params.get("date_from"))
        date_to = parse_iso_date(params.get("date_to"))
        validate_date_range(date_from, date_to)
        debt_min = parse_money_param(
            params.get("remaining_min"),
            "Минимальный остаток",
        )
        debt_max = parse_money_param(
            params.get("remaining_max"),
            "Максимальный остаток",
        )
        remaining_currency = params.get("remaining_currency")
        if remaining_currency and remaining_currency not in ("KZT", "USD"):
            raise ValidationError({
                "detail": "Неизвестная валюта остатка",
                "code": "bad_currency",
            })
        if debt_min is not None and debt_max is not None and debt_min > debt_max:
            raise ValidationError(
                {"detail": "Минимальный остаток больше максимального",
                 "code": "bad_range"})
        store_id = parse_store_id(params.get("store"))

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
                orders = [o for o in orders if o.store_id == store_id]
            totals = sum_by_currency(orders, order_remaining)
            currency = primary_currency(totals, fallback=client.currency)
            debt = totals.get(currency, Decimal("0"))
            if debt <= 0:
                continue
            filtered_debt = (
                totals.get(remaining_currency, Decimal("0"))
                if remaining_currency
                else debt
            )
            if debt_min is not None and filtered_debt < debt_min:
                continue
            if debt_max is not None and filtered_debt > debt_max:
                continue
            stores = [s for s in client.stores.all()
                      if any(o.store_id == s.id for o in orders)]
            rows.append({
                "client_id": client.id,
                "client_name": client.name,
                "client_phone": client.phone,
                # debt_total — основная валюта; полная раскладка рядом.
                "debt_total": money_string(debt),
                "debt_currency": currency,
                "debt_by_currency": as_money_strings(totals),
                "orders_count": len(orders),
                "unpaid_count": sum(1 for o in orders if o.payment_status == "unpaid"),
                "partial_count": sum(1 for o in orders if o.payment_status == "partial"),
                "stores_count": len(stores),
                "overdue_count": sum(
                    1 for s in stores
                    if s.payment_schedule_type != "none" and is_payment_window_open(s, today)
                ),
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
        orders = list(self._debt_orders(client))
        totals = sum_by_currency(orders, order_remaining)
        currency = primary_currency(totals, fallback=client.currency)
        debt = totals.get(currency, Decimal("0"))
        stores = [s for s in client.stores.all()
                  if any(o.store_id == s.id for o in orders)]
        # За всё время: отгруженные заказы «в долг», включая уже погашенные.
        lifetime = [o for o in client.orders.all()
                    if o.status == "shipped" and o.settlement_intent == "debt"]
        lifetime_total = sum_by_currency(lifetime, lambda o: o.total_amount)
        lifetime_paid = sum_by_currency(lifetime, lambda o: o.paid_total)
        # Просрочено = остаток по заказам магазинов, у которых сегодня день оплаты.
        overdue_stores = {s.id for s in stores
                          if s.payment_schedule_type != "none"
                          and is_payment_window_open(s, today)}
        overdue = sum_by_currency(
            [o for o in orders if o.store_id in overdue_stores], order_remaining)
        return Response({
            "client": self.get_serializer(client).data,
            "debt_total": money_string(debt),
            "debt_currency": currency,
            "debt_by_currency": as_money_strings(totals),
            "lifetime_total": money_string(
                lifetime_total.get(currency, Decimal("0"))),
            "lifetime_paid": money_string(
                lifetime_paid.get(currency, Decimal("0"))),
            "lifetime_by_currency": {
                code: {
                    "total": money_string(lifetime_total.get(code, Decimal("0"))),
                    "paid": money_string(lifetime_paid.get(code, Decimal("0"))),
                }
                for code in sorted({*lifetime_total, *lifetime_paid})
            },
            "overdue_total": money_string(overdue.get(currency, Decimal("0"))),
            "overdue_by_currency": as_money_strings(overdue),
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
    queryset = Store.objects.select_related("client__user").order_by("id")
    serializer_class = StoreSerializer
    pagination_class = OptInPageNumberPagination

    required_perms = {
        "list": "clients.view", "retrieve": "clients.view",
        "create": "clients.create", "update": "clients.edit",
        "partial_update": "clients.edit", "destroy": "clients.delete",
        "check_overdue": "clients.edit",
        "debts": "reports.view",
        "debt_detail": "reports.view",
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

    @transaction.atomic
    def perform_update(self, serializer):
        # Canonical order is Client -> Store, matching client deletion whose
        # cascade later reaches Store. Read the FK optimistically, lock every
        # possible Client in pk order, then lock/recheck the Store.
        current_client_id = (
            Store.objects.filter(pk=serializer.instance.pk)
            .values_list("client_id", flat=True)
            .first()
        )
        if current_client_id is None:
            raise StoreChanged()
        requested_client = serializer.validated_data.get("client")
        if (
            requested_client is not None
            and requested_client.pk != current_client_id
        ):
            raise ValidationError({
                "detail": "Клиента магазина изменить нельзя — создайте новый магазин",
                "code": "client_locked",
            })
        next_client_id = (
            requested_client.pk if requested_client is not None else current_client_id
        )
        locked_clients = {
            client_pk: _lock_scoped_client(client_pk, self.request.user)
            for client_pk in sorted({current_client_id, next_client_id})
        }
        try:
            store = Store.objects.select_for_update().get(pk=serializer.instance.pk)
        except Store.DoesNotExist as exc:
            raise StoreChanged() from exc
        if store.client_id not in locked_clients:
            raise StoreChanged()
        serializer.instance = store
        serializer.save(client=locked_clients[next_client_id])

    @transaction.atomic
    def perform_destroy(self, instance):
        current_client_id = (
            Store.objects.filter(pk=instance.pk)
            .values_list("client_id", flat=True)
            .first()
        )
        if current_client_id is None:
            raise StoreChanged()
        _lock_scoped_client(current_client_id, self.request.user)
        try:
            store = Store.objects.select_for_update().get(pk=instance.pk)
        except Store.DoesNotExist as exc:
            raise StoreChanged() from exc
        if store.client_id != current_client_id:
            raise StoreChanged()
        store.delete()

    @action(detail=True, methods=["get"], url_path="debt-detail")
    def debt_detail(self, request, pk=None):
        from apps.orders.serializers import OrderSerializer
        store = self.get_object()
        today = timezone.localdate()
        qs = with_order_api_relations(store.orders.all()).order_by("created_at")
        orders = debt_orders(qs)
        totals = sum_by_currency(orders, order_remaining)
        currency = primary_currency(totals, fallback=store.client.currency)
        return Response({
            "store": StoreSerializer(store).data,
            "client_name": store.client.name,
            "debt_total": money_string(totals.get(currency, Decimal("0"))),
            "debt_currency": currency,
            "debt_by_currency": as_money_strings(totals),
            "window_open": is_payment_window_open(store, today),
            "orders": OrderSerializer(orders, many=True, context={"request": request}).data,
        })

    @action(detail=False, methods=["get"], url_path="debts")
    def debts(self, request):
        """Долги по магазинам: сумма непогашенного, расписание, окно/просрочка."""
        today = timezone.localdate()
        rows = []
        # Долг считается по quantity/unit_price позиции — товар здесь не
        # читается, поэтому джоин к каталогу не нужен. Набор сразу сужен до
        # отгруженных «в долг»: остальные заказы debt_orders всё равно отсеет.
        debt_candidates = Order.objects.filter(
            status="shipped", settlement_intent="debt",
        ).prefetch_related("items", "payments")
        for store in self.get_queryset().prefetch_related(
                Prefetch("orders", queryset=debt_candidates)):
            orders = debt_orders(store.orders.all())
            totals = sum_by_currency(orders, order_remaining)
            currency = primary_currency(totals, fallback=store.client.currency)
            debt = totals.get(currency, Decimal("0"))
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
                "debt_currency": currency,
                "debt_by_currency": as_money_strings(totals),
                "orders_count": len(orders),
                "window_open": window_open,
                # просрочка: окно сегодня открыто, но долг ещё висит
                "overdue": window_open and store.payment_schedule_type != "none",
            })
        rows.sort(key=lambda r: (
            r["debt_currency"],
            -Decimal(r["debt_total"]),
            r["store_name"].casefold(),
        ))
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
