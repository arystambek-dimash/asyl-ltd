"""Приход зерна вагонами: силосы, поставки, вагоны и неизменяемый леджер.

Правила хранения:
- вес — только целые килограммы (никаких float);
- остаток силоса выводится ТОЛЬКО из движений ``GrainMovement``;
- резерв места — отдельные записи ``SiloReservation`` (сумма активных);
- движения после проведения неизменяемы: правка — обратной операцией.
"""

from django.conf import settings
from django.db import models
from django.db.models import Sum

from .statuses import EXPECTED, ON_SITE_STATUSES, WAGON_STATUSES

# Ориентация машины на кадре весовой: передом к камере — заезд, задом — выезд.
VEHICLE_ORIENTATION_FRONT = "front"
VEHICLE_ORIENTATION_REAR = "rear"
VEHICLE_ORIENTATIONS = [
    ("", "Не определена"),
    (VEHICLE_ORIENTATION_FRONT, "Передом к камере"),
    (VEHICLE_ORIENTATION_REAR, "Задом к камере"),
]

PASSAGE_SCALE_DEFAULT_STABLE_WEIGHT_SECONDS = 10
PASSAGE_SCALE_MIN_STABLE_WEIGHT_SECONDS = 2
PASSAGE_SCALE_MAX_STABLE_WEIGHT_SECONDS = 60


class GrainSettings(models.Model):
    """Единственная строка настроек модуля (порог расхождения и датчиков)."""

    allowed_discrepancy_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=1
    )
    sensor_warning_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=5
    )

    class Meta:
        verbose_name = "Настройки зерна"

    @classmethod
    def get(cls) -> "GrainSettings":
        row = cls.objects.first()
        return row if row is not None else cls.objects.create()


class SiloType(models.Model):
    """Назначение силоса и маршрут прихода для конкретного вида зерна."""

    name = models.CharField(max_length=100, unique=True)
    grain_culture = models.CharField(max_length=100, blank=True, default="")
    grain_class = models.CharField(max_length=50, blank=True, default="")
    color = models.CharField(max_length=7, default="#C58A35")
    description = models.CharField(max_length=300, blank=True, default="")
    default_silo = models.ForeignKey(
        "Silo",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="default_for_types",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name


class Silo(models.Model):
    STATUSES = ["active", "blocked", "maintenance"]

    name = models.CharField(max_length=100, unique=True)
    total_capacity_kg = models.PositiveBigIntegerField()
    silo_type = models.ForeignKey(
        SiloType,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="silos",
    )
    grain_culture = models.CharField(max_length=100, blank=True, default="")
    grain_class = models.CharField(max_length=50, blank=True, default="")
    allow_mixing = models.BooleanField(default=False)
    is_quarantine = models.BooleanField(default=False)
    status = models.CharField(max_length=20, default="active")
    unloading_line = models.CharField(max_length=100, blank=True, default="")
    # Оценка физического датчика уровня; расчётный остаток она НЕ заменяет.
    sensor_estimated_kg = models.PositiveBigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name

    @property
    def current_balance_kg(self) -> int:
        last = self.movements.order_by("-id").first()
        return last.balance_after_kg if last else 0

    @property
    def reserved_kg(self) -> int:
        return (
            self.reservations.filter(active=True).aggregate(total=Sum("amount_kg"))[
                "total"
            ]
            or 0
        )

    @property
    def free_capacity_kg(self) -> int:
        return self.total_capacity_kg - self.current_balance_kg - self.reserved_kg


class GrainSupply(models.Model):
    STATUSES = ["draft", "expected", "closed", "cancelled"]

    supplier = models.CharField(max_length=200)
    grain_type = models.ForeignKey(
        SiloType,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="supplies",
    )
    assigned_silo = models.ForeignKey(
        Silo,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="planned_supplies",
    )
    simple_flow = models.BooleanField(default=False)
    contract = models.CharField(max_length=200, blank=True, default="")
    culture = models.CharField(max_length=100)
    grain_class = models.CharField(max_length=50, blank=True, default="")
    expected_date = models.DateField(null=True, blank=True)
    expected_total_kg = models.PositiveBigIntegerField(null=True, blank=True)
    document_weight_kg = models.PositiveBigIntegerField(null=True, blank=True)
    wagons_expected = models.PositiveIntegerField(null=True, blank=True)
    note = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, default="draft")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="grain_supplies",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"Поставка #{self.pk} · {self.supplier}"


