"""«Отправить отчёт» из истории грузчика: текст владельца, кому и как отправить, очередь бота."""
from datetime import date, datetime, time, timedelta

import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.bots.models import BotChat, BotClientProfile, OutgoingMessage
from apps.bots.parsing import parse_rail_report
from apps.bots.runner import RUNNING, BotRunner
from apps.bots.tests.samples import train_order
from apps.bots.tests.telegram_fakes import DINARA, DINARA_CHAT, FakeTelegram, bot_alive
from apps.bots.wagon_report import (
    BOT,
    BOT_OFF,
    LINK,
    MAX_SEND_ATTEMPTS,
    NO_USERNAME,
    NOT_STARTED,
    REPORT_QUEUE_TIMEOUT,
    SENDING_TIMEOUT,
    compose_rail_report,
    message_state,
    period_report,
    recipient_to,
    report_recipient,
    send_pending_reports,
    send_wagon_report,
)
from apps.catalog.models import Product, ProductAlias
from apps.clients.models import Client
from apps.common.telegram import TelegramError, TelegramOutcomeUnknown, TelegramRefused
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem
from apps.shipments.models import Shipment

pytestmark = pytest.mark.django_db

DAY = date(2026, 9, 24)  # четверг


def _at(day=DAY, hour=12, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _shipped(client, product, *, bags=1360, truck_number="", station="Раустан", at=None, wagons=()):
    """Отгруженный вагонный заказ: кнопкой «Отгружено» (без вагонов) или по отчёту (с вагонами)."""
    return train_order(
        client, product, bags=bags, shipped_at=at or _at(), wagons=wagons, truck_number=truck_number,
        rail_station=station)


def _rows(*orders):
    """Заказы, как их читает история грузчика: словари — отдельными запросами на всю страницу."""
    return list(
        Order.objects.filter(pk__in=[order.pk for order in orders])
        .select_related("client__user", "shipment")
        .prefetch_related("items__product", "shipment__wagons")
        .order_by("-pk")
    )


def _text(*orders):
    return compose_rail_report(_rows(*orders)).text


@pytest.fixture
def other_client(department):
    return Client.objects.create_with_user(
        first_name="Бек", phone="+998 90 222 33 44", company_name="ООО BEK TRADE",
        currency="USD", department=department, country="Узбекистан",
    )


# --- текст отчёта -------------------------------------------------------------------------------


def test_order_shipped_by_the_button_reports_its_wagon_number_and_tonnes(client, product):
    """№366: вагонный заказ отгружен кнопкой — вагонов отгрузки нет, есть номер заказа и мешки."""
    order = _shipped(client, product, bags=8160, truck_number="12345678", station="")

    assert _text(order).splitlines() == [
        "чт 24.09.26 Узбекистан ООО OSIYO NAV NIHOL",
        "Ст. 1 вагон",
        "Д1с-12345678-408 тн",
    ]


def test_order_without_a_number_or_a_code_says_so(client):
    no_code = Product.objects.create(name="ДБН 1с", color="Red", weight_kg="50")
    half = Product.objects.create(name="Отруби", color="Blue", weight_kg="25")
    ProductAlias.objects.create(code="ОТР", product=half)
    order = _shipped(client, no_code)
    OrderItem.objects.create(order=order, product=half, quantity=30, unit_price="7.50")

    assert _text(order).splitlines()[1:] == [
        "Ст. Раустан 2 вагон",
        f"{no_code}-без номера-68 тн",
        "ОТР-без номера-0,75 тн",
    ]


def test_report_order_lists_its_wagons_and_parses_back(client, product):
    order = _shipped(client, product, bags=2720, wagons=("28087658", "28087666"))

    text = _text(order)

    assert text.splitlines() == [
        "чт 24.09.26 Узбекистан ООО OSIYO NAV NIHOL",
        "Ст. Раустан 2 вагон",
        "Д1с-28087658-68 тн",
        "Д1с-28087666-68 тн",
    ]
    assert parse_rail_report(text).ok


def test_client_is_named_as_reports_name_it_and_code_is_the_latest_spelling(client, product, boss):
    order = _shipped(client, product, truck_number="12345678")
    BotClientProfile.objects.create(name="OSIYO NAV", client=client, currency="USD", created_by=boss)
    BotClientProfile.objects.create(name="OSIYO KZT", client=client, currency="KZT", created_by=boss)
    ProductAlias.objects.create(code="D1", spelling="D-1", product=product)

    header, _, line = _text(order).splitlines()

    assert header == "чт 24.09.26 Узбекистан OSIYO NAV"
    assert line == "D-1-12345678-68 тн"


def test_period_report_groups_orders_by_day_client_and_station_in_time_order(client, other_client, product):
    first = _shipped(client, product, truck_number="28087658", at=_at(hour=9))
    second = _shipped(client, product, truck_number="28087666", at=_at(hour=11))
    other_station = _shipped(client, product, truck_number="28087674", station="Сарыагаш", at=_at(hour=10))
    next_day = _shipped(client, product, truck_number="28087682", at=_at(DAY.replace(day=25), hour=8))
    other = _shipped(other_client, product, truck_number="28087690", at=_at(hour=8))

    report = compose_rail_report(_rows(first, second, other_station, next_day, other))

    assert report.text == "\n\n".join([
        "чт 24.09.26 Узбекистан ООО BEK TRADE\nСт. Раустан 1 вагон\nД1с-28087690-68 тн",
        "чт 24.09.26 Узбекистан ООО OSIYO NAV NIHOL\nСт. Раустан 2 вагон\nД1с-28087658-68 тн\nД1с-28087666-68 тн",
        "чт 24.09.26 Узбекистан ООО OSIYO NAV NIHOL\nСт. Сарыагаш 1 вагон\nД1с-28087674-68 тн",
        "пт 25.09.26 Узбекистан ООО OSIYO NAV NIHOL\nСт. Раустан 1 вагон\nД1с-28087682-68 тн",
    ])
    assert report.order_ids == [other.pk, first.pk, other_station.pk, second.pk, next_day.pk]


def test_only_shipped_wagon_orders_are_reported(client, product):
    shipped = _shipped(client, product, truck_number="12345678")
    truck = _shipped(client, product)
    Order.objects.filter(pk=truck.pk).update(transport_type="truck")
    pending = Order.objects.create(client=client, currency="USD", transport_type="train", status="confirmed")

    report = compose_rail_report(_rows(shipped, truck, pending))

    assert report.order_ids == [shipped.pk]
    assert compose_rail_report(_rows(truck, pending)).text == ""


def test_dictionaries_are_read_once_for_the_whole_period(client, other_client, product, django_assert_num_queries):
    orders = [_shipped(client, product, truck_number="12345678", at=_at(hour=hour)) for hour in range(8, 14)]
    orders += [_shipped(other_client, product, bags=2720, wagons=("28087658", "28087666"))]
    rows = _rows(*orders)

    # Коды товаров и названия клиентов — по запросу на весь период.
    with django_assert_num_queries(2):
        report = compose_rail_report(rows)

    assert len(report.order_ids) == 7


# --- кому и как ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "to"), [
    ("Динара", "Динаре"), ("Мария", "Марии"), ("Таня", "Тане"), ("Азамат", "Азамату"),
    ("Андрей", "Андрею"), ("ДИНАРА", "ДИНАРЕ"), ("Айгуль", "Айгуль"), ("Динара Ахметова", "Динара Ахметова"),
])
def test_recipient_is_named_in_the_dative(name, to):
    assert recipient_to(name) == to


