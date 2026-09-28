"""«Отправить отчёт» из истории грузчика: текст владельца, получатели, очередь бота и доставки."""
from datetime import date, datetime, time, timedelta

import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.bots.models import BotChat, BotClientProfile, OutgoingMessage, ReportDelivery
from apps.bots.parsing import parse_rail_report
from apps.bots.runner import RUNNING, BotRunner
from apps.bots.tests.samples import train_order
from apps.bots.tests.telegram_fakes import DINARA, DINARA_CHAT, FakeTelegram, bot_alive
from apps.bots.wagon_report import (
    BOT_OFF,
    MAX_SEND_ATTEMPTS,
    NO_RECIPIENTS,
    NOT_STARTED,
    REPORT_QUEUE_TIMEOUT,
    SENDING_TIMEOUT,
    compose_rail_report,
    delivery_state,
    period_report,
    report_draft,
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


# --- кому -----------------------------------------------------------------------------------------


def _started(username, chat_id, title=""):
    """Человек написал боту /start — у бота есть его личный чат."""
    return BotChat.objects.create(
        chat_id=chat_id, chat_type="private", title=title or username, username=username,
        last_message_at=timezone.now())


def test_draft_lists_recipients_and_who_can_get_the_report(client, product, bot_on):
    bot_on.report_recipients = [DINARA, "d1maaash"]
    bot_on.save()
    order = _shipped(client, product, truck_number="12345678")

    draft = report_draft(_rows(order))

    assert draft["recipients"] == [
        {"username": DINARA, "name": "Динара", "ready": True},
        {"username": "d1maaash", "name": "", "ready": False},
    ]
    assert (draft["can_send"], draft["reason"], draft["order_ids"]) == (True, "", [order.pk])


@pytest.mark.parametrize(("change", "reason"), [
    ("bot_off", BOT_OFF), ("no_recipients", NO_RECIPIENTS), ("not_started", NOT_STARTED),
])
def test_draft_says_why_the_report_cannot_be_sent(client, product, bot_on, settings, change, reason):
    if change == "bot_off":
        settings.TELEGRAM_BOT_ENABLED = False
    elif change == "no_recipients":
        bot_on.report_recipients = []
        bot_on.save()
    else:
        BotChat.objects.all().delete()

    draft = report_draft(_rows(_shipped(client, product, truck_number="12345678")))

    assert (draft["can_send"], draft["reason"]) == (False, reason)


def test_bot_is_offered_only_while_its_process_is_alive(client, product, bot_on, settings):
    """Флаги включены, но бот не работает — отправить нельзя: иначе отчёт вечно ждал бы в очереди."""
    rows = _rows(_shipped(client, product, truck_number="12345678"))
    assert report_draft(rows)["can_send"]
    max_age = timedelta(seconds=settings.TELEGRAM_BOT_HEARTBEAT_MAX_AGE_SECONDS)

    # Контейнер бота остановлен или простаивает (TELEGRAM_BOT_ENABLED=0 только у него): круги не отмечаются.
    bot_alive(bot_on, polled_at=timezone.now() - max_age - timedelta(seconds=1)).save()
    assert report_draft(rows)["reason"] == BOT_OFF
    # Нет токена или Telegram недоступен — бот пишет «degraded».
    bot_alive(bot_on).save()
    bot_on.runtime_status = "degraded"
    bot_on.save()
    assert report_draft(rows)["reason"] == BOT_OFF
    # Telegram отверг токен.
    bot_alive(bot_on).save()
    bot_on.bot_state = "unauthorized"
    bot_on.save()
    assert report_draft(rows)["reason"] == BOT_OFF


# --- отправка -----------------------------------------------------------------------------------


def test_sending_queues_a_delivery_per_ready_recipient_once_per_press(client, product, wagon_viewer, bot_on):
    bot_on.report_recipients = [DINARA, "d1maaash", "not_started"]
    bot_on.save()
    _started("d1maaash", "900", "Димаш")
    orders = [_shipped(client, product, truck_number="12345678"), _shipped(client, product, truck_number="28087658")]

    first = send_wagon_report(orders, "отчёт", wagon_viewer, key="key-00000002")
    again = send_wagon_report(orders, "отчёт", wagon_viewer, key="key-00000002")

    assert again.pk == first.pk
    assert (OutgoingMessage.objects.count(), first.order_ids) == (1, [order.pk for order in orders])
    # Кто не писал боту /start — не получит: бот не может написать первым.
    assert list(first.deliveries.values_list("username", "name", "chat_id", "status")) == [
        (DINARA, "Динара", DINARA_CHAT, "queued"), ("d1maaash", "Димаш", "900", "queued")]
    for shipment in Shipment.objects.filter(order__in=orders):
        assert (shipment.report_sent_at, shipment.report_sent_by, shipment.report_message) == (
            first.created_at, wagon_viewer, first)
    events = EventLog.objects.filter(event_type="rail_report").order_by("order_id")
    assert [(event.order_id, event.message) for event in events] == [
        (order.pk, f"Отчёт о вагонах отправлен Telegram-ботом: @{DINARA}, @d1maaash") for order in orders
    ]
    assert events[0].payload["recipients"] == [DINARA, "d1maaash"]


@pytest.mark.parametrize(("change", "code"), [
    ("bot_off", "report_bot_off"), ("no_recipients", "report_no_recipients"), ("not_started", "report_not_started"),
])
def test_sending_is_refused_with_the_reason(client, product, wagon_viewer, bot_on, settings, change, code):
    if change == "bot_off":
        settings.TELEGRAM_BOT_ENABLED = False
    elif change == "no_recipients":
        bot_on.report_recipients = []
        bot_on.save()
    else:
        BotChat.objects.all().delete()
    order = _shipped(client, product, truck_number="12345678")

    with pytest.raises(ValidationError) as caught:
        send_wagon_report([order], "отчёт", wagon_viewer, key="key-00000003")

    assert caught.value.detail["code"] == code
    assert not OutgoingMessage.objects.exists()
    assert Shipment.objects.get(order=order).report_sent_at is None


def test_bot_sends_each_delivery_once(client, product, wagon_viewer, bot_on):
    bot_on.report_recipients = [DINARA, "d1maaash"]
    bot_on.save()
    _started("d1maaash", "900")
    order = _shipped(client, product, truck_number="12345678")
    message = send_wagon_report([order], "отчёт", wagon_viewer, key="key-00000004")
    api = FakeTelegram()

    assert send_pending_reports(api) == 2
    assert send_pending_reports(api) == 0

    assert api.sent == [(DINARA_CHAT, "отчёт", ""), ("900", "отчёт", "")]
    first = message.deliveries.first()
    assert (first.status, first.provider_message_id, first.error) == (ReportDelivery.SENT, f"{DINARA_CHAT}:1001", "")
    assert first.sent_at is not None


def test_provider_refusals_are_retried_and_then_visible(client, product, wagon_viewer, bot_on):
    """Telegram отказал — сообщение точно не ушло: бот повторяет его на следующих кругах."""
    order = _shipped(client, product, truck_number="12345678")
    message = send_wagon_report([order], "отчёт", wagon_viewer, key="key-00000005")
    delivery = message.deliveries.get()
    api = FakeTelegram(fail_send=TelegramError("Telegram sendMessage: нет связи (URLError)"))

    with pytest.raises(TelegramError):
        send_pending_reports(api)
    delivery.refresh_from_db()
    assert (delivery.status, delivery.attempts, delivery.error) == (
        ReportDelivery.QUEUED, 1, "Telegram sendMessage: нет связи (URLError)")

    for _ in range(MAX_SEND_ATTEMPTS - 1):
        with pytest.raises(TelegramError):
            send_pending_reports(api)
    delivery.refresh_from_db()
    assert (delivery.status, delivery.attempts) == (ReportDelivery.FAILED, MAX_SEND_ATTEMPTS)
    assert send_pending_reports(FakeTelegram()) == 0


def test_refused_delivery_does_not_hold_back_the_next_one(client, product, wagon_viewer, bot_on):
    bot_on.report_recipients = [DINARA, "d1maaash"]
    bot_on.save()
    _started("d1maaash", "900")
    order = _shipped(client, product, truck_number="12345678")
    message = send_wagon_report([order], "отчёт", wagon_viewer, key="key-00000021")

    class BlockedByDinara(FakeTelegram):
        def send_message(self, chat_id, text, *, reply_to=""):
            if chat_id == DINARA_CHAT:
                raise TelegramRefused("Telegram sendMessage: HTTP 403 — Forbidden: bot was blocked by the user")
            return super().send_message(chat_id, text, reply_to=reply_to)

    api = BlockedByDinara()
    assert send_pending_reports(api) == 1

    assert api.sent == [("900", "отчёт", "")]
    refused = message.deliveries.get(username=DINARA)
    assert (refused.status, refused.attempts) == (ReportDelivery.QUEUED, 1)
    assert "blocked" in refused.error


class _WatchedTelegram(FakeTelegram):
    """Запоминает статус доставки в момент отправки."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.statuses = []

    def send_message(self, chat_id, text, *, reply_to=""):
        self.statuses.append(ReportDelivery.objects.get(chat_id=chat_id).status)
        return super().send_message(chat_id, text, reply_to=reply_to)


def test_delivery_is_claimed_before_it_is_sent(client, product, wagon_viewer, bot_on):
    order = _shipped(client, product, truck_number="12345678")
    send_wagon_report([order], "отчёт", wagon_viewer, key="key-00000007")
    api = _WatchedTelegram()

    assert send_pending_reports(api) == 1

    assert api.statuses == [ReportDelivery.SENDING]
    assert ReportDelivery.objects.get().status == ReportDelivery.SENT


def test_unanswered_sending_is_not_repeated_and_asks_to_check_telegram(client, product, wagon_viewer, bot_on):
    """Тайм-аут после того, как Telegram принял сообщение: повтор задвоил бы отчёт у получателя."""
    order = _shipped(client, product, truck_number="12345678")
    send_wagon_report([order], "отчёт", wagon_viewer, key="key-00000008")
    api = FakeTelegram(fail_send=TelegramOutcomeUnknown("Telegram sendMessage: нет связи (TimeoutError)"))

    with pytest.raises(TelegramError):
        send_pending_reports(api)

    delivery = ReportDelivery.objects.get()
    assert (delivery.status, delivery.error) == (ReportDelivery.UNKNOWN, "Telegram sendMessage: нет связи (TimeoutError)")
    assert delivery_state(delivery) == (ReportDelivery.UNKNOWN, "Telegram sendMessage: нет связи (TimeoutError)")
    retry = FakeTelegram()
    assert send_pending_reports(retry) == 0
    assert retry.sent == []


def test_sending_interrupted_by_a_crash_is_not_repeated(client, product, wagon_viewer, bot_on):
    """Бот упал между отправкой и отметкой: доставка «отправляется» — без повтора, человек проверит Telegram."""
    order = _shipped(client, product, truck_number="12345678")
    stuck = send_wagon_report([order], "застрял", wagon_viewer, key="key-00000009").deliveries.get()
    in_flight = send_wagon_report([order], "уходит", wagon_viewer, key="key-00000010").deliveries.get()
    now = timezone.now()
    ReportDelivery.objects.filter(pk=stuck.pk).update(
        status=ReportDelivery.SENDING, updated_at=now - SENDING_TIMEOUT - timedelta(seconds=1))
    # Другой процесс бота отправляет прямо сейчас — его строку не трогаем.
    ReportDelivery.objects.filter(pk=in_flight.pk).update(status=ReportDelivery.SENDING, updated_at=now)
    stuck.refresh_from_db()
    assert delivery_state(stuck) == (ReportDelivery.UNKNOWN, "бот прервался во время отправки")
    api = FakeTelegram()

    assert send_pending_reports(api) == 0

    assert api.sent == []
    stuck.refresh_from_db()
    in_flight.refresh_from_db()
    assert (stuck.status, stuck.error) == (ReportDelivery.UNKNOWN, "бот прервался во время отправки")
    assert in_flight.status == ReportDelivery.SENDING


def test_delivery_the_bot_did_not_take_in_time_is_not_sent_later(client, product, wagon_viewer, bot_on):
    """Бот лежал дольше срока: доставка уже «Не отправлено» — поднявшись, бот её не шлёт."""
    order = _shipped(client, product, truck_number="12345678")
    old = send_wagon_report([order], "старый", wagon_viewer, key="key-00000011").deliveries.get()
    fresh = send_wagon_report([order], "свежий", wagon_viewer, key="key-00000012").deliveries.get()
    ReportDelivery.objects.filter(pk=old.pk).update(created_at=timezone.now() - REPORT_QUEUE_TIMEOUT - timedelta(seconds=1))
    old.refresh_from_db()
    assert delivery_state(old) == (ReportDelivery.FAILED, "бот не отправил за 10 минут")
    assert delivery_state(fresh) == (ReportDelivery.QUEUED, "")
    api = FakeTelegram()

    assert send_pending_reports(api) == 1

    assert [text for _, text, _ in api.sent] == ["свежий"]
    old.refresh_from_db()
    assert (old.status, old.error) == (ReportDelivery.FAILED, "бот не отправил за 10 минут")


def test_bot_loop_sends_queued_reports(client, product, wagon_viewer, bot_on):
    order = _shipped(client, product, truck_number="12345678")
    send_wagon_report([order], "отчёт", wagon_viewer, key="key-00000006")
    api = FakeTelegram()

    assert BotRunner(client_factory=lambda: api).poll_once() == RUNNING

    assert api.sent == [(DINARA_CHAT, "отчёт", "")]
    assert ReportDelivery.objects.get().status == ReportDelivery.SENT


def test_long_report_goes_in_parts(client, product, wagon_viewer, bot_on):
    text = "\n".join(f"Д1с-{index:08d}-68 тн" for index in range(400))
    order = _shipped(client, product, truck_number="12345678")
    send_wagon_report([order], text, wagon_viewer, key="key-00000013")
    api = FakeTelegram()

    assert send_pending_reports(api) == 1

    assert len(api.sent) > 1
    assert "\n".join(part for _, part, _ in api.sent) == text
    assert ReportDelivery.objects.get().provider_message_id == f"{DINARA_CHAT}:1001"


def test_failure_after_a_sent_part_is_not_repeated(client, product, wagon_viewer, bot_on):
    text = "\n".join(f"Д1с-{index:08d}-68 тн" for index in range(400))
    order = _shipped(client, product, truck_number="12345678")
    send_wagon_report([order], text, wagon_viewer, key="key-00000014")

    class FailsSecond(FakeTelegram):
        def send_message(self, chat_id, text, *, reply_to=""):
            if self.sent:
                raise TelegramRefused("Telegram sendMessage: HTTP 429")
            return super().send_message(chat_id, text, reply_to=reply_to)

    with pytest.raises(TelegramOutcomeUnknown):
        send_pending_reports(FailsSecond())

    delivery = ReportDelivery.objects.get()
    assert delivery.status == ReportDelivery.UNKNOWN
    assert delivery.error.startswith("ушла часть отчёта (1 из")


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
