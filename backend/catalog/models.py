from decimal import Decimal
from django.db import models


class Grade(models.Model):
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Packaging(models.Model):
    name = models.CharField(max_length=50, unique=True)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Product(models.Model):
    COLORS = [("Red", "Красный"), ("Green", "Зелёный"), ("Blue", "Синий")]
    WEIGHTS = [(Decimal("25"), "25 кг"), (Decimal("50"), "50 кг")]

    # Старые поля (удаляются в финальной миграции после переноса данных).
    grade = models.ForeignKey("Grade", on_delete=models.PROTECT, related_name="products", null=True, blank=True)
    packaging = models.ForeignKey("Packaging", on_delete=models.PROTECT, related_name="products", null=True, blank=True)
    cv_class_old = models.CharField(max_length=20, blank=True, default="")

    # Новые поля (nullable до переноса данных, not-null в финальной миграции).
    name = models.CharField(max_length=100, null=True, blank=True)
    color = models.CharField(max_length=10, choices=COLORS, null=True, blank=True)
    new_weight_kg = models.DecimalField(max_digits=6, decimal_places=2, choices=WEIGHTS, null=True, blank=True)

    price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)

    @property
    def weight_kg(self):
        if self.new_weight_kg is not None:
            return self.new_weight_kg
        return self.packaging.weight_kg if self.packaging_id else None

    @weight_kg.setter
    def weight_kg(self, value):
        # Переходный сеттер: позволяет Product(weight_kg=…) до финальной миграции,
        # где weight_kg станет реальным полем.
        self.new_weight_kg = value

    @property
    def cv_class(self):
        if not self.color or self.weight_kg is None:
            return ""
        w = "50" if Decimal(self.weight_kg) == Decimal("50") else "25"
        return f"{self.color}_{w}"

    def __str__(self):
        if self.name and self.color:
            return f"{self.name} · {dict(self.COLORS)[self.color]} {int(self.weight_kg)} кг"
        if self.grade_id and self.packaging_id:
            return f"{self.grade.name} {self.packaging.name}"
        return f"Товар #{self.pk}"
