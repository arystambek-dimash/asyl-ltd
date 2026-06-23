from decimal import Decimal
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
