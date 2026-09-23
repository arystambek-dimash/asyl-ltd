"""Журнал WhatsApp-бота: права, вкладки, разбор и «Провести» от имени человека, настройки."""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.bots import whatsapp
from apps.bots.models import BotMessage, WhatsAppBotSettings
from apps.bots.service_user import ensure_bot_user
from apps.bots.tests.samples import CONDUCT_CODES, OWNER_BAGS, OWNER_DAY, OWNER_REPORT
from apps.bots.tests.whatsapp_fakes import GROUP, JIN, incoming
from apps.catalog.models import ProductAlias
from apps.eventlog.models import EventLog
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db

STATUS = "/api/bots/whatsapp/status/"
SETTINGS = "/api/bots/whatsapp/settings/"
MESSAGES = "/api/bots/whatsapp/messages/"


def _url(message, action=""):
    return f"{MESSAGES}{message.pk}/" + (f"{action}/" if action else "")


@pytest.fixture
def bot_settings():
    row = WhatsAppBotSettings.load()
    row.enabled = True
    row.allowed_chat_ids = [GROUP]
    row.allowed_sender_ids = [JIN]
    row.save()
    return row


@pytest.fixture
def receive(bot_settings):
    bot_user = ensure_bot_user()

    def _receive(message):
        stored = whatsapp.ingest(message, bot_settings)
        whatsapp.process_pending(user=bot_user, bot_settings=bot_settings)
        stored.refresh_from_db()
        return stored

    return _receive


@pytest.fixture
def reviewer(user_with_perms):
    """Динара: журнал бота и права проведения отчёта."""
    return user_with_perms("dinara", codes=[*CONDUCT_CODES, "bots.view", "bots.manage"])


@pytest.fixture
def viewer(user_with_perms):
    return user_with_perms("bot-viewer", codes=["bots.view"])


@pytest.fixture
def unknown_code_message(receive, client, product, price):
    ProductAlias.objects.all().delete()
    message = receive(incoming(OWNER_REPORT))
    assert message.status == "needs_review"
    return message


# --- права ----------------------------------------------------------------------------------------


def test_journal_needs_bots_view(auth_client, user_with_perms, bot_settings):
    stranger = auth_client(user_with_perms("stranger", codes=["orders.view"]))

    for url in (STATUS, MESSAGES):
        assert stranger.get(url).status_code == 403


def test_viewer_sees_but_cannot_conduct_or_ignore(auth_client, viewer, unknown_code_message):
    api = auth_client(viewer)

    assert api.get(MESSAGES).status_code == 200
    assert api.post(_url(unknown_code_message, "preview"), {"text": OWNER_REPORT}, format="json").status_code == 200
    for action in ("apply", "ignore"):
        assert api.post(_url(unknown_code_message, action), {"text": OWNER_REPORT}, format="json").status_code == 403


def test_status_shows_the_process_counts_and_settings(auth_client, viewer, bot_settings, settings):
    settings.WHATSAPP_BOT_ENABLED = True
    WhatsAppBotSettings.objects.update(instance_state="authorized", runtime_status="running")

    data = auth_client(viewer).get(STATUS).data

    assert (data["server_enabled"], data["runtime_status"], data["instance_state"]) == (True, "running", "authorized")
    assert data["counts"] == {"review": 0, "applied": 0, "ignored": 0, "all": 0}
    assert data["settings"]["allowed_chat_ids"] == [GROUP]
    assert (data["can_manage"], data["can_configure"]) == (False, False)


# --- вкладки --------------------------------------------------------------------------------------


def test_tabs_and_search(auth_client, reviewer, receive, client, product, price):
    applied = receive(incoming(OWNER_REPORT))
    review = receive(incoming(OWNER_REPORT, message_id="MSG2"))
    thanks = receive(incoming("Спасибо", message_id="MSG3"))
    api = auth_client(reviewer)

    def ids(query=""):
        return [row["id"] for row in api.get(f"{MESSAGES}{query}").data]

    assert ids() == [review.pk]
    assert ids("?status=applied") == [applied.pk]
    assert len(ids("?status=ignored")) == 1
    assert len(ids("?status=all")) == 3
    assert ids(f"?status=all&search={applied.order_id}") == [applied.pk]
    assert ids("?status=all&search=28087658") == [review.pk, applied.pk]
    assert ids("?status=all&search=Спасибо") == [thanks.pk]
    row = api.get(f"{MESSAGES}?status=applied").data[0]
    assert (row["order"], row["status"], row["sender_name"], row["parsed"]["wagons"]) == (
        applied.order_id, "applied", "Джин-Син", 12)


