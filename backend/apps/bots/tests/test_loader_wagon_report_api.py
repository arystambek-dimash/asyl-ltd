"""«Отправить отчёт» в истории грузчика (вкладка «Вагоны»): составить, отправить, отметка в истории."""
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.bots.models import OutgoingMessage, ReportDelivery, TelegramBotSettings
from apps.bots.tests.samples import train_order
from apps.bots.tests.telegram_fakes import DINARA, DINARA_CHAT
from apps.bots.wagon_report import REPORT_QUEUE_TIMEOUT
from apps.clients.models import Client
from apps.eventlog.models import EventLog
from apps.orders.backdate import backdate_moment
from apps.orders.models import Order
from apps.sales.models import Department

pytestmark = pytest.mark.django_db

COMPOSE = "/api/loader/wagon-report/compose/"
SEND = "/api/loader/wagon-report/send/"
HISTORY = "/api/loader/history/"
SETTINGS = "/api/bots/telegram/settings/"


def _shipped(client, product, *, truck_number="12345678", bags=8160, day=None, hour=12, wagons=(), station=""):
    """Вагонный заказ, отгруженный кнопкой (без вагонов) или по отчёту (с вагонами)."""
    at = backdate_moment(day or timezone.localdate()).replace(hour=hour)
    return train_order(
        client, product, bags=bags, shipped_at=at, wagons=wagons, truck_number=truck_number, rail_station=station)


def _send(api, orders, *, text="отчёт", key="key-00000001"):
    return api.post(SEND, {"order_ids": [order.pk for order in orders], "text": text, "key": key}, format="json")


@pytest.fixture
def api(auth_client, wagon_viewer):
    return auth_client(wagon_viewer)


def test_compose_one_order_shipped_by_the_button(api, client, product):
    """№366: вагонный заказ, отгруженный кнопкой, — отчёт из номера заказа и мешков."""
    order = _shipped(client, product)
    day = timezone.localdate()

    response = api.get(COMPOSE, {"order": order.pk})

    assert response.status_code == 200, response.data
    header = " ".join(["пн вт ср чт пт сб вс".split()[day.weekday()], f"{day:%d.%m.%y}",
                       "Узбекистан ООО OSIYO NAV NIHOL"])
    text = f"{header}\nСт. 1 вагон\nД1с-12345678-408 тн"
    assert response.data["text"] == text
    assert response.data["order_ids"] == [order.pk]
    assert (response.data["recipients"], response.data["can_send"], response.data["reason"]) == ([], False, "bot_off")


def test_compose_tells_who_gets_the_report(api, client, product, bot_on):
    order = _shipped(client, product)

    data = api.get(COMPOSE, {"order": order.pk}).data

    assert data["recipients"] == [{"username": DINARA, "name": "Динара", "ready": True}]
    assert (data["can_send"], data["reason"]) == (True, "")


def test_compose_the_history_filter_groups_the_period(api, client, product):
    today = timezone.localdate()
    first = _shipped(client, product, truck_number="28087658", hour=9)
    second = _shipped(client, product, truck_number="28087666", hour=10)
    report = _shipped(client, product, bags=2720, wagons=("28087674", "28087682"), station="Раустан", hour=11)
    _shipped(client, product, truck_number="28087690", day=today - timedelta(days=2))
    truck = _shipped(client, product, truck_number="")
    Order.objects.filter(pk=truck.pk).update(transport_type="truck")

    data = api.get(COMPOSE).data

    assert data["order_ids"] == [first.pk, second.pk, report.pk]
    blocks = data["text"].split("\n\n")
    assert [block.splitlines()[1] for block in blocks] == ["Ст. 2 вагон", "Ст. Раустан 2 вагон"]
    assert blocks[0].splitlines()[2:] == ["Д1с-28087658-408 тн", "Д1с-28087666-408 тн"]

    two_days_ago = (today - timedelta(days=2)).isoformat()
    assert len(api.get(COMPOSE, {"date_from": two_days_ago, "date_to": today.isoformat()}).data["order_ids"]) == 4
    assert api.get(COMPOSE, {"search": "28087682"}).data["order_ids"] == [report.pk]


def test_compose_for_an_empty_period_is_empty(api):
    assert api.get(COMPOSE).data["text"] == ""


