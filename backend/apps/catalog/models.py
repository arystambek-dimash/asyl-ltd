from decimal import Decimal
from django.conf import settings
from django.db import models


class Product(models.Model):
    COLORS = [("Red", "Красный"), ("Green", "Зелёный"), ("Blue", "Синий")]
    WEIGHTS = [(Decimal("25"), "25 кг"), (Decimal("50"), "50 кг")]

    name = models.CharField(max_length=100)
    color = models.CharField(max_length=10, choices=COLORS)
    weight_kg = models.DecimalField(max_digits=6, decimal_places=2, choices=WEIGHTS)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("name", "color", "weight_kg")

    @property
    def cv_class(self):
        w = "50" if Decimal(self.weight_kg) == Decimal("50") else "25"
        return f"{self.color}_{w}"

    def __str__(self):
        return f"{self.name} · {dict(self.COLORS)[self.color]} {int(self.weight_kg)} кг"


class ClientPrice(models.Model):
    """Договорная цена товара для конкретного клиента (прайс-лист клиента)."""
    client = models.ForeignKey(
        "clients.Client", on_delete=models.CASCADE, related_name="prices")
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="client_prices")
    price = models.DecimalField(max_digits=12, decimal_places=2)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="set_client_prices")

    class Meta:
        unique_together = ("client", "product")
