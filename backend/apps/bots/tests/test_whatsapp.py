"""WhatsApp-бот: приём без дублей, автопроведение, разбор, правки, ответы цитатой, журнал."""
from decimal import Decimal
from unittest.mock import patch

import pytest
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.bots import llm, whatsapp
from apps.bots.models import BotMessage, WhatsAppBotSettings
from apps.bots.parsing import parse_rail_report
from apps.bots.providers.green_api import GreenApiError
from apps.bots.service_user import ensure_bot_user
from apps.bots.tests.samples import OWNER_BAGS, OWNER_REPORT, OWNER_WAGONS, report
from apps.bots.tests.whatsapp_fakes import GROUP, JIN, FakeGreenApi, deleted, edited, incoming
from apps.catalog.models import ClientPrice, ProductAlias
from apps.eventlog.models import EventLog
from apps.orders.models import Order
from apps.shipments.models import ShipmentWagon
from apps.warehouse.models import StockItem

pytestmark = pytest.mark.django_db


@pytest.fixture
def bot_settings():
    row = WhatsAppBotSettings.load()
    row.enabled = True
    row.allowed_chat_ids = [GROUP]
    row.allowed_sender_ids = [JIN]
    row.save()
    return row


@pytest.fixture
def bot_user():
    return ensure_bot_user()


def _receive(bot_settings, bot_user, message):
    stored = whatsapp.ingest(message, bot_settings)
    whatsapp.process_pending(user=bot_user, bot_settings=bot_settings)
    if stored is not None:
        stored.refresh_from_db()
    return stored


def _stock(product):
    return sum(StockItem.objects.filter(product=product).values_list("bags", flat=True))


# --- приём ------------------------------------------------------------------------------------------


def test_redelivered_notification_is_stored_once(bot_settings):
    first = whatsapp.ingest(incoming(OWNER_REPORT), bot_settings)
    again = whatsapp.ingest(incoming(OWNER_REPORT), bot_settings)

    assert first.pk == again.pk
    assert BotMessage.objects.count() == 1
    assert (first.chat_id, first.sender_id, first.status, first.text) == (GROUP, JIN, "received", OWNER_REPORT)


def test_foreign_chat_is_not_stored_but_listed_for_settings(bot_settings):
    assert whatsapp.ingest(incoming("личное", chat_id="77011234567@c.us"), bot_settings) is None

    assert BotMessage.objects.count() == 0
    seen = WhatsAppBotSettings.load().seen_chats
    assert seen["77011234567@c.us"]["name"] == "Отгрузка вагонов"


def test_group_not_yet_allowed_is_kept_as_skipped_and_can_be_conducted_later(
    bot_settings, bot_user, client, product, price, conductor,
):
    # Группу ещё не выбрали в настройках: отчёт не теряется, но и не проводится.
    bot_settings.allowed_chat_ids = []
    bot_settings.save()

    message = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))

    assert (message.status, message.reply, Order.objects.count()) == ("ignored", "", 0)
    assert message.issues[0]["code"] == "chat_not_allowed"
    assert GROUP in WhatsAppBotSettings.load().seen_chats
    assert whatsapp.apply_message(message, conductor).status == "applied"


# --- автопроведение ------------------------------------------------------------------------------------


def test_owner_report_is_conducted_by_the_bot_and_answered_with_a_quote(
    bot_settings, bot_user, client, product, price,
):
    stock = _stock(product)

    message = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))

    assert message.status == "applied", message.issues
    order = message.order
    assert (order.status, order.transport_type, order.created_by, order.rail_station) == (
        "shipped", "train", bot_user, "Раустан")
    assert ShipmentWagon.objects.filter(shipment__order=order, source_message=message).count() == 12
    assert _stock(product) == stock - OWNER_BAGS
    assert message.reply == (
        f"Проведено: заказ №{order.pk}, ООО OSIYO NAV NIHOL, ст. Раустан, 12 вагонов, 816 т "
        f"(16 320 мешков {product})"
    )
    assert message.parsed["wagons"] == 12 and message.parsed["bags"] == OWNER_BAGS
    # Суммы в ответе — только по настройке.
    assert "сумма" not in message.reply


def test_amounts_in_reply_only_when_enabled(bot_settings, bot_user, client, product, price):
    bot_settings.show_amounts_in_reply = True
    bot_settings.save()

    message = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))

    assert message.reply.endswith("; сумма 122 400 USD")


