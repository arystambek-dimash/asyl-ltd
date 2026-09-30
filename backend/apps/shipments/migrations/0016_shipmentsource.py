import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0017_remove_product_ask_truck_weight"),
        ("shipments", "0015_sync_product_label_snapshots"),
        ("warehouse", "0011_multi_warehouse_contract"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShipmentSource",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("bags", models.PositiveIntegerField()),
                (
                    "product",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="shipment_sources",
                        to="catalog.product",
                    ),
                ),
                (
                    "shipment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sources",
                        to="shipments.shipment",
                    ),
                ),
                (
                    "warehouse",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="shipment_sources",
                        to="warehouse.warehouse",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("shipment", "product", "warehouse"),
                        name="shipment_source_unique_cell",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("bags__gt", 0)),
                        name="shipment_source_bags_positive",
                    ),
                ],
            },
        ),
    ]
