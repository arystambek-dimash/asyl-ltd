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
    """Стартовая схема, которую суперадмин может полностью заменить.

    Совпадает с пресетом «Как на эскизе» во фронтенде
    (frontend/src/components/factory-map/preset.ts) — меняйте синхронно.
    """
    return [
        {
            "id": "gate",
            "name": "КПП · охрана",
            "kind": "gate",
            "x": 42,
            "y": 264,
            "width": 96,
            "height": 72,
            "color": "#C58A35",
            "note": "Шлагбаум и распознавание номеров",
        },
        {
            "id": "parking",
            "name": "Парковка сотрудников",
            "kind": "parking",
            "x": 92,
            "y": 96,
            "width": 180,
            "height": 92,
            "color": "#697386",
            "note": "Личный транспорт",
        },
        {
            "id": "silo-park",
            "name": "Цистерны хранения зерна",
            "kind": "silos",
            "x": 268,
            "y": 88,
            "width": 216,
            "height": 196,
            "color": "#A66A20",
            "note": "Живые остатки из силосного парка",
        },
        {
            "id": "mill",
            "name": "Мельница · Производство",
            "kind": "production",
            "x": 532,
            "y": 96,
            "width": 250,
            "height": 168,
            "color": "#4E6B55",
            "note": "Фасовка и робот KUKA",
        },
        {
            "id": "truck-scale",
            "name": "Автовесы CAS",
            "kind": "scale",
            "x": 336,
            "y": 352,
            "width": 252,
            "height": 96,
            "color": "#3D7187",
            "note": "Только отруби (насыпь)",
        },
        {
            "id": "dock",
            "name": "Пост погрузки",
            "kind": "dock",
            "x": 648,
            "y": 380,
            "width": 190,
            "height": 104,
            "color": "#C58A35",
            "note": "Автотранспорт · CV-подсчёт",
        },
        {
            "id": "warehouse",
            "name": "Склад готовой продукции",
            "kind": "warehouse",
            "x": 860,
            "y": 268,
            "width": 300,
            "height": 330,
            "color": "#4E6B55",
            "note": "Остатки мешков по сортам",
        },
        {
            "id": "conveyor",
            "name": "Конвейер → вагон",
            "kind": "conveyor",
            "x": 906,
            "y": 58,
            "width": 96,
            "height": 214,
            "color": "#59636B",
            "note": "Подача мешков на погрузку",
        },
        {
            "id": "canteen",
            "name": "Столовая · 70 мест",
            "kind": "canteen",
            "x": 110,
            "y": 432,
            "width": 176,
            "height": 112,
            "color": "#C58A35",
            "note": "Кухня · обеды для сотрудников",
        },
        {
            "id": "office",
            "name": "Офис",
            "kind": "office",
            "x": 336,
            "y": 486,
            "width": 204,
            "height": 132,
            "color": "#315D74",
            "note": "Директор · Бухгалтер · Экран видеонаблюдения",
        },
        {
            "id": "cam-gate",
            "name": "Камера · КПП",
            "kind": "camera",
            "x": 150,
            "y": 212,
            "width": 48,
            "height": 48,
            "color": "#6D28D9",
            "note": "",
        },
        {
            "id": "cam-scale",
            "name": "Камера · Автовесы",
            "kind": "camera",
            "x": 612,
            "y": 360,
            "width": 48,
            "height": 48,
            "color": "#6D28D9",
            "note": "",
        },
        {
            "id": "cam-silo",
            "name": "Камера · Цистерны",
            "kind": "camera",
            "x": 494,
            "y": 76,
            "width": 48,
            "height": 48,
            "color": "#6D28D9",
            "note": "",
        },
        {
            "id": "cam-mill",
            "name": "Камера · Производство",
            "kind": "camera",
            "x": 794,
            "y": 108,
            "width": 48,
            "height": 48,
            "color": "#6D28D9",
            "note": "",
        },
        {
            "id": "cam-dock",
            "name": "Камера · Пост погрузки",
            "kind": "camera",
            "x": 848,
            "y": 430,
            "width": 48,
            "height": 48,
            "color": "#6D28D9",
            "note": "",
        },
        {
            "id": "cam-warehouse",
            "name": "Камера · Склад",
            "kind": "camera",
            "x": 1132,
            "y": 220,
            "width": 48,
            "height": 48,
            "color": "#6D28D9",
            "note": "",
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