def test_redelivery_after_conducting_changes_nothing(bot_settings, bot_user, client, product, price):
    _receive(bot_settings, bot_user, incoming(OWNER_REPORT))

    _receive(bot_settings, bot_user, incoming(OWNER_REPORT))

    assert Order.objects.count() == 1
    assert BotMessage.objects.get().status == "applied"


def test_same_report_in_a_new_message_goes_to_review(bot_settings, bot_user, client, product, price):
    first = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))

    second = _receive(bot_settings, bot_user, incoming(OWNER_REPORT, message_id="MSG2"))

    assert Order.objects.count() == 1
    assert second.status == "needs_review"
    assert {issue["code"] for issue in second.issues} >= {"wagon_already_shipped"}
    assert second.reply.startswith(
        f"Принято, на проверке: Вагон {OWNER_WAGONS[0]} уже отгружен в заказе №{first.order_id}")
    assert second.reply.endswith("и ещё 9 причин")


def test_unresolved_report_waits_for_a_human_and_touches_nothing(bot_settings, bot_user, client, product, price):
    ProductAlias.objects.all().delete()
    stock = _stock(product)

    message = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))

    assert message.status == "needs_review"
    assert [issue["code"] for issue in message.issues] == ["product_unknown"]
    assert message.reply == "Принято, на проверке: Неизвестный код товара «Д1с» — выберите товар"
    assert (Order.objects.count(), _stock(product), message.order) == (0, stock, None)


def test_bot_needs_its_own_rights(bot_settings, bot_user, client, product, price):
    bot_user.employee.permissions.filter(code="loader.wagons").delete()
    bot_user = type(bot_user).objects.get(pk=bot_user.pk)

    message = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))

    assert message.status == "needs_review"
    assert message.issues[0]["code"] == "not_applied"
    assert Order.objects.count() == 0
    # Внутренние ошибки проведения — в журнал, не в чат.
    assert message.reply == "Принято, на проверке: нужна проверка человеком"


STRANGER = "77019998877@c.us"


def test_unknown_sender_in_the_group_is_skipped(bot_settings, bot_user, client, product, price):
    message = _receive(bot_settings, bot_user, incoming(OWNER_REPORT, sender_id=STRANGER))

    assert (message.status, message.reply) == ("ignored", "")
    assert message.issues[0]["code"] == "sender_not_allowed"
    assert Order.objects.count() == 0


@pytest.mark.parametrize("text", ["Спасибо", "ок 👍", ""])
def test_chat_talk_is_not_a_report(bot_settings, bot_user, text):
    message = _receive(bot_settings, bot_user, incoming(text))

    assert (message.status, message.reply, message.issues) == ("ignored", "", [])


def test_too_long_message_is_rejected(bot_settings, bot_user):
    message = _receive(bot_settings, bot_user, incoming("x" * 9000))

    assert message.status == "rejected"
    assert message.issues[0]["code"] == "too_long"


def test_too_long_message_of_an_unknown_sender_is_skipped(bot_settings, bot_user):
    message = _receive(bot_settings, bot_user, incoming("x" * 9000, sender_id=STRANGER))

    assert (message.status, message.reply) == ("ignored", "")
    assert message.issues[0]["code"] == "sender_not_allowed"


# --- правки и удаления --------------------------------------------------------------------------------


def test_edit_of_a_conducted_report_goes_to_review_and_is_never_conducted(
    bot_settings, bot_user, client, product, price,
):
    original = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))
    fixed = report(*(f"Д1с-{number}-68 тн" for number in OWNER_WAGONS[:11]))

    revision = _receive(bot_settings, bot_user, edited(fixed))

    assert (revision.kind, revision.original, revision.status) == ("edited", original, "needs_review")
    assert revision.order_id == original.order_id
    assert revision.issues[0]["code"] == "message_edited"
    assert revision.reply == (
        f"Принято, на проверке: Сообщение изменено после отправки — по нему уже проведён заказ "
        f"№{original.order_id}, проверьте заказ"
    )
    assert Order.objects.count() == 1


def test_edit_of_an_unknown_sender_is_skipped_without_a_reply(bot_settings, bot_user, client, product, price):
    _receive(bot_settings, bot_user, incoming(OWNER_REPORT, sender_id=STRANGER))

    revision = _receive(bot_settings, bot_user, edited(OWNER_REPORT, sender_id=STRANGER))

    assert (revision.status, revision.reply) == ("ignored", "")
    assert revision.issues[0]["code"] == "sender_not_allowed"


def test_unknown_senders_change_of_a_conducted_report_reaches_a_human_silently(
    bot_settings, bot_user, client, product, price,
):
    original = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))

    revision = _receive(bot_settings, bot_user, edited(OWNER_REPORT, sender_id=STRANGER))

    assert (revision.status, revision.order_id, revision.reply) == ("needs_review", original.order_id, "")
    assert revision.issues[0]["code"] == "message_edited"


