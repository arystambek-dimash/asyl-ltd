"""«Отправить отчёт» из истории грузчика: текст владельца, кому и как отправить, очередь бота."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.bots.models import BotClientProfile, OutgoingMessage, WhatsAppBotSettings
from apps.bots.parsing import parse_rail_report
from apps.bots.providers.green_api import GreenApiError, GreenApiOutcomeUnknown
from apps.bots.runner import RUNNING, BotRunner
from apps.bots.tests.whatsapp_fakes import GROUP, FakeGreenApi, bot_alive
from apps.bots.wagon_report import (
    BOT,
    LINK,
    MAX_SEND_ATTEMPTS,
    REPORT_QUEUE_TIMEOUT,
    SENDING_TIMEOUT,
    compose_rail_report,
    message_state,
    recipient_to,
    report_recipient,
    send_pending_reports,
    send_wagon_report,
    whatsapp_link,
)
from apps.catalog.models import Product, ProductAlias
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem
from apps.shipments.models import Shipment, ShipmentWagon

pytestmark = pytest.mark.django_db

DAY = date(2026, 9, 24)  # четверг
DINARA = "77011234567"


def _at(day=DAY, hour=12, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _shipped(client, product, *, bags=1360, truck_number="", station="Раустан", at=None, wagons=(), items=None):
    """Отгруженный вагонный заказ: кнопкой «Отгружено» (без вагонов) или по отчёту (с вагонами)."""
    order = Order.objects.create(
        client=client, currency="USD", department="export", transport_type="train", status="shipped",
        truck_number=truck_number, rail_station=station)
    for item_product, quantity in items or [(product, bags)]:
        OrderItem.objects.create(order=order, product=item_product, quantity=quantity, unit_price="7.50")
    shipment = Shipment.objects.create(order=order, bags_loaded=bags, shipped_at=at or _at())
    for position, number in enumerate(wagons, start=1):
        ShipmentWagon.objects.create(
            shipment=shipment, number=number, product=product, bags=1360, weight_kg="68000", position=position)
    return order


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


@pytest.fixture
def bot_on(settings):
    settings.WHATSAPP_BOT_ENABLED = True
    row = WhatsAppBotSettings.load()
    row.enabled = True
    row.allowed_chat_ids = [GROUP]
    row.report_recipient_phone = DINARA
    row.seen_chats = {GROUP: {"name": "Отгрузка вагонов", "at": "2026-09-24T07:00:00+05:00"}}
    bot_alive(row).save()
    return row


@pytest.fixture
def wagon_loader(user_with_perms):
    return user_with_perms("wagon-loader", codes=["loader.view", "loader.wagons"])


# --- текст отчёта -------------------------------------------------------------------------------


def test_order_shipped_by_the_button_reports_its_wagon_number_and_tonnes(client, product):
    """№366: вагонный заказ отгружен кнопкой — вагонов отгрузки нет, есть номер заказа и мешки."""
    order = _shipped(client, product, bags=8160, truck_number="12345678", station="")

    assert _text(order).splitlines() == [
        "чт 24.09.26 Узбекистан ООО OSIYO NAV NIHOL",
        "Ст. 1 вагон",
        "Д1с-12345678-408 тн",
    ]


def test_order_without_a_number_or_a_code_says_so(client, boss):
    no_code = Product.objects.create(name="ДБН 1с", color="Red", weight_kg="50")
    half = Product.objects.create(name="Отруби", color="Blue", weight_kg="25")
    ProductAlias.objects.create(code="ОТР", product=half)
    order = _shipped(client, no_code, items=[(no_code, 1360), (half, 30)])

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


def test_without_the_bot_the_report_goes_by_a_whatsapp_link_to_dinara():
    recipient = report_recipient()

    assert (recipient.name, recipient.to, recipient.phone, recipient.delivery) == ("Динара", "Динаре", "", LINK)
    assert whatsapp_link("", "сб 19.09\nСт. 1 вагон") == "https://wa.me/?text=%D1%81%D0%B1%2019.09%0A%D0%A1%D1%82.%201%20%D0%B2%D0%B0%D0%B3%D0%BE%D0%BD"
    assert whatsapp_link(DINARA, "a b") == f"https://wa.me/{DINARA}?text=a%20b"


def test_enabled_bot_sends_to_the_phone_and_without_it_to_the_allowed_group(bot_on, settings):
    assert (report_recipient().delivery, report_recipient().chat_id) == (BOT, f"{DINARA}@c.us")

    bot_on.report_recipient_phone = ""
    bot_on.save()
    recipient = report_recipient()
    assert (recipient.delivery, recipient.chat_id, recipient.chat_name) == (BOT, GROUP, "Отгрузка вагонов")

    bot_on.allowed_chat_ids = []
    bot_on.save()
    assert report_recipient().delivery == LINK


def test_bot_switched_off_on_the_server_or_in_the_journal_means_a_link(bot_on, settings):
    settings.WHATSAPP_BOT_ENABLED = False
    assert report_recipient().delivery == LINK

    settings.WHATSAPP_BOT_ENABLED = True
    bot_on.enabled = False
    bot_on.save()
    recipient = report_recipient()
    assert (recipient.delivery, recipient.phone) == (LINK, DINARA)


def test_bot_is_offered_only_while_its_process_is_alive(bot_on, settings):
    """Флаги включены, но бот не работает — ссылкой: иначе отчёт вечно ждал бы в очереди."""
    assert report_recipient().delivery == BOT
    max_age = timedelta(seconds=settings.WHATSAPP_BOT_HEARTBEAT_MAX_AGE_SECONDS)

    # Контейнер бота остановлен или простаивает (WHATSAPP_BOT_ENABLED=0 только у него): круги не отмечаются.
    bot_alive(bot_on, polled_at=timezone.now() - max_age - timedelta(seconds=1)).save()
    assert report_recipient().delivery == LINK
    bot_on.polled_at = None
    bot_on.save()
    assert report_recipient().delivery == LINK

    # Нет ключей Green-API или провайдер недоступен — бот пишет «degraded».
    bot_alive(bot_on).save()
    bot_on.runtime_status = "degraded"
    bot_on.save()
    assert report_recipient().delivery == LINK

    # Номер не авторизован в Green-API.
    bot_alive(bot_on).save()
    bot_on.instance_state = "notAuthorized"
    bot_on.save()
    recipient = report_recipient()
    assert (recipient.delivery, recipient.phone) == (LINK, DINARA)


# --- отправка -----------------------------------------------------------------------------------


def test_link_sending_marks_shipments_and_logs_an_event_per_order(client, product, wagon_loader):
    orders = [_shipped(client, product, truck_number="12345678"), _shipped(client, product, truck_number="28087658")]

    message = send_wagon_report(orders, "отчёт", wagon_loader, delivery=LINK, key="key-00000001")

    assert (message.status, message.recipient_name, message.chat_id, message.text) == (
        OutgoingMessage.LINK, "Динара", "", "отчёт")
    assert message.order_ids == [order.pk for order in orders]
    for shipment in Shipment.objects.filter(order__in=orders):
        assert (shipment.report_sent_at, shipment.report_sent_by, shipment.report_message) == (
            message.created_at, wagon_loader, message)
    events = EventLog.objects.filter(event_type="rail_report").order_by("order_id")
    assert [(event.order_id, event.message) for event in events] == [
        (order.pk, "Отчёт о вагонах отправлен Динаре через WhatsApp") for order in orders
    ]
    assert events[0].payload["message_id"] == message.pk


def test_bot_sending_is_queued_once_per_press(client, product, wagon_loader, bot_on):
    order = _shipped(client, product, truck_number="12345678")

    first = send_wagon_report([order], "отчёт", wagon_loader, delivery=BOT, key="key-00000002")
    again = send_wagon_report([order], "отчёт", wagon_loader, delivery=BOT, key="key-00000002")

    assert again.pk == first.pk
    assert (first.status, first.chat_id) == (OutgoingMessage.QUEUED, f"{DINARA}@c.us")
    assert OutgoingMessage.objects.count() == 1
    assert EventLog.objects.get(event_type="rail_report").message == "Отчёт о вагонах отправлен Динаре ботом WhatsApp"


def test_bot_delivery_is_refused_when_the_bot_went_off(client, product, wagon_loader):
    order = _shipped(client, product, truck_number="12345678")

    with pytest.raises(ValidationError) as caught:
        send_wagon_report([order], "отчёт", wagon_loader, delivery=BOT, key="key-00000003")

    assert caught.value.detail["code"] == "report_bot_unavailable"
    assert not OutgoingMessage.objects.exists()
    assert Shipment.objects.get(order=order).report_sent_at is None


def test_bot_sends_a_queued_report_once(client, product, wagon_loader, bot_on):
    order = _shipped(client, product, truck_number="12345678")
    message = send_wagon_report([order], "отчёт", wagon_loader, delivery=BOT, key="key-00000004")
    api = FakeGreenApi()

    assert send_pending_reports(api) == 1
    assert send_pending_reports(api) == 0

    assert api.sent == [(f"{DINARA}@c.us", "отчёт", "")]
    message.refresh_from_db()
    assert (message.status, message.provider_message_id, message.error) == (OutgoingMessage.SENT, "REPLY1", "")
    assert message.sent_at is not None


def test_provider_refusals_are_retried_and_then_visible(client, product, wagon_loader, bot_on):
    """Провайдер отказал (4xx) — сообщение точно не ушло: бот повторяет его на следующих кругах."""
    order = _shipped(client, product, truck_number="12345678")
    message = send_wagon_report([order], "отчёт", wagon_loader, delivery=BOT, key="key-00000005")
    api = FakeGreenApi(fail_send=GreenApiError("Green-API sendMessage: HTTP 429"))

    with pytest.raises(GreenApiError):
        send_pending_reports(api)
    message.refresh_from_db()
    assert (message.status, message.attempts, message.error) == (
        OutgoingMessage.QUEUED, 1, "Green-API sendMessage: HTTP 429")

    for _ in range(MAX_SEND_ATTEMPTS - 1):
        with pytest.raises(GreenApiError):
            send_pending_reports(api)
    message.refresh_from_db()
    assert (message.status, message.attempts) == (OutgoingMessage.FAILED, MAX_SEND_ATTEMPTS)
    assert send_pending_reports(FakeGreenApi()) == 0


class _WatchedGreenApi(FakeGreenApi):
    """Запоминает статус строки в момент отправки."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.statuses = []

    def send_message(self, chat_id, message, *, quoted_message_id=""):
        self.statuses.append(OutgoingMessage.objects.get(text=message).status)
        return super().send_message(chat_id, message, quoted_message_id=quoted_message_id)


