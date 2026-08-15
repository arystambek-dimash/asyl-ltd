import uuid

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("cameras", "0016_ai_session_conveyor_control"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConveyorDevice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("name", models.CharField(max_length=80)),
                ("camera_source", models.CharField(max_length=32, unique=True)),
                ("secret_sha256", models.CharField(editable=False, max_length=64, unique=True)),
                ("is_active", models.BooleanField(default=True)),
                ("desired_state", models.BooleanField(default=False)),
                ("command_revision", models.PositiveBigIntegerField(default=1)),
                ("command_target_total", models.PositiveIntegerField(blank=True, null=True)),
                ("command_terminal", models.BooleanField(default=True)),
                ("stop_reason", models.CharField(default="enrolled", max_length=64)),
                ("armed_device_boot_id", models.UUIDField(blank=True, null=True)),
                ("armed_edge_boot_id", models.UUIDField(blank=True, null=True)),
                ("run_started_at", models.DateTimeField(blank=True, null=True)),
                ("last_seen_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("last_boot_id", models.UUIDField(blank=True, null=True)),
                ("last_sequence", models.PositiveBigIntegerField(blank=True, null=True, validators=[django.core.validators.MaxValueValidator(9223372036854775807)])),
                ("last_ack_revision", models.PositiveBigIntegerField(blank=True, null=True)),
                ("output_state", models.BooleanField(blank=True, null=True)),
                ("feedback_state", models.BooleanField(blank=True, null=True)),
                ("fault", models.CharField(blank=True, default="", max_length=128)),
                ("uptime_ms", models.PositiveBigIntegerField(blank=True, null=True)),
                ("wifi_rssi", models.SmallIntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(-127), django.core.validators.MaxValueValidator(0)])),
                ("firmware", models.CharField(blank=True, default="", max_length=64)),
                ("last_ai_seen_at", models.DateTimeField(blank=True, null=True)),
                ("last_ai_boot_id", models.UUIDField(blank=True, null=True)),
                ("last_ai_sequence", models.PositiveBigIntegerField(blank=True, null=True)),
                ("last_ai_reported_total", models.PositiveIntegerField(blank=True, null=True)),
                ("last_ai_terminal_reason", models.CharField(blank=True, max_length=32, null=True)),
                ("last_total", models.PositiveIntegerField(default=0)),
                ("last_progress_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("command_session", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="cloud_conveyor_commands", to="cameras.aicountingsession")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_conveyor_devices", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["name", "id"]},
        ),
        migrations.AddConstraint(
            model_name="conveyordevice",
            constraint=models.CheckConstraint(condition=models.Q(("command_revision__gte", 1)), name="conveyor_revision_positive"),
        ),
        migrations.AddConstraint(
            model_name="conveyordevice",
            constraint=models.CheckConstraint(condition=models.Q(("command_session__isnull", True), ("command_target_total__gte", 1), _connector="OR"), name="conveyor_bound_target_positive"),
        ),
    ]
