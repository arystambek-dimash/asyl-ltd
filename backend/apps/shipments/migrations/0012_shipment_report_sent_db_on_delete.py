from django.db import migrations

from apps.common.migration_ops import db_on_delete


class Migration(migrations.Migration):
    """Откат образа: старый код удаляет сотрудника, не зная об отметке «отчёт отправлен»."""

    dependencies = [
        ("shipments", "0011_shipment_report_sent"),
    ]

    operations = [
        db_on_delete("shipments", "Shipment", "report_sent_by", "SET NULL"),
        db_on_delete("shipments", "Shipment", "report_message", "SET NULL"),
    ]
