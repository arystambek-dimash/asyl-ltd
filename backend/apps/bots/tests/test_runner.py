"""Процесс бота: простой без флага, выключатель в журнале, круг опроса, сбои провайдера, heartbeat."""
import json
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

import whatsapp_bot_healthcheck as healthcheck
from apps.bots.models import BotMessage, WhatsAppBotSettings
from apps.bots.runner import DEGRADED, DISABLED, RUNNING, BotRunner
from apps.bots.tests.samples import OWNER_REPORT
from apps.bots.tests.whatsapp_fakes import GROUP, JIN, FakeGreenApi, incoming, webhook_body
from apps.orders.models import Order

pytestmark = pytest.mark.django_db


@pytest.fixture
def enabled(settings):
    settings.WHATSAPP_BOT_ENABLED = True
    row = WhatsAppBotSettings.load()
    row.enabled = True
    row.allowed_chat_ids = [GROUP]
    row.allowed_sender_ids = [JIN]
    row.save()
    return row


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _runner(api, clock=None):
    return BotRunner(client_factory=lambda: api, clock=clock or Clock())


def test_without_the_flag_the_process_idles_and_never_calls_the_provider(settings):
    settings.WHATSAPP_BOT_ENABLED = False

    def factory():
        raise AssertionError("провайдер не нужен")

    assert BotRunner(client_factory=factory).poll_once() == DISABLED
    assert WhatsAppBotSettings.objects.count() == 0


def test_switched_off_in_the_journal_keeps_the_queue(enabled):
    enabled.enabled = False
    enabled.save()
    api = FakeGreenApi(webhook_body(incoming(OWNER_REPORT)))

    assert _runner(api).poll_once() == DISABLED

    assert (len(api.queue), api.deleted, BotMessage.objects.count()) == (1, [], 0)
    row = WhatsAppBotSettings.load()
    assert (row.runtime_status, row.instance_state) == ("disabled", "authorized")


def test_one_round_stores_acknowledges_conducts_and_answers(enabled, client, product, price):
    api = FakeGreenApi(webhook_body(incoming(OWNER_REPORT)))

    assert _runner(api).poll_once() == RUNNING

    message = BotMessage.objects.get()
    assert (message.status, message.order.status) == ("applied", "shipped")
    assert api.deleted == [1]
    assert api.sent == [(GROUP, message.reply, "MSG1")]
    row = WhatsAppBotSettings.load()
    assert (row.runtime_status, row.runtime_error, row.polled_at is not None) == ("running", "", True)


def test_first_start_keeps_the_groups_reports_while_the_admin_picks_the_group(enabled, client, product, price):
    # Порядок настройки: включить бота → группа появляется в «Недавно писали
    # боту» → выбрать её. Отчёты до этого не теряются — они в «Пропущено».
    enabled.allowed_chat_ids = []
    enabled.save()
    api = FakeGreenApi(webhook_body(incoming(OWNER_REPORT)))

    assert _runner(api).poll_once() == RUNNING

    message = BotMessage.objects.get()
    assert (message.status, message.issues[0]["code"]) == ("ignored", "chat_not_allowed")
    assert (api.deleted, api.sent, Order.objects.count()) == ([1], [], 0)
    assert GROUP in WhatsAppBotSettings.load().seen_chats


def test_redelivered_notification_is_acknowledged_without_a_second_order(enabled, client, product, price):
    body = webhook_body(incoming(OWNER_REPORT))
    runner = _runner(FakeGreenApi(body))
    runner.poll_once()

    api = FakeGreenApi(body)
    runner._client = api
    runner.poll_once()

    assert api.deleted == [1]
    assert (Order.objects.count(), BotMessage.objects.count()) == (1, 1)


def test_provider_outage_is_degraded_and_nothing_is_lost(enabled):
    api = FakeGreenApi(webhook_body(incoming(OWNER_REPORT)), fail_receive=True)

    assert _runner(api).poll_once() == DEGRADED

    assert BotMessage.objects.count() == 0
    row = WhatsAppBotSettings.load()
    assert row.runtime_status == "degraded"
    assert "receiveNotification" in row.runtime_error


