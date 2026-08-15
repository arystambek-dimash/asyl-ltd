from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("shipments", "0004_shipment_scale_weights"),
    ]

    operations = [
        migrations.AddField(
            model_name="shipment",
            name="weigh_in_camera",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="shipment",
            name="weigh_in_session_id",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="shipment",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        weigh_in_camera="",
                        weigh_in_session_id__isnull=True,
                    )
                    | (
                        ~models.Q(weigh_in_camera="")
                        & models.Q(weigh_in_session_id__isnull=False)
                    )
                ),
                name="shipment_scale_provenance_pair",
            ),
        ),
    ]