def test_report_is_claimed_before_it_is_sent(client, product, wagon_loader, bot_on):
    order = _shipped(client, product, truck_number="12345678")
    send_wagon_report([order], "отчёт", wagon_loader, delivery=BOT, key="key-00000007")
    api = _WatchedGreenApi()

    assert send_pending_reports(api) == 1

    assert api.statuses == [OutgoingMessage.SENDING]
    assert OutgoingMessage.objects.get().status == OutgoingMessage.SENT


def test_unanswered_sending_is_not_repeated_and_asks_to_check_whatsapp(client, product, wagon_loader, bot_on):
    """Тайм-аут после того, как провайдер принял сообщение: повтор задвоил бы отчёт у Динары."""
    order = _shipped(client, product, truck_number="12345678")
    message = send_wagon_report([order], "отчёт", wagon_loader, delivery=BOT, key="key-00000008")
    api = FakeGreenApi(fail_send=GreenApiOutcomeUnknown("Green-API sendMessage: нет связи (TimeoutError)"))

    with pytest.raises(GreenApiError):
        send_pending_reports(api)

    message.refresh_from_db()
    assert (message.status, message.error) == (OutgoingMessage.UNKNOWN, "Green-API sendMessage: нет связи (TimeoutError)")
    assert message_state(message) == (OutgoingMessage.UNKNOWN, "Green-API sendMessage: нет связи (TimeoutError)")
    retry = FakeGreenApi()
    assert send_pending_reports(retry) == 0
    assert retry.sent == []