def test_without_the_bot_the_report_goes_by_a_telegram_link_to_dinara():
    recipient = report_recipient()

    assert (recipient.name, recipient.to, recipient.username, recipient.delivery, recipient.reason) == (
        "Динара", "Динаре", "", LINK, BOT_OFF)


def test_enabled_bot_sends_to_the_private_chat_of_the_recipient(bot_on):
    recipient = report_recipient()
    assert (recipient.delivery, recipient.chat_id, recipient.username, recipient.reason) == (
        BOT, DINARA_CHAT, DINARA, "")

    bot_on.report_recipient_username = ""
    bot_on.save()
    assert (report_recipient().delivery, report_recipient().reason) == (LINK, NO_USERNAME)


def test_bot_cannot_write_first_to_a_recipient_who_never_started_it(bot_on):
    BotChat.objects.all().delete()

    recipient = report_recipient()

    assert (recipient.delivery, recipient.reason, recipient.chat_id) == (LINK, NOT_STARTED, "")


def test_bot_switched_off_on_the_server_or_in_the_journal_means_a_link(bot_on, settings):
    settings.TELEGRAM_BOT_ENABLED = False
    assert report_recipient().delivery == LINK

    settings.TELEGRAM_BOT_ENABLED = True
    bot_on.enabled = False
    bot_on.save()
    recipient = report_recipient()
    assert (recipient.delivery, recipient.username, recipient.reason) == (LINK, DINARA, BOT_OFF)