def test_delete_of_a_conducted_report_goes_to_review(bot_settings, bot_user, client, product, price):
    original = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))

    revision = _receive(bot_settings, bot_user, deleted())

    assert (revision.kind, revision.status, revision.reply) == ("deleted", "needs_review", "")
    assert revision.issues[0]["code"] == "message_deleted"
    assert revision.order_id == original.order_id


def test_delete_of_chat_talk_is_skipped(bot_settings, bot_user):
    _receive(bot_settings, bot_user, incoming("Спасибо"))

    revision = _receive(bot_settings, bot_user, deleted())

    assert revision.status == "ignored"


def test_repeated_edits_are_separate_and_redelivery_is_not(bot_settings, bot_user):
    _receive(bot_settings, bot_user, incoming("Спасибо"))
    first = whatsapp.ingest(edited("один", message_id="E1"), bot_settings)
    again = whatsapp.ingest(edited("один", message_id="E1"), bot_settings)
    other = whatsapp.ingest(edited("два", message_id="E2"), bot_settings)

    assert first.pk == again.pk != other.pk


# --- сбои и ИИ ---------------------------------------------------------------------------------------


def test_crash_is_retried_then_left_for_a_human(bot_settings, bot_user, client, product, price):
    with patch("apps.bots.whatsapp.apply_rail_report", side_effect=RuntimeError("boom")):
        message = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))
        assert (message.status, message.attempts, message.reply) == ("failed", 1, "")
        for _ in range(3):
            whatsapp.process_pending(user=bot_user, bot_settings=bot_settings)

    message.refresh_from_db()
    assert (message.status, message.attempts) == ("failed", whatsapp.MAX_ATTEMPTS)
    assert message.error == "RuntimeError: boom"
    assert message.reply == "Принято, на проверке: не удалось провести автоматически"
    assert Order.objects.count() == 0


FREE_TEXT = "Джин-Син: сегодня 19.09 ушли вагоны 28087658 и 28087666 по 68 тонн, мука Д1с, Раустан, OSIYO"


def test_llm_is_off_by_default(bot_settings, bot_user, client, product, price):
    with patch.object(llm, "_request") as request:
        message = _receive(bot_settings, bot_user, incoming(FREE_TEXT))

    request.assert_not_called()
    assert (message.status, message.draft) == ("needs_review", "")


def test_llm_is_not_asked_about_a_well_formed_report(bot_settings, bot_user, client, product, price, settings):
    settings.WHATSAPP_BOT_LLM_ENABLED = True
    settings.OPENAI_API_KEY = "unit-test-only"
    ProductAlias.objects.all().delete()

    with patch.object(llm, "_request") as request:
        message = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))

    request.assert_not_called()
    assert (message.status, message.draft) == ("needs_review", "")


def test_llm_draft_waits_for_confirmation_and_is_never_conducted(
    bot_settings, bot_user, client, product, price, settings,
):
    settings.WHATSAPP_BOT_LLM_ENABLED = True
    settings.OPENAI_API_KEY = "unit-test-only"
    answer = {
        "day": "19.09.2026", "country": "Узбекистан", "client_name": "ООО OSIYO NAV NIHOL", "station": "Раустан",
        "wagons": [{"code": "Д1с", "number": "28087658", "tons": "68"}, {"code": "Д1с", "number": "28087666",
                                                                        "tons": "68"}],
    }
    with patch.object(llm, "_request", return_value=answer) as request:
        message = _receive(bot_settings, bot_user, incoming(FREE_TEXT))

    request.assert_called_once_with(FREE_TEXT)
    assert message.status == "awaiting_confirmation"
    assert message.draft == (
        "сб 19.09.26 Узбекистан ООО OSIYO NAV NIHOL\nСт. Раустан 2 вагон\n"
        "Д1с-28087658-68 тн\nД1с-28087666-68 тн"
    )
    assert parse_rail_report(message.draft).ok
    assert message.issues[-1]["code"] == "llm_draft"
    assert "ИИ" not in message.reply
    assert Order.objects.count() == 0


def test_human_decision_keeps_the_llm_draft(bot_settings, bot_user, settings, conductor):
    settings.WHATSAPP_BOT_LLM_ENABLED = True
    settings.OPENAI_API_KEY = "unit-test-only"
    answer = {"day": "19.09.2026", "country": "", "client_name": "OSIYO", "station": "Раустан",
              "wagons": [{"code": "Д1с", "number": "28087658", "tons": "68"}]}
    with patch.object(llm, "_request", return_value=answer):
        message = _receive(bot_settings, bot_user, incoming(FREE_TEXT))

    ignored = whatsapp.ignore_message(message, conductor)

    assert (ignored.status, ignored.draft) == ("ignored", message.draft)
    assert ignored.draft


