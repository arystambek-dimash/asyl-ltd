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
