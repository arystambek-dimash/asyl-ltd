from django.db import migrations

from apps.common.migration_ops import db_on_delete


class Migration(migrations.Migration):
    """Откат образа: старый код удаляет сотрудника, не зная об отправленных отчётах."""

    dependencies = [
        ("bots", "0006_outgoing_report"),
    ]

    operations = [
        db_on_delete("bots", "OutgoingMessage", "created_by", "SET NULL"),
    ]