def test_list_is_paged_without_a_query_per_row(auth_client, reviewer, bot_settings, conductor):
    for index in range(3):
        message = whatsapp.ingest(incoming(f"Спасибо {index}", message_id=f"M{index}"), bot_settings)
        whatsapp.ignore_message(message, conductor)
    api = auth_client(reviewer)
    with CaptureQueriesContext(connection) as few:
        assert api.get(f"{MESSAGES}?status=all&page=1").status_code == 200
    for index in range(3, 12):
        message = whatsapp.ingest(incoming(f"Спасибо {index}", message_id=f"M{index}"), bot_settings)
        whatsapp.ignore_message(message, conductor)

    with CaptureQueriesContext(connection) as many:
        response = api.get(f"{MESSAGES}?status=all&page=1&page_size=50")

    assert response.data["count"] == 12
    assert len(many.captured_queries) == len(few.captured_queries)


# --- разбор и «Провести» ----------------------------------------------------------------------------


def test_reviewer_resolves_the_code_and_conducts_as_herself(auth_client, reviewer, unknown_code_message, product):
    api = auth_client(reviewer)
    body = {"text": unknown_code_message.text}

    preview = api.post(_url(unknown_code_message, "preview"), body, format="json").data
    assert (preview["ok"], preview["unresolved"]["products"]) == (False, ["Д1с"])
    assert any(option["id"] == product.pk for option in api.get(_url(unknown_code_message, "options")).data["products"])

    remembered = api.post(_url(unknown_code_message, "product-codes"),
                          {**body, "code": "Д1с", "product": product.pk}, format="json")
    assert remembered.status_code == 200, remembered.data
    assert (remembered.data["ok"], remembered.data["can_apply"]) == (True, True)

    response = api.post(_url(unknown_code_message, "apply"), body, format="json")

    assert response.status_code == 200, response.data
    assert (response.data["status"], response.data["resolved_by_name"]) == ("applied", "A B")
    order = Order.objects.get(pk=response.data["order"])
    assert (order.created_by, order.status) == (reviewer, "shipped")
    assert response.data["reply"].startswith(f"Проведено: заказ №{order.pk}")


def test_reviewer_without_conduct_rights_gets_403(auth_client, user_with_perms, unknown_code_message, product):
    ProductAlias.objects.create(code="Д1с", product=product)
    clerk = user_with_perms("bot-clerk", codes=["bots.view", "bots.manage"])

    response = auth_client(clerk).post(_url(unknown_code_message, "apply"), {"text": OWNER_REPORT}, format="json")

    assert response.status_code == 403
    assert Order.objects.count() == 0


def test_manual_order_is_shipped_by_the_message(auth_client, reviewer, receive, client, product, price):
    manual = Order.objects.create(
        client=client, currency="USD", department="export", transport_type="train", status="confirmed",
        arrival_date=OWNER_DAY)
    OrderItem.objects.create(order=manual, product=product, quantity=OWNER_BAGS, unit_price="7.40")
    message = receive(incoming(OWNER_REPORT))
    assert {issue["code"] for issue in message.issues} == {"manual_order_duplicate"}
    api = auth_client(reviewer)

    preview = api.post(_url(message, "preview"), {"text": OWNER_REPORT}, format="json").data
    assert preview["shippable_orders"] == [manual.pk]
    response = api.post(_url(message, "apply"), {"text": OWNER_REPORT, "order": manual.pk}, format="json")

    assert response.status_code == 200, response.data
    assert response.data["order"] == manual.pk
    manual.refresh_from_db()
    assert manual.status == "shipped"
    assert Order.objects.count() == 1


