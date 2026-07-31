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

from .statuses import EXPECTED, WAGON_STATUSES


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
    WEIGHT_SOURCES = ["auto", "manual"]

    supply = models.ForeignKey(
        GrainSupply,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="wagons",
    )
    number = models.CharField(max_length=30, blank=True, default="")
    workflow = models.CharField(max_length=20, default="legacy")
    number_source = models.CharField(max_length=20, default="manual")
    number_camera_source = models.CharField(max_length=32, blank=True, default="")
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
        ]

    def __str__(self):
        return f"Вагон {self.number or f'#{self.pk}'}"

    @property
    def planned_weight_kg(self) -> int | None:
        """Вес для резерва/сверки: документы точнее ожиданий."""
        return self.document_weight_kg or self.expected_weight_kg


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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


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