def test_compose_reads_dictionaries_once_for_the_period(api, client, product, django_assert_max_num_queries):
    _shipped(client, product, hour=8)
    with CaptureQueriesContext(connection) as small:
        assert len(api.get(COMPOSE).data["order_ids"]) == 1
    for hour in range(9, 15):
        _shipped(client, product, hour=hour)
    _shipped(client, product, bags=2720, wagons=("28087674", "28087682"), hour=16)

    with django_assert_max_num_queries(len(small.captured_queries)):
        assert len(api.get(COMPOSE).data["order_ids"]) == 8


def test_trucks_only_loader_cannot_compose_or_send(auth_client, user_with_perms, client, product):
    trucks = auth_client(user_with_perms("trucks", codes=["loader.view", "loader.confirm", "loader.trucks"]))
    order = _shipped(client, product)

    assert trucks.get(COMPOSE).status_code == 403
    assert trucks.get(COMPOSE, {"order": order.pk}).status_code == 403
    assert _send(trucks, [order]).status_code == 403
    assert not OutgoingMessage.objects.exists()


def test_other_department_orders_are_not_reported(auth_client, user_with_perms, client, product):
    retail = Department.objects.create(code="retail", name="Розница")
    loader = user_with_perms("retail-loader", codes=["loader.view", "loader.wagons"], department=retail)
    api = auth_client(loader)
    order = _shipped(client, product)

    assert api.get(COMPOSE, {"order": order.pk}).status_code == 404
    assert api.get(COMPOSE).data["order_ids"] == []
    assert _send(api, [order]).status_code == 404


def test_compose_one_order_must_be_a_shipped_wagon_order(api, client, product):
    pending = Order.objects.create(client=client, currency="USD", transport_type="train", status="confirmed")

    assert api.get(COMPOSE, {"order": pending.pk}).status_code == 404
    assert api.get(COMPOSE, {"order": "abc"}).status_code == 400


def test_send_queues_the_report_and_marks_the_history_rows(api, client, product, wagon_viewer, bot_on):
    orders = [_shipped(client, product, hour=9), _shipped(client, product, truck_number="28087658", hour=10)]

    first = _send(api, orders, text="сб 19.09.26 Узбекистан ООО OSIYO\nСт. 1 вагон\nД1с-12345678-408 тн",
                  key="press-0001")
    again = _send(api, orders, key="press-0001")

    assert (first.status_code, again.status_code) == (200, 200), first.data
    data = first.data
    assert (data["order_ids"], data["sent_at"] is not None) == ([o.pk for o in orders], True)
    assert data["deliveries"] == [{"to": f"@{DINARA}", "status": "queued", "status_label": "В очереди", "error": ""}]
    assert (OutgoingMessage.objects.count(), ReportDelivery.objects.get().chat_id) == (1, DINARA_CHAT)
    rows = {row["id"]: row for row in api.get(HISTORY, {"transport": "train"}).data}
    for order in orders:
        row = rows[order.pk]
        assert row["report_deliveries"] == data["deliveries"]
        assert row["report_sent_at"] is not None
    assert EventLog.objects.filter(event_type="rail_report", user=wagon_viewer).count() == 2


def test_send_when_the_bot_is_off_is_refused_inside_the_screen(api, client, product):
    order = _shipped(client, product)

    response = _send(api, [order])

    assert response.status_code == 400
    assert response.data["code"] == "report_bot_off"
    assert "не работает" in response.data["detail"]


def test_send_validates_its_input(api, client, product, bot_on):
    order = _shipped(client, product)
    unshipped = Order.objects.create(client=client, currency="USD", transport_type="train", status="confirmed")

    assert _send(api, [order], text=" ").status_code == 400
    assert _send(api, [order], key="short").status_code == 400
    assert api.post(SEND, {"order_ids": [], "text": "x", "key": "key-00000001"}, format="json").status_code == 400
    assert _send(api, [order, unshipped]).status_code == 404
    assert not OutgoingMessage.objects.exists()


def test_history_rows_show_each_recipients_error(api, client, product, bot_on):
    order = _shipped(client, product)
    _send(api, [order])
    ReportDelivery.objects.update(
        status=ReportDelivery.FAILED, error="Telegram sendMessage: HTTP 403 — Forbidden: bot was blocked by the user")

    row = api.get(HISTORY, {"transport": "train"}).data[0]

    assert row["report_deliveries"] == [{
        "to": f"@{DINARA}", "status": "failed", "status_label": "Не отправлено",
        "error": "Telegram sendMessage: HTTP 403 — Forbidden: bot was blocked by the user",
    }]


