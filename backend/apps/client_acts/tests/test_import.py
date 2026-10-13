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
from apps.clients.models import Client, Store
from apps.clients.services import client_history
from apps.eventlog.models import EventLog
from apps.orders.backdate import backdate_moment
from apps.orders.debt import order_overpaid
from apps.orders.models import Order, OrderItem, Payment, PaymentRefund
from apps.orders.refunds import sync_refund_totals
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


def _crm_order(client, amount, shipped_day, *, currency="USD", status="shipped", store=None):
    order = Order.objects.create(client=client, status=status, currency=currency, department="mill", store=store)
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
        "orders": 1, "orders_total": "100.00", "payments": 0, "payments_total": "0.00",
        "refunds": 0, "refunds_total": "0.00", "last_day": "2026-06-15",
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


RETURN = "Возврат ТМЗ от покупателя {number} от {day}"


def test_goods_returns_and_counter_supplies_close_debt_as_act_offset(client, importer):
    act = _act(
        [
            sale("23.04.2026", 633400, "360"),
            ("31.05.2026", "Поступление ТМЗ и услуг 00000000342 от 31.05.2026", None, 20000),
            bank("11.06.2026", 300000, "332"),
            ("07.08.2026", RETURN.format(number="4", day="07.08.2026"), None, 26890),
        ],
        currency="KZT",
    )

    preview = preview_act(client, act, importer)
    assert preview["payments"] == {"count": 3, "total": "346890.00"}
    assert preview["offsets"] == {"count": 2, "total": "46890.00"}
    _run(client, act, importer)

    assert sorted(Payment.objects.values_list("method", "amount")) == [
        ("bank", Decimal("300000.00")), ("offset", Decimal("20000.00")), ("offset", Decimal("26890.00")),
    ]
    rows = client_history(client)["payments"]
    assert {row["method_label"] for row in rows} == {"Банковский перевод", "Зачёт по акту сверки"}
    assert _balance(client, "KZT") == act.closing == Decimal("286510.00")


def test_credit_beyond_debit_leaves_us_owing_the_client(client, importer):
    act = _act([
        sale("10.06.2026", 100, "1"),
        ("12.06.2026", RETURN.format(number="5", day="12.06.2026"), None, 130),
    ])

    _run(client, act, importer)

    summary = client_history(client)["summary"]["by_currency"]["USD"]
    assert (summary["debt"], summary["overpaid"]) == ("0.00", "30.00")
    assert act.closing == Decimal("-30.00")


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


def _debt_and_owed(client, currency="USD"):
    """Долг и «мы должны клиенту» по отдельности: в карточке они не должны висеть вместе."""
    summary = client_history(client)["summary"]["by_currency"][currency]
    return Decimal(summary["debt"]), Decimal(summary["overpaid"])


def test_overpayment_of_an_earlier_act_pays_shipments_of_the_next_act(client, importer):
    first = [sale("10.06.2026", 100, "1"), ("12.06.2026", RETURN.format(number="5", day="12.06.2026"), None, 130)]
    _run(client, _act(first), importer)
    assert _debt_and_owed(client) == (Decimal("0"), Decimal("30.00"))

    wider = _act([*first, sale("15.08.2026", 50, "2")], period=("01.01.2026", "30.09.2026"))
    _run(client, wider, importer)

    # Не «долг 50 и мы должны 30», а как в акте: клиент должен 20.
    assert _debt_and_owed(client) == (Decimal("20.00"), Decimal("0"))
    assert wider.closing == Decimal("20.00")
    document = ClientActDocument.objects.get(kind="payment")
    assert document.unapplied == 0
    # Деньги возврата по-прежнему 130 и датой 12.06 — касса по дням не изменилась.
    assert sorted(document.payments.values_list("amount", flat=True)) == [Decimal("30.00"), Decimal("100.00")]
    assert {timezone.localdate(p.paid_at) for p in document.payments.all()} == {date(2026, 6, 12)}


def test_earlier_overpayment_covers_only_what_the_next_act_adds(client, importer):
    first = [sale("10.06.2026", 100, "1"), bank("12.06.2026", 130, "394")]
    _run(client, _act(first), importer)

    _run(client, _act([*first, sale("15.08.2026", 10, "2")], period=("01.01.2026", "30.09.2026")), importer)

    assert _debt_and_owed(client) == (Decimal("0"), Decimal("20.00"))
    assert sum(ClientActDocument.objects.get(kind="payment").payments.values_list("amount", flat=True)) == 130


def test_shop_payment_schedule_does_not_block_act_credits(client, importer):
    # Магазин платит по понедельникам, а кредит акта — в среду 10.06.2026.
    shop = Store.objects.create(client=client, name="Магазин", payment_schedule_type="weekly", payment_days=[1])
    debt = _crm_order(client, 300, date(2026, 6, 1), store=shop)
    act = _act([bank("10.06.2026", 500, "394")])

    _run(client, act, importer)

    debt.refresh_from_db()
    assert debt.payment_status == "settled"
    assert _debt_and_owed(client) == (Decimal("0"), Decimal("200.00"))


