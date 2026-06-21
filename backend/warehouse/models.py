from django.conf import settings
from django.db import models


class StockItem(models.Model):
    product = models.OneToOneField(
        "catalog.Product", on_delete=models.CASCADE, related_name="stock"
    )
    bags = models.PositiveIntegerField(default=0)


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
    balance_after = models.PositiveIntegerField()
    reason = models.CharField(max_length=20, choices=REASONS, default="adjustment")
    note = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ["-created_at", "-id"]
