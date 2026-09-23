from django.db import migrations

from apps.common.migration_ops import db_on_delete


class Migration(migrations.Migration):
    """Откат образа: старый код удаляет клиента и сотрудника, не зная о профилях бота."""

    dependencies = [
        ("bots", "0001_initial"),
    ]

    operations = [
        db_on_delete("bots", "BotClientProfile", "client", "CASCADE"),
        db_on_delete("bots", "BotClientProfile", "created_by", "SET NULL"),
    ]
