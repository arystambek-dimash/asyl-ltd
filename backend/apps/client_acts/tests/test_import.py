"""Импорт акта сверки: реализации — заказы задним числом, поступления — оплаты долга."""

from datetime import date
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.catalog.models import Product
from apps.client_acts.models import ClientActDocument
from apps.client_acts.parser import parse_act
from apps.client_acts.services import import_act_chunk, preview_act
from apps.clients.models import Client
from apps.clients.services import client_history
from apps.orders.backdate import backdate_moment
from apps.orders.debt import order_overpaid
from apps.orders.models import Order, OrderItem, Payment
from apps.shipments.models import Shipment
from apps.warehouse.models import StockMovement

from .act_factory import act_xlsx, bank, cash, sale

pytestmark = pytest.mark.django_db

IMPORT_CODES = ["orders.view", "orders.edit", "orders.confirm", "payments.view", "payments.create"]


@pytest.fixture
def importer(user_with_perms, departments):
    return user_with_perms("act-importer", codes=IMPORT_CODES, department=departments[0])


@pytest.fixture
def client(departments):
    return Client.objects.create_with_user(
        first_name="Дан Агро Групп", phone="+996 555 000 000", department=departments[0], currency="USD",
    )


def _act(rows, **options):
    options.setdefault("counterparty", 'ОсОО "Дан Агро Групп"')
    return parse_act(act_xlsx(rows, **options))


def _run(client, act, user, *, start=None, budget=2):
    """Импорт до конца маленькими порциями — как окно, которое шлёт файл, пока не «готово»."""
    steps = []
    while True:
        result = import_act_chunk(client, act, user, start=start, budget=budget)
        steps.append(result)
        if result["finished"]:
            return steps
        assert len(steps) < 100


def _orders(client):
    return list(Order.objects.filter(client=client).order_by("created_at", "pk"))


def _day(moment):
    return timezone.localtime(moment).date()


def _balance(client, currency="USD"):
    totals = client_history(client)["summary"]["by_currency"][currency]
    return Decimal(totals["debt"]) - Decimal(totals["overpaid"])


def test_sales_become_shipped_orders_on_their_day_without_touching_stock(client, importer):
    act = _act([sale("10.06.2026", 6625, "527"), sale("11.06.2026", "8125.50", "528")])

    steps = _run(client, act, importer)

    first, second = _orders(client)
    assert [(o.status, o.currency, o.total_amount, o.department) for o in (first, second)] == [
        ("shipped", "USD", Decimal("6625.00"), "mill"),
        ("shipped", "USD", Decimal("8125.50"), "mill"),
    ]
    assert _day(first.created_at) == _day(first.shipment.shipped_at) == date(2026, 6, 10)
    assert _day(second.shipment.shipped_at) == date(2026, 6, 11)
    assert first.shipment.stock_deducted is False
    assert not StockMovement.objects.exists()
    item = first.items.get()
    assert (item.quantity, item.unit_price, item.product.is_active) == (1, Decimal("6625.00"), False)
    assert first.notes == "Акт сверки № 00000000509: Реализация ТМЗ и услуг 527 от 10.06.2026"
    assert steps[-1]["finished"] and sum(step["processed"] for step in steps) == 2
    assert set(ClientActDocument.objects.values_list("document", "order_id")) == {
        ("Реализация ТМЗ и услуг 527 от 10.06.2026", first.pk),
        ("Реализация ТМЗ и услуг 528 от 11.06.2026", second.pk),
    }
    assert _balance(client) == act.closing == Decimal("14750.50")


def test_payment_pays_debt_on_its_day_and_prepayment_waits_for_next_sales(client, importer):
    act = _act([
        sale("10.06.2026", 100, "1"),
        bank("12.06.2026", 150, "394"),
        sale("15.06.2026", 80, "2"),
    ])

    _run(client, act, importer)

    first, second = _orders(client)
    payments = list(Payment.objects.order_by("pk"))
    assert [(p.order_id, p.amount, p.method, p.status, _day(p.paid_at), _day(p.confirmed_at)) for p in payments] == [
        (first.pk, Decimal("100.00"), "bank", "confirmed", date(2026, 6, 12), date(2026, 6, 12)),
        # Предоплата гасит следующую отгрузку, но остаётся деньгами своего дня в кассе.
        (second.pk, Decimal("50.00"), "bank", "confirmed", date(2026, 6, 12), date(2026, 6, 12)),
    ]
    assert payments[0].note.startswith("Акт сверки № 00000000509: Платежное поручение (входящее) 394 от 12.06.2026")
    assert payments[1].note == "Акт сверки № 00000000509: Платежное поручение (входящее) 394 от 12.06.2026"
    document = ClientActDocument.objects.get(kind="payment")
    assert document.unapplied == 0
    assert set(document.payments.values_list("pk", flat=True)) == {p.pk for p in payments}
    assert _balance(client) == act.closing == Decimal("30.00")