class Wagon(models.Model):
    WEIGHT_SOURCES = ["auto", "manual", "scale"]

    # Направление рейса. Приход: транспорт въезжает гружёным и оставляет зерно
    # в силосе, нетто = вход − выход. Проход: въезжает пустым, забирает отруби
    # и уезжает гружёным, нетто = выход − вход. Это ровно обратная формула,
    # поэтому направление хранится явно, а не выводится из весов задним числом.
    INTAKE = "intake"
    PASSAGE = "passage"
    DIRECTIONS = [INTAKE, PASSAGE]

    supply = models.ForeignKey(
        GrainSupply,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="wagons",
    )
    number = models.CharField(max_length=30, blank=True, default="")
    workflow = models.CharField(max_length=20, default="legacy")
    direction = models.CharField(max_length=10, default=INTAKE)
    # Что вывозят на проходе («Отруби», «Мучка»…). Для прихода поле пустое:
    # там культура берётся из типа зерна поставки.
    cargo_name = models.CharField(max_length=100, blank=True, default="")
    number_source = models.CharField(max_length=20, default="manual")
    number_camera_source = models.CharField(max_length=32, blank=True, default="")
    vehicle_plate_event = models.OneToOneField(
        "cameras.VehiclePlateEvent",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="grain_wagon",
    )
    exit_vehicle_plate_event = models.OneToOneField(
        "cameras.VehiclePlateEvent",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="grain_exit_wagon",
    )
    status = models.CharField(max_length=30, default=EXPECTED)
    unplanned = models.BooleanField(default=False)
    # Вес по документам на конкретный вагон (для проверки расхождений).
    document_weight_kg = models.PositiveBigIntegerField(null=True, blank=True)
    expected_weight_kg = models.PositiveBigIntegerField(null=True, blank=True)

    arrived_at = models.DateTimeField(null=True, blank=True)
    arrived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    gross_weight_kg = models.PositiveBigIntegerField(null=True, blank=True)
    tare_weight_kg = models.PositiveBigIntegerField(null=True, blank=True)
    net_weight_kg = models.PositiveBigIntegerField(null=True, blank=True)

    assigned_silo = models.ForeignKey(
        Silo,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="assigned_wagons",
    )
    unloading_point = models.CharField(max_length=100, blank=True, default="")
    unloading_started_at = models.DateTimeField(null=True, blank=True)
    silo_arrived_at = models.DateTimeField(null=True, blank=True)
    unloading_finished_at = models.DateTimeField(null=True, blank=True)
    unloading_paused = models.BooleanField(default=False)

    exited_at = models.DateTimeField(null=True, blank=True)
    exit_note = models.CharField(max_length=300, blank=True, default="")

    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.CheckConstraint(
                name="wagon_status_valid",
                condition=models.Q(status__in=WAGON_STATUSES),
            ),
            models.CheckConstraint(
                name="wagon_direction_valid",
                condition=models.Q(direction__in=["intake", "passage"]),
            ),
            models.UniqueConstraint(
                fields=["number"],
                condition=(
                    models.Q(
                        direction="passage",
                        status__in=sorted(ON_SITE_STATUSES),
                    )
                    & ~models.Q(number="")
                ),
                name="grain_one_active_passage_plate",
            ),
        ]

    def __str__(self):
        return f"Вагон {self.number or f'#{self.pk}'}"

    @property
    def planned_weight_kg(self) -> int | None:
        """Вес для резерва/сверки: документы точнее ожиданий."""
        return self.document_weight_kg or self.expected_weight_kg

    @property
    def is_passage(self) -> bool:
        return self.direction == self.PASSAGE

    @property
    def entry_weight_kg(self) -> int | None:
        """Вес на въезде. У прихода это брутто, у прохода — пустая машина."""
        return self.gross_weight_kg

    @property
    def exit_weight_kg(self) -> int | None:
        """Вес на выезде. У прихода это тара, у прохода — гружёная машина."""
        return self.tare_weight_kg

    def computed_net_kg(self) -> int | None:
        """Нетто по двум весам. Направление задаёт знак разности.

        Приход: въехал гружёным, уехал пустым → вход − выход.
        Проход: въехал пустым, уехал гружёным → выход − вход.
        Единственное место, где это правило записано; сервисы и сериализаторы
        обязаны считать нетто только отсюда.
        """
        entry, exit_weight = self.entry_weight_kg, self.exit_weight_kg
        if entry is None or exit_weight is None:
            return None
        return exit_weight - entry if self.is_passage else entry - exit_weight