def test_credit_with_only_unshipped_orders_is_refused_before_import(client, importer):
    _crm_order(client, 1000, date(2026, 6, 1), status="confirmed")

    problems = preview_act(client, _act([bank("12.06.2026", 500, "394")]), importer)["problems"]

    assert len(problems) == 1 and "нет отгруженных заказов" in problems[0]


def test_next_act_with_opening_balance_absorbs_overpayment_across_small_requests(client, importer):
    _run(client, _act([sale("10.06.2026", 100, "1"), bank("12.06.2026", 1000, "394")]), importer)
    assert _debt_and_owed(client) == (Decimal("0"), Decimal("900.00"))
    # Следующий акт начинается сальдо «мы должны 900» и добавляет 20 отгрузок по 10.
    july = _act(
        [sale(f"{day:02d}.07.2026", 10, f"1{day:02d}") for day in range(1, 21)],
        period=("01.07.2026", "30.09.2026"),
        opening=(None, 900),
    )
    assert preview_act(client, july, importer)["problems"] == []

    steps = _run(client, july, importer, budget=3)

    assert len(steps) > 3
    assert _debt_and_owed(client) == (Decimal("0"), Decimal("700.00"))
    assert ClientActDocument.objects.get(kind="payment").unapplied == 0
    # Деньги 12.06 в кассе те же 1000, разошлись по заказам; каждый перенос — в журнале.
    assert sum(Payment.objects.filter(paid_at__date=date(2026, 6, 12)).values_list("amount", flat=True)) == 1000
    assert EventLog.objects.filter(event_type="client_act_reclaim").exists()


def test_interrupted_import_shows_money_still_to_spread_and_resumes(client, importer):
    act = _act([*(sale("10.06.2026", 10, str(n)) for n in range(1, 6)), bank("20.06.2026", 50, "394")])

    first = import_act_chunk(client, act, importer, budget=6)

    # Все документы уже в реестре, но 40 из платёжки ещё не разнесены.
    assert (first["remaining"], first["finished"]) == (0, False)
    preview = preview_act(client, act, importer)
    assert (preview["sales"]["count"], preview["payments"]["count"], preview["pending"]) == (0, 0, "40.00")
    assert preview["balance_after"] == "0.00"

    _run(client, act, importer)

    assert _debt_and_owed(client) == (Decimal("0"), Decimal("0"))
    assert preview_act(client, act, importer)["pending"] == "0.00"


def test_debt_held_by_an_open_invoice_does_not_stall_the_reclaim(client, importer):
    held = _crm_order(client, 100, date(2026, 6, 1))
    Payment.objects.create(order=held, amount=Decimal("100"), method="invoice", status="requested")
    first = [sale("10.06.2026", 10, "1"), bank("12.06.2026", 30, "394")]
    _run(client, _act(first), importer)

    steps = _run(client, _act([*first, sale("15.08.2026", 10, "2")], period=("01.01.2026", "30.09.2026")), importer, budget=1)

    assert steps[-1]["finished"]
    newest = Order.objects.get(notes__endswith="Реализация ТМЗ и услуг 2 от 15.08.2026")
    assert newest.payment_status == "settled"
    # Долг заказа со счётом в работе остаётся, переплата акта ушла на новую отгрузку.
    assert _debt_and_owed(client) == (Decimal("100.00"), Decimal("10.00"))


def test_cashier_refund_of_act_overpayment_does_not_block_the_next_act(client, importer):
    _run(client, _act([sale("05.01.2026", 100, "1"), bank("10.01.2026", 300, "394")], period=("01.01.2026", "31.03.2026")), importer)
    overpayment = Payment.objects.filter(amount=Decimal("200")).get()
    refund = PaymentRefund.objects.create(
        payment=overpayment, amount=Decimal("120"), method="cash", status="completed", reason="К возврату",
        completed_at=backdate_moment(date(2026, 4, 5)),
    )
    sync_refund_totals(overpayment, overpayment.order)
    # В 1С возврат кассы — дебет расходным ордером; следующий акт начинается сальдо «мы должны 200».
    april = _act(
        [("05.04.2026", "Расходный кассовый ордер 7 от 05.04.2026", 120, None), sale("03.05.2026", 50, "2")],
        period=("01.04.2026", "30.06.2026"),
        opening=(None, 200),
    )

    preview = preview_act(client, april, importer)

    assert preview["problems"] == []
    assert (preview["crm"]["refunds"], preview["crm"]["last_day"]) == (1, "2026-04-05")
    assert (preview["start"], preview["before_start"], preview["sales"]["count"]) == ("2026-04-06", 1, 1)
    _run(client, april, importer, start=date(2026, 4, 6))
    # Возврат не задвоен: как в акте, мы должны клиенту 30.
    assert _debt_and_owed(client) == (Decimal("0"), Decimal("30.00"))
    assert april.closing == Decimal("-30.00")
    assert refund.pk


def test_confirmed_but_unshipped_order_is_not_a_debit_in_the_balance(client, importer):
    _crm_order(client, 1000, date(2026, 5, 1))
    _crm_order(client, 500, date(2026, 5, 2), status="confirmed")
    june = _act([sale("05.06.2026", 200, "1")], period=("01.06.2026", "30.06.2026"), opening=(1000, None))

    preview = preview_act(client, june, importer)

    assert preview["problems"] == []
    assert preview["balance_after"] == "1200.00"
