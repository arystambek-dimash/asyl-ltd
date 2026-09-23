"""Отчёт о вагонах → заказ и отгрузка: клиент, товары, цены, дубли.

Разбор текста — :mod:`apps.bots.parsing` (чистый). Здесь — всё, что требует
базы: кто клиент и в какой валюте ему считать, какие это товары, по какой
цене, не проведён ли отчёт уже (ботом или вручную). Если что-то не сошлось,
отчёт уходит на разбор человеку — склад, долг и заказы не трогаются.

Этим пользуются и WhatsApp-бот, и «Вставить отчёт» у грузчика: оба проводят
отчёт одной операцией :func:`conduct_rail_report` с правами своего
пользователя (у бота — сервисный пользователь без права менять цены).
"""
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.catalog.models import ClientPrice, Product, ProductAlias
from apps.catalog.services import remember_product_alias
from apps.clients.models import Client
from apps.common.money import money_string
from apps.common.text import dictionary_key, match_key
from apps.eventlog.services import log_event
from apps.orders.backdate import backdate_moment
from apps.orders.models import Order, OrderItem
from apps.orders.services import MESSAGE_MAX_LENGTH, confirm_order, lock_live_order
from apps.orders.statuses import AWAITING_SHIPMENT_STATUSES, CLOSED_STATUSES
from apps.orders.transport import order_wagons, rail_phrase
from apps.sales.access import assigned_department_id
from apps.shipments.access import assert_can_ship_transport
from apps.shipments.models import ShipmentWagon
from apps.shipments.services import RailWagon, rail_bags_mismatch, ship_rail_report
from apps.warehouse.services import resolve_warehouse, stock_balances

from .models import DEFAULT_DUPLICATE_WINDOW_DAYS, BotClientProfile, WhatsAppBotSettings
from .parsing import KG_PER_TON, RailReport, ReportIssue, bags_for, format_rail_report, format_tons

# Цена клиента дальше этого от цены прошлого вагонного заказа — на разбор.
DEFAULT_PRICE_TOLERANCE_PCT = Decimal("15")
# Ручной вагонный заказ того же клиента с теми же мешками в пределах ±2 дней —
# вероятно, этот отчёт уже внесли руками.
MANUAL_DUPLICATE_DAYS = 2
RAIL_TRANSPORT = "train"
# Клиент отчёта — чужого отдела: провести может только его отдел.
CLIENT_OTHER_DEPARTMENT = "client_other_department"
# Похоже, отчёт уже внесли руками — заказ без вагонов на те же мешки.
MANUAL_ORDER_DUPLICATE = "manual_order_duplicate"
# Цена клиента далеко от прошлого вагонного заказа (текст — с суммами).
PRICE_MISMATCH = "price_mismatch"
_HUNDRED = Decimal("100")


@dataclass(frozen=True)
class ResolvedItem:
    """Позиция будущего заказа: товар отчёта, мешки всех его вагонов и цена."""

    product: Product
    code: str  # код товара как в отчёте
    bags: int
    wagons: int
    unit_price: Decimal | None = None
    # Цена прошлого отгруженного вагонного заказа клиента (сверка цены).
    reference_price: Decimal | None = None
    reference_order_id: int | None = None

    @property
    def amount(self) -> Decimal | None:
        return None if self.unit_price is None else self.unit_price * self.bags


@dataclass(frozen=True)
class ResolvedReport:
    report: RailReport
    client: Client | None
    currency: str
    department: str
    wagons: tuple[RailWagon, ...]
    items: tuple[ResolvedItem, ...]
    # Из-за чего отчёт нельзя провести без человека (включая ошибки разбора).
    issues: tuple[ReportIssue, ...]
    # Не мешает провести, но стоит показать: например, склад уйдёт в минус.
    warnings: tuple[ReportIssue, ...] = ()
    profile: BotClientProfile | None = None
    # Код товара отчёта (ключ словаря) → товар: и у вагонов, не вошедших в
    # ``wagons`` (вес не делится на мешки), — для предпросмотра.
    products: dict[str, Product] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def total_bags(self) -> int:
        return sum(item.bags for item in self.items)