def weighing_photo_path(instance, filename: str) -> str:
    return f"grain/weighings/{instance.wagon_id}/{filename}"


def unassigned_weighing_photo_path(instance, filename: str) -> str:
    return f"grain/unassigned/{filename}"


class WeighingRecord(models.Model):
    """Журнал всех взвешиваний, включая повторные и ручные правки."""

    KINDS = ["gross", "tare"]

    wagon = models.ForeignKey(Wagon, on_delete=models.CASCADE, related_name="weighings")
    kind = models.CharField(max_length=10)
    weight_kg = models.PositiveBigIntegerField()
    scale_number = models.CharField(max_length=50, blank=True, default="")
    source = models.CharField(max_length=10, default="manual")
    manual_reason = models.CharField(max_length=300, blank=True, default="")
    previous_weight_kg = models.PositiveBigIntegerField(null=True, blank=True)
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # Кадр машины с Camera-PC на момент взвешивания. Заполняется после
    # фиксации веса и никогда не блокирует саму операцию.
    photo = models.FileField(upload_to=weighing_photo_path, null=True, blank=True)
    photo_request_id = models.UUIDField(null=True, blank=True, db_index=True)
    photo_camera = models.CharField(max_length=32, blank=True, default="")
    # Как машина стояла на кадре: заезд ждём передом, выезд — задом.
    orientation = models.CharField(
        max_length=8, blank=True, default="", choices=VEHICLE_ORIENTATIONS
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class UnassignedWeighing(models.Model):
    """Вес с автовесов, который не удалось привязать к рейсу без оператора.

    Появляется, когда номер не распознан, а на территории уже есть открытые
    проходы: угадывать, чей это выезд, нельзя. Автоматика не останавливается,
    вес и фото сохраняются здесь, оператор привязывает их позже.
    """

    OPEN = "open"
    ASSIGNED = "assigned"
    DISCARDED = "discarded"
    STATUSES = [(OPEN, "Ожидает"), (ASSIGNED, "Привязано"), (DISCARDED, "Отклонено")]

    capture = models.OneToOneField(
        "AutomaticPassageCapture",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="unassigned_weighing",
    )
    weight_kg = models.PositiveBigIntegerField()
    stable_weight_at = models.DateTimeField()
    scale_number = models.CharField(max_length=50, blank=True, default="")
    scale_age_seconds = models.DecimalField(
        max_digits=10, decimal_places=3, null=True, blank=True
    )
    scale_updated_at = models.CharField(max_length=64, blank=True, default="")
    camera = models.CharField(max_length=32, blank=True, default="")
    photo = models.FileField(
        upload_to=unassigned_weighing_photo_path, null=True, blank=True
    )
    photo_request_id = models.UUIDField(null=True, blank=True, db_index=True)
    # open_passages_exist — номер не прочитан при открытых рейсах;
    # entry_missing — выезд с прочитанным номером, у которого нет заезда.
    reason = models.CharField(max_length=64, blank=True, default="")
    vehicle_number = models.CharField(max_length=30, blank=True, default="")
    orientation = models.CharField(
        max_length=8, blank=True, default="", choices=VEHICLE_ORIENTATIONS
    )
    status = models.CharField(max_length=12, choices=STATUSES, default=OPEN)
    wagon = models.ForeignKey(
        Wagon,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="unassigned_weighings",
    )
    action = models.CharField(max_length=10, blank=True, default="")
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.CheckConstraint(
                name="grain_unassigned_weighing_status_valid",
                condition=models.Q(status__in=["open", "assigned", "discarded"]),
            ),
            models.CheckConstraint(
                name="grain_unassigned_weighing_action_valid",
                condition=models.Q(action__in=["", "entry", "exit"]),
            ),
        ]