def test_bot_is_offered_only_while_its_process_is_alive(bot_on, settings):
    """Флаги включены, но бот не работает — ссылкой: иначе отчёт вечно ждал бы в очереди."""
    assert report_recipient().delivery == BOT
    max_age = timedelta(seconds=settings.TELEGRAM_BOT_HEARTBEAT_MAX_AGE_SECONDS)

    # Контейнер бота остановлен или простаивает (TELEGRAM_BOT_ENABLED=0 только у него): круги не отмечаются.
    bot_alive(bot_on, polled_at=timezone.now() - max_age - timedelta(seconds=1)).save()
    assert report_recipient().delivery == LINK
    bot_on.polled_at = None
    bot_on.save()
    assert report_recipient().delivery == LINK

    # Нет токена или Telegram недоступен — бот пишет «degraded».
    bot_alive(bot_on).save()
    bot_on.runtime_status = "degraded"
    bot_on.save()
    assert report_recipient().delivery == LINK

    # Telegram отверг токен.
    bot_alive(bot_on).save()
    bot_on.bot_state = "unauthorized"
    bot_on.save()
    recipient = report_recipient()
    assert (recipient.delivery, recipient.username) == (LINK, DINARA)


# --- отправка -----------------------------------------------------------------------------------


def test_link_sending_marks_shipments_and_logs_an_event_per_order(client, product, wagon_viewer):
    orders = [_shipped(client, product, truck_number="12345678"), _shipped(client, product, truck_number="28087658")]

    message = send_wagon_report(orders, "отчёт", wagon_viewer, delivery=LINK, key="key-00000001")

    assert (message.status, message.recipient_name, message.chat_id, message.text) == (
        OutgoingMessage.LINK, "Динара", "", "отчёт")
    assert message.order_ids == [order.pk for order in orders]
    for shipment in Shipment.objects.filter(order__in=orders):
        assert (shipment.report_sent_at, shipment.report_sent_by, shipment.report_message) == (
            message.created_at, wagon_viewer, message)
    events = EventLog.objects.filter(event_type="rail_report").order_by("order_id")
    assert [(event.order_id, event.message) for event in events] == [
        (order.pk, "Отчёт о вагонах отправлен Динаре ссылкой Telegram") for order in orders
    ]
    assert events[0].payload["message_id"] == message.pk


def test_bot_sending_is_queued_once_per_press(client, product, wagon_viewer, bot_on):
    order = _shipped(client, product, truck_number="12345678")

    first = send_wagon_report([order], "отчёт", wagon_viewer, delivery=BOT, key="key-00000002")
    again = send_wagon_report([order], "отчёт", wagon_viewer, delivery=BOT, key="key-00000002")

    assert again.pk == first.pk
    assert (first.status, first.chat_id, first.username) == (OutgoingMessage.QUEUED, DINARA_CHAT, DINARA)
    assert OutgoingMessage.objects.count() == 1
    assert EventLog.objects.get(event_type="rail_report").message == "Отчёт о вагонах отправлен Динаре Telegram-ботом"


def test_bot_delivery_is_refused_when_the_bot_went_off(client, product, wagon_viewer):
    order = _shipped(client, product, truck_number="12345678")

    with pytest.raises(ValidationError) as caught:
        send_wagon_report([order], "отчёт", wagon_viewer, delivery=BOT, key="key-00000003")

    assert caught.value.detail["code"] == "report_bot_unavailable"
    assert not OutgoingMessage.objects.exists()
    assert Shipment.objects.get(order=order).report_sent_at is None


def test_bot_sends_a_queued_report_once(client, product, wagon_viewer, bot_on):
    order = _shipped(client, product, truck_number="12345678")
    message = send_wagon_report([order], "отчёт", wagon_viewer, delivery=BOT, key="key-00000004")
    api = FakeTelegram()

    assert send_pending_reports(api) == 1
    assert send_pending_reports(api) == 0

    assert api.sent == [(DINARA_CHAT, "отчёт", "")]
    message.refresh_from_db()
    assert (message.status, message.provider_message_id, message.error) == (
        OutgoingMessage.SENT, f"{DINARA_CHAT}:1001", "")
    assert message.sent_at is not None


