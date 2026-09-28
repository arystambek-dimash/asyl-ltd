"""bots.0012: один получатель → список, прежние отправки → строки доставки."""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = [("bots", "0011_telegram_bot_db_on_delete")]
AFTER = [("bots", "0012_report_recipients")]


class ReportRecipientsMigrationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE)
        old = executor.loader.project_state(BEFORE).apps
        settings_row = old.get_model("bots", "TelegramBotSettings").objects.first() or old.get_model(
            "bots", "TelegramBotSettings").objects.create()
        settings_row.report_recipient_username = "dinara_k"
        settings_row.save()
        message = old.get_model("bots", "OutgoingMessage").objects.create(
            key="press-00000001", recipient_name="Динара", username="dinara_k", chat_id="777",
            text="отчёт", order_ids=[5], status="sent", provider_message_id="777:9", attempts=1)
        self.message_id, self.created_at = message.pk, message.created_at

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(AFTER)
        self.apps = executor.loader.project_state(AFTER).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_single_recipient_becomes_the_list_and_old_sends_keep_their_status(self):
        assert self.apps.get_model("bots", "TelegramBotSettings").objects.get().report_recipients == ["dinara_k"]
        delivery = self.apps.get_model("bots", "ReportDelivery").objects.get()
        assert (delivery.message_id, delivery.username, delivery.name, delivery.chat_id, delivery.status) == (
            self.message_id, "dinara_k", "Динара", "777", "sent")
        assert (delivery.provider_message_id, delivery.attempts, delivery.created_at) == (
            "777:9", 1, self.created_at)

    def test_new_sends_insert_without_the_retired_columns(self):
        message = self.apps.get_model("bots", "OutgoingMessage").objects.create(key="press-00000002", text="x")
        with connection.cursor() as cursor:
            cursor.execute("SELECT status, recipient_name FROM bots_outgoingmessage WHERE id = %s", [message.pk])
            # Откатанный образ увидит «Не подтверждено», а не ложное «Отправлено».
            assert cursor.fetchone() == ("unknown", "")