@transaction.atomic
def remember_client_profile(name: str, client: Client, currency: str, user) -> BotClientProfile:
    """Запомнить, кто клиент под названием из отчёта и в какой валюте ему считать.

    Бот проводит отчёты по профилю сам (валюта — это цены и долг), поэтому
    каждое изменение остаётся в журнале. Сотрудник отдела не перенаправляет
    название, под которым отчёты уже узнают клиента другого отдела (по
    профилю или по карточке): иначе его отчёты списали бы склад и записали
    долг не тому клиенту. Такое название меняет сотрудник без отдела.
    """
    key = dictionary_key(name, "название клиента", BotClientProfile._meta.get_field("name").max_length)
    if currency not in dict(Client.CURRENCIES):
        raise ValidationError({"detail": "Выберите валюту: KZT или USD", "code": "invalid_currency"})
    _assert_client_in_scope(user, client)
    # Под блокировкой профиля: два разбора одного названия не перетрут друг друга молча.
    BotClientProfile.objects.select_for_update().filter(name_key=key).first()
    current, _, _ = _resolve_client(name, [])
    if current is not None:
        _assert_client_in_scope(user, current, "Под этим названием отчёты узнают клиента другого отдела")
    fields = {"name": name, "client": client, "currency": currency}
    profile, _ = BotClientProfile.objects.update_or_create(
        name_key=key, defaults=fields, create_defaults={**fields, "created_by": user},
    )
    log_event(
        "clients",
        f"Клиент в отчётах о вагонах «{profile.name}» → {client.display_name}, {currency}",
        user=user,
        payload={"client_id": client.pk, "name": profile.name, "currency": currency},
    )
    return profile


def _client_keys(company_name: str, first_name: str, last_name: str) -> set[str]:
    """Как клиента могут назвать в отчёте: ТОО/ИП из карточки или имя клиента."""
    keys = {match_key(company_name), match_key(f"{first_name} {last_name}")}
    keys.discard("")
    return keys


def _resolve_client(name: str, issues: list) -> tuple[Client | None, str, BotClientProfile | None]:
    """Профиль бота (название → клиент и валюта) или ровно одно точное совпадение."""
    key = match_key(name)
    if not key:
        return None, "", None
    profile = (
        BotClientProfile.objects.select_related("client__department", "client__user")
        .filter(name_key=key)
        .first()
    )
    if profile is not None:
        return profile.client, profile.currency, profile
    matched = [
        pk
        for pk, company_name, first_name, last_name in Client.objects.values_list(
            "pk", "company_name", "user__first_name", "user__last_name"
        )
        if key in _client_keys(company_name, first_name, last_name)
    ]
    if len(matched) != 1:
        code, message = (
            ("client_unknown", f"Клиент «{name}» не найден — выберите клиента и валюту")
            if not matched
            else ("client_ambiguous", f"Под «{name}» подходят несколько клиентов — выберите нужного")
        )
        issues.append(ReportIssue(code, message, subject=name))
        return None, "", None
    client = Client.objects.select_related("department", "user").get(pk=matched[0])
    return client, client.currency, None


def _client_department(client: Client, issues: list) -> str:
    department = client.department
    if department is None:
        issues.append(ReportIssue(
            "client_department_missing",
            f"У клиента «{client.display_name}» не выбран отдел — закрепите отдел в карточке клиента",
        ))
        return ""
    if not department.is_active:
        issues.append(ReportIssue(
            "client_department_inactive",
            f"Отдел клиента «{client.display_name}» отключён — смените отдел в карточке клиента",
        ))
        return ""
    return department.code


