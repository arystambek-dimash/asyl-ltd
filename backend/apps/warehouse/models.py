from django.conf import settings
from django.db import models


class StockItem(models.Model):
    product = models.OneToOneField(
        "catalog.Product", on_delete=models.CASCADE, related_name="stock"
    )
    # IntegerField (не Positive): при списании по факту CV остаток может уйти в
    # минус, если посчитали больше, чем было на складе (с предупреждением).
    bags = models.IntegerField(default=0)


class StockReceipt(models.Model):
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT)
    bags = models.PositiveIntegerField()
    received_at = models.DateTimeField(auto_now_add=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )


class StockMovement(models.Model):
    """История движений склада: каждое изменение остатка (+/-)."""

    REASONS = [
        ("adjustment", "Корректировка"),
        ("shipment", "Отгрузка"),
        ("receipt", "Приёмка"),
    ]

    product = models.ForeignKey(
        "catalog.Product", on_delete=models.CASCADE, related_name="movements"
    )
    delta = models.IntegerField()  # >0 добавлено, <0 списано
    balance_after = models.IntegerField()  # может быть отрицательным (списание в минус)
    reason = models.CharField(max_length=20, choices=REASONS, default="adjustment")
    note = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            # История движений только растёт и читается «сверху».
            models.Index(
                fields=["-created_at", "-id"], name="stockmovement_recent_idx"
            ),
        ]


def default_factory_zones():
    """Стартовая схема, которую суперадмин может полностью заменить."""
    return [
        {
            "id": "gate",
            "name": "Проходная",
            "kind": "gate",
            "x": 64,
            "y": 286,
            "width": 150,
            "height": 104,
            "color": "#C58A35",
            "note": "Въезд и выезд вагонов",
        },
        {
            "id": "rail-scale",
            "name": "Ж/д весы",
            "kind": "scale",
            "x": 280,
            "y": 286,
            "width": 190,
            "height": 104,
            "color": "#3D7187",
            "note": "Взвешивание вагонов",
        },
        {
            "id": "silo-park",
            "name": "Силосный парк",
            "kind": "silos",
            "x": 540,
            "y": 88,
            "width": 300,
            "height": 220,
            "color": "#A66A20",
            "note": "Хранение зерна",
        },
        {
            "id": "mill",
            "name": "Мельница",
            "kind": "production",
            "x": 520,
            "y": 390,
            "width": 300,
            "height": 220,
            "color": "#315D74",
            "note": "Производственный корпус",
        },
        {
            "id": "warehouse",
            "name": "Склад готовой продукции",
            "kind": "warehouse",
            "x": 890,
            "y": 390,
            "width": 250,
            "height": 220,
            "color": "#4E6B55",
            "note": "Хранение готовой продукции",
        },
        {
            "id": "laboratory",
            "name": "Лаборатория",
            "kind": "lab",
            "x": 890,
            "y": 88,
            "width": 250,
            "height": 150,
            "color": "#6E5B84",
            "note": "Контроль качества зерна",
        },
    ]


class FactoryMap(models.Model):
    """Единая сохраняемая схема физических участков завода."""

    singleton = models.BooleanField(default=True, unique=True, editable=False)
    title = models.CharField(max_length=100, default="Схема завода")
    zones = models.JSONField(default=default_factory_zones, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="factory_map_updates",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Схема завода"

    @classmethod
    def get(cls):
        row, _ = cls.objects.get_or_create(singleton=True)
        return row
