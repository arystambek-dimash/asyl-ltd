from django.db import migrations

from apps.common.migration_ops import db_on_delete


class Migration(migrations.Migration):
    """Откат образа: старый код удаляет сотрудника, не зная о настройках Telegram-бота."""

    dependencies = [
        ("bots", "0010_telegram_bot"),
    ]

    operations = [
        db_on_delete("bots", "TelegramBotSettings", "updated_by", "SET NULL"),
    ]
