from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cameras", "0019_always_on_production_stock"),
    ]

    operations = [
        migrations.AddField(
            model_name="alwaysoncountercursor",
            name="event_caught_up_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="alwaysoncountercursor",
            name="last_event_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="alwaysoncountercursor",
            name="last_event_id",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="alwaysoncountercursor",
            name="event_journal_id",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="alwaysoncountercursor",
            name="event_compat_total",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="alwaysoncountercursor",
            name="event_boundary_validated",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="alwaysoncountercursor",
            name="event_drain_required_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="alwaysoncountercursor",
            name="event_stop_drain_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="alwaysoncountercursor",
            name="event_stop_confirmed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="alwaysoncountercursor",
            name="event_sync_error",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="alwaysoncountercursor",
            name="event_sync_failed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="alwaysoncountercursor",
            name="event_sync_supported",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="AlwaysOnImportedEvent",
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
                ("camera", models.CharField(max_length=32)),
                ("upstream_event_id", models.PositiveBigIntegerField()),
                ("occurred_at", models.DateTimeField(db_index=True)),
                ("source", models.CharField(max_length=16)),
                ("mode", models.CharField(max_length=16)),
                (
                    "class_name",
                    models.CharField(blank=True, default="", max_length=100),
                ),
                (
                    "total_after",
                    models.PositiveBigIntegerField(blank=True, null=True),
                ),
                ("applied_to_analytics", models.BooleanField(default=False)),
                ("imported_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["upstream_event_id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("camera", "upstream_event_id"),
                        name="cameras_one_imported_event_per_camera_id",
                    )
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="alwaysoncountercursor",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(last_event_id__isnull=True)
                    | (
                        models.Q(event_compat_total__isnull=False)
                        & models.Q(last_total=models.F("event_compat_total"))
                    )
                ),
                name="cameras_event_cursor_compat_total",
            ),
        ),
    ]