def test_bot_that_stopped_polling_is_not_offered(api, client, product, bot_on):
    """Флаги бота включены, но его процесс давно не отмечал круги — отправить нельзя, а не в вечную очередь."""
    order = _shipped(client, product)
    TelegramBotSettings.objects.update(polled_at=timezone.now() - timedelta(hours=1))

    draft = api.get(COMPOSE, {"order": order.pk}).data

    assert (draft["can_send"], draft["reason"]) == (False, "bot_off")
    assert _send(api, [order]).data["code"] == "report_bot_off"


def test_history_shows_a_delivery_the_bot_never_took_as_not_sent(api, client, product, bot_on):
    """Бот так и не взял доставку из очереди — «Не отправлено»: грузчик отправит его ещё раз."""
    order = _shipped(client, product)
    _send(api, [order])
    ReportDelivery.objects.update(created_at=timezone.now() - REPORT_QUEUE_TIMEOUT - timedelta(seconds=1))

    row = api.get(HISTORY, {"transport": "train"}).data[0]

    assert [(item["status"], item["error"]) for item in row["report_deliveries"]] == [
        ("failed", "бот не отправил за 10 минут")]


def test_history_reads_report_marks_without_n_plus_one(api, client, product, bot_on, django_assert_max_num_queries):
    # Вчерашние отгрузки: свежая (≤ часа) отгрузка спрашивает журнал для «Отменить».
    yesterday = timezone.localdate() - timedelta(days=1)
    params = {"transport": "train", "date_from": yesterday.isoformat()}
    first = _shipped(client, product, day=yesterday)
    _send(api, [first])
    with CaptureQueriesContext(connection) as small:
        assert len(api.get(HISTORY, params).data) == 1
    # Следующий запрос очищает журнал запросов соединения — считаем сразу.
    budget = len(small.captured_queries)
    orders = [_shipped(client, product, day=yesterday, hour=hour) for hour in range(8, 12)]
    _send(api, orders, key="key-00000002")

    with django_assert_max_num_queries(budget):
        rows = api.get(HISTORY, params).data

    assert len(rows) == 5 and all(row["report_deliveries"][0]["status"] == "queued" for row in rows)


# --- настройки получателя ------------------------------------------------------------------------


@pytest.fixture
def admin_api(auth_client, user_with_perms):
    return auth_client(user_with_perms("bot-admin", codes=["bots.view", "sys_permissions.manage"]))


def test_recipients_are_edited_in_the_bot_settings(admin_api, api, client, product, dinara_started):
    response = admin_api.put(SETTINGS, {"report_recipients": ["@Dinara_K", "d1maaash", "dinara_k"]}, format="json")

    assert response.status_code == 200, response.data
    assert response.data["settings"]["report_recipients"] == [DINARA, "d1maaash"]
    assert response.data["settings"]["report_recipient_chats"] == [
        {"username": DINARA, "name": "Динара", "ready": True},
        {"username": "d1maaash", "name": "", "ready": False},
    ]
    order = _shipped(client, product)
    assert [item["username"] for item in api.get(COMPOSE, {"order": order.pk}).data["recipients"]] == [
        DINARA, "d1maaash"]


@pytest.mark.parametrize("value", ["+7 701 123 45 67", "@ab"])
def test_recipients_are_validated(admin_api, value):
    response = admin_api.put(SETTINGS, {"report_recipients": [value]}, format="json")

    assert response.status_code == 400
    assert "report_recipients" in response.data["detail"]
    assert TelegramBotSettings.load().report_recipients == []


def test_recipients_can_be_cleared(admin_api):
    row = TelegramBotSettings.load()
    row.report_recipients = [DINARA]
    row.save()

    response = admin_api.put(SETTINGS, {"report_recipients": []}, format="json")

    assert response.status_code == 200
    assert TelegramBotSettings.load().report_recipients == []


def test_loader_cannot_change_the_recipients(api):
    response = api.put(SETTINGS, {"report_recipients": [DINARA]}, format="json")

    assert response.status_code == 403


def test_country_outside_the_header_list_is_omitted(api, department, product):
    """Страна не из списка шапки опускается, клиент — по карточке."""
    far = Client.objects.create_with_user(
        first_name="Ли", phone="+86 138 0000 0000", company_name="LI TRADING", currency="USD",
        department=department, country="Вьетнам")
    order = _shipped(far, product)

    header = api.get(COMPOSE, {"order": order.pk}).data["text"].splitlines()[0]

    assert header.endswith(" LI TRADING") and "Вьетнам" not in header
