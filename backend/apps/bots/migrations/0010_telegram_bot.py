"""WhatsApp-бот → Telegram-бот (решение владельца, 28.09).

Настройки — новая строка TelegramBotSettings: пороги отчёта и имя
получателя переносятся из настроек WhatsApp-бота, пользоваться ботом сразу
может администратор @d1maaash, а бот проводит отчёты, как только на сервере
заданы TELEGRAM_BOT_ENABLED=1 и токен. Таблица настроек WhatsApp-бота и
колонка номера у отправленных отчётов остаются в базе для отката образа
(из состояния Django уходят; физически — отдельной миграцией позже).
Сервисный пользователь бота переименовывается: заказы, проведённые
WhatsApp-ботом, остаются за ним же.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import apps.bots.models

OLD_BOT_USERNAME = "whatsapp-bot"
NEW_BOT_USERNAME = "telegram-bot"


def seed_telegram_settings(apps, schema_editor):
    database = schema_editor.connection.alias
    Old = apps.get_model("bots", "WhatsAppBotSettings")
    New = apps.get_model("bots", "TelegramBotSettings")
    old = Old.objects.using(database).filter(singleton=True).first()
    defaults = {"enabled": True, "allowed_usernames": ["d1maaash"]}
    if old is not None:
        defaults.update(
            show_amounts_in_reply=old.show_amounts_in_reply,
            duplicate_window_days=old.duplicate_window_days,
            price_tolerance_pct=old.price_tolerance_pct,
            report_recipient_name=old.report_recipient_name,
        )
    New.objects.using(database).get_or_create(singleton=True, defaults=defaults)


def rename_bot_user(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    users = User.objects.using(schema_editor.connection.alias)
    if users.filter(username=NEW_BOT_USERNAME).exists():
        return
    users.filter(username=OLD_BOT_USERNAME).update(username=NEW_BOT_USERNAME, first_name="Telegram-бот")


def restore_bot_user(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    users = User.objects.using(schema_editor.connection.alias)
    if users.filter(username=OLD_BOT_USERNAME).exists():
        return
    users.filter(username=NEW_BOT_USERNAME).update(username=OLD_BOT_USERNAME, first_name="WhatsApp-бот")


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0009_botmessage_drop_parsed_status"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TelegramBotSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("singleton", models.BooleanField(default=True, editable=False, unique=True)),
                ("enabled", models.BooleanField(default=False)),
                ("allowed_usernames", models.JSONField(blank=True, default=apps.bots.models.default_allowed_usernames)),
                ("show_amounts_in_reply", models.BooleanField(default=False)),
                ("duplicate_window_days", models.PositiveSmallIntegerField(default=3)),
                ("price_tolerance_pct", models.DecimalField(decimal_places=2, default=15, max_digits=5)),
                ("report_recipient_name", models.CharField(default="Динара", max_length=60)),
                ("report_recipient_username", models.CharField(blank=True, default="", max_length=64)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("runtime_status", models.CharField(blank=True, default="", max_length=20)),
                ("runtime_error", models.CharField(blank=True, default="", max_length=300)),
                ("polled_at", models.DateTimeField(blank=True, null=True)),
                ("bot_state", models.CharField(blank=True, default="", max_length=40)),
                ("bot_state_at", models.DateTimeField(blank=True, null=True)),
                ("bot_username", models.CharField(blank=True, default="", max_length=64)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="BotChat",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("chat_id", models.CharField(max_length=32, unique=True)),
                ("chat_type", models.CharField(max_length=20)),
                ("title", models.CharField(blank=True, default="", max_length=200)),
                ("username", models.CharField(blank=True, default="", max_length=64)),
                ("last_message_at", models.DateTimeField()),
            ],
            options={
                "ordering": ["-last_message_at", "-pk"],
                "indexes": [models.Index(fields=["username"], name="bots_chat_username_idx")],
            },
        ),
        migrations.AddField(
            model_name="botmessage",
            name="sender_username",
            field=models.CharField(blank=True, db_default="", default="", max_length=64),
        ),
        migrations.AlterField(
            model_name="botmessage",
            name="provider",
            field=models.CharField(default="telegram", max_length=20),
        ),
        migrations.AddField(
            model_name="outgoingmessage",
            name="username",
            field=models.CharField(blank=True, db_default="", default="", max_length=64),
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
                max_length=10,
            ),
        ),
        # Номер WhatsApp не пишется: колонка остаётся для отката, но новые
        # строки должны вставляться без неё.
        migrations.AlterField(
            model_name="outgoingmessage",
            name="phone",
            field=models.CharField(blank=True, db_default="", default="", max_length=15),
        ),
        migrations.RunPython(seed_telegram_settings, migrations.RunPython.noop),
        migrations.RunPython(rename_bot_user, restore_bot_user),
        # Expand/contract, как tasks 0003: таблица настроек WhatsApp-бота
        # остаётся для образа автоотката, но без внешнего ключа — ни удаление
        # сотрудника, ни очистка таблиц не должны упираться в её ссылку.
        migrations.AlterField(
            model_name="whatsappbotsettings",
            name="updated_by",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(model_name="outgoingmessage", name="phone"),
                migrations.DeleteModel(name="WhatsAppBotSettings"),
            ],
        ),
    ]