class PassageWeightCapture(models.Model):
    """Durable weight-first command joining one scale read to one plate result."""

    ENTRY = "entry"
    EXIT = "exit"
    ACTIONS = [(ENTRY, "Въезд"), (EXIT, "Выезд")]

    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    STATUSES = [
        (PROCESSING, "Выполняется"),
        (COMPLETED, "Завершено"),
        (FAILED, "Ошибка"),
    ]

    CLAIMED = "claimed"
    RECOGNIZING = "recognizing"
    APPLYING = "applying"
    DONE = "done"
    STAGES = [
        (CLAIMED, "Запрос принят"),
        (RECOGNIZING, "Распознавание номера"),
        (APPLYING, "Сохранение результата"),
        (DONE, "Завершено"),
    ]

    idempotency_key = models.UUIDField(unique=True)
    wagon = models.ForeignKey(
        Wagon,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="passage_weight_captures",
    )
    # Keep the physical-operation audit addressable even after an explicitly
    # authorized trip deletion detaches the live FK.
    wagon_id_snapshot = models.PositiveBigIntegerField(db_index=True)
    action = models.CharField(max_length=10, choices=ACTIONS)
    wagon_status_before = models.CharField(max_length=30)
    status = models.CharField(max_length=12, choices=STATUSES, default=PROCESSING)
    stage = models.CharField(max_length=16, choices=STAGES, default=CLAIMED)
    camera = models.CharField(max_length=32)
    camera_source = models.CharField(max_length=4, blank=True, default="")
    stable_weight_at = models.DateTimeField(null=True, blank=True)
    weight_kg = models.PositiveBigIntegerField(null=True, blank=True)
    scale_number = models.CharField(max_length=50, blank=True, default="")
    scale_age_seconds = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
    )
    scale_updated_at = models.CharField(max_length=64, blank=True, default="")
    vehicle_number = models.CharField(max_length=30, blank=True, default="")
    recognized_at = models.DateTimeField(null=True, blank=True)
    confirmation_votes = models.PositiveSmallIntegerField(null=True, blank=True)
    detector_confidence = models.DecimalField(
        max_digits=7,
        decimal_places=6,
        null=True,
        blank=True,
    )
    ocr_confidence = models.DecimalField(
        max_digits=7,
        decimal_places=6,
        null=True,
        blank=True,
    )
    ai_payload_json = models.JSONField(default=dict, blank=True)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    retryable = models.BooleanField(default=False)
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_detail = models.CharField(max_length=300, blank=True, default="")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.CheckConstraint(
                name="grain_passage_capture_action_valid",
                condition=models.Q(action__in=["entry", "exit"]),
            ),
            models.CheckConstraint(
                name="grain_passage_capture_status_valid",
                condition=models.Q(status__in=["processing", "completed", "failed"]),
            ),
            models.CheckConstraint(
                name="grain_passage_capture_stage_valid",
                condition=models.Q(
                    stage__in=["claimed", "recognizing", "applying", "done"]
                ),
            ),
            models.UniqueConstraint(
                fields=["wagon", "action"],
                condition=models.Q(status__in=["processing", "completed"]),
                name="grain_one_active_passage_capture",
            ),
        ]