def test_ignored_message_can_still_be_conducted_later(auth_client, reviewer, unknown_code_message, product):
    api = auth_client(reviewer)

    response = api.post(_url(unknown_code_message, "ignore"))

    assert (response.status_code, response.data["status"], response.data["resolved_by_name"]) == (
        200, "ignored", "A B")
    # Пропустили по ошибке: пока код товара неизвестен — «на разбор», склад не тронут.
    refused = api.post(_url(unknown_code_message, "apply"), {"text": OWNER_REPORT}, format="json")
    assert (refused.status_code, refused.data["code"]) == (400, "rail_report_needs_review")
    assert BotMessage.objects.get().status == "ignored"
    ProductAlias.objects.create(code="Д1с", product=product)
    applied = api.post(_url(unknown_code_message, "apply"), {"text": OWNER_REPORT}, format="json")
    assert (applied.status_code, applied.data["status"]) == (200, "applied")


def test_conducted_message_cannot_be_conducted_again(auth_client, reviewer, receive, client, product, price):
    message = receive(incoming(OWNER_REPORT))

    response = auth_client(reviewer).post(_url(message, "apply"), {"text": OWNER_REPORT}, format="json")

    assert (response.status_code, response.data["code"]) == (400, "bot_message_applied")
    assert Order.objects.count() == 1


# --- настройки ------------------------------------------------------------------------------------


def test_settings_are_changed_only_by_an_administrator(auth_client, reviewer, boss, bot_settings):
    body = {"enabled": False}

    assert auth_client(reviewer).put(SETTINGS, body, format="json").status_code == 403

    WhatsAppBotSettings.objects.update(instance_state="authorized", runtime_status="running")
    response = auth_client(boss).put(SETTINGS, {
        "enabled": False,
        "allowed_chat_ids": [GROUP, GROUP],
        "allowed_sender_ids": ["+998 90 111 22 33", "77011234567@c.us"],
        "show_amounts_in_reply": True,
        "duplicate_window_days": 7,
        "price_tolerance_pct": "10",
    }, format="json")

    assert response.status_code == 200, response.data
    row = WhatsAppBotSettings.load()
    assert (row.enabled, row.allowed_chat_ids, row.show_amounts_in_reply, row.duplicate_window_days) == (
        False, [GROUP], True, 7)
    assert row.allowed_sender_ids == ["998901112233@c.us", "77011234567@c.us"]
    assert row.updated_by == boss
    # Состояние процесса пишет только бот — сохранение настроек его не трогает.
    assert (row.instance_state, row.runtime_status) == ("authorized", "running")
    assert response.data["settings"]["allowed_sender_ids"] == row.allowed_sender_ids
    assert EventLog.objects.filter(event_type="whatsapp_bot", user=boss).exists()


@pytest.mark.parametrize(("value", "stored"), [
    # Казахстанский номер как его набирают дома: 8 — выход на 7.
    ("8 701 123 45 67", "77011234567@c.us"),
    ("+7 701 123 45 67", "77011234567@c.us"),
    ("+998 90 111 22 33", "998901112233@c.us"),
    # С плюсом номер уже международный: +84 (Вьетнам) не трогаем.
    ("+84 912 345 678", "84912345678@c.us"),
    (GROUP, GROUP),
])
def test_sender_numbers_are_stored_as_whatsapp_sees_them(auth_client, boss, bot_settings, value, stored):
    response = auth_client(boss).put(SETTINGS, {"allowed_sender_ids": [value]}, format="json")

    assert response.status_code == 200, response.data
    assert WhatsAppBotSettings.load().allowed_sender_ids == [stored]


@pytest.mark.parametrize("value", ["not a chat", "123@example.com", "@g.us"])
def test_bad_chat_ids_are_refused(auth_client, boss, bot_settings, value):
    response = auth_client(boss).put(SETTINGS, {"allowed_chat_ids": [value]}, format="json")

    assert response.status_code == 400
    assert WhatsAppBotSettings.load().allowed_chat_ids == [GROUP]


def test_seen_chats_help_to_pick_the_group(auth_client, viewer, bot_settings):
    whatsapp.ingest(incoming("привет", chat_id="120363000000000001@g.us"), bot_settings)

    chats = auth_client(viewer).get(STATUS).data["settings"]["seen_chats"]

    assert {"id": "120363000000000001@g.us", "name": "Отгрузка вагонов"}.items() <= chats[0].items()