def test_llm_failure_leaves_plain_review(bot_settings, bot_user, settings):
    settings.WHATSAPP_BOT_LLM_ENABLED = True
    settings.OPENAI_API_KEY = "unit-test-only"
    with patch.object(llm, "_request", side_effect=OSError("down")):
        message = _receive(bot_settings, bot_user, incoming(FREE_TEXT))

    assert (message.status, message.draft) == ("needs_review", "")


# --- ответы -----------------------------------------------------------------------------------------


def _price_mismatch(bot_settings, bot_user, price):
    first = report(*(f"Д1с-{number}-68 тн" for number in OWNER_WAGONS[:6]))
    assert _receive(bot_settings, bot_user, incoming(first)).status == "applied"
    ClientPrice.objects.filter(pk=price.pk).update(price="10.00")
    second = report(*(f"Д1с-{number}-68 тн" for number in OWNER_WAGONS[6:]))
    return _receive(bot_settings, bot_user, incoming(second, message_id="MSG2"))


def test_price_mismatch_reply_has_no_prices(bot_settings, bot_user, client, product, price):
    message = _price_mismatch(bot_settings, bot_user, price)

    assert message.status == "needs_review"
    assert [issue["code"] for issue in message.issues] == ["price_mismatch"]
    # Журнал — с ценами, чат — без сумм (по умолчанию суммы в ответе выключены).
    assert "10.00 USD" in message.issues[0]["message"]
    assert message.reply == "Принято, на проверке: «Д1с»: цена отличается от прошлого вагонного заказа"


def test_price_mismatch_reply_shows_prices_only_when_amounts_are_on(bot_settings, bot_user, client, product, price):
    bot_settings.show_amounts_in_reply = True
    bot_settings.save()

    message = _price_mismatch(bot_settings, bot_user, price)

    assert message.reply == f"Принято, на проверке: {message.issues[0]['message']}"


def test_replies_are_sent_once_as_quotes(bot_settings, bot_user, client, product, price):
    original = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))
    revision = _receive(bot_settings, bot_user, edited("Спасибо, исправил"))
    api = FakeGreenApi()

    assert whatsapp.send_pending_replies(api) == 2
    assert whatsapp.send_pending_replies(api) == 0

    assert api.sent == [
        (GROUP, original.reply, "MSG1"),
        # Правку цитировать нельзя — цитата исходного сообщения.
        (GROUP, revision.reply, "MSG1"),
    ]
    original.refresh_from_db()
    assert (original.reply_message_id, original.reply_sent_at is not None) == ("REPLY1", True)


def test_failed_reply_counts_an_attempt_and_reports_the_outage(bot_settings, bot_user):
    message = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))

    with pytest.raises(GreenApiError):
        whatsapp.send_pending_replies(FakeGreenApi(fail_send=True))

    message.refresh_from_db()
    assert (message.reply_attempts, message.reply_sent_at) == (1, None)
    assert "HTTP 500" in message.error


def test_reply_replaced_while_it_was_sent_goes_out_next_round(
    bot_settings, bot_user, client, product, price, conductor,
):
    ProductAlias.objects.all().delete()
    message = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))
    ProductAlias.objects.create(code="Д1с", product=product)

    class ConductedWhileSending(FakeGreenApi):
        def send_message(self, chat_id, text, *, quoted_message_id=""):
            if not self.sent:
                whatsapp.apply_message(message, conductor)
            return super().send_message(chat_id, text, quoted_message_id=quoted_message_id)

    api = ConductedWhileSending()
    whatsapp.send_pending_replies(api)
    whatsapp.send_pending_replies(api)

    message.refresh_from_db()
    assert [text for _, text, _ in api.sent] == [
        "Принято, на проверке: Неизвестный код товара «Д1с» — выберите товар", message.reply]
    assert message.reply.startswith("Проведено:") and message.reply_message_id == "REPLY2"


# --- журнал: действия человека ------------------------------------------------------------------------