class AutomaticPassageCapture(models.Model):
    """One durable automatic operation for one observed scale occupancy.

    The polling loop commits this row before contacting either the strict
    scale endpoint or Camera-PC.  A terminal row remains attached to the lane
    state until fresh zero readings prove that the vehicle has left the scale;
    a failed operation also requires explicit operator acknowledgement.  This
    makes a process/container restart fail closed instead of recognizing the
    same parked vehicle twice.
    """

    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    STATUSES = [
        (PROCESSING, "Выполняется"),
        (COMPLETED, "Завершено"),
        (FAILED, "Нужен оператор"),
    ]

    CLAIMED = "claimed"
    RECOGNIZING = "recognizing"
    APPLYING = "applying"
    DONE = "done"
    STAGES = [
        (CLAIMED, "Весы захвачены"),
        (RECOGNIZING, "Распознавание номера"),
        (APPLYING, "Сохранение рейса"),
        (DONE, "Завершено"),
    ]

    idempotency_key = models.UUIDField(unique=True)
    scale_number = models.CharField(max_length=50, default="truck")
    status = models.CharField(max_length=12, choices=STATUSES, default=PROCESSING)
    stage = models.CharField(max_length=16, choices=STAGES, default=CLAIMED)
    camera = models.CharField(max_length=32)
    camera_source = models.CharField(max_length=4, blank=True, default="")
    stable_weight_at = models.DateTimeField(null=True, blank=True)
    trigger_weight_kg = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    weight_kg = models.PositiveBigIntegerField(null=True, blank=True)
    scale_age_seconds = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
    )
    scale_updated_at = models.CharField(max_length=64, blank=True, default="")
    vehicle_number = models.CharField(max_length=30, blank=True, default="")
    recognized_at = models.DateTimeField(null=True, blank=True)
    confirmation_votes = models.PositiveSmallIntegerField(null=True, blank=True)
    detector_confidence = models.DecimalField(
        max_digits=7,
        decimal_places=6,
        null=True,
        blank=True,
    )
    ocr_confidence = models.DecimalField(
        max_digits=7,
        decimal_places=6,
        null=True,
        blank=True,
    )
    ai_payload_json = models.JSONField(default=dict, blank=True)
    # Ответ классификатора ориентации Camera-PC: front/rear и его уверенность.
    orientation = models.CharField(
        max_length=8, blank=True, default="", choices=VEHICLE_ORIENTATIONS
    )
    orientation_confidence = models.DecimalField(
        max_digits=7,
        decimal_places=6,
        null=True,
        blank=True,
    )
    recognition_attempts = models.PositiveSmallIntegerField(default=0)
    final_lookup_attempted = models.BooleanField(default=False)
    # Каждая попытка OCR — отдельный запрос к Camera-PC со своим UUID и
    # свежей меткой стабильного веса: Camera-PC не принимает триггер старше
    # нескольких секунд и кэширует ответ по UUID.
    attempt_request_id = models.UUIDField(null=True, blank=True)
    attempt_stable_weight_at = models.DateTimeField(null=True, blank=True)
    needs_new_attempt = models.BooleanField(default=False)
    # Номер так и не распознан: вес применяется без номера (рейс без номера
    # или неопознанное взвешивание), лента освобождается сама.
    plate_unresolved = models.BooleanField(default=False)
    # Только сбой записи в базу оставляет ленту заблокированной до
    # подтверждения оператором; сбои распознавания не требуют человека.
    requires_acknowledgement = models.BooleanField(default=True)
    retryable = models.BooleanField(default=False)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_detail = models.CharField(max_length=300, blank=True, default="")
    processing_started_at = models.DateTimeField(null=True, blank=True)
    vehicle_plate_event = models.OneToOneField(
        "cameras.VehiclePlateEvent",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="automatic_passage_capture",
    )
    wagon = models.ForeignKey(
        Wagon,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="automatic_passage_captures",
    )
    action = models.CharField(max_length=10, blank=True, default="")
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cleared_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.CheckConstraint(
                name="grain_auto_capture_status_valid",
                condition=models.Q(status__in=["processing", "completed", "failed"]),
            ),
            models.CheckConstraint(
                name="grain_auto_capture_stage_valid",
                condition=models.Q(
                    stage__in=["claimed", "recognizing", "applying", "done"]
                ),
            ),
            models.CheckConstraint(
                name="grain_auto_capture_action_valid",
                condition=models.Q(action__in=["", "entry", "exit", "unassigned"]),
            ),
        ]

    @property
    def needs_operator(self) -> bool:
        """Latched failure that only a human may release."""

        return (
            self.status == self.FAILED
            and self.requires_acknowledgement
            and self.acknowledged_at is None
        )