def test_notification_is_acknowledged_only_after_it_is_stored(enabled):
    api = FakeGreenApi(webhook_body(incoming(OWNER_REPORT)))

    with patch("apps.bots.runner.ingest", side_effect=RuntimeError("db")), pytest.raises(RuntimeError):
        _runner(api).poll_once()

    assert api.deleted == []


def test_unauthorized_number_is_degraded(enabled):
    api = FakeGreenApi(state="notAuthorized")

    assert _runner(api).poll_once() == DEGRADED

    assert "notAuthorized" in WhatsAppBotSettings.load().runtime_error


def test_state_is_checked_once_a_minute(enabled):
    api, clock = FakeGreenApi(), Clock()
    runner = _runner(api, clock)

    runner.poll_once()
    clock.now += 30
    runner.poll_once()
    clock.now += 31
    runner.poll_once()

    assert api.state_calls == 2


def test_state_change_notification_updates_the_journal(enabled):
    api = FakeGreenApi({"typeWebhook": "stateInstanceChanged", "stateInstance": "blocked"})

    assert _runner(api).poll_once() == DEGRADED

    assert WhatsAppBotSettings.load().instance_state == "blocked"
    assert api.deleted == [1]


def test_missing_credentials_are_degraded_not_a_crash(enabled, settings):
    settings.WHATSAPP_BOT_INSTANCE_ID = ""
    settings.WHATSAPP_BOT_API_TOKEN = ""

    assert BotRunner().poll_once() == DEGRADED

    assert "WHATSAPP_BOT_INSTANCE_ID" in WhatsAppBotSettings.load().runtime_error


# --- команда и healthcheck ------------------------------------------------------------------------


def test_command_once_writes_a_healthy_disabled_heartbeat(settings, tmp_path, monkeypatch):
    heartbeat = tmp_path / "whatsapp-bot" / "heartbeat.json"
    settings.WHATSAPP_BOT_ENABLED = False
    settings.WHATSAPP_BOT_HEARTBEAT_FILE = str(heartbeat)

    call_command("run_whatsapp_bot", "--once", stdout=StringIO())

    assert json.loads(heartbeat.read_text(encoding="utf-8"))["status"] == "disabled"
    monkeypatch.setenv("WHATSAPP_BOT_HEARTBEAT_FILE", str(heartbeat))
    assert healthcheck.main() == 0


# Команда закрывает соединения между кругами, как остальные мониторы.
@pytest.mark.django_db(transaction=True)
def test_command_once_runs_a_round(enabled, settings, tmp_path, client, product, price):
    heartbeat = tmp_path / "heartbeat.json"
    settings.WHATSAPP_BOT_HEARTBEAT_FILE = str(heartbeat)
    api = FakeGreenApi(webhook_body(incoming(OWNER_REPORT)))

    with patch("apps.bots.management.commands.run_whatsapp_bot.BotRunner", lambda: _runner(api)):
        call_command("run_whatsapp_bot", "--once", stdout=StringIO())

    assert json.loads(heartbeat.read_text(encoding="utf-8"))["status"] == "running"
    assert BotMessage.objects.get().status == "applied"


def test_healthcheck_fails_a_stale_or_missing_heartbeat(tmp_path, monkeypatch):
    heartbeat = tmp_path / "heartbeat.json"
    monkeypatch.setenv("WHATSAPP_BOT_HEARTBEAT_FILE", str(heartbeat))
    assert healthcheck.main() == 1

    heartbeat.write_text(json.dumps({"status": "running", "updated_at": 1}), encoding="utf-8")
    assert healthcheck.main() == 1

    heartbeat.write_text(json.dumps({"status": "stopped", "updated_at": 9e9}), encoding="utf-8")
    assert healthcheck.main() == 1
