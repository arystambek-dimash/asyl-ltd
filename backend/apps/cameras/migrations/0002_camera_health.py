from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("cameras", "0001_ai_counting_session")]

    operations = [
        migrations.CreateModel(
            name="CameraHealthState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("singleton", models.BooleanField(default=True, editable=False, unique=True)),
                ("status", models.CharField(choices=[("initializing", "Initializing"), ("healthy", "Healthy"), ("degraded", "Degraded"), ("outage", "Outage")], default="initializing", max_length=16)),
                ("observed_status", models.CharField(choices=[("initializing", "Initializing"), ("healthy", "Healthy"), ("degraded", "Degraded"), ("outage", "Outage")], default="initializing", max_length=16)),
                ("expected_count", models.PositiveSmallIntegerField(default=0)),
                ("online_count", models.PositiveSmallIntegerField(default=0)),
                ("failure_streak", models.PositiveSmallIntegerField(default=0)),
                ("degraded_streak", models.PositiveSmallIntegerField(default=0)),
                ("recovery_streak", models.PositiveSmallIntegerField(default=0)),
                ("first_failure_at", models.DateTimeField(blank=True, null=True)),
                ("first_degraded_at", models.DateTimeField(blank=True, null=True)),
                ("last_checked_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("last_good_at", models.DateTimeField(blank=True, null=True)),
                ("last_changed_at", models.DateTimeField(blank=True, null=True)),
                ("outage_started_at", models.DateTimeField(blank=True, null=True)),
                ("components", models.JSONField(blank=True, default=dict)),
                ("streams", models.JSONField(blank=True, default=dict)),
                ("last_error", models.CharField(blank=True, default="", max_length=1000)),
            ],
            options={"verbose_name": "camera health state"},
        ),
        migrations.CreateModel(
            name="CameraIncident",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("singleton", models.BooleanField(default=True, editable=False)),
                ("started_at", models.DateTimeField(db_index=True)),
                ("confirmed_at", models.DateTimeField()),
                ("resolved_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("severity", models.CharField(choices=[("degraded", "Degraded"), ("outage", "Outage")], default="outage", max_length=12)),
                ("expected_count", models.PositiveSmallIntegerField(default=0)),
                ("minimum_online_count", models.PositiveSmallIntegerField(default=0)),
                ("degraded_details", models.JSONField(blank=True, default=dict)),
                ("outage_details", models.JSONField(blank=True, default=dict)),
                ("recovery_details", models.JSONField(blank=True, default=dict)),
                ("degraded_alert_attempted_at", models.DateTimeField(blank=True, null=True)),
                ("degraded_alert_sent_at", models.DateTimeField(blank=True, null=True)),
                ("degraded_alert_superseded_at", models.DateTimeField(blank=True, null=True)),
                ("outage_alert_attempted_at", models.DateTimeField(blank=True, null=True)),
                ("outage_alert_sent_at", models.DateTimeField(blank=True, null=True)),
                ("recovery_alert_attempted_at", models.DateTimeField(blank=True, null=True)),
                ("recovery_alert_sent_at", models.DateTimeField(blank=True, null=True)),
                ("alert_error", models.CharField(blank=True, default="", max_length=1000)),
            ],
            options={"ordering": ["-started_at"]},
        ),
        migrations.AddConstraint(
            model_name="cameraincident",
            constraint=models.UniqueConstraint(
                condition=Q(resolved_at__isnull=True),
                fields=("singleton",),
                name="cameras_one_open_camera_incident",
            ),
        ),
    ]