def _resolve_products(report: RailReport, issues: list) -> dict[str, Product]:
    """Код товара отчёта → товар по словарю (catalog.ProductAlias)."""
    aliases = {
        alias.code: alias.product
        for alias in ProductAlias.objects.select_related("product").filter(
            code__in={wagon.code_key for wagon in report.wagons}
        )
    }
    products: dict[str, Product] = {}
    checked: set[str] = set()
    for wagon in report.wagons:
        # Один код — одна причина, а не по строке на каждый из 12 вагонов.
        if wagon.code_key in checked:
            continue
        checked.add(wagon.code_key)
        product = aliases.get(wagon.code_key)
        if product is None:
            issues.append(ReportIssue(
                "product_unknown", f"Неизвестный код товара «{wagon.code}» — выберите товар",
                wagon.line, wagon.code))
        elif not product.is_active:
            issues.append(ReportIssue(
                "product_archived", f"Товар «{product}» (код «{wagon.code}») в архиве",
                wagon.line, wagon.code))
        else:
            products[wagon.code_key] = product
    return products


def _resolve_wagons(report: RailReport, products: dict[str, Product], issues: list) -> list[RailWagon]:
    wagons = []
    for wagon in report.wagons:
        product = products.get(wagon.code_key)
        if product is None:
            continue
        bags = bags_for(wagon.weight_kg, product.weight_kg)
        if bags is None:
            issues.append(ReportIssue(
                "bags_not_whole",
                f"Вагон {wagon.number}: {format_tons(wagon.tons)} т не делится на мешки "
                f"по {format_tons(product.weight_kg)} кг",
                wagon.line, wagon.number))
            continue
        wagons.append(RailWagon(wagon.number, product, bags, wagon.weight_kg))
    return wagons


def _group_items(report: RailReport, wagons: list[RailWagon]) -> list[ResolvedItem]:
    """Одна позиция заказа на товар — в порядке первого вагона с ним."""
    codes = {wagon.number: wagon.code for wagon in report.wagons}
    bags: dict[int, int] = defaultdict(int)
    counts: dict[int, int] = defaultdict(int)
    first: dict[int, RailWagon] = {}
    for wagon in wagons:
        first.setdefault(wagon.product.pk, wagon)
        bags[wagon.product.pk] += wagon.bags
        counts[wagon.product.pk] += 1
    return [
        ResolvedItem(wagon.product, codes[wagon.number], bags[pk], counts[pk])
        for pk, wagon in first.items()
    ]


def _last_wagon_price(client: Client, currency: str, product: Product) -> OrderItem | None:
    """Цена товара в последнем отгруженном вагонном заказе клиента в той же валюте."""
    return (
        OrderItem.objects.select_related("order")
        .filter(
            order__client=client,
            order__currency=currency,
            order__transport_type=RAIL_TRANSPORT,
            order__status="shipped",
            order__deleted_at__isnull=True,
            order__purged_at__isnull=True,
            product=product,
            unit_price__gt=0,
        )
        # Легаси-отгрузка без записи Shipment (NULL) не должна обгонять настоящие.
        .order_by(F("order__shipment__shipped_at").desc(nulls_last=True), "-order_id")
        .first()
    )


def _price_items(
    items: list[ResolvedItem], client: Client, currency: str, tolerance_pct: Decimal, issues: list,
) -> list[ResolvedItem]:
    prices = dict(
        ClientPrice.objects.filter(
            client=client, currency=currency, product__in=[item.product for item in items],
        ).values_list("product_id", "price")
    )
    priced = []
    for item in items:
        price = prices.get(item.product.pk)
        if price is None or price <= 0:
            issues.append(ReportIssue(
                "price_missing",
                f"Нет цены «{item.product}» для клиента в {currency} — укажите цену в карточке клиента",
                subject=item.code))
            priced.append(item)
            continue
        reference = _last_wagon_price(client, currency, item.product)
        item = replace(item, unit_price=price)
        if reference is not None:
            item = replace(item, reference_price=reference.unit_price, reference_order_id=reference.order_id)
            deviation = abs(price - reference.unit_price) * _HUNDRED / reference.unit_price
            if deviation > tolerance_pct:
                issues.append(ReportIssue(
                    PRICE_MISMATCH,
                    f"«{item.product}»: цена {money_string(price)} {currency}, а в прошлом вагонном "
                    f"заказе №{reference.order_id} — {money_string(reference.unit_price)} {currency} "
                    f"(разница {deviation.quantize(Decimal('1'))}%) — проверьте цену",
                    subject=item.code, order_id=reference.order_id))
        priced.append(item)
    return priced


