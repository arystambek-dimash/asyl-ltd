"""Зерно: силосы, поставки, приход вагонами и вывоз машинами через автовесы.

Здесь же журнал взвешиваний, память тары, очередь непривязанных взвешиваний
и проверка номера, захваты распознавания, датасет ориентации машин, остановки
вагонов под аркой и неизменяемый леджер силосов.

Правила хранения:
- вес — только целые килограммы (никаких float);
- остаток силоса выводится ТОЛЬКО из движений ``GrainMovement``;
- резерв места — отдельные записи ``SiloReservation`` (сумма активных);
- движения после проведения неизменяемы: правка — обратной операцией.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone

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

# Допуск расхождения нетто с документами/ожиданием, %. Сверх него приход
# останавливается в «Расхождение веса» и ждёт решения оператора.
WEIGHT_DISCREPANCY_ALLOWED_PERCENT = Decimal("1")


class GrainSettings(models.Model):
    """Единственная строка настроек модуля (порог расхождения).

    Больше не читается: допуск — ``WEIGHT_DISCREPANCY_ALLOWED_PERCENT``.
    Таблицу удалить миграцией после сверки значения на проде.
    """

    allowed_discrepancy_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=1
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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name

    # silo_overview() кладёт остаток и резерв аннотациями _balance_kg/_reserved_kg.
    # Команды берут силос заново под select_for_update и считают вживую.
    @property
    def current_balance_kg(self) -> int:
        if hasattr(self, "_balance_kg"):
            return self._balance_kg
        last = self.movements.order_by("-id").first()
        return last.balance_after_kg if last else 0

    @property
    def reserved_kg(self) -> int:
        if hasattr(self, "_reserved_kg"):
            return self._reserved_kg
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
    culture = models.CharField(max_length=100)
    grain_class = models.CharField(max_length=50, blank=True, default="")
    expected_total_kg = models.PositiveBigIntegerField(null=True, blank=True)
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
    workflow = models.CharField(max_length=20, default="simple")
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
        return f"{'Вывоз' if self.is_passage else 'Вагон'} {self.label}"

    @property
    def label(self) -> str:
        """Номер рейса, а пока его нет — #id."""
        return self.number or f"#{self.pk}"

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

    def weight_difference_kg(self) -> int | None:
        """Нетто минус плановый вес; None — сверять не с чем."""
        planned = self.planned_weight_kg
        if planned is None or self.net_weight_kg is None:
            return None
        return self.net_weight_kg - planned

    def weight_difference_percent(self) -> Decimal | None:
        """Отклонение нетто от планового веса в % с точностью до сотых."""
        difference = self.weight_difference_kg()
        planned = self.planned_weight_kg
        if difference is None or not planned:
            return None
        percent = Decimal(difference) / Decimal(planned) * 100
        return percent.quantize(Decimal("0.01"))

    def weight_matches(self) -> bool | None:
        """Вес в допуске. Единственное место сверки: статус расхождения в
        сервисах и флаг в API читают его, чтобы не разойтись на границе."""
        percent = self.weight_difference_percent()
        if percent is None:
            return None
        return abs(percent) <= WEIGHT_DISCREPANCY_ALLOWED_PERCENT


def weighing_photo_path(instance, filename: str) -> str:
    return f"grain/weighings/{instance.wagon_id}/{filename}"


def unassigned_weighing_photo_path(instance, filename: str) -> str:
    return f"grain/unassigned/{filename}"


class WeighingRecord(models.Model):
    """Журнал всех взвешиваний, включая повторные и ручные правки."""

    wagon = models.ForeignKey(Wagon, on_delete=models.CASCADE, related_name="weighings")
    kind = models.CharField(max_length=10)
    weight_kg = models.PositiveBigIntegerField()
    scale_number = models.CharField(max_length=50, blank=True, default="")
    source = models.CharField(max_length=10, default="manual")
    reference_record = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="tare_reuses"
    )
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


class VehicleTareMemory(models.Model):
    """Latest confirmed entry for a plate; its record retains scale/manual provenance."""

    number = models.CharField(max_length=30, unique=True)
    record = models.ForeignKey(
        WeighingRecord, on_delete=models.CASCADE, related_name="current_tare_memories"
    )
    observed_at = models.DateTimeField(db_index=True)
    updated_at = models.DateTimeField(auto_now=True)


