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
        # Ordering по -started_at применяется к каждому чтению истории, но
        # индекса под него не было: список сессий сортировал всю таблицу.
        indexes = [
            models.Index(fields=["-started_at"], name="ai_session_started_idx"),
        ]
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
    # Изменение защищено отдельным правом ai_247.manage.
    always_on_camera_sources = models.JSONField(default=list, blank=True)
    # Одна камера высокого разрешения, закреплённая за будущим контуром
    # круглосуточного распознавания номеров вагонов.
    wagon_number_camera_source = models.CharField(max_length=32, blank=True, default="")
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
        configured = {
            source
            for source in (row.camera_sources if row else [])
            if isinstance(source, str) and source
        }
        # Камера, закреплённая за физическим моноблоком, всегда разрешена для
        # его рабочего процесса, даже если администратор убрал её из старого
        # общего списка операторов.
        configured.update(
            MonoblockDevice.objects.filter(is_active=True).values_list(
                "camera_source", flat=True
            )
        )
        return configured

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
        row = (
            cls.objects.filter(singleton=True).only("always_on_camera_sources").first()
        )
        sources = row.always_on_camera_sources if row else []
        return [source for source in sources if isinstance(source, str) and source]

    @classmethod
    def wagon_number_source(cls) -> str:
        row = (
            cls.objects.filter(singleton=True)
            .only("wagon_number_camera_source")
            .first()
        )
        source = row.wagon_number_camera_source if row else ""
        return source if isinstance(source, str) else ""