def _stock_warnings(items: list[ResolvedItem]) -> list[ReportIssue]:
    """Остатка не хватает — вагон всё равно уехал: склад уйдёт в минус (не блокирует)."""
    if not items:
        return []
    balances = stock_balances(resolve_warehouse(), [item.product.pk for item in items])
    return [
        ReportIssue(
            "stock_short",
            f"«{item.product}»: на складе {balances[item.product.pk]} меш., "
            f"отгружается {item.bags} — остаток уйдёт в минус",
            subject=item.code)
        for item in items
        if balances[item.product.pk] < item.bags
    ]


def configured_duplicate_window_days() -> int:
    """±дней от даты отчёта из настроек бота — у бота, у грузчика и в журнале одинаково.

    Вагоны ходят по кругу, поэтому окно, а не «когда-либо». Строку настроек не
    создаёт: на сервере её может ещё не быть — тогда ``DEFAULT_DUPLICATE_WINDOW_DAYS`` (3).
    """
    configured = (
        WhatsAppBotSettings.objects.filter(singleton=True)
        .values_list("duplicate_window_days", flat=True)
        .first()
    )
    return configured or DEFAULT_DUPLICATE_WINDOW_DAYS


def _shipped_wagon_duplicates(report: RailReport, day: date, window_days: int | None) -> list[ReportIssue]:
    """Вагоны отчёта, уже отгруженные в живом заказе с датой в пределах ±``window_days`` от ``day``.

    Дата отгрузки вагона — дата его отчёта (:func:`apps.shipments.services.ship_rail_report`
    датирует отгрузку днём отчёта). Повторно присланный отчёт — той же датой;
    тот же вагон через неделю — новый рейс, а не дубль. ``None`` — из настроек бота.
    """
    numbers = [wagon.number for wagon in report.wagons]
    if not numbers:
        return []
    window = timedelta(days=configured_duplicate_window_days() if window_days is None else window_days)
    shipped = ShipmentWagon.objects.select_related("shipment").filter(
        number__in=numbers,
        shipment__order__deleted_at__isnull=True,
        shipment__order__purged_at__isnull=True,
        shipment__shipped_at__date__range=(day - window, day + window),
    )
    lines = {wagon.number: wagon.line for wagon in report.wagons}
    return sorted(
        (
            ReportIssue(
                "wagon_already_shipped",
                f"Вагон {wagon.number} уже отгружен в заказе №{wagon.shipment.order_id} "
                f"({timezone.localtime(wagon.shipment.shipped_at):%d.%m})",
                lines[wagon.number], wagon.number, wagon.shipment.order_id)
            for wagon in shipped
        ),
        key=lambda issue: (issue.line or 0, issue.order_id or 0),
    )


def _manual_order_duplicates(client: Client, day: date, total_bags: int) -> list[ReportIssue]:
    """Вагонный заказ того же клиента на те же мешки, внесённый вручную (без вагонов)."""
    low, high = day - timedelta(days=MANUAL_DUPLICATE_DAYS), day + timedelta(days=MANUAL_DUPLICATE_DAYS)
    candidates = (
        Order.objects.filter(client=client, transport_type=RAIL_TRANSPORT)
        .exclude(status__in=CLOSED_STATUSES)
        # Заказ, проведённый по отчёту, опознаётся по вагонам: одинаковые
        # партии уходят каждый день, и вчерашний отчёт — не дубль сегодняшнего.
        .exclude(shipment__wagons__isnull=False)
        .filter(
            Q(arrival_date__range=(low, high))
            | Q(created_at__date__range=(low, high))
            | Q(shipment__shipped_at__date__range=(low, high))
        )
        .annotate(report_bags=Sum("items__quantity"))
        .filter(report_bags=total_bags)
        .order_by("-created_at", "-pk")
    )
    return [
        ReportIssue(
            MANUAL_ORDER_DUPLICATE,
            f"Похоже, этот отчёт уже внесён вручную: заказ №{order.pk} на {total_bags} меш. — "
            "проверьте, чтобы не списать склад дважды",
            order_id=order.pk)
        for order in candidates
    ]