class UnassignedWeighing(models.Model):
    """Вес с автовесов, ещё не привязанный к рейсу.

    Сюда паркуется каждое стабильное взвешивание автоматики: проверка номера
    (WeighingIdentityCheck) сама проводит его как заезд или выезд. Если
    номер, направление или рейс определить нельзя, взвешивание остаётся в
    очереди, и оператор привязывает его позже. Автоматика не останавливается.
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
    # Почему вес припаркован:
    # identity_verification_required — ждёт проверки номера (обычный путь);
    # plate_unreadable / orientation_unknown — номер или направление не прочитаны;
    # entry_missing — гружёный выезд, которому не нашлось заезда: ни рейса под
    # прочитанным номером, ни припаркованного пустого веса, ни единственного
    # безымянного рейса, ждущего выезда (при нескольких безымянных не гадаем);
    # passage_state_conflict / automatic_passage_apply_failed или код ошибки
    # захвата — автоматика не смогла провести вес.
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


class WeighingIdentityCheck(models.Model):
    """Durable, bounded automatic identification of one parked weighing.

    Books the weighing as an entry or an exit by its plate and direction
    (camera OCR first, a single saved frame read by the model as a fallback).
    """

    PENDING = "pending"
    PROCESSING = "processing"
    RETRYING = "retrying"
    REVIEW = "review"
    MATCHED = "matched"

    weighing = models.OneToOneField(
        UnassignedWeighing, on_delete=models.CASCADE, related_name="identity_check"
    )
    status = models.CharField(max_length=16, default=PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    lease_until = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    model = models.CharField(max_length=80, blank=True, default="")
    response_id = models.CharField(max_length=100, blank=True, default="")
    reason = models.CharField(max_length=100, blank=True, default="")
    evidence = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                name="grain_identity_status_valid",
                condition=models.Q(status__in=["pending", "processing", "retrying", "review", "matched"]),
            ),
        ]


class RecognitionCapture(models.Model):
    """Common state of one stable-weight trigger answered by Camera-PC.

    Shared by the operator's weight-first command and the automatic scale
    lane; status/stage labels and lane-specific columns stay on each model.
    """

    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

    CLAIMED = "claimed"
    RECOGNIZING = "recognizing"
    APPLYING = "applying"
    DONE = "done"

    idempotency_key = models.UUIDField(unique=True)
    camera = models.CharField(max_length=32)
    camera_source = models.CharField(max_length=4, blank=True, default="")
    stable_weight_at = models.DateTimeField(null=True, blank=True)
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
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    retryable = models.BooleanField(default=False)
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_detail = models.CharField(max_length=300, blank=True, default="")
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True
        ordering = ["-id"]


class PassageWeightCapture(RecognitionCapture):
    """Durable weight-first command joining one scale read to one plate result."""

    ENTRY = "entry"
    EXIT = "exit"
    ACTIONS = [(ENTRY, "Въезд"), (EXIT, "Выезд")]

    STATUSES = [
        (RecognitionCapture.PROCESSING, "Выполняется"),
        (RecognitionCapture.COMPLETED, "Завершено"),
        (RecognitionCapture.FAILED, "Ошибка"),
    ]
    STAGES = [
        (RecognitionCapture.CLAIMED, "Запрос принят"),
        (RecognitionCapture.RECOGNIZING, "Распознавание номера"),
        (RecognitionCapture.APPLYING, "Сохранение результата"),
        (RecognitionCapture.DONE, "Завершено"),
    ]

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
    status = models.CharField(
        max_length=12, choices=STATUSES, default=RecognitionCapture.PROCESSING
    )
    stage = models.CharField(
        max_length=16, choices=STAGES, default=RecognitionCapture.CLAIMED
    )
    scale_number = models.CharField(max_length=50, blank=True, default="")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta(RecognitionCapture.Meta):
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


class AutomaticPassageCapture(RecognitionCapture):
    """One durable automatic operation for one observed scale occupancy.

    The polling loop commits this row before contacting either the strict
    scale endpoint or Camera-PC.  A terminal row remains attached to the lane
    state until fresh zero readings prove that the vehicle has left the scale;
    a failed operation also requires explicit operator acknowledgement.  This
    makes a process/container restart fail closed instead of recognizing the
    same parked vehicle twice.
    """

    STATUSES = [
        (RecognitionCapture.PROCESSING, "Выполняется"),
        (RecognitionCapture.COMPLETED, "Завершено"),
        (RecognitionCapture.FAILED, "Нужен оператор"),
    ]
    STAGES = [
        (RecognitionCapture.CLAIMED, "Весы захвачены"),
        (RecognitionCapture.RECOGNIZING, "Распознавание номера"),
        (RecognitionCapture.APPLYING, "Сохранение рейса"),
        (RecognitionCapture.DONE, "Завершено"),
    ]

    scale_number = models.CharField(max_length=50, default="truck")
    status = models.CharField(
        max_length=12, choices=STATUSES, default=RecognitionCapture.PROCESSING
    )
    stage = models.CharField(
        max_length=16, choices=STAGES, default=RecognitionCapture.CLAIMED
    )
    trigger_weight_kg = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    # Ответ классификатора ориентации Camera-PC: front/rear.
    orientation = models.CharField(
        max_length=8, blank=True, default="", choices=VEHICLE_ORIENTATIONS
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
    # Physical departure fences camera retries even before the clear streak
    # completes. Existing captures already dispatched OCR before this field.
    recognition_dispatched = models.BooleanField(default=True)
    departure_observed_at = models.DateTimeField(null=True, blank=True)
    recognition_valid_until = models.DateTimeField(null=True, blank=True)
    # Только сбой записи в базу оставляет ленту заблокированной до
    # подтверждения оператором; сбои распознавания не требуют человека.
    requires_acknowledgement = models.BooleanField(default=True)
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
    cleared_at = models.DateTimeField(null=True, blank=True)

    class Meta(RecognitionCapture.Meta):
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


class WeighingPhotoDelivery(models.Model):
    """Durable evidence independent of OCR success and trip assignment."""

    request_id = models.UUIDField(unique=True)
    camera = models.CharField(max_length=32)
    capture = models.ForeignKey(
        AutomaticPassageCapture,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="photo_deliveries",
    )
    photo = models.FileField(upload_to="grain/evidence/", null=True, blank=True)
    status = models.CharField(
        max_length=12,
        default="pending",
        choices=[
            ("pending", "Фото ожидается"),
            ("retrying", "Повторная загрузка"),
            ("saved", "Фото сохранено"),
            ("unavailable", "Фото недоступно"),
        ],
    )
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    snapshot_attempted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


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
    wagon = models.ForeignKey(
        Wagon, on_delete=models.CASCADE, related_name="lab_checks"
    )
    moisture = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    impurity = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    grain_class = models.CharField(max_length=50, blank=True, default="")
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
    """Оприходованная часть вагона в конкретном силосе."""

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


class WagonArchStop(models.Model):
    """One wagon stop under the unloading arch, imported from the wagon collector.

    The collector's arrival UUID is the idempotency key. The frame is stored as
    a ``WeighingPhotoDelivery`` with the same ``request_id`` and linked to the
    entry weighing through ``photo_request_id``.
    """

    OPEN, CLOSED, ATTENTION, SUPERSEDED = "open", "closed", "attention", "superseded"

    stop_id = models.UUIDField(unique=True)
    camera = models.CharField(max_length=32)
    arrived_at = models.DateTimeField(db_index=True)
    full_weight_kg = models.PositiveBigIntegerField()
    scale_age_seconds = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    scale_updated_at = models.CharField(max_length=64, blank=True, default="")
    still_seconds = models.DecimalField(max_digits=8, decimal_places=1, null=True, blank=True)
    number = models.CharField(max_length=30, blank=True, default="")
    number_source = models.CharField(max_length=12, blank=True, default="")
    recognition_error = models.CharField(max_length=64, blank=True, default="")
    ocr_attempts = models.PositiveSmallIntegerField(default=0)
    photo_request_id = models.UUIDField(db_index=True)
    wagon = models.ForeignKey(Wagon, null=True, blank=True, on_delete=models.SET_NULL, related_name="arch_stops")
    # ``wagon`` стоит SET_NULL, поэтому удаление рейса стирает связь. Этот
    # снимок переживает удаление и отличает «рейс ещё не открыт» от «рейс был
    # и его удалили» — иначе импортёр открывал бы удалённый рейс заново.
    opened_wagon_id = models.BigIntegerField(null=True, blank=True)
    # Стоп-корень этой цепочки пересдач: входной вес и кадр берутся оттуда.
    continues = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="continued_by"
    )
    entry_applied_at = models.DateTimeField(null=True, blank=True)
    departure_id = models.UUIDField(null=True, blank=True, unique=True)
    exit_weight_kg = models.PositiveBigIntegerField(null=True, blank=True)
    exit_stable_at = models.DateTimeField(null=True, blank=True)
    departed_at = models.DateTimeField(null=True, blank=True)
    motion_gap = models.BooleanField(default=False)
    exit_applied_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, default=OPEN)
    blocked_reason = models.CharField(max_length=64, blank=True, default="")
    blocked_detail = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-arrived_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["open", "closed", "attention", "superseded"]),
                name="wagon_arch_stop_status_valid",
            ),
        ]
