"""Процесс бота: простой без флага, выключатель в журнале, круг опроса, команды, сбои Telegram, heartbeat."""
import json
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

import telegram_bot_healthcheck as healthcheck
from apps.bots.models import BotChat, BotMessage, TelegramBotSettings
from apps.bots.runner import DEGRADED, DISABLED, RUNNING, UNAUTHORIZED_ERROR, BotRunner
from apps.bots.tests.samples import OWNER_REPORT
from apps.bots.tests.telegram_fakes import BOT_USERNAME, GROUP, FakeTelegram, update
from apps.orders.models import Order

pytestmark = pytest.mark.django_db


@pytest.fixture
def enabled(settings, bot_settings):
    settings.TELEGRAM_BOT_ENABLED = True
    return bot_settings


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _runner(api, clock=None):
    return BotRunner(client_factory=lambda: api, clock=clock or Clock())


def test_without_the_flag_the_process_idles_and_never_calls_telegram(settings):
    settings.TELEGRAM_BOT_ENABLED = False

    def factory():
        raise AssertionError("Telegram не нужен")

    assert BotRunner(client_factory=factory).poll_once() == DISABLED


def test_switched_off_in_the_journal_keeps_the_queue(enabled):
    enabled.enabled = False
    enabled.save()
    api = FakeTelegram(update(OWNER_REPORT))

    assert _runner(api).poll_once() == DISABLED

    assert (api.offsets, BotMessage.objects.count()) == ([], 0)
    row = TelegramBotSettings.load()
    assert (row.runtime_status, row.bot_state, row.bot_username) == ("disabled", "authorized", BOT_USERNAME)


def test_one_round_stores_conducts_and_answers(enabled, client, product, price):
    api = FakeTelegram(update(OWNER_REPORT))

    assert _runner(api).poll_once() == RUNNING

    message = BotMessage.objects.get()
    assert (message.status, message.order.status, message.sender_username) == ("applied", "shipped", "jin_sin")
    assert api.sent == [(GROUP, message.reply, f"{GROUP}:11")]
    row = TelegramBotSettings.load()
    assert (row.runtime_status, row.runtime_error, row.polled_at is not None) == ("running", "", True)


def test_updates_are_confirmed_by_the_next_poll(enabled, client, product, price):
    api = FakeTelegram(update(OWNER_REPORT, update_id=41))
    runner = _runner(api)

    runner.poll_once()
    runner.poll_once()

    assert api.offsets == [None, 42]
    assert api.queue == []


def test_report_from_someone_not_allowed_waits_in_the_journal(enabled, client, product, price):
    api = FakeTelegram(update(OWNER_REPORT, username="stranger"))

    assert _runner(api).poll_once() == RUNNING

    # Что сообщение «Пропущено» и его можно провести из журнала — test_messages; здесь — круг опроса.
    assert BotMessage.objects.get().status == "ignored"
    assert (api.sent, Order.objects.count()) == ([], 0)


def test_redelivered_update_is_stored_once(enabled, client, product, price):
    body = update(OWNER_REPORT)
    runner = _runner(FakeTelegram(body))
    runner.poll_once()

    runner._client = FakeTelegram(body)
    runner._offset = None
    runner.poll_once()

    assert (Order.objects.count(), BotMessage.objects.count()) == (1, 1)


def test_update_is_confirmed_only_after_it_is_stored(enabled):
    api = FakeTelegram(update(OWNER_REPORT, update_id=7))
    runner = _runner(api)

    with patch("apps.bots.runner.ingest", side_effect=RuntimeError("db")), pytest.raises(RuntimeError):
        runner.poll_once()

    assert runner._offset is None


def test_telegram_outage_is_degraded_and_nothing_is_lost(enabled):
    api = FakeTelegram(update(OWNER_REPORT), fail_receive=True)

    assert _runner(api).poll_once() == DEGRADED

    assert BotMessage.objects.count() == 0
    row = TelegramBotSettings.load()
    assert row.runtime_status == "degraded"
    assert "getUpdates" in row.runtime_error


def test_rejected_token_is_degraded(enabled):
    api = FakeTelegram(unauthorized=True)

    assert _runner(api).poll_once() == DEGRADED

    row = TelegramBotSettings.load()
    assert (row.bot_state, row.runtime_error) == ("unauthorized", UNAUTHORIZED_ERROR)


def test_token_is_checked_once_a_minute(enabled):
    api, clock = FakeTelegram(), Clock()
    runner = _runner(api, clock)

    runner.poll_once()
    clock.now += 30
    runner.poll_once()
    clock.now += 31
    runner.poll_once()

    assert api.me_calls == 2


def test_missing_token_is_degraded_not_a_crash(enabled, settings):
    settings.TELEGRAM_BOT_TOKEN = ""

    assert BotRunner().poll_once() == DEGRADED

    assert "TELEGRAM_BOT_TOKEN" in TelegramBotSettings.load().runtime_error


# --- команды ---------------------------------------------------------------------------------------


def test_start_answers_right_away_and_is_not_a_journal_message(enabled):
    api = FakeTelegram(update("/start", chat_id=501, chat_type="private"))

    _runner(api).poll_once()

    assert BotMessage.objects.count() == 0
    [(chat_id, text, reply_to)] = api.sent
    assert (chat_id, reply_to) == ("501", "501:11")
    assert "вы допущены к боту" in text
    assert BotChat.objects.get(chat_id="501").username == "jin_sin"


def test_start_tells_a_stranger_whom_to_ask(enabled):
    api = FakeTelegram(update("/start", chat_id=9, chat_type="private", username="Stranger"))

    _runner(api).poll_once()

    [(_, text, _)] = api.sent
    assert "нет доступа" in text and "@stranger" in text


def test_command_for_another_bot_in_the_group_is_a_plain_message(enabled):
    api = FakeTelegram(update("/start@other_bot"))

    _runner(api).poll_once()

    assert api.sent == []
    assert BotMessage.objects.get().status == "ignored"


def test_command_reply_failure_does_not_block_the_queue(enabled):
    api = FakeTelegram(update("/start", update_id=5), fail_send=True)
    runner = _runner(api)

    assert runner.poll_once() == RUNNING

    assert runner._offset == 6


# --- команда и healthcheck ------------------------------------------------------------------------


def test_command_once_writes_a_healthy_disabled_heartbeat(settings, tmp_path, monkeypatch):
    heartbeat = tmp_path / "telegram-bot" / "heartbeat.json"
    settings.TELEGRAM_BOT_ENABLED = False
    settings.TELEGRAM_BOT_HEARTBEAT_FILE = str(heartbeat)

    call_command("run_telegram_bot", "--once", stdout=StringIO())

    assert json.loads(heartbeat.read_text(encoding="utf-8"))["status"] == "disabled"
    monkeypatch.setenv("TELEGRAM_BOT_HEARTBEAT_FILE", str(heartbeat))
    assert healthcheck.main() == 0


# Команда закрывает соединения между кругами, как остальные мониторы.
@pytest.mark.django_db(transaction=True)
def test_command_once_runs_a_round(enabled, settings, tmp_path, client, product, price):
    heartbeat = tmp_path / "heartbeat.json"
    settings.TELEGRAM_BOT_HEARTBEAT_FILE = str(heartbeat)
    api = FakeTelegram(update(OWNER_REPORT))

    with patch("apps.bots.management.commands.run_telegram_bot.BotRunner", lambda: _runner(api)):
        call_command("run_telegram_bot", "--once", stdout=StringIO())

    assert json.loads(heartbeat.read_text(encoding="utf-8"))["status"] == "running"
    assert BotMessage.objects.get().status == "applied"
