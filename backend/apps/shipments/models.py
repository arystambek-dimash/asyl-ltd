from django.db import models


class Shipment(models.Model):
    class WeightSource(models.TextChoices):
        LEGACY = "legacy", "Старые данные"
        ESTIMATED = "estimated", "Расчёт по товару"
        MANUAL = "manual", "Ручной ввод"
        SCALE = "scale", "Автомобильные весы"

    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="shipment"
    )
    # Вагон едет без номера машины — поле необязательно.
    truck_number = models.CharField(max_length=30, blank=True, default="")
    # Старые строки могли содержать здесь расчётный вес мешков. Источник нужен,
    # чтобы после обновления не принять такой расчёт за реальный вес машины.
    weigh_in_kg = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    weigh_in_source = models.CharField(
        max_length=16,
        choices=WeightSource.choices,
        default=WeightSource.LEGACY,
        db_default=WeightSource.LEGACY,
    )
    # Для КАМАЗа, который приезжает пустым и уезжает гружёным:
    # net = weigh_out - weigh_in. У вагонов и старых отгрузок поля остаются NULL.
    weigh_out_kg = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    net_weight_kg = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    bags_loaded = models.PositiveIntegerField(default=0)
    arrived_at = models.DateTimeField(null=True, blank=True)
    loading_started_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
