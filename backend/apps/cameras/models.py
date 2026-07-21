from django.conf import settings
from django.db import models
from django.db.models import Q


class AiCountingSession(models.Model):
    """Durable ownership of a per-camera AI counting slot.

    The camera worker keeps the live counter, while this row records which
    order owns a given camera. Partial unique constraints on `camera` and
    `order` allow several different loadings to run in parallel, while keeping
    every camera and every order in at most one open session (safe across
    workers/tablets).
    """

    STARTING = "starting"
    ACTIVE = "active"
    CLOSED = "closed"
    FAILED = "failed"
    OPEN_STATUSES = (STARTING, ACTIVE)

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
    # Имя аннотированного MediaMTX-потока (например cam2ai). Само видео
    # остаётся на ПК камер; в PostgreSQL хранится только ссылка на поток.
    recording_stream = models.CharField(max_length=64, blank=True, default="")
    last_status = models.JSONField(default=dict, blank=True)
    error = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["camera"],
                condition=Q(status__in=["starting", "active"]),
                name="cameras_one_open_session_per_camera",
            ),
            models.UniqueConstraint(
                fields=["order"],
                condition=Q(status__in=["starting", "active"]),
                name="cameras_one_open_session_per_order",
            ),
        ]


class MonoblockCameraSettings(models.Model):
    """Admin-managed camera names and allowlist for loading workflows."""

    singleton = models.BooleanField(default=True, unique=True, editable=False)
    camera_sources = models.JSONField(default=list, blank=True)
    # Камеры, чьи модели работают 24/7 без публикации/записи видео.
    # Настройка доступна только Django superuser.
    always_on_camera_sources = models.JSONField(default=list, blank=True)
    camera_names = models.JSONField(default=dict, blank=True)
    # Сколько календарных дней держать завершённые заказы на живом борде.
    # 1 означает «только сегодня».
    completed_orders_days = models.PositiveSmallIntegerField(default=1)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="monoblock_camera_settings_updates",
    )
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def allowed_sources(cls) -> set[str]:
        row = cls.objects.filter(singleton=True).only("camera_sources").first()
        return {
            source for source in (row.camera_sources if row else [])
            if isinstance(source, str) and source
        }

    @classmethod
    def display_names(cls) -> dict[str, str]:
        row = cls.objects.filter(singleton=True).only("camera_names").first()
        names = row.camera_names if row and isinstance(row.camera_names, dict) else {}
        return {
            source: name.strip()
            for source, name in names.items()
            if isinstance(source, str) and isinstance(name, str) and name.strip()
        }

    @classmethod
    def always_on_sources(cls) -> list[str]:
        row = cls.objects.filter(singleton=True).only(
            "always_on_camera_sources"
        ).first()
        sources = row.always_on_camera_sources if row else []
        return [
            source for source in sources
            if isinstance(source, str) and source
        ]


class CameraHealthState(models.Model):
    """Last durable result of the end-to-end camera monitor.

    There is deliberately one row.  Keeping the heartbeat in PostgreSQL makes
    deploy checks independent from the monitor process itself: a dead monitor
    cannot keep returning a cached green response.
    """

    INITIALIZING = "initializing"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OUTAGE = "outage"
    STATUSES = (
        (INITIALIZING, "Initializing"),
        (HEALTHY, "Healthy"),
        (DEGRADED, "Degraded"),
        (OUTAGE, "Outage"),
    )

    singleton = models.BooleanField(default=True, unique=True, editable=False)
    status = models.CharField(max_length=16, choices=STATUSES, default=INITIALIZING)
    observed_status = models.CharField(
        max_length=16, choices=STATUSES, default=INITIALIZING
    )
    expected_count = models.PositiveSmallIntegerField(default=0)
    online_count = models.PositiveSmallIntegerField(default=0)
    failure_streak = models.PositiveSmallIntegerField(default=0)
    degraded_streak = models.PositiveSmallIntegerField(default=0)
    recovery_streak = models.PositiveSmallIntegerField(default=0)
    first_failure_at = models.DateTimeField(null=True, blank=True)
    first_degraded_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_good_at = models.DateTimeField(null=True, blank=True)
    last_changed_at = models.DateTimeField(null=True, blank=True)
    outage_started_at = models.DateTimeField(null=True, blank=True)
    components = models.JSONField(default=dict, blank=True)
    streams = models.JSONField(default=dict, blank=True)
    last_error = models.CharField(max_length=1000, blank=True, default="")

    class Meta:
        verbose_name = "camera health state"


class CameraIncident(models.Model):
    """Confirmed degraded/outage period and its alert audit trail."""

    DEGRADED = "degraded"
    OUTAGE = "outage"
    SEVERITIES = ((DEGRADED, "Degraded"), (OUTAGE, "Outage"))

    singleton = models.BooleanField(default=True, editable=False)
    started_at = models.DateTimeField(db_index=True)
    confirmed_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True, db_index=True)
    severity = models.CharField(max_length=12, choices=SEVERITIES, default=OUTAGE)
    expected_count = models.PositiveSmallIntegerField(default=0)
    minimum_online_count = models.PositiveSmallIntegerField(default=0)
    degraded_details = models.JSONField(default=dict, blank=True)
    outage_details = models.JSONField(default=dict, blank=True)
    recovery_details = models.JSONField(default=dict, blank=True)
    degraded_alert_attempted_at = models.DateTimeField(null=True, blank=True)
    degraded_alert_sent_at = models.DateTimeField(null=True, blank=True)
    degraded_alert_superseded_at = models.DateTimeField(null=True, blank=True)
    outage_alert_attempted_at = models.DateTimeField(null=True, blank=True)
    outage_alert_sent_at = models.DateTimeField(null=True, blank=True)
    recovery_alert_attempted_at = models.DateTimeField(null=True, blank=True)
    recovery_alert_sent_at = models.DateTimeField(null=True, blank=True)
    alert_error = models.CharField(max_length=1000, blank=True, default="")

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["singleton"],
                condition=Q(resolved_at__isnull=True),
                name="cameras_one_open_camera_incident",
            )
        ]
