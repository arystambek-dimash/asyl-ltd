from django.db import migrations

from apps.common.migration_ops import db_on_delete


class Migration(migrations.Migration):
    """Откат образа: старый ``rollback_shipment`` удаляет Shipment, не зная о вагонах."""

    dependencies = [
        ("shipments", "0008_shipmentwagon"),
    ]

    operations = [
        db_on_delete("shipments", "ShipmentWagon", "shipment", "CASCADE"),
        db_on_delete("shipments", "ShipmentWagon", "product", "SET NULL"),
    ]
