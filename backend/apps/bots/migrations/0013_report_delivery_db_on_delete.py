from django.db import migrations

from apps.common.migration_ops import db_on_delete


class Migration(migrations.Migration):
    """Откат образа: старый код не знает о доставках отчёта и удалял бы отправку без них."""

    dependencies = [
        ("bots", "0012_report_recipients"),
    ]

    operations = [
        db_on_delete("bots", "ReportDelivery", "message", "CASCADE"),
    ]
