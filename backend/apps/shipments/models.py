from django.db import models


class Shipment(models.Model):
    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="shipment"
    )
    # Вагон едет без номера машины — поле необязательно.
    truck_number = models.CharField(max_length=30, blank=True, default="")
    # Ручной или расчётный входной вес в sales-flow.
    weigh_in_kg = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    bags_loaded = models.PositiveIntegerField(default=0)
    arrived_at = models.DateTimeField(null=True, blank=True)
    loading_started_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)


def default_waybill_signers():
    # Как на бумажном бланке мельницы; меняется в настройках накладной.
    return [
        {"role": "Бухгалтер", "name": "Егамбердиева Д"},
        {"role": "Склад", "name": "Тажи А"},
        {"role": "Кассир", "name": "Ибрагимова Г"},
    ]


class WaybillSettings(models.Model):
    """Шапка и подписи «Накладной на отпуск товаров» — одна строка на всё приложение."""

    singleton = models.BooleanField(default=True, unique=True, editable=False)
    point_name = models.CharField(max_length=120, default="мельница Аксу")
    signers = models.JSONField(default=default_waybill_signers)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls) -> "WaybillSettings":
        settings, _ = cls.objects.get_or_create(singleton=True)
        return settings
