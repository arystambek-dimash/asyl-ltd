from django.db import migrations

from apps.common.migration_ops import db_on_delete


class Migration(migrations.Migration):
    """Откат образа: старый ``rollback_shipment`` удаляет Shipment, не зная о складах-источниках."""

    dependencies = [
        ("shipments", "0016_shipmentsource"),
    ]

    operations = [
        db_on_delete("shipments", "ShipmentSource", "shipment", "CASCADE"),
        db_on_delete("shipments", "ShipmentSource", "product", "SET NULL"),
    ]
