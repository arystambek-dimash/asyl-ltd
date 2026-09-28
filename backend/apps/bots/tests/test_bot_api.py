"""Журнал Telegram-бота: права, вкладки, разбор и «Провести» от имени человека, настройки."""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.bots import messages
from apps.bots.models import BotMessage, TelegramBotSettings
from apps.bots.tests.samples import (
    CONDUCT_CODES,
    OWNER_DAY,
    OWNER_REPORT,
    manual_train_order,
)
from apps.bots.tests.telegram_fakes import DINARA, JIN, incoming, private
from apps.catalog.models import ProductAlias
from apps.eventlog.models import EventLog
from apps.orders.models import Order

pytestmark = pytest.mark.django_db

STATUS = "/api/bots/telegram/status/"
SETTINGS = "/api/bots/telegram/settings/"
MESSAGES = "/api/bots/telegram/messages/"


def _url(message, action=""):
    return f"{MESSAGES}{message.pk}/" + (f"{action}/" if action else "")


@pytest.fixture
def reviewer(user_with_perms):
    """Динара: журнал бота и права проведения отчёта."""
    return user_with_perms("dinara", codes=[*CONDUCT_CODES, "bots.view", "bots.manage"])


@pytest.fixture
def viewer(user_with_perms):
    return user_with_perms("bot-viewer", codes=["bots.view"])


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
    settings.TELEGRAM_BOT_ENABLED = True
    TelegramBotSettings.objects.update(bot_state="authorized", bot_username="asyl_bot", runtime_status="running")

    data = auth_client(viewer).get(STATUS).data

    assert (data["server_enabled"], data["runtime_status"], data["bot_state"], data["bot_username"]) == (
        True, "running", "authorized", "asyl_bot")
    assert data["counts"] == {"review": 0, "applied": 0, "ignored": 0, "all": 0}
    assert data["settings"]["allowed_usernames"] == [JIN]
    assert (data["can_manage"], data["can_configure"]) == (False, False)


# --- вкладки --------------------------------------------------------------------------------------


def test_tabs_and_search(auth_client, reviewer, receive, client, product, price):
    applied = receive(incoming(OWNER_REPORT))
    review = receive(incoming(OWNER_REPORT, message_id="12"))
    thanks = receive(incoming("Спасибо", message_id="13"))
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
    assert len(ids("?status=all&search=@Jin_sin")) == 3
    row = api.get(f"{MESSAGES}?status=applied").data[0]
    assert (row["order"], row["status"], row["sender_name"], row["parsed"]["wagons"]) == (
        applied.order_id, "applied", "Джин-Син", 12)


def test_list_is_paged_without_a_query_per_row(auth_client, reviewer, bot_settings, conductor):
    for index in range(3):
        message = messages.ingest(incoming(f"Спасибо {index}", message_id=str(100 + index)))
        messages.ignore_message(message, conductor)
    api = auth_client(reviewer)
    with CaptureQueriesContext(connection) as few:
        assert api.get(f"{MESSAGES}?status=all&page=1").status_code == 200
    for index in range(3, 12):
        message = messages.ingest(incoming(f"Спасибо {index}", message_id=str(100 + index)))
        messages.ignore_message(message, conductor)

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
    manual = manual_train_order(client, product, arrival_date=OWNER_DAY)
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

    TelegramBotSettings.objects.update(bot_state="authorized", runtime_status="running")
    response = auth_client(boss).put(SETTINGS, {
        "enabled": False,
        "allowed_usernames": ["@D1maaash", "d1maaash", "https://t.me/jin_sin"],
        "show_amounts_in_reply": True,
        "duplicate_window_days": 7,
        "price_tolerance_pct": "10",
        "report_recipient_name": "  Динара  ",
        "report_recipient_username": "@Dinara_K",
    }, format="json")

    assert response.status_code == 200, response.data
    row = TelegramBotSettings.load()
    assert (row.enabled, row.allowed_usernames, row.show_amounts_in_reply, row.duplicate_window_days) == (
        False, ["d1maaash", "jin_sin"], True, 7)
    assert (row.report_recipient_name, row.report_recipient_username) == ("Динара", DINARA)
    assert row.updated_by == boss
    # Состояние процесса пишет только бот — сохранение настроек его не трогает.
    assert (row.bot_state, row.runtime_status) == ("authorized", "running")
    assert response.data["settings"]["allowed_usernames"] == row.allowed_usernames
    assert EventLog.objects.filter(event_type="telegram_bot", user=boss).exists()


@pytest.mark.parametrize("value", ["not a username", "@ab", "@1dinara", "dinara-k"])
def test_bad_usernames_are_refused(auth_client, boss, bot_settings, value):
    response = auth_client(boss).put(SETTINGS, {"allowed_usernames": [value]}, format="json")

    assert response.status_code == 400
    assert TelegramBotSettings.load().allowed_usernames == [JIN]


def test_recent_chats_and_whether_the_recipient_started_the_bot(auth_client, viewer, bot_settings):
    bot_settings.report_recipient_username = DINARA
    bot_settings.save()
    api = auth_client(viewer)
    assert api.get(STATUS).data["settings"]["report_recipient_started"] is False

    messages.ingest(private("/start"))

    data = api.get(STATUS).data["settings"]
    assert data["report_recipient_started"] is True
    assert {"type": "private", "username": DINARA}.items() <= data["recent_chats"][0].items()
