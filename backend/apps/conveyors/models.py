import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

MAX_SEQUENCE = 2**63 - 1


class ConveyorDevice(models.Model):
    """Cloud-polled ESP32 controller and its fail-safe command state."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=80)
    camera_source = models.CharField(max_length=32, unique=True)
    secret_sha256 = models.CharField(max_length=64, unique=True, editable=False)
    is_active = models.BooleanField(default=True)

    # Commands are revisions, not edge-triggered events. OFF is the durable
    # default and ON is valid only while the short lease in a sync response is
    # being renewed.
    desired_state = models.BooleanField(default=False)
    command_revision = models.PositiveBigIntegerField(default=1)
    command_session = models.ForeignKey(
        "cameras.AiCountingSession",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="cloud_conveyor_commands",
    )
    command_target_total = models.PositiveIntegerField(null=True, blank=True)
    command_terminal = models.BooleanField(default=True)
    stop_reason = models.CharField(max_length=64, default="enrolled")
    armed_device_boot_id = models.UUIDField(null=True, blank=True)
    armed_edge_boot_id = models.UUIDField(null=True, blank=True)
    run_started_at = models.DateTimeField(null=True, blank=True)

    # Latest authenticated ESP report. output_state is the GPIO/output latch;
    # feedback_state must be a physically separate contactor auxiliary input.
    last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_boot_id = models.UUIDField(null=True, blank=True)
    last_sequence = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(MAX_SEQUENCE)],
    )
    last_ack_revision = models.PositiveBigIntegerField(null=True, blank=True)
    output_state = models.BooleanField(null=True, blank=True)
    feedback_state = models.BooleanField(null=True, blank=True)
    fault = models.CharField(max_length=128, blank=True, default="")
    uptime_ms = models.PositiveBigIntegerField(null=True, blank=True)
    wifi_rssi = models.SmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(-127), MaxValueValidator(0)],
    )
    firmware = models.CharField(max_length=64, blank=True, default="")

    # Latest authenticated camera-PC observation. It is deliberately typed so
    # leases never depend on mutable/unvalidated JSON snapshots.
    last_ai_seen_at = models.DateTimeField(null=True, blank=True)
    last_ai_boot_id = models.UUIDField(null=True, blank=True)
    last_ai_sequence = models.PositiveBigIntegerField(null=True, blank=True)
    # Keep the exact payload total independently from the monotonic business
    # counter.  A regressed report must terminally stop the belt, but an exact
    # retry after a lost HTTP response must still be recognised as a duplicate.
    last_ai_reported_total = models.PositiveIntegerField(null=True, blank=True)
    last_ai_terminal_reason = models.CharField(max_length=32, null=True, blank=True)
    last_total = models.PositiveIntegerField(default=0)
    last_progress_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_conveyor_devices",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(command_revision__gte=1),
                name="conveyor_revision_positive",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(command_session__isnull=True)
                    | models.Q(command_target_total__gte=1)
                ),
                name="conveyor_bound_target_positive",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.camera_source})"
