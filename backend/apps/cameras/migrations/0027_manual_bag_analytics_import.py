import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cameras", "0026_imported_event_continuous_analytics"),
    ]

    operations = [
        migrations.CreateModel(
            name="ManualBagAnalyticsImportBatch",
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
                ("file_sha256", models.CharField(max_length=64, unique=True)),
                ("schema_name", models.CharField(max_length=100)),
                ("source_filename", models.CharField(max_length=255)),
                ("model_id", models.CharField(max_length=100)),
                ("model_sha256", models.CharField(max_length=64)),
                ("camera", models.CharField(max_length=32)),
                ("source", models.CharField(max_length=16)),
                ("analytics_scope", models.CharField(max_length=32)),
                ("event_count", models.PositiveIntegerField()),
                ("first_captured_at", models.DateTimeField()),
                ("last_captured_at", models.DateTimeField()),
                ("per_day", models.JSONField(default=dict)),
                ("applied_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-applied_at", "-id"]},
        ),
        migrations.CreateModel(
            name="ManualBagAnalyticsImportEvent",
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
                (
                    "idempotency_key",
                    models.CharField(max_length=255, unique=True),
                ),
                ("sequence", models.PositiveIntegerField()),
                ("captured_at", models.DateTimeField(db_index=True)),
                ("local_day", models.DateField(db_index=True)),
                ("camera", models.CharField(max_length=32)),
                ("source", models.CharField(max_length=16)),
                ("model_event_origin", models.CharField(max_length=32)),
                ("source_row_id", models.PositiveBigIntegerField()),
                (
                    "shadow_run_id",
                    models.PositiveBigIntegerField(blank=True, null=True),
                ),
                ("class_name", models.CharField(max_length=100)),
                ("color", models.CharField(max_length=100)),
                ("color_confidence", models.FloatField(blank=True, null=True)),
                (
                    "brand",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                ("brand_confidence", models.FloatField(blank=True, null=True)),
                (
                    "sku",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
                ("classification_status", models.CharField(max_length=32)),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="events",
                        to="cameras.manualbaganalyticsimportbatch",
                    ),
                ),
            ],
            options={
                "ordering": ["sequence"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("batch", "sequence"),
                        name="cameras_one_manual_event_sequence_per_batch",
                    )
                ],
            },
        ),
    ]