def _day_issues(report: RailReport) -> list[ReportIssue]:
    """Причины разбора плюс отчёт из будущего."""
    issues = list(report.issues)
    if report.day is not None and report.day > timezone.localdate():
        issues.append(ReportIssue("future_day", f"Дата отчёта {report.day:%d.%m.%Y} ещё не наступила"))
    return issues


def _resolve_goods(
    report: RailReport, issues: list,
) -> tuple[dict[str, Product], list[RailWagon], list[ResolvedItem]]:
    """Товары по кодам, вагоны отчёта с мешками и позиции по товарам (без цен)."""
    products = _resolve_products(report, issues)
    wagons = _resolve_wagons(report, products, issues)
    return products, wagons, _group_items(report, wagons)


def resolve_report(
    report: RailReport,
    *,
    user=None,
    duplicate_window_days: int | None = None,
    price_tolerance_pct: Decimal | int = DEFAULT_PRICE_TOLERANCE_PCT,
) -> ResolvedReport:
    """Сопоставить разобранный отчёт с базой. Ничего не пишет.

    Проверяет всё сразу (а не до первой ошибки): человеку на разборе нужен
    полный список причин. ``issues`` пустой — отчёт можно проводить.

    ``user`` — чей это предпросмотр: клиент чужого отдела — причина разбора,
    и его цены и заказы сотруднику не показываются.
    """
    issues = _day_issues(report)
    client, currency, profile = (
        _resolve_client(report.client_name, issues) if report.client_name else (None, "", None)
    )
    foreign = client is not None and not _client_in_scope(user, client)
    if foreign:
        issues.append(ReportIssue(
            CLIENT_OTHER_DEPARTMENT,
            f"Клиент «{report.client_name}» закреплён за другим отделом — отчёт проводит его отдел",
            subject=report.client_name))
    department = _client_department(client, issues) if client is not None else ""
    products, wagons, items = _resolve_goods(report, issues)
    priced = client is not None and not foreign
    if priced:
        items = _price_items(items, client, currency, Decimal(price_tolerance_pct), issues)
    day = report.day or timezone.localdate()
    issues.extend(_shipped_wagon_duplicates(report, day, duplicate_window_days))
    if priced and items:
        issues.extend(_manual_order_duplicates(client, day, sum(item.bags for item in items)))
    return ResolvedReport(
        report=report,
        client=client,
        currency=currency,
        department=department,
        wagons=tuple(wagons),
        items=tuple(items),
        issues=tuple(issues),
        warnings=tuple(_stock_warnings(items)),
        profile=profile,
        products=products,
    )


def _require_resolved(resolved: ResolvedReport) -> tuple[Client, date]:
    """Клиент и день проводимого отчёта; есть причины — «на разбор»."""
    client, day = resolved.client, resolved.report.day
    if resolved.ok and client is not None and day is not None:
        return client, day
    raise ValidationError({
        "detail": "Отчёт нужно проверить: " + "; ".join(issue.message for issue in resolved.issues),
        "code": "rail_report_needs_review",
        "issues": [issue.code for issue in resolved.issues],
    })


def _can_create(user) -> bool:
    return user is not None and all(user.has_perm_code(code) for code in ("orders.create", "orders.confirm"))


def _assert_can_create(user) -> None:
    if not _can_create(user):
        raise PermissionDenied("Нет права создавать и подтверждать заказы")


def assert_can_conduct(user) -> None:
    """Провести отчёт: создать и подтвердить заказ и отгрузить вагоны."""
    _assert_can_create(user)
    assert_can_ship_transport(user, RAIL_TRANSPORT)