def test_provider_refusals_are_retried_and_then_visible(client, product, wagon_viewer, bot_on):
    """Telegram отказал (4xx) — сообщение точно не ушло: бот повторяет его на следующих кругах."""
    order = _shipped(client, product, truck_number="12345678")
    message = send_wagon_report([order], "отчёт", wagon_viewer, delivery=BOT, key="key-00000005")
    api = FakeTelegram(fail_send=TelegramError("Telegram sendMessage: HTTP 429"))

    with pytest.raises(TelegramError):
        send_pending_reports(api)
    message.refresh_from_db()
    assert (message.status, message.attempts, message.error) == (
        OutgoingMessage.QUEUED, 1, "Telegram sendMessage: HTTP 429")

    for _ in range(MAX_SEND_ATTEMPTS - 1):
        with pytest.raises(TelegramError):
            send_pending_reports(api)
    message.refresh_from_db()
    assert (message.status, message.attempts) == (OutgoingMessage.FAILED, MAX_SEND_ATTEMPTS)
    assert send_pending_reports(FakeTelegram()) == 0


def test_refused_report_does_not_hold_back_the_next_one(client, product, wagon_viewer, bot_on):
    order = _shipped(client, product, truck_number="12345678")
    refused = send_wagon_report([order], "первый", wagon_viewer, delivery=BOT, key="key-00000021")
    send_wagon_report([order], "второй", wagon_viewer, delivery=BOT, key="key-00000022")

    class RefusesFirst(FakeTelegram):
        def send_message(self, chat_id, text, *, reply_to=""):
            if text == "первый":
                raise TelegramRefused("Telegram sendMessage: HTTP 403 — Forbidden: bot was blocked by the user")
            return super().send_message(chat_id, text, reply_to=reply_to)

    api = RefusesFirst()
    assert send_pending_reports(api) == 1

    assert [text for _, text, _ in api.sent] == ["второй"]
    refused.refresh_from_db()
    assert (refused.status, refused.attempts) == (OutgoingMessage.QUEUED, 1)
    assert "blocked" in refused.error