def test_sending_interrupted_by_a_crash_is_not_repeated(client, product, wagon_loader, bot_on):
    """Бот упал между отправкой и отметкой: строка «отправляется» — без повтора, человек проверит WhatsApp."""
    order = _shipped(client, product, truck_number="12345678")
    stuck = send_wagon_report([order], "застрял", wagon_loader, delivery=BOT, key="key-00000009")
    in_flight = send_wagon_report([order], "уходит", wagon_loader, delivery=BOT, key="key-00000010")
    now = timezone.now()
    OutgoingMessage.objects.filter(pk=stuck.pk).update(
        status=OutgoingMessage.SENDING, updated_at=now - SENDING_TIMEOUT - timedelta(seconds=1))
    # Другой процесс бота отправляет прямо сейчас — его строку не трогаем.
    OutgoingMessage.objects.filter(pk=in_flight.pk).update(status=OutgoingMessage.SENDING, updated_at=now)
    stuck.refresh_from_db()
    assert message_state(stuck) == (OutgoingMessage.UNKNOWN, "бот прервался во время отправки")
    api = FakeGreenApi()

    assert send_pending_reports(api) == 0

    assert api.sent == []
    stuck.refresh_from_db()
    in_flight.refresh_from_db()
    assert (stuck.status, stuck.error) == (OutgoingMessage.UNKNOWN, "бот прервался во время отправки")
    assert in_flight.status == OutgoingMessage.SENDING


