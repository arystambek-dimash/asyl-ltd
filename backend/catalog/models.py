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
    # Класс мешка для CV-счётчика: Red_50 / Red_25 / Blue_50 / Blue_25 /
    # Green_50 / Green_25 — совпадает с классами детектора (weights/detector.pt).
    # Пусто = товар не считается по видео.
    CV_CLASSES = [
        ("Red_50", "Красный 50 кг"), ("Red_25", "Красный 25 кг"),
        ("Blue_50", "Синий 50 кг"), ("Blue_25", "Синий 25 кг"),
        ("Green_50", "Зелёный 50 кг"), ("Green_25", "Зелёный 25 кг"),
    ]

    grade = models.ForeignKey(Grade, on_delete=models.PROTECT, related_name="products")
    packaging = models.ForeignKey(
        Packaging, on_delete=models.PROTECT, related_name="products"
    )
    cv_class = models.CharField(max_length=20, blank=True, default="", choices=CV_CLASSES)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("grade", "packaging")

    @property
    def weight_kg(self):
        return self.packaging.weight_kg

    def __str__(self):
        return f"{self.grade.name} {self.packaging.name}"
