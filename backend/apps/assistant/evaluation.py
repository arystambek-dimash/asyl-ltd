"""Проверка качества ИИ-ассистента: вопросы с эталоном, посчитанным самой системой.

Эталон считают модули-владельцы — правила долга ``orders/debt.py``, живые
заказы через ``Order.objects`` (корзина исключена), остатки склада, — а не
SQL: ассистент сверяется с тем, что показывают страницы системы. Ответ верен,
если в его тексте или таблице есть все эталонные числа (суммы с копейками —
с точностью до тенге, целые — точно) и названия.
"""

from __future__ import annotations

import re
import statistics
import time as clock
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db.models import Sum
from django.utils import timezone

from apps.catalog.models import Product
from apps.common.openai_responses import OpenAIResponseError
from apps.clients.models import Client
from apps.grain.models import Wagon
from apps.orders.debt import debt_by_currency, order_payment_status, order_remaining
from apps.orders.models import Order, OrderItem, Payment
from apps.orders.statuses import AWAITING_SHIPMENT_STATUSES
from apps.warehouse.models import StockItem

from . import agent

_NUMBER_RE = re.compile(r"(?<![\d.,])\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
_MONEY_TOLERANCE = Decimal("1")


@dataclass(frozen=True)
class Expected:
    """Что должно быть в ответе: каждое из ``numbers`` и хотя бы одно имя из каждой группы ``names``."""

    numbers: tuple[Decimal, ...] = ()
    names: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class Case:
    key: str
    question: str
    expected: Callable[[], Expected]


def _numbers(*values) -> tuple[Decimal, ...]:
    return tuple(Decimal(str(value)) for value in values)


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    first = date(year, month, 1)
    after = date(year + month // 12, month % 12 + 1, 1)
    zone = timezone.get_current_timezone()
    return datetime.combine(first, time.min, zone), datetime.combine(after, time.min, zone)


def _client_names(client: Client) -> tuple[str, ...]:
    return tuple(name for name in (client.company_name.strip(), client.name) if name)


def _orders_total() -> Expected:
    return Expected(numbers=_numbers(Order.objects.count()))


def _awaiting_shipment() -> Expected:
    return Expected(numbers=_numbers(Order.objects.filter(status__in=AWAITING_SHIPMENT_STATUSES).count()))


def _shipped_orders():
    return Order.objects.filter(status="shipped").prefetch_related("items", "payments")


def _debt_by_currency() -> Expected:
    totals = debt_by_currency(_shipped_orders())
    return Expected(numbers=tuple(amount for amount in totals.values() if amount > 0))


def _top_debtor() -> Expected:
    debts: dict[int, Decimal] = defaultdict(Decimal)
    for order in _shipped_orders().filter(currency="KZT"):
        if order.is_debt:
            debts[order.client_id] += order_remaining(order)
    client_id, amount = max(debts.items(), key=lambda item: item[1])
    client = Client.objects.select_related("user").get(pk=client_id)
    return Expected(numbers=(amount,), names=(_client_names(client),))


def _clients_count() -> Expected:
    return Expected(numbers=_numbers(Client.objects.count()))


def _stock_bags() -> Expected:
    return Expected(numbers=_numbers(StockItem.objects.aggregate(bags=Sum("bags"))["bags"] or 0))


def _shipped_bags_september() -> Expected:
    # Период — по дню продажи, как в «Отчётах»: отгрузка, а у старых заказов без неё — создание.
    start, end = _month_bounds(2026, 9)
    orders = Order.objects.annotate(sale_at=Order.SALE_AT).filter(
        status="shipped", sale_at__gte=start, sale_at__lt=end,
    )
    bags = OrderItem.objects.filter(order__in=orders.values("pk")).aggregate(bags=Sum("quantity"))["bags"]
    return Expected(numbers=_numbers(bags or 0))


def _confirmed_payments() -> Expected:
    live = Order.objects.values("pk")
    return Expected(numbers=_numbers(Payment.objects.filter(status="confirmed", order__in=live).count()))


def _top_product() -> Expected:
    top = (
        OrderItem.objects.filter(order__in=Order.objects.filter(status="shipped"), product__isnull=False)
        .values("product")
        .annotate(bags=Sum("quantity"))
        .order_by("-bags")
        .first()
    )
    product = Product.objects.get(pk=top["product"])
    return Expected(numbers=_numbers(top["bags"]), names=((product.name,),))


def _grain_net() -> Expected:
    net = Wagon.objects.aggregate(net=Sum("net_weight_kg"))["net"] or 0
    return Expected(numbers=_numbers(net))


def _top_clients_bags() -> Expected:
    top = (
        OrderItem.objects.filter(order__in=Order.objects.filter(status="shipped"))
        .values("order__client")
        .annotate(bags=Sum("quantity"))
        .order_by("-bags")[:3]
    )
    clients = Client.objects.select_related("user").in_bulk([row["order__client"] for row in top])
    return Expected(
        numbers=tuple(Decimal(row["bags"]) for row in top),
        names=tuple(_client_names(clients[row["order__client"]]) for row in top),
    )


def _settled_share() -> Expected:
    shipped = list(_shipped_orders())
    settled = sum(order_payment_status(order) == "settled" for order in shipped)
    return Expected(numbers=(round(Decimal(settled * 100) / len(shipped), 1),))


CASES = [
    Case("orders_total", "Сколько всего заказов в системе?", _orders_total),
    Case("awaiting_shipment", "Сколько заказов сейчас ждут отгрузки?", _awaiting_shipment),
    Case("debt_by_currency", "Какой общий долг клиентов? Раздели по валютам.", _debt_by_currency),
    Case("top_debtor", "Кто клиент с самым большим долгом в тенге и сколько он должен?", _top_debtor),
    Case("clients_count", "Сколько клиентов в системе?", _clients_count),
    Case("stock_bags", "Сколько мешков сейчас лежит на всех складах вместе?", _stock_bags),
    Case("shipped_bags_september", "Сколько мешков отгрузили в сентябре 2026 года?", _shipped_bags_september),
    Case("confirmed_payments", "Сколько оплат подтверждено (в статусе «Оплачено»)?", _confirmed_payments),
    Case("top_product", "Какой товар отгрузили больше всего по мешкам за всё время и сколько мешков?", _top_product),
    Case("grain_net", "Сколько килограммов зерна нетто по всем рейсам вагонов и машин вместе?", _grain_net),
    Case(
        "top_clients_bags",
        "Назови трёх клиентов, которым отгрузили больше всего мешков за всё время, и сколько каждому.",
        _top_clients_bags,
    ),
    Case("settled_share", "Какая доля отгруженных заказов оплачена полностью, в процентах?", _settled_share),
]


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(re.sub(r"[   ]", "", text).replace(",", "."))
    except InvalidOperation:
        return None


def found_numbers(text: str, table: dict[str, Any] | None) -> list[Decimal]:
    """Числа из текста ответа («1 250 000,50») и из ячеек его таблицы."""
    values = [number for match in _NUMBER_RE.findall(text) if (number := _decimal(match)) is not None]
    for row in (table or {}).get("rows", []):
        for cell in row:
            if isinstance(cell, (int, float)) and not isinstance(cell, bool):
                values.append(Decimal(str(cell)))
            elif isinstance(cell, str) and (number := _decimal(cell)) is not None:
                values.append(number)
    return values


def _tolerance(value: Decimal) -> Decimal:
    # Сумму с копейками ассистент может округлить до тенге; целые (штуки, мешки) — только точно.
    return Decimal("0") if value == value.to_integral_value() else _MONEY_TOLERANCE


def missing(expected: Expected, text: str, table: dict[str, Any] | None) -> list[str]:
    """Чего из эталона нет в ответе; пусто — ответ верный."""
    numbers = found_numbers(text, table)
    haystack = " ".join([text, *(str(cell) for row in (table or {}).get("rows", []) for cell in row)]).lower()
    lost = [
        f"число {value}" for value in expected.numbers
        if not any(abs(found - value) <= _tolerance(value) for found in numbers)
    ]
    lost += [
        f"имя «{' / '.join(group)}»" for group in expected.names
        if not any(name.lower() in haystack for name in group)
    ]
    return lost


def run_case(case: Case, user, *, model: str | None = None, effort: str | None = None) -> dict[str, Any]:
    """Задать вопрос ассистенту и сверить с эталоном: верность, время, шаги, токены."""
    expected = case.expected()
    trace: list[dict[str, Any]] = []
    started = clock.monotonic()
    generator = agent.run(user, case.question, [], trace, model=model, effort=effort)
    result: dict[str, Any] = {"case": case.key, "question": case.question}
    try:
        while True:
            next(generator)
    except StopIteration as stop:
        answer = stop.value
        lost = missing(expected, answer.text, answer.table)
        result.update(ok=not lost, missing=lost, answer=answer.text, stats=answer.stats)
    except (OpenAIResponseError, agent.AssistantError) as exc:
        result.update(ok=False, missing=[f"сбой {exc.code}"], answer="", stats={})
    result["seconds"] = round(clock.monotonic() - started, 1)
    result["sql"] = [step["sql"] for step in trace if step.get("kind") == "sql"]
    return result


def summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Итог прогона: доля верных, время ответа (медиана, худший), шаги и кэш токенов."""
    seconds = [result["seconds"] for result in results]
    stats = [result["stats"] for result in results if result["stats"]]
    input_tokens = sum(item.get("input_tokens", 0) for item in stats)
    return {
        "correct": sum(result["ok"] for result in results),
        "total": len(results),
        "median_seconds": round(statistics.median(seconds), 1) if seconds else 0,
        "max_seconds": max(seconds, default=0),
        "avg_model_calls": round(statistics.mean(item["model_calls"] for item in stats), 1) if stats else 0,
        "avg_sql_queries": round(statistics.mean(item["sql_queries"] for item in stats), 1) if stats else 0,
        "avg_input_tokens": round(input_tokens / len(stats)) if stats else 0,
        "cached_share": round(sum(item.get("cached_tokens", 0) for item in stats) / input_tokens, 2) if input_tokens else 0,
    }