class _WatchedTelegram(FakeTelegram):
    """Запоминает статус строки в момент отправки."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.statuses = []

    def send_message(self, chat_id, text, *, reply_to=""):
        self.statuses.append(OutgoingMessage.objects.get(text=text).status)
        return super().send_message(chat_id, text, reply_to=reply_to)


def test_report_is_claimed_before_it_is_sent(client, product, wagon_viewer, bot_on):
    order = _shipped(client, product, truck_number="12345678")
    send_wagon_report([order], "отчёт", wagon_viewer, delivery=BOT, key="key-00000007")
    api = _WatchedTelegram()

    assert send_pending_reports(api) == 1

    assert api.statuses == [OutgoingMessage.SENDING]
    assert OutgoingMessage.objects.get().status == OutgoingMessage.SENT


def test_unanswered_sending_is_not_repeated_and_asks_to_check_telegram(client, product, wagon_viewer, bot_on):
    """Тайм-аут после того, как Telegram принял сообщение: повтор задвоил бы отчёт у Динары."""
    order = _shipped(client, product, truck_number="12345678")
    message = send_wagon_report([order], "отчёт", wagon_viewer, delivery=BOT, key="key-00000008")
    api = FakeTelegram(fail_send=TelegramOutcomeUnknown("Telegram sendMessage: нет связи (TimeoutError)"))

    with pytest.raises(TelegramError):
        send_pending_reports(api)

    message.refresh_from_db()
    assert (message.status, message.error) == (OutgoingMessage.UNKNOWN, "Telegram sendMessage: нет связи (TimeoutError)")
    assert message_state(message) == (OutgoingMessage.UNKNOWN, "Telegram sendMessage: нет связи (TimeoutError)")
    retry = FakeTelegram()
    assert send_pending_reports(retry) == 0
    assert retry.sent == []


def test_sending_interrupted_by_a_crash_is_not_repeated(client, product, wagon_viewer, bot_on):
    """Бот упал между отправкой и отметкой: строка «отправляется» — без повтора, человек проверит Telegram."""
    order = _shipped(client, product, truck_number="12345678")
    stuck = send_wagon_report([order], "застрял", wagon_viewer, delivery=BOT, key="key-00000009")
    in_flight = send_wagon_report([order], "уходит", wagon_viewer, delivery=BOT, key="key-00000010")
    now = timezone.now()
    OutgoingMessage.objects.filter(pk=stuck.pk).update(
        status=OutgoingMessage.SENDING, updated_at=now - SENDING_TIMEOUT - timedelta(seconds=1))
    # Другой процесс бота отправляет прямо сейчас — его строку не трогаем.
    OutgoingMessage.objects.filter(pk=in_flight.pk).update(status=OutgoingMessage.SENDING, updated_at=now)
    stuck.refresh_from_db()
    assert message_state(stuck) == (OutgoingMessage.UNKNOWN, "бот прервался во время отправки")
    api = FakeTelegram()

    assert send_pending_reports(api) == 0

    assert api.sent == []
    stuck.refresh_from_db()
    in_flight.refresh_from_db()
    assert (stuck.status, stuck.error) == (OutgoingMessage.UNKNOWN, "бот прервался во время отправки")
    assert in_flight.status == OutgoingMessage.SENDING


def test_report_the_bot_did_not_take_in_time_is_not_sent_later(client, product, wagon_viewer, bot_on):
    """Бот лежал дольше срока: отчёт уже «Не отправлено» (его отправили ссылкой) — поднявшись, бот его не шлёт."""
    order = _shipped(client, product, truck_number="12345678")
    old = send_wagon_report([order], "старый", wagon_viewer, delivery=BOT, key="key-00000011")
    fresh = send_wagon_report([order], "свежий", wagon_viewer, delivery=BOT, key="key-00000012")
    OutgoingMessage.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - REPORT_QUEUE_TIMEOUT - timedelta(seconds=1))
    old.refresh_from_db()
    assert message_state(old) == (OutgoingMessage.FAILED, "бот не отправил за 10 минут")
    assert message_state(fresh) == (OutgoingMessage.QUEUED, "")
    api = FakeTelegram()

    assert send_pending_reports(api) == 1

    assert [text for _, text, _ in api.sent] == ["свежий"]
    old.refresh_from_db()
    assert (old.status, old.error) == (OutgoingMessage.FAILED, "бот не отправил за 10 минут")


def test_bot_loop_sends_queued_reports(client, product, wagon_viewer, bot_on):
    order = _shipped(client, product, truck_number="12345678")
    send_wagon_report([order], "отчёт", wagon_viewer, delivery=BOT, key="key-00000006")
    api = FakeTelegram()

    assert BotRunner(client_factory=lambda: api).poll_once() == RUNNING

    assert api.sent == [(DINARA_CHAT, "отчёт", "")]
    assert OutgoingMessage.objects.get().status == OutgoingMessage.SENT


def test_long_report_goes_in_parts(client, product, wagon_viewer, bot_on):
    text = "\n".join(f"Д1с-{index:08d}-68 тн" for index in range(400))
    order = _shipped(client, product, truck_number="12345678")
    send_wagon_report([order], text, wagon_viewer, delivery=BOT, key="key-00000013")
    api = FakeTelegram()

    assert send_pending_reports(api) == 1

    assert len(api.sent) > 1
    assert "\n".join(part for _, part, _ in api.sent) == text
    assert OutgoingMessage.objects.get().provider_message_id == f"{DINARA_CHAT}:1001"


def test_failure_after_a_sent_part_is_not_repeated(client, product, wagon_viewer, bot_on):
    text = "\n".join(f"Д1с-{index:08d}-68 тн" for index in range(400))
    order = _shipped(client, product, truck_number="12345678")
    message = send_wagon_report([order], text, wagon_viewer, delivery=BOT, key="key-00000014")

    class FailsSecond(FakeTelegram):
        def send_message(self, chat_id, text, *, reply_to=""):
            if self.sent:
                raise TelegramError("Telegram sendMessage: HTTP 429")
            return super().send_message(chat_id, text, reply_to=reply_to)

    with pytest.raises(TelegramOutcomeUnknown):
        send_pending_reports(FailsSecond())

    message.refresh_from_db()
    assert message.status == OutgoingMessage.UNKNOWN
    assert message.error.startswith("ушла часть отчёта (1 из")


def test_period_report_covers_every_department_and_skips_other_days(client, other_client, product):
    today = _shipped(client, product, truck_number="12345678", at=_at(hour=9))
    _shipped(other_client, product, truck_number="28087658", at=_at(DAY - timedelta(days=1)))

    report = period_report(DAY, DAY)

    assert report.order_ids == [today.pk]
    assert period_report(DAY - timedelta(days=1), DAY).order_ids == [
        order.pk for order in Order.objects.order_by("shipment__shipped_at")]


def test_tonnes_keep_fractions(client):
    sack = Product.objects.create(name="Мука 1с", color="Red", weight_kg="25")
    order = _shipped(client, sack, bags=2701, truck_number="12345678")

    # 2701 мешок × 25 кг = 67,525 т.
    assert _text(order).splitlines()[-1] == f"{sack}-12345678-67,525 тн"
