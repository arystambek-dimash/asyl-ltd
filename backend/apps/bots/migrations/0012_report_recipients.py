"""«Отправить отчёт» — нескольким получателям (решение владельца, 29.09).

Настройки: вместо одного получателя (имя + username) — список username.
Отправка: нажатие (OutgoingMessage) и доставка каждому получателю
(ReportDelivery); прежние отправки переносятся строкой доставки. Поля
получателя и статуса у OutgoingMessage и настройки одного получателя
остаются в базе для отката образа — из состояния Django уходят (с DB
default, чтобы новые строки вставлялись без них), физически — позже.
"""

import django.db.models.deletion
from django.db import migrations, models


def forwards(apps, schema_editor):
    database = schema_editor.connection.alias
    Settings = apps.get_model("bots", "TelegramBotSettings")
    OutgoingMessage = apps.get_model("bots", "OutgoingMessage")
    ReportDelivery = apps.get_model("bots", "ReportDelivery")
    for row in Settings.objects.using(database).all():
        row.report_recipients = [row.report_recipient_username] if row.report_recipient_username else []
        row.save(update_fields=["report_recipients"])
    for message in OutgoingMessage.objects.using(database).order_by("pk").iterator():
        delivery = ReportDelivery.objects.using(database).create(
            message=message,
            username=message.username,
            name=message.recipient_name,
            chat_id=message.chat_id,
            status=message.status,
            provider_message_id=message.provider_message_id,
            attempts=message.attempts,
            error=message.error,
            sent_at=message.sent_at,
        )
        ReportDelivery.objects.using(database).filter(pk=delivery.pk).update(
            created_at=message.created_at, updated_at=message.updated_at)


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0011_telegram_bot_db_on_delete"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReportDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("username", models.CharField(blank=True, default="", max_length=64)),
                ("name", models.CharField(blank=True, default="", max_length=200)),
                ("chat_id", models.CharField(blank=True, default="", max_length=128)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "В очереди"),
                            ("sending", "Отправляется"),
                            ("sent", "Отправлено"),
                            ("failed", "Не отправлено"),
                            ("unknown", "Не подтверждено"),
                            ("link", "Ссылкой"),
                        ],
                        max_length=10,
                    ),
                ),
                ("provider_message_id", models.CharField(blank=True, default="", max_length=160)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("error", models.CharField(blank=True, default="", max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="deliveries",
                        to="bots.outgoingmessage",
                    ),
                ),
            ],
            options={
                "ordering": ["pk"],
                "indexes": [models.Index(fields=["status", "id"], name="bots_delivery_status_idx")],
            },
        ),
        migrations.AddField(
            model_name="telegrambotsettings",
            name="report_recipients",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        # Колонки остаются для отката, но новые строки вставляются без них.
        migrations.AlterField(
            model_name="telegrambotsettings",
            name="report_recipient_name",
            field=models.CharField(db_default="Динара", default="Динара", max_length=60),
        ),
        migrations.AlterField(
            model_name="telegrambotsettings",
            name="report_recipient_username",
            field=models.CharField(blank=True, db_default="", default="", max_length=64),
        ),
        migrations.AlterField(
            model_name="outgoingmessage",
            name="recipient_name",
            field=models.CharField(db_default="", default="", max_length=60),
        ),
        migrations.AlterField(
            model_name="outgoingmessage",
            name="chat_id",
            field=models.CharField(blank=True, db_default="", default="", max_length=128),
        ),
        migrations.AlterField(
            model_name="outgoingmessage",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "В очереди"),
                    ("sending", "Отправляется"),
                    ("sent", "Отправлено"),
                    ("failed", "Не отправлено"),
                    ("unknown", "Не подтверждено"),
                    ("link", "Ссылкой"),
                ],
                # Откат образа: старый код не видит доставок и покажет такие
                # отчёты «Не подтверждено», а не «Отправлено»; его бот их не шлёт.
                db_default="unknown",
                default="unknown",
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="outgoingmessage",
            name="provider_message_id",
            field=models.CharField(blank=True, db_default="", default="", max_length=160),
        ),
        migrations.AlterField(
            model_name="outgoingmessage",
            name="attempts",
            field=models.PositiveSmallIntegerField(db_default=0, default=0),
        ),
        migrations.AlterField(
            model_name="outgoingmessage",
            name="error",
            field=models.CharField(blank=True, db_default="", default="", max_length=500),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveIndex(model_name="outgoingmessage", name="bots_outgoing_status_idx"),
                migrations.RemoveField(model_name="outgoingmessage", name="recipient_name"),
                migrations.RemoveField(model_name="outgoingmessage", name="username"),
                migrations.RemoveField(model_name="outgoingmessage", name="chat_id"),
                migrations.RemoveField(model_name="outgoingmessage", name="status"),
                migrations.RemoveField(model_name="outgoingmessage", name="provider_message_id"),
                migrations.RemoveField(model_name="outgoingmessage", name="attempts"),
                migrations.RemoveField(model_name="outgoingmessage", name="error"),
                migrations.RemoveField(model_name="outgoingmessage", name="sent_at"),
                migrations.RemoveField(model_name="telegrambotsettings", name="report_recipient_name"),
                migrations.RemoveField(model_name="telegrambotsettings", name="report_recipient_username"),
            ],
        ),
    ]