def test_final_overpayment_lies_on_the_last_order_as_owed_to_client(client, importer):
    act = _act([
        sale("10.06.2026", 100, "1"),
        sale("11.06.2026", 200, "2"),
        bank("20.06.2026", 375, "394"),
    ])

    _run(client, act, importer)

    first, last = _orders(client)
    assert order_overpaid(first) == 0
    assert order_overpaid(last) == Decimal("75.00")
    assert last.payment_status == "settled"
    assert ClientActDocument.objects.get(kind="payment").unapplied == 0
    summary = client_history(client)["summary"]["by_currency"]["USD"]
    assert (summary["debt"], summary["overpaid"]) == ("0.00", "75.00")
    assert act.closing == Decimal("-75.00")


def test_same_act_again_and_a_wider_act_import_only_new_documents(client, importer):
    rows = [sale("10.06.2026", 100, "1"), bank("12.06.2026", 60, "394")]
    _run(client, _act(rows), importer)

    again = preview_act(client, _act(rows), importer)
    assert (again["sales"]["count"], again["payments"]["count"], again["imported"]) == (0, 0, 2)
    _run(client, _act(rows), importer)
    assert Order.objects.count() == 1 and Payment.objects.count() == 1

    wider = _act([*rows, sale("20.06.2026", 50, "2")], period=("01.01.2026", "30.06.2026"))
    _run(client, wider, importer)

    assert Order.objects.count() == 2
    assert _balance(client) == wider.closing == Decimal("90.00")


def test_chunks_continue_where_the_previous_one_stopped(client, importer):
    act = _act([sale("10.06.2026", 10, str(n)) for n in range(1, 6)])

    first = import_act_chunk(client, act, importer, budget=2)
    second = import_act_chunk(client, act, importer, budget=2)
    third = import_act_chunk(client, act, importer, budget=2)

    assert [(r["processed"], r["remaining"], r["finished"]) for r in (first, second, third)] == [
        (2, 3, False), (2, 1, False), (1, 0, True),
    ]
    assert Order.objects.count() == 5


def test_one_payment_for_many_orders_is_spread_over_several_requests(client, importer):
    act = _act([*(sale("10.06.2026", 10, str(n)) for n in range(1, 6)), bank("20.06.2026", 45, "394")])

    steps = _run(client, act, importer, budget=2)

    # 5 заказов + 5 долей оплаты по 2 записи за запрос; последняя порция — только деньги.
    assert [(s["processed"], s["payments"], s["remaining"], s["finished"]) for s in steps] == [
        (2, 0, 4, False), (2, 0, 2, False), (2, 1, 0, False), (0, 2, 0, False), (0, 2, 0, True),
    ]
    assert sorted(Payment.objects.values_list("amount", flat=True)) == [Decimal("5.00"), *[Decimal("10.00")] * 4]
    assert ClientActDocument.objects.get(kind="payment").unapplied == 0
    assert _balance(client) == act.closing == Decimal("5.00")


def test_preview_counts_and_writes_nothing(client, importer):
    act = _act([sale("10.06.2026", 100, "1"), sale("11.06.2026", 50, "2"), cash("12.06.2026", 30, "7")])

    preview = preview_act(client, act, importer)

    assert preview["sales"] == {"count": 2, "total": "150.00"}
    assert preview["payments"] == {"count": 1, "total": "30.00"}
    assert preview["act"]["currency"] == "USD"
    assert preview["act"]["closing"] == "120.00"
    assert preview["balance_after"] == "120.00"
    assert preview["start"] == preview["default_start"] == "2026-01-01"
    assert preview["name_matches"] is True
    assert preview["problems"] == []
    assert not Order.objects.exists() and not ClientActDocument.objects.exists()


