from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("shipments", "0005_shipment_scale_provenance"),
    ]

    operations = [
        # Expand/contract rollout: new code no longer sees these fields, while
        # the columns remain compatible with old workers and an image rollback.
        # `weigh_in_camera` was the only NOT NULL column without a persistent
        # DB default; add one before new code starts inserting Shipment rows.
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        'ALTER TABLE "shipments_shipment" '
                        'ALTER COLUMN "weigh_in_camera" SET DEFAULT \'\''
                    ),
                    reverse_sql=(
                        'ALTER TABLE "shipments_shipment" '
                        'ALTER COLUMN "weigh_in_camera" DROP DEFAULT'
                    ),
                ),
            ],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="shipment",
                    name="shipment_scale_provenance_pair",
                ),
                migrations.RemoveField(
                    model_name="shipment",
                    name="weigh_in_camera",
                ),
                migrations.RemoveField(
                    model_name="shipment",
                    name="weigh_in_session_id",
                ),
                migrations.RemoveField(
                    model_name="shipment",
                    name="weigh_in_source",
                ),
                migrations.RemoveField(
                    model_name="shipment",
                    name="weigh_out_kg",
                ),
                migrations.RemoveField(
                    model_name="shipment",
                    name="net_weight_kg",
                ),
            ],
        ),
    ]