class PassageScaleAutomationState(models.Model):
    """Persistent fail-closed edge detector for one physical truck scale."""

    UNARMED = "unarmed"
    ARMED = "armed"
    STABILIZING = "stabilizing"
    PROCESSING = "processing"
    AWAITING_CLEAR = "awaiting_clear"
    PHASES = [
        (UNARMED, "Ожидает подтверждения пустых весов"),
        (ARMED, "Ожидает машину"),
        (STABILIZING, "Подтверждает стабильный вес"),
        (PROCESSING, "Обрабатывает взвешивание"),
        (AWAITING_CLEAR, "Ожидает освобождения весов"),
    ]

    scale_number = models.CharField(max_length=50, unique=True, default="truck")
    phase = models.CharField(max_length=20, choices=PHASES, default=UNARMED)
    clear_streak = models.PositiveSmallIntegerField(default=0)
    stable_streak = models.PositiveSmallIntegerField(default=0)
    stable_weight_seconds = models.PositiveSmallIntegerField(
        default=PASSAGE_SCALE_DEFAULT_STABLE_WEIGHT_SECONDS
    )
    stability_started_at = models.DateTimeField(null=True, blank=True)
    candidate_weight_kg = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    current_capture = models.OneToOneField(
        AutomaticPassageCapture,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lane_state",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scale_number"]
        constraints = [
            models.CheckConstraint(
                name="grain_passage_scale_phase_valid",
                condition=models.Q(
                    phase__in=[
                        "unarmed",
                        "armed",
                        "stabilizing",
                        "processing",
                        "awaiting_clear",
                    ]
                ),
            ),
            models.CheckConstraint(
                name="grain_passage_scale_stable_seconds_valid",
                condition=models.Q(
                    stable_weight_seconds__gte=(
                        PASSAGE_SCALE_MIN_STABLE_WEIGHT_SECONDS
                    ),
                    stable_weight_seconds__lte=(
                        PASSAGE_SCALE_MAX_STABLE_WEIGHT_SECONDS
                    ),
                ),
            ),
        ]


class LabCheck(models.Model):
    DECISIONS = [
        "accepted",
        "accepted_with_restrictions",
        "rejected",
        "quarantine",
    ]

    wagon = models.ForeignKey(
        Wagon, on_delete=models.CASCADE, related_name="lab_checks"
    )
    moisture = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    impurity = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    nature = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    grain_class = models.CharField(max_length=50, blank=True, default="")
    infestation = models.BooleanField(default=False)
    damage = models.CharField(max_length=300, blank=True, default="")
    note = models.TextField(blank=True, default="")
    decision = models.CharField(max_length=30)
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class SiloReservation(models.Model):
    """Резерв места под конкретный вагон, снимается при оприходовании."""

    wagon = models.OneToOneField(
        Wagon, on_delete=models.CASCADE, related_name="reservation"
    )
    silo = models.ForeignKey(
        Silo, on_delete=models.PROTECT, related_name="reservations"
    )
    amount_kg = models.PositiveBigIntegerField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SiloAllocation(models.Model):
    """Часть разгрузки вагона в конкретный силос (поддержка нескольких)."""

    MEASUREMENT_SOURCES = [
        "intermediate_weighing",
        "conveyor_scale",
        "flow_meter",
        "weighing_hopper",
        "manual",
    ]

    wagon = models.ForeignKey(
        Wagon, on_delete=models.CASCADE, related_name="allocations"
    )
    silo = models.ForeignKey(Silo, on_delete=models.PROTECT, related_name="allocations")
    amount_kg = models.PositiveBigIntegerField()
    measurement_source = models.CharField(max_length=30, default="manual")
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)


