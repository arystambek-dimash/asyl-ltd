"""Persistence plans for order API serialization.

The serializer traverses several related objects. Keeping the loading plan in
one place prevents list and detail endpoints from drifting back into N+1
queries when serializer fields evolve.
"""

from collections import defaultdict
from datetime import date, timedelta

from decimal import Decimal

from django.db.models import (
    Case,
    CharField,
    Count,
    DecimalField,
    Exists,
    F,
    Max,
    OuterRef,
    Prefetch,
    Q,
    QuerySet,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Cast, Coalesce, Concat, Greatest, NullIf, Trim, TruncDate
from django.db.models.lookups import Exact, IContains
from rest_framework.exceptions import ValidationError
from django.utils import timezone

from apps.common.plates import is_valid_plate, normalize_plate, plate_match_key
from apps.common.query_params import (
    filter_date_range,
    parse_date_range,
    parse_iso_date,
    parse_search_param,
    parse_store_id,
    plate_search_q,
)
from apps.sales.access import scope_by_client_department
from apps.sales.labels import UNASSIGNED_CODE
from apps.shipments.models import ShipmentWagon

from .debt import DEBT_STATUS, overpaid_amount
from .models import Order, OrderItem, Payment, StatusChangeRequest
from .statuses import (
    AWAITING_SHIPMENT_STATUSES,
    ON_POST_STATUSES,
    PUBLIC_STATUS_LABELS,
    statuses_in_group,
)


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


def payment_refundable_sum():
    """Сколько ещё можно вернуть (Payment.available_for_refund): каждая оплата за
    вычетом завершённых и начатых возвратов, не ниже нуля."""
    return Sum(
        Greatest(
            F("amount") - F("refunded_amount") - F("pending_refund_amount"), ZERO_MONEY
        ),
        output_field=MONEY,
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


def awaiting_payment_orders(queryset: QuerySet[Order]) -> QuerySet[Order]:
    """«Ждут оплаты»: несогласованные долги — рабочий список кассы.

    Остаток отгруженного заказа сразу долг клиента (orders/debt.py). Но если
    клиент не выбирал «в долг» (способ оплаты не выбран — ``pending``, оплата
    не дошла — ``instant``), касса должна взять оплату или согласовать долг.
    """
    return with_order_amounts(
        queryset.filter(status=DEBT_STATUS)
        .exclude(settlement_intent="debt")
        .exclude(payment_status="settled"),
        select=False,
    ).filter(amount_remaining__gt=0)


def awaiting_shipment_orders(queryset: QuerySet[Order]) -> QuerySet[Order]:
    """«К отгрузке»: заказ ждёт отгрузки и оплачен не полностью.

    Касса берёт по нему предоплату (statuses.is_payment_open) — наличными,
    своим Kaspi-терминалом или отметкой об удалённой оплате.
    """
    return with_order_amounts(
        queryset.filter(status__in=AWAITING_SHIPMENT_STATUSES), select=False
    ).filter(amount_remaining__gt=0)


def overpaid_orders(queryset: QuerySet[Order]) -> QuerySet[Order]:
    """«К возврату»: заказы с переплатой (debt.overpaid_amount).

    Подтверждённая оплата неизменна, поэтому после уменьшения количества или
    цены излишек остаётся у заказа, пока касса не оформит возврат. Заказы с
    деньгами — почти вся история отдела, а подзапрос суммы на каждый заказ
    повторялся бы на каждой странице и в COUNT опрашиваемого экрана, — поэтому
    переплата считается сразу группирующими запросами (:func:`order_overpaid_by_id`),
    а выборка сужается до найденных заказов.
    """
    return queryset.filter(pk__in=list(order_overpaid_by_id(queryset)))


def _item_totals_by_order(order_ids) -> dict[int, Decimal]:
    """Сумма позиций каждого заказа из ``order_ids`` одним группирующим запросом."""
    return dict(
        OrderItem.objects.filter(order_id__in=order_ids)
        .order_by()
        .values("order_id")
        .annotate(value=item_value_sum())
        .values_list("order_id", "value")
    )


def order_overpaid_by_id(queryset: QuerySet[Order]) -> dict[int, Decimal]:
    """Переплата по каждому заказу выборки (debt.overpaid_amount) за два группирующих запроса.

    Переплата бывает только при подтверждённых деньгах: суммы позиций берутся
    лишь у заказов, где они есть. Заказа нет в словаре — переплаты нет.
    """
    confirmed = Payment.objects.filter(
        order_id__in=queryset.order_by().values("pk"), status="confirmed"
    ).order_by()
    money = (
        confirmed.values("order_id", "order__status")
        .annotate(value=payment_refundable_sum())
        .values_list("order_id", "order__status", "value")
    )
    totals = _item_totals_by_order(confirmed.values("order_id"))
    zero = Decimal("0")
    overpaid = (
        (pk, overpaid_amount(status, refundable, totals.get(pk) or zero))
        for pk, status, refundable in money
    )
    return {pk: amount for pk, amount in overpaid if amount > 0}


def order_remaining_by_id(queryset: QuerySet[Order]) -> dict[int, Decimal]:
    """Остаток по каждому заказу выборки за два группирующих запроса.

    :func:`with_order_amounts` считает остаток подзапросом на каждый заказ —
    список должников берёт все отгруженные «в долг» за всю историю, и цена
    росла с каждой оплатой (на 40 тыс. заказов — 50 тыс. повторов подзапроса).
    Формулы те же, суммы по позициям и оплатам берутся разом по всей выборке.
    Заказа нет в словаре — остаток ноль.
    """
    ids = queryset.order_by().values("pk")
    totals = _item_totals_by_order(ids)
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


def order_department(prefix: str = ""):
    """Отдел заказа для фильтров и сводок: без своего отдела — отдел клиента.

    Заказ с пустым ``department`` (заявка портала, старые данные) учитывается
    там же, где его клиент, — как в кассе и в доступе отдела к клиентам
    (sales.access.scope_by_client_department). Нет отдела ни у заказа, ни у
    клиента — пустая строка. ``prefix`` — путь до заказа (``order__`` у оплат).
    """
    return Case(
        When(
            **{f"{prefix}department": ""},
            then=Coalesce(F(f"{prefix}client__department__code"), Value("")),
        ),
        default=F(f"{prefix}department"),
        output_field=CharField(),
    )


def department_q(code: str, prefix: str = "") -> Q:
    """Условие ``?department=``: заказы отдела ``code``, ``__unassigned`` — без отдела."""
    return Q(Exact(order_department(prefix), "" if code == UNASSIGNED_CODE else code))


def filter_order_scope(queryset, params, *, prefix="", date_field=None, department_extra=None):
    """Общие фильтры списков заказов и оплат: ``?department=``, ``?store=`` и период.

    ``prefix`` — путь до заказа (``order__`` у оплат), период — по полю
    ``date_field``, если оно задано. ``department_extra`` добавляется к
    условию отдела через ИЛИ.
    """
    department = params.get("department")
    if department:
        match = department_q(department, prefix)
        if department_extra is not None:
            match |= department_extra
        queryset = queryset.filter(match)
    store = parse_store_id(params.get("store"))
    if store:
        queryset = queryset.filter(**{f"{prefix}store_id": store})
    if date_field:
        date_from, date_to = parse_date_range(params)
        queryset = filter_date_range(queryset, date_field, date_from, date_to)
    return queryset


def filter_status_group(queryset: QuerySet[Order], group: str | None) -> QuerySet[Order]:
    """``?status_group=``: публичная группа статусов («Ожидает загрузки» — это
    confirmed/arrived/loading, точечный status для неё не годится)."""
    if not group:
        return queryset
    if group not in PUBLIC_STATUS_LABELS:
        raise ValidationError({"detail": "Неизвестная группа статусов", "code": "bad_status_group"})
    return queryset.filter(status__in=statuses_in_group(group))


def _client_name(prefix: str):
    """Имя клиента, как ``Client.name``: ФИО пользователя, без него — логин."""
    return Coalesce(
        NullIf(
            Trim(Concat(f"{prefix}user__first_name", Value(" "), f"{prefix}user__last_name")),
            Value(""),
        ),
        F(f"{prefix}user__username"),
    )


def client_search_q(search: str, prefix: str = "client__") -> Q:
    """Клиент по имени, названию ТОО или телефону — одинаково во всех списках.

    ``prefix`` — путь до клиента (``order__client__`` у оплат).
    """
    return (
        Q(IContains(_client_name(prefix), search))
        | Q(**{f"{prefix}company_name__icontains": search})
        | Q(**{f"{prefix}phone__icontains": search})
    )


def filter_order_search(queryset: QuerySet[Order], search: str) -> QuerySet[Order]:
    if not search:
        return queryset
    queryset = queryset.alias(search_id=Cast("id", CharField()))
    return queryset.filter(
        client_search_q(search)
        | Q(search_id__icontains=search)
        | order_plate_q(search)
    )


# Номер вагона ищется от трёх цифр: «12» нашло бы половину вагонов за месяц.
WAGON_SEARCH_MIN_DIGITS = 3


def order_plate_q(search: str) -> Q:
    """Номер тягача или прицепа — в любой записи (с пробелами, с/без «KG»),
    а у вагонного заказа — и номер любого вагона отгрузки по отчёту."""
    condition = plate_search_q("truck_number", search) | plate_search_q("trailer_number", search)
    digits = "".join(search.split())
    if digits.isascii() and digits.isdigit() and len(digits) >= WAGON_SEARCH_MIN_DIGITS:
        # Exists, а не JOIN: у заказа 12 вагонов — строка не размножается.
        condition |= Q(Exists(
            ShipmentWagon.objects.filter(shipment__order_id=OuterRef("pk"), number__contains=digits)
        ))
    return condition


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
    if key == "amount":
        queryset = with_order_amounts(queryset, select=False)
    if key == "client":
        queryset = queryset.alias(sort_client=_client_name("client__"))
    # Явная сортировка — строго по выбранной колонке: без скрытого
    # «отгруженные в конец», иначе порядок по дате выглядит вперемешку.
    direction = "-" if descending else ""
    fields: list[str] = []
    if key == "amount":
        fields.append(direction + "currency")
    fields.extend([direction + columns[key], direction + "id"])
    return queryset.order_by(*dict.fromkeys(fields))


# Поиск не привязан ко дню, но выехавшие заказы старше месяца на посту не
# нужны — для них есть архив заказов.
BOARD_SEARCH_SHIPPED_DAYS = 30


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
        scope = Q(status__in=AWAITING_SHIPMENT_STATUSES) | Q(
            status="shipped", shipment__shipped_at__date__gte=since
        )
        match = order_plate_q(search) | client_search_q(search)
        # ``str.isdigit()`` истинно и для «²», а ``int()`` на нём падает —
        # номером заказа считаем только ASCII-цифры.
        if search.isascii() and search.isdigit():
            match |= Q(id=int(search))
        return queryset.filter(scope & match)

    def waiting_on(when: date) -> Q:
        return Q(status="confirmed") & (
            Q(arrival_date=when) | Q(arrival_date__isnull=True, created_at__date=when)
        )

    # Заказ на посту живёт на доске, пока не выедет, — сколько бы дней ни
    # длилась погрузка.
    if day is None or day == today:
        since = today - timedelta(days=max(0, completed_order_days - 1))
        return queryset.filter(
            Q(status__in=ON_POST_STATUSES)
            | waiting_on(today)
            | Q(status="shipped", shipment__shipped_at__date__gte=since)
        )
    return queryset.filter(
        Q(status="shipped", shipment__shipped_at__date=day)
        | (
            Q(status__in=ON_POST_STATUSES)
            & (
                Q(shipment__arrived_at__date=day)
                | Q(shipment__loading_started_at__date=day)
            )
        )
        | waiting_on(day)
    )


def planned_day():
    """Плановый день заказа: дата приезда, а без неё — день оформления.

    TruncDate считает день в часовом поясе проекта: Cast дал бы дату по UTC
    и ночные заказы уехали бы в соседний день.
    """
    return Coalesce("arrival_date", TruncDate("created_at"))


def awaiting_shipment_bags(warehouse, product_ids, *, exclude_order_id=None) -> dict[int, int]:
    """Мешки, уже обещанные клиентам со склада: {product_id: мешков}.

    Живые заказы того же склада, ждущие отгрузки: склад списывает их мешки
    только при отгрузке, поэтому в остатке они ещё числятся. Склад заказа с
    позициями всегда задан (его закрепляет сама база), корзину отсекает
    менеджер ``Order.objects``.
    """
    product_ids = set(product_ids)
    orders = Order.objects.filter(warehouse=warehouse, status__in=AWAITING_SHIPMENT_STATUSES)
    if exclude_order_id is not None:
        orders = orders.exclude(pk=exclude_order_id)
    bags = dict.fromkeys(product_ids, 0)
    bags.update(
        OrderItem.objects.filter(order__in=orders, product_id__in=product_ids)
        .order_by()
        .values("product_id")
        .annotate(total=Sum("quantity"))
        .values_list("product_id", "total")
    )
    return bags


# Фильтры быстрого ввода «Фуры»: без номера тягача (любой день) и все на сегодня.
TRANSPORT_QUEUE_FILTERS = ("missing", "today")


def transport_rows(queryset: QuerySet[Order]) -> QuerySet[Order]:
    """Лёгкая строка «Фур»: клиент, мешки и владелец номера — без истории заказа."""
    bags = (
        OrderItem.objects.filter(order_id=OuterRef("pk"))
        .order_by()
        .values("order_id")
        .annotate(value=Sum("quantity"))
        .values("value")
    )
    return (
        queryset.select_related(None)
        .prefetch_related(None)
        .select_related("client__user", "truck_number_set_by")
        .annotate(planned_on=planned_day(), bags=Coalesce(Subquery(bags), 0))
    )


def transport_queue(queryset: QuerySet[Order], scope: str) -> QuerySet[Order]:
    """Подтверждённые фуры, которым вводят номер тягача и прицепа.

    ``missing`` — все без номера тягача, старые сверху; ``today`` — все с
    плановым днём сегодня, с номером и без.
    """
    queryset = transport_rows(queryset.filter(status="confirmed", transport_type="truck"))
    if scope == "today":
        queryset = queryset.filter(planned_on=timezone.localdate())
    else:
        queryset = queryset.filter(truck_number="")
    return queryset.order_by("planned_on", "id")


def recent_transport_pairs(client_ids, *, limit: int) -> dict[int, list[dict]]:
    """Последние пары «тягач + прицеп» клиентов — подсказки «как в прошлый раз».

    Один группирующий запрос на всю страницу: каждая пара со своим последним
    заказом. Одна машина в разной записи — одна подсказка (слитно); свободный
    текст вместо номера («самовывоз») не подсказывается.
    """
    if not client_ids:
        return {}
    rows = (
        Order.objects.filter(client_id__in=client_ids, transport_type="truck")
        .exclude(truck_number="")
        .order_by()
        .values("client_id", "truck_number", "trailer_number")
        .annotate(last_id=Max("id"))
    )
    pairs: dict[int, list[dict]] = defaultdict(list)
    seen: dict[int, set] = defaultdict(set)
    for row in sorted(rows, key=lambda row: row["last_id"], reverse=True):
        client_id, truck, trailer = row["client_id"], row["truck_number"], row["trailer_number"]
        key = (plate_match_key(truck), plate_match_key(trailer))
        if (
            len(pairs[client_id]) >= limit
            or key in seen[client_id]
            or not is_valid_plate(truck)
            or (trailer and not is_valid_plate(trailer))
        ):
            continue
        seen[client_id].add(key)
        pairs[client_id].append(
            {"truck_number": normalize_plate(truck), "trailer_number": normalize_plate(trailer)}
        )
    return dict(pairs)


def shipping_calendar_days(queryset: QuerySet[Order], first_day: date, last_day: date) -> list[dict]:
    """Итоги отгрузки по дням месяца: сколько заказов ждёт погрузки и сколько уехало.

    День заказа в очереди — плановый приезд, а без него день оформления;
    у выехавшего — день фактической отгрузки. Одна и та же поездка попадает
    в календарь один раз, поэтому счётчики дня не пересекаются.
    """
    waiting = (
        queryset.filter(status__in=AWAITING_SHIPMENT_STATUSES)
        .annotate(day=planned_day())
        .filter(day__gte=first_day, day__lte=last_day)
        .values("day")
        .annotate(orders=Count("id", distinct=True))
    )
    shipped = (
        queryset.filter(status="shipped", shipment__shipped_at__date__gte=first_day,
                        shipment__shipped_at__date__lte=last_day)
        .annotate(day=TruncDate("shipment__shipped_at"))
        .values("day")
        .annotate(orders=Count("id", distinct=True))
    )
    days: dict[date, dict] = {}
    for row in waiting:
        days.setdefault(row["day"], {"waiting": 0, "shipped": 0})["waiting"] = row["orders"]
    for row in shipped:
        days.setdefault(row["day"], {"waiting": 0, "shipped": 0})["shipped"] = row["orders"]
    return [{"day": day.isoformat(), **values} for day, values in sorted(days.items())]


def post_board_params(params) -> dict:
    """``?day=`` и ``?search=`` доски — одинаково для заказов и истории AI."""
    return {
        "day": parse_iso_date(params.get("day")),
        "search": parse_search_param(params.get("search")),
    }


# Очередь подтверждения кассы («Заявки и оплаты»): оплата в работе без счёта
# платёжного сервиса. Кассой вручную закрывается всё, за что сервис не
# отвечает, — это включает старые клиентские способы оплаты, поэтому методы не
# перечисляются: устойчивый признак один — у заявки нет счёта провайдера.
# Очередь общая для всех отделов.
CASHIER_QUEUE_PAYMENT = Q(
    apipay_invoice__isnull=True,
    status__in=Payment.IN_PROGRESS_STATUSES,
)


def cashier_queue_payments(user) -> QuerySet[Payment]:
    """Оплаты очереди кассы, которые видит ``user``: без счёта провайдера, в живых заказах.

    Одна выборка для «Кассы» и счётчика «Подтвердить оплаты» на дашборде.
    Оплаты в работе видны кассе любого отдела.
    """
    return scope_by_client_department(
        Payment.objects.filter(CASHIER_QUEUE_PAYMENT, order__deleted_at__isnull=True),
        user,
        client_path="order__client",
        shared=CASHIER_QUEUE_PAYMENT,
    )


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
    ).prefetch_related("payment_refunds__requested_by")
    status_requests = StatusChangeRequest.objects.select_related(
        "requested_by", "decided_by"
    )
    return queryset.select_related(
        "client__user",
        "client__department",
        "store",
        "warehouse",
        "shipment",
        "deleted_by",
    ).prefetch_related(
        "items__product",
        "client__prices",
        # Вагоны отгрузки по отчёту — везде, где виден номер вагонного заказа.
        "shipment__wagons",
        Prefetch("payments", queryset=payments),
        Prefetch("status_requests", queryset=status_requests),
    )
