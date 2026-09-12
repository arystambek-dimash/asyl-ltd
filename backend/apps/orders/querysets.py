"""Persistence plans for order API serialization.

The serializer traverses several related objects. Keeping the loading plan in
one place prevents list and detail endpoints from drifting back into N+1
queries when serializer fields evolve.
"""

from datetime import date, timedelta

from decimal import Decimal

from django.db.models import (
    Case,
    CharField,
    DecimalField,
    F,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    QuerySet,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Cast, Coalesce, Concat, Greatest, NullIf, Trim
from rest_framework.exceptions import ValidationError
from django.utils import timezone

from apps.common.query_params import (
    parse_iso_date,
    parse_search_param,
    plate_search_q,
)

from .models import Order, OrderItem, Payment, StatusChangeRequest


MONEY = DecimalField(max_digits=30, decimal_places=2)
ZERO_MONEY = Value(Decimal("0"), output_field=MONEY)


def item_value_sum():
    """Сумма позиций заказа: позиция без цены даёт ноль, как в модели."""
    return Sum(F("quantity") * Coalesce("unit_price", ZERO_MONEY), output_field=MONEY)


def payment_net_sum():
    """Сумма подтверждённых оплат: каждая за вычетом завершённых возвратов и не ниже нуля."""
    return Sum(
        Greatest(F("amount") - F("refunded_amount"), ZERO_MONEY), output_field=MONEY
    )


def with_order_amounts(
    queryset: QuerySet[Order], *, select: bool = True
) -> QuerySet[Order]:
    """Read-only totals without hydrating every historical item and payment.

    Separate subqueries avoid multiplying items by payments. Match the model:
    unpriced items contribute zero, only confirmed payments count, and each
    payment's net amount is clamped separately after completed refunds.
    Подзапросы коррелированные — по одному на строку; для расчёта по всей
    выборке разом есть :func:`order_remaining_by_id`.
    """
    items = (
        OrderItem.objects.filter(order_id=OuterRef("pk"))
        .order_by()
        .values("order_id")
        .annotate(value=item_value_sum())
    )
    payments = (
        Payment.objects.filter(order_id=OuterRef("pk"), status="confirmed")
        .order_by()
        .values("order_id")
        .annotate(value=payment_net_sum())
    )
    annotate = queryset.annotate if select else queryset.alias
    queryset = annotate(
        amount_total=Coalesce(
            Subquery(items.values("value"), output_field=MONEY), ZERO_MONEY
        ),
        amount_paid=Coalesce(
            Subquery(payments.values("value"), output_field=MONEY), ZERO_MONEY
        ),
    )
    annotate = queryset.annotate if select else queryset.alias
    return annotate(amount_remaining=F("amount_total") - F("amount_paid"))


def order_remaining_by_id(queryset: QuerySet[Order]) -> dict[int, Decimal]:
    """Остаток по каждому заказу выборки за два группирующих запроса.

    :func:`with_order_amounts` считает остаток подзапросом на каждый заказ —
    список должников берёт все отгруженные «в долг» за всю историю, и цена
    росла с каждой оплатой (на 40 тыс. заказов — 50 тыс. повторов подзапроса).
    Формулы те же, суммы по позициям и оплатам берутся разом по всей выборке.
    Заказа нет в словаре — остаток ноль.
    """
    ids = queryset.order_by().values("pk")
    totals = dict(
        OrderItem.objects.filter(order_id__in=ids)
        .order_by()
        .values("order_id")
        .annotate(value=item_value_sum())
        .values_list("order_id", "value")
    )
    paid = dict(
        Payment.objects.filter(order_id__in=ids, status="confirmed")
        .order_by()
        .values("order_id")
        .annotate(value=payment_net_sum())
        .values_list("order_id", "value")
    )
    zero = Decimal("0")
    return {
        pk: (totals.get(pk) or zero) - (paid.get(pk) or zero)
        for pk in totals.keys() | paid.keys()
    }


def filter_order_search(queryset: QuerySet[Order], search: str) -> QuerySet[Order]:
    if not search:
        return queryset
    # Same full name / username fallback displayed by Client.name.
    queryset = queryset.alias(
        search_name=Coalesce(
            NullIf(
                Trim(
                    Concat(
                        "client__user__first_name",
                        Value(" "),
                        "client__user__last_name",
                    )
                ),
                Value(""),
            ),
            F("client__user__username"),
        ),
        search_id=Cast("id", CharField()),
    )
    return queryset.filter(
        Q(search_name__icontains=search)
        | Q(search_id__icontains=search)
        | plate_search_q("truck_number", search)
    )


def order_page_sort(queryset: QuerySet[Order], ordering: str) -> QuerySet[Order]:
    """Sort before pagination; the legacy unparameterized API stays unchanged."""
    descending = ordering.startswith("-")
    key = ordering.removeprefix("-")
    columns = {
        "id": "id",
        "created": "created_at",
        "status": "status",
        "client": "sort_client",
        "amount": "amount_total",
    }
    if key not in columns:
        raise ValidationError(
            {"detail": "Неизвестная сортировка заказов", "code": "bad_ordering"}
        )
    queryset = queryset.alias(
        done_rank=Case(
            When(status="shipped", then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
    )
    if key == "amount":
        queryset = with_order_amounts(queryset, select=False)
    if key == "client":
        queryset = queryset.alias(
            sort_client=Coalesce(
                NullIf(
                    Trim(
                        Concat(
                            "client__user__first_name",
                            Value(" "),
                            "client__user__last_name",
                        )
                    ),
                    Value(""),
                ),
                F("client__user__username"),
            )
        )
    direction = "-" if descending else ""
    fields = ["done_rank"]
    if key == "amount":
        fields.append(direction + "currency")
    fields.extend([direction + columns[key], direction + "id"])
    return queryset.order_by(*dict.fromkeys(fields))


# Заказ уже на посту: машина заехала, грузится или ждёт выезда. Такие строки
# живут на доске, пока не выедут, — сколько бы дней ни длилась погрузка.
BOARD_ACTIVE_STATUSES = ("arrived", "loading", "loaded")
# Поиск не привязан ко дню, но выехавшие заказы старше месяца на посту не
# нужны — для них есть архив заказов.
BOARD_SEARCH_SHIPPED_DAYS = 30


def _client_name_query(search: str) -> Q:
    """``Client.name`` — это ФИО пользователя; ТОО ищут и по названию."""
    return (
        Q(client__user__first_name__icontains=search)
        | Q(client__user__last_name__icontains=search)
        | Q(client__company_name__icontains=search)
    )


def for_post_board(
    queryset: QuerySet[Order],
    completed_order_days: int,
    day: date | None = None,
    search: str = "",
) -> QuerySet[Order]:
    """Проекция живого поста: сегодняшняя работа, выбранный день или поиск.

    По умолчанию (``day`` пуст или сегодня) — всё, что сейчас на посту, плюс
    подтверждённые заказы сегодняшнего дня: с плановым приездом сегодня
    или созданные сегодня без даты. Старая очередь подтверждённых доску не
    засоряет — её находят поиском по номеру или выбором дня. Выехавшие — за
    окно ``completed_order_days``.

    Другой день ``D`` — что происходило именно тогда: выехали ``D``, заехали
    или начали грузиться ``D``, подтверждённые с приездом на ``D`` или
    созданные ``D`` без даты приезда.

    Непустой ``search`` игнорирует правило дня: ищет по номеру машины,
    клиенту и номеру заказа среди статусов доски, выехавшие — за последний
    месяц.
    """
    today = timezone.localdate()
    if search:
        since = today - timedelta(days=BOARD_SEARCH_SHIPPED_DAYS)
        scope = Q(status__in=("confirmed", *BOARD_ACTIVE_STATUSES)) | Q(
            status="shipped", shipment__shipped_at__date__gte=since
        )
        match = plate_search_q("truck_number", search) | _client_name_query(search)
        # ``str.isdigit()`` истинно и для «²», а ``int()`` на нём падает —
        # номером заказа считаем только ASCII-цифры.
        if search.isascii() and search.isdigit():
            match |= Q(id=int(search))
        return queryset.filter(scope & match)
    waiting_on = lambda when: Q(status="confirmed") & (  # noqa: E731 - small local predicate
        Q(arrival_date=when) | Q(arrival_date__isnull=True, created_at__date=when)
    )
    if day is None or day == today:
        since = today - timedelta(days=max(0, completed_order_days - 1))
        return queryset.filter(
            Q(status__in=BOARD_ACTIVE_STATUSES)
            | waiting_on(today)
            | Q(status="shipped", shipment__shipped_at__date__gte=since)
        )
    return queryset.filter(
        Q(status="shipped", shipment__shipped_at__date=day)
        | (
            Q(status__in=BOARD_ACTIVE_STATUSES)
            & (
                Q(shipment__arrived_at__date=day)
                | Q(shipment__loading_started_at__date=day)
            )
        )
        | waiting_on(day)
    )


def post_board_params(params) -> dict:
    """``?day=`` и ``?search=`` доски — одинаково для заказов и истории AI."""
    return {
        "day": parse_iso_date(params.get("day")),
        "search": parse_search_param(params.get("search")),
    }


def with_payment_api_relations(
    queryset: QuerySet[Payment], *, order_context: bool = False
) -> QuerySet[Payment]:
    """Loading plan for ``PaymentSerializer`` and its queue subclass.

    ``effective_status``/``can_issue`` read the provider invoice on every row,
    and ``can_restore`` compares the sibling payments of the same order against
    ``order.total_amount``, which sums the order items. All three relations
    must therefore be loaded eagerly. ``order_context`` adds the order/client
    columns the queue and transaction lists display.
    """
    queryset = queryset.select_related("apipay_invoice").prefetch_related(
        "order__items",
        "order__payments",
        "apipay_invoice__refunds",
        "payment_refunds__requested_by",
    )
    if order_context:
        queryset = queryset.select_related(
            "order__client__user", "order__store",
            "recorded_by", "received_by", "confirmed_by",
        )
    return queryset


def with_order_api_relations(queryset: QuerySet[Order]) -> QuerySet[Order]:
    payments = Payment.objects.select_related(
        "recorded_by", "received_by", "confirmed_by", "apipay_invoice"
    ).prefetch_related(
        "apipay_invoice__refunds", "payment_refunds__requested_by"
    )
    status_requests = StatusChangeRequest.objects.select_related(
        "requested_by", "decided_by"
    )
    return queryset.select_related(
        "client__user",
        "client__department",
        "store",
        "warehouse",
        "shipment",
        "debt_override_by",
        "deleted_by",
    ).prefetch_related(
        "items__product",
        "client__prices",
        Prefetch("payments", queryset=payments),
        Prefetch("status_requests", queryset=status_requests),
    )