class MonoblockDevice(models.Model):
    """Отдельная учётная запись физического моноблока и одна его камера."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="monoblock_device",
    )
    name = models.CharField(max_length=80)
    camera_source = models.CharField(max_length=32, unique=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_monoblock_devices",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class AlwaysOnCounterCursor(models.Model):
    """Последний сырой счётчик агента для вычисления реального прироста."""

    camera = models.CharField(max_length=32, unique=True)
    last_total = models.PositiveIntegerField(default=0)
    last_per_color = models.JSONField(default=dict, blank=True)
    last_mode = models.CharField(max_length=16, blank=True, default="")
    # ``NULL`` keeps compatibility with camera-PC builds that only expose
    # aggregate snapshots.  The first successful /events response switches
    # this camera permanently to the durable event stream, including when the
    # first page is empty and the high-water mark is therefore zero.
    last_event_id = models.PositiveBigIntegerField(null=True, blank=True)
    event_journal_id = models.CharField(max_length=64, null=True, blank=True)
    last_event_at = models.DateTimeField(null=True, blank=True)
    event_caught_up_at = models.DateTimeField(null=True, blank=True)
    # NULL: not probed since this schema was installed; False: explicit 404
    # legacy service; True: durable event journal is the sole count source.
    event_sync_supported = models.BooleanField(null=True, blank=True)
    event_boundary_validated = models.BooleanField(default=False)
    event_drain_required_at = models.DateTimeField(null=True, blank=True)
    event_stop_drain_requested_at = models.DateTimeField(null=True, blank=True)
    event_stop_confirmed_at = models.DateTimeField(null=True, blank=True)
    event_compat_total = models.PositiveIntegerField(null=True, blank=True)
    event_sync_error = models.CharField(max_length=500, blank=True, default="")
    event_sync_failed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # Old backend images know only ``last_total``.  During an image
            # rollback they must not apply snapshots behind a frozen event
            # cursor and make a later re-rollout count the same crossings
            # twice.  New event ingestion advances both fields atomically.
            models.CheckConstraint(
                condition=(
                    Q(last_event_id__isnull=True)
                    | (
                        Q(event_compat_total__isnull=False)
                        & Q(last_total=models.F("event_compat_total"))
                    )
                ),
                name="cameras_event_cursor_compat_total",
            ),
        ]


class AlwaysOnImportedEvent(models.Model):
    """One durable camera-PC count event applied to CRM at most once."""

    camera = models.CharField(max_length=32)
    upstream_event_id = models.PositiveBigIntegerField()
    occurred_at = models.DateTimeField(db_index=True)
    source = models.CharField(max_length=16)
    mode = models.CharField(max_length=16)
    class_name = models.CharField(max_length=100, blank=True, default="")
    total_after = models.PositiveBigIntegerField(null=True, blank=True)
    applied_to_analytics = models.BooleanField(default=False)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["upstream_event_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["camera", "upstream_event_id"],
                name="cameras_one_imported_event_per_camera_id",
            ),
        ]


class VehiclePlateEvent(models.Model):
    """Metadata-only vehicle plate observation received from the camera PC.

    The edge service owns detection/OCR and retains no media in this table.
    ``event_id`` is the durable idempotency boundary between retries from the
    camera PC and CRM ingestion.
    """

    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    PROCESSING_STATUSES = (
        (RECEIVED, "Received"),
        (PROCESSING, "Processing"),
        (PROCESSED, "Processed"),
        (FAILED, "Failed"),
    )

    event_id = models.UUIDField(unique=True)
    vehicle_number = models.CharField(max_length=8)
    camera = models.CharField(max_length=32)
    source = models.CharField(max_length=4)
    detected_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    stationary_seconds = models.DecimalField(max_digits=8, decimal_places=3)
    confirmation_votes = models.PositiveSmallIntegerField()
    detector_confidence = models.DecimalField(max_digits=5, decimal_places=4)
    ocr_confidence = models.DecimalField(max_digits=5, decimal_places=4)
    payload_json = models.JSONField(default=dict)
    processing_status = models.CharField(
        max_length=16,
        choices=PROCESSING_STATUSES,
        default=RECEIVED,
    )
    processing_attempts = models.PositiveIntegerField(default=0)
    processing_action = models.CharField(max_length=16, blank=True, default="")
    processing_error = models.CharField(max_length=64, blank=True, default="")
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-detected_at", "-id"]
        indexes = [
            models.Index(
                fields=["vehicle_number"],
                name="plate_vehicle_number_idx",
            ),
            models.Index(fields=["detected_at"], name="plate_detected_at_idx"),
            models.Index(
                fields=["camera", "-detected_at"],
                name="plate_camera_detected_idx",
            ),
        ]


class AlwaysOnDailyAnalytics(models.Model):
    """Накопленный 24/7-счёт за день и аудируемая ручная поправка."""

    camera = models.CharField(max_length=32)
    day = models.DateField(db_index=True)
    model_total = models.PositiveIntegerField(default=0)
    model_per_color = models.JSONField(default=dict, blank=True)
    adjustment = models.IntegerField(default=0)
    # Строка уехала в архив: в текущем счётчике её больше нет, но день
    # остаётся на графике и в истории. Обнуление счётчика — это перенос
    # накопленного в архив, а не потеря данных.
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # Прямая ссылка на закрытие: по одной метке времени дни двух архивов
    # надёжно не разделить, а разбивка по дням нужна именно по каждому.
    archive = models.ForeignKey(
        "cameras.AlwaysOnCountArchive",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="daily_rows",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["camera"]
        constraints = [
            models.UniqueConstraint(
                fields=["camera", "day"],
                name="cameras_one_always_on_total_per_day",
            ),
        ]

    @property
    def total(self) -> int:
        return max(0, self.model_total + self.adjustment)


class AlwaysOnColorProductMapping(models.Model):
    """Warehouse product selected in advance for one camera/color pair."""

    camera = models.CharField(max_length=32)
    color = models.CharField(max_length=32)
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.PROTECT,
        related_name="always_on_color_mappings",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="always_on_color_mapping_updates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["camera", "color"]
        constraints = [
            models.UniqueConstraint(
                fields=["camera", "color"],
                name="cameras_one_product_per_camera_color",
            ),
        ]


class AlwaysOnProductionRun(models.Model):
    """A contiguous interval in which an always-on model counted one color."""

    camera = models.CharField(max_length=32, db_index=True)
    business_day = models.DateField(db_index=True)
    color = models.CharField(max_length=32)
    started_at = models.DateTimeField(db_index=True)
    last_counted_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True, db_index=True)
    model_bags = models.PositiveIntegerField(default=0)
    is_approximate = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(
                fields=["camera", "-started_at"],
                name="aon_run_camera_started_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["camera", "color"],
                condition=Q(ended_at__isnull=True),
                name="cameras_one_open_run_per_color",
            ),
        ]


class AlwaysOnProductionCorrection(models.Model):
    """Audited manual change to a production color total."""

    camera = models.CharField(max_length=32)
    business_day = models.DateField(db_index=True)
    color = models.CharField(max_length=32)
    delta = models.IntegerField()
    reason = models.CharField(max_length=500)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="always_on_production_corrections",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["camera", "business_day", "color"],
                name="aon_corr_camera_day_color_idx",
            ),
        ]


class AlwaysOnStockBatch(models.Model):
    """Idempotent warehouse posting scheduled for one production day."""

    SCHEDULED = "scheduled"
    BLOCKED = "blocked"
    POSTED = "posted"
    EMPTY = "empty"
    FAILED = "failed"
    STATUSES = (
        (SCHEDULED, "Scheduled"),
        (BLOCKED, "Blocked"),
        (POSTED, "Posted"),
        (EMPTY, "Empty"),
        (FAILED, "Failed"),
    )

    camera = models.CharField(max_length=32)
    business_day = models.DateField()
    scheduled_for = models.DateTimeField()
    status = models.CharField(max_length=12, choices=STATUSES, default=SCHEDULED)
    total_bags = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True, default="")
    attempts = models.PositiveIntegerField(default=0)
    posted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-business_day", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["camera", "business_day"],
                name="cameras_one_stock_batch_per_day",
            ),
        ]


class AlwaysOnStockPosting(models.Model):
    """One product receipt produced by an automatic daily stock batch."""

    batch = models.ForeignKey(
        AlwaysOnStockBatch,
        on_delete=models.CASCADE,
        related_name="items",
    )
    color = models.CharField(max_length=32)
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    detected_bags = models.PositiveIntegerField()
    correction_bags = models.IntegerField(default=0)
    posted_bags = models.PositiveIntegerField()
    receipt = models.OneToOneField(
        "warehouse.StockReceipt",
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["color"]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "color"],
                name="cameras_one_stock_posting_per_color",
            ),
        ]


class AlwaysOnCountArchive(models.Model):
    """Закрытый период 24/7-счёта: что было накоплено на момент обнуления.

    Архив хранит уже посчитанное, поэтому переписывать его нельзя — новые
    закрытия добавляют строки, а не меняют старые.
    """

    camera = models.CharField(max_length=32, db_index=True)
    # Границы включительно: с какого по какой день собран этот архив.
    period_start = models.DateField()
    period_end = models.DateField()
    model_total = models.PositiveIntegerField(default=0)
    model_per_color = models.JSONField(default=dict, blank=True)
    adjustment = models.IntegerField(default=0)
    total = models.PositiveIntegerField(default=0)
    days = models.PositiveIntegerField(default=0)
    # Снимок дней, которые нельзя пометить архивными: день закрытия
    # продолжает считаться дальше, поэтому его вклад сохраняется здесь.
    day_rows = models.JSONField(default=list, blank=True)
    note = models.CharField(max_length=500, blank=True, default="")
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="always_on_archives",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


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