def _passes(check, *args) -> bool:
    try:
        check(*args)
    except PermissionDenied:
        return False
    return True


def can_conduct(user) -> bool:
    """«Провести» новый отчёт: loader.confirm + loader.wagons + orders.create + orders.confirm."""
    return _passes(assert_can_conduct, user)


def can_ship_by_report(user) -> bool:
    """«Отгрузить по отчёту» заранее внесённый заказ — как любую отгрузку вагонов."""
    return user is not None and _passes(assert_can_ship_transport, user, RAIL_TRANSPORT)


def can_remember_products(user) -> bool:
    """Код товара из отчёта запоминает тот, кто правит каталог или создаёт заказы.

    Словарь читает бот, который проводит отчёты сам: неверный код списал бы
    со склада не тот товар, — поэтому грузчику без этих прав он закрыт.
    """
    return user is not None and (user.has_perm_code("catalog.edit") or _can_create(user))


def can_remember_clients(user) -> bool:
    """Клиента и валюту под названием из отчёта выбирает тот, кто создаёт заказы."""
    return _can_create(user)


def _client_in_scope(user, client: Client) -> bool:
    """Сотрудник отдела проводит отчёты только своих клиентов — как и отгружает их заказы."""
    department_id = assigned_department_id(user) if user is not None else None
    return department_id is None or client.department_id == department_id


def _assert_client_in_scope(user, client: Client, message: str = "Клиент отчёта закреплён за другим отделом") -> None:
    if not _client_in_scope(user, client):
        raise PermissionDenied(message)


@transaction.atomic
def create_rail_order(resolved: ResolvedReport, user) -> Order:
    """Подтверждённый вагонный заказ по отчёту — по образцу ``repeat_order``.

    Остаток не проверяется: вагон уже уехал, нехватку показывает
    ``resolved.warnings``, а списание в минус пишет журнал склада. Цены —
    из прайса клиента в валюте отчёта; сам прайс заказ по отчёту не меняет.

    Заказ по отчёту за прошедший день создан тем днём — как новый заказ,
    зафиксированный задним числом (:mod:`apps.orders.fixation`): списки и
    сводки по дате создания видят его в день отгрузки. Подтверждение и запись
    «Заказ по отчёту о вагонах» остаются с настоящим временем — это аудит.
    """
    _assert_can_create(user)
    client, day = _require_resolved(resolved)
    report = resolved.report
    client = Client.objects.select_for_update().get(pk=client.pk)
    # Под блокировкой и по свежему клиенту: ``confirm_order`` отдел не
    # проверяет (заявки — общая очередь), а этот сервис зовут не только из
    # :func:`conduct_rail_report`.
    _assert_client_in_scope(user, client)
    order = Order.objects.create(
        client=client,
        currency=resolved.currency,
        department=resolved.department,
        transport_type=RAIL_TRANSPORT,
        warehouse=resolve_warehouse(),
        rail_station=report.station[:120],
        arrival_date=day,
        status="pending",
        created_by=user,
    )
    if day < timezone.localdate():
        order.created_at = backdate_moment(day)
        Order.objects.filter(pk=order.pk).update(created_at=order.created_at)
    for item in resolved.items:
        OrderItem.objects.create(order=order, product=item.product, quantity=item.bags, unit_price=item.unit_price)
    # Цены уже в позициях: без ``prices`` подтверждение не переписывает прайс клиента.
    confirm_order(order, user)
    log_event(
        "rail_report",
        (
            f"Заказ по отчёту о вагонах: {client.display_name}, "
            f"{rail_phrase(len(resolved.wagons), report.station)}, "
            f"{format_tons(report.total_tons)} т, {resolved.total_bags} меш."
        )[:MESSAGE_MAX_LENGTH],
        user=user,
        order=order,
        payload={
            "day": day.isoformat(),
            "country": report.country,
            "client_name": report.client_name,
            "station": report.station,
            "wagons": [wagon.number for wagon in resolved.wagons],
            "bags": resolved.total_bags,
            "tons": str(report.total_tons),
            "profile_id": resolved.profile.pk if resolved.profile else None,
        },
    )
    return order