def test_report_the_bot_did_not_take_in_time_is_not_sent_later(client, product, wagon_loader, bot_on):
    """Бот лежал дольше срока: отчёт уже «Не отправлено» (его отправили ссылкой) — поднявшись, бот его не шлёт."""
    order = _shipped(client, product, truck_number="12345678")
    old = send_wagon_report([order], "старый", wagon_loader, delivery=BOT, key="key-00000011")
    fresh = send_wagon_report([order], "свежий", wagon_loader, delivery=BOT, key="key-00000012")
    OutgoingMessage.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - REPORT_QUEUE_TIMEOUT - timedelta(seconds=1))
    old.refresh_from_db()
    assert message_state(old) == (OutgoingMessage.FAILED, "бот не отправил за 10 минут")
    assert message_state(fresh) == (OutgoingMessage.QUEUED, "")
    api = FakeGreenApi()

    assert send_pending_reports(api) == 1

    assert [text for _, text, _ in api.sent] == ["свежий"]
    old.refresh_from_db()
    assert (old.status, old.error) == (OutgoingMessage.FAILED, "бот не отправил за 10 минут")


def test_bot_loop_sends_queued_reports(client, product, wagon_loader, bot_on):
    order = _shipped(client, product, truck_number="12345678")
    send_wagon_report([order], "отчёт", wagon_loader, delivery=BOT, key="key-00000006")
    api = FakeGreenApi()

    assert BotRunner(client_factory=lambda: api).poll_once() == RUNNING

    assert api.sent == [(f"{DINARA}@c.us", "отчёт", "")]
    assert OutgoingMessage.objects.get().status == OutgoingMessage.SENT


def test_tonnes_keep_fractions(client, boss):
    sack = Product.objects.create(name="Мука 1с", color="Red", weight_kg="25")
    order = _shipped(client, sack, bags=2701, truck_number="12345678")

    assert _text(order).splitlines()[-1] == f"{sack}-12345678-67,525 тн"
    assert Decimal("67.525") == Decimal(2701) * Decimal("25") / 1000