def _crm_order(client, amount, shipped_day, *, currency="USD"):
    order = Order.objects.create(client=client, status="shipped", currency=currency, department="mill")
    product, _ = Product.objects.get_or_create(name="Мука", color="Red", weight_kg=Decimal("50"))
    OrderItem.objects.create(order=order, product=product, quantity=1, unit_price=Decimal(amount))
    moment = backdate_moment(shipped_day)
    Shipment.objects.create(order=order, shipped_at=moment)
    Order.objects.filter(pk=order.pk).update(created_at=moment)
    return order


def test_crm_orders_inside_the_period_move_the_default_start(client, importer):
    _crm_order(client, 100, date(2026, 6, 15))
    _crm_order(client, 999, date(2026, 6, 15), currency="KZT")  # другая валюта не пересекается
    act = _act([sale("10.06.2026", 100, "1"), sale("15.06.2026", 100, "2"), sale("20.06.2026", 70, "3")])

    preview = preview_act(client, act, importer)

    assert preview["crm"] == {
        "orders": 1, "orders_total": "100.00", "payments": 0, "payments_total": "0.00", "last_day": "2026-06-15",
    }
    assert preview["default_start"] == preview["start"] == "2026-06-16"
    assert (preview["sales"]["count"], preview["before_start"]) == (1, 2)

    _run(client, act, importer, start=date(2026, 6, 16))

    assert Order.objects.filter(client=client, currency="USD").count() == 2
    chosen = preview_act(client, act, importer, start=date(2026, 1, 1))
    assert (chosen["start"], chosen["sales"]["count"]) == ("2026-01-01", 2)


def test_opening_debt_of_a_fresh_client_becomes_an_order_on_the_first_day(client, importer):
    act = _act([sale("10.06.2026", 100, "1")], opening=(500, None))

    preview = preview_act(client, act, importer)
    assert preview["opening_order"] == "500.00"
    _run(client, act, importer)

    opening = Order.objects.get(notes__contains="Сальдо на начало")
    assert (opening.total_amount, _day(opening.shipment.shipped_at)) == (Decimal("500.00"), date(2026, 1, 1))
    assert opening.notes == "Акт сверки № 00000000509: Сальдо на начало 01.01.2026"
    assert _balance(client) == act.closing == Decimal("600.00")


def test_opening_balance_that_crm_does_not_have_blocks_the_import(client, importer):
    _crm_order(client, 120, date(2025, 12, 1))
    act = _act([sale("10.06.2026", 100, "1")], opening=(500, None))

    preview = preview_act(client, act, importer)

    assert len(preview["problems"]) == 1
    assert "Сальдо на начало" in preview["problems"][0]
    with pytest.raises(ValidationError) as caught:
        import_act_chunk(client, act, importer)
    assert caught.value.detail["code"] == "act_import_blocked"
    assert Order.objects.count() == 1


def test_opening_balance_already_in_crm_is_left_alone(client, importer):
    _crm_order(client, 500, date(2025, 12, 1))
    act = _act([sale("10.06.2026", 100, "1")], opening=(500, None))

    assert preview_act(client, act, importer)["opening_order"] is None
    _run(client, act, importer)

    assert _balance(client) == act.closing == Decimal("600.00")


def test_overpayment_with_no_order_to_hold_it_is_refused(client, importer):
    act = _act([bank("12.06.2026", 60, "394")])

    problems = preview_act(client, act, importer)["problems"]

    assert len(problems) == 1 and "переплат" in problems[0].lower()


def test_tenge_act_and_cash_receipt(client, importer):
    act = _act([sale("10.06.2026", 300000, "1"), cash("11.06.2026", 100000, "5")], currency="KZT")

    _run(client, act, importer)

    order = Order.objects.get()
    assert order.currency == "KZT"
    assert list(Payment.objects.values_list("method", "amount")) == [("cash", Decimal("100000.00"))]


def test_needs_order_and_payment_rights(client, user_with_perms, departments):
    clerk = user_with_perms("clerk", codes=["orders.edit", "payments.create"], department=departments[0])
    act = _act([sale("10.06.2026", 100, "1")])

    with pytest.raises(PermissionDenied):
        import_act_chunk(client, act, clerk)
    assert not Order.objects.exists()


def test_other_department_client_is_out_of_reach(client, user_with_perms, departments):
    stranger = user_with_perms("stranger", codes=IMPORT_CODES, department=departments[1])

    with pytest.raises(PermissionDenied):
        import_act_chunk(client, _act([sale("10.06.2026", 100, "1")]), stranger)