def test_human_conducts_a_reviewed_message_with_own_rights(
    bot_settings, bot_user, client, product, price, conductor,
):
    ProductAlias.objects.all().delete()
    message = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))
    ProductAlias.objects.create(code="Д1с", product=product)

    applied = whatsapp.apply_message(message, conductor)

    assert (applied.status, applied.resolved_by, applied.issues) == ("applied", conductor, [])
    assert applied.order.created_by == conductor
    assert applied.reply.startswith(f"Проведено: заказ №{applied.order_id}")
    assert applied.reply_sent_at is None
    assert EventLog.objects.filter(event_type="whatsapp_bot", order=applied.order, user=conductor).exists()


def test_human_can_conduct_a_corrected_text(bot_settings, bot_user, client, product, price, conductor):
    broken = OWNER_REPORT.replace("Ст. Раустан 12 вагон", "Раустан 12")
    message = _receive(bot_settings, bot_user, incoming(broken))
    assert message.status == "needs_review"

    applied = whatsapp.apply_message(message, conductor, text=OWNER_REPORT)

    assert applied.status == "applied"
    assert EventLog.objects.get(event_type="whatsapp_bot").payload["text"] == OWNER_REPORT


def test_human_without_rights_cannot_conduct(bot_settings, bot_user, client, product, price, user_with_perms):
    ProductAlias.objects.all().delete()
    message = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))
    ProductAlias.objects.create(code="Д1с", product=product)
    viewer = user_with_perms("bot-viewer", codes=["orders.create", "orders.confirm"])

    with pytest.raises(PermissionDenied):
        whatsapp.apply_message(message, viewer)

    message.refresh_from_db()
    assert (message.status, Order.objects.count()) == ("needs_review", 0)


def test_bot_retry_does_not_undo_a_human_decision(bot_settings, bot_user, client, product, price, conductor):
    with patch("apps.bots.whatsapp.apply_rail_report", side_effect=RuntimeError("boom")):
        message = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))
    assert (message.status, message.attempts) == ("failed", 1)
    # Бот взял сообщение на повтор, а человек тем временем его провёл.
    stale = whatsapp.pending_messages().get()
    applied = whatsapp.apply_message(message, conductor)

    whatsapp.process_message(stale, user=bot_user, bot_settings=bot_settings)

    message.refresh_from_db()
    assert (message.status, message.order_id, message.resolved_by) == ("applied", applied.order_id, conductor)
    assert message.reply == applied.reply
    assert Order.objects.count() == 1


def test_bot_does_not_conduct_a_message_a_human_ignored(bot_settings, bot_user, client, product, price, conductor):
    stale = whatsapp.ingest(incoming(OWNER_REPORT), bot_settings)
    whatsapp.ignore_message(stale, conductor)

    whatsapp.process_message(stale, user=bot_user, bot_settings=bot_settings)

    stale.refresh_from_db()
    assert (stale.status, stale.resolved_by, Order.objects.count()) == ("ignored", conductor, 0)


def test_human_conducted_message_links_its_wagons(bot_settings, bot_user, client, product, price, conductor):
    ProductAlias.objects.all().delete()
    message = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))
    ProductAlias.objects.create(code="Д1с", product=product)

    applied = whatsapp.apply_message(message, conductor)

    assert ShipmentWagon.objects.filter(shipment__order=applied.order, source_message=message).count() == 12


def test_conducted_message_cannot_be_conducted_or_ignored_again(
    bot_settings, bot_user, client, product, price, conductor,
):
    message = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))

    for action in (lambda: whatsapp.apply_message(message, conductor), lambda: whatsapp.ignore_message(message, conductor)):
        with pytest.raises(ValidationError) as raised:
            action()
        assert raised.value.detail["code"] == "bot_message_applied"


def test_ignore_keeps_the_reasons_and_who(bot_settings, bot_user, client, product, price, conductor):
    ProductAlias.objects.all().delete()
    message = _receive(bot_settings, bot_user, incoming(OWNER_REPORT))

    ignored = whatsapp.ignore_message(message, conductor)

    assert (ignored.status, ignored.resolved_by) == ("ignored", conductor)
    assert ignored.issues[0]["code"] == "product_unknown"


def test_status_counts(bot_settings, bot_user, client, product, price):
    _receive(bot_settings, bot_user, incoming(OWNER_REPORT))
    _receive(bot_settings, bot_user, incoming(OWNER_REPORT, message_id="MSG2"))
    _receive(bot_settings, bot_user, incoming("Спасибо", message_id="MSG3"))

    assert whatsapp.status_counts() == {"review": 1, "applied": 1, "ignored": 1, "all": 3}


def test_price_tolerance_comes_from_settings(bot_settings, bot_user, client, product, price):
    assert whatsapp.report_options(bot_settings) == {"duplicate_window_days": 3, "price_tolerance_pct": Decimal("15")}