@transaction.atomic
def conduct_rail_report(
    report: RailReport,
    user,
    *,
    duplicate_window_days: int | None = None,
    price_tolerance_pct: Decimal | int = DEFAULT_PRICE_TOLERANCE_PCT,
) -> Order:
    """Провести отчёт: заказ, подтверждение, вагоны, склад и долг — одной транзакцией.

    Проверки повторяются под блокировкой клиента: бот и человек, проводящие
    один отчёт одновременно, не отгрузят его дважды — второй увидит вагоны
    первого и получит «на разбор».
    """
    assert_can_conduct(user)

    def resolve() -> ResolvedReport:
        return resolve_report(
            report, duplicate_window_days=duplicate_window_days, price_tolerance_pct=price_tolerance_pct)

    client, _ = _require_resolved(resolve())
    # Отказ — до записи; под блокировкой клиента заказ проверяет это ещё раз.
    _assert_client_in_scope(user, client)
    Client.objects.select_for_update().filter(pk=client.pk).first()
    resolved = resolve()
    _, day = _require_resolved(resolved)
    order = create_rail_order(resolved, user)
    ship_rail_report(order, resolved.wagons, user, station=report.station, shipped_day=day)
    order.refresh_from_db()
    return order


def resolve_order_report(
    report: RailReport,
    order: Order,
    *,
    duplicate_window_days: int | None = None,
) -> ResolvedReport:
    """Отчёт для заранее внесённого вагонного заказа («Отгрузить по отчёту»). Ничего не пишет.

    Клиент, валюта, отдел и цены — из заказа: название клиента в отчёте
    может быть незнакомо, но не может оказаться другим клиентом. Мешки
    отчёта по каждому товару должны совпасть с заказом (как в
    :func:`apps.shipments.services.ship_rail_report`). Похожий ручной заказ
    здесь не ищется — это он и есть.
    """
    issues = _day_issues(report)
    client = order.client
    if report.client_name:
        named, _, _ = _resolve_client(report.client_name, [])
        if named is not None and named.pk != client.pk:
            issues.append(ReportIssue(
                "client_mismatch",
                f"В отчёте клиент «{report.client_name}», а заказ №{order.pk} — «{client.display_name}»",
                subject=report.client_name, order_id=order.pk))
    if order.transport_type != RAIL_TRANSPORT or order.status not in AWAITING_SHIPMENT_STATUSES:
        issues.append(ReportIssue(
            "order_not_waiting", f"Заказ №{order.pk} не ждёт отгрузки вагонами", order_id=order.pk))
    products, wagons, items = _resolve_goods(report, issues)
    prices = {item.product_id: item.unit_price for item in order.items.all()}
    items = [replace(item, unit_price=prices.get(item.product.pk)) for item in items]
    # Мешки сверяются, когда распознаны все вагоны: иначе расхождение ложное.
    if wagons and len(wagons) == len(report.wagons):
        mismatch = rail_bags_mismatch(order, wagons)
        if mismatch:
            issues.append(ReportIssue(
                "rail_bags_mismatch", f"Мешки не совпадают — {mismatch}. Поправьте заказ.", order_id=order.pk))
    issues.extend(_shipped_wagon_duplicates(report, report.day or timezone.localdate(), duplicate_window_days))
    return ResolvedReport(
        report=report,
        client=client,
        currency=order.currency,
        department=order.department,
        wagons=tuple(wagons),
        items=tuple(items),
        issues=tuple(issues),
        warnings=tuple(_stock_warnings(items)),
        products=products,
    )


