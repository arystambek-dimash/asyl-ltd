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
    grade = models.ForeignKey(Grade, on_delete=models.PROTECT, related_name="products")
    packaging = models.ForeignKey(
        Packaging, on_delete=models.PROTECT, related_name="products"
    )
    price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("grade", "packaging")

    @property
    def weight_kg(self):
        return self.packaging.weight_kg

    def __str__(self):
        return f"{self.grade.name} {self.packaging.name}"