class GrainMovement(models.Model):
    """Неизменяемый леджер движений зерна по силосам (аналог StockMovement)."""

    TYPES = [
        "income",
        "expense",
        "transfer_in",
        "transfer_out",
        "adjustment",
        "inventory_correction",
    ]

    silo = models.ForeignKey(Silo, on_delete=models.PROTECT, related_name="movements")
    movement_type = models.CharField(max_length=25)
    delta_kg = models.BigIntegerField()
    balance_after_kg = models.BigIntegerField()
    wagon = models.ForeignKey(
        Wagon, null=True, blank=True, on_delete=models.PROTECT, related_name="movements"
    )
    supply = models.ForeignKey(
        GrainSupply,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="movements",
    )
    batch_number = models.CharField(max_length=60, blank=True, default="")
    note = models.CharField(max_length=300, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["silo", "-id"], name="grainmove_silo_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError(
                "Движение зерна неизменяемо: оформите корректирующую операцию"
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Движение зерна нельзя удалить: оформите обратную операцию")


class VehicleOrientationSample(models.Model):
    """Кадр с меткой «передом/задом», отправленный на Camera-PC для дообучения.

    Датасет собирается сам: у завершённого рейса кадр заезда — передом, кадр
    выезда — задом (это работает и для тяжёлых машин); без завершённого рейса
    метку даёт вес (пустая легче VEHICLE_ORIENTATION_EMPTY_MAX_KG, гружёная
    тяжелее VEHICLE_ORIENTATION_LOADED_MIN_KG, между — кадр пропускается).
    Кадр, на котором классификатор был уверен в обратном, не отправляется, а
    помечается конфликтом: такие кадры смотрит человек.
    """

    WEIGHING = "weighing"
    UNASSIGNED = "unassigned"
    KINDS = [(WEIGHING, "Взвешивание"), (UNASSIGNED, "Неопознанное взвешивание")]
    BY_TRIP = "trip"
    BY_WEIGHT = "weight"
    # A human looked at the frame: automatic relabelling never overrides it.
    BY_MANUAL = "manual"

    record_kind = models.CharField(max_length=12, choices=KINDS)
    record_id = models.PositiveBigIntegerField()
    label = models.CharField(max_length=8, choices=VEHICLE_ORIENTATIONS[1:])
    label_source = models.CharField(max_length=8)
    weight_kg = models.PositiveBigIntegerField()
    captured_at = models.DateTimeField()
    model_orientation = models.CharField(max_length=8, blank=True, default="")
    conflict = models.BooleanField(default=False)
    # Excluded by a human (not a truck, unreadable frame): never trained on;
    # removal_pending asks Camera-PC to drop a copy it already received.
    excluded = models.BooleanField(default=False)
    removal_pending = models.BooleanField(default=False)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    # sent_at — «текущая метка доставлена»: сбрасывается при перемаркировке,
    # чтобы кадр ушёл заново. delivered_at — «ПК держит копию кадра»: живёт
    # от первой удачной отправки до подтверждённого удаления на ПК, и только
    # по нему решается, надо ли просить ПК забыть кадр.
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["record_kind", "record_id"],
                name="grain_one_orientation_sample_per_record",
            ),
            models.CheckConstraint(
                name="grain_orientation_sample_label_valid",
                condition=models.Q(label__in=["front", "rear"]),
            ),
        ]

    @property
    def sample_id(self) -> str:
        return f"{self.record_kind}-{self.record_id}"


class VehicleOrientationDatasetState(models.Model):
    """Состояние сборщика датасета ориентации — одна строка (pk=1).

    ``collect_since`` — водораздел сбора: взвешивания, созданные раньше него,
    ``collect()`` не смотрит. Его двигает очистка датасета (``purge_all`` —
    на «сейчас», ``purge_samples`` с отсечкой — на неё), иначе ночной сбор
    воссоздал бы только что стёртые образцы из тех же фото и снова отправил
    их на Camera-PC. Стёртый период в датасет больше не возвращается.
    """

    collect_since = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls) -> "VehicleOrientationDatasetState":
        row, _ = cls.objects.get_or_create(pk=1)
        return row
