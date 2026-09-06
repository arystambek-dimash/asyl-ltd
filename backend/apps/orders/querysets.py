"""Persistence plans for order API serialization.

The serializer traverses several related objects. Keeping the loading plan in
one place prevents list and detail endpoints from drifting back into N+1
queries when serializer fields evolve.
"""

from datetime import date, timedelta

from django.db.models import Prefetch, Q, QuerySet
from django.utils import timezone

from apps.common.query_params import (
    parse_iso_date,
    parse_search_param,
    plate_search_q,
)

from .models import Order, Payment, StatusChangeRequest

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
    подтверждённые заказы без даты приезда или с датой не позже сегодня:
    машина может приехать позже плана, и ждущая работа прошлых дней не
    прячется; будущие приезды доску не засоряют. Выехавшие — за окно
    ``completed_order_days``.

    Другой день ``D`` — что происходило именно тогда: выехали ``D``, заехали
    или начали грузиться ``D``, либо подтверждённые с приездом на ``D``.

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
    if day is None or day == today:
        since = today - timedelta(days=max(0, completed_order_days - 1))
        return queryset.filter(
            Q(status__in=BOARD_ACTIVE_STATUSES)
            | (
                Q(status="confirmed")
                & (Q(arrival_date__isnull=True) | Q(arrival_date__lte=today))
            )
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
        | Q(status="confirmed", arrival_date=day)
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
    return (
        queryset
        .select_related(
            "client__user", "store", "warehouse", "shipment",
            "debt_override_by", "deleted_by"
        )
        .prefetch_related(
            "items__product",
            "client__prices",
            Prefetch("payments", queryset=payments),
            Prefetch("status_requests", queryset=status_requests),
        )
    )