@transaction.atomic
def ship_order_by_report(
    report: RailReport,
    order: Order,
    user,
    *,
    duplicate_window_days: int | None = None,
) -> Order:
    """Отгрузить заранее внесённый вагонный заказ по отчёту — под блокировкой заказа.

    Заказ, склад и долг не создаются заново: вагоны, станция и день отчёта
    ложатся на существующий заказ (:func:`apps.shipments.services.ship_rail_report`).
    """
    assert_can_ship_transport(user, RAIL_TRANSPORT)
    # Заказ, затем клиент — как везде (lock_live_order → область отдела,
    # подтверждение, смена отдела): обратный порядок ловил взаимоблокировку.
    # Проверки — под блокировкой клиента, как в conduct_rail_report: бот,
    # проводящий тот же отчёт новым заказом, увидит вагоны этой отгрузки.
    order = lock_live_order(order, user)
    Client.objects.select_for_update().filter(pk=order.client_id).first()
    resolved = resolve_order_report(report, order, duplicate_window_days=duplicate_window_days)
    _, day = _require_resolved(resolved)
    ship_rail_report(order, resolved.wagons, user, station=report.station, shipped_day=day)
    order.refresh_from_db()
    return order


def apply_rail_report(
    report: RailReport,
    user,
    *,
    order: Order | None = None,
    duplicate_window_days: int | None = None,
    price_tolerance_pct: Decimal | int = DEFAULT_PRICE_TOLERANCE_PCT,
) -> Order:
    """«Провести» у грузчика и в журнале бота: новый отчёт — заказ, подтверждение
    и отгрузка; с ``order`` — отгрузка заранее внесённого заказа."""
    if order is None:
        return conduct_rail_report(
            report, user, duplicate_window_days=duplicate_window_days, price_tolerance_pct=price_tolerance_pct)
    return ship_order_by_report(report, order, user, duplicate_window_days=duplicate_window_days)


def remember_report_product(code: str, product: Product, user) -> ProductAlias:
    """Код товара из отчёта → товар (для экрана разбора; права — :func:`can_remember_products`)."""
    if not can_remember_products(user):
        raise PermissionDenied("Код товара запоминает тот, кто правит товары или создаёт заказы")
    return remember_product_alias(code, product, user)


def remember_report_client(name: str, client: Client, currency: str, user) -> BotClientProfile:
    """Название клиента из отчёта → клиент и валюта (права — :func:`can_remember_clients`)."""
    if not can_remember_clients(user):
        raise PermissionDenied("Клиента отчёта выбирает тот, кто создаёт заказы")
    return remember_client_profile(name, client, currency, user)


def rail_report_texts(orders) -> dict[int, str]:
    """«Скопировать отчёт»: отгрузка вагонов снова в формате владельца.

    Для страницы истории — по запросу на коды товаров и на названия клиентов
    в отчётах на всю страницу (вагоны — из предзагрузки). Код товара — самый
    свежий из словаря в написании отчёта («Д1с»), без кода — название товара;
    клиент — как его называет отчёт в валюте заказа, иначе по карточке.
    Заказа без вагонов в словаре нет.
    """
    shipped = [
        (order, wagons)
        for order in orders
        if order.transport_type == RAIL_TRANSPORT
        and (wagons := order_wagons(order))
        and order.shipment.shipped_at is not None
    ]
    if not shipped:
        return {}
    codes = {
        product_id: spelling or code
        for product_id, spelling, code in ProductAlias.objects.filter(
            product_id__in={wagon.product_id for _, wagons in shipped for wagon in wagons if wagon.product_id},
        ).order_by("created_at", "pk").values_list("product_id", "spelling", "code")
    }
    names = {
        (client_id, currency): name
        for client_id, currency, name in BotClientProfile.objects.filter(
            client_id__in={order.client_id for order, _ in shipped},
        ).order_by("updated_at", "pk").values_list("client_id", "currency", "name")
    }
    return {
        order.pk: format_rail_report(
            day=timezone.localtime(order.shipment.shipped_at).date(),
            country=order.client.country,
            client_name=names.get((order.client_id, order.currency)) or order.client.display_name,
            station=order.rail_station,
            wagons=[
                (codes.get(wagon.product_id) or wagon.product_label, wagon.number, wagon.weight_kg / KG_PER_TON)
                for wagon in wagons
            ],
        )
        for order, wagons in shipped
    }
