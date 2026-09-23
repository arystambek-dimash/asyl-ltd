from django.db import migrations

from apps.common.migration_ops import db_on_delete


class Migration(migrations.Migration):
    """Откат образа: старый код удаляет заказ и сотрудника, не зная о сообщениях бота."""

    dependencies = [
        ("bots", "0003_whatsapp_bot"),
    ]

    operations = [
        db_on_delete("bots", "BotMessage", "order", "SET NULL"),
        db_on_delete("bots", "BotMessage", "original", "SET NULL"),
        db_on_delete("bots", "BotMessage", "resolved_by", "SET NULL"),
        db_on_delete("bots", "WhatsAppBotSettings", "updated_by", "SET NULL"),
    ]
