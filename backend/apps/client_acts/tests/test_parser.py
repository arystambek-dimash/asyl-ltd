"""Разбор акта сверки 1С: строгая проверка раскладки и итогов."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from rest_framework.exceptions import ValidationError

from apps.client_acts.parser import parse_act

from .act_factory import act_xlsx, bank, cash, sale


def _error(content: bytes) -> str:
    with pytest.raises(ValidationError) as caught:
        parse_act(content)
    assert caught.value.detail["code"] == "act_format"
    return str(caught.value.detail["detail"])


def test_reads_header_rows_and_totals_of_a_1c_act():
    content = act_xlsx(
        [
            sale("10.06.2026", 6625, "00000000527"),
            sale("10.06.2026", 8125, "00000000528"),
            bank("10.07.2026", 20000, "00000000394"),
            sale("13.07.2026", 4140, "00000000666"),
        ],
        counterparty='ОсОО "Дан Агро Групп"',
    )

    act = parse_act(content)

    assert act.number == "00000000509"
    assert act.period_start == date(2026, 1, 1)
    assert act.period_end == date(2026, 10, 7)
    assert act.currency == "USD"
    assert act.counterparty == 'ОсОО "Дан Агро Групп"'
    assert act.opening == Decimal("0")
    assert act.closing == Decimal("-1110")
    assert [(row.day, row.kind, row.document, row.amount, row.method) for row in act.rows] == [
        (date(2026, 6, 10), "sale", "Реализация ТМЗ и услуг 00000000527 от 10.06.2026", Decimal("6625"), None),
        (date(2026, 6, 10), "sale", "Реализация ТМЗ и услуг 00000000528 от 10.06.2026", Decimal("8125"), None),
        (date(2026, 7, 10), "payment", "Платежное поручение (входящее) 00000000394 от 10.07.2026",
         Decimal("20000"), "bank"),
        (date(2026, 7, 13), "sale", "Реализация ТМЗ и услуг 00000000666 от 13.07.2026", Decimal("4140"), None),
    ]
    # Номер строки Excel — чтобы человек нашёл документ в файле.
    assert act.rows[0].line == 10


def test_cash_order_is_cash_and_opening_balance_is_signed():
    act = parse_act(act_xlsx([cash("02.02.2026", "1500.50")], currency="KZT", opening=(None, 500)))

    assert act.currency == "KZT"
    assert act.opening == Decimal("-500")
    assert act.closing == Decimal("-2000.50")
    assert act.rows[0].kind == "payment"
    assert act.rows[0].method == "cash"


@pytest.mark.parametrize("label", ["тенге", "тг", "KZT"])
def test_tenge_spellings(label):
    assert parse_act(act_xlsx([sale("10.06.2026", 100)], currency=label)).currency == "KZT"


def test_amounts_written_as_text_and_dates_as_cells():
    content = act_xlsx([(datetime(2026, 6, 10), "Реализация ТМЗ и услуг 1 от 10.06.2026", "6 625,00", None)])

    act = parse_act(content)

    assert act.rows[0].day == date(2026, 6, 10)
    assert act.rows[0].amount == Decimal("6625.00")


def test_unknown_currency_is_refused():
    assert "валют" in _error(act_xlsx([sale("10.06.2026", 100)], currency="EUR"))


def test_not_an_xlsx_file_is_refused():
    assert "xlsx" in _error(b"not a workbook")


def test_missing_columns_are_refused():
    message = _error(act_xlsx([sale("10.06.2026", 100)], header=("Дата", "Документ", "Сумма", "Кредит")))
    assert "Дебет" in message


def test_turnover_mismatch_is_refused():
    message = _error(act_xlsx([sale("10.06.2026", 100)], turnover=(150, None)))
    assert "Обороты" in message


def test_closing_balance_mismatch_is_refused():
    message = _error(act_xlsx([sale("10.06.2026", 100)], closing=(90, None)))
    assert "Сальдо на конец" in message


def test_unknown_document_with_amount_is_refused_with_its_line():
    message = _error(act_xlsx([
        sale("10.06.2026", 100),
        ("11.06.2026", "Возврат товаров от покупателя 00000000003 от 11.06.2026", None, 40),
    ]))
    assert "строка 12" in message
    assert "Возврат товаров от покупателя" in message


def test_sale_on_the_credit_side_is_refused():
    message = _error(act_xlsx([("10.06.2026", "Реализация ТМЗ и услуг 5 от 10.06.2026", None, 100)]))
    assert "строка 10" in message


def test_row_with_amount_and_bad_date_is_refused():
    message = _error(act_xlsx([("31.02.26x", "Реализация ТМЗ и услуг 5 от 10.06.2026", 100, None)]))
    assert "дата" in message.lower()


def test_act_without_movements_is_refused():
    assert "нет документов" in _error(act_xlsx([]))


def test_same_document_twice_is_refused():
    message = _error(act_xlsx([sale("10.06.2026", 100, "7"), sale("10.06.2026", 100, "7")]))
    assert "строка 12" in message
    assert "строке 10" in message
