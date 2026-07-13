from django.conf import settings
from django.db import models
from django.db.models import Q


class AiCountingSession(models.Model):
    """Durable ownership of the single AI/GPU counting slot.

    The camera worker keeps the live counter, while this row records which
    order owns it.  A partial unique constraint makes the one-session rule
    safe across all Django workers and concurrent tablets.
    """

    STARTING = "starting"
    ACTIVE = "active"
    CLOSED = "closed"
    FAILED = "failed"
    OPEN_STATUSES = (STARTING, ACTIVE)

    # Every row has the same value. Combined with the conditional unique
    # constraint below this gives us one global open slot.
    singleton = models.BooleanField(default=True, editable=False)
    order = models.ForeignKey(
        "orders.Order", on_delete=models.PROTECT, related_name="ai_counting_sessions"
    )
    camera = models.CharField(max_length=32)
    status = models.CharField(max_length=12, default=STARTING)
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_counting_sessions_started",
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_counting_sessions_closed",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    final_total = models.PositiveIntegerField(null=True, blank=True)
    last_status = models.JSONField(default=dict, blank=True)
    error = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["singleton"],
                condition=Q(status__in=["starting", "active"]),
                name="cameras_one_open_ai_counting_session",
            )
        ]

